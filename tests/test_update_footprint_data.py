"""
Tests for building footprint node data from raw Montandon files.
"""

from datetime import UTC, datetime
import gzip
import json
from types import SimpleNamespace
from unittest.mock import Mock

import requests

from monty_tool.geo.schemas import Footprint
from monty_tool.tools import update_footprint_data
from monty_tool.tools.resources import RAW_BUCKET_PREFIX


def _bucket(*names):
    bucket = Mock()
    bucket.objects.filter.return_value = [
        SimpleNamespace(key=RAW_BUCKET_PREFIX + name) for name in names
    ]
    return bucket


def test_raw_keys_prefer_geometry_and_reject_gdacs_without_it():
    keys = update_footprint_data.raw_footprint_keys(_bucket(
        'cems-response.nogeom.jsonl.gz',
        'gdacs-hazards.nogeom.jsonl.gz',
        'usgs-events.jsonl.gz', 'usgs-events.nogeom.jsonl.gz',
        'emdat-events.jsonl.gz',
    ))
    assert keys == {
        'cems-response': RAW_BUCKET_PREFIX + 'cems-response.nogeom.jsonl.gz',
        'usgs-events': RAW_BUCKET_PREFIX + 'usgs-events.jsonl.gz',
    }


def test_node_data_skips_uncorrelated_and_failed_items(monkeypatch, tmp_path):
    footprint = Footprint(
        source='USGS', item_id='usgs-event-us2', event_ref='us2', layer='shaking_intensity',
        label='Shaking intensity', detail='modelled', datetime=datetime(2025, 8, 31, tzinfo=UTC),
        data_url='https://example.invalid/grid.covjson',
    )
    fetch = Mock(side_effect=[requests.ConnectionError('down'), [footprint]])
    monkeypatch.setattr(update_footprint_data, 'footprints_from_raw_item', fetch)
    path = tmp_path / 'usgs-events.jsonl.gz'
    with gzip.open(path, 'wt', encoding='utf-8') as file:
        for item in (
            {'id': 'usgs-event-us0', 'properties': {}},
            {'id': 'usgs-event-us1', 'properties': {'monty:corr_id': 'c1'}},
            {'id': 'usgs-event-us2', 'properties': {'monty:corr_id': 'c2'}},
        ):
            file.write(json.dumps(item) + '\n')

    nodes = list(update_footprint_data.footprint_node_data(path))

    assert [(node['item_id'], node['corr_id']) for node in nodes] == [('usgs-event-us2', 'c2')]
    assert fetch.call_count == 2


def test_node_data_links_matched_events(monkeypatch, tmp_path):
    """
    Verify matched event IDs replace correlation-ID linking, with no match giving none.
    """
    def footprint(item_id):
        return Footprint(
            source='USGS', item_id=item_id, event_ref=item_id, layer='shaking_intensity',
            label='Shaking intensity', detail='modelled', datetime=datetime(2025, 8, 31, tzinfo=UTC),
            data_url='https://example.invalid/grid.covjson',
        )
    monkeypatch.setattr(
        update_footprint_data, 'footprints_from_raw_item',
        lambda item: [footprint(item['id'])],
    )
    path = tmp_path / 'usgs-events.jsonl.gz'
    with gzip.open(path, 'wt', encoding='utf-8') as file:
        for item_id in ('usgs-event-us1', 'usgs-event-us2'):
            file.write(json.dumps({'id': item_id, 'properties': {'monty:corr_id': 'c'}}) + '\n')

    nodes = list(update_footprint_data.footprint_node_data(
        path, {'usgs-event-us1': ['emdat-event-2025-0001-AFG']},
    ))

    assert [node.get('event_ids') for node in nodes] == [['emdat-event-2025-0001-AFG'], []]


def test_shakemap_links_use_gdacs_file_and_montandon_emdat(monkeypatch, tmp_path):
    """
    Verify ShakeMap matching reads the raw USGS and GDACS files and fetches EM-DAT quakes.
    """
    def write(name, items):
        path = tmp_path / name
        with gzip.open(path, 'wt', encoding='utf-8') as file:
            for item in items:
                file.write(json.dumps(item) + '\n')
        return path
    usgs = write('usgs-events.jsonl.gz', [{
        'id': 'usgs-event-us1', 'bbox': [13.19, 42.70, 13.19, 42.70],
        'properties': {'datetime': '2016-08-24T01:36:32Z', 'eq:magnitude': 6.2},
    }])
    gdacs = write('gdacs-hazards.jsonl.gz', [{
        'id': 'gdacs-hazard-1-2', 'bbox': [13.2, 42.7, 13.2, 42.7],
        'properties': {'datetime': '2016-08-24T01:37:00Z', 'monty:hazard_codes': ['GH0101']},
    }])
    emdat = {
        'id': 'emdat-event-2016-0313-ITA', 'geometry': {'type': 'Point', 'coordinates': [13.3, 42.6]},
        'properties': {'datetime': '2016-08-24T00:00:00Z', 'monty:hazard_codes': ['nat-geo-ear-gro']},
    }
    monkeypatch.setattr(update_footprint_data, 'fetch_emdat_quakes', lambda: iter([emdat]))

    assert update_footprint_data.shakemap_event_links(usgs, gdacs) == {
        'usgs-event-us1': ['emdat-event-2016-0313-ITA', 'gdacs-event-1-2'],
    }
    assert update_footprint_data.shakemap_event_links(usgs, None) == {
        'usgs-event-us1': ['emdat-event-2016-0313-ITA'],
    }
