"""
Local cache of raw Montandon Items, so collections are pulled once and
read from disk thereafter.
"""

# Imports

import gzip
import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import polars as pl
from pystac_client import Client

from monty_tool.api_utils import DEFAULT_PAGE_SIZE, get_pystac_client


# Resources

# Gitignored, so everything under here is local-only.
DEFAULT_CACHE_DIR = Path('data/raw')

# STAC `fields` extension payload that strips geometry from Items. On
# country-level sources like EM-DAT, geometry is >99% of the payload and
# duplicates `monty:country_codes`.
NO_GEOMETRY_FIELDS = {'exclude': ['geometry']}


# Cache utilities

def raw_cache_path(
    collection_id: str,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    geometry: bool = True,
    ) -> Path:
    """
    Path to the gzipped JSON Lines file caching a collection, with a
    `.nogeom` marker when it was pulled without geometry.
    """
    suffix = '.jsonl.gz' if geometry else '.nogeom.jsonl.gz'
    return cache_dir / f'{collection_id}{suffix}'


def pull_collection(
    collection_id: str,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    client: Client | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    geometry: bool = True,
    ) -> Path:
    """
    Stream every raw Item in a collection to a gzipped JSON Lines file,
    one Item per line, and return its path.

    Pages are written as they arrive, into a temporary file that is only
    renamed into place once the pull completes, so a failed pull never
    leaves a partial cache behind.

    With `geometry=False`, the server strips geometry before sending; use
    this for country-level sources where a single Item can exceed 10 MB.
    """
    if client is None:
        client = get_pystac_client()
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = raw_cache_path(collection_id, cache_dir, geometry=geometry)
    partial_path = path.with_suffix('.partial')
    search = client.search(
        collections=[collection_id],
        limit=page_size,
        fields=None if geometry else NO_GEOMETRY_FIELDS,
    )
    with gzip.open(partial_path, 'wt', encoding='utf-8') as file:
        for page in search.pages_as_dicts():
            for item in page.get('features', []):
                file.write(json.dumps(item) + '\n')
    partial_path.replace(path)
    return path


def load_collection(
    collection_id: str,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    geometry: bool = True,
    ) -> Iterator[dict[str, Any]]:
    """
    Yield raw Items from a cached collection, one at a time.

    Wrap in `list(...)` for a full in-memory collection.
    """
    path = raw_cache_path(collection_id, cache_dir, geometry=geometry)
    if not path.exists():
        raise FileNotFoundError(
            f'{collection_id!r} is not cached at {path}; '
            f'run pull_collection({collection_id!r}, geometry={geometry}) first'
        )
    with gzip.open(path, 'rt', encoding='utf-8') as file:
        for line in file:
            yield json.loads(line)


# DataFrame utilities

def _flatten_item(item: dict[str, Any]) -> dict[str, Any]:
    """
    Flatten one raw Item into a single-level record.

    Top-level STAC keys are kept as-is, `properties` are promoted to
    columns under their API names (e.g. `monty:corr_id`), and nested
    dictionaries within properties are flattened with dotted keys
    (e.g. `monty:impact_detail.value`). Geometry is reduced to its type.
    """
    record: dict[str, Any] = {
        'id': item.get('id'),
        'collection': item.get('collection'),
        'bbox': item.get('bbox'),
        'geometry_type': (item.get('geometry') or {}).get('type'),
        'n_links': len(item.get('links') or []),
        'n_assets': len(item.get('assets') or {}),
    }
    for key, value in (item.get('properties') or {}).items():
        if isinstance(value, dict):
            for sub_key, sub_value in value.items():
                record[f'{key}.{sub_key}'] = sub_value
        else:
            record[key] = value
    return record


def items_to_frame(items: Iterable[dict[str, Any]]) -> pl.DataFrame:
    """
    Build a Polars DataFrame with one row per raw Item.

    Columns are the union of keys across all Items; Items missing a key
    get null, so column null-rates directly measure field coverage.
    """
    records = [_flatten_item(item) for item in items]
    return pl.DataFrame(records, infer_schema_length=None, strict=False)
