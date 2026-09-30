"""
Set up the local network database from node data stored in an S3 bucket.
"""

# Imports

import logging
from collections.abc import Generator
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, TypeVar

from pydantic import TypeAdapter, ValidationError

from monty_tool.boto3_utils.s3_protocols import S3Bucket
from monty_tool.boto3_utils.s3_utils import download_object, get_bucket
from monty_tool.network.initialize import initialize_db
from monty_tool.network.insert import (
    insert_go_records_into_graph_db,
    insert_montandon_records_into_graph_db,
)
from monty_tool.network.schemas import (
    GOAppealNodeData,
    GOEventNodeData,
    MontandonItemNodeData,
)
from monty_tool.tools.resources import (
    GO_APPEAL_NODE_DATA_BUCKET_KEY,
    GO_EVENT_NODE_DATA_BUCKET_KEY,
    MONTANDON_NODE_DATA_BUCKET_PREFIX,
    get_env_bucket_name,
    read_gzip,
)


# Logger

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


# Validators

MONTANDON_NODE_DATA_ADAPTER = TypeAdapter(MontandonItemNodeData)
GO_EVENT_NODE_DATA_ADAPTER = TypeAdapter(GOEventNodeData)
GO_APPEAL_NODE_DATA_ADAPTER = TypeAdapter(GOAppealNodeData)

NodeData = TypeVar('NodeData')


# Helpers

def _montandon_node_data_keys(bucket: S3Bucket) -> list[str]:
    """
    Return every Montandon node data object key in the bucket.
    """
    return sorted(
        obj.key
        for obj in bucket.objects.filter(Prefix=MONTANDON_NODE_DATA_BUCKET_PREFIX)
        if obj.key.endswith('.jsonl.gz')
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

    initialize_db()
    with TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        for key in montandon_keys:
            node_data = _download_node_data(
                bucket=bucket,
                bucket_name=bucket_name,
                key=key,
                tmp_path=tmp_path,
                adapter=MONTANDON_NODE_DATA_ADAPTER,
                node_type='Montandon',
            )
            insert_montandon_records_into_graph_db(node_data)

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


if __name__ == '__main__':
    main()
