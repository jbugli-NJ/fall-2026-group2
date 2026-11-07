import logging
from pathlib import Path
from typing import cast
from unittest.mock import Mock
from types import SimpleNamespace

import pytest

from monty_tool.boto3_utils.s3_protocols import S3Bucket
from monty_tool.news import s3_storage
from monty_tool.news.s3_storage import download_collection_cache
from monty_tool.tools.resources import RAW_BUCKET_PREFIX

@pytest.mark.parametrize(
    ("geometry", "filename"),
    [
        (True, "gdacs-events.jsonl.gz"),
        (False, "gdacs-events.nogeom.jsonl.gz"),
    ],
)
def test_download_collection_cache(
    tmp_path: Path, geometry: bool, filename: str
) -> None:
    bucket = Mock()
    bucket.download_file.side_effect = (
        lambda _key, path: Path(path).write_bytes(b"example input")
    )
    source_key = RAW_BUCKET_PREFIX + filename
    cache_dir = tmp_path / "raw"

    result = download_collection_cache(
        cast(S3Bucket, bucket),
        source_key=source_key,
        collection="gdacs-events",
        cache_dir=cache_dir,
        geometry=geometry,
    )

    assert result == cache_dir / filename
    assert result.read_bytes() == b"example input"
    bucket.download_file.assert_called_once_with(source_key, result)


def test_download_rejects_other_prefix(tmp_path: Path) -> None:
    bucket = Mock()

    with pytest.raises(ValueError, match="raw/"):
        download_collection_cache(
            cast(S3Bucket, bucket),
            source_key="other/gdacs-events.jsonl.gz",
            collection="gdacs-events",
            cache_dir=tmp_path,
        )

    bucket.download_file.assert_not_called()


def test_list_media_cloud_snapshot_keys_uses_upload_prefix() -> None:
    bucket = Mock()
    prefix = "test-owner/media_cloud_articles/current/"
    bucket.objects.filter.return_value = [
        SimpleNamespace(key=prefix + "b.json"),
        SimpleNamespace(key=prefix + "notes.txt"),
        SimpleNamespace(key=prefix + "a.json"),
        SimpleNamespace(key="test-owner/media_cloud_articles/snapshots/old.json"),
    ]

    keys = s3_storage.list_media_cloud_snapshot_keys(
        cast(S3Bucket, bucket), prefix="test-owner"
    )

    assert keys == [prefix + "a.json", prefix + "b.json"]
    bucket.objects.filter.assert_called_once_with(Prefix=prefix)


def test_iter_media_cloud_snapshots_downloads_and_reads(monkeypatch) -> None:
    bucket = Mock()
    key = "test-owner/media_cloud_articles/current/emdat-events/event-1.json"
    bucket.objects.filter.return_value = [SimpleNamespace(key=key)]
    run = Mock()
    run.job.event.collection = "emdat-events"
    run.job.event.item_id = "event-1"
    read = Mock(return_value=run)
    monkeypatch.setattr(s3_storage, "read_media_cloud_snapshot", read)

    found = list(
        s3_storage.iter_media_cloud_snapshots(
            cast(S3Bucket, bucket), prefix="test-owner"
        )
    )

    assert found == [(key, run)]
    assert bucket.download_file.call_args.args[0] == key
    read.assert_called_once()


@pytest.mark.parametrize('relative_key', [
    'emdat-events/event-0.json',
    'emdat-events/emdat-events/event-1.json',
])
def test_iter_skips_mismatched_snapshot_and_continues(monkeypatch, caplog, relative_key) -> None:
    bucket = Mock()
    key = f"test-owner/media_cloud_articles/current/{relative_key}"
    valid_key = "test-owner/media_cloud_articles/current/emdat-events/event-1.json"
    bucket.objects.filter.return_value = [SimpleNamespace(key=key), SimpleNamespace(key=valid_key)]
    run = Mock()
    run.job.event.collection = "emdat-events"
    run.job.event.item_id = "event-1"
    monkeypatch.setattr(s3_storage, "read_media_cloud_snapshot", Mock(return_value=run))
    with caplog.at_level(logging.DEBUG, logger=s3_storage.__name__):
        found = list(s3_storage.iter_media_cloud_snapshots(bucket, prefix="test-owner"))
    assert found == [(valid_key, run)]
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.DEBUG
    assert key in caplog.records[0].getMessage()


def test_current_path_encodes_event_ids_without_path_traversal(tmp_path) -> None:
    path = s3_storage.current_snapshot_path(
        tmp_path, collection="emdat-events", event_id="../event/a",
    )
    assert path.parent == tmp_path / "current" / "emdat-events"
    assert path.name == "..%2Fevent%2Fa.json"
