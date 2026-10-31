"""
Footprints from Copernicus EMS rapid mapping (CEMS) activations.

Each activation (EMSR###) has one or more areas of interest (AOIs), and
each AOI gets products over time: delineation (where the hazard is),
grading (what was damaged), and monitoring updates of either. Montandon
holds one `cems-response` Item per product, whose assets link the
product's GeoJSON layers on the CEMS viewer bucket. This module turns
those layers into `Footprint` records.

Coverage starts in 2024, and the 2024-25 Items are still being backfilled
with their layer assets: in October 2026 nearly every 2026 activation had
layers but only about a third of earlier ones did.
"""

import logging
import re
from typing import Any

import requests
from pystac_client import Client

from monty_tool.api_utils import DEFAULT_PAGE_SIZE, get_pystac_client
from monty_tool.geo.schemas import Detail, Footprint, extent_km, grade_detail

logger = logging.getLogger(__name__)

COLLECTION = 'cems-response'

SOURCE = 'CEMS'

# Layer files are a few MB at most, but the bucket can be slow.
FETCH_TIMEOUT = 120

# CEMS layer names to (layer, label, detail, severity property). Layers
# not listed are skipped: notAnalysedA (areas the analysts could not
# see), hydrography and physiography (reference rivers and terrain), and
# ancillaryCrisisInfo (sparse annotation points).
LAYERS: dict[str, tuple[str, str, Detail, str | None]] = {
    'observedEventA': ('observed_extent', 'Observed hazard extent', 'mapped', 'notation'),
    'observedEventL': ('observed_extent', 'Observed hazard extent', 'mapped', 'notation'),
    'observedEventP': ('observed_extent', 'Observed hazard extent', 'mapped', 'notation'),
    'maximumFloodExtentA': ('maximum_extent', 'Maximum flood extent', 'mapped', 'notation'),
    'maximumWaterExtentA': ('maximum_extent', 'Maximum flood extent', 'mapped', 'notation'),
    'previousObservedEventA': ('previous_extent', 'Previously observed extent', 'mapped', 'notation'),
    'modelledEventA': ('modelled_extent', 'Modelled hazard extent', 'modelled', 'notation'),
    'floodDepthA': ('water_depth', 'Estimated water depth (m)', 'modelled', 'value'),
    'waterDepthA': ('water_depth', 'Estimated water depth (m)', 'modelled', 'value'),
    'builtUpA': ('buildings', 'Buildings', 'mapped', 'damage_gra'),
    'builtUpP': ('buildings', 'Buildings', 'mapped', 'damage_gra'),
    'transportationA': ('transport', 'Roads and transport', 'mapped', 'damage_gra'),
    'transportationL': ('transport', 'Roads and transport', 'mapped', 'damage_gra'),
    'transportationP': ('transport', 'Roads and transport', 'mapped', 'damage_gra'),
    'facilitiesA': ('facilities', 'Facilities', 'mapped', 'damage_gra'),
    'facilitiesL': ('facilities', 'Facilities', 'mapped', 'damage_gra'),
    'facilitiesP': ('facilities', 'Facilities', 'mapped', 'damage_gra'),
    'naturalLandUseA': ('land_use', 'Affected land use', 'mapped', None),
    'groundMovementA': ('ground_movement', 'Ground movement', 'mapped', None),
}

# Monitoring products prefix their layer keys, e.g.
# EMSR933_AOI03_DEL_MONIT02_maximumFloodExtentA.
_MONITORING_PREFIX = re.compile(r'^EMSR\d+_AOI\d+_[A-Z]+_MONIT\d+_')

_ACTIVATION = re.compile(r'EMSR\d+')

# Item titles end with the AOI name, e.g.
# 'CEMS delineation product — Moraira (EMSR935)'.
_AREA_NAME = re.compile(r'—\s*(.+?)\s*\(EMSR\d+\)\s*$')


def layer_name(asset_key: str) -> str:
    """
    The CEMS layer name behind an asset key, without any monitoring prefix.
    """
    return _MONITORING_PREFIX.sub('', asset_key)


def footprints_from_item(item: dict[str, Any]) -> list[Footprint]:
    """
    One Footprint per GeoJSON layer on a `cems-response` Item, without
    downloading the layers. Withdrawn products, which CEMS has retracted
    or superseded, yield nothing.
    """
    properties = item['properties']
    detail = properties.get('monty:response_detail') or {}
    if detail.get('status') == 'withdrawn':
        return []

    activation = detail.get('source_id')
    if activation is None:
        match = _ACTIVATION.search(item['id'])
        if match is None:
            return []
        activation = match.group()

    area = _AREA_NAME.search(properties.get('title') or '')
    source_url = next(
        (
            link['href'] for link in item.get('links') or []
            if link.get('rel') == 'derived_from'
        ),
        None,
    )

    footprints = []
    for key, asset in (item.get('assets') or {}).items():
        mapping = LAYERS.get(layer_name(key))
        href = asset.get('href') or ''
        if mapping is None or not href.endswith('.json'):
            continue
        layer, label, layer_detail, severity = mapping
        footprints.append(Footprint(
            source=SOURCE,
            item_id=item['id'],
            event_ref=activation,
            area_name=area.group(1) if area else None,
            layer=layer,
            label=label,
            detail=layer_detail,
            datetime=properties['datetime'],
            hazard_codes=properties.get('monty:hazard_codes') or [],
            country_codes=properties.get('monty:country_codes') or [],
            data_url=href,
            source_url=source_url,
            severity_property=severity,
        ))
    return footprints


def load_geojson(footprint: Footprint) -> Footprint:
    """
    Download a footprint's layer, returning a copy with `geojson`,
    `feature_count` and `size_km` filled in and `detail` regraded by size.
    """
    response = requests.get(footprint.data_url, timeout=FETCH_TIMEOUT)
    response.raise_for_status()
    geojson = response.json()
    size_km = extent_km(geojson)
    return footprint.model_copy(update={
        'geojson': geojson,
        'feature_count': len(geojson.get('features') or []),
        'size_km': size_km,
        'detail': grade_detail(footprint.detail, size_km),
    })


def get_activation_items(
    activation: str,
    client: Client | None = None,
    ) -> list[dict[str, Any]]:
    """
    Every `cems-response` Item for one activation, e.g. 'EMSR935'.
    """
    if not _ACTIVATION.fullmatch(activation):
        raise ValueError(f'Not a CEMS activation ID: {activation!r}')
    client = client or get_pystac_client()
    search = client.search(
        collections=[COLLECTION],
        filter={
            'op': 'like',
            'args': [{'property': 'id'}, f'{COLLECTION}-{activation}-%'],
        },
        filter_lang='cql2-json',
        limit=DEFAULT_PAGE_SIZE,
    )
    return list(search.items_as_dicts())


def get_cems_footprints(
    activation: str,
    *,
    download: bool = True,
    client: Client | None = None,
    ) -> list[Footprint]:
    """
    Every footprint layer for one CEMS activation, newest product first.

    With `download`, each layer's GeoJSON is fetched; a layer that fails
    to download is logged and returned without it rather than dropping
    the rest.
    """
    footprints = [
        footprint
        for item in get_activation_items(activation, client=client)
        for footprint in footprints_from_item(item)
    ]
    footprints.sort(key=lambda footprint: footprint.datetime, reverse=True)
    if not download:
        return footprints

    loaded = []
    for footprint in footprints:
        try:
            loaded.append(load_geojson(footprint))
        except (requests.RequestException, ValueError) as error:
            logger.warning('Could not load %s: %s', footprint.data_url, error)
            loaded.append(footprint)
    return loaded

