"""
Script to update network node data using stored bucket data.
"""

# Imports

from collections.abc import Callable, Generator, Iterable
import gzip
import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from pydantic import ValidationError
from sentence_transformers import SentenceTransformer

from monty_tool.api_schemas import GOAppeal, GOEvent, MontandonItem
from monty_tool.boto3_utils.s3_protocols import S3Bucket
from monty_tool.boto3_utils.s3_utils import (
    download_object,
    get_bucket,
    upload_object,
)
from monty_tool.embeddings.generate import EMBEDDING_MODEL
from monty_tool.network.node_data import (
    go_appeals_to_node_data,
    go_events_to_node_data,
    montandon_items_to_node_data,
)
from monty_tool.tools.resources import (
    GO_APPEAL_BUCKET_KEY,
    GO_APPEAL_NODE_DATA_BUCKET_KEY,
    GO_EVENT_BUCKET_KEY,
    GO_EVENT_NODE_DATA_BUCKET_KEY,
    MONTANDON_NODE_DATA_BUCKET_PREFIX,
    RAW_BUCKET_PREFIX,
    get_env_bucket_name,
    read_gzip,
)


# Logger

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


# Constants

BATCH_SIZE = 128


# Helpers

def _raw_gzip_keys(bucket: S3Bucket) -> list[str]:
    """
    Return all gzipped JSONL object keys under the raw data prefix.
    """
    return sorted(
        obj.key
        for obj in bucket.objects.filter(Prefix=RAW_BUCKET_PREFIX)
        if obj.key.endswith('.jsonl.gz')
    )


def _montandon_items(path: Path) -> Generator[MontandonItem, None, None]:
    """
    Validate each JSONL record in a gzip file as a Montandon item.
    """
    for line_number, record in enumerate(read_gzip(path), start=1):
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


def _go_events(path: Path) -> list[GOEvent]:
    """
    Validate every gzip JSONL record as an IFRC GO event.
    """
    events: list[GOEvent] = []
    for line_number, record in enumerate(read_gzip(path), start=1):
        try:
            events.append(GOEvent.model_validate(record))
        except ValidationError as error:
            raise ValueError(
                f'Invalid GO event record in {path} at line {line_number}'
            ) from error
    return events


def _go_appeals(path: Path) -> list[GOAppeal]:
    """
    Validate every gzip JSONL record as an IFRC GO appeal.
    """
    appeals: list[GOAppeal] = []
    for line_number, record in enumerate(read_gzip(path), start=1):
        try:
            appeals.append(GOAppeal.model_validate(record))
        except ValidationError as error:
            raise ValueError(
                f'Invalid GO appeal record in {path} at line {line_number}'
            ) from error
    return appeals


def _write_gzip(path: Path, node_data: Iterable[Any]) -> int:
    """
    Write node data as a gzip JSONL file and return its record count.
    """
    node_count = 0
    with gzip.open(path, 'wt', encoding='utf-8') as file:
        for node in node_data:
            file.write(json.dumps(node, default=str) + '\n')
            node_count += 1
    return node_count


def _process_go_event_gzip(input_path: Path, output_path: Path) -> int:
    """
    Convert gzip JSONL IFRC GO events into gzip JSONL node data.
    """
    return _write_gzip(
        output_path,
        go_events_to_node_data(_go_events(input_path)),
    )


def _process_go_appeal_gzip(input_path: Path, output_path: Path) -> int:
    """
    Convert gzip JSONL IFRC GO appeals into gzip JSONL node data.
    """
    return _write_gzip(
        output_path,
        go_appeals_to_node_data(_go_appeals(input_path)),
    )


def _process_and_upload(
    bucket: S3Bucket,
    bucket_name: str,
    input_key: str,
    output_key: str,
    input_path: Path,
    output_path: Path,
    processor: Callable[[Path, Path], int],
    ) -> None:
    """
    Download one bucket object, convert it, and upload its node data.
    """
    input_uri = f's3://{bucket_name}/{input_key}'
    output_uri = f's3://{bucket_name}/{output_key}'
    logger.info('Starting processing: %s', input_uri)
    download_object(bucket, input_key, input_path)
    logger.info('Downloaded %s to %s', input_uri, input_path)
    node_count = processor(input_path, output_path)
    logger.info('Saved node data: %s', output_path)
    upload_object(bucket, output_path, output_key)
    logger.info('Uploaded %s (%d nodes)', output_uri, node_count)


def main():
    bucket_name = get_env_bucket_name()
    bucket = get_bucket(bucket_name)
    raw_keys = _raw_gzip_keys(bucket)
    if not raw_keys:
        raise ValueError(
            f'No gzip JSONL files found under {RAW_BUCKET_PREFIX!r}'
        )

    embedding_model = SentenceTransformer(
        EMBEDDING_MODEL.value,
        revision=EMBEDDING_MODEL.revision,
    )
    with TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        for raw_key in raw_keys:
            input_path = tmp_path.joinpath('input.jsonl.gz')
            output_path = tmp_path.joinpath('output.jsonl.gz')
            output_key = MONTANDON_NODE_DATA_BUCKET_PREFIX + Path(raw_key).name
            _process_and_upload(
                bucket=bucket,
                bucket_name=bucket_name,
                input_key=raw_key,
                output_key=output_key,
                input_path=input_path,
                output_path=output_path,
                processor=lambda input_path, output_path: _process_gzip(
                    input_path=input_path,
                    output_path=output_path,
                    embedding_model=embedding_model,
                ),
            )
        _process_and_upload(
            bucket=bucket,
            bucket_name=bucket_name,
            input_key=GO_EVENT_BUCKET_KEY,
            output_key=GO_EVENT_NODE_DATA_BUCKET_KEY,
            input_path=tmp_path.joinpath('go-event-input.jsonl.gz'),
            output_path=tmp_path.joinpath('go-event-output.jsonl.gz'),
            processor=_process_go_event_gzip,
        )
        _process_and_upload(
            bucket=bucket,
            bucket_name=bucket_name,
            input_key=GO_APPEAL_BUCKET_KEY,
            output_key=GO_APPEAL_NODE_DATA_BUCKET_KEY,
            input_path=tmp_path.joinpath('go-appeal-input.jsonl.gz'),
            output_path=tmp_path.joinpath('go-appeal-output.jsonl.gz'),
            processor=_process_go_appeal_gzip,
        )


if __name__ == '__main__':
    main()
