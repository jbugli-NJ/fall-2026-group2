"""
Tests for building NASA POWER queries from Montandon records.
"""

# Imports

from datetime import date, datetime, timezone

import pytest

from monty_tool.api_schemas import MontandonItem
from monty_tool.event_context import EventContext
from monty_tool.weather import query as weather_query
from monty_tool.weather.query import build_weather_query


# Test object helpers

# Pinned so windows that clamp to "today" stay stable as the suite ages.
TODAY = date(2026, 9, 26)


class _FixedDate(date):
    """
    A `date` whose `today()` never moves.
    """

    @classmethod
    def today(cls) -> date:
        return TODAY


@pytest.fixture(autouse=True)
def _freeze_today(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(weather_query, 'date', _FixedDate)


def _item(**overrides) -> MontandonItem:
    """
    A point-located event Item, for overriding per test.
    """
    geometry = overrides.pop('geometry', {'type': 'Point', 'coordinates': [-76.83, 4.42]})
    properties = {
        'roles': ['event'],
        'title': 'Flood',
        'description': 'River flooding.',
        'keywords': ['flood'],
        'datetime': '2026-09-10T00:00:00Z',
        'start_datetime': '2026-09-10T00:00:00Z',
        'end_datetime': '2026-09-12T00:00:00Z',
        'monty:corr_id': 'correlation-1',
        'monty:hazard_codes': ['FL'],
        'monty:country_codes': ['COL'],
        'monty:episode_number': 1,
    }
    properties.update(overrides.pop('properties', {}))
    return MontandonItem.model_validate({
        'id': 'gdacs-event-1',
        'collection': 'gdacs-events',
        'bbox': [-76.83, 4.42, -76.83, 4.42],
        'geometry': geometry,
        'links': [],
        'properties': properties,
        **overrides,
    })


def _context(**overrides) -> EventContext:
    """
    An `EventContext` built directly, bypassing Item validation.
    """
    fields = {
        'item_id': 'event-1',
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


# Tests: the window

def test_window_extends_before_and_after_the_event():
    """
    Confirms the default window pads the event on both sides.
    """
    result = build_weather_query(_item())
    assert result is not None

    # Event runs 10-12 Sept; 7 days before and 1 after.
    assert result.start_date == date(2026, 9, 3)
    assert result.end_date == date(2026, 9, 13)


def test_window_is_weighted_towards_the_run_up():
    """
    Confirms more days are taken before the event than after.

    The rain that causes a flood falls beforehand, so the antecedent
    period carries the signal; this is the opposite of the news window.
    """
    assert weather_query.DEFAULT_DAYS_BEFORE > weather_query.DEFAULT_DAYS_AFTER


def test_window_padding_is_configurable():
    """
    Confirms callers can widen or narrow the window.
    """
    result = build_weather_query(_item(), days_before=0, days_after=0)
    assert result is not None

    assert result.start_date == date(2026, 9, 10)
    assert result.end_date == date(2026, 9, 12)


def test_negative_padding_is_rejected():
    """
    Confirms negative padding fails rather than silently inverting the window.
    """
    with pytest.raises(ValueError, match='must not be negative'):
        build_weather_query(_item(), days_before=-1)


def test_window_end_is_clamped_to_today():
    """
    Confirms a window never runs past today, where POWER has no data.
    """
    result = build_weather_query(_context(
        end_datetime=datetime(2026, 9, 30, tzinfo=timezone.utc),
    ))
    assert result is not None

    assert result.end_date == TODAY


# Tests: accepted inputs

def test_accepts_an_event_context_directly():
    """
    Confirms a prepared context is usable without rebuilding it, matching
    `build_news_query`.
    """
    from_item = build_weather_query(_item())
    from_context = build_weather_query(_context(item_id='gdacs-event-1'))
    assert from_item is not None
    assert from_context is not None

    assert from_item.start_date == from_context.start_date
    assert from_item.end_date == from_context.end_date
    assert from_item.latitude == from_context.latitude


def test_query_carries_the_record_identity():
    """
    Confirms results stay joinable to the record and point they came from.
    """
    result = build_weather_query(_item())

    assert result is not None
    assert result.item_id == 'gdacs-event-1'
    assert (result.latitude, result.longitude) == (4.42, -76.83)


# Tests: refused records

@pytest.mark.parametrize('geometry', [
    {'type': 'Polygon', 'coordinates': [[[0, 0], [0, 1], [1, 1], [0, 0]]]},
    None,
])
def test_records_without_a_point_are_skipped(geometry):
    """
    Confirms non-point records return None instead of using a bbox centre.

    A country-level Polygon's bounding box can span a continent, so its
    centre is not where the event happened.
    """
    assert build_weather_query(_item(geometry=geometry)) is None


def test_events_before_power_coverage_are_refused():
    """
    Confirms pre-1981 events fail with a clear message rather than a 422.
    """
    with pytest.raises(ValueError, match='before POWER coverage'):
        build_weather_query(_context(
            start_datetime=datetime(1975, 6, 1, tzinfo=timezone.utc),
            end_datetime=datetime(1975, 6, 3, tzinfo=timezone.utc),
        ))


def test_padding_that_predates_coverage_is_trimmed_not_refused():
    """
    Confirms only the padding is clipped when the event itself is covered.
    """
    result = build_weather_query(_context(
        start_datetime=datetime(1981, 1, 3, tzinfo=timezone.utc),
        end_datetime=datetime(1981, 1, 4, tzinfo=timezone.utc),
    ))
    assert result is not None

    # 7 days before 3 Jan 1981 would be 27 Dec 1980, outside coverage.
    assert result.start_date == date(1981, 1, 1)
    assert result.end_date == date(1981, 1, 5)


def test_future_events_are_refused():
    """
    Confirms forecast-dated records fail rather than returning empty.

    POWER answers a future range with an empty series and HTTP 200, which
    would otherwise pass for a successful all-missing result.
    """
    with pytest.raises(ValueError, match='in the future'):
        build_weather_query(_context(
            start_datetime=datetime(2027, 1, 1, tzinfo=timezone.utc),
            end_datetime=datetime(2027, 1, 3, tzinfo=timezone.utc),
        ))


def test_backwards_event_range_is_refused():
    """
    Confirms a record ending before it starts cannot produce a query.

    `MontandonItem` does not enforce the ordering, so this reaches here.
    """
    with pytest.raises(ValueError, match='unusable date range'):
        build_weather_query(
            _context(
                start_datetime=datetime(2026, 9, 20, tzinfo=timezone.utc),
                end_datetime=datetime(2026, 8, 1, tzinfo=timezone.utc),
            ),
            days_before=0,
            days_after=0,
        )
