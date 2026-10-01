"""
Retrieve weather for Montandon records, with a resumable local cache.
"""

# Imports

import gzip
import json
import logging
import time
import zlib
from collections.abc import Iterable, Iterator
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from requests import RequestException

from monty_tool.api_schemas import MontandonItem
from monty_tool.data_cache import DEFAULT_CACHE_DIR
from monty_tool.event_context import EventContext
from monty_tool.weather.api import PowerRateLimitError, get_weather_data
from monty_tool.weather.query import build_weather_query
from monty_tool.weather.schemas import WeatherQuery, WeatherResult


logger = logging.getLogger(__name__)


# Resources

WEATHER_CACHE_DIR = DEFAULT_CACHE_DIR.parent / 'weather'

# POWER answers a single point per request in about 0.8 seconds, so a
# serial pull of all 43,000 GDACS records is a ~10 hour job. Everything
# below is shaped by that.
POWER_REQUEST_DELAY_SECONDS = 0.2

# Capping workers at 5 in line with docs:
# https://power.larc.nasa.gov/docs/tutorials/service-data-request/api/#__tabbed_2_2
MAX_WORKERS = 5

# Queued work is capped at this multiple of the worker count, so pulling
# from a generator of 43,000 records does not build the whole queue in
# memory before the first result lands.
QUEUE_DEPTH_PER_WORKER = 4

# A 429 means the pace was wrong, not the request, so it is retried
# after a pause that doubles each time.
RATE_LIMIT_BACKOFF_SECONDS = 5.0
RATE_LIMIT_RETRIES = 3


# Single-record retrieval

def get_event_weather(query: WeatherQuery) -> WeatherResult:
    """
    Fetch and parse weather for one prepared query.

    Request failures propagate to the caller's existing error handling.
    """
    response = get_weather_data(
        query.latitude,
        query.longitude,
        query.start_date,
        query.end_date,
    )
    return response.to_weather_result(query)


# Cache utilities

def weather_cache_path(cache_dir: Path = WEATHER_CACHE_DIR) -> Path:
    """
    Path to the gzipped JSON Lines file caching weather results.
    """
    return cache_dir / 'weather.jsonl.gz'


def _iter_cache_lines(path: Path) -> Iterator[str]:
    """
    Yield complete lines from a cache file, tolerating a truncated tail.

    A pull killed outright (rather than interrupted) leaves the final
    gzip member without its trailer, which raises `EOFError` on read.
    The records written before that point are still intact and worth
    recovering: without this, one `kill -9` would cost the whole run and
    make resuming impossible. Only whole lines are yielded, since a kill
    can also land mid-record.
    """
    try:
        with gzip.open(path, 'rt', encoding='utf-8') as file:
            for line in file:
                if line.endswith('\n'):
                    yield line
                else:
                    # A final line with no newline was cut short.
                    logger.warning(
                        'Discarding an incomplete final record in %s.', path,
                    )
    except (EOFError, zlib.error, gzip.BadGzipFile):
        logger.warning(
            'Weather cache %s ends mid-stream, so an earlier pull was '
            'killed; recovering the records written before that point.',
            path,
        )


def load_event_weather(
    cache_dir: Path = WEATHER_CACHE_DIR,
    ) -> Iterator[WeatherResult]:
    """
    Yield cached weather results, one at a time.

    Wrap in `list(...)` for all of them in memory.
    """
    path = weather_cache_path(cache_dir)
    if not path.exists():
        raise FileNotFoundError(
            f'No weather cached at {path}; run pull_event_weather() first'
        )
    for line in _iter_cache_lines(path):
        yield WeatherResult.model_validate_json(line)


def cached_item_ids(cache_dir: Path = WEATHER_CACHE_DIR) -> set[str]:
    """
    IDs of records already in the cache, used to resume a pull.
    """
    path = weather_cache_path(cache_dir)
    if not path.exists():
        return set()
    return {
        json.loads(line)['item_id']
        for line in _iter_cache_lines(path)
    }


def _repair_cache(path: Path) -> None:
    """
    Rewrite a cache whose stream was left broken by a killed pull.

    Appending onto a truncated gzip member does not extend it, it
    corrupts it: the reader then fails on the join with a zlib error
    rather than at the end, losing records that were otherwise intact.
    Rewriting the recovered records first means a resumed pull appends
    to a sound file.
    """
    if not path.exists():
        return

    lines: list[str] = []
    truncated = False
    try:
        with gzip.open(path, 'rt', encoding='utf-8') as file:
            for line in file:
                if line.endswith('\n'):
                    lines.append(line)
                else:
                    truncated = True
    except (EOFError, zlib.error, gzip.BadGzipFile):
        truncated = True

    if not truncated:
        return

    # Write beside the cache and rename, so an interruption during the
    # repair cannot leave the cache worse than it was found.
    repaired_path = path.with_suffix('.repair')
    with gzip.open(repaired_path, 'wt', encoding='utf-8') as file:
        file.writelines(lines)
    repaired_path.replace(path)

    logger.warning(
        'Repaired %s after an interrupted pull; kept %d records.',
        path, len(lines),
    )


# Bulk retrieval

def _fetch_with_backoff(
    query: WeatherQuery,
    delay_seconds: float,
    retries: int = RATE_LIMIT_RETRIES,
    ) -> WeatherResult:
    """
    Fetch one record, pausing and retrying if POWER asks us to slow down.
    """
    for attempt in range(retries + 1):
        try:
            result = get_event_weather(query)
        except PowerRateLimitError:
            if attempt == retries:
                raise
            backoff = RATE_LIMIT_BACKOFF_SECONDS * (2 ** attempt)
            logger.warning(
                'POWER rate-limited %s; retrying in %.0fs.',
                query.item_id, backoff,
            )
            time.sleep(backoff)
            continue

        # Pace each worker rather than the pull as a whole, so the
        # courtesy gap holds however many workers are running.
        if delay_seconds:
            time.sleep(delay_seconds)
        return result

    raise AssertionError('unreachable')


def _iter_pending_queries(
    items: Iterable[MontandonItem | EventContext],
    already_cached: set[str],
    padding: dict[str, int],
    counts: dict[str, int],
    ) -> Iterator[WeatherQuery]:
    """
    Yield a query per record still needing one, skipping the rest.

    Records with no point, or outside POWER's era, are expected rather
    than exceptional: most collections are polygon-based.
    """
    for item in items:
        item_id = item.item_id if isinstance(item, EventContext) else item.id
        if item_id in already_cached:
            continue
        try:
            yield build_weather_query(item, **padding)
        except ValueError as error:
            logger.debug('Skipping %s: %s', item_id, error)
            counts['skipped'] += 1


def pull_event_weather(
    items: Iterable[MontandonItem | EventContext],
    cache_dir: Path = WEATHER_CACHE_DIR,
    max_workers: int = MAX_WORKERS,
    delay_seconds: float = POWER_REQUEST_DELAY_SECONDS,
    days_before: int | None = None,
    days_after: int | None = None,
    ) -> Path:
    """
    Fetch weather for many records, appending each result as it arrives.

    Unlike `pull_collection` and `pull_go_resource`, which write to a
    temporary file and rename it into place, this appends directly and
    skips records already cached. Those pulls are one streamed request
    and can safely start over; this one is a request per record, where
    discarding the work done so far because of a single dropped
    connection would be the worse failure.

    Records POWER cannot answer for are logged and skipped, and a failed
    request costs one record rather than the run. Re-running resumes
    where this left off.

    Requests run concurrently, but results are written in the order the
    records arrived in, so a pull is reproducible whatever the worker
    count and however the network behaves on the day.
    """
    if max_workers < 1:
        raise ValueError('max_workers must be at least 1.')

    cache_dir.mkdir(parents=True, exist_ok=True)
    path = weather_cache_path(cache_dir)
    _repair_cache(path)
    already_cached = cached_item_ids(cache_dir)

    padding = {}
    if days_before is not None:
        padding['days_before'] = days_before
    if days_after is not None:
        padding['days_after'] = days_after

    counts = {'pulled': 0, 'skipped': 0, 'failed': 0}
    queries = _iter_pending_queries(items, already_cached, padding, counts)

    with gzip.open(path, 'at', encoding='utf-8') as file:

        def record(future: Future) -> None:
            """
            Write one finished result, or account for its failure.

            Only ever called from the main thread: workers fetch, and
            nothing else touches the file. `GzipFile` is not thread
            safe, so writing from the workers instead would need a lock
            and would give up the ordering the queue provides.
            """
            try:
                result = future.result()
            except (RequestException, RuntimeError, ValueError) as error:
                logger.warning(
                    'Weather retrieval failed (%s); continuing.',
                    type(error).__name__,
                )
                counts['failed'] += 1
                return

            # Flush per record so an interrupted run keeps what it has;
            # a buffered write would lose the last few hours.
            file.write(result.model_dump_json() + '\n')
            file.flush()
            counts['pulled'] += 1

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            # A queue rather than a set: taking the oldest request first
            # keeps the written order equal to the order records came
            # in, while the rest of the window carries on in the
            # background. Draining whichever finished first would make
            # the cache's order depend on the network.
            pending: deque[Future] = deque()
            queue_depth = max_workers * QUEUE_DEPTH_PER_WORKER
            try:
                for query in queries:
                    pending.append(
                        pool.submit(_fetch_with_backoff, query, delay_seconds)
                    )
                    # Write through once the window is full, so memory
                    # stays bounded on a 43,000 record generator.
                    while len(pending) >= queue_depth:
                        record(pending.popleft())
            finally:
                # Drain even if the records ran out early or the caller
                # interrupted: work already paid for should reach disk.
                while pending:
                    record(pending.popleft())

    logger.info(
        'Weather pull complete: %d retrieved, %d skipped, %d failed.',
        counts['pulled'], counts['skipped'], counts['failed'],
    )
    return path
