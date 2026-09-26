"""
Tests for the record-scoped weather LLM tool.
"""

# Imports

from datetime import date, datetime, timezone
from unittest.mock import Mock

import pytest
from requests import ConnectionError as RequestsConnectionError

from monty_tool.event_context import EventContext
from monty_tool.llm import tools as llm_tools
from monty_tool.llm.tools import WeatherTools
from monty_tool.weather.schemas import WeatherQuery, build_weather_result


# Test object helpers

def _context(item_id: str = 'gdacs-event-1', **overrides) -> EventContext:
    """
    A point-located record, the kind POWER can answer for.
    """
    fields = {
        'item_id': item_id,
        'collection': 'gdacs-events',
        'correlation_id': 'correlation-1',
        'roles': ['event'],
        'title': 'Flood in Colombia',
        'description': None,
        'keywords': [],
        'country_codes': ['COL'],
        'hazard_codes': ['FL'],
        'start_datetime': datetime(2026, 9, 10, tzinfo=timezone.utc),
        'end_datetime': datetime(2026, 9, 12, tzinfo=timezone.utc),
        'geometry_type': 'Point',
        'bbox': (-76.83, 4.42, -76.83, 4.42),
        'longitude': -76.83,
        'latitude': 4.42,
    }
    fields.update(overrides)
    return EventContext(**fields)


def _polygon_context(item_id: str = 'ifrcevent-event-1') -> EventContext:
    """
    A country-level record, which POWER cannot answer for.
    """
    return _context(
        item_id=item_id, geometry_type='Polygon', longitude=None, latitude=None,
    )


def _result(item_id: str = 'gdacs-event-1', missing: bool = False):
    """
    A parsed POWER result for the tool to return.
    """
    value = -999.0 if missing else 4.0
    query = WeatherQuery(
        item_id=item_id, latitude=4.42, longitude=-76.83,
        start_date=date(2026, 9, 3), end_date=date(2026, 9, 5),
    )
    return build_weather_result(query, {
        'geometry': {'type': 'Point', 'coordinates': [-76.83, 4.42, 950.0]},
        'properties': {'parameter': {
            'T2M': {'20260903': 21.0 if not missing else -999.0,
                    '20260904': 22.0 if not missing else -999.0,
                    '20260905': 23.0 if not missing else -999.0},
            'PRECTOTCORR': {'20260903': value, '20260904': 12.0 if not missing else value,
                            '20260905': value},
        }},
        'header': {'sources': ['MERRA2'], 'fill_value': -999.0,
                   'start': '20260903', 'end': '20260905'},
        'parameters': {'T2M': {'units': 'C', 'longname': 'T'},
                       'PRECTOTCORR': {'units': 'mm/day', 'longname': 'P'}},
    })


@pytest.fixture
def retrieve(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """
    Replaces retrieval so no test reaches the network.
    """
    mock = Mock(return_value=_result())
    monkeypatch.setattr(llm_tools, 'get_event_weather', mock)
    return mock


# Tests: the tool definition

def test_only_point_located_records_are_offered():
    """
    Confirms records POWER cannot answer for are left out of the enum.

    Offering a country-level record and then refusing every call wastes
    a tool call the assistant has a limited budget of.
    """
    tools = WeatherTools([_context('a'), _polygon_context('p'), _context('b')])

    offered = tools.definitions[0]['function']['parameters']['properties']
    assert offered['item_id']['enum'] == ['a', 'b']


def test_the_tool_is_named_and_scoped():
    """
    Confirms the definition matches what the client dispatches on.
    """
    definition = WeatherTools([_context()]).definitions[0]['function']

    assert definition['name'] == 'get_event_weather'
    assert definition['parameters']['required'] == ['item_id']
    assert definition['parameters']['additionalProperties'] is False


@pytest.mark.parametrize('count', [0, 11])
def test_record_count_is_bounded(count: int):
    """
    Confirms the record limit matches `NewsTools`.
    """
    with pytest.raises(ValueError, match='between 1 and 10'):
        WeatherTools([_context(f'event-{n}') for n in range(count)])


# Tests: successful calls

def test_a_call_returns_a_summary_not_a_daily_series(retrieve: Mock):
    """
    Confirms the assistant receives figures, not a row per day.

    The client caps prompts at 8192 tokens, so a full series would
    crowd out the conversation it is meant to inform.
    """
    tools = WeatherTools([_context()])

    result = tools.execute('get_event_weather', {'item_id': 'gdacs-event-1'})

    assert result['status'] == 'ok'
    assert result['peak_precipitation'] == 12.0
    assert result['total_precipitation'] == 20.0
    assert result['units']['PRECTOTCORR'] == 'mm/day'
    assert 'days_detail' not in result
    # The series itself must not travel to the model.
    assert not any(isinstance(value, list) and value and isinstance(value[0], dict)
                   for value in result.values())


def test_the_point_and_its_provenance_travel_with_the_result(retrieve: Mock):
    """
    Confirms the answer says where and from what the weather came.
    """
    result = WeatherTools([_context()]).execute(
        'get_event_weather', {'item_id': 'gdacs-event-1'},
    )

    assert (result['latitude'], result['longitude']) == (4.42, -76.83)
    assert result['elevation'] == 950.0
    assert result['sources'] == ['MERRA2']


def test_the_window_can_be_widened_by_the_model(retrieve: Mock):
    """
    Confirms padding arguments reach the query.

    A flood is explained by the preceding week's rain, so the model
    needs to be able to ask for it.
    """
    WeatherTools([_context()]).execute('get_event_weather', {
        'item_id': 'gdacs-event-1', 'days_before': 14, 'days_after': 0,
    })

    query = retrieve.call_args.args[0]
    assert query.start_date == date(2026, 8, 27)
    assert query.end_date == date(2026, 9, 12)


def test_a_window_with_no_data_reports_empty(monkeypatch: pytest.MonkeyPatch):
    """
    Confirms an all-missing window is not reported as a successful one.
    """
    monkeypatch.setattr(
        llm_tools, 'get_event_weather', Mock(return_value=_result(missing=True)),
    )

    result = WeatherTools([_context()]).execute(
        'get_event_weather', {'item_id': 'gdacs-event-1'},
    )

    assert result['status'] == 'empty'
    assert result['total_precipitation'] is None


# Tests: refused calls

def test_an_unknown_tool_name_is_refused(retrieve: Mock):
    """
    Confirms the tool answers only to its own name.
    """
    result = WeatherTools([_context()]).execute('search_event_news', {})

    assert result['status'] == 'error'
    retrieve.assert_not_called()


def test_an_unknown_record_is_refused(retrieve: Mock):
    """
    Confirms a hallucinated item_id cannot reach POWER.
    """
    result = WeatherTools([_context()]).execute(
        'get_event_weather', {'item_id': 'invented-event'},
    )

    assert result['status'] == 'error'
    assert 'item_id' in result['message']
    retrieve.assert_not_called()


@pytest.mark.parametrize('arguments', [
    {'item_id': 'gdacs-event-1', 'days_before': 400},
    {'item_id': 'gdacs-event-1', 'days_before': -1},
    {'item_id': 'gdacs-event-1', 'latitude': 0.0},
    {'item_id': ''},
    {},
])
def test_invalid_arguments_are_refused(retrieve: Mock, arguments: dict):
    """
    Confirms out-of-range windows and invented arguments are rejected.

    Bounding the window stops one tool call asking POWER for years of
    data, and forbidding extras stops the model supplying its own point.
    """
    result = WeatherTools([_context()]).execute('get_event_weather', arguments)

    assert result['status'] == 'error'
    retrieve.assert_not_called()


def test_a_record_without_a_point_is_refused_clearly(retrieve: Mock):
    """
    Confirms a polygon record fails with an explanation, not a crash.

    It is kept callable rather than dropped so a model that asks anyway
    is told why.
    """
    tools = WeatherTools([_context('a'), _polygon_context('p')])

    result = tools.execute('get_event_weather', {'item_id': 'p'})

    assert result['status'] == 'error'
    assert 'no point geometry' in result['message']
    retrieve.assert_not_called()


def test_a_connection_failure_is_reported_not_raised(
    monkeypatch: pytest.MonkeyPatch,
):
    """
    Confirms a dropped connection reaches the model as an error status.
    """
    monkeypatch.setattr(llm_tools, 'get_event_weather',
                        Mock(side_effect=RequestsConnectionError('dropped')))

    result = WeatherTools([_context()]).execute(
        'get_event_weather', {'item_id': 'gdacs-event-1'},
    )

    assert result['status'] == 'error'
    assert 'NASA POWER connection failed' in result['message']


def test_an_api_failure_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch):
    """
    Confirms a POWER error reaches the model rather than ending the turn.
    """
    monkeypatch.setattr(llm_tools, 'get_event_weather',
                        Mock(side_effect=RuntimeError('NASA POWER failed: HTTP 422')))

    result = WeatherTools([_context()]).execute(
        'get_event_weather', {'item_id': 'gdacs-event-1'},
    )

    assert result['status'] == 'error'
    assert '422' in result['message']
