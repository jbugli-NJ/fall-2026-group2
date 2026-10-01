"""
Tests for weather retrieval from Montandon Items.
"""

# Imports

import threading
import time
from datetime import date
from unittest.mock import Mock

import pytest
from requests import ConnectionError as RequestsConnectionError

from monty_tool.api_schemas import MontandonItem
from monty_tool.weather import retrieval
from monty_tool.weather.schemas import POWERResponse, WeatherQuery, WeatherResult


# Test object helpers

def _payload() -> POWERResponse:
    """
    Build a POWER response covering the default event window.
    """
    stamps = [f'202401{day:02d}' for day in range(3, 14)]
    return POWERResponse.model_validate({
        'geometry': {'type': 'Point', 'coordinates': [-76.83, 4.42, 950.0]},
        'properties': {'parameter': {
            'T2M': {stamp: 21.0 for stamp in stamps},
            'PRECTOTCORR': {stamp: 4.0 for stamp in stamps},
        }},
        'header': {
            'sources': ['MERRA2'],
            'fill_value': -999.0,
            'start': stamps[0],
            'end': stamps[-1],
        },
        'parameters': {
            'T2M': {'units': 'C', 'longname': 'Temperature at 2 Meters'},
            'PRECTOTCORR': {'units': 'mm/day', 'longname': 'Precipitation Corrected'},
        },
    })


def _item(item_id: str = 'gdacs-event-1', **overrides) -> MontandonItem:
    """
    Build a point-located Montandon Item for retrieval.
    """
    properties = {
        'roles': ['event'],
        'title': 'Flood',
        'description': 'River flooding.',
        'datetime': '2024-01-10T00:00:00Z',
        'start_datetime': '2024-01-10T00:00:00Z',
        'end_datetime': '2024-01-12T00:00:00Z',
        'monty:corr_id': 'correlation-1',
        'monty:hazard_codes': ['FL'],
        'monty:country_codes': ['COL'],
        'monty:episode_number': 1,
    }
    properties.update(overrides.pop('properties', {}))
    return MontandonItem.model_validate({
        'id': item_id,
        'collection': 'gdacs-events',
        'bbox': [-76.83, 4.42, -76.83, 4.42],
        'geometry': {'type': 'Point', 'coordinates': [-76.83, 4.42]},
        'links': [],
        'properties': properties,
        **overrides,
    })


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """
    Replace the POWER call so tests do not reach the network.
    """
    mock = Mock(return_value=_payload())
    monkeypatch.setattr(retrieval, 'get_weather_data', mock)
    return mock


# Tests: returned results

def test_get_event_weather_passes_the_query_through(api: Mock):
    """
    Confirms the query's point and window reach the API unchanged.
    """
    query = WeatherQuery(
        item_id='gdacs-event-1', latitude=4.42, longitude=-76.83,
        start_date=date(2024, 1, 3), end_date=date(2024, 1, 13),
    )

    result = retrieval.get_event_weather(query)

    assert api.call_args.args == (4.42, -76.83, date(2024, 1, 3), date(2024, 1, 13))
    assert result.item_id == query.item_id
    assert result.days[0].temperature_mean == 21.0


def test_pull_returns_weather_results(api: Mock):
    """
    Confirms Montandon Items produce a list of event-linked WeatherResults.
    """
    results = retrieval.pull_event_weather(
        [_item('a'), _item('b')], delay_seconds=0, max_workers=1,
    )

    assert isinstance(results, list)
    assert all(isinstance(result, WeatherResult) for result in results)
    assert [result.item_id for result in results] == ['a', 'b']
    assert results[0].elevation == 950.0
    assert results[0].sources == ['MERRA2']
    assert results[0].units['T2M'] == 'C'
    assert len(results[0].days) == 11
    assert results[0].summary()['total_precipitation'] == 44.0


def test_empty_input_returns_empty_list(api: Mock):
    """
    Confirms an empty Item list returns no results and makes no requests.
    """
    assert retrieval.pull_event_weather([], delay_seconds=0) == []
    api.assert_not_called()


def test_each_call_fetches_fresh_data(api: Mock):
    """
    Confirms repeated collection calls fetch again without cache reuse.
    """
    items = [_item('a')]
    first = retrieval.pull_event_weather(items, delay_seconds=0, max_workers=1)
    second = retrieval.pull_event_weather(items, delay_seconds=0, max_workers=1)

    assert first == second
    assert api.call_count == 2


# Tests: skipped records and failures

def test_unsupported_records_are_skipped(api: Mock):
    """
    Confirms missing geometry and dates before POWER coverage skip requests.
    """
    results = retrieval.pull_event_weather(
        [
            _item('no-point', geometry=None),
            _item('old', properties={
                'datetime': '1980-01-10T00:00:00Z',
                'start_datetime': '1980-01-10T00:00:00Z',
                'end_datetime': '1980-01-12T00:00:00Z',
            }),
            _item('valid'),
        ],
        delay_seconds=0, max_workers=1,
    )

    assert api.call_count == 1
    assert [result.item_id for result in results] == ['valid']


def test_a_failed_request_does_not_end_the_run(api: Mock, caplog):
    """
    Confirms a failed request is logged and omitted while others succeed.
    """
    api.side_effect = [_payload(), RequestsConnectionError('simulated drop'), _payload()]

    results = retrieval.pull_event_weather(
        [_item('a'), _item('b'), _item('c')], delay_seconds=0, max_workers=1,
    )

    assert [result.item_id for result in results] == ['a', 'c']
    assert 'Weather retrieval failed for b' in caplog.text


# Tests: pacing and padding

def test_the_courtesy_delay_is_applied_per_record(api: Mock, monkeypatch):
    """
    Confirms each successful request is paced and skipped Items earn no pause.
    """
    sleeps = []
    monkeypatch.setattr(retrieval.time, 'sleep', sleeps.append)

    retrieval.pull_event_weather(
        [_item('a'), _item('no-point', geometry=None), _item('b')],
        delay_seconds=0.2, max_workers=1,
    )

    assert sleeps == [0.2, 0.2]


def test_window_padding_reaches_the_query(api: Mock):
    """
    Confirms padding overrides reach both the request and returned date range.
    """
    results = retrieval.pull_event_weather(
        [_item()], delay_seconds=0, max_workers=1, days_before=0, days_after=0,
    )

    assert api.call_args.args[2:] == (date(2024, 1, 10), date(2024, 1, 12))
    assert [day.date for day in results[0].days] == [
        date(2024, 1, 10), date(2024, 1, 11), date(2024, 1, 12),
    ]


# Tests: concurrency

@pytest.mark.parametrize('max_workers', [1, 5])
def test_results_keep_input_order(monkeypatch, max_workers):
    """
    Confirms results retain Item order when later requests finish first.
    """
    def fetch(query, delay_seconds):
        """
        Delay the first Item to reverse completion order across workers.
        """
        if query.item_id == 'a':
            time.sleep(0.02)
        return _payload().to_weather_result(query)

    monkeypatch.setattr(retrieval, '_fetch_with_backoff', fetch)

    results = retrieval.pull_event_weather(
        [_item('a'), _item('b'), _item('c')],
        delay_seconds=0, max_workers=max_workers,
    )

    assert [result.item_id for result in results] == ['a', 'b', 'c']


def test_requests_run_concurrently(monkeypatch):
    """
    Confirms requests overlap without exceeding the requested worker count.
    """
    state = {'live': 0, 'peak': 0}
    lock = threading.Lock()

    def fetch(query, delay_seconds):
        """
        Track overlapping requests while simulating API latency.
        """
        with lock:
            state['live'] += 1
            state['peak'] = max(state['peak'], state['live'])
        time.sleep(0.01)
        with lock:
            state['live'] -= 1
        return _payload().to_weather_result(query)

    monkeypatch.setattr(retrieval, '_fetch_with_backoff', fetch)
    results = retrieval.pull_event_weather(
        [_item(str(number)) for number in range(12)],
        delay_seconds=0, max_workers=4,
    )

    assert len(results) == 12
    assert 1 < state['peak'] <= 4


def test_worker_count_must_be_positive(api: Mock):
    """
    Confirms a zero worker count is rejected before fetching weather.
    """
    with pytest.raises(ValueError, match='at least 1'):
        retrieval.pull_event_weather([_item()], max_workers=0)
    api.assert_not_called()


# Tests: rate limiting

def test_a_rate_limited_request_is_retried(api: Mock, monkeypatch):
    """
    Confirms a rate-limited request succeeds after a backoff and retry.
    """
    api.side_effect = [retrieval.PowerRateLimitError('429'), _payload()]
    backoffs = []
    monkeypatch.setattr(retrieval.time, 'sleep', backoffs.append)

    results = retrieval.pull_event_weather([_item('a')], delay_seconds=0, max_workers=1)

    assert [result.item_id for result in results] == ['a']
    assert api.call_count == 2
    assert backoffs == [retrieval.RATE_LIMIT_BACKOFF_SECONDS]


def test_backoff_doubles_until_retries_are_exhausted(api: Mock, monkeypatch):
    """
    Confirms repeated rate limits back off exponentially and omit the result.
    """
    api.side_effect = retrieval.PowerRateLimitError('429')
    backoffs = []
    monkeypatch.setattr(retrieval.time, 'sleep', backoffs.append)

    results = retrieval.pull_event_weather([_item('a')], delay_seconds=0, max_workers=1)

    assert results == []
    assert api.call_count == retrieval.RATE_LIMIT_RETRIES + 1
    assert backoffs == [
        retrieval.RATE_LIMIT_BACKOFF_SECONDS * 2 ** attempt
        for attempt in range(retrieval.RATE_LIMIT_RETRIES)
    ]
