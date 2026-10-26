"""
Tests for the shared footprint geometry helpers.
"""

import pytest

from monty_tool.geo.schemas import REGIONAL_KM, extent_km, grade_detail


def test_extent_km_handles_empty_and_points():
    """
    Confirms extent is None without coordinates and zero for a point.
    """
    assert extent_km({'type': 'FeatureCollection', 'features': []}) is None
    assert extent_km({'type': 'Point', 'coordinates': [10, 10]}) == 0


def test_extent_km_uses_longer_side_at_mid_latitude():
    """
    Confirms longitude spans shrink with latitude and the longer side wins.
    """
    line = {'type': 'LineString', 'coordinates': [[0, 60], [2, 60]]}

    assert extent_km(line) == pytest.approx(2 * 111.32 * 0.5, abs=0.1)


def test_extent_km_walks_nested_containers():
    """
    Confirms features, multi-geometries and geometry collections all count.
    """
    collection = {
        'type': 'FeatureCollection',
        'features': [
            {'type': 'Feature', 'geometry': None, 'properties': {}},
            {'type': 'Feature', 'properties': {}, 'geometry': {
                'type': 'GeometryCollection',
                'geometries': [
                    {'type': 'Point', 'coordinates': [0, 0]},
                    {'type': 'MultiPolygon', 'coordinates': [[[[0, 0], [0, 1], [0, 0]]]]},
                ],
            }},
        ],
    }

    assert extent_km(collection) == pytest.approx(110.57, abs=0.1)


def test_grade_detail_marks_wide_outlines_regional():
    """
    Confirms outlines wider than REGIONAL_KM become regional, and that
    graded footprints and tracks keep their grade at any size.
    """
    assert grade_detail('outline', REGIONAL_KM + 1) == 'regional'
    assert grade_detail('outline', REGIONAL_KM) == 'outline'
    assert grade_detail('outline', None) == 'outline'
    assert grade_detail('modelled', REGIONAL_KM * 10) == 'modelled'
    assert grade_detail('track', REGIONAL_KM * 10) == 'track'
