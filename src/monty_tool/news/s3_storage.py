"""Transfer disaster input and news snapshots through S3."""

from pathlib import Path

from monty_tool.boto3_utils.s3_protocols import S3Bucket
from monty_tool.boto3_utils.s3_utils import download_object, upload_object
from monty_tool.data_cache import raw_cache_path
from monty_tool.tools.resources import BUCKET_DATA_PREFIX, RAW_BUCKET_PREFIX


NEWS_ARTICLES_PREFIX = BUCKET_DATA_PREFIX + "newsapi_articles/"


def download_collection_cache(
    bucket: S3Bucket,
    *,
    source_key: str,
    collection: str,
    cache_dir: Path,
    geometry: bool = True,
) -> Path:
    """Download one S3 disaster collection where the existing loader expects it."""
    if not source_key.startswith(RAW_BUCKET_PREFIX):
        raise ValueError("source_key must be under the raw/ prefix.")

    path = raw_cache_path(collection, cache_dir=cache_dir, geometry=geometry)
    path.parent.mkdir(parents=True, exist_ok=True)
    download_object(bucket, source_key, path)
    return path


def upload_news_snapshot(bucket: S3Bucket, snapshot: Path) -> str:
    """Upload one completed local news snapshot without changing its contents."""
    key = NEWS_ARTICLES_PREFIX + snapshot.name
    upload_object(bucket, snapshot, key)
    return key
