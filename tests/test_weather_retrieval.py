"""
Tests for weather retrieval and its resumable cache.
"""

# Imports

import gzip
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest
from requests import ConnectionError as RequestsConnectionError

from monty_tool.event_context import EventContext
from monty_tool.weather_api import PowerRateLimitError
from monty_tool.weather import retrieval
from monty_tool.weather.schemas import POWERResponse, WeatherQuery


# Test object helpers

def _payload(start: str = '20260903', days: int = 3) -> POWERResponse:
    """
    A POWER response covering `days` days from `start`.
    """
    stamps = [f'{start[:6]}{int(start[6:]) + offset:02d}' for offset in range(days)]
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


def _context(item_id: str = 'gdacs-event-1', **overrides) -> EventContext:
    """
    A point-located context, the input `pull_event_weather` accepts.
    """
    fields = {
        'item_id': item_id,
        'collection': 'gdacs-events',
        'correlation_id': 'correlation-1',
        'roles': ['event'],
        'title': 'Flood',
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
    A context POWER cannot answer for, as most collections produce.
    """
    return _context(
        item_id=item_id,
        geometry_type='Polygon',
        longitude=None,
        latitude=None,
    )


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """
    Replaces the POWER call so no test reaches the network.
    """
    mock = Mock(return_value=_payload())
    monkeypatch.setattr(retrieval, 'get_weather_data', mock)
    return mock


# Tests: single-record retrieval

def test_get_event_weather_passes_the_query_through(api: Mock):
    """
    Confirms the query's point and window reach the API unchanged.
    """
    query = WeatherQuery(
        item_id='gdacs-event-1', latitude=4.42, longitude=-76.83,
        start_date=date(2026, 9, 3), end_date=date(2026, 9, 5),
    )

    result = retrieval.get_event_weather(query)

    assert api.call_args.args == (4.42, -76.83, date(2026, 9, 3), date(2026, 9, 5))
    assert result.item_id == 'gdacs-event-1'
    assert len(result.days) == 3


# Tests: the cache

def test_pull_writes_results_that_load_back(tmp_path: Path, api: Mock):
    """
    Confirms retrieved weather round-trips through the cache.
    """
    retrieval.pull_event_weather(
        [_context('a'), _context('b')], cache_dir=tmp_path, delay_seconds=0, max_workers=1,
    )

    results = list(retrieval.load_event_weather(tmp_path))
    assert [result.item_id for result in results] == ['a', 'b']
    assert results[0].units['PRECTOTCORR'] == 'mm/day'


def test_pull_creates_the_cache_directory(tmp_path: Path, api: Mock):
    """
    Confirms a missing cache directory is created rather than raising.
    """
    cache_dir = tmp_path / 'nested' / 'weather'
    retrieval.pull_event_weather([_context()], cache_dir=cache_dir, delay_seconds=0, max_workers=1)

    assert retrieval.weather_cache_path(cache_dir).exists()


def test_load_missing_cache_says_what_to_run(tmp_path: Path):
    """
    Confirms the error for an empty cache names the pull to run.
    """
    with pytest.raises(FileNotFoundError, match='pull_event_weather'):
        list(retrieval.load_event_weather(tmp_path))


def test_cached_item_ids_is_empty_before_any_pull(tmp_path: Path):
    """
    Confirms a missing cache reads as empty rather than raising, so a
    first run and a resumed run take the same path.
    """
    assert retrieval.cached_item_ids(tmp_path) == set()


# Tests: resuming

def test_rerunning_a_pull_refetches_nothing(tmp_path: Path, api: Mock):
    """
    Confirms cached records are not requested again.

    A full pull is a request per record over roughly ten hours, so a
    re-run has to resume rather than start over.
    """
    items = [_context('a'), _context('b')]
    retrieval.pull_event_weather(items, cache_dir=tmp_path, delay_seconds=0, max_workers=1)
    assert api.call_count == 2

    retrieval.pull_event_weather(items, cache_dir=tmp_path, delay_seconds=0, max_workers=1)

    assert api.call_count == 2
    assert [r.item_id for r in retrieval.load_event_weather(tmp_path)] == ['a', 'b']


def test_resuming_fetches_only_the_new_records(tmp_path: Path, api: Mock):
    """
    Confirms a widened record set appends without duplicating.
    """
    retrieval.pull_event_weather([_context('a')], cache_dir=tmp_path, delay_seconds=0, max_workers=1)
    retrieval.pull_event_weather(
        [_context('a'), _context('b'), _context('c')],
        cache_dir=tmp_path, delay_seconds=0, max_workers=1,
    )

    assert api.call_count == 3
    assert [r.item_id for r in retrieval.load_event_weather(tmp_path)] == ['a', 'b', 'c']


def test_an_interrupted_pull_keeps_what_it_retrieved(tmp_path: Path, api: Mock):
    """
    Confirms results already fetched survive an interruption.

    This is why the pull appends rather than renaming a temporary file
    into place the way `pull_collection` does: here, discarding the run
    would throw away hours of work.
    """
    def interrupt_after_two():
        yield _context('a')
        yield _context('b')
        raise KeyboardInterrupt('simulated interruption')

    with pytest.raises(KeyboardInterrupt):
        retrieval.pull_event_weather(
            interrupt_after_two(), cache_dir=tmp_path, delay_seconds=0, max_workers=1,
        )

    assert [r.item_id for r in retrieval.load_event_weather(tmp_path)] == ['a', 'b']


# Tests: tolerating bad records

def test_records_without_a_point_are_skipped_not_fetched(tmp_path: Path, api: Mock):
    """
    Confirms polygon records are passed over without a request.

    Most collections are polygon-based, so these are routine rather than
    exceptional and must not end the run.
    """
    retrieval.pull_event_weather(
        [_polygon_context('p1'), _context('a'), _polygon_context('p2')],
        cache_dir=tmp_path, delay_seconds=0, max_workers=1,
    )

    assert api.call_count == 1
    assert [r.item_id for r in retrieval.load_event_weather(tmp_path)] == ['a']


def test_a_failed_request_does_not_end_the_run(tmp_path: Path, monkeypatch):
    """
    Confirms one dropped connection costs one record, not the whole pull.
    """
    calls = {'count': 0}

    def flaky(*args, **kwargs):
        calls['count'] += 1
        if calls['count'] == 2:
            raise RequestsConnectionError('simulated drop')
        return _payload()

    monkeypatch.setattr(retrieval, 'get_weather_data', flaky)

    retrieval.pull_event_weather(
        [_context('a'), _context('b'), _context('c')],
        cache_dir=tmp_path, delay_seconds=0, max_workers=1,
    )

    assert [r.item_id for r in retrieval.load_event_weather(tmp_path)] == ['a', 'c']


def test_a_failed_record_is_retried_on_the_next_run(tmp_path: Path, monkeypatch):
    """
    Confirms failures are not cached as absences, so a resume retries them.
    """
    failing = Mock(side_effect=RequestsConnectionError('simulated drop'))
    monkeypatch.setattr(retrieval, 'get_weather_data', failing)
    retrieval.pull_event_weather([_context('a')], cache_dir=tmp_path, delay_seconds=0, max_workers=1)

    monkeypatch.setattr(retrieval, 'get_weather_data', Mock(return_value=_payload()))
    retrieval.pull_event_weather([_context('a')], cache_dir=tmp_path, delay_seconds=0, max_workers=1)

    assert [r.item_id for r in retrieval.load_event_weather(tmp_path)] == ['a']


# Tests: request pacing and padding

def test_the_courtesy_delay_is_applied_per_record(
    tmp_path: Path, api: Mock, monkeypatch: pytest.MonkeyPatch,
):
    """
    Confirms requests are paced, and only after a record is retrieved.
    """
    sleeps = []
    monkeypatch.setattr(retrieval.time, 'sleep', sleeps.append)

    retrieval.pull_event_weather(
        [_context('a'), _polygon_context('p1'), _context('b')],
        cache_dir=tmp_path, delay_seconds=0.2, max_workers=1,
    )

    # Two requests, so two pauses; the skipped polygon earns none.
    assert sleeps == [0.2, 0.2]


def test_window_padding_reaches_the_query(tmp_path: Path, api: Mock):
    """
    Confirms padding overrides are passed through to the built query.
    """
    retrieval.pull_event_weather(
        [_context('a')], cache_dir=tmp_path, delay_seconds=0, max_workers=1,
        days_before=0, days_after=0,
    )

    # The event runs 10-12 Sept, so an unpadded window is exactly that.
    assert api.call_args.args[2] == date(2026, 9, 10)
    assert api.call_args.args[3] == date(2026, 9, 12)


# Tests: surviving a killed pull

def _truncate(path: Path, bytes_removed: int = 40) -> None:
    """
    Chop the tail off a cache file, as `kill -9` mid-write leaves it.

    The final gzip member loses its trailer, so reading raises EOFError
    even though the records before it are intact.
    """
    data = path.read_bytes()
    path.write_bytes(data[:-bytes_removed])


def test_a_killed_pull_still_yields_its_complete_records(
    tmp_path: Path, api: Mock,
):
    """
    Confirms a cache truncated mid-stream recovers what it holds.

    A pull killed outright leaves no gzip trailer. Refusing to read the
    file would cost the whole run; a ten hour job cannot be restarted
    from nothing over one lost trailer.
    """
    items = [_context(f'event-{number}') for number in range(60)]
    retrieval.pull_event_weather(items, cache_dir=tmp_path, delay_seconds=0, max_workers=1)
    _truncate(retrieval.weather_cache_path(tmp_path))

    recovered = [result.item_id for result in retrieval.load_event_weather(tmp_path)]

    assert recovered, 'a truncated cache must still yield its intact records'
    assert len(recovered) < len(items), 'the truncation must cost something'
    assert recovered == [item.item_id for item in items][:len(recovered)]


def test_a_killed_pull_can_still_be_resumed(tmp_path: Path, api: Mock):
    """
    Confirms IDs are readable from a truncated cache, so a re-run
    continues instead of starting over.
    """
    items = [_context(f'event-{number}') for number in range(60)]
    retrieval.pull_event_weather(items, cache_dir=tmp_path, delay_seconds=0, max_workers=1)
    _truncate(retrieval.weather_cache_path(tmp_path))

    recovered = retrieval.cached_item_ids(tmp_path)
    api.reset_mock()
    retrieval.pull_event_weather(items, cache_dir=tmp_path, delay_seconds=0, max_workers=1)

    assert 0 < len(recovered) < len(items)
    # Only the records lost with the trailer are fetched again.
    assert api.call_count == len(items) - len(recovered)
    assert len(list(retrieval.load_event_weather(tmp_path))) == len(items)


def test_a_cache_corrupted_by_appending_still_reads(tmp_path: Path, api: Mock):
    """
    Confirms a cache appended to while truncated is still readable.

    Appending onto a broken member corrupts the join, which surfaces as
    a zlib error rather than the EOFError a plain truncation gives. A
    cache in that state predates the repair step, so reading it must not
    raise.
    """
    items = [_context(f'event-{number}') for number in range(60)]
    retrieval.pull_event_weather(items, cache_dir=tmp_path, delay_seconds=0, max_workers=1)

    path = retrieval.weather_cache_path(tmp_path)
    _truncate(path)
    # Append a fresh member onto the broken one, as an unrepaired
    # resume would.
    with gzip.open(path, 'at', encoding='utf-8') as file:
        file.write('{"item_id": "appended"}\n')

    recovered = [result.item_id for result in retrieval.load_event_weather(tmp_path)]

    assert recovered
    assert recovered == [item.item_id for item in items][:len(recovered)]


def test_a_record_cut_mid_write_is_discarded(tmp_path: Path):
    """
    Confirms a final record without its newline is dropped, not parsed.

    A kill can land mid-write, and half a JSON object is not a result.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = retrieval.weather_cache_path(tmp_path)
    with gzip.open(path, 'wt', encoding='utf-8') as file:
        file.write('{"item_id": "a"}\n')
        file.write('{"item_id": "b"}\n')
        file.write('{"item_id": "c", "days": [{"date')  # cut mid-write

    assert retrieval.cached_item_ids(tmp_path) == {'a', 'b'}


# Tests: concurrency

def _latency_tracker(payload: POWERResponse, delay: float = 0.01):
    """
    A POWER stand-in that records how many requests overlap.
    """
    state = {'live': 0, 'peak': 0}
    lock = threading.Lock()

    def call(*args, **kwargs):
        with lock:
            state['live'] += 1
            state['peak'] = max(state['peak'], state['live'])
        time.sleep(delay)
        with lock:
            state['live'] -= 1
        return payload

    return call, state


@pytest.mark.parametrize('max_workers', [1, 4, 8])
def test_results_keep_record_order_at_any_worker_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, max_workers: int,
):
    """
    Confirms the cache's order does not depend on the worker count or on
    which request happens to finish first.

    Draining whichever future completed first would make a pull's output
    depend on the network that day.
    """
    call, _ = _latency_tracker(_payload(), delay=0.0)

    calls = {'count': 0}

    def uneven(*args, **kwargs):
        # Later records finish first, inverting completion order.
        time.sleep(max(0.0, 0.05 - 0.002 * calls['count']))
        calls['count'] += 1
        return call()

    monkeypatch.setattr(retrieval, 'get_weather_data', uneven)
    items = [_context(f'event-{number:03d}') for number in range(20)]

    retrieval.pull_event_weather(
        items, cache_dir=tmp_path, delay_seconds=0, max_workers=max_workers,
    )

    written = [result.item_id for result in retrieval.load_event_weather(tmp_path)]
    assert written == [item.item_id for item in items]


def test_requests_actually_run_concurrently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """
    Confirms workers overlap rather than queueing behind each other.
    """
    call, state = _latency_tracker(_payload())
    monkeypatch.setattr(retrieval, 'get_weather_data', call)

    retrieval.pull_event_weather(
        [_context(f'event-{number}') for number in range(16)],
        cache_dir=tmp_path, delay_seconds=0, max_workers=4,
    )

    assert state['peak'] > 1, 'requests ran serially'
    assert state['peak'] <= 4, 'more requests in flight than workers'


def test_queued_work_stays_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """
    Confirms submission does not run ahead of completion without limit.

    A pull reads from a generator of tens of thousands of records, so an
    unbounded queue would build every future before the first lands.
    """
    submitted = []
    real_submit = ThreadPoolExecutor.submit

    def counting_submit(self, *args, **kwargs):
        submitted.append(1)
        return real_submit(self, *args, **kwargs)

    call, _ = _latency_tracker(_payload(), delay=0.005)
    monkeypatch.setattr(retrieval, 'get_weather_data', call)
    monkeypatch.setattr(ThreadPoolExecutor, 'submit', counting_submit)

    written = []
    real_open = gzip.open

    def watching_open(*args, **kwargs):
        handle = real_open(*args, **kwargs)
        if 'a' in str(args[1] if len(args) > 1 else kwargs.get('mode', '')):
            real_write = handle.write

            def write(data):
                # Record how far submission had run by each write.
                written.append(len(submitted))
                return real_write(data)

            handle.write = write
        return handle

    monkeypatch.setattr(retrieval.gzip, 'open', watching_open)

    retrieval.pull_event_weather(
        [_context(f'event-{number}') for number in range(40)],
        cache_dir=tmp_path, delay_seconds=0, max_workers=2,
    )

    queue_depth = 2 * retrieval.QUEUE_DEPTH_PER_WORKER
    # By the first write, submission can only be one full window ahead.
    assert written[0] <= queue_depth


def test_worker_count_must_be_positive(tmp_path: Path, api: Mock):
    """
    Confirms a zero or negative worker count is refused outright.
    """
    with pytest.raises(ValueError, match='at least 1'):
        retrieval.pull_event_weather(
            [_context()], cache_dir=tmp_path, max_workers=0,
        )


# Tests: rate limiting

def test_a_rate_limited_request_is_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """
    Confirms a 429 pauses and retries rather than losing the record.

    POWER publishes no rate limit and throttles by observed usage, so
    being told to slow down is an expected part of a long pull.
    """
    calls = {'count': 0}

    def rate_limited_once(*args, **kwargs):
        calls['count'] += 1
        if calls['count'] == 1:
            raise PowerRateLimitError('429')
        return _payload()

    backoffs = []
    monkeypatch.setattr(retrieval, 'get_weather_data', rate_limited_once)
    monkeypatch.setattr(retrieval.time, 'sleep', backoffs.append)

    retrieval.pull_event_weather(
        [_context('a')], cache_dir=tmp_path, delay_seconds=0, max_workers=1,
    )

    assert [r.item_id for r in retrieval.load_event_weather(tmp_path)] == ['a']
    assert backoffs == [retrieval.RATE_LIMIT_BACKOFF_SECONDS]


def test_backoff_doubles_between_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """
    Confirms repeated throttling backs off further each time, and the
    record is dropped rather than retried forever.
    """
    always_limited = Mock(side_effect=PowerRateLimitError('429'))
    backoffs = []
    monkeypatch.setattr(retrieval, 'get_weather_data', always_limited)
    monkeypatch.setattr(retrieval.time, 'sleep', backoffs.append)

    retrieval.pull_event_weather(
        [_context('a'), _context('b')],
        cache_dir=tmp_path, delay_seconds=0, max_workers=1,
    )

    base = retrieval.RATE_LIMIT_BACKOFF_SECONDS
    # Per record: three pauses, doubling, then give up on the fourth try.
    assert backoffs == [base, base * 2, base * 4] * 2
    assert always_limited.call_count == (retrieval.RATE_LIMIT_RETRIES + 1) * 2
    assert retrieval.cached_item_ids(tmp_path) == set()


def test_an_interrupted_parallel_pull_keeps_finished_work(
    tmp_path: Path, api: Mock,
):
    """
    Confirms results in flight when the records run out are still
    written, rather than discarded with the pool.
    """
    def interrupt_after_two():
        yield _context('a')
        yield _context('b')
        raise KeyboardInterrupt('simulated interruption')

    with pytest.raises(KeyboardInterrupt):
        retrieval.pull_event_weather(
            interrupt_after_two(), cache_dir=tmp_path,
            delay_seconds=0, max_workers=4,
        )

    assert [r.item_id for r in retrieval.load_event_weather(tmp_path)] == ['a', 'b']
