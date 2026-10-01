"""
Update NASA POWER weather results using Montandon records stored in S3.
"""

# Imports

import gzip
import logging
from collections.abc import Iterator
from itertools import batched
from pathlib import Path
from tempfile import TemporaryDirectory

from botocore.exceptions import ClientError
from pydantic import ValidationError

from monty_tool.api_schemas import MontandonItem, PointGeometry
from monty_tool.boto3_utils.s3_protocols import S3Bucket
from monty_tool.boto3_utils.s3_utils import download_object, get_bucket, upload_object
from monty_tool.tools.resources import (
    NASA_POWER_BUCKET_KEY,
    RAW_BUCKET_PREFIX,
    get_env_bucket_name,
    read_gzip,
)
from monty_tool.weather.retrieval import WeatherQueryKey, pull_event_weather
from monty_tool.weather.schemas import WeatherResult


# Logger

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


# Constants

BATCH_SIZE = 500


# Helpers

def _download_existing(bucket: S3Bucket, path: Path) -> set[str]:
    """
    Download and validate existing weather results, returning their item IDs.
    """
    try:
        download_object(bucket, NASA_POWER_BUCKET_KEY, path)
    except ClientError as error:
        if error.response['Error']['Code'] not in {'404', 'NoSuchKey', 'NotFound'}:
            raise
        logger.info('No existing NASA POWER data; starting a new file.')
        with gzip.open(path, 'wt', encoding='utf-8'):
            pass
        return set()

    item_ids = set()
    for line_number, record in enumerate(read_gzip(path), start=1):
        try:
            result = WeatherResult.model_validate(record)
        except ValidationError as error:
            raise ValueError(
                f'Invalid NASA POWER result in {path} at line {line_number}'
            ) from error
        item_ids.add(result.item_id)
    return item_ids


def _new_point_items(
    bucket: S3Bucket,
    path: Path,
    existing_ids: set[str],
    ) -> Iterator[MontandonItem]:
    """
    Download and validate raw records, yielding records with point geometry.
    """
    keys = sorted(
        obj.key
        for obj in bucket.objects.filter(Prefix=RAW_BUCKET_PREFIX)
        if obj.key.endswith('.jsonl.gz')
    )
    if not keys:
        raise ValueError(f'No gzip JSONL files found under {RAW_BUCKET_PREFIX!r}')

    seen_ids = set(existing_ids)
    for key in keys:
        logger.info(f'Downloading Montandon data: {key}')
        download_object(bucket, key, path)
        for line_number, record in enumerate(read_gzip(path), start=1):
            try:
                item = MontandonItem.model_validate(record)
            except ValidationError as error:
                raise ValueError(
                    f'Invalid Montandon record in {key} at line {line_number}'
                ) from error
            if not isinstance(item.geometry, PointGeometry) or item.id in seen_ids:
                continue
            seen_ids.add(item.id)
            yield item


def main() -> None:
    """
    Collect new point-record weather and upload a checkpoint after each batch.
    """
    bucket_name = get_env_bucket_name()
    bucket = get_bucket(bucket_name)
    with TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        weather_path = tmp_path.joinpath('weather.jsonl.gz')
        existing_ids = _download_existing(bucket, weather_path)
        logger.info(f'Loaded {len(existing_ids)} existing NASA POWER results.')

        items = _new_point_items(bucket, tmp_path.joinpath('montandon.jsonl.gz'), existing_ids)
        results_by_query: dict[WeatherQueryKey, WeatherResult] = {}

        for i, batch in enumerate(batched(items, BATCH_SIZE)):
            results = pull_event_weather(list(batch), results_by_query=results_by_query)
            with gzip.open(weather_path, 'at', encoding='utf-8') as file:
                for result in results:
                    file.write(result.model_dump_json() + '\n')
            upload_object(bucket, weather_path, NASA_POWER_BUCKET_KEY)
            logger.info(
                f"Uploaded batch {i+1} ({len(results)} results) "
                f"to s3://{bucket_name}/{NASA_POWER_BUCKET_KEY}"
            )


if __name__ == '__main__':
    main()
