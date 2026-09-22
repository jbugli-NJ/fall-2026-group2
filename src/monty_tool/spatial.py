"""
Polygon and point GeoJSON geometry -> H3 cell coverage.

Turns Montandon Item geometry (Point, Polygon, MultiPolygon) into H3
cell IDs so hazard events can be joined against any other H3-indexed
raster or vector layer (population, land cover, etc).
"""

# Imports

from typing import Any

import h3


# Resources

# A point geometry has no area, so approximate its footprint with the
# 2-ring of cells around the cell it falls in.
POINT_K_RING = 2

# A single-ring GeoJSON polygon spanning >= this many degrees of
# longitude can't be valid: a legitimate ring never needs more than
# 180 degrees, so anything wider means the source crosses the
# antimeridian without being split into a MultiPolygon. A few
# `ifrcevent-events` records in the wild do this (e.g. a "Russia -
# Floods" event carrying Russia's national boundary as one
# unsplit ring), and handing that to `h3.geo_to_cells` makes it flood-fill
# most of the globe. Skip rather than hang.
MAX_LONGITUDE_SPAN_DEGREES = 180


def _longitude_span(geometry: dict[str, Any]) -> float:
    """
    Max - min longitude across every coordinate in a Polygon/MultiPolygon.
    """
    lons: list[float] = []

    def walk(coords: Any) -> None:
        if not coords:
            return
        if isinstance(coords[0], (int, float)):
            lons.append(coords[0])
        else:
            for sub in coords:
                walk(sub)

    walk(geometry.get('coordinates'))
    return max(lons) - min(lons) if lons else 0.0


# Geometry -> cells

def polygon_to_h3_cells(geometry: dict[str, Any] | None, resolution: int = 6) -> list[str]:
    """
    Convert a GeoJSON geometry dict to the H3 cell IDs covering it.

    `Point` geometries expand to a k-ring around their cell; `Polygon`
    and `MultiPolygon` geometries use `h3.geo_to_cells`. Any other or
    missing geometry type, or a ring wide enough to indicate an
    unsplit antimeridian crossing (see `MAX_LONGITUDE_SPAN_DEGREES`),
    returns an empty list.
    """
    if not geometry:
        return []
    geometry_type = geometry.get('type')
    if geometry_type == 'Point':
        lng, lat = geometry['coordinates']
        origin = h3.latlng_to_cell(lat, lng, resolution)
        return list(h3.grid_disk(origin, POINT_K_RING))
    if geometry_type in ('Polygon', 'MultiPolygon'):
        if _longitude_span(geometry) >= MAX_LONGITUDE_SPAN_DEGREES:
            return []
        return list(h3.geo_to_cells(geometry, resolution))
    return []


# Cells -> table rows

def cells_to_geodataframe(cells: list[str]) -> list[dict[str, Any]]:
    """
    Expand H3 cell IDs into rows of `{h3_cell, lat, lng, area_km2}`.
    """
    rows = []
    for cell in cells:
        lat, lng = h3.cell_to_latlng(cell)
        rows.append({
            'h3_cell': cell,
            'lat': lat,
            'lng': lng,
            'area_km2': h3.cell_area(cell, unit='km^2'),
        })
    return rows


# Events -> cells

def fill_events_with_h3(events: list[dict[str, Any]], resolution: int = 6) -> list[dict[str, Any]]:
    """
    Expand events with geometry into one row per (event, H3 cell).

    Each input event is a dict with `event_id`, `geometry` (a GeoJSON
    geometry dict), and whatever other fields should be carried through
    (e.g. `hazard_codes`, `country_codes`, `start_datetime`). Events
    whose geometry yields no cells are dropped.
    """
    rows = []
    for event in events:
        geometry = event.get('geometry')
        cells = polygon_to_h3_cells(geometry, resolution)
        geometry_type = (geometry or {}).get('type')
        for cell in cells:
            rows.append({
                'event_id': event.get('event_id'),
                'h3_cell': cell,
                'hazard_codes': event.get('hazard_codes'),
                'country_codes': event.get('country_codes'),
                'start_datetime': event.get('start_datetime'),
                'geometry_type': geometry_type,
            })
    return rows
