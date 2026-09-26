"""
Schemas for weather search parameters and NASA POWER responses.

Flow:
MontandonItem -> WeatherQuery -> weather_api.py -> POWER -> POWERResponse -> WeatherResult
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from monty_tool.weather_api import FILL_VALUE


class WeatherQuery(BaseModel):
    """
    Search parameters generated from a Montandon disaster record.

    Carries `item_id` so results can be joined back to the record they
    came from, the same way `NewsQuery` does.
    """

    item_id: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    start_date: date
    end_date: date

    @model_validator(mode='after')
    def validate_date_range(self) -> WeatherQuery:
        if self.start_date > self.end_date:
            raise ValueError('start_date must be on or before end_date.')
        return self


# POWER response schemas

class POWERHeader(BaseModel):
    """
    The `header` block of a POWER response.
    """

    model_config = ConfigDict(extra='ignore')

    # Which reanalysis the values came from, e.g. MERRA2. Worth keeping:
    # it is the provenance for every value in the response.
    sources: list[str] = Field(default_factory=list)
    fill_value: float = FILL_VALUE
    start: str
    end: str


class POWERParameterInfo(BaseModel):
    """
    Units and full name for one parameter, from the `parameters` block.
    """

    model_config = ConfigDict(extra='ignore')

    units: str
    longname: str


class POWERResponse(BaseModel):
    """
    Validated response returned directly by POWER.

    POWER nests values parameter-first, then date:
    `properties.parameter.T2M.20240101`. That shape is preserved here and
    transposed into per-day rows by `WeatherResult`.
    """

    model_config = ConfigDict(extra='ignore')

    geometry: dict
    properties: dict
    header: POWERHeader
    parameters: dict[str, POWERParameterInfo] = Field(default_factory=dict)


# Result schemas

class WeatherDay(BaseModel):
    """
    One day of weather at one point.

    Every measurement is optional: POWER lags real time, so days inside
    a requested range can come back with no data. A missing value is
    None here, never the -999.0 sentinel POWER sends.
    """

    date: date
    temperature_mean: float | None = None
    temperature_max: float | None = None
    temperature_min: float | None = None
    precipitation: float | None = None
    wind_speed: float | None = None


# Maps POWER's parameter names onto `WeatherDay` fields. Keeping this
# next to the model means adding a parameter is a one-line change in
# two places, not a rewrite of the transpose.
PARAMETER_FIELDS = {
    'T2M': 'temperature_mean',
    'T2M_MAX': 'temperature_max',
    'T2M_MIN': 'temperature_min',
    'PRECTOTCORR': 'precipitation',
    'WS10M': 'wind_speed',
}


class WeatherResult(BaseModel):
    """
    POWER results linked back to the originating Montandon record.
    """

    item_id: str
    latitude: float
    longitude: float
    start_date: date
    end_date: date

    # POWER echoes the requested point back rather than snapping to a
    # grid, so it is not worth storing twice. Elevation is the one thing
    # the response adds, and it is a useful sanity check: a land event
    # reporting 0.0 m usually means the coordinates are wrong.
    elevation: float | None = None

    sources: list[str] = Field(default_factory=list)
    units: dict[str, str] = Field(default_factory=dict)
    days: list[WeatherDay] = Field(default_factory=list)

    @property
    def missing_days(self) -> int:
        """
        Days in range that POWER returned no measurements for.
        """
        return sum(
            1 for day in self.days
            if all(
                getattr(day, field) is None
                for field in PARAMETER_FIELDS.values()
            )
        )

    @property
    def is_complete(self) -> bool:
        """
        Whether every day in the requested range carries measurements.
        """
        return self.missing_days == 0

    def summary(self) -> dict[str, Any]:
        """
        Reduce the series to the few figures that describe the window.

        A full series is a row per day per measurement, which is more
        than an assistant's prompt budget allows and more than a graph
        node should carry. Peaks and totals are what the window is
        actually asked about: how much rain fell, how hot it got.

        Every figure is None when nothing was measured, so an absence
        is never reported as a zero.
        """
        def values(field: str) -> list[float]:
            return [
                value for value in
                (getattr(day, field) for day in self.days)
                if value is not None
            ]

        precipitation = values('precipitation')

        # Pair each reading with its date before taking the peak, so the
        # comparison runs on values already narrowed to float. Taking it
        # over the days themselves leaves the key returning float | None,
        # which has no ordering.
        measured_rain = [
            (day.date, day.precipitation)
            for day in self.days
            if day.precipitation is not None
        ]
        peak_rain = max(measured_rain, key=lambda pair: pair[1], default=None)
        maxima = values('temperature_max') or values('temperature_mean')
        minima = values('temperature_min') or values('temperature_mean')
        winds = values('wind_speed')

        return {
            'start_date': self.start_date.isoformat(),
            'end_date': self.end_date.isoformat(),
            'days': len(self.days),
            'missing_days': self.missing_days,
            'total_precipitation': (
                round(sum(precipitation), 2) if precipitation else None
            ),
            'peak_precipitation': (
                round(peak_rain[1], 2) if peak_rain else None
            ),
            'peak_precipitation_date': (
                peak_rain[0].isoformat() if peak_rain else None
            ),
            'max_temperature': max(maxima) if maxima else None,
            'min_temperature': min(minima) if minima else None,
            'max_wind_speed': max(winds) if winds else None,
            'units': self.units,
        }


def build_weather_result(
    query: WeatherQuery,
    payload: dict,
    ) -> WeatherResult:
    """
    Transpose a raw POWER payload into per-day rows for one record.

    POWER's own `fill_value` is read from the response header rather
    than assumed, and every occurrence becomes None.
    """
    response = POWERResponse.model_validate(payload)
    fill_value = response.header.fill_value

    parameter_values = response.properties.get('parameter', {})

    # Collect each date once across all parameters; a parameter can be
    # short of the full range without the others being.
    day_values: dict[date, dict[str, float]] = {}
    for parameter, values in parameter_values.items():
        field = PARAMETER_FIELDS.get(parameter)
        if field is None:
            continue
        for stamp, value in values.items():
            if value == fill_value:
                continue
            day = datetime.strptime(stamp, '%Y%m%d').date()
            day_values.setdefault(day, {})[field] = value

    # Emit a row for every requested day, not just the days POWER
    # answered for. A dense series means downstream code can count gaps
    # and align events by day offset; dropping the gaps instead would
    # make a truncated range look like a complete short one.
    days = []
    day = query.start_date
    while day <= query.end_date:
        days.append(WeatherDay(date=day, **day_values.get(day, {})))
        day += timedelta(days=1)

    coordinates = response.geometry.get('coordinates') or []

    return WeatherResult(
        item_id=query.item_id,
        latitude=query.latitude,
        longitude=query.longitude,
        start_date=query.start_date,
        end_date=query.end_date,
        elevation=coordinates[2] if len(coordinates) > 2 else None,
        sources=response.header.sources,
        units={
            name: info.units
            for name, info in response.parameters.items()
        },
        days=days,
    )
