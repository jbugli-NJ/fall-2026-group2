"""
Build NASA POWER queries from Montandon records or shared event context.
"""

from __future__ import annotations

from datetime import date, timedelta

from monty_tool.api_schemas import MontandonItem
from monty_tool.event_context import EventContext, build_event_context
from monty_tool.weather_api import POWER_START_DATE
from monty_tool.weather.schemas import WeatherQuery


# Weather before an event is the causal signal, not a footnote: the
# rainfall that produces a flood falls in the days leading up to it. This
# window is therefore weighted backwards, unlike the news window in
# `news/query.py`, where reporting follows the event.
DEFAULT_DAYS_BEFORE = 7
DEFAULT_DAYS_AFTER = 1


def build_weather_query(
    item: MontandonItem | EventContext,
    days_before: int = DEFAULT_DAYS_BEFORE,
    days_after: int = DEFAULT_DAYS_AFTER,
    ) -> WeatherQuery:
    """
    Build a weather query for one point-located record.

    Raises `ValueError` for records POWER cannot answer for: those with
    no point geometry, and those predating its coverage.
    """
    if days_before < 0 or days_after < 0:
        raise ValueError('days_before and days_after must not be negative.')

    if isinstance(item, EventContext):
        context = item
    else:
        context = build_event_context(item)

    # `EventContext` only carries coordinates for Point geometry, and
    # that restriction is the reason: a country-level Polygon's bounding
    # box can span a continent, so its centre is not where the event
    # happened. Those records need a spatial step of their own before
    # they can be given a point.
    if context.longitude is None or context.latitude is None:
        raise ValueError(
            f'{context.item_id!r} has no point geometry '
            f'(geometry_type={context.geometry_type!r}); '
            f'POWER needs a single point.'
        )

    event_start = context.start_datetime.date()
    event_end = context.end_datetime.date()

    # An event POWER has no era for cannot be answered at all, so this is
    # a different failure from a window that merely reaches too far back.
    if event_start < POWER_START_DATE:
        raise ValueError(
            f'{context.item_id!r} starts {event_start.isoformat()}, '
            f'before POWER coverage begins '
            f'{POWER_START_DATE.isoformat()}.'
        )

    today = date.today()

    # Forecast-style records dated ahead of today get no data: POWER
    # answers future ranges with an empty series rather than an error,
    # which would otherwise look like a successful all-missing result.
    if event_start > today:
        raise ValueError(
            f'{context.item_id!r} starts {event_start.isoformat()}, '
            f'in the future; POWER has no forecast data.'
        )

    # Trim the window rather than failing when only the padding falls
    # outside coverage; the event itself is still answerable.
    from_date = max(
        event_start - timedelta(days=days_before),
        POWER_START_DATE,
    )

    to_date = min(event_end + timedelta(days=days_after), today)

    # Records whose end precedes their start survive `MontandonItem`
    # validation, so clamping above can invert the range.
    if from_date > to_date:
        raise ValueError(
            f'{context.item_id!r} has an unusable date range: '
            f'{from_date.isoformat()} to {to_date.isoformat()}.'
        )

    return WeatherQuery(
        item_id=context.item_id,
        latitude=context.latitude,
        longitude=context.longitude,
        start_date=from_date,
        end_date=to_date,
    )
