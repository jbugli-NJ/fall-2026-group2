"""
Tests for the NASA POWER data update tool.
"""

from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from monty_tool.api_schemas import MontandonItem
from monty_tool.tools import update_nasa_power_data as tool
from monty_tool.tools.resources import RAW_BUCKET_PREFIX
from monty_tool.weather.schemas import WeatherResult


# Test object helpers

def _item(item_id: str, *, point: bool = True) -> MontandonItem:
    """
    Build a Montandon record with optional point geometry.
    """
    return MontandonItem.model_validate({
        'id': item_id,
        'collection': 'gdacs-events',
        'bbox': [-76.83, 4.42, -76.83, 4.42],
        'geometry': {'type': 'Point', 'coordinates': [-76.83, 4.42]} if point else None,
        'links': [],
        'properties': {
            'roles': ['event'],
            'title': 'Flood',
            'description': 'River flooding.',
            'datetime': '2024-01-10T00:00:00Z',
            'start_datetime': '2024-01-10T00:00:00Z',
            'end_datetime': '2024-01-12T00:00:00Z',
            'monty:corr_id': 'correlation-1',
            'monty:hazard_codes': ['FL'],
            'monty:country_codes': ['COL'],
            'monty:episode_number': 1,
        },
    })


# Tests

def test_new_point_items_filters_existing_and_non_point_records(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ):
    """
    Confirms stored IDs, duplicate Items, and non-point records are skipped.
    """
    items = [_item('existing'), _item('no-point', point=False), _item('new'), _item('new')]
    bucket = Mock()
    bucket.objects.filter.return_value = [
        SimpleNamespace(key=RAW_BUCKET_PREFIX + 'events.jsonl.gz'),
    ]
    monkeypatch.setattr(tool, 'download_object', Mock())
    monkeypatch.setattr(tool, 'read_gzip', lambda path: (
        item.model_dump(mode='json', by_alias=True) for item in items
    ))

    results = list(tool._new_point_items(bucket, tmp_path / 'raw.jsonl.gz', {'existing'}))

    assert [item.id for item in results] == ['new']


def test_uploads_completed_batch_before_later_failure(monkeypatch: pytest.MonkeyPatch):
    """
    Confirms the first 500 results are uploaded before a later batch fails.
    """
    items = [_item(str(number)) for number in range(501)]
    results = [
        WeatherResult(
            item_id=item.id, latitude=4.42, longitude=-76.83,
            start_date=date(2024, 1, 3), end_date=date(2024, 1, 13),
        )
        for item in items[:500]
    ]
    bucket = Mock()
    monkeypatch.setattr(tool, 'get_env_bucket_name', lambda: 'test-bucket')
    monkeypatch.setattr(tool, 'get_bucket', lambda name: bucket)
    monkeypatch.setattr(tool, '_download_existing', Mock(return_value=set()))
    monkeypatch.setattr(tool, '_new_point_items', Mock(return_value=iter(items)))
    fetch = Mock(side_effect=[results, RuntimeError('simulated failure')])
    monkeypatch.setattr(tool, 'pull_event_weather', fetch)
    uploads = []
    monkeypatch.setattr(
        tool, 'upload_object',
        lambda bucket, path, key: uploads.append(list(tool.read_gzip(path))),
    )

    with pytest.raises(RuntimeError, match='simulated failure'):
        tool.main()

    assert [len(call.args[0]) for call in fetch.call_args_list] == [500, 1]
    assert len(uploads) == 1
    assert [record['item_id'] for record in uploads[0]] == list(map(str, range(500)))
