"""
Tests for shared tool resources.
"""

# Imports

import gzip
import json

from monty_tool.tools.resources import get_env_bucket_name, read_gzip


# Tests

def test_get_env_bucket_name_reads_aws_bucket(monkeypatch):
    """
    Checks that the configured bucket name is returned.
    """
    monkeypatch.setenv('AWS_BUCKET', 'test-bucket')
    assert get_env_bucket_name() == 'test-bucket'


def test_read_gzip_reads_jsonl_records(tmp_path):
    """
    Tests reading JSONL records from a gzip file.
    """
    path = tmp_path.joinpath('records.jsonl.gz')
    with gzip.open(path, 'wt', encoding='utf-8') as file:
        file.write(json.dumps({'id': 'one'}) + '\n')
        file.write(json.dumps({'id': 'two'}) + '\n')

    assert list(read_gzip(path)) == [{'id': 'one'}, {'id': 'two'}]
