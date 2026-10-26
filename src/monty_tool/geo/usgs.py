"""
Earthquake shaking footprints from USGS ShakeMap.

Fetched from USGS directly rather than through Montandon: the
`usgs-hazards` ShakeMap Items carry a placeholder geometry, and in
October 2026 Montandon held only about 70% of the M5.5+ quakes in the
USGS catalog, every one of which had a ShakeMap.

ShakeMap publishes intensity as a grid (CoverageJSON) and as contour
lines. Contours that run off the grid edge do not close, so footprints
are built from the grid instead: cells are grouped into one polygon per
Modified Mercalli Intensity (MMI) band, from IV (light shaking) up.
"""

import math
from datetime import UTC, datetime
from typing import Any

import requests
from shapely import box, unary_union
from shapely.geometry import mapping

from monty_tool.geo.schemas import Footprint, extent_km

SOURCE = 'USGS'

EVENT_URL = 'https://earthquake.usgs.gov/fdsnws/event/1/query'

# Medium resolution is about 2 km per cell, and about 200 KB for a M7;
# low is coarse enough to show steps, high is four times the size.
GRID_CONTENT = 'download/coverage_mmi_medium_res.covjson'

FETCH_TIMEOUT = 120

# Light shaking (IV) is the lowest band that matters for impact; I-III
# would mostly add a wide ring of weak or unfelt shaking.
DEFAULT_MIN_MMI = 4

# Hazard codes Montandon gives USGS earthquakes, so ShakeMap footprints
# line up with Montandon records in the hazard crosswalk.
HAZARD_CODES = ['GH0101', 'EQ', 'nat-geo-ear-gro']

# Roman numeral, USGS shaking description and USGS colour per MMI band.
MMI_BANDS: dict[int, tuple[str, str, str]] = {
    1: ('I', 'Not felt', '#ffffff'),
    2: ('II', 'Weak', '#bfccff'),
    3: ('III', 'Weak', '#a0e6ff'),
    4: ('IV', 'Light', '#80ffff'),
    5: ('V', 'Moderate', '#7aff93'),
    6: ('VI', 'Strong', '#ffff00'),
    7: ('VII', 'Very strong', '#ffc800'),
    8: ('VIII', 'Severe', '#ff9100'),
    9: ('IX', 'Violent', '#ff0000'),
    10: ('X+', 'Extreme', '#c80000'),
}


def usgs_event_id(event_id: str) -> str:
    """
    The USGS event ID behind a USGS or Montandon ID, e.g.
    'usgs-hazard-us6000u43g-shakemap' -> 'us6000u43g'.
    """
    event_id = event_id.removeprefix('usgs-event-').removeprefix('usgs-hazard-')
    return event_id.removesuffix('-shakemap')


def mmi_band(value: float) -> int:
    """
    The MMI band a grid value falls in, as USGS rounds it: 5.5 to 6.5 is VI.
    """
    return min(10, max(1, math.floor(value + 0.5)))


def get_event(event_id: str) -> dict[str, Any]:
    """
    A USGS event's GeoJSON detail, including its products.
    """
    response = requests.get(
        EVENT_URL,
        params={'eventid': usgs_event_id(event_id), 'format': 'geojson'},
        timeout=FETCH_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def footprint_from_event(event: dict[str, Any]) -> Footprint | None:
    """
    The ShakeMap footprint of a USGS event, without downloading its grid,
    or None when the event has no ShakeMap.
    """
    shakemaps = event['properties'].get('products', {}).get('shakemap') or []
    if not shakemaps:
        return None
    shakemap = max(shakemaps, key=lambda product: product.get('preferredWeight', 0))
    grid = shakemap.get('contents', {}).get(GRID_CONTENT)
    if grid is None:
        return None

    properties = event['properties']
    status = shakemap['properties'].get('map-status', 'unknown status')
    version = shakemap['properties'].get('version', '?')
    return Footprint(
        source=SOURCE,
        item_id=f"usgs-shakemap-{event['id']}-v{version}",
        event_ref=event['id'],
        area_name=properties.get('place'),
        layer='shaking_intensity',
        label=f'Shaking intensity (ShakeMap v{version}, {status})',
        detail='modelled',
        datetime=datetime.fromtimestamp(properties['time'] / 1000, tz=UTC),
        hazard_codes=HAZARD_CODES,
        data_url=grid['url'],
        source_url=properties.get('url'),
        severity_property='mmi',
    )


def grid_to_geojson(
    coverage: dict[str, Any],
    min_mmi: int = DEFAULT_MIN_MMI,
    ) -> dict[str, Any]:
    """
    One feature per MMI band from a ShakeMap CoverageJSON grid, strongest
    shaking first.

    Each cell is treated as a square centred on its grid point. Runs of
    same-band cells along a row are merged before the union, which keeps
    a medium-resolution grid to a few thousand rectangles.
    """
    axes = coverage['domain']['axes']
    x_axis, y_axis = axes['x'], axes['y']
    columns, rows = x_axis['num'], y_axis['num']
    dx = (x_axis['stop'] - x_axis['start']) / max(columns - 1, 1)
    dy = (y_axis['stop'] - y_axis['start']) / max(rows - 1, 1)
    values = coverage['ranges']['MMI']['values']

    rectangles: dict[int, list[Any]] = {}
    for row in range(rows):
        y = y_axis['start'] + row * dy
        run_band, run_start = None, 0
        for column in range(columns + 1):
            value = values[row * columns + column] if column < columns else None
            band = mmi_band(value) if value is not None else None
            if band is not None and band < min_mmi:
                band = None
            if band == run_band:
                continue
            if run_band is not None:
                x0 = x_axis['start'] + run_start * dx
                x1 = x_axis['start'] + (column - 1) * dx
                rectangles.setdefault(run_band, []).append(
                    box(x0 - dx / 2, y - dy / 2, x1 + dx / 2, y + dy / 2),
                )
            run_band, run_start = band, column

    features = []
    for band in sorted(rectangles, reverse=True):
        numeral, description, colour = MMI_BANDS[band]
        features.append({
            'type': 'Feature',
            'geometry': mapping(unary_union(rectangles[band])),
            'properties': {
                'mmi': band,
                'intensity': numeral,
                'shaking': description,
                'color': colour,
            },
        })
    return {'type': 'FeatureCollection', 'features': features}


def load_geojson(footprint: Footprint, min_mmi: int = DEFAULT_MIN_MMI) -> Footprint:
    """
    Download a ShakeMap footprint's grid, returning a copy with `geojson`,
    `feature_count` and `size_km` filled in.
    """
    response = requests.get(footprint.data_url, timeout=FETCH_TIMEOUT)
    response.raise_for_status()
    geojson = grid_to_geojson(response.json(), min_mmi=min_mmi)
    return footprint.model_copy(update={
        'geojson': geojson,
        'feature_count': len(geojson['features']),
        'size_km': extent_km(geojson),
    })


def get_shakemap_footprints(
    event_id: str,
    *,
    download: bool = True,
    min_mmi: int = DEFAULT_MIN_MMI,
    ) -> list[Footprint]:
    """
    The ShakeMap footprint of one quake, by USGS or Montandon ID, as a
    list so it matches the other adapters; empty when USGS has no
    ShakeMap for it.
    """
    footprint = footprint_from_event(get_event(event_id))
    if footprint is None:
        return []
    if download:
        footprint = load_geojson(footprint, min_mmi=min_mmi)
    return [footprint]
