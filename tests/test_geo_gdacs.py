"""
Tests for GDACS footprints.
"""

from unittest.mock import Mock

import pytest

from monty_tool.geo import gdacs


def _polygon(size_degrees):
    return {
        'type': 'Polygon',
        'coordinates': [[
            [0, 0], [size_degrees, 0], [size_degrees, size_degrees],
            [0, size_degrees], [0, 0],
        ]],
    }


def _item(episode=1, hazard_codes=('EN0205',), geometry=None, datetime='2026-10-22T00:00:00Z'):
    return {
        'id': f'gdacs-hazard-1033087-{episode}',
        'geometry': geometry if geometry is not None else _polygon(0.1),
        'properties': {
            'title': 'Forest fires in Russia',
            'datetime': datetime,
            'start_datetime': '2026-10-22T00:00:00Z',
            'end_datetime': '2026-10-23T00:00:00Z',
            'monty:hazard_codes': list(hazard_codes),
            'monty:country_codes': ['RUS'],
            'monty:src_event_id': '1033087',
            'monty:episode_number': episode,
            'monty:hazard_detail': {'severity_label': 'Green'},
            'severitydata': {'severitytext': 'Green impact for forestfire in 5332 ha'},
        },
        'assets': {
            'report': {'href': 'https://www.gdacs.org/report.aspx?eventid=1033087'},
        },
        'links': [
            {'rel': 'via', 'href': 'https://www.gdacs.org/gdacsapi/api/events/getepisodedata'},
        ],
    }


@pytest.mark.parametrize('event_id', [
    '1033087', 'gdacs-hazard-1033087-1', 'gdacs-event-1033087-12',
])
def test_gdacs_event_id_strips_montandon_wrapping(event_id):
    """
    Confirms Montandon event and hazard IDs map to the GDACS event ID.
    """
    assert gdacs.gdacs_event_id(event_id) == '1033087'


def test_fire_polygon_becomes_outline_footprint():
    """
    Confirms a wildfire polygon keeps its geometry, alert and severity.
    """
    footprint = gdacs.footprint_from_item(_item())

    assert footprint is not None
    assert footprint.layer == 'burnt_area'
    assert footprint.label == 'Burnt area (episode 1)'
    assert footprint.detail == 'outline'
    assert footprint.event_ref == '1033087'
    assert footprint.end_datetime is not None
    assert footprint.source_url == 'https://www.gdacs.org/report.aspx?eventid=1033087'
    assert footprint.geojson is not None
    [feature] = footprint.geojson['features']
    assert feature['geometry'] == _polygon(0.1)
    assert feature['properties']['alert'] == 'Green'
    assert feature['properties']['severity'] == 'Green impact for forestfire in 5332 ha'


def test_wide_polygon_is_graded_regional():
    """
    Confirms a drought-sized polygon is graded regional.
    """
    footprint = gdacs.footprint_from_item(
        _item(hazard_codes=('MH0401',), geometry=_polygon(8)),
    )

    assert footprint is not None
    assert footprint.layer == 'drought_area'
    assert footprint.detail == 'regional'


def test_unlisted_hazard_is_labelled_from_crosswalk():
    """
    Confirms a polygon for a hazard without its own layer gets the
    crosswalk label.
    """
    footprint = gdacs.footprint_from_item(_item(hazard_codes=('GH0101',)))

    assert footprint is not None
    assert footprint.layer == 'hazard_area'
    assert footprint.label == 'Earthquake area (episode 1)'


def test_point_geometry_has_no_footprint():
    """
    Confirms point-only hazards such as earthquakes yield nothing.
    """
    point = {'type': 'Point', 'coordinates': [10, 10]}

    assert gdacs.footprint_from_item(_item(geometry=point)) is None


def test_get_gdacs_footprints_orders_episodes_newest_first():
    """
    Confirms episodes come back newest first and point episodes are dropped.
    """
    client = Mock()
    client.search.return_value.items_as_dicts.return_value = [
        _item(episode=1, datetime='2026-10-20T00:00:00Z'),
        _item(episode=2, datetime='2026-10-22T00:00:00Z'),
        _item(episode=3, geometry={'type': 'Point', 'coordinates': [0, 0]}),
    ]

    footprints = gdacs.get_gdacs_footprints('gdacs-event-1033087-1', client=client)

    assert [f.item_id for f in footprints] == [
        'gdacs-hazard-1033087-2', 'gdacs-hazard-1033087-1',
    ]
    search_filter = client.search.call_args.kwargs['filter']
    assert search_filter['args'][1] == 'gdacs-hazard-1033087-%'


def test_get_gdacs_footprints_rejects_bad_event_id():
    """
    Confirms a non-numeric event ID is rejected before searching.
    """
    with pytest.raises(ValueError):
        gdacs.get_gdacs_footprints('EMSR935', client=Mock())
