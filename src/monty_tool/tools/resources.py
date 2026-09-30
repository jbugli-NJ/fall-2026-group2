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
logging.basicConfig(level=logging.DEBUG)


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


# File parsing

def read_gzip(path: Path) -> Generator[Any, None, None]:
    """
    Generator to return `.gzip` file contents.
    """
    with gzip.open(path, 'rt', encoding='utf-8') as file:
        for line in file:
            yield json.loads(line)
