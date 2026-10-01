from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest

from monty_tool.boto3_utils.s3_protocols import S3Bucket
from monty_tool.news.s3_storage import (
    NEWS_ARTICLES_PREFIX,
    download_collection_cache,
    upload_news_snapshot,
)
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


def test_upload_news_snapshot(tmp_path: Path) -> None:
    bucket = Mock()
    snapshot = tmp_path / "news-123.json"
    snapshot.write_text("{}", encoding="utf-8")

    key = upload_news_snapshot(cast(S3Bucket, bucket), snapshot)

    assert key == NEWS_ARTICLES_PREFIX + "news-123.json"
    bucket.upload_file.assert_called_once_with(snapshot, key)
