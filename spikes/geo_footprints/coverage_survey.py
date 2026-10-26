"""
Spike: survey how often each source has a usable hazard footprint.

For CEMS, USGS ShakeMap, GDACS, PDC and IBTrACS, counts events since
2024 by hazard type and records whether each comes with a detailed
footprint, how big it is, and what fields are available for matching it
to events from other sources (`monty:corr_id` only links within a
source). Items are pulled once into data/geo_survey/ (gitignored) as
slimmed-down records; pass --refresh to pull again.

    uv run python spikes/geo_footprints/coverage_survey.py
"""

# Imports

import argparse
import gzip
import json
import math
import os
import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

from monty_tool.api_utils import get_pystac_client
from monty_tool.features import hazard_code_lookup


# Constants

CACHE_DIR = Path('data/geo_survey')

REPORT_PATH = CACHE_DIR / 'report.md'

START = '2024-01-01T00:00:00Z'

USGS_CATALOG_URL = 'https://earthquake.usgs.gov/fdsnws/event/1/query'

# ShakeMap contours are only published for larger quakes.
MIN_MAGNITUDE = 5.5

# Footprints wider than this are treated as regional rather than
# event-shaped; roughly the size of a large metropolitan area.
COARSE_KM = 200

# PDC footprints sit behind one HTTP fetch each, so only sample them.
PDC_SAMPLES_PER_CLUSTER = 8

# CEMS asset keys that are not footprint layers.
CEMS_NON_LAYERS = re.compile(r'^(download|basemap.*|.*_style|thumbnail)$')


# Geometry summaries

def _points(coordinates: Any) -> Iterator[list[float]]:
    if coordinates and isinstance(coordinates[0], (int, float)):
        yield coordinates
    else:
        for part in coordinates:
            yield from _points(part)


def summarise_geometry(geometry: dict[str, Any] | None) -> dict[str, Any]:
    """
    Type, vertex count and approximate extent in km of a GeoJSON geometry.
    """
    if not geometry:
        return {'type': None, 'vertices': 0, 'size_km': None}
    parts = (
        geometry['geometries']
        if geometry['type'] == 'GeometryCollection' else [geometry]
    )
    points = [p for part in parts for p in _points(part['coordinates'])]
    if not points:
        return {'type': geometry['type'], 'vertices': 0, 'size_km': None}
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    mid_lat = math.radians((min(ys) + max(ys)) / 2)
    width = (max(xs) - min(xs)) * 111.32 * math.cos(mid_lat)
    height = (max(ys) - min(ys)) * 110.57
    return {
        'type': geometry['type'],
        'vertices': len(points),
        'size_km': round(max(width, height), 1),
    }


# Pulling

def _slim(item: dict[str, Any], geometry: bool) -> dict[str, Any]:
    """
    Keep what the survey needs from an Item: properties, asset keys and
    hrefs, and a geometry summary instead of the geometry itself.
    """
    record = {
        'id': item['id'],
        'properties': item['properties'],
        'assets': {
            key: asset.get('href')
            for key, asset in (item.get('assets') or {}).items()
        },
    }
    if geometry:
        record['geometry'] = summarise_geometry(item.get('geometry'))
    return record


def pull(
    name: str,
    collection: str,
    geometry: bool,
    refresh: bool,
    cql_filter: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
    """
    Slimmed Items from a collection since START, cached as JSON Lines.
    """
    path = CACHE_DIR / f'{name}.jsonl.gz'
    if path.exists() and not refresh:
        with gzip.open(path, 'rt', encoding='utf-8') as file:
            return [json.loads(line) for line in file]
    print(f'Pulling {collection} since {START}...', flush=True)
    exclude = ['links'] if geometry else ['links', 'geometry']
    search = get_pystac_client().search(
        collections=[collection],
        datetime=f'{START}/..',
        limit=1000,
        filter=cql_filter,
        filter_lang='cql2-json' if cql_filter else None,
        fields={'exclude': exclude},
    )
    records = []
    for item in search.items_as_dicts():
        records.append(_slim(item, geometry))
        if len(records) % 10_000 == 0:
            print(f'  {len(records)} items', flush=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix('.partial')
    with gzip.open(partial, 'wt', encoding='utf-8') as file:
        for record in records:
            file.write(json.dumps(record) + '\n')
    partial.replace(path)
    print(f'  {len(records)} items cached to {path}', flush=True)
    return records


# Helpers

_CLUSTERS: dict[str, str] = {}


def hazard_cluster(record: dict[str, Any]) -> str:
    """
    Canonical hazard cluster of the first recognised hazard code.
    """
    if not _CLUSTERS:
        lookup = hazard_code_lookup()
        _CLUSTERS.update(zip(lookup['hazard_code'], lookup['hazard_cluster']))
    for code in record['properties'].get('monty:hazard_codes') or []:
        if code in _CLUSTERS:
            return _CLUSTERS[code]
    return 'Unknown'


def latest_per_event(
    records: Iterable[dict[str, Any]],
    key: Callable[[dict[str, Any]], str],
    ) -> list[dict[str, Any]]:
    """
    Keep the most recent record for each source event, so repeated
    episodes or updates of one event count once.
    """
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        event = key(record)
        current = latest.get(event)
        if current is None or (
            record['properties']['datetime']
            > current['properties']['datetime']
        ):
            latest[event] = record
    return list(latest.values())


def _src_event(record: dict[str, Any]) -> str:
    properties = record['properties']
    return properties.get('monty:src_event_id') or properties['monty:corr_id']


def _pct(part: int, whole: int) -> str:
    return f'{100 * part / whole:.0f}%' if whole else '-'


def _median(values: list[float]) -> str:
    return f'{statistics.median(values):.0f}' if values else '-'


def _table(header: list[str], rows: list[list[Any]]) -> str:
    lines = [
        '| ' + ' | '.join(header) + ' |',
        '|' + '---|' * len(header),
    ]
    lines += ['| ' + ' | '.join(str(cell) for cell in row) + ' |' for row in rows]
    return '\n'.join(lines)


def matching_fields(records: list[dict[str, Any]]) -> list[Any]:
    """
    How well a source's records can be matched to other sources: share
    with a time range, share with a real clock time, geometry types, and
    which hazard-code vocabularies appear.
    """
    total = len(records)
    with_range = sum(
        1 for r in records if r['properties'].get('start_datetime')
    )
    with_time = sum(
        1 for r in records
        if not r['properties']['datetime'].endswith('T00:00:00Z')
    )
    geometry_types = Counter(
        (r.get('geometry') or {}).get('type') or 'not pulled'
        for r in records
    )
    vocabularies = Counter()
    for record in records:
        for code in record['properties'].get('monty:hazard_codes') or []:
            if re.fullmatch(r'[A-Z]{2}\d{4}', code):
                vocabularies['UNDRR'] += 1
            elif re.fullmatch(r'[A-Z]{2}', code):
                vocabularies['GLIDE'] += 1
            elif code.startswith('nat-'):
                vocabularies['EM-DAT'] += 1
    return [
        total,
        _pct(with_range, total),
        _pct(with_time, total),
        ', '.join(f'{k} {_pct(v, total)}' for k, v in geometry_types.most_common()),
        ', '.join(sorted(vocabularies)),
    ]


# Surveys

def survey_cems(refresh: bool) -> tuple[str, list[Any]]:
    responses = pull('cems-response', 'cems-response', False, refresh)
    events = pull('cems-events', 'cems-events', True, refresh)

    activations: dict[str, dict[str, Any]] = defaultdict(
        lambda: {'layers': set(), 'aois': set(), 'cluster': None,
                 'countries': set(), 'year': None},
    )
    for record in responses:
        match = re.search(r'EMSR\d+', record['id'])
        if not match:
            continue
        activation = activations[match.group()]
        aoi = re.search(r'aoi\d+', record['id'])
        if aoi:
            activation['aois'].add(aoi.group())
        activation['layers'].update(
            key for key, href in record['assets'].items()
            if not CEMS_NON_LAYERS.match(key)
            and (href or '').endswith('.json')
        )
        activation['cluster'] = activation['cluster'] or hazard_cluster(record)
        activation['countries'].update(
            record['properties'].get('monty:country_codes') or [],
        )
        activation['year'] = record['properties']['datetime'][:4]

    by_cluster: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for activation in activations.values():
        by_cluster[activation['cluster']].append(activation)
    rows = []
    for cluster, group in sorted(by_cluster.items(), key=lambda kv: -len(kv[1])):
        with_layers = [a for a in group if a['layers']]
        layers = Counter(layer for a in group for layer in a['layers'])
        rows.append([
            cluster, len(group), _pct(len(with_layers), len(group)),
            _median([len(a['aois']) for a in group]),
            ', '.join(f'{k} ({v})' for k, v in layers.most_common(6)),
        ])
    years = Counter(a['year'] for a in activations.values())
    countries = Counter(c for a in activations.values() for c in a['countries'])
    text = '\n\n'.join([
        '## CEMS rapid mapping',
        f'{len(activations)} activations with response items since 2024 '
        f'({len(responses)} response items, {len(events)} cems-events items). '
        'Per year: ' + ', '.join(f'{y}: {n}' for y, n in sorted(years.items()))
        + '.',
        _table(
            ['Hazard', 'Activations', 'With GeoJSON layers', 'Median AOIs',
             'Most common layers (activations)'],
            rows,
        ),
        'Top countries: ' + ', '.join(
            f'{c} ({n})' for c, n in countries.most_common(15)
        ) + '.',
    ])
    return text, ['CEMS events', *matching_fields(events)]


def survey_usgs(refresh: bool) -> tuple[str, list[Any]]:
    magnitude = {'op': '>=', 'args': [{'property': 'eq:magnitude'}, MIN_MAGNITUDE]}
    montandon = pull('usgs-events-m55', 'usgs-events', True, refresh, magnitude)
    montandon_ids = {
        record['properties'].get('monty:src_event_id')
        or record['id'].removeprefix('usgs-event-')
        for record in montandon
    }

    catalog = requests.get(
        USGS_CATALOG_URL,
        params={
            'format': 'geojson', 'starttime': START[:10],
            'minmagnitude': MIN_MAGNITUDE, 'limit': 20000,
        },
        timeout=300,
    ).json()['features']
    # USGS events carry every network's ID for the same quake; Montandon
    # may hold any one of them.
    in_montandon = [
        quake for quake in catalog
        if montandon_ids & set(filter(None, quake['properties']['ids'].split(',')))
    ]
    with_shakemap = [
        quake for quake in catalog
        if 'shakemap' in quake['properties']['types'].split(',')
    ]
    missing = [quake for quake in catalog if quake not in in_montandon]
    missing_big = sorted(
        (q for q in missing if q['properties']['mag'] >= 6.5),
        key=lambda q: -q['properties']['mag'],
    )
    rows = []
    for low, high in [(5.5, 6.0), (6.0, 6.5), (6.5, 7.0), (7.0, 10.0)]:
        band = [q for q in catalog if low <= q['properties']['mag'] < high]
        rows.append([
            f'M{low}-{high}' if high < 10 else f'M{low}+', len(band),
            _pct(sum(q in in_montandon for q in band), len(band)),
            _pct(sum(q in with_shakemap for q in band), len(band)),
        ])
    text = '\n\n'.join([
        '## USGS ShakeMap',
        f'USGS catalog has {len(catalog)} quakes of M{MIN_MAGNITUDE}+ since '
        f'2024; Montandon usgs-events has {len(montandon)} items at that '
        f'magnitude.',
        _table(
            ['Magnitude', 'USGS quakes', 'In Montandon', 'With ShakeMap'],
            rows,
        ),
        'Largest quakes missing from Montandon: ' + '; '.join(
            f"{q['id']} M{q['properties']['mag']} {q['properties']['place']}"
            for q in missing_big[:10]
        ) + '.' if missing_big else 'No M6.5+ quakes missing from Montandon.',
    ])
    return text, ['USGS events', *matching_fields(montandon)]


def survey_gdacs(refresh: bool) -> tuple[str, list[Any]]:
    hazards = pull('gdacs-hazards', 'gdacs-hazards', True, refresh)
    events = latest_per_event(hazards, _src_event)
    by_cluster: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in events:
        by_cluster[hazard_cluster(record)].append(record)
    rows = []
    for cluster, group in sorted(by_cluster.items(), key=lambda kv: -len(kv[1])):
        shaped = [
            r for r in group
            if r['geometry']['type'] in ('Polygon', 'MultiPolygon')
        ]
        sizes = [r['geometry']['size_km'] for r in shaped]
        fine = [s for s in sizes if s is not None and s <= COARSE_KM]
        rows.append([
            cluster, len(group), _pct(len(shaped), len(group)),
            _median(sizes), _pct(len(fine), len(group)),
        ])
    text = '\n\n'.join([
        '## GDACS hazard polygons',
        f'{len(hazards)} gdacs-hazards items since 2024, {len(events)} '
        'distinct events (latest episode kept).',
        _table(
            ['Hazard', 'Events', 'Polygon', 'Median size km',
             f'Polygon under {COARSE_KM} km'],
            rows,
        ),
    ])
    return text, ['GDACS hazards', *matching_fields(events)]


def survey_pdc(refresh: bool) -> tuple[str, list[Any]]:
    hazards = pull('pdc-hazards', 'pdc-hazards', True, refresh)
    events = latest_per_event(hazards, _src_event)
    by_cluster: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in events:
        by_cluster[hazard_cluster(record)].append(record)
    rows = []
    for cluster, group in sorted(by_cluster.items(), key=lambda kv: -len(kv[1])):
        with_maps = [r for r in group if r['assets'].get('Maps')]
        sizes, vertices, failures = [], [], 0
        for record in sorted(
            with_maps, key=lambda r: r['properties']['datetime'], reverse=True,
        )[:PDC_SAMPLES_PER_CLUSTER]:
            try:
                maps = requests.get(record['assets']['Maps'], timeout=60).json()
            except (requests.RequestException, ValueError):
                failures += 1
                continue
            active = [
                f for f in maps.get('features', [])
                if str(f['properties'].get('isActive')) == 'True'
            ] or maps.get('features', [])
            for feature in active[:1]:
                summary = summarise_geometry(feature['geometry'])
                if summary['size_km'] is not None:
                    sizes.append(summary['size_km'])
                    vertices.append(summary['vertices'])
        rows.append([
            cluster, len(group), _pct(len(with_maps), len(group)),
            len(sizes), _median(sizes), _median(vertices),
            failures or '',
        ])
    text = '\n\n'.join([
        '## PDC Maps polygons',
        f'{len(hazards)} pdc-hazards items since 2024, {len(events)} '
        f'distinct events. Footprints sampled from the newest '
        f'{PDC_SAMPLES_PER_CLUSTER} events per hazard with a Maps asset.',
        _table(
            ['Hazard', 'Events', 'With Maps asset', 'Sampled',
             'Median size km', 'Median vertices', 'Fetch failures'],
            rows,
        ),
    ])
    return text, ['PDC hazards', *matching_fields(events)]


def survey_ibtracs(refresh: bool) -> tuple[str, list[Any]]:
    events = pull('ibtracs-events', 'ibtracs-events', True, refresh)
    storms = latest_per_event(events, _src_event)
    types = Counter(r['geometry']['type'] for r in storms)
    lengths = [r['geometry']['size_km'] for r in storms if r['geometry']['size_km']]
    text = '\n\n'.join([
        '## IBTrACS tracks',
        f'{len(storms)} storms since 2024 in ibtracs-events. Geometry: '
        + ', '.join(f'{k} {v}' for k, v in types.most_common())
        + f'. Median track extent {_median(lengths)} km.',
    ])
    return text, ['IBTrACS events', *matching_fields(storms)]


# Main

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--refresh', action='store_true',
        help='Pull from the API again instead of using data/geo_survey/.',
    )
    args = parser.parse_args()
    load_dotenv()
    if os.getenv('MONTANDON_API_TOKEN') is None:
        raise SystemExit('MONTANDON_API_TOKEN is unset; add it to .env.')

    sections, matching = [], []
    for survey in [survey_cems, survey_usgs, survey_gdacs, survey_pdc,
                   survey_ibtracs]:
        text, row = survey(args.refresh)
        sections.append(text)
        matching.append(row)
        print('\n' + text + '\n', flush=True)

    sections.append('\n\n'.join([
        '## Fields for cross-source matching',
        _table(
            ['Source', 'Records', 'Has start/end', 'Has clock time',
             'Geometry', 'Hazard vocabularies'],
            matching,
        ),
    ]))
    print('\n' + sections[-1])
    report = f'# Footprint coverage survey (since {START[:10]})\n\n'
    REPORT_PATH.write_text(report + '\n\n'.join(sections) + '\n')
    print(f'\nWrote {REPORT_PATH}')


if __name__ == '__main__':
    main()
