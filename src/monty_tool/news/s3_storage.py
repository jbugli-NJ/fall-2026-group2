"""Transfer disaster input and news snapshots through S3."""

import json
from pathlib import Path

from monty_tool.boto3_utils.s3_protocols import S3Bucket
from monty_tool.boto3_utils.s3_utils import download_object, upload_object
from monty_tool.data_cache import raw_cache_path
from monty_tool.tools.resources import BUCKET_DATA_PREFIX, RAW_BUCKET_PREFIX
from monty_tool.news.history import news_job_key
from monty_tool.news.pipeline import NewsCollectionJob
from monty_tool.news.schemas import NewsSearchResult


NEWS_ARTICLES_PREFIX = BUCKET_DATA_PREFIX + "newsapi_articles/"

def news_snapshot_key(
    job: NewsCollectionJob, *, page_size: int = 100
) -> str:
    """Return the same S3 key for the same news search."""
    return (
        f"{NEWS_ARTICLES_PREFIX}by-job/"
        f"{news_job_key(job, page_size=page_size)}.json"
    )

def download_news_snapshot(
    bucket: S3Bucket,
    *,
    job: NewsCollectionJob,
    output_dir: Path,
    page_size: int = 100,
) -> Path | None:
    """Download an existing result for this exact search, if present."""
    key = news_snapshot_key(job, page_size=page_size)

    exists = any(
        obj.key == key
        for obj in bucket.objects.filter(Prefix=key)
    )
    if not exists:
        return None

    path = output_dir / "s3-reuse" / Path(key).name
    path.parent.mkdir(parents=True, exist_ok=True)
    download_object(bucket, key, path)

    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        saved_job = NewsCollectionJob.model_validate(report["job"])
        result = NewsSearchResult.model_validate(report["result"])

        expected_parameters = {
            "page_size": page_size,
            "language": "en",
            "sort_by": "relevancy",
            "page": 1,
        }
        valid = (
            report["schema_version"] == 1
            and report["search_parameters"] == expected_parameters
            and news_job_key(saved_job, page_size=page_size)
            == news_job_key(job, page_size=page_size)
            and report["status"] == (
                "ok" if result.articles else "empty"
            )
            and result.item_id == job.query.item_id
            and result.query == job.query.query
            and result.from_date == job.query.from_date
            and result.to_date == job.query.to_date
        )
        if not valid:
            raise ValueError("Snapshot does not match this search.")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid S3 news snapshot: {key}") from exc

    return path

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
    """Upload a successful result under its stable search key."""
    try:
        report = json.loads(snapshot.read_text(encoding="utf-8"))
        job = NewsCollectionJob.model_validate(report["job"])
        result = NewsSearchResult.model_validate(report["result"])
        parameters = report["search_parameters"]
        page_size = parameters["page_size"]

        if (
            report["schema_version"] != 1
            or report["status"] != ("ok" if result.articles else "empty")
            or parameters != {
                "page_size": page_size,
                "language": "en",
                "sort_by": "relevancy",
                "page": 1,
            }
            or result.item_id != job.query.item_id
            or result.query != job.query.query
            or result.from_date != job.query.from_date
            or result.to_date != job.query.to_date
        ):
            raise ValueError("Snapshot is not a matching successful result.")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid news snapshot: {snapshot}") from exc

    key = news_snapshot_key(job, page_size=page_size)
    upload_object(bucket, snapshot, key)
    return key
