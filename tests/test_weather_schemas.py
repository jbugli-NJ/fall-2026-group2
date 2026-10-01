"""
Tests for the NASA POWER response schemas and their transpose.
"""

# Imports

from datetime import date

import pytest
from pydantic import ValidationError

from monty_tool.weather.schemas import POWERResponse, WeatherQuery


# Test object helpers

def _query(**overrides) -> WeatherQuery:
    """
    A valid query over a three-day window, for overriding per test.
    """
    fields = {
        'item_id': 'gdacs-event-1',
        'latitude': 38.9,
        'longitude': -77.03,
        'start_date': date(2024, 1, 1),
        'end_date': date(2024, 1, 3),
    }
    fields.update(overrides)
    return WeatherQuery(**fields)


def _payload(parameter: dict | None = None, **overrides) -> POWERResponse:
    """
    A POWER response shaped like the live API's, with real field names.
    """
    payload = {
        'geometry': {'type': 'Point', 'coordinates': [-77.03, 38.9, 70.81]},
        'properties': {
            'parameter': {
                'T2M': {'20240101': 2.94, '20240102': 1.31, '20240103': 1.84},
                'PRECTOTCORR': {'20240101': 0.28, '20240102': 0.0, '20240103': 0.0},
            } if parameter is None else parameter,
        },
        'header': {
            'sources': ['MERRA2'],
            'fill_value': -999.0,
            'start': '20240101',
            'end': '20240103',
        },
        'parameters': {
            'T2M': {'units': 'C', 'longname': 'Temperature at 2 Meters'},
            'PRECTOTCORR': {'units': 'mm/day', 'longname': 'Precipitation Corrected'},
        },
    }
    payload.update(overrides)
    return POWERResponse.model_validate(payload)


# Tests: query validation

def test_query_rejects_out_of_range_coordinates():
    """
    Confirms coordinates outside the globe are refused before any request.
    """
    with pytest.raises(ValidationError):
        _query(latitude=91.0)
    with pytest.raises(ValidationError):
        _query(longitude=-181.0)


def test_query_rejects_backwards_date_range():
    """
    Confirms a range running backwards in time is refused.
    """
    with pytest.raises(ValidationError, match='on or before'):
        _query(start_date=date(2024, 2, 1), end_date=date(2024, 1, 1))


def test_query_allows_single_day_range():
    """
    Confirms a one-day window is valid, since many events last a day.
    """
    query = _query(start_date=date(2024, 1, 1), end_date=date(2024, 1, 1))
    assert query.start_date == query.end_date


# Tests: fill values

def test_fill_values_become_none():
    """
    Confirms POWER's -999.0 sentinel never reaches a measurement field.

    A -999.0 left in place averages silently instead of failing, so this
    is the difference between a missing day and a corrupt one.
    """
    result = _payload({
        'T2M': {'20240101': 2.94, '20240102': -999.0, '20240103': 1.84},
        'PRECTOTCORR': {'20240101': -999.0, '20240102': 0.0, '20240103': 0.0},
    }).to_weather_result(_query())

    assert [day.temperature_mean for day in result.days] == [2.94, None, 1.84]
    assert [day.precipitation for day in result.days] == [None, 0.0, 0.0]


def test_fill_value_is_read_from_the_response_header():
    """
    Confirms the sentinel comes from the payload, not a hardcoded default.
    """
    payload = _payload({'T2M': {'20240101': -77.0, '20240102': 1.31, '20240103': 1.84}})
    payload.header.fill_value = -77.0

    result = payload.to_weather_result(_query())

    assert [day.temperature_mean for day in result.days] == [None, 1.31, 1.84]


def test_zero_is_kept_as_a_measurement():
    """
    Confirms a real zero is not mistaken for a missing value.
    """
    result = _payload({
        'PRECTOTCORR': {'20240101': 0.0, '20240102': 0.0, '20240103': 0.0},
    }).to_weather_result(_query())

    assert [day.precipitation for day in result.days] == [0.0, 0.0, 0.0]
    assert result.missing_days == 0


# Tests: the day series

def test_every_requested_day_gets_a_row():
    """
    Confirms days POWER omits still appear, so a truncated range does not
    pass for a complete shorter one.

    POWER lags real time, so a window running up to today routinely comes
    back short.
    """
    result = _payload().to_weather_result(_query(end_date=date(2024, 1, 5)))

    assert [day.date for day in result.days] == [
        date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3),
        date(2024, 1, 4), date(2024, 1, 5),
    ]
    assert result.days[3].temperature_mean is None
    assert result.days[4].precipitation is None


def test_missing_days_counts_only_fully_empty_days():
    """
    Confirms a day holding any measurement is not counted as missing.
    """
    result = _payload({
        'T2M': {'20240101': 2.94, '20240102': -999.0},
        'PRECTOTCORR': {'20240101': 0.28, '20240102': 1.5},
    }).to_weather_result(_query(end_date=date(2024, 1, 4)))

    # 1 Jan is full, 2 Jan holds precipitation only, 3 and 4 Jan are absent.
    assert result.missing_days == 2
    assert result.is_complete is False


def test_complete_range_reports_complete():
    """
    Confirms a fully populated range is reported as complete.
    """
    result = _payload().to_weather_result(_query())

    assert result.missing_days == 0
    assert result.is_complete is True


def test_days_are_ordered_chronologically():
    """
    Confirms the series is sorted by date, not by POWER's key order.
    """
    result = _payload({
        'T2M': {'20240103': 1.84, '20240101': 2.94, '20240102': 1.31},
    }).to_weather_result(_query())

    assert [day.date.day for day in result.days] == [1, 2, 3]
    assert [day.temperature_mean for day in result.days] == [2.94, 1.31, 1.84]


def test_dates_outside_the_requested_range_are_dropped():
    """
    Confirms the series covers the requested window and nothing else.
    """
    result = _payload({
        'T2M': {'20231231': 9.9, '20240101': 2.94, '20240104': 8.8},
    }).to_weather_result(_query())

    assert [day.date for day in result.days] == [
        date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3),
    ]
    assert result.days[0].temperature_mean == 2.94


# Tests: parameters and provenance

def test_unmapped_parameters_are_ignored():
    """
    Confirms a parameter with no `WeatherDay` field does not raise.

    Requesting an extra POWER parameter should not break parsing before
    the model has a field for it.
    """
    result = _payload({
        'T2M': {'20240101': 2.94, '20240102': 1.31, '20240103': 1.84},
        'RH2M': {'20240101': 70.0, '20240102': 71.0, '20240103': 72.0},
    }).to_weather_result(_query())

    assert result.days[0].temperature_mean == 2.94
    assert not hasattr(result.days[0], 'rh2m')


def test_units_and_sources_are_preserved():
    """
    Confirms units and provenance survive, so values are never bare numbers.
    """
    result = _payload().to_weather_result(_query())

    assert result.units['T2M'] == 'C'
    assert result.units['PRECTOTCORR'] == 'mm/day'
    assert result.sources == ['MERRA2']


def test_elevation_is_read_from_the_geometry():
    """
    Confirms elevation is taken from the third coordinate when present.
    """
    result = _payload().to_weather_result(_query())
    assert result.elevation == 70.81


def test_missing_elevation_is_none():
    """
    Confirms a two-coordinate geometry yields no elevation rather than raising.
    """
    result = _payload(
        geometry={'type': 'Point', 'coordinates': [-77.03, 38.9]},
    ).to_weather_result(_query())
    assert result.elevation is None


def test_result_carries_the_query_identity():
    """
    Confirms results stay joinable to the Montandon record they came from.
    """
    result = _payload().to_weather_result(_query(item_id='gdacs-event-42'))

    assert result.item_id == 'gdacs-event-42'
    assert (result.latitude, result.longitude) == (38.9, -77.03)
    assert result.start_date == date(2024, 1, 1)
    assert result.end_date == date(2024, 1, 3)


# Tests: the summary

def test_summary_reports_totals_and_peaks():
    """
    Confirms the summary carries the figures a window is asked about.
    """
    result = _payload({
        'T2M_MAX': {'20240101': 5.0, '20240102': 9.0, '20240103': 7.0, '20240104': 6.0},
        'T2M_MIN': {'20240101': 1.0, '20240102': -2.0, '20240103': 3.0, '20240104': 0.0},
        'PRECTOTCORR': {'20240101': 1.0, '20240102': 12.5, '20240103': 0.0, '20240104': 2.5},
        'WS10M': {'20240101': 3.0, '20240102': 8.5, '20240103': 2.0, '20240104': 4.0},
    }).to_weather_result(_query(end_date=date(2024, 1, 4)))

    summary = result.summary()

    assert summary['days'] == 4
    assert summary['total_precipitation'] == 16.0
    assert summary['peak_precipitation'] == 12.5
    assert summary['peak_precipitation_date'] == '2024-01-02'
    assert summary['max_temperature'] == 9.0
    assert summary['min_temperature'] == -2.0
    assert summary['max_wind_speed'] == 8.5


def test_summary_reports_absence_as_none_not_zero():
    """
    Confirms an unmeasured window yields nulls.

    Reporting 0 mm of rain for days POWER never covered would read as a
    dry spell rather than missing data.
    """
    result = _payload({
        'T2M': {'20240101': -999.0, '20240102': -999.0, '20240103': -999.0},
        'PRECTOTCORR': {'20240101': -999.0, '20240102': -999.0, '20240103': -999.0},
    }).to_weather_result(_query())

    summary = result.summary()

    assert summary['missing_days'] == 3
    assert summary['total_precipitation'] is None
    assert summary['peak_precipitation'] is None
    assert summary['peak_precipitation_date'] is None
    assert summary['max_temperature'] is None
    assert summary['max_wind_speed'] is None


def test_summary_falls_back_to_mean_temperature():
    """
    Confirms temperature is still reported when only T2M was requested.
    """
    result = _payload({
        'T2M': {'20240101': 2.0, '20240102': 8.0, '20240103': 5.0},
    }).to_weather_result(_query())

    summary = result.summary()

    assert summary['max_temperature'] == 8.0
    assert summary['min_temperature'] == 2.0


def test_summary_ignores_missing_days_in_its_totals():
    """
    Confirms gaps neither break the totals nor count towards them.
    """
    result = _payload({
        'PRECTOTCORR': {'20240101': 4.0, '20240103': 6.0},
    }).to_weather_result(_query(end_date=date(2024, 1, 5)))

    summary = result.summary()

    assert summary['days'] == 5
    assert summary['missing_days'] == 3
    assert summary['total_precipitation'] == 10.0
    assert summary['peak_precipitation_date'] == '2024-01-03'
