from pathlib import Path
from typing import cast
from unittest.mock import Mock
from monty_tool.news import s3_storage

import pytest

from monty_tool.boto3_utils.s3_protocols import S3Bucket
from monty_tool.news.s3_storage import (
    NEWS_ARTICLES_PREFIX,
    download_collection_cache,
    upload_news_snapshot,
    list_news_snapshot_keys,
)
from monty_tool.tools.resources import RAW_BUCKET_PREFIX
from types import SimpleNamespace

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


def test_upload_news_snapshot_rejects_invalid_report(tmp_path: Path) -> None:
    bucket = Mock()
    snapshot = tmp_path / "news-123.json"
    snapshot.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid news snapshot"):
        upload_news_snapshot(cast(S3Bucket, bucket), snapshot)

    bucket.upload_file.assert_not_called()

def test_list_news_snapshot_keys_selects_json_in_job_prefix() -> None:
    bucket = Mock()
    prefix = f"{NEWS_ARTICLES_PREFIX}by-job/"
    bucket.objects.filter.return_value = [
        SimpleNamespace(key=prefix + "b.json"),
        SimpleNamespace(key=prefix + "notes.txt"),
        SimpleNamespace(key=prefix + "a.json"),
    ]

    result = list_news_snapshot_keys(cast(S3Bucket, bucket))

    assert result == [prefix + "a.json", prefix + "b.json"]
    bucket.objects.filter.assert_called_once_with(Prefix=prefix)

def test_list_media_cloud_snapshot_keys_uses_upload_prefix() -> None:
    bucket = Mock()
    prefix = "test-owner/media_cloud_articles/snapshots/"
    bucket.objects.filter.return_value = [
        SimpleNamespace(key=prefix + "b.json"),
        SimpleNamespace(key=prefix + "notes.txt"),
        SimpleNamespace(key=prefix + "a.json"),
    ]

    keys = s3_storage.list_media_cloud_snapshot_keys(
        cast(S3Bucket, bucket), prefix="test-owner"
    )

    assert keys == [prefix + "a.json", prefix + "b.json"]
    bucket.objects.filter.assert_called_once_with(Prefix=prefix)


def test_iter_media_cloud_snapshots_downloads_and_reads(monkeypatch) -> None:
    bucket = Mock()
    key = "test-owner/media_cloud_articles/snapshots/one.json"
    bucket.objects.filter.return_value = [SimpleNamespace(key=key)]
    run = Mock()
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