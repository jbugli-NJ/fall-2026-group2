"""
Tests for the Montandon API utility module.
"""

# Imports

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

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


class _FakeClient:
    """
    Stands in for `pystac_client.Client`, recording every search call.
    """

    def __init__(self, pages: list[dict], collection_ids: list[str] = ()):
        self.pages = pages
        self.collection_ids = collection_ids
        self.calls: list[dict] = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeSearch(self.pages)

    def get_collections(self):
        return [SimpleNamespace(id=collection_id) for collection_id in self.collection_ids]


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
    monkeypatch.setattr(api_utils, 'get_pystac_client', lambda: _FakeClient([{'features': [item]}]))

    items = api_utils.get_collection_items_raw('events', max_items=5)
    assert items == [item]
    assert items[0]['properties']['monty:etl_id'] == 'etl-1'


def test_get_collection_items_raw_validate_passes_valid_items(monkeypatch: pytest.MonkeyPatch):
    """
    Confirms `validate=True` is a no-op on schema-conformant Items.
    """
    item = _valid_item()
    monkeypatch.setattr(api_utils, 'get_pystac_client', lambda: _FakeClient([{'features': [item]}]))
    assert api_utils.get_collection_items_raw('events', validate=True) == [item]


def test_get_collection_items_raw_validate_names_failing_item(monkeypatch: pytest.MonkeyPatch):
    """
    Confirms `validate=True` raises with the offending Item ID and the Pydantic cause.
    """
    item = _valid_item(id='bad-item', properties={})
    monkeypatch.setattr(api_utils, 'get_pystac_client', lambda: _FakeClient([{'features': [item]}]))

    with pytest.raises(ValueError, match="'bad-item'.*'events'") as info:
        api_utils.get_collection_items_raw('events', validate=True)
    assert isinstance(info.value.__cause__, ValidationError)


def test_count_collection_items_sums_pages():
    """
    Confirms the count sums `numberReturned` across pages, falling back to the feature count.
    """
    client = _FakeClient([
        {'numberReturned': 2, 'features': [{'id': 'a'}, {'id': 'b'}]},
        {'features': [{'id': 'c'}]},  # no numberReturned: fall back to len(features)
    ])
    assert api_utils.count_collection_items('c', client=client, page_size=2) == 3


def test_count_collection_items_requests_ids_only():
    """
    Confirms counting pages with only IDs requested, at the given page size.
    """
    client = _FakeClient([{'numberReturned': 0, 'features': []}])
    api_utils.count_collection_items('c', client=client, page_size=250)

    (call,) = client.calls
    assert call['collections'] == ['c']
    assert call['limit'] == 250
    assert call['fields'] == api_utils.COUNT_ONLY_FIELDS


def test_get_collection_counts_keys_by_collection(monkeypatch: pytest.MonkeyPatch):
    """
    Confirms every collection the API lists is counted and keyed by its ID.
    """
    client = _FakeClient([{'numberReturned': 4, 'features': []}], collection_ids=['x', 'y'])
    monkeypatch.setattr(api_utils, 'get_pystac_client', lambda: client)

    assert api_utils.get_collection_counts() == {'x': 4, 'y': 4}
    assert [call['collections'] for call in client.calls] == [['x'], ['y']]
