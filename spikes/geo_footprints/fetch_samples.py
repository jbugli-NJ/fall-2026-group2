"""
Spike: pull sample hazard footprints from each source, for geo_integration.

Saves one GeoJSON FeatureCollection per sample under data/geo_samples/
(gitignored), each feature tagged with `_source`, `_layer` and `_note` so
the map can label them. The samples are hand-picked from a first look at
staging in October 2026; swap the IDs below for others to inspect.

    uv run python spikes/geo_footprints/fetch_samples.py
    uv run python spikes/geo_footprints/make_map.py
"""

# Imports

import json
import os
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


# Constants

OUT_DIR = Path('data/geo_samples')

STAC_URL = 'https://montandon-eoapi-stage.ifrc.org/stac/collections'

CEMS_URL = 'https://rapidmapping-viewer.s3.eu-west-1.amazonaws.com'

USGS_EVENT_URL = 'https://earthquake.usgs.gov/fdsnws/event/1/query'

# (name, source, layer, note, URL) for GeoJSON files linked from CEMS items.
CEMS_LAYERS = [
    ('cems_flood_observed', 'CEMS', 'Observed flood extent',
     'EMSR935 AOI01 Moraira, Spain', 'EMSR935/AOI01/DEL_PRODUCT/'
     'EMSR935_AOI01_DEL_PRODUCT_observedEventA_v1.json'),
    ('cems_flood_modelled', 'CEMS', 'Modelled flood extent',
     'EMSR935 AOI01 Moraira, Spain', 'EMSR935/AOI01/DEL_PRODUCT/'
     'EMSR935_AOI01_DEL_PRODUCT_modelledEventA_v1.json'),
    ('cems_flood_depth', 'CEMS', 'Flood depth',
     'EMSR935 AOI01 Moraira, Spain', 'EMSR935/AOI01/DEL_PRODUCT/'
     'EMSR935_AOI01_DEL_PRODUCT_floodDepthA_v1.json'),
    ('cems_grading_buildings', 'CEMS', 'Damage grading: buildings',
     'EMSR935 AOI02 Gandia, Spain', 'EMSR935/AOI02/GRA_PRODUCT/'
     'EMSR935_AOI02_GRA_PRODUCT_builtUpP_v1.json'),
    ('cems_grading_transport', 'CEMS', 'Damage grading: transport',
     'EMSR935 AOI02 Gandia, Spain', 'EMSR935/AOI02/GRA_PRODUCT/'
     'EMSR935_AOI02_GRA_PRODUCT_transportationL_v1.json'),
    ('cems_grading_observed', 'CEMS', 'Observed flood extent (grading AOI)',
     'EMSR935 AOI02 Gandia, Spain', 'EMSR935/AOI02/GRA_PRODUCT/'
     'EMSR935_AOI02_GRA_PRODUCT_observedEventA_v1.json'),
]

# USGS events whose ShakeMap MMI contours are pulled straight from USGS.
USGS_EVENTS = ['us6000u43g']

# (name, layer, note, Montandon hazard item ID) for GDACS hazard items.
GDACS_HAZARDS = [
    ('gdacs_flood_france', 'Flood polygon', 'Flood in France',
     'gdacs-hazard-1104243-2'),
    ('gdacs_fire_russia', 'Wildfire polygon', 'Forest fires in Russia',
     'gdacs-hazard-1033087-1'),
    ('gdacs_fire_australia', 'Wildfire polygon', 'Forest fires in Australia',
     'gdacs-hazard-1033085-1'),
    ('gdacs_drought_sahel', 'Drought polygon',
     'Drought in CAF/CMR/NER/NGA/TCD; expected too coarse',
     'gdacs-hazard-1027567-2'),
]

# Latest IBTrACS hazard item for a storm; each item carries the full track.
IBTRACS_HAZARDS = [
    ('ibtracs_agueda', 'Tropical Cyclone AGUEDA',
     '2026293S05054-hazard-20261022T000000Z'),
]

# PDC hazard items whose `Maps` asset holds the hazard polygons.
PDC_HAZARDS = [
    ('pdc_storm_brazil', 'Thunderstorm, South and Southeast Brazil; '
     'expected too coarse',
     'pdc-hazard-937967d1-2350-4706-857e-d73bf8d5d7db-555498-1792498419071'),
]


# Fetching

def _headers() -> dict[str, str]:
    """
    Montandon auth headers, from MONTANDON_API_TOKEN in .env.
    """
    load_dotenv()
    token = os.getenv('MONTANDON_API_TOKEN')
    if token is None:
        raise ValueError('MONTANDON_API_TOKEN is unset!')
    return {'Authorization': f'Bearer {token}'}


def _get_json(url: str, **kwargs: Any) -> Any:
    response = requests.get(url, timeout=120, **kwargs)
    response.raise_for_status()
    return response.json()


def _stac_item(collection: str, item_id: str) -> dict[str, Any]:
    return _get_json(
        f'{STAC_URL}/{collection}/items/{item_id}', headers=_headers(),
    )


def _tag(
    features: list[dict[str, Any]],
    source: str,
    layer: str,
    note: str,
    ) -> dict[str, Any]:
    """
    Wrap features in a FeatureCollection, labelling each for the map.
    """
    for feature in features:
        feature.setdefault('properties', {})
        feature['properties'].update(
            {'_source': source, '_layer': layer, '_note': note},
        )
    return {'type': 'FeatureCollection', 'features': features}


def _item_feature(item: dict[str, Any]) -> dict[str, Any]:
    """
    A STAC item as a plain feature, keeping only its title and datetime.
    """
    properties = item['properties']
    return {
        'type': 'Feature',
        'geometry': item['geometry'],
        'properties': {
            'id': item['id'],
            'title': properties.get('title'),
            'datetime': properties.get('datetime'),
        },
    }


def fetch_cems() -> dict[str, dict[str, Any]]:
    samples = {}
    for name, source, layer, note, path in CEMS_LAYERS:
        data = _get_json(f'{CEMS_URL}/{path}')
        samples[name] = _tag(data['features'], source, layer, note)
    return samples


def fetch_usgs() -> dict[str, dict[str, Any]]:
    samples = {}
    for event_id in USGS_EVENTS:
        event = _get_json(
            USGS_EVENT_URL, params={'eventid': event_id, 'format': 'geojson'},
        )
        shakemap = event['properties']['products']['shakemap'][0]
        url = shakemap['contents']['download/cont_mmi.json']['url']
        contours = _get_json(url)['features']
        note = event['properties']['title']
        samples[f'usgs_shakemap_{event_id}'] = _tag(
            contours, 'USGS', 'ShakeMap MMI contours', note,
        )
        epicentre = {
            'type': 'Feature',
            'geometry': event['geometry'],
            'properties': {'title': note},
        }
        samples[f'usgs_epicentre_{event_id}'] = _tag(
            [epicentre], 'USGS', 'Epicentre', note,
        )
    return samples


def fetch_gdacs() -> dict[str, dict[str, Any]]:
    samples = {}
    for name, layer, note, item_id in GDACS_HAZARDS:
        item = _stac_item('gdacs-hazards', item_id)
        samples[name] = _tag([_item_feature(item)], 'GDACS', layer, note)
    return samples


def fetch_ibtracs() -> dict[str, dict[str, Any]]:
    samples = {}
    for name, note, item_id in IBTRACS_HAZARDS:
        item = _stac_item('ibtracs-hazards', item_id)
        samples[name] = _tag(
            [_item_feature(item)], 'IBTrACS', 'Cyclone track', note,
        )
    return samples


def fetch_pdc() -> dict[str, dict[str, Any]]:
    samples = {}
    for name, note, item_id in PDC_HAZARDS:
        item = _stac_item('pdc-hazards', item_id)
        maps = _get_json(item['assets']['Maps']['href'])
        # Older revisions of the same polygon come along marked inactive.
        active = [
            feature for feature in maps['features']
            if str(feature['properties'].get('isActive')) == 'True'
        ]
        samples[name] = _tag(active, 'PDC', 'Hazard polygon (active)', note)
    return samples


# Main

def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fetchers = [fetch_cems, fetch_usgs, fetch_gdacs, fetch_ibtracs, fetch_pdc]
    for fetch in fetchers:
        for name, collection in fetch().items():
            path = OUT_DIR / f'{name}.geojson'
            path.write_text(json.dumps(collection))
            count = len(collection['features'])
            print(f'{path}  ({count} features)')


if __name__ == '__main__':
    main()
