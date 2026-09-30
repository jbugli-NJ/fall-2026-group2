"""
Script to update network node data using stored bucket data.
"""

# Imports

from collections.abc import Generator, Iterable
import gzip
import json
import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from pydantic import ValidationError
from sentence_transformers import SentenceTransformer

from monty_tool.api_schemas import MontandonItem
from monty_tool.boto3_utils.s3_protocols import S3Bucket
from monty_tool.boto3_utils.s3_utils import (
    download_object,
    get_bucket,
    upload_object,
)
from monty_tool.embeddings.generate import EMBEDDING_MODEL_NAME
from monty_tool.network.node_data import montandon_items_to_node_data


# Logger

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)


# Constants

RAW_PREFIX = 'raw/'
MONTANDON_NODE_DATA_PREFIX = 'node_data/montandon/'
BATCH_SIZE = 128


# Helpers

def _bucket_name() -> str:
    """
    Retrieve the selected bucket name from the environment.
    """
    bucket = os.getenv('AWS_BUCKET', '').strip()
    if bucket  == '':
        logger.warning('AWS_BUCKET is unset! Defaulting to dats-capstone')
        bucket = 'dats-capstone'
    return bucket


def _raw_gzip_keys(bucket: S3Bucket) -> list[str]:
    """
    Return all gzipped JSONL object keys under the raw-data prefix.
    """
    return sorted(
        obj.key
        for obj in bucket.objects.filter(Prefix=RAW_PREFIX)
        if obj.key.endswith('.jsonl.gz')
    )


def _read_gzip(path: Path) -> Generator[Any, None, None]:
    """
    Generator to return `.gzip` file contents.
    """
    with gzip.open(path, 'rt', encoding='utf-8') as file:
        for line in file:
            yield json.loads(line)


def _montandon_items(path: Path) -> Generator[MontandonItem, None, None]:
    """
    Validate each JSONL record in a gzip file as a Montandon item.
    """
    for line_number, record in enumerate(_read_gzip(path), start=1):
        try:
            yield MontandonItem.model_validate(record)
        except ValidationError as error:
            raise ValueError(
                f'Invalid Montandon record in {path} at line {line_number}'
            ) from error


def _batches(
    items: Iterable[MontandonItem],
    batch_size: int,
    ) -> Generator[list[MontandonItem], None, None]:
    """
    Yield fixed-size batches from an iterable of Montandon items.
    """
    batch: list[MontandonItem] = []
    for item in items:
        batch.append(item)
        if len(batch) == batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def _process_gzip(
    input_path: Path,
    output_path: Path,
    embedding_model: SentenceTransformer,
    ) -> int:
    """
    Convert a gzip JSONL file of Montandon records into gzip JSONL node data.
    """
    node_count = 0
    with gzip.open(output_path, 'wt', encoding='utf-8') as file:
        for items in _batches(_montandon_items(input_path), BATCH_SIZE):
            node_data = montandon_items_to_node_data(
                items=items,
                embedding_model=embedding_model,
            )
            for node in node_data:
                file.write(json.dumps(node, default=str) + '\n')
                node_count += 1
    logger.debug(f"Processed {node_count} nodes | {output_path=}")
    return node_count


def main():
    bucket_name = _bucket_name()
    bucket = get_bucket(bucket_name)
    raw_keys = _raw_gzip_keys(bucket)
    if not raw_keys:
        raise ValueError(f'No gzip JSONL files found under {RAW_PREFIX!r}')

    embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    with TemporaryDirectory() as temporary_directory:
        temporary_path = Path(temporary_directory)
        for raw_key in raw_keys:
            bucket_input_uri = f"s3://{bucket_name}/{raw_key.lstrip('/')}"
            logger.info(f"Starting processing: {bucket_input_uri}")
            input_path = temporary_path.joinpath('input.jsonl.gz')
            output_path = temporary_path.joinpath('output.jsonl.gz')
            output_key = MONTANDON_NODE_DATA_PREFIX + Path(raw_key).name
            download_object(bucket, raw_key, input_path)
            logger.info(f"Downloaded {bucket_input_uri} to {input_path}")
            node_count = _process_gzip(
                input_path=input_path,
                output_path=output_path,
                embedding_model=embedding_model,
            )
            logger.info(f"Saved node data: {output_path}")
            logger.info('Uploading %s (%d nodes)', output_key, node_count)
            upload_object(bucket, output_path, output_key)
            logger.info(f"Uploaded node data: s3://{bucket_name}/{output_key.lstrip('/')}")


if __name__ == '__main__':
    main()
