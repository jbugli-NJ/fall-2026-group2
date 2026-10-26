"""
Footprints from GDACS hazard polygons.

GDACS publishes a polygon for floods, wildfires (burnt area from GWIS)
and droughts, and only a point for earthquakes, cyclones and volcanoes.
Montandon carries the polygon as the geometry of each `gdacs-hazards`
Item, one Item per episode, so no extra download is needed. Drought
polygons are typically regional (median about 1,400 km across in the
October 2026 survey) and are graded as such by size.
"""

from functools import cache
from typing import Any

from pystac_client import Client

from monty_tool.api_utils import DEFAULT_PAGE_SIZE, get_pystac_client
from monty_tool.features import hazard_code_lookup
from monty_tool.geo.schemas import Footprint, extent_km, grade_detail

COLLECTION = 'gdacs-hazards'

SOURCE = 'GDACS'

POLYGON_TYPES = ('Polygon', 'MultiPolygon')

# UNDRR code prefixes to (layer, label). Anything else with a polygon is
# labelled from the hazard crosswalk.
LAYERS = {
    'EN0205': ('burnt_area', 'Burnt area'),
    'MH06': ('flood_area', 'Flood area'),
    'MH0401': ('drought_area', 'Drought area'),
}


@cache
def _hazard_labels() -> dict[str, str]:
    lookup = hazard_code_lookup()
    return dict(zip(lookup['hazard_code'], lookup['hazard_label']))


def gdacs_event_id(event_id: str) -> str:
    """
    The GDACS event ID behind a GDACS or Montandon ID, e.g.
    'gdacs-hazard-1033087-1' -> '1033087'.
    """
    for prefix in ('gdacs-hazard-', 'gdacs-event-'):
        if event_id.startswith(prefix):
            return event_id.removeprefix(prefix).split('-')[0]
    return event_id


def _layer(hazard_codes: list[str]) -> tuple[str, str]:
    for code in hazard_codes:
        for prefix, layer in LAYERS.items():
            if code.startswith(prefix):
                return layer
    labels = _hazard_labels()
    label = next((labels[code] for code in hazard_codes if code in labels), 'Hazard')
    return 'hazard_area', f'{label} area'


def footprint_from_item(item: dict[str, Any]) -> Footprint | None:
    """
    The footprint of one GDACS hazard episode, or None when GDACS only
    gives a point for it.
    """
    geometry = item.get('geometry')
    if not geometry or geometry.get('type') not in POLYGON_TYPES:
        return None

    properties = item['properties']
    hazard_codes = properties.get('monty:hazard_codes') or []
    layer, label = _layer(hazard_codes)
    severity = properties.get('severitydata') or {}
    alert = (properties.get('monty:hazard_detail') or {}).get('severity_label')
    episode = properties.get('monty:episode_number')
    geojson = {
        'type': 'FeatureCollection',
        'features': [{
            'type': 'Feature',
            'geometry': geometry,
            'properties': {
                'alert': alert,
                'severity': severity.get('severitytext'),
                'episode': episode,
                'start': properties.get('start_datetime'),
                'end': properties.get('end_datetime'),
            },
        }],
    }
    size_km = extent_km(geojson)
    report = (item.get('assets') or {}).get('report') or {}
    return Footprint(
        source=SOURCE,
        item_id=item['id'],
        event_ref=properties.get('monty:src_event_id') or gdacs_event_id(item['id']),
        area_name=properties.get('title'),
        layer=layer,
        label=label if episode is None else f'{label} (episode {episode})',
        detail=grade_detail('outline', size_km),
        datetime=properties['datetime'],
        start_datetime=properties.get('start_datetime'),
        end_datetime=properties.get('end_datetime'),
        hazard_codes=hazard_codes,
        country_codes=properties.get('monty:country_codes') or [],
        data_url=next(
            (link['href'] for link in item.get('links') or [] if link.get('rel') == 'via'),
            report.get('href', ''),
        ),
        source_url=report.get('href'),
        severity_property='alert',
        feature_count=1,
        size_km=size_km,
        geojson=geojson,
    )


def get_event_items(
    event_id: str,
    client: Client | None = None,
    ) -> list[dict[str, Any]]:
    """
    Every `gdacs-hazards` Item (one per episode) for a GDACS event.

    GDACS event IDs are only unique within a hazard type, so in rare
    cases this can return episodes of an unrelated event of another type.
    """
    event_id = gdacs_event_id(event_id)
    if not event_id.isdigit():
        raise ValueError(f'Not a GDACS event ID: {event_id!r}')
    client = client or get_pystac_client()
    search = client.search(
        collections=[COLLECTION],
        filter={
            'op': 'like',
            'args': [{'property': 'id'}, f'gdacs-hazard-{event_id}-%'],
        },
        filter_lang='cql2-json',
        limit=DEFAULT_PAGE_SIZE,
    )
    return list(search.items_as_dicts())


def get_gdacs_footprints(
    event_id: str,
    *,
    client: Client | None = None,
    ) -> list[Footprint]:
    """
    One footprint per GDACS episode with a polygon, newest first; empty
    for point-only hazards such as earthquakes and cyclones.
    """
    footprints = [
        footprint
        for item in get_event_items(event_id, client=client)
        if (footprint := footprint_from_item(item)) is not None
    ]
    footprints.sort(key=lambda footprint: footprint.datetime, reverse=True)
    return footprints
