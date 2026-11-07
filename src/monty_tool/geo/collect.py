"""
Footprints for raw Montandon Items, dispatched to the adapter for each
Item's collection, with a short summary of what the footprint grades.
"""

# Imports

import logging
from typing import Any, Literal

import requests

from monty_tool.geo import cems, gdacs, usgs
from monty_tool.geo.schemas import Footprint


# Logger

logger = logging.getLogger(__name__)


# Resources

# Where a footprint's outline comes from. CEMS layers and GDACS polygons
# are files or geometry that Montandon itself holds; a ShakeMap's
# intensity bands come from USGS, since Montandon only has the epicentre
# and a bounding box.
type Origin = Literal['montandon', 'external']

ORIGINS: dict[str, Origin] = {
    cems.SOURCE: 'montandon',
    gdacs.SOURCE: 'montandon',
    usgs.SOURCE: 'external',
}

FOOTPRINT_COLLECTIONS = (cems.COLLECTION, gdacs.COLLECTION, 'usgs-events')

# ShakeMaps are worth fetching for damaging quakes only; smaller ones
# rarely have impact records to compare against, and Montandon holds
# tens of thousands of them.
MIN_SHAKEMAP_MAGNITUDE = 5.5


# Helpers

def severity_levels(footprint: Footprint) -> list[str]:
    """
    The distinct values of a footprint's grading property across its
    features, e.g. MMI bands or CEMS damage grades, as sorted strings.
    """
    if footprint.geojson is None or footprint.severity_property is None:
        return []
    values = {
        str(value)
        for feature in footprint.geojson.get('features') or []
        if (value := (feature.get('properties') or {}).get(footprint.severity_property)) is not None
    }
    return sorted(values)


def _download_cems(footprint: Footprint) -> Footprint:
    """
    Load a CEMS layer, keeping the undownloaded record when it fails so
    one broken layer does not drop the activation.
    """
    try:
        return cems.load_geojson(footprint)
    except (requests.RequestException, ValueError) as error:
        logger.warning('Could not load %s: %s', footprint.data_url, error)
        return footprint


def footprints_from_raw_item(item: dict[str, Any]) -> list[Footprint]:
    """
    Every footprint a raw Montandon Item yields, with GeoJSON loaded;
    empty for collections without footprints and for small quakes.
    """
    collection = item.get('collection')
    if collection == cems.COLLECTION:
        return [_download_cems(footprint) for footprint in cems.footprints_from_item(item)]
    if collection == gdacs.COLLECTION:
        footprint = gdacs.footprint_from_item(item)
        return [] if footprint is None else [footprint]
    if collection == 'usgs-events':
        magnitude = item['properties'].get('eq:magnitude')
        if magnitude is None or magnitude < MIN_SHAKEMAP_MAGNITUDE:
            return []
        return usgs.footprints_from_item(item)
    return []
