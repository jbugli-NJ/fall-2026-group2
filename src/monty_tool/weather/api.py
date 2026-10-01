"""
Minimal NASA POWER client to connect to the POWER API and
return validated weather data for coordinates.
"""

from datetime import date

import requests

from monty_tool.weather.schemas import FILL_VALUE as FILL_VALUE, POWERResponse

# POWER serves daily values for a single point per request; there is no
# bulk endpoint, so callers pulling many events should cache results
POWER_API_URL = 'https://power.larc.nasa.gov/api/temporal/daily/point'

# Parameters chosen to cover the hazards Montandon records most often:
# rainfall for floods, temperature for heatwaves, wind for storms.
DEFAULT_PARAMETERS = (
    'T2M',          # mean temperature at 2 m, degrees C
    'T2M_MAX',      # daily maximum temperature, degrees C
    'T2M_MIN',      # daily minimum temperature, degrees C
    'PRECTOTCORR',  # bias-corrected precipitation, mm/day
    'WS10M',        # mean wind speed at 10 m, m/s
)

# POWER groups its parameters into communities that determine which
# variables are offered and in what units. RE (Renewable Energy) is the
# one carrying the surface meteorology above.
DEFAULT_COMMUNITY = 'RE'

# POWER's daily series is MERRA-2 derived and starts here; earlier dates
# are rejected with a 422 rather than returned empty. GDACS records only
# reach back to 2000, but EM-DAT runs to 1900, so roughly an eighth of it
# sits outside POWER's coverage entirely.
POWER_START_DATE = date(1981, 1, 1)


class PowerRateLimitError(RuntimeError):
    """
    Raised when POWER refuses a request with HTTP 429.

    POWER publishes no rate limit; the team monitors usage and throttles
    to keep access equitable. Callers should back off rather than treat
    this as a permanent failure.
    """


def get_weather_data(
    lat: float,
    lon: float,
    start: date,
    end: date,
    *,
    parameters: tuple[str, ...] = DEFAULT_PARAMETERS,
    community: str = DEFAULT_COMMUNITY,
    ) -> POWERResponse:
    """
    Retrieve daily weather for one point over a date range.

    `lat`/`lon` must describe an actual point. Only point-located
    Montandon records (GDACS) qualify; the bounding-box centre of a
    country-level Polygon record is not where the event happened.

    Returns a POWERResponse containing the fields used by weather processing,
    including `FILL_VALUE` entries for missing data.
    Call `model_dump()` for a nested Python dictionary or `model_dump_json()`
    for JSON. POWER lags real time, so recent events can come back short
    of their full range or empty.
    """
    if start > end:
        raise ValueError('start must be on or before end.')

    params = {
        'parameters': ','.join(parameters),
        'community': community,
        'longitude': lon,
        'latitude': lat,
        # POWER takes compact YYYYMMDD dates, not the ISO dates used
        # elsewhere in this project.
        'start': start.strftime('%Y%m%d'),
        'end': end.strftime('%Y%m%d'),
        'format': 'JSON',
    }

    response = requests.get(POWER_API_URL, params=params, timeout=30)

    # Separated from the errors below because it is the only retryable error
    if response.status_code == 429:
        raise PowerRateLimitError(
            'NASA POWER refused the request: HTTP 429 (too many requests).'
        )

    # POWER signals bad points, date ranges, and parameter names with a
    # 422 and an explanatory body, so the status line is the reliable
    # check. The payload's `messages` list is informational and stays
    # empty on failures, so it is not treated as an error here.
    if response.status_code != 200:
        raise RuntimeError(
            f'NASA POWER failed: HTTP {response.status_code} - '
            f'{response.text[:200]}'
        )

    return POWERResponse.model_validate(response.json())
