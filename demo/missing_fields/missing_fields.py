"""
Count, per Montandon source and year, how many records lack date,
geometry, code or impact fields.

Answers the instructor's 10-27 question (Reviews #7 and #8): which
records are incomplete, and where. Every number comes from Montandon
itself; nothing is filled in from the upstream sources, since the gaps
are the finding (the data gap in the gap analysis).

Each collection is read one year at a time (USGS one month at a time),
asking the API for only the fields that are counted, so nothing large is
downloaded or stored. Finished windows are checkpointed to
data/missing_fields/checkpoint.jsonl (gitignored), so a timeout resumes
where it stopped; pass --refresh to start over. The API is the live
staging server, so the report is stamped with the date it was run.

    uv run python demo/missing_fields/missing_fields.py --collections cems-events
    uv run python demo/missing_fields/missing_fields.py
"""

# Imports

import argparse
import csv
import json
import logging
import math
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pystac_client import Client

from monty_tool.api_utils import get_pystac_client
from monty_tool.features import hazard_code_lookup


# Logger

logger = logging.getLogger(__name__)


# Constants

# The checkpoint is a large working file, kept out of git; the summary
# CSV and report are committed next to this script.
OUT_DIR = Path('data/missing_fields')
CHECKPOINT_PATH = OUT_DIR / 'checkpoint.jsonl'
RESULTS_DIR = Path(__file__).parent / 'results'
CSV_PATH = RESULTS_DIR / 'missing_fields.csv'
REPORT_PATH = RESULTS_DIR / 'report.md'

# Response products and the test collection are not disaster records.
# PDC is skipped too: its impacts run to millions of records a month from
# 2025 and are not needed for the table.
SKIPPED_COLLECTIONS = {'rsh-test2-events', 'pdc-events', 'pdc-hazards', 'pdc-impacts'}
RECORD_SUFFIXES = ('-events', '-hazards', '-impacts')

# USGS holds every recorded quake (over a million). M4.5+ keeps 99% of
# EM-DAT earthquake disasters since 2000 (674 checked on 2026-11-08), and
# is fetched one month at a time because the magnitude filter is slow.
USGS_MIN_MAGNITUDE = 4.5

# Earliest year read one year at a time; anything before goes in one
# window. IBTrACS starts in 1842, so this keeps real old storms.
FIRST_YEAR = 1800

# Records dated before FIRST_YEAR or after the run date are placeholders
# (e.g. DesInventar's 0001-01-01 and 2202-01-01).
PLACEHOLDER_BUCKET = 'placeholder'

# Wider than this, a bounding box is regional or national rather than
# event-shaped; same cut-off as demo/geo_footprints/coverage_survey.py.
COARSE_KM = 200

PAGE_SIZE = 500
RETRIES = 3

FIELDS = {
    'include': [
        'id',
        'bbox',
        'properties.datetime',
        'properties.start_datetime',
        'properties.end_datetime',
        'properties.title',
        'properties.description',
        'properties.monty:corr_id',
        'properties.monty:country_codes',
        'properties.monty:hazard_codes',
        'properties.monty:hazard_detail',
        'properties.monty:impact_detail',
        'properties.keywords',
        'properties.monty:src_event_id',
        'properties.processing:version',
    ],
    'exclude': ['geometry', 'assets', 'links'],
}

# What each count means, in report order. Every check is True when the
# record has the problem.
CHECKS = {
    'no_datetime': 'no `datetime`',
    'no_start_datetime': 'no `start_datetime`',
    'no_end_datetime': 'no `end_datetime`',
    'end_before_start': '`end_datetime` before `start_datetime`',
    'placeholder_date': f'dated before {FIRST_YEAR} or after the run date',
    'no_geometry': 'no geometry (no `bbox`)',
    'point_only': 'a single point',
    'coarse_geometry': f'an extent over {COARSE_KM} km',
    'no_country': 'no country codes',
    'null_country': 'a null country code',
    'unknown_country_only': 'only `UNK` as country',
    'no_hazard_codes': 'no hazard codes',
    'unrecognised_hazard_code': 'a hazard code not in the crosswalk',
    'no_corr_id': 'no `monty:corr_id`',
    'blank_description': 'a blank, `NA` or `None` description',
    'no_keywords': 'no `keywords` (older ETL)',
    'no_src_event_id': 'no `monty:src_event_id`, the source\'s own event ID (older ETL)',
    'no_processing_version': 'no `processing:version` (older, unversioned ETL)',
    'no_hazard_detail': 'no `monty:hazard_detail` (hazards)',
    'no_severity_value': 'no severity value (hazards)',
    'no_impact_detail': 'no `monty:impact_detail` (impacts)',
    'no_impact_type': 'no impact type (impacts)',
    'no_impact_value': 'no impact value (impacts)',
    'no_impact_unit': 'no impact unit (impacts)',
}


# Record checks

def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None


def record_date(item: dict[str, Any]) -> datetime | None:
    """
    The date a record is filed under: `datetime`, else `start_datetime`.
    """
    properties = item.get('properties') or {}
    return _parse(properties.get('datetime')) or _parse(properties.get('start_datetime'))


def year_bucket(item: dict[str, Any], today: date) -> str | None:
    """
    The year a record is counted in, `placeholder` for impossible dates,
    or None when it has no date at all.
    """
    when = record_date(item)
    if when is None:
        return None
    if when.year < FIRST_YEAR or when.date() > today:
        return PLACEHOLDER_BUCKET
    return str(when.year)


def bbox_km(bbox: list[float]) -> float:
    """
    Approximate width or height of a bounding box in km, whichever is larger.
    """
    west, south, east, north = bbox[0], bbox[1], bbox[-2], bbox[-1]
    mid_lat = math.radians((south + north) / 2)
    width = abs(east - west) * 111.32 * math.cos(mid_lat)
    height = abs(north - south) * 110.57
    return max(width, height)


def _blank(text: Any) -> bool:
    if text is None:
        return True
    stripped = str(text).strip()
    return stripped in {'', 'NA', 'None'} or stripped.endswith(': None')


def check_record(
    item: dict[str, Any],
    record_type: str,
    known_hazard_codes: set[str],
    today: date,
    ) -> dict[str, bool]:
    """
    Which of `CHECKS` a record fails. Hazard and impact checks only apply
    to their own record type and are False otherwise.
    """
    properties = item.get('properties') or {}
    start = _parse(properties.get('start_datetime'))
    end = _parse(properties.get('end_datetime'))
    bbox = item.get('bbox')
    countries = properties.get('monty:country_codes') or []
    hazards = properties.get('monty:hazard_codes') or []
    hazard_detail = properties.get('monty:hazard_detail') or {}
    impact_detail = properties.get('monty:impact_detail') or {}
    size = bbox_km(bbox) if bbox else None
    is_hazard = record_type == 'hazards'
    is_impact = record_type == 'impacts'
    return {
        'no_datetime': _parse(properties.get('datetime')) is None,
        'no_start_datetime': start is None,
        'no_end_datetime': end is None,
        'end_before_start': start is not None and end is not None and end < start,
        'placeholder_date': year_bucket(item, today) == PLACEHOLDER_BUCKET,
        'no_geometry': not bbox,
        'point_only': size == 0,
        'coarse_geometry': size is not None and size > COARSE_KM,
        'no_country': not countries,
        'null_country': any(code is None for code in countries),
        'unknown_country_only': bool(countries) and all(code == 'UNK' for code in countries),
        'no_hazard_codes': not hazards,
        'unrecognised_hazard_code': any(code not in known_hazard_codes for code in hazards),
        'no_corr_id': not properties.get('monty:corr_id'),
        'blank_description': _blank(properties.get('description')),
        'no_keywords': not properties.get('keywords'),
        'no_src_event_id': _blank(properties.get('monty:src_event_id')),
        'no_processing_version': _blank(properties.get('processing:version')),
        'no_hazard_detail': is_hazard and not hazard_detail,
        'no_severity_value': is_hazard and hazard_detail.get('severity_value') is None,
        'no_impact_detail': is_impact and not impact_detail,
        'no_impact_type': is_impact and not impact_detail.get('type'),
        'no_impact_value': is_impact and impact_detail.get('value') is None,
        'no_impact_unit': is_impact and not impact_detail.get('unit'),
    }


# Windows

@dataclass(frozen=True)
class Window:
    """
    A slice of a collection's timeline, read in one search.
    """
    label: str
    interval: str
    buckets: frozenset[str]


def _iso(day: date) -> str:
    return f'{day.isoformat()}T00:00:00Z'


def windows(first_year: int, today: date, monthly_from: int | None = None) -> list[Window]:
    """
    One window per year from `first_year` to today (per month from
    `monthly_from` on), plus one before FIRST_YEAR and one after today for
    placeholder dates.
    """
    placeholder = frozenset({PLACEHOLDER_BUCKET})
    result = [Window('before', f'../{_iso(date(FIRST_YEAR, 1, 1))}', placeholder)]
    for year in range(max(first_year, FIRST_YEAR), today.year + 1):
        if monthly_from is not None and year >= monthly_from:
            for month in range(1, 13):
                start = date(year, month, 1)
                if start > today:
                    break
                end = date(year + month // 12, month % 12 + 1, 1)
                result.append(Window(
                    f'{year}-{month:02d}', f'{_iso(start)}/{_iso(end)}', frozenset({str(year)}),
                ))
        else:
            result.append(Window(
                str(year), f'{_iso(date(year, 1, 1))}/{_iso(date(year + 1, 1, 1))}',
                frozenset({str(year)}),
            ))
    result.append(Window('after', f'{_iso(today)}/..', placeholder))
    return result


# Counting

def _search_filter(collection: str) -> dict[str, Any] | None:
    if collection.startswith('usgs-'):
        return {'op': '>=', 'args': [{'property': 'eq:magnitude'}, USGS_MIN_MAGNITUDE]}
    return None


def _items(client: Client, collection: str, interval: str) -> Iterator[dict[str, Any]]:
    search_filter = _search_filter(collection)
    yield from client.search(
        collections=[collection],
        datetime=interval,
        limit=PAGE_SIZE,
        fields=FIELDS,
        filter=search_filter,
        filter_lang='cql2-json' if search_filter else None,
    ).items_as_dicts()


def count_window(
    client: Client,
    collection: str,
    window: Window,
    known_hazard_codes: set[str],
    today: date,
    ) -> dict[str, Counter[str]]:
    """
    Check counts per year bucket for the records filed under this window.
    Records spanning several windows are only counted in their own one.
    """
    record_type = collection.rsplit('-', 1)[1]
    counts: dict[str, Counter[str]] = {}
    for attempt in range(1, RETRIES + 1):
        counts = {}
        try:
            for item in _items(client, collection, window.interval):
                bucket = year_bucket(item, today)
                if bucket not in window.buckets:
                    continue
                bucket_counts = counts.setdefault(bucket, Counter())
                bucket_counts['records'] += 1
                for check, failed in check_record(item, record_type, known_hazard_codes, today).items():
                    bucket_counts[check] += failed
            return counts
        except Exception as error:  # the API raises a bare APIError on timeouts
            logger.warning('%s %s attempt %d failed: %s', collection, window.label, attempt, error)
            time.sleep(5 * attempt)
    raise RuntimeError(f'{collection} {window.label} failed {RETRIES} times')


def _load_checkpoint() -> dict[tuple[str, str], dict[str, Any]]:
    done = {}
    if CHECKPOINT_PATH.exists():
        for line in CHECKPOINT_PATH.read_text().splitlines():
            row = json.loads(line)
            done[(row['collection'], row['window'])] = row
    return done


def record_collections(client: Client) -> dict[str, int]:
    """
    Every events, hazards and impacts collection, with the first year of
    its temporal extent.
    """
    result = {}
    for collection in client.get_collections():
        if collection.id in SKIPPED_COLLECTIONS or not collection.id.endswith(RECORD_SUFFIXES):
            continue
        start = collection.extent.temporal.intervals[0][0]
        result[collection.id] = start.year if start else FIRST_YEAR
    return dict(sorted(result.items()))


def run(collections: list[str] | None, refresh: bool, today: date) -> None:
    """
    Count every window not yet in the checkpoint, appending each as it finishes.
    """
    client = get_pystac_client()
    known_hazard_codes = set(hazard_code_lookup()['hazard_code'].to_list())
    available = record_collections(client)
    selected = collections or list(available)
    unknown = sorted(set(selected) - set(available))
    if unknown:
        raise ValueError(f'Not record collections in Montandon: {unknown}')

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if refresh:
        CHECKPOINT_PATH.unlink(missing_ok=True)
    done = _load_checkpoint()
    with CHECKPOINT_PATH.open('a') as checkpoint:
        for collection in selected:
            first_year = available[collection]
            monthly_from = first_year if collection.startswith('usgs-') else None
            for window in windows(first_year, today, monthly_from):
                if (collection, window.label) in done:
                    continue
                row: dict[str, Any] = {
                    'collection': collection, 'window': window.label,
                    'run_date': today.isoformat(),
                }
                try:
                    row['counts'] = count_window(client, collection, window, known_hazard_codes, today)
                except RuntimeError as error:
                    logger.error('%s', error)
                    row['failed'] = True
                checkpoint.write(json.dumps(row) + '\n')
                checkpoint.flush()
            logger.info('Finished %s', collection)


# Reporting

def summarise() -> tuple[list[dict[str, Any]], list[str]]:
    """
    One row per collection and year from the checkpoint, failed windows
    flagged so a gap in the counts is never silent.
    """
    rows: dict[tuple[str, str], Counter[str]] = {}
    failed: Counter[str] = Counter()
    run_dates = set()
    for row in _load_checkpoint().values():
        if row['collection'] in SKIPPED_COLLECTIONS:
            continue
        run_dates.add(row['run_date'])
        if row.get('failed'):
            failed[row['collection']] += 1
            continue
        for bucket, counts in row['counts'].items():
            rows.setdefault((row['collection'], bucket), Counter()).update(counts)
    if len(run_dates) > 1:
        logger.warning('Checkpoint mixes runs from %s; pass --refresh for one snapshot', sorted(run_dates))
    return [
        {
            'collection': collection, 'year': bucket,
            'failed_windows': failed[collection],
            'records': counts['records'],
            **{check: counts[check] for check in CHECKS},
        }
        for (collection, bucket), counts in sorted(rows.items())
    ], sorted(run_dates)


def write_csv(rows: list[dict[str, Any]]) -> None:
    with CSV_PATH.open('w', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=['collection', 'year', 'failed_windows', 'records', *CHECKS])
        writer.writeheader()
        writer.writerows(rows)


def percent(count: int, records: int) -> str:
    """
    Whole percent, but '<1' rather than '0' so a rare problem stays visible.
    """
    if not records:
        return '–'
    if 0 < count < records / 200:
        return '<1'
    return f'{100 * count / records:.0f}'


def write_report(rows: list[dict[str, Any]], run_dates: list[str]) -> None:
    """
    Per-collection totals as percentages, for a first read; the CSV has
    the per-year counts.
    """
    totals: dict[str, Counter[str]] = {}
    failed: dict[str, int] = {}
    for row in rows:
        totals.setdefault(row['collection'], Counter()).update(
            {key: row[key] for key in ['records', *CHECKS]},
        )
        failed[row['collection']] = row['failed_windows']
    lines = [
        '# Montandon missing fields',
        '',
        f"Snapshot of the staging API on {', '.join(run_dates)}. USGS is limited to M{USGS_MIN_MAGNITUDE}+.",
        'Percent of records with each problem; per-year counts are in `missing_fields.csv`.',
        '',
        '| Check | Meaning |', '|---|---|',
        *[f'| `{check}` | {meaning} |' for check, meaning in CHECKS.items()],
        '',
        '| Collection | Records | Failed windows | ' + ' | '.join(f'`{check}`' for check in CHECKS) + ' |',
        '|---|---|---|' + '---|' * len(CHECKS),
    ]
    for collection, counts in sorted(totals.items()):
        records = counts['records']
        cells = [percent(counts[check], records) for check in CHECKS]
        lines.append(f"| {collection} | {records:,} | {failed[collection]} | " + ' | '.join(cells) + ' |')
    empty = sorted({row['collection'] for row in _load_checkpoint().values()} - set(totals) - SKIPPED_COLLECTIONS)
    if empty:
        lines += ['', f"No records: {', '.join(f'`{collection}`' for collection in empty)}."]
    REPORT_PATH.write_text('\n'.join(lines) + '\n')


# Main

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--collections', nargs='+', help='Collections to count; defaults to every record collection.')
    parser.add_argument('--refresh', action='store_true', help='Discard the checkpoint and count again.')
    parser.add_argument('--report-only', action='store_true', help='Rebuild the CSV and report from the checkpoint.')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    load_dotenv()
    today = datetime.now(timezone.utc).date()
    if not args.report_only:
        run(args.collections, args.refresh, today)
    rows, run_dates = summarise()
    RESULTS_DIR.mkdir(exist_ok=True)
    write_csv(rows)
    write_report(rows, run_dates)
    logger.info('Wrote %s and %s', CSV_PATH, REPORT_PATH)


if __name__ == '__main__':
    main()
