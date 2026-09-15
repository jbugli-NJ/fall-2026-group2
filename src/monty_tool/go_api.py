"""
IFRC GO API access and local cache.

GO is the operational platform behind `ifrcevent-*` Items in Montandon:
appeals, DREFs, field reports, and severity levels live here, not in the
STAC API. Montandon's `ifrcevent-event-<n>` IDs are GO event IDs.

The public endpoints used here need no token; anonymous requests see
`visibility = PUBLIC` records only.
"""

# Imports

import gzip
import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import polars as pl
import requests

from monty_tool.data_cache import DEFAULT_CACHE_DIR


# Resources

GO_API_URL = 'https://goadmin.ifrc.org/api/v2'

# Largest page the list endpoints return.
GO_PAGE_SIZE = 500

# Courtesy pause between pages; the API has no configured throttle.
GO_PAGE_DELAY_SECONDS = 0.5

GO_CACHE_DIR = DEFAULT_CACHE_DIR.parent / 'go'

# `appeal.atype`
APPEAL_TYPES = {0: 'DREF', 1: 'Emergency Appeal', 2: 'International Appeal', 3: 'Forecast Based Action'}



# API utilities

def iter_go_list(resource: str, params: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
    """
    Yield every record from a paginated GO list endpoint such as `event`
    or `appeal`.
    """
    url: str | None = f'{GO_API_URL}/{resource}/'
    query = {'limit': GO_PAGE_SIZE, **(params or {})}
    while url:
        response = requests.get(url, params=query, timeout=60)
        response.raise_for_status()
        payload = response.json()
        yield from payload.get('results', [])
        url = payload.get('next')
        query = {}  # `next` already carries the offset
        if url:
            time.sleep(GO_PAGE_DELAY_SECONDS)


def go_cache_path(resource: str, cache_dir: Path = GO_CACHE_DIR) -> Path:
    """
    Path to the gzipped JSON Lines file caching a GO resource.
    """
    return cache_dir / f'{resource}.jsonl.gz'


def pull_go_resource(resource: str, cache_dir: Path = GO_CACHE_DIR) -> Path:
    """
    Stream every public record of a GO list resource to a gzipped JSON
    Lines file and return its path.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = go_cache_path(resource, cache_dir)
    partial_path = path.with_suffix('.partial')
    with gzip.open(partial_path, 'wt', encoding='utf-8') as file:
        for record in iter_go_list(resource):
            file.write(json.dumps(record) + '\n')
    partial_path.replace(path)
    return path


def load_go_resource(resource: str, cache_dir: Path = GO_CACHE_DIR) -> Iterator[dict[str, Any]]:
    """
    Yield cached GO records one at a time.
    """
    path = go_cache_path(resource, cache_dir)
    if not path.exists():
        raise FileNotFoundError(f'{resource!r} is not cached at {path}; run pull_go_resource({resource!r}) first')
    with gzip.open(path, 'rt', encoding='utf-8') as file:
        for line in file:
            yield json.loads(line)


# DataFrame utilities

def _appeal_type(atype: Any) -> str | None:
    """
    Label an `appeal.atype` code; 0 is DREF, so test for None explicitly.
    """
    if atype is None:
        return None
    return APPEAL_TYPES.get(int(atype), str(atype))


def _name(value: Any) -> Any:
    """
    Reduce a nested `{id, name, ...}` object to its name.
    """
    return value.get('name') if isinstance(value, dict) else value


def events_to_frame(events: Iterator[dict[str, Any]] | list[dict[str, Any]]) -> pl.DataFrame:
    """
    One row per GO event with scalar fields, ISO3 country list, and
    appeal / field-report counts. Appeals are flattened separately by
    `appeals_to_frame`.
    """
    records = []
    for event in events:
        appeals = event.get('appeals') or []
        records.append({
            'go_event_id': event['id'],
            'name': event.get('name'),
            'dtype': _name(event.get('dtype')),
            'glide': event.get('glide') or None,
            'disaster_start_date': event.get('disaster_start_date'),
            'num_affected': event.get('num_affected'),
            'ifrc_severity_level': event.get('ifrc_severity_level_display'),
            'active_deployments': event.get('active_deployments'),
            'country_codes': [c.get('iso3') for c in event.get('countries') or [] if c.get('iso3')],
            'n_appeals': len(appeals),
            'n_field_reports': len(event.get('field_reports') or []),
            'amount_requested': sum(a.get('amount_requested') or 0 for a in appeals),
            'amount_funded': sum(a.get('amount_funded') or 0 for a in appeals),
            'num_beneficiaries': sum(a.get('num_beneficiaries') or 0 for a in appeals),
            'appeal_types': sorted({t for a in appeals if (t := _appeal_type(a.get('atype'))) is not None}),
            'created_at': event.get('created_at'),
        })
    return pl.DataFrame(records, infer_schema_length=None, strict=False)


def appeals_to_frame(appeals: Iterator[dict[str, Any]] | list[dict[str, Any]]) -> pl.DataFrame:
    """
    One row per GO appeal with its event ID, type, funding, and dates.
    """
    records = []
    for appeal in appeals:
        country = appeal.get('country') or {}
        records.append({
            'appeal_id': appeal['id'],
            'code': appeal.get('code'),
            'go_event_id': appeal.get('event'),
            'name': appeal.get('name'),
            'atype': _appeal_type(appeal.get('atype')),
            'status': appeal.get('status_display'),
            'dtype': _name(appeal.get('dtype')),
            'country_code': country.get('iso3') if isinstance(country, dict) else None,
            'region': (appeal.get('region') or {}).get('region_name'),
            'sector': appeal.get('sector'),
            'amount_requested': appeal.get('amount_requested'),
            'amount_funded': appeal.get('amount_funded'),
            'num_beneficiaries': appeal.get('num_beneficiaries'),
            'start_date': appeal.get('start_date'),
            'end_date': appeal.get('end_date'),
        })
    return pl.DataFrame(records, infer_schema_length=None, strict=False)
