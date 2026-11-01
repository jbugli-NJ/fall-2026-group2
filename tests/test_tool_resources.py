"""
Tests for shared tool resources.
"""

# Imports

import gzip
import importlib.util
import json
import logging

import dotenv
import pytest

from monty_tool.tools import resources
from monty_tool.tools.resources import get_env_bucket_name, get_env_bucket_prefix, read_gzip


# Tests

def test_get_env_bucket_name_reads_aws_bucket(monkeypatch):
    """
    Checks that the configured bucket name is returned.
    """
    monkeypatch.setenv('AWS_BUCKET', 'test-bucket')
    assert get_env_bucket_name() == 'test-bucket'


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        ('my.name@example.com', 'my.name@example.com/'),
        ('my.name@example.com/', 'my.name@example.com/'),
        ('  /team/weather-test///  ', 'team/weather-test/'),
    ],
)
def test_get_env_bucket_prefix_normalizes_folder(monkeypatch, caplog, value, expected):
    """
    Normalize configured folders while preserving nested paths.
    """
    monkeypatch.setenv('AWS_BUCKET_PREFIX', value)
    with caplog.at_level(logging.WARNING):
        assert get_env_bucket_prefix() == expected
    assert not caplog.records


@pytest.mark.parametrize('value', [None, '', '   ', ' /// '])
def test_get_env_bucket_prefix_warns_on_default(monkeypatch, caplog, value):
    """
    Warn when an unset or empty folder falls back to the default.
    """
    if value is None:
        monkeypatch.delenv('AWS_BUCKET_PREFIX', raising=False)
    else:
        monkeypatch.setenv('AWS_BUCKET_PREFIX', value)
    with caplog.at_level(logging.WARNING):
        assert get_env_bucket_prefix() == 'aidan.carlisle@gwu.edu/'
    assert 'AWS_BUCKET_PREFIX is unset or blank' in caplog.text
    assert 'Defaulting to aidan.carlisle@gwu.edu/' in caplog.text


def test_bucket_paths_use_configured_prefix(monkeypatch, tmp_path):
    """
    Use the direct environment prefix for all paths, overriding dotenv.
    """
    monkeypatch.setenv('AWS_BUCKET_PREFIX', 'team/environment-test')
    env_path = tmp_path / '.env'
    env_path.write_text('AWS_BUCKET_PREFIX=team/dotenv-test\n')
    load_dotenv = dotenv.load_dotenv
    monkeypatch.setattr(dotenv, 'load_dotenv', lambda: load_dotenv(env_path))
    spec = importlib.util.spec_from_file_location('isolated_resources', resources.__file__)
    assert spec is not None and spec.loader is not None
    configured = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(configured)

    expected_suffixes = {
        'BUCKET_DATA_PREFIX': '',
        'RAW_BUCKET_PREFIX': 'raw/',
        'GO_BUCKET_PREFIX': 'go/',
        'NASA_POWER_BUCKET_PREFIX': 'nasa_power/',
        'NASA_POWER_BUCKET_KEY': 'nasa_power/weather.jsonl.gz',
        'MONTANDON_NODE_DATA_BUCKET_PREFIX': 'node_data/montandon/',
        'GO_EVENT_NODE_DATA_BUCKET_PREFIX': 'node_data/go_event/',
        'GO_APPEAL_NODE_DATA_BUCKET_PREFIX': 'node_data/go_appeal/',
        'GO_EVENT_BUCKET_KEY': 'go/event.jsonl.gz',
        'GO_APPEAL_BUCKET_KEY': 'go/appeal.jsonl.gz',
        'GO_EVENT_NODE_DATA_BUCKET_KEY': 'node_data/go_event/event.jsonl.gz',
        'GO_APPEAL_NODE_DATA_BUCKET_KEY': 'node_data/go_appeal/appeal.jsonl.gz',
    }
    for name, suffix in expected_suffixes.items():
        assert getattr(configured, name) == 'team/environment-test/' + suffix


def test_bucket_paths_load_dotenv_before_initialization(monkeypatch, tmp_path):
    """
    Load the dotenv prefix before initializing bucket paths.
    """
    monkeypatch.delenv('AWS_BUCKET_PREFIX', raising=False)
    env_path = tmp_path / '.env'
    env_path.write_text('AWS_BUCKET_PREFIX=team/dotenv-test\n')
    load_dotenv = dotenv.load_dotenv
    monkeypatch.setattr(dotenv, 'load_dotenv', lambda: load_dotenv(env_path))
    spec = importlib.util.spec_from_file_location('isolated_resources', resources.__file__)
    assert spec is not None and spec.loader is not None
    configured = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(configured)

    assert configured.BUCKET_DATA_PREFIX == 'team/dotenv-test/'
    assert configured.RAW_BUCKET_PREFIX == 'team/dotenv-test/raw/'


def test_read_gzip_reads_jsonl_records(tmp_path):
    """
    Tests reading JSONL records from a gzip file.
    """
    path = tmp_path.joinpath('records.jsonl.gz')
    with gzip.open(path, 'wt', encoding='utf-8') as file:
        file.write(json.dumps({'id': 'one'}) + '\n')
        file.write(json.dumps({'id': 'two'}) + '\n')

    assert list(read_gzip(path)) == [{'id': 'one'}, {'id': 'two'}]
