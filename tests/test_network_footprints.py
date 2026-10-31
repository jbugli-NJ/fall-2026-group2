"""
Tests for footprint summaries linked to existing graph events.
"""

from datetime import UTC, datetime
import gzip
import json
from unittest.mock import MagicMock

import pytest

from monty_tool.geo.schemas import Footprint
from monty_tool.network.footprints import (
    footprint_to_node_data,
    insert_footprints,
    validated_footprint_node_data,
)


def _footprint(**changes) -> Footprint:
    data = {
        'source': 'CEMS', 'item_id': 'cems-response-EMSR847-aoi01-del',
        'event_ref': 'EMSR847', 'area_name': 'Montego Bay',
        'layer': 'observed_flood_extent', 'label': 'Observed flood extent', 'detail': 'mapped',
        'datetime': datetime(2025, 10, 30, tzinfo=UTC),
        'data_url': 'https://example.invalid/layer.json', 'severity_property': 'grade',
        'feature_count': 2, 'size_km': 12.5,
        'geojson': {'type': 'FeatureCollection', 'features': [
            {'type': 'Feature', 'geometry': None, 'properties': {'grade': 'Destroyed'}},
            {'type': 'Feature', 'geometry': None, 'properties': {'grade': 'Damaged'}},
        ]},
    }
    return Footprint.model_validate(data | changes)


def test_node_data_summarises_without_outline():
    node = footprint_to_node_data(_footprint(), corr_id='20251028-JAM-MH0309-1-GCDB')
    assert node['id'] == 'cems-response-EMSR847-aoi01-del#observed_flood_extent'
    assert node['corr_id'] == '20251028-JAM-MH0309-1-GCDB'
    assert node['origin'] == 'montandon'
    assert node['severity_levels'] == ['Damaged', 'Destroyed']
    assert 'geojson' not in node


def test_shakemap_node_data_is_external():
    footprint = _footprint(source='USGS', item_id='usgs-event-us1', layer='shaking_intensity')
    assert footprint_to_node_data(footprint, corr_id='c')['origin'] == 'external'


def test_validated_rows_round_trip(tmp_path):
    node = footprint_to_node_data(_footprint(), corr_id='c')
    path = tmp_path / 'footprint.jsonl.gz'
    with gzip.open(path, 'wt', encoding='utf-8') as file:
        file.write(json.dumps(node, default=str) + '\n')
    assert list(validated_footprint_node_data(path)) == [node]


def test_invalid_row_names_its_line(tmp_path):
    path = tmp_path / 'footprint.jsonl.gz'
    with gzip.open(path, 'wt', encoding='utf-8') as file:
        file.write(json.dumps({'id': 'x'}) + '\n')
    with pytest.raises(ValueError, match='line 1'):
        list(validated_footprint_node_data(path))


def test_insert_reports_linked_and_unmatched_counts():
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    tx = MagicMock()
    tx.run.return_value.single.return_value = {'linked': 3, 'unmatched': 1}
    session.execute_write.side_effect = lambda work: work(tx)

    nodes = [footprint_to_node_data(_footprint(), corr_id='c')]
    assert insert_footprints(driver, nodes) == (3, 1)
    assert tx.run.call_args.kwargs['items'] == nodes
