"""
Tests for evidence snapshots and answer archives.
"""

# Imports

from copy import deepcopy
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError
from neo4j.time import DateTime

from monty_tool.gap_analysis import storage


# Fixtures

@pytest.fixture
def artifacts(monkeypatch):
    """
    Store uploaded bytes so reads exercise the same archive format.
    """
    objects = {}
    def upload(bucket, path, key):
        objects[key] = path.read_bytes()
    def download(bucket, key, output_path):
        output_path.write_bytes(objects[key])
    monkeypatch.setattr(storage, 'get_bucket', Mock())
    monkeypatch.setattr(storage, 'get_env_bucket_name', lambda: 'test-bucket')
    monkeypatch.setattr(storage, 'get_env_bucket_prefix', lambda: 'owner/')
    monkeypatch.setattr(storage, 'upload_object', upload)
    monkeypatch.setattr(storage, 'download_object', download)
    return objects


# Tests

def test_evidence_round_trip_preserves_snapshot_and_uses_content_hash(artifacts):
    """
    Saved records remain unchanged when the in-memory retrieval result changes.
    """
    started = DateTime(2020, 1, 1, 12, 0, 0, nanosecond=123456789)
    evidence = {
        'events': [{
            'event_id': 'évent',
            'impacts': [],
            'start_datetime': started,
            'weather': {'mean_temperature': 20},
        }],
        'responses': [],
        'news': [{'evidence_excerpt': 'Homes were damaged.'}],
    }
    uri = storage.save_evidence(evidence)
    assert uri.startswith('s3://test-bucket/owner/gap_analysis/evidence/')
    assert uri.endswith('.json')
    assert storage.save_evidence(dict(reversed(list(evidence.items())))) == uri
    assert evidence['events'][0]['start_datetime'] is started
    original = deepcopy(evidence)
    original['events'][0]['start_datetime'] = str(started)
    evidence['events'][0]['weather']['mean_temperature'] = 99
    assert storage.load_evidence(uri) == original
    assert storage.save_evidence(evidence) != uri
    assert len(artifacts) == 2


def test_load_answer_reads_plain_utf8_text(artifacts):
    """
    Answers are read as plain text rather than assistant response envelopes.
    """
    artifacts['answer.txt'] = 'An answer with é.\nSecond line.'.encode('utf-8')
    assert storage.load_answer(
        's3://test-bucket/answer.txt',
    ) == 'An answer with é.\nSecond line.'


def test_missing_snapshot_is_not_replaced_with_a_graph_query(monkeypatch):
    """
    Archive failures propagate rather than changing the evidence used for evaluation.
    """
    monkeypatch.setattr(storage, 'get_bucket', Mock())
    missing = ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
    monkeypatch.setattr(storage, 'download_object', Mock(side_effect=missing))
    with pytest.raises(ClientError):
        storage.load_evidence('s3://test-bucket/missing.json')


@pytest.mark.parametrize('uri', ['https://example.com/evidence.json', 's3://bucket', ''])
def test_invalid_archive_uri_is_rejected(uri):
    """
    Archive reads require a bucket and object key.
    """
    with pytest.raises(ValueError, match='Invalid S3 URI'):
        storage.load_evidence(uri)
