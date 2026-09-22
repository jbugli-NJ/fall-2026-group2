"""
Tests for `monty_tool.spatial`, using synthetic GeoJSON fixtures only
(no network calls).
"""

from monty_tool.spatial import fill_events_with_h3, polygon_to_h3_cells


# Fixtures

# A small box over Washington, DC.
DC_POLYGON = {
    'type': 'Polygon',
    'coordinates': [[
        [-77.05, 38.85],
        [-76.95, 38.85],
        [-76.95, 38.95],
        [-77.05, 38.95],
        [-77.05, 38.85],
    ]],
}

DC_POINT = {'type': 'Point', 'coordinates': [-77.0, 38.9]}

# Two disjoint boxes: DC and a box over Baltimore.
DC_BALTIMORE_MULTIPOLYGON = {
    'type': 'MultiPolygon',
    'coordinates': [
        DC_POLYGON['coordinates'],
        [[
            [-76.65, 39.25],
            [-76.55, 39.25],
            [-76.55, 39.35],
            [-76.65, 39.35],
            [-76.65, 39.25],
        ]],
    ],
}


# polygon_to_h3_cells

def test_polygon_returns_nonempty_cells_at_resolution_6():
    cells = polygon_to_h3_cells(DC_POLYGON, resolution=6)
    assert cells
    assert all(isinstance(cell, str) for cell in cells)


def test_point_returns_cells_via_k_ring():
    cells = polygon_to_h3_cells(DC_POINT, resolution=6)
    assert cells
    # A k-ring around one cell is always more than a single cell.
    assert len(cells) > 1


def test_multipolygon_returns_cells_from_all_subpolygons():
    dc_cells = set(polygon_to_h3_cells(DC_POLYGON, resolution=6))
    combined_cells = set(polygon_to_h3_cells(DC_BALTIMORE_MULTIPOLYGON, resolution=6))
    assert dc_cells
    assert dc_cells.issubset(combined_cells)
    # Baltimore box contributes cells DC alone doesn't have.
    assert combined_cells - dc_cells


def test_missing_or_unsupported_geometry_returns_empty_list():
    assert polygon_to_h3_cells({}, resolution=6) == []
    assert polygon_to_h3_cells({'type': 'LineString', 'coordinates': []}, resolution=6) == []


def test_unsplit_antimeridian_ring_returns_empty_list():
    # A single ring spanning the full -180..180 longitude range, as seen
    # in a handful of real `ifrcevent-events` records that carry a
    # national boundary crossing the antimeridian without being split
    # into a MultiPolygon. Handing this to h3 flood-fills most of the
    # globe, so it must be rejected before ever calling into h3.
    antimeridian_ring = {
        'type': 'Polygon',
        'coordinates': [[
            [-179.9, 41.2],
            [179.9, 41.2],
            [179.9, 81.9],
            [-179.9, 81.9],
            [-179.9, 41.2],
        ]],
    }
    assert polygon_to_h3_cells(antimeridian_ring, resolution=6) == []


# fill_events_with_h3

def test_fill_events_with_h3_one_row_per_event_cell():
    events = [
        {
            'event_id': 'evt-1',
            'geometry': DC_POLYGON,
            'hazard_codes': ['FL'],
            'country_codes': ['USA'],
            'start_datetime': '2026-01-01T00:00:00Z',
        },
        {
            'event_id': 'evt-2',
            'geometry': DC_POINT,
            'hazard_codes': ['EQ'],
            'country_codes': ['USA'],
            'start_datetime': '2026-02-01T00:00:00Z',
        },
    ]
    rows = fill_events_with_h3(events, resolution=6)

    expected_cells_1 = set(polygon_to_h3_cells(DC_POLYGON, resolution=6))
    expected_cells_2 = set(polygon_to_h3_cells(DC_POINT, resolution=6))
    assert len(rows) == len(expected_cells_1) + len(expected_cells_2)

    rows_evt_1 = [r for r in rows if r['event_id'] == 'evt-1']
    assert {r['h3_cell'] for r in rows_evt_1} == expected_cells_1
    assert all(r['geometry_type'] == 'Polygon' for r in rows_evt_1)
    assert all(r['hazard_codes'] == ['FL'] for r in rows_evt_1)

    rows_evt_2 = [r for r in rows if r['event_id'] == 'evt-2']
    assert {r['h3_cell'] for r in rows_evt_2} == expected_cells_2
    assert all(r['geometry_type'] == 'Point' for r in rows_evt_2)


def test_fill_events_with_h3_drops_events_without_geometry():
    events = [{'event_id': 'evt-empty', 'geometry': None, 'hazard_codes': [], 'country_codes': [], 'start_datetime': None}]
    assert fill_events_with_h3(events, resolution=6) == []
