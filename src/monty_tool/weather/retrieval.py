"""
Retrieve weather for Montandon records.
"""

# Imports

import logging
import time
from collections import deque
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor

from requests import RequestException

from monty_tool.api_schemas import MontandonItem
from monty_tool.weather.api import PowerRateLimitError, get_weather_data
from monty_tool.weather.query import build_weather_query
from monty_tool.weather.schemas import WeatherQuery, WeatherResult


logger = logging.getLogger(__name__)


# Resources

POWER_REQUEST_DELAY_SECONDS = 0.2

# Capping workers at 5 in line with docs:
# https://power.larc.nasa.gov/docs/tutorials/service-data-request/api/#__tabbed_2_2
MAX_WORKERS = 5

# Bound queued requests while collecting results in input order.
QUEUE_DEPTH_PER_WORKER = 4

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


def _iter_queries(
    items: list[MontandonItem],
    padding: dict[str, int],
    counts: dict[str, int],
    ) -> Iterator[WeatherQuery]:
    """
    Yield queries for records POWER can answer, skipping unsupported records.
    """
    for item in items:
        try:
            query = build_weather_query(item, **padding)
        except ValueError as error:
            logger.debug('Skipping %s: %s', item.id, error)
            counts['skipped'] += 1
            continue
        if query is None:
            counts['skipped'] += 1
            continue
        yield query


def pull_event_weather(
    items: list[MontandonItem],
    max_workers: int = MAX_WORKERS,
    delay_seconds: float = POWER_REQUEST_DELAY_SECONDS,
    days_before: int | None = None,
    days_after: int | None = None,
    ) -> list[WeatherResult]:
    """
    Fetch weather for Montandon Items and return results in input order.

    Records POWER cannot answer for are logged and skipped. Failed requests
    are logged and omitted, while rate-limited requests are retried with
    backoff. Each call fetches fresh data and performs no local caching.
    """
    if max_workers < 1:
        raise ValueError('max_workers must be at least 1.')

    padding = {}
    if days_before is not None:
        padding['days_before'] = days_before
    if days_after is not None:
        padding['days_after'] = days_after

    counts = {'pulled': 0, 'skipped': 0, 'failed': 0}
    queries = _iter_queries(items, padding, counts)
    results: list[WeatherResult] = []

    def record(query: WeatherQuery, future: Future[WeatherResult]) -> None:
        """
        Collect one finished result, or log its failure.
        """
        try:
            result = future.result()
        except (RequestException, RuntimeError, ValueError) as error:
            logger.warning(
                'Weather retrieval failed for %s (%s); continuing.',
                query.item_id, type(error).__name__,
            )
            counts['failed'] += 1
            return
        results.append(result)
        counts['pulled'] += 1

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        pending: deque[tuple[WeatherQuery, Future[WeatherResult]]] = deque()
        queue_depth = max_workers * QUEUE_DEPTH_PER_WORKER
        for query in queries:
            pending.append((
                query, pool.submit(_fetch_with_backoff, query, delay_seconds),
            ))
            while len(pending) >= queue_depth:
                record(*pending.popleft())
        while pending:
            record(*pending.popleft())

    logger.info(
        'Weather pull complete: %d retrieved, %d skipped, %d failed.',
        counts['pulled'], counts['skipped'], counts['failed'],
    )
    return results
