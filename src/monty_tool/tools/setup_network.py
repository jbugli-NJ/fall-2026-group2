"""
Set up the local network database from node data stored in an S3 bucket.
"""

# Imports

import logging
from collections.abc import Generator
from itertools import batched
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, TypeVar

from pydantic import TypeAdapter, ValidationError
from botocore.exceptions import ClientError

from monty_tool.boto3_utils.s3_protocols import S3Bucket
from monty_tool.boto3_utils.s3_utils import download_object, get_bucket
from monty_tool.network.initialize import (
    clear_db,
    initialize_db,
    initialize_vector_indexes,
)
from monty_tool.network.insert import (
    create_montandon_deterministic_relationships,
    create_montandon_similarity_relationships,
    insert_go_records_into_graph_db,
    insert_montandon_nodes,
)
from monty_tool.network.resources import (
    NETWORK_INSERT_BATCH_SIZE,
    get_graph_db_driver,
)
from monty_tool.network.schemas import (
    GOAppealNodeData,
    GOEventNodeData,
    MontandonItemNodeData,
)
from monty_tool.network.weather import insert_weather_properties, validated_weather_node_data
from monty_tool.tools.resources import (
    GO_APPEAL_NODE_DATA_BUCKET_KEY,
    GO_EVENT_NODE_DATA_BUCKET_KEY,
    MONTANDON_NODE_DATA_BUCKET_PREFIX,
    NASA_POWER_BUCKET_KEY,
    get_env_bucket_name,
    read_gzip,
)
from monty_tool.network.news import load_news_into_graph


# Logger

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


# Validators

MONTANDON_NODE_DATA_ADAPTER = TypeAdapter(MontandonItemNodeData)
GO_EVENT_NODE_DATA_ADAPTER = TypeAdapter(GOEventNodeData)
GO_APPEAL_NODE_DATA_ADAPTER = TypeAdapter(GOAppealNodeData)

NodeData = TypeVar('NodeData')


# Helpers

def _download_validated_weather(bucket: S3Bucket, bucket_name: str, tmp_path: Path) -> Path:
    """
    Download and validate weather data from the S3 bucket.
    """
    path = tmp_path.joinpath('weather.jsonl.gz')
    uri = f's3://{bucket_name}/{NASA_POWER_BUCKET_KEY}'
    logger.info('Downloading %s', uri)
    try:
        download_object(bucket, NASA_POWER_BUCKET_KEY, path)
    except ClientError as error:
        if error.response['Error']['Code'] in {'404', 'NoSuchKey', 'NotFound'}:
            raise ValueError(f'Required NASA POWER input is missing: {uri}') from error
        raise
    seen_ids: set[str] = set()
    for line_number, node in enumerate(validated_weather_node_data(path), start=1):
        if node['item_id'] in seen_ids:
            raise ValueError(
                f'Duplicate weather item_id {node["item_id"]!r} in {path} at line {line_number}'
            )
        seen_ids.add(node['item_id'])
    logger.info('Validated %d NASA POWER records', len(seen_ids))
    if not seen_ids:
        logger.warning('NASA POWER input is empty; rebuilding without weather properties.')
    return path


def _insert_weather_data(path: Path) -> None:
    """
    Stream weather properties into existing Montandon nodes in batches.
    """
    count = 0
    with get_graph_db_driver() as driver:
        for batch in batched(validated_weather_node_data(path), NETWORK_INSERT_BATCH_SIZE):
            count += insert_weather_properties(driver, list(batch))
    logger.info('Attached NASA POWER properties to %d Montandon nodes', count)


def _montandon_node_data_keys(bucket: S3Bucket) -> list[str]:
    """
    Return Montandon gzip keys, preferring nogeom when both variants exist.
    """
    keys = {
        obj.key
        for obj in bucket.objects.filter(Prefix=MONTANDON_NODE_DATA_BUCKET_PREFIX)
        if obj.key.endswith('.jsonl.gz')
    }
    return sorted(
        key for key in keys
        if key.removesuffix('.jsonl.gz') + '.nogeom.jsonl.gz' not in keys
    )


def _validated_node_data(
    path: Path,
    adapter: TypeAdapter[NodeData],
    node_type: str,
    ) -> Generator[NodeData, None, None]:
    """
    Yield node data validated using a Pydantic TypedDict adapter.
    """
    for line_number, record in enumerate(read_gzip(path), start=1):
        try:
            yield adapter.validate_python(record)
        except ValidationError as error:
            raise ValueError(
                f'Invalid {node_type} node data in {path} at line {line_number}'
            ) from error


def _download_node_data(
    bucket: S3Bucket,
    bucket_name: str,
    key: str,
    tmp_path: Path,
    adapter: TypeAdapter[NodeData],
    node_type: str,
    ) -> list[NodeData]:
    """
    Download and validate one gzip JSONL node data object.
    """
    path = tmp_path.joinpath(Path(key).name)
    logger.info('Downloading s3://%s/%s', bucket_name, key)
    download_object(bucket, key, path)
    return list(_validated_node_data(path, adapter, node_type))


def _insert_montandon_node_data(
    bucket: S3Bucket,
    bucket_name: str,
    key: str,
    tmp_path: Path,
    ) -> None:
    """
    Download, validate, and batch-insert one Montandon node-data object.
    """
    path = tmp_path.joinpath(Path(key).name)
    logger.info('Downloading s3://%s/%s', bucket_name, key)
    download_object(bucket, key, path)
    with get_graph_db_driver() as driver:
        for node_data in batched(
            _validated_node_data(
                path,
                MONTANDON_NODE_DATA_ADAPTER,
                'Montandon',
            ),
            NETWORK_INSERT_BATCH_SIZE,
        ):
            insert_montandon_nodes(driver, list(node_data))


def main():
    """
    Initialize the local network database and load its stored node data.
    """
    bucket_name = get_env_bucket_name()
    bucket = get_bucket(bucket_name)
    montandon_keys = _montandon_node_data_keys(bucket)
    if not montandon_keys:
        raise ValueError(
            'No gzip JSONL node data found under '
            f'{MONTANDON_NODE_DATA_BUCKET_PREFIX!r}'
        )

    with TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        weather_path = _download_validated_weather(bucket, bucket_name, tmp_path)
        clear_db()
        initialize_db()
        for key in montandon_keys:
            _insert_montandon_node_data(
                bucket=bucket,
                bucket_name=bucket_name,
                key=key,
                tmp_path=tmp_path,
            )
        _insert_weather_data(weather_path)
        initialize_vector_indexes()
        create_montandon_deterministic_relationships()
        create_montandon_similarity_relationships()

        event_data = _download_node_data(
            bucket=bucket,
            bucket_name=bucket_name,
            key=GO_EVENT_NODE_DATA_BUCKET_KEY,
            tmp_path=tmp_path,
            adapter=GO_EVENT_NODE_DATA_ADAPTER,
            node_type='GO event',
        )
        appeal_data = _download_node_data(
            bucket=bucket,
            bucket_name=bucket_name,
            key=GO_APPEAL_NODE_DATA_BUCKET_KEY,
            tmp_path=tmp_path,
            adapter=GO_APPEAL_NODE_DATA_ADAPTER,
            node_type='GO appeal',
        )
        insert_go_records_into_graph_db(event_data, appeal_data)

        with get_graph_db_driver() as driver:
            news_links = load_news_into_graph(
                bucket,
                bucket_name=bucket_name,
                driver=driver,
            )
        logger.info("Loaded %d NewsAPI article-event links", news_links)

if __name__ == '__main__':
    main()
