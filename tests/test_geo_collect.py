"""
Tests for dispatching raw Montandon Items to the footprint adapters.
"""

from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
import requests

from monty_tool.geo import cems, collect, gdacs, usgs
from monty_tool.geo.schemas import Footprint


def _footprint(**changes) -> Footprint:
    data = {
        'source': 'USGS', 'item_id': 'usgs-event-us1', 'event_ref': 'us1',
        'layer': 'shaking_intensity', 'label': 'Shaking intensity', 'detail': 'modelled',
        'datetime': datetime(2025, 8, 31, tzinfo=UTC),
        'data_url': 'https://example.invalid/grid.covjson', 'severity_property': 'mmi',
    }
    return Footprint.model_validate(data | changes)


def _features(*values):
    return {'type': 'FeatureCollection', 'features': [
        {'type': 'Feature', 'geometry': None, 'properties': {'mmi': value}} for value in values
    ]}


def test_severity_levels_are_distinct_sorted_strings():
    footprint = _footprint(geojson=_features(7, 5, 7, None, 6))
    assert collect.severity_levels(footprint) == ['5', '6', '7']


@pytest.mark.parametrize('changes', [{'geojson': None}, {'severity_property': None}])
def test_severity_levels_need_outline_and_property(changes):
    footprint = _footprint(**({'geojson': _features(7)} | changes))
    assert collect.severity_levels(footprint) == []


def test_every_source_has_an_origin():
    """
    ShakeMap bands come from USGS; CEMS and GDACS outlines from Montandon.
    """
    assert collect.ORIGINS == {'CEMS': 'montandon', 'GDACS': 'montandon', 'USGS': 'external'}


def test_unknown_collection_yields_nothing():
    assert collect.footprints_from_raw_item({'collection': 'emdat-events', 'properties': {}}) == []


@pytest.mark.parametrize('magnitude', [None, 5.4])
def test_small_quakes_skip_shakemap_fetch(monkeypatch, magnitude):
    fetch = Mock()
    monkeypatch.setattr(usgs, 'footprints_from_item', fetch)
    item = {'collection': 'usgs-events', 'properties': {'eq:magnitude': magnitude}}
    assert collect.footprints_from_raw_item(item) == []
    fetch.assert_not_called()


def test_damaging_quakes_fetch_shakemap(monkeypatch):
    footprint = _footprint()
    monkeypatch.setattr(usgs, 'footprints_from_item', Mock(return_value=[footprint]))
    item = {'collection': 'usgs-events', 'properties': {'eq:magnitude': 6.0}}
    assert collect.footprints_from_raw_item(item) == [footprint]


def test_gdacs_point_hazard_yields_nothing(monkeypatch):
    monkeypatch.setattr(gdacs, 'footprint_from_item', Mock(return_value=None))
    assert collect.footprints_from_raw_item({'collection': gdacs.COLLECTION}) == []


def test_failed_cems_layer_is_kept_without_outline(monkeypatch):
    """
    One unreachable layer keeps its record rather than dropping the activation.
    """
    footprint = _footprint(source='CEMS', layer='observed_flood_extent', detail='mapped')
    loaded = footprint.model_copy(update={'geojson': _features(1)})
    monkeypatch.setattr(cems, 'footprints_from_item', Mock(return_value=[footprint, footprint]))
    monkeypatch.setattr(cems, 'load_geojson', Mock(side_effect=[requests.ConnectionError('down'), loaded]))
    result = collect.footprints_from_raw_item({'collection': cems.COLLECTION})
    assert result == [footprint, loaded]
