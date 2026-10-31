"""
Spike: pull sample hazard footprints from each source, for geo_integration.

Every sample starts from a Montandon record and goes through the
`monty_tool.geo` adapters where a source has one, so the samples show
what the graph pipeline would see. Saves one `Footprint` per line to
data/geo_samples/footprints.jsonl (gitignored), which make_map.py
validates on load. The samples are hand-picked from staging in October
2026; swap the IDs below for others to inspect.

    uv run python demo/geo_footprints/fetch_samples.py
    uv run python demo/geo_footprints/make_map.py
"""

# Imports

from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from pystac_client import Client

from monty_tool.api_utils import get_pystac_client
from monty_tool.geo.cems import get_cems_footprints
from monty_tool.geo.gdacs import get_gdacs_footprints
from monty_tool.geo.schemas import Footprint, extent_km, grade_detail
from monty_tool.geo.usgs import get_shakemap_footprints


# Constants

OUT_PATH = Path('data/geo_samples/footprints.jsonl')

# CEMS activations; each returns every layer of every product.
CEMS_ACTIVATIONS = ['EMSR935']

# Montandon quakes whose ShakeMaps are built into intensity bands.
USGS_ITEMS = [
    'usgs-event-us6000rtdt',  # M7.6 2025 Aomori Prefecture, Japan
    'usgs-event-us6000tkt2',  # M7.7 68 km NNW of Ende, Indonesia
]

# GDACS events; the drought is expected to grade as regional.
GDACS_EVENTS = [
    '1104243',  # Flood in France
    '1033087',  # Forest fires in Russia
    '1033085',  # Forest fires in Australia
    '1027567',  # Drought in CAF/CMR/NER/NGA/TCD
]

# IBTrACS and PDC have no adapter (a track is not an area, and most PDC
# polygons are regional), so the spike builds their Footprints itself.
IBTRACS_ITEMS = [
    '2026293S05054-hazard-20261022T000000Z',  # Tropical Cyclone AGUEDA
]

PDC_ITEMS = [
    # Thunderstorm, South and Southeast Brazil
    'pdc-hazard-937967d1-2350-4706-857e-d73bf8d5d7db-555498-1792498419071',
]

FETCH_TIMEOUT = 120


# Fetching

def _item(client: Client, collection: str, item_id: str) -> dict[str, Any]:
    [item] = client.search(collections=[collection], ids=[item_id]).items_as_dicts()
    return item


def _self_url(item: dict[str, Any]) -> str:
    return next(link['href'] for link in item['links'] if link['rel'] == 'self')


def fetch_cems(client: Client) -> list[Footprint]:
    return [
        footprint
        for activation in CEMS_ACTIVATIONS
        for footprint in get_cems_footprints(activation, client=client)
    ]


def fetch_usgs(client: Client) -> list[Footprint]:
    return [
        footprint
        for item_id in USGS_ITEMS
        for footprint in get_shakemap_footprints(item_id, client=client)
    ]


def fetch_gdacs(client: Client) -> list[Footprint]:
    return [
        footprint
        for event_id in GDACS_EVENTS
        for footprint in get_gdacs_footprints(event_id, client=client)
    ]


def fetch_ibtracs(client: Client) -> list[Footprint]:
    footprints = []
    for item_id in IBTRACS_ITEMS:
        item = _item(client, 'ibtracs-hazards', item_id)
        properties = item['properties']
        geojson = {'type': 'FeatureCollection', 'features': [
            {'type': 'Feature', 'geometry': item['geometry'], 'properties': {}},
        ]}
        footprints.append(Footprint(
            source='IBTrACS',
            item_id=item_id,
            event_ref=properties.get('monty:src_event_id') or item_id,
            layer='track',
            label='Cyclone track',
            detail='track',
            datetime=properties['datetime'],
            hazard_codes=properties.get('monty:hazard_codes') or [],
            country_codes=properties.get('monty:country_codes') or [],
            data_url=_self_url(item),
            feature_count=1,
            size_km=extent_km(geojson),
            geojson=geojson,
        ))
    return footprints


def fetch_pdc(client: Client) -> list[Footprint]:
    footprints = []
    for item_id in PDC_ITEMS:
        item = _item(client, 'pdc-hazards', item_id)
        properties = item['properties']
        url = item['assets']['Maps']['href']
        response = requests.get(url, timeout=FETCH_TIMEOUT)
        response.raise_for_status()
        # Older revisions of the same polygon come along marked inactive.
        active = [
            feature for feature in response.json()['features']
            if str(feature['properties'].get('isActive')) == 'True'
        ]
        geojson = {'type': 'FeatureCollection', 'features': active}
        size_km = extent_km(geojson)
        footprints.append(Footprint(
            source='PDC',
            item_id=item_id,
            event_ref=properties.get('monty:src_event_id') or item_id,
            area_name=properties.get('title'),
            layer='hazard_polygon',
            label='Hazard polygon (active)',
            detail=grade_detail('outline', size_km),
            datetime=properties['datetime'],
            hazard_codes=properties.get('monty:hazard_codes') or [],
            country_codes=properties.get('monty:country_codes') or [],
            data_url=url,
            feature_count=len(active),
            size_km=size_km,
            geojson=geojson,
        ))
    return footprints


# Main

def main() -> None:
    load_dotenv()
    client = get_pystac_client()
    fetchers = [fetch_cems, fetch_usgs, fetch_gdacs, fetch_ibtracs, fetch_pdc]
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open('w') as file:
        for fetch in fetchers:
            for footprint in fetch(client):
                file.write(footprint.model_dump_json() + '\n')
                print(
                    f'{footprint.source} {footprint.item_id} {footprint.layer}: '
                    f'{footprint.feature_count} features, {footprint.detail}',
                )
    print(f'Wrote {OUT_PATH}')


if __name__ == '__main__':
    main()
