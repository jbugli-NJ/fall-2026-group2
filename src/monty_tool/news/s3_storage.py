"""Transfer disaster input and news snapshots through S3."""
from datetime import datetime, timezone
from hashlib import sha256
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from monty_tool.news.runner import MediaCloudJobRun

import json
from pathlib import Path

from monty_tool.boto3_utils.s3_protocols import S3Bucket
from monty_tool.boto3_utils.s3_utils import download_object, upload_object
from monty_tool.data_cache import raw_cache_path
from monty_tool.tools.resources import BUCKET_DATA_PREFIX, RAW_BUCKET_PREFIX
from monty_tool.news.history import news_job_key
from monty_tool.news.pipeline import NewsCollectionJob
from monty_tool.news.schemas import NewsSearchResult, news_search_parameters
from collections.abc import Iterator
from tempfile import TemporaryDirectory

NEWS_ARTICLES_PREFIX = BUCKET_DATA_PREFIX + "newsapi_articles/"

def list_news_snapshot_keys(bucket: S3Bucket) -> list[str]:
    """List saved news-search results for network loading."""
    prefix = f"{NEWS_ARTICLES_PREFIX}by-job/"
    return sorted(
        obj.key
        for obj in bucket.objects.filter(Prefix=prefix)
        if obj.key.endswith(".json")
    )

def news_snapshot_key(
    job: NewsCollectionJob, *, page_size: int = 100
) -> str:
    """Return the same S3 key for the same news search."""
    return (
        f"{NEWS_ARTICLES_PREFIX}by-job/"
        f"{news_job_key(job, page_size=page_size)}.json"
    )

def read_news_snapshot(
    path: Path, *, key: str
) -> tuple[NewsCollectionJob, NewsSearchResult]:
    """Validate a saved search result and return its job and articles."""
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        job = NewsCollectionJob.model_validate(report["job"])
        result = NewsSearchResult.model_validate(report["result"])
        parameters = report["search_parameters"]
        page_size = parameters["page_size"]

        if type(page_size) is not int or not 1 <= page_size <= 100:
            raise ValueError("Invalid page size.")

        if (
            report["schema_version"] != 1
            or parameters != news_search_parameters(job.query, page_size=page_size)
            or report["status"] != ("ok" if result.articles else "empty")
            or job.event.item_id != job.query.item_id
            or result.item_id != job.query.item_id
            or result.query != job.query.query
            or result.from_date != job.query.from_date
            or result.to_date != job.query.to_date
            or key != news_snapshot_key(job, page_size=page_size)
        ):
            raise ValueError("Snapshot contents do not match its S3 key.")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid S3 news snapshot: {key}") from exc

    return job, result

def iter_news_snapshots(
    bucket: S3Bucket,
) -> Iterator[tuple[str, NewsCollectionJob, NewsSearchResult]]:
    """Download and validate saved results one at a time."""
    with TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "snapshot.json"

        for key in list_news_snapshot_keys(bucket):
            download_object(bucket, key, path)
            job, result = read_news_snapshot(path, key=key)
            yield key, job, result

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

        expected_parameters = news_search_parameters(job.query, page_size=page_size)
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
            or parameters != news_search_parameters(job.query, page_size=page_size)
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


def save_media_cloud_snapshot(
    run: "MediaCloudJobRun",
    *,
    output_dir: Path,
) -> Path:
    """Save candidates, available bodies, and relevance decisions."""
    from monty_tool.news.collector import _save_snapshot

    result = run.model_dump(mode="json")

    # Evidence normally excludes body_text from JSON.
    # Include it explicitly in the persistent snapshot.
    for saved, assessment in zip(
        result["assessments"],
        run.assessments,
        strict=True,
    ):
        saved["evidence"]["body_text"] = assessment.evidence.body_text

    report = {
        "schema_version": 1,
        "provider": "media_cloud",
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "run": result,
    }
    return _save_snapshot(output_dir, report)


def read_media_cloud_snapshot(path: Path) -> "MediaCloudJobRun":
    """Read a saved Media Cloud run and validate its structure."""
    from monty_tool.news.runner import MediaCloudJobRun

    report = json.loads(path.read_text(encoding="utf-8"))

    if (
        not isinstance(report, dict)
        or report.get("schema_version") != 1
        or report.get("provider") != "media_cloud"
        or "run" not in report
    ):
        raise ValueError("Invalid Media Cloud snapshot.")

    return MediaCloudJobRun.model_validate(report["run"])


def upload_media_cloud_snapshot(
    bucket: S3Bucket,
    snapshot: Path,
    *,
    prefix: str,
) -> str:
    """Upload a validated snapshot under an explicit S3 prefix."""
    folder = prefix.strip().strip("/")
    if not folder:
        raise ValueError("An explicit S3 folder prefix is required.")

    read_media_cloud_snapshot(snapshot)

    digest = sha256(snapshot.read_bytes()).hexdigest()
    key = f"{folder}/media_cloud_articles/snapshots/{digest}.json"

    upload_object(bucket, snapshot, key)
    return key