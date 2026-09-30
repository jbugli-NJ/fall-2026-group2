"""
Tests for the network setup tool.
"""

# Imports

from types import SimpleNamespace
from typing import cast

from monty_tool.boto3_utils.s3_protocols import S3Bucket
from monty_tool.tools.resources import MONTANDON_NODE_DATA_BUCKET_PREFIX
from monty_tool.tools.setup_network import _montandon_node_data_keys


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

def test_montandon_node_data_keys_lists_gzip_files_only():
    """
    Checks that only `.gzip` files are flagged for inserts.
    """
    bucket = _Bucket([
        'node_data/montandon/old-file.jsonl.gz',
        MONTANDON_NODE_DATA_BUCKET_PREFIX + 'z.jsonl.gz',
        MONTANDON_NODE_DATA_BUCKET_PREFIX + 'a.jsonl.gz',
        MONTANDON_NODE_DATA_BUCKET_PREFIX + 'notes.txt',
    ])

    keys = _montandon_node_data_keys(cast(S3Bucket, bucket))

    assert bucket.objects.prefix == MONTANDON_NODE_DATA_BUCKET_PREFIX
    assert keys == [
        MONTANDON_NODE_DATA_BUCKET_PREFIX + 'a.jsonl.gz',
        MONTANDON_NODE_DATA_BUCKET_PREFIX + 'z.jsonl.gz',
    ]
