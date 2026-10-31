"""
Earthquake shaking footprints from USGS ShakeMap.

Quakes are looked up in Montandon, so a quake Montandon lacks gets no
footprint and shows up as a gap rather than being filled in from USGS
(in October 2026 Montandon held about 70% of the M5.5+ quakes in the
USGS catalog). The ShakeMap itself is not in Montandon: the geometry of
its `usgs-hazards` Items is just the rectangle around the map, with no
shaking intensity, and is sometimes empty. Every `usgs-events`
and `usgs-hazards` Item does link the USGS event it was built from as
its `source` asset, though, and that event lists the ShakeMap files.

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

from pystac_client import Client

from monty_tool.api_utils import get_pystac_client
from monty_tool.geo.schemas import Footprint, extent_km

SOURCE = 'USGS'

# Montandon ID prefixes to the collection holding them.
COLLECTIONS = {
    'usgs-event-': 'usgs-events',
    'usgs-hazard-': 'usgs-hazards',
}

# Medium resolution is about 2 km per cell, and about 200 KB for a M7;
# low is coarse enough to show steps, high is four times the size.
GRID_CONTENT = 'download/coverage_mmi_medium_res.covjson'

FETCH_TIMEOUT = 120

# Light shaking (IV) is the lowest band that matters for impact; I-III
# would mostly add a wide ring of weak or unfelt shaking.
DEFAULT_MIN_MMI = 4

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


def mmi_band(value: float) -> int:
    """
    The MMI band a grid value falls in, as USGS rounds it: 5.5 to 6.5 is VI.
    """
    return min(10, max(1, math.floor(value + 0.5)))


def get_item(item_id: str, client: Client | None = None) -> dict[str, Any] | None:
    """
    A Montandon `usgs-events` or `usgs-hazards` Item, or None when
    Montandon does not hold it.
    """
    collection = next(
        (name for prefix, name in COLLECTIONS.items() if item_id.startswith(prefix)),
        None,
    )
    if collection is None:
        raise ValueError(f'Not a Montandon USGS item ID: {item_id!r}')
    client = client or get_pystac_client()
    search = client.search(collections=[collection], ids=[item_id])
    return next(iter(search.items_as_dicts()), None)


def get_source_event(item: dict[str, Any]) -> dict[str, Any]:
    """
    The USGS event GeoJSON, including its products, that a Montandon USGS
    Item links as its `source` asset.
    """
    source = item.get('assets', {}).get('source')
    if source is None:
        raise ValueError(f"Montandon item {item['id']} has no source asset")
    response = requests.get(source['href'], timeout=FETCH_TIMEOUT)
    response.raise_for_status()
    return response.json()


def footprint_from_event(
    event: dict[str, Any],
    item: dict[str, Any],
    ) -> Footprint | None:
    """
    The ShakeMap footprint of a USGS event, without downloading its grid,
    or None when the event has no ShakeMap. `item` is the Montandon Item
    the event was reached from, which the footprint is filed under and
    takes its hazard and country codes from.
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
        item_id=item['id'],
        event_ref=event['id'],
        area_name=properties.get('place'),
        layer='shaking_intensity',
        label=f'Shaking intensity (ShakeMap v{version}, {status})',
        detail='modelled',
        datetime=datetime.fromtimestamp(properties['time'] / 1000, tz=UTC),
        hazard_codes=item['properties'].get('monty:hazard_codes') or [],
        country_codes=item['properties'].get('monty:country_codes') or [],
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


def footprints_from_item(
    item: dict[str, Any],
    *,
    download: bool = True,
    min_mmi: int = DEFAULT_MIN_MMI,
    ) -> list[Footprint]:
    """
    The ShakeMap footprint of a Montandon USGS Item's quake, as a list so
    it matches the other adapters; empty when USGS has no ShakeMap for it.
    """
    footprint = footprint_from_event(get_source_event(item), item)
    if footprint is None:
        return []
    if download:
        footprint = load_geojson(footprint, min_mmi=min_mmi)
    return [footprint]


def get_shakemap_footprints(
    item_id: str,
    *,
    download: bool = True,
    min_mmi: int = DEFAULT_MIN_MMI,
    client: Client | None = None,
    ) -> list[Footprint]:
    """
    The ShakeMap footprint of one quake by Montandon ID, e.g.
    'usgs-event-us6000u43g'; empty when Montandon does not hold the quake
    or USGS has no ShakeMap for it.
    """
    item = get_item(item_id, client=client)
    if item is None:
        return []
    return footprints_from_item(item, download=download, min_mmi=min_mmi)
