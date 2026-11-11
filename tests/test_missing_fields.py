"""
Tests for the record checks and windowing in demo/missing_fields.
"""

import importlib.util
from datetime import date
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / 'demo' / 'missing_fields' / 'missing_fields.py'
spec = importlib.util.spec_from_file_location('missing_fields', SCRIPT)
assert spec is not None and spec.loader is not None
missing_fields = importlib.util.module_from_spec(spec)
spec.loader.exec_module(missing_fields)

TODAY = date(2026, 11, 8)
KNOWN = {'GH0101', 'EQ', 'nat-geo-ear-gro'}


def _item(**properties):
    base = {
        'datetime': '2024-05-01T00:00:00Z',
        'start_datetime': '2024-05-01T00:00:00Z',
        'end_datetime': '2024-05-02T00:00:00Z',
        'description': 'Earthquake in Somewhere',
        'monty:corr_id': '20240501-NPL-GH0101-1-GCDB',
        'monty:country_codes': ['NPL'],
        'monty:hazard_codes': ['GH0101', 'EQ'],
        'keywords': ['Earthquake', 'Nepal'],
        'monty:src_event_id': 'us7000abcd',
        'processing:version': '0.2.4',
    }
    base.update(properties)
    return {'id': 'x', 'bbox': [85.0, 27.0, 85.5, 27.5], 'properties': base}


def _failed(item, record_type='events'):
    return {
        check for check, failed
        in missing_fields.check_record(item, record_type, KNOWN, TODAY).items()
        if failed
    }


def test_complete_event_has_no_problems():
    assert _failed(_item()) == set()


def test_missing_dates_are_counted_separately():
    item = _item(start_datetime=None, end_datetime=None)
    assert _failed(item) == {'no_start_datetime', 'no_end_datetime'}


def test_end_before_start():
    item = _item(end_datetime='2024-04-01T00:00:00Z')
    assert 'end_before_start' in _failed(item)


@pytest.mark.parametrize('when', ['0001-01-01T00:00:00Z', '2202-01-01T00:00:00Z'])
def test_placeholder_dates(when):
    item = _item(datetime=when, start_datetime=when, end_datetime=when)
    assert 'placeholder_date' in _failed(item)
    assert missing_fields.year_bucket(item, TODAY) == missing_fields.PLACEHOLDER_BUCKET


def test_year_bucket_falls_back_to_start_datetime():
    item = _item(datetime=None)
    assert missing_fields.year_bucket(item, TODAY) == '2024'


def test_year_bucket_none_without_dates():
    item = _item(datetime=None, start_datetime=None)
    assert missing_fields.year_bucket(item, TODAY) is None


def test_geometry_checks():
    no_geometry = _item()
    no_geometry['bbox'] = None
    point = _item()
    point['bbox'] = [85.0, 27.0, 85.0, 27.0]
    country = _item()
    country['bbox'] = [80.0, 26.0, 88.2, 30.4]
    assert 'no_geometry' in _failed(no_geometry)
    assert 'point_only' in _failed(point)
    assert 'coarse_geometry' in _failed(country)


def test_country_checks():
    assert 'no_country' in _failed(_item(**{'monty:country_codes': []}))
    assert 'null_country' in _failed(_item(**{'monty:country_codes': [None]}))
    assert 'unknown_country_only' in _failed(_item(**{'monty:country_codes': ['UNK']}))
    assert 'unknown_country_only' not in _failed(_item(**{'monty:country_codes': ['UNK', 'NPL']}))


def test_hazard_code_checks():
    assert 'no_hazard_codes' in _failed(_item(**{'monty:hazard_codes': []}))
    assert 'unrecognised_hazard_code' in _failed(_item(**{'monty:hazard_codes': ['XX9999']}))


@pytest.mark.parametrize('text', [None, '', 'NA', 'None', 'DROUGHT in KOUTCHARAM: None'])
def test_blank_descriptions(text):
    assert 'blank_description' in _failed(_item(description=text))


def test_impact_checks_only_apply_to_impacts():
    event = _item()
    assert 'no_impact_detail' not in _failed(event)
    impact = _item(**{'monty:impact_detail': {'type': 'death', 'value': None, 'unit': None}})
    assert _failed(impact, 'impacts') == {'no_impact_value', 'no_impact_unit'}
    assert 'no_impact_detail' in _failed(_item(), 'impacts')


def test_hazard_checks_only_apply_to_hazards():
    hazard = _item(**{'monty:hazard_detail': {'severity_label': 'Green'}})
    assert _failed(hazard, 'hazards') == {'no_severity_value'}
    assert 'no_hazard_detail' in _failed(_item(), 'hazards')
    assert 'no_hazard_detail' not in _failed(_item())


def test_yearly_windows_cover_extent_and_placeholders():
    windows = missing_fields.windows(2024, TODAY)
    assert [w.label for w in windows] == ['before', '2024', '2025', '2026', 'after']
    assert windows[1].interval == '2024-01-01T00:00:00Z/2025-01-01T00:00:00Z'
    assert windows[-1].interval == '2026-11-08T00:00:00Z/..'
    assert windows[0].buckets == windows[-1].buckets == {missing_fields.PLACEHOLDER_BUCKET}


def test_monthly_windows_stop_at_today_and_roll_over_december():
    windows = missing_fields.windows(2025, TODAY, monthly_from=2025)
    labels = [w.label for w in windows]
    assert labels[1] == '2025-01' and labels[-2] == '2026-11'
    december = next(w for w in windows if w.label == '2025-12')
    assert december.interval == '2025-12-01T00:00:00Z/2026-01-01T00:00:00Z'
    assert december.buckets == {'2025'}


def test_windows_clamp_ancient_extents():
    windows = missing_fields.windows(1, TODAY)
    assert windows[1].label == str(missing_fields.FIRST_YEAR)


def test_bbox_km_is_zero_for_points():
    assert missing_fields.bbox_km([10.0, 10.0, 10.0, 10.0]) == 0
    assert 50 < missing_fields.bbox_km([10.0, 10.0, 10.5, 10.5]) < 60


def test_monthly_from_splits_only_later_years():
    labels = [w.label for w in missing_fields.windows(2025, TODAY, monthly_from=2026)]
    assert labels[:3] == ['before', '2025', '2026-01']
    assert labels[-2:] == ['2026-11', 'after']


def test_percent_keeps_rare_problems_visible():
    assert missing_fields.percent(0, 1000) == '0'
    assert missing_fields.percent(1, 1000) == '<1'
    assert missing_fields.percent(500, 1000) == '50'
    assert missing_fields.percent(0, 0) == '–'


def test_older_etl_fields():
    item = _item(**{'keywords': [], 'monty:src_event_id': None, 'processing:version': None})
    assert {'no_keywords', 'no_src_event_id', 'no_processing_version'} <= _failed(item)
