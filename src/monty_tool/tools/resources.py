"""
Shared resources for tool operation.
"""

# Imports

from collections.abc import Generator
import gzip
import json
import logging
import os
from pathlib import Path
from typing import Any


# Logger

logger = logging.getLogger(__name__)


# Environment helpers

def get_env_bucket_name() -> str:
    """
    Retrieve the selected bucket name from the environment.
    """
    bucket = os.getenv('AWS_BUCKET', '').strip()
    if bucket  == '':
        logger.warning('AWS_BUCKET is unset! Defaulting to dats-capstone')
        bucket = 'dats-capstone'
    return bucket


def get_env_bucket_prefix() -> str:
    """
    Retrieve the selected S3 folder prefix with one trailing slash.
    """
    prefix = os.getenv('AWS_BUCKET_PREFIX', '').strip().strip('/')
    if prefix == '':
        logger.warning(
            'AWS_BUCKET_PREFIX is unset or blank! '
            'Defaulting to aidan.carlisle@gwu.edu/'
        )
        return 'aidan.carlisle@gwu.edu/'
    return prefix + '/'


# Bucket resources

BUCKET_DATA_PREFIX = get_env_bucket_prefix()

RAW_BUCKET_PREFIX = BUCKET_DATA_PREFIX + 'raw/'
GO_BUCKET_PREFIX = BUCKET_DATA_PREFIX + 'go/'
NASA_POWER_BUCKET_PREFIX = BUCKET_DATA_PREFIX + 'nasa_power/'
NASA_POWER_BUCKET_KEY = NASA_POWER_BUCKET_PREFIX + 'weather.jsonl.gz'

MONTANDON_NODE_DATA_BUCKET_PREFIX = BUCKET_DATA_PREFIX + 'node_data/montandon/'
GO_EVENT_NODE_DATA_BUCKET_PREFIX = BUCKET_DATA_PREFIX + 'node_data/go_event/'
GO_APPEAL_NODE_DATA_BUCKET_PREFIX = BUCKET_DATA_PREFIX + 'node_data/go_appeal/'

GO_EVENT_BUCKET_KEY = GO_BUCKET_PREFIX + 'event.jsonl.gz'
GO_APPEAL_BUCKET_KEY = GO_BUCKET_PREFIX + 'appeal.jsonl.gz'

GO_EVENT_NODE_DATA_BUCKET_KEY = (
    GO_EVENT_NODE_DATA_BUCKET_PREFIX + 'event.jsonl.gz'
)
GO_APPEAL_NODE_DATA_BUCKET_KEY = (
    GO_APPEAL_NODE_DATA_BUCKET_PREFIX + 'appeal.jsonl.gz'
)


# File parsing

def read_gzip(path: Path) -> Generator[Any, None, None]:
    """
    Generator to return `.gzip` file contents.
    """
    with gzip.open(path, 'rt', encoding='utf-8') as file:
        for line in file:
            yield json.loads(line)
