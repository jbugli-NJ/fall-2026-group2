"""
The source-independent footprint record, and geometry helpers shared by
the source adapters.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# How much to trust a footprint's outline, best first:
#   mapped   - drawn from imagery of the event itself (CEMS observed extent)
#   modelled - derived from a model (ShakeMap, CEMS flood depth)
#   outline  - a source's own hazard polygon (GDACS fire or flood)
#   track    - a storm path, not an area (IBTrACS)
#   regional - an outline wider than REGIONAL_KM; context, not an impact area
Detail = Literal['mapped', 'modelled', 'outline', 'track', 'regional']

# Footprints wider than this cover a region rather than an affected area;
# the 2026-10 coverage survey found GDACS drought and most PDC polygons
# well above it and CEMS, GDACS fire and GDACS flood mostly below.
REGIONAL_KM = 200.0


class Footprint(BaseModel):
    """
    One footprint layer for one hazard, from one source.

    `geojson` is a FeatureCollection, or None when the caller chose not
    to download it. `severity_property` names the per-feature property
    that grades it (flood depth band, damage grade, ...), when there is
    one.
    """
    source: str
    item_id: str
    event_ref: str = Field(description="The source's own event ID, e.g. EMSR935.")
    area_name: str | None = None
    layer: str
    label: str
    detail: Detail
    datetime: datetime
    start_datetime: datetime | None = None
    end_datetime: datetime | None = None
    hazard_codes: list[str] = Field(default_factory=list)
    country_codes: list[str] = Field(default_factory=list)
    data_url: str
    source_url: str | None = None
    severity_property: str | None = None
    feature_count: int | None = None
    size_km: float | None = None
    geojson: dict[str, Any] | None = None


# Geometry helpers

def _positions(coordinates: Any) -> Iterator[list[float]]:
    if coordinates and isinstance(coordinates[0], (int, float)):
        yield coordinates
    else:
        for part in coordinates:
            yield from _positions(part)


def _geometries(geojson: dict[str, Any]) -> Iterator[dict[str, Any]]:
    kind = geojson.get('type')
    if kind == 'FeatureCollection':
        for feature in geojson.get('features') or []:
            yield from _geometries(feature)
    elif kind == 'Feature':
        if geojson.get('geometry'):
            yield from _geometries(geojson['geometry'])
    elif kind == 'GeometryCollection':
        for geometry in geojson.get('geometries') or []:
            yield from _geometries(geometry)
    elif kind is not None:
        yield geojson


def extent_km(geojson: dict[str, Any]) -> float | None:
    """
    The longer side of a GeoJSON object's bounding box, in km.

    Uses an equirectangular approximation at the box's mid-latitude,
    which is plenty to tell a town-sized footprint from a regional one.
    Ignores antimeridian crossings, so a footprint straddling 180 degrees
    reads as very wide.
    """
    positions = [
        position
        for geometry in _geometries(geojson)
        for position in _positions(geometry.get('coordinates') or [])
    ]
    if not positions:
        return None
    longitudes = [position[0] for position in positions]
    latitudes = [position[1] for position in positions]
    mid_latitude = math.radians((min(latitudes) + max(latitudes)) / 2)
    width = (max(longitudes) - min(longitudes)) * 111.32 * math.cos(mid_latitude)
    height = (max(latitudes) - min(latitudes)) * 110.57
    return round(max(width, height), 1)


def grade_detail(detail: Detail, size_km: float | None) -> Detail:
    """
    Downgrade an 'outline' footprint to 'regional' when it is too wide to
    be an impact area. Mapped and modelled footprints keep their grade at
    any size: a large quake's ShakeMap is wide but graded throughout,
    whereas a wide outline is one undifferentiated blob.
    """
    if detail == 'outline' and size_km is not None and size_km > REGIONAL_KM:
        return 'regional'
    return detail
