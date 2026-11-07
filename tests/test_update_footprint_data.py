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
