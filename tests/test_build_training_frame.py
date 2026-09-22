"""
Tests for the H3 training-frame assembly, using synthetic events and
impact totals only (no cache or network).
"""

# Imports

import math

import polars as pl

from monty_tool import build_training_frame as btf
from monty_tool.spatial import polygon_to_h3_cells


# Fixtures

POINT = {'type': 'Point', 'coordinates': [-77.0, 38.9]}

EVENTS = [
    {'event_id': 'corr-a', 'geometry': POINT, 'hazard_codes': ['FL'], 'country_codes': ['USA'], 'start_datetime': '2026-01-01T00:00:00Z'},
    {'event_id': 'corr-b', 'geometry': POINT, 'hazard_codes': ['EQ'], 'country_codes': ['USA'], 'start_datetime': '2026-02-01T00:00:00Z'},
    {'event_id': 'corr-none', 'geometry': None, 'hazard_codes': [], 'country_codes': [], 'start_datetime': None},
]


# events_to_h3_frame

def test_events_to_h3_frame_batches_all_events(monkeypatch):
    monkeypatch.setattr(btf, 'EVENT_BATCH_SIZE', 1)
    frame = btf.events_to_h3_frame(EVENTS, resolution=6)
    cells_per_point = len(polygon_to_h3_cells(POINT, resolution=6))
    assert frame.height == 2 * cells_per_point
    assert set(frame['event_id'].unique()) == {'corr-a', 'corr-b'}


# add_impact_labels

def test_add_impact_labels_joins_and_logs():
    frame = pl.DataFrame({'event_id': ['corr-a', 'corr-a', 'corr-b'], 'h3_cell': ['x', 'y', 'z']})
    impacts = pl.DataFrame({'monty:corr_id': ['corr-a'], 'affected_total': [99], 'death': [3]})
    labelled = btf.add_impact_labels(frame, impacts)

    assert labelled.height == 3
    a = labelled.filter(pl.col('event_id') == 'corr-a')
    assert a['affected_total'].to_list() == [99, 99]
    assert a['log_affected'][0] == math.log(100)
    b = labelled.filter(pl.col('event_id') == 'corr-b')
    assert b['affected_total'][0] is None
    assert b['log_affected'][0] == 0.0


# Sources

def test_every_source_has_distinct_output_path():
    paths = {btf.output_path(source) for source in btf.SOURCES}
    assert len(paths) == len(btf.SOURCES)
