"""
Tests for the Montandon API utility module.
"""

# Imports

from collections.abc import Sequence
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from pystac_client import Client

from monty_tool import api_utils


# Test object helpers

class _FakeSearch:
    """
    Stands in for `pystac_client.ItemSearch`, serving pre-built pages.
    """

    def __init__(self, pages: list[dict]):
        self.pages = pages

    def pages_as_dicts(self):
        yield from self.pages

    def items_as_dicts(self):
        for page in self.pages:
            yield from page['features']


def _client(pages: list[dict], collection_ids: Sequence[str] = ()) -> Mock:
    """
    A `Client` mock whose every search serves the same pre-built pages.
    """
    client = Mock(spec=Client)
    client.search.return_value = _FakeSearch(pages)
    client.get_collections.return_value = [
        SimpleNamespace(id=collection_id) for collection_id in collection_ids
    ]
    return client


def _valid_item(**overrides) -> dict:
    item = {
        'id': 'event-1',
        'collection': 'events',
        'bbox': [0, 0, 1, 1],
        'geometry': {'type': 'Point', 'coordinates': [0, 0]},
        'links': [],
        'properties': {
            'roles': ['event'],
            'title': 'Flood',
            'description': 'River flooding.',
            'keywords': ['river', 'flood'],
            'datetime': '2026-09-01T00:00:00Z',
            'start_datetime': '2026-09-01T00:00:00Z',
            'end_datetime': '2026-09-02T00:00:00Z',
            'monty:corr_id': 'correlation-1',
            'monty:hazard_codes': ['FL'],
            'monty:country_codes': ['USA'],
            'monty:src_event_id': 'source-1',
            'monty:episode_number': 1,
        },
    }
    item.update(overrides)
    return item


# Tests

def test_get_headers_uses_env(monkeypatch: pytest.MonkeyPatch):
    """
    Confirms that headers use the API token in the environment.
    """
    monkeypatch.setenv('MONTANDON_API_TOKEN', 'test-token')
    headers = api_utils._get_headers()
    assert 'test-token' in headers['Authorization']


def test_get_collection_items_raw_keeps_unknown_fields(monkeypatch: pytest.MonkeyPatch):
    """
    Confirms raw retrieval returns Items untouched, including keys the schema ignores.
    """
    item = _valid_item()
    item['properties']['monty:etl_id'] = 'etl-1'
    monkeypatch.setattr(api_utils, 'get_pystac_client', lambda: _client([{'features': [item]}]))

    items = api_utils.get_collection_items_raw('events', max_items=5)
    assert items == [item]
    assert items[0]['properties']['monty:etl_id'] == 'etl-1'


def test_get_collection_items_raw_validate_passes_valid_items(monkeypatch: pytest.MonkeyPatch):
    """
    Confirms `validate=True` is a no-op on schema-conformant Items.
    """
    item = _valid_item()
    monkeypatch.setattr(api_utils, 'get_pystac_client', lambda: _client([{'features': [item]}]))
    assert api_utils.get_collection_items_raw('events', validate=True) == [item]


def test_get_collection_items_raw_validate_names_failing_item(monkeypatch: pytest.MonkeyPatch):
    """
    Confirms `validate=True` raises with the offending Item ID and the Pydantic cause.
    """
    item = _valid_item(id='bad-item', properties={})
    monkeypatch.setattr(api_utils, 'get_pystac_client', lambda: _client([{'features': [item]}]))

    with pytest.raises(ValueError, match="'bad-item'.*'events'") as info:
        api_utils.get_collection_items_raw('events', validate=True)
    assert isinstance(info.value.__cause__, ValidationError)


def test_count_collection_items_sums_pages():
    """
    Confirms the count sums `numberReturned` across pages, falling back to the feature count.
    """
    client = _client([
        {'numberReturned': 2, 'features': [{'id': 'a'}, {'id': 'b'}]},
        {'features': [{'id': 'c'}]},  # no numberReturned: fall back to len(features)
    ])
    assert api_utils.count_collection_items('c', client=client, page_size=2) == 3


def test_count_collection_items_requests_ids_only():
    """
    Confirms counting pages with only IDs requested, at the given page size.
    """
    client = _client([{'numberReturned': 0, 'features': []}])
    api_utils.count_collection_items('c', client=client, page_size=250)

    client.search.assert_called_once_with(
        collections=['c'],
        limit=250,
        fields=api_utils.COUNT_ONLY_FIELDS,
    )


def test_get_collection_counts_keys_by_collection(monkeypatch: pytest.MonkeyPatch):
    """
    Confirms every collection the API lists is counted and keyed by its ID.
    """
    client = _client([{'numberReturned': 4, 'features': []}], collection_ids=['x', 'y'])
    monkeypatch.setattr(api_utils, 'get_pystac_client', lambda: client)

    assert api_utils.get_collection_counts() == {'x': 4, 'y': 4}
    assert [call.kwargs['collections'] for call in client.search.call_args_list] == [['x'], ['y']]
