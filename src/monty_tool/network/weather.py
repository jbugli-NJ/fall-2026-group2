"""
Utilities to convert stored NASA POWER results and attach summaries to existing graph nodes.
"""

# Imports

from collections.abc import Iterator
from pathlib import Path
from statistics import fmean

from neo4j import Driver, ManagedTransaction

from monty_tool.network.schemas import WeatherNodeData, WeatherNodeProperties
from monty_tool.tools.resources import read_gzip
from monty_tool.weather.schemas import PARAMETER_FIELDS, WeatherResult


# Resources

SUPPORTED_UNITS = {
    'T2M': 'C',
    'T2M_MAX': 'C',
    'T2M_MIN': 'C',
    'PRECTOTCORR': 'mm/day',
    'WS10M': 'm/s',
}


# Utilities

def weather_result_to_node_data(result: WeatherResult) -> WeatherNodeData:
    """
    Convert a validated weather result into flat graph summary properties.
    """
    readings: dict[str, list[float]] = {
        field: [value for day in result.days if (value := getattr(day, field)) is not None]
        for field in PARAMETER_FIELDS.values()
    }

    for parameter, expected_unit in SUPPORTED_UNITS.items():
        unit = result.units.get(parameter)
        if unit is not None and unit != expected_unit:
            raise ValueError(f'Unsupported {parameter} unit {unit!r}; expected {expected_unit!r}.')
        if readings[PARAMETER_FIELDS[parameter]] and unit is None:
            raise ValueError(f'Missing unit for measured parameter {parameter}.')

    measured_rain = sorted(
        ((day.date, day.precipitation) for day in result.days if day.precipitation is not None),
        key=lambda pair: pair[0],
    )
    peak_rain = max(measured_rain, key=lambda pair: pair[1], default=None)
    properties: WeatherNodeProperties = {
        'weather_source': 'NASA POWER',
        'weather_sources': result.sources,
        'weather_latitude': result.latitude,
        'weather_longitude': result.longitude,
        'weather_elevation': result.elevation,
        'weather_elevation_unit': 'm' if result.elevation is not None else None,
        'weather_start_date': result.start_date,
        'weather_end_date': result.end_date,
        'weather_expected_days': (result.end_date - result.start_date).days + 1,
        'weather_precipitation_measured_days': len(readings['precipitation']),
        'weather_temperature_mean_measured_days': len(readings['temperature_mean']),
        'weather_temperature_max_measured_days': len(readings['temperature_max']),
        'weather_temperature_min_measured_days': len(readings['temperature_min']),
        'weather_wind_speed_measured_days': len(readings['wind_speed']),
        # Each mm/day reading describes one day, so its accumulated amount is mm.
        'weather_observed_precipitation_total': (
            round(sum(readings['precipitation']), 2) if readings['precipitation'] else None
        ),
        'weather_peak_daily_precipitation': peak_rain[1] if peak_rain else None,
        'weather_peak_daily_precipitation_date': peak_rain[0] if peak_rain else None,
        'weather_mean_temperature': (
            fmean(readings['temperature_mean']) if readings['temperature_mean'] else None
        ),
        'weather_max_temperature': max(readings['temperature_max'], default=None),
        'weather_min_temperature': min(readings['temperature_min'], default=None),
        'weather_max_daily_mean_wind_speed': max(readings['wind_speed'], default=None),
        'weather_precipitation_unit': result.units.get('PRECTOTCORR'),
        'weather_observed_precipitation_total_unit': 'mm' if measured_rain else None,
        'weather_temperature_mean_unit': result.units.get('T2M'),
        'weather_temperature_max_unit': result.units.get('T2M_MAX'),
        'weather_temperature_min_unit': result.units.get('T2M_MIN'),
        'weather_wind_speed_unit': result.units.get('WS10M'),
    }
    return {'item_id': result.item_id, 'properties': properties}


def validated_weather_node_data(path: Path) -> Iterator[WeatherNodeData]:
    """
    Read weather rows with validation errors identifying their path and line.
    """
    line_number = 1
    try:
        for record in read_gzip(path):
            yield weather_result_to_node_data(WeatherResult.model_validate(record))
            line_number += 1
    except ValueError as error:
        raise ValueError(f'Invalid weather data in {path} at line {line_number}: {error}') from error


def insert_weather_properties(driver: Driver, node_data: list[WeatherNodeData]) -> int:
    """
    Match exact IDs and attach weather atomically, rejecting missing targets.
    """
    def attach(tx: ManagedTransaction) -> int:
        """
        Check every target before updating the batch in the same transaction.
        """
        missing = tx.run(
            """
            UNWIND $items AS item
            OPTIONAL MATCH (node:MontandonItem {id: item.item_id})
            WITH item, node WHERE node IS NULL
            RETURN item.item_id AS item_id
            LIMIT 10
            """,
            items=node_data,
        )
        missing_ids = [record['item_id'] for record in missing]
        if missing_ids:
            raise ValueError(f'Weather IDs have no matching Montandon node: {missing_ids!r}')
        result = tx.run(
            """
            UNWIND $items AS item
            MATCH (node:MontandonItem {id: item.item_id})
            SET node += item.properties
            RETURN count(node) AS attached
            """,
            items=node_data,
        )
        return result.single(strict=True)['attached']

    with driver.session(database='neo4j') as session:
        return session.execute_write(attach)
