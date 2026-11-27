"""
Match USGS quakes to the GDACS and EM-DAT events that describe them, so
ShakeMap footprints attach to events with usable countries.

Linking ShakeMaps by `monty:corr_id` only reaches Montandon's own USGS
events, whose country codes are rarely usable: in November 2026, of the
M5.5+ quakes whose title names a country, 71 had that country code, 274
had another one and the rest were UNK. GDACS and EM-DAT quakes carry
reliable countries but share no ID with USGS, so quakes are matched by
time and distance instead:

- GDACS takes its quakes from the USGS feed, so a GDACS quake matches a
  USGS quake whose origin time is within 2 minutes and whose epicentre is
  within 100 km.
- EM-DAT records a disaster's day and affected area (a point or province
  polygons), so an EM-DAT quake matches the largest USGS quake within a
  day whose epicentre is within 150 km of that area. When that quake has
  no ShakeMap, the record gets none, rather than an aftershock's. Quakes
  tied for largest (a doublet) are all linked.
"""

# Imports

import logging
import math
import time
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from pystac_client import Client
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points

from monty_tool.api_utils import get_pystac_client


# Logger

logger = logging.getLogger(__name__)


# Constants

EMDAT_COLLECTION = 'emdat-events'
QUAKE_HAZARD_CODES = ('EQ', 'GH0101', 'nat-geo-ear-gro')
GDACS_SECONDS = 120
GDACS_KM = 100
EMDAT_KM = 150
EMDAT_DAYS = 1
EARTH_RADIUS_KM = 6371


# Schemas

@dataclass(frozen=True)
class Quake:
    """
    A located quake: a USGS event or a GDACS quake hazard.
    """
    item_id: str
    time: datetime
    latitude: float
    longitude: float
    magnitude: float


# Helpers

def _when(text: str) -> datetime:
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def is_quake(item: dict[str, Any]) -> bool:
    """
    Whether a raw Montandon Item is tagged as an earthquake.
    """
    codes = item.get('properties', {}).get('monty:hazard_codes') or []
    return any(code in QUAKE_HAZARD_CODES for code in codes)


def quake_from_item(item: dict[str, Any]) -> Quake | None:
    """
    The quake a raw Item describes, located at the centre of its bounding
    box (a USGS epicentre is a point), or None without a time or box.
    """
    properties = item.get('properties', {})
    bbox = item.get('bbox')
    when = properties.get('datetime')
    if not bbox or not when:
        return None
    west, south, east, north = bbox[:4]
    return Quake(
        item_id=item['id'],
        time=_when(when),
        latitude=(south + north) / 2,
        longitude=(west + east) / 2,
        magnitude=float(properties.get('eq:magnitude') or 0),
    )


def gdacs_event_id(hazard_id: str) -> str:
    """
    The GDACS event a GDACS hazard Item belongs to (same event and episode).
    """
    return hazard_id.replace('gdacs-hazard-', 'gdacs-event-', 1)


def km_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Great-circle distance in km.
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((phi2 - phi1) / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def km_to_geometry(latitude: float, longitude: float, geometry: BaseGeometry) -> float:
    """
    Distance in km from a point to the nearest part of a geometry (0 inside it).
    """
    nearest = nearest_points(Point(longitude, latitude), geometry)[1]
    return km_between(latitude, longitude, nearest.y, nearest.x)


# Matching

def gdacs_links(quakes: Iterable[Quake], gdacs_hazards: Iterable[dict[str, Any]]) -> dict[str, set[str]]:
    """
    GDACS event IDs for each USGS quake, keyed by USGS Item ID.
    """
    by_minute: dict[int, list[Quake]] = defaultdict(list)
    for item in gdacs_hazards:
        quake = quake_from_item(item) if is_quake(item) else None
        if quake is not None:
            by_minute[int(quake.time.timestamp() // 60)].append(quake)
    links: dict[str, set[str]] = defaultdict(set)
    for quake in quakes:
        minute = int(quake.time.timestamp() // 60)
        window = GDACS_SECONDS // 60 + 1
        for offset in range(-window, window + 1):
            for gdacs in by_minute.get(minute + offset, []):
                if (
                    abs((gdacs.time - quake.time).total_seconds()) <= GDACS_SECONDS
                    and km_between(quake.latitude, quake.longitude, gdacs.latitude, gdacs.longitude) <= GDACS_KM
                ):
                    links[quake.item_id].add(gdacs_event_id(gdacs.item_id))
    return dict(links)


def emdat_links(quakes: Iterable[Quake], emdat_events: Iterable[dict[str, Any]]) -> dict[str, set[str]]:
    """
    EM-DAT event IDs for each USGS quake, keyed by USGS Item ID. Each EM-DAT
    quake goes to the largest USGS quake near it in time and space, or to
    all of them when several share the largest magnitude (a doublet).
    """
    by_day: dict[date, list[Quake]] = defaultdict(list)
    for quake in quakes:
        by_day[quake.time.date()].append(quake)
    links: dict[str, set[str]] = defaultdict(set)
    for item in emdat_events:
        when = item.get('properties', {}).get('datetime')
        if not item.get('geometry') or not when or not is_quake(item):
            continue
        area = shape(item['geometry'])
        day = _when(when).date()
        nearby = [
            quake
            for offset in range(-EMDAT_DAYS, EMDAT_DAYS + 1)
            for quake in by_day.get(day + timedelta(days=offset), [])
            if km_to_geometry(quake.latitude, quake.longitude, area) <= EMDAT_KM
        ]
        if nearby:
            largest = max(quake.magnitude for quake in nearby)
            for quake in nearby:
                if quake.magnitude == largest:
                    links[quake.item_id].add(item['id'])
    return dict(links)


def quake_event_links(
    usgs_events: Iterable[dict[str, Any]],
    gdacs_hazards: Iterable[dict[str, Any]],
    emdat_events: Iterable[dict[str, Any]],
    min_magnitude: float = 0,
) -> dict[str, list[str]]:
    """
    GDACS and EM-DAT event IDs for each USGS quake of at least
    `min_magnitude`, keyed by USGS Item ID. Pass every such quake, not just
    those with ShakeMaps, so an aftershock cannot stand in for a larger one.
    """
    quakes = [
        quake for item in usgs_events
        if (quake := quake_from_item(item)) is not None and quake.magnitude >= min_magnitude
    ]
    links: dict[str, set[str]] = defaultdict(set)
    for found in (gdacs_links(quakes, gdacs_hazards), emdat_links(quakes, emdat_events)):
        for item_id, event_ids in found.items():
            links[item_id].update(event_ids)
    return {item_id: sorted(event_ids) for item_id, event_ids in links.items()}


# Montandon

def fetch_emdat_quakes(client: Client | None = None, attempts: int = 4) -> Iterator[dict[str, Any]]:
    """
    Every EM-DAT quake event from Montandon, with geometry (the shared raw
    EM-DAT file is stored without it).
    """
    client = client or get_pystac_client()
    query = {'op': 'a_overlaps', 'args': [{'property': 'monty:hazard_codes'}, list(QUAKE_HAZARD_CODES)]}
    for attempt in range(1, attempts + 1):
        try:
            search = client.search(
                collections=[EMDAT_COLLECTION], filter=query, filter_lang='cql2-json',
                max_items=None, limit=250,
            )
            items = [item.to_dict() for item in search.items()]
        except Exception as error:
            if attempt == attempts:
                raise
            logger.warning('EM-DAT quake search failed (attempt %d): %r', attempt, error)
            time.sleep(10 * attempt)
            continue
        logger.info('Fetched %d EM-DAT quakes from Montandon', len(items))
        yield from items
        return
