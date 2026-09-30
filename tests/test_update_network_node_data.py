"""
Tests for the node data update tool.
"""

# Imports

from types import SimpleNamespace
from typing import cast

from monty_tool.boto3_utils.s3_protocols import S3Bucket
from monty_tool.tools.resources import RAW_BUCKET_PREFIX
from monty_tool.tools.update_network_node_data import _raw_gzip_keys


# Test helpers

class _BucketObjects:
    def __init__(self, keys: list[str]):
        self.keys = keys
        self.prefix: str | None = None

    def filter(self, *, Prefix: str):
        self.prefix = Prefix
        return [
            SimpleNamespace(key=key)
            for key in self.keys
            if key.startswith(Prefix)
        ]


class _Bucket:
    def __init__(self, keys: list[str]):
        self.objects = _BucketObjects(keys)


# Tests

def test_raw_gzip_keys_lists_gzip_files_under_the_raw_prefix():
    """
    Checks that updating finds sorted raw gzip keys.
    """
    bucket = _Bucket([
        'raw/old-file.jsonl.gz',
        RAW_BUCKET_PREFIX + 'z.jsonl.gz',
        RAW_BUCKET_PREFIX + 'a.jsonl.gz',
        RAW_BUCKET_PREFIX + 'notes.txt',
    ])

    keys = _raw_gzip_keys(cast(S3Bucket, bucket))

    assert bucket.objects.prefix == RAW_BUCKET_PREFIX
    assert keys == [
        RAW_BUCKET_PREFIX + 'a.jsonl.gz',
        RAW_BUCKET_PREFIX + 'z.jsonl.gz',
    ]
