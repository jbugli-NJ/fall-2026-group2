"""
Tests for weather summaries attached to existing graph records.
"""

from datetime import date
import gzip

import pytest

from monty_tool.network.weather import (
    validated_weather_node_data,
    weather_result_to_node_data,
)
from monty_tool.weather.schemas import WeatherDay, WeatherResult


def _result(**changes):
    """
    Build a partial retrieval with zero rain and distinct temperature parameters.
    """
    data = {
        'item_id': 'event-1', 'latitude': 10, 'longitude': 20, 'elevation': 0,
        'start_date': date(2024, 1, 1), 'end_date': date(2024, 1, 3),
        'sources': ['MERRA2'],
        'units': {'PRECTOTCORR': 'mm/day', 'T2M': 'C', 'T2M_MAX': 'C', 'T2M_MIN': 'C', 'WS10M': 'm/s'},
        'days': [
            WeatherDay(date=date(2024, 1, 1), precipitation=2, temperature_mean=20,
                       temperature_max=30, temperature_min=5, wind_speed=2),
            WeatherDay(date=date(2024, 1, 3), precipitation=8, temperature_mean=10,
                       temperature_max=25, temperature_min=-2, wind_speed=5),
        ],
    }
    return WeatherResult.model_validate(data | changes)


def test_partial_summary_keeps_parameter_coverage_and_units():
    """
    Count coverage separately and accumulate one-day rain readings in millimeters.
    """
    node = weather_result_to_node_data(_result())
    props = node['properties']
    assert props['weather_expected_days'] == 3
    assert (props['weather_elevation'], props['weather_elevation_unit']) == (0, 'm')
    assert props['weather_temperature_mean_unit'] == 'C'
    assert props['weather_precipitation_measured_days'] == 2
    assert props['weather_temperature_max_measured_days'] == 2
    assert props['weather_observed_precipitation_total'] == 10
    assert props['weather_observed_precipitation_total_unit'] == 'mm'
    assert props['weather_precipitation_unit'] == 'mm/day'
    assert props['weather_peak_daily_precipitation_date'] == date(2024, 1, 3)
    assert props['weather_max_temperature'] == 30
    assert props['weather_min_temperature'] == -2
    assert props['weather_mean_temperature'] == 15
    assert props['weather_max_daily_mean_wind_speed'] == 5


@pytest.mark.parametrize('values, expected', [
    ([0, None, 10], (5, 2)), ([None, None, None], (None, 0)), ([0, 0, 0], (0, 3)),
])
def test_temperature_summary_with_missing_readings(values, expected):
    """
    Average measured daily means without using them as maximum or minimum readings.
    """
    days = [WeatherDay(date=date(2024, 1, i), temperature_mean=value)
            for i, value in enumerate(values, start=1)]
    props = weather_result_to_node_data(_result(days=days))['properties']
    assert (props['weather_mean_temperature'], props['weather_temperature_mean_measured_days']) == expected
    assert props['weather_max_temperature'] is None
    assert props['weather_min_temperature'] is None


@pytest.mark.parametrize('rain, expected', [(None, (None, None, 0)), (0, (0, 0, 3))])
def test_rain_summary_distinguishes_missing_and_zero(rain, expected):
    """
    Preserve the distinction between no measurements and measured dry days.
    """
    days = [WeatherDay(date=date(2024, 1, i), precipitation=rain) for i in (1, 2, 3)]
    props = weather_result_to_node_data(_result(days=days))['properties']
    assert (
        props['weather_observed_precipitation_total'],
        props['weather_peak_daily_precipitation'],
        props['weather_precipitation_measured_days'],
    ) == expected


@pytest.mark.parametrize('unit', ['inches/day', None])
def test_rain_unit_is_required_and_supported(unit):
    """
    Reject readings with missing or unsupported units.
    """
    result = _result()
    if unit is None:
        result.units.pop('PRECTOTCORR')
    else:
        result.units['PRECTOTCORR'] = unit
    with pytest.raises(ValueError, match='unit'):
        weather_result_to_node_data(result)


def test_invalid_json_has_path_and_line(tmp_path):
    """
    Identify a malformed JSON row precisely.
    """
    path = tmp_path / 'weather.jsonl.gz'
    with gzip.open(path, 'wt') as file:
        file.write(_result().model_dump_json() + '\nnot json\n')
    with pytest.raises(ValueError, match=r'weather.jsonl.gz at line 2'):
        list(validated_weather_node_data(path))
