"""
Tests for the local raw Item cache and its DataFrame utilities.
"""

# Imports

from pathlib import Path
from unittest.mock import Mock

import pytest
from pystac_client import Client

from monty_tool import data_cache


# Test object helpers

class _FakeSearch:
    """
    Stands in for `pystac_client.ItemSearch`, serving pre-built pages.
    """

    def __init__(self, pages: list[dict], fail_after: int | None = None):
        self.pages = pages
        self.fail_after = fail_after

    def pages_as_dicts(self):
        for index, page in enumerate(self.pages):
            if self.fail_after is not None and index >= self.fail_after:
                raise ConnectionError('server dropped the connection')
            yield page


def _client(pages: list[dict], fail_after: int | None = None) -> Mock:
    """
    A `Client` mock whose every search serves the same pre-built pages.
    """
    client = Mock(spec=Client)
    client.search.side_effect = lambda **kwargs: _FakeSearch(pages, fail_after=fail_after)
    return client


def _page(*ids: str) -> dict:
    return {'features': [{'id': item_id, 'collection': 'c'} for item_id in ids]}


def _item(**overrides) -> dict:
    item = {
        'id': 'event-1',
        'collection': 'emdat-events',
        'bbox': [0, 0, 1, 1],
        'geometry': {'type': 'Point', 'coordinates': [0, 0]},
        'links': [{'rel': 'self'}, {'rel': 'collection'}],
        'assets': {},
        'properties': {
            'title': 'Flood',
            'monty:corr_id': 'corr-1',
            'monty:impact_detail': {'type': 'death', 'value': 3},
        },
    }
    item.update(overrides)
    return item


# Tests: cache paths

def test_raw_cache_path_marks_geometry_exclusion(tmp_path: Path):
    """
    Confirms that pulls with and without geometry cache to different files.
    """
    with_geometry = data_cache.raw_cache_path('emdat-events', tmp_path)
    without_geometry = data_cache.raw_cache_path('emdat-events', tmp_path, geometry=False)
    assert with_geometry == tmp_path / 'emdat-events.jsonl.gz'
    assert without_geometry == tmp_path / 'emdat-events.nogeom.jsonl.gz'


# Tests: pull and load

def test_pull_collection_round_trips_through_load(tmp_path: Path):
    """
    Confirms that every Item across all pages is written and read back in order.
    """
    client = _client([_page('a', 'b'), _page('c')])
    path = data_cache.pull_collection('c', cache_dir=tmp_path, client=client, page_size=2)

    assert path == data_cache.raw_cache_path('c', tmp_path)
    assert path.exists()
    assert not path.with_suffix('.partial').exists()
    assert [item['id'] for item in data_cache.load_collection('c', tmp_path)] == ['a', 'b', 'c']


def test_pull_collection_search_arguments(tmp_path: Path):
    """
    Confirms the page size is passed through and geometry is only excluded on request.
    """
    client = _client([_page('a')])
    data_cache.pull_collection('c', cache_dir=tmp_path, client=client, page_size=50)
    data_cache.pull_collection('c', cache_dir=tmp_path, client=client, geometry=False)

    with_geometry, without_geometry = client.search.call_args_list
    assert with_geometry.kwargs['collections'] == ['c']
    assert with_geometry.kwargs['limit'] == 50
    assert with_geometry.kwargs['fields'] is None
    assert without_geometry.kwargs['fields'] == data_cache.NO_GEOMETRY_FIELDS


def test_pull_collection_creates_cache_dir(tmp_path: Path):
    """
    Confirms a missing cache directory is created rather than raising.
    """
    cache_dir = tmp_path / 'nested' / 'raw'
    data_cache.pull_collection('c', cache_dir=cache_dir, client=_client([_page('a')]))
    assert data_cache.raw_cache_path('c', cache_dir).exists()


def test_pull_collection_failure_leaves_no_cache(tmp_path: Path):
    """
    Confirms a pull that dies mid-way never produces a loadable (partial) cache.
    """
    client = _client([_page('a'), _page('b')], fail_after=1)
    with pytest.raises(ConnectionError):
        data_cache.pull_collection('c', cache_dir=tmp_path, client=client)

    assert not data_cache.raw_cache_path('c', tmp_path).exists()
    with pytest.raises(FileNotFoundError):
        list(data_cache.load_collection('c', tmp_path))


def test_load_collection_missing_names_the_collection(tmp_path: Path):
    """
    Confirms the error for an uncached collection says what to pull.
    """
    with pytest.raises(FileNotFoundError, match="'emdat-events'.*geometry=False"):
        list(data_cache.load_collection('emdat-events', tmp_path, geometry=False))


# Tests: flattening

def test_flatten_item_promotes_and_dots_properties():
    """
    Confirms properties become top-level columns and nested dicts get dotted keys.
    """
    record = data_cache._flatten_item(_item())
    assert record['id'] == 'event-1'
    assert record['collection'] == 'emdat-events'
    assert record['geometry_type'] == 'Point'
    assert record['n_links'] == 2
    assert record['n_assets'] == 0
    assert record['title'] == 'Flood'
    assert record['monty:corr_id'] == 'corr-1'
    assert record['monty:impact_detail.type'] == 'death'
    assert record['monty:impact_detail.value'] == 3
    assert 'monty:impact_detail' not in record


def test_flatten_item_tolerates_missing_geometry_and_links():
    """
    Confirms Items pulled without geometry (or with no links/assets) still flatten.
    """
    record = data_cache._flatten_item(_item(geometry=None, links=None, assets=None))
    assert record['geometry_type'] is None
    assert record['n_links'] == 0
    assert record['n_assets'] == 0


def test_items_to_frame_unions_columns_with_nulls():
    """
    Confirms the frame has one row per Item and nulls where an Item lacks a key.
    """
    frame = data_cache.items_to_frame([
        _item(id='a'),
        _item(id='b', properties={'title': 'Storm', 'keywords': ['x']}),
    ])
    assert frame.height == 2
    assert frame['id'].to_list() == ['a', 'b']
    assert frame['keywords'].null_count() == 1
    assert frame['monty:impact_detail.value'].null_count() == 1
    assert frame['title'].null_count() == 0
