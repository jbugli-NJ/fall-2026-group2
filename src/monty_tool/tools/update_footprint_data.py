"""
Build hazard footprint node data from raw Montandon records stored in S3.

ShakeMap footprints are linked to the GDACS and EM-DAT quakes matched by
monty_tool.geo.quake_links, not by correlation ID; EM-DAT quakes are
fetched from Montandon, since the shared raw EM-DAT file has no geometry.
"""

# Imports

import gzip
import json
import logging
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import requests

from monty_tool.boto3_utils.s3_protocols import S3Bucket
from monty_tool.boto3_utils.s3_utils import download_object, get_bucket, upload_object
from monty_tool.geo.collect import (
    FOOTPRINT_COLLECTIONS,
    MIN_SHAKEMAP_MAGNITUDE,
    footprints_from_raw_item,
)
from monty_tool.geo.gdacs import COLLECTION as GDACS_COLLECTION
from monty_tool.geo.quake_links import fetch_emdat_quakes, quake_event_links
from monty_tool.network.footprints import footprint_to_node_data
from monty_tool.network.schemas import FootprintNodeData
from monty_tool.tools.resources import (
    FOOTPRINT_NODE_DATA_BUCKET_KEY,
    RAW_BUCKET_PREFIX,
    get_env_bucket_name,
    read_gzip,
)


# Logger

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

USGS_COLLECTION = 'usgs-events'


# Helpers

def raw_footprint_keys(bucket: S3Bucket) -> dict[str, str]:
    """
    The raw file for each footprint collection, keyed by collection. GDACS
    polygons are Item geometry, so its `.nogeom` file is not usable.
    """
    names = {
        obj.key.removeprefix(RAW_BUCKET_PREFIX): obj.key
        for obj in bucket.objects.filter(Prefix=RAW_BUCKET_PREFIX)
    }
    keys = {}
    for collection in FOOTPRINT_COLLECTIONS:
        key = names.get(f'{collection}.jsonl.gz')
        if key is None and collection != GDACS_COLLECTION:
            key = names.get(f'{collection}.nogeom.jsonl.gz')
        if key is None:
            logger.warning('No usable raw file for %s; skipping it.', collection)
            continue
        keys[collection] = key
    return keys


def footprint_node_data(
    path: Path,
    event_links: dict[str, list[str]] | None = None,
) -> Iterator[FootprintNodeData]:
    """
    Footprint node data for every raw Item in a file, logging and skipping
    Items whose footprints cannot be fetched. With `event_links`, each
    footprint links to its Item's matched events instead of by correlation
    ID (an Item with no match links to none).
    """
    for line_number, item in enumerate(read_gzip(path), start=1):
        corr_id = item.get('properties', {}).get('monty:corr_id')
        if corr_id is None:
            continue
        try:
            footprints = footprints_from_raw_item(item)
        except (requests.RequestException, ValueError, KeyError) as error:
            logger.warning('Skipping %s at line %d: %s', item.get('id'), line_number, error)
            continue
        event_ids = None if event_links is None else event_links.get(item['id'], [])
        for footprint in footprints:
            yield footprint_to_node_data(footprint, corr_id, event_ids)


def shakemap_event_links(usgs_path: Path, gdacs_path: Path | None) -> dict[str, list[str]]:
    """
    GDACS and EM-DAT event IDs for each USGS quake in the raw USGS file.
    """
    links = quake_event_links(
        read_gzip(usgs_path),
        read_gzip(gdacs_path) if gdacs_path is not None else [],
        fetch_emdat_quakes(),
        min_magnitude=MIN_SHAKEMAP_MAGNITUDE,
    )
    logger.info('Matched %d USGS quakes to GDACS or EM-DAT events', len(links))
    return links


def main() -> None:
    """
    Rebuild the footprint node data, uploading a checkpoint after each collection.
    """
    bucket_name = get_env_bucket_name()
    bucket = get_bucket(bucket_name)
    keys = raw_footprint_keys(bucket)
    if not keys:
        raise ValueError(f'No footprint collections found under {RAW_BUCKET_PREFIX!r}')

    with TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        output_path = tmp_path.joinpath('footprint.jsonl.gz')
        seen_ids: set[str] = set()
        with gzip.open(output_path, 'wt', encoding='utf-8'):
            pass
        raw_paths: dict[str, Path] = {}
        for collection, key in keys.items():
            raw_path = tmp_path.joinpath(Path(key).name)
            logger.info('Downloading Montandon data: %s', key)
            download_object(bucket, key, raw_path)
            raw_paths[collection] = raw_path
        for collection, raw_path in raw_paths.items():
            event_links = (
                shakemap_event_links(raw_path, raw_paths.get(GDACS_COLLECTION))
                if collection == USGS_COLLECTION else None
            )
            count = 0
            with gzip.open(output_path, 'at', encoding='utf-8') as file:
                for node in footprint_node_data(raw_path, event_links):
                    if node['id'] in seen_ids:
                        continue
                    seen_ids.add(node['id'])
                    file.write(json.dumps(node, default=str) + '\n')
                    count += 1
            upload_object(bucket, output_path, FOOTPRINT_NODE_DATA_BUCKET_KEY)
            logger.info(
                'Uploaded %d %s footprints to s3://%s/%s',
                count, collection, bucket_name, FOOTPRINT_NODE_DATA_BUCKET_KEY,
            )

if __name__ == '__main__':
    main()
