"""Reuse completed Media Cloud runs with the same collection settings."""

import json
from hashlib import sha256
from pathlib import Path
from typing import TypedDict

from monty_tool.boto3_utils.s3_protocols import S3Bucket
from monty_tool.boto3_utils.s3_utils import download_object
from monty_tool.news.pipeline import MediaCloudCollectionJob
from monty_tool.news.s3_storage import read_media_cloud_snapshot


class MediaCloudRunSettings(TypedDict):
    page_size: int
    max_pages: int
    max_body_attempts: int
    verification_mode: str


def media_cloud_run_key(
    job: MediaCloudCollectionJob, settings: MediaCloudRunSettings
) -> str:
    """Identify one exact event search and assessment configuration."""
    identity = {
        "job": job.model_dump(mode="json"),
        "settings": settings,
    }
    encoded = json.dumps(
        identity, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _matching_snapshot(
    path: Path,
    *,
    job: MediaCloudCollectionJob,
    settings: MediaCloudRunSettings,
    run_key: str,
) -> bool:
    """Reject older, incomplete, or mismatched snapshots for reuse."""
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(report, dict)
            or report.get("run_key") != run_key
            or report.get("request_settings") != settings
        ):
            return False
        run = read_media_cloud_snapshot(path)
    except (OSError, TypeError, ValueError):
        return False
    return run.job == job and media_cloud_run_key(run.job, settings) == run_key


def find_local_media_cloud_snapshot(
    output_dir: Path,
    *,
    job: MediaCloudCollectionJob,
    settings: MediaCloudRunSettings,
    run_key: str,
) -> Path | None:
    """Find a validated prior run in the current output directory."""
    for path in sorted(output_dir.glob("news-*.json")):
        if _matching_snapshot(path, job=job, settings=settings, run_key=run_key):
            return path
    return None


def download_media_cloud_snapshot_for_run(
    bucket: S3Bucket,
    *,
    prefix: str,
    output_dir: Path,
    job: MediaCloudCollectionJob,
    settings: MediaCloudRunSettings,
    run_key: str,
) -> tuple[Path, str] | None:
    """Reuse an exact completed S3 run without a new Media Cloud request."""
    folder = prefix.strip().strip("/")
    if not folder:
        raise ValueError("An explicit S3 folder prefix is required.")
    key_prefix = f"{folder}/media_cloud_articles/snapshots/{run_key}-"
    keys = [
        obj.key for obj in bucket.objects.filter(Prefix=key_prefix)
        if obj.key.startswith(key_prefix) and obj.key.endswith(".json")
    ]
    if not keys:
        return None
    key = max(keys)

    path = output_dir / "s3-reuse" / f"{run_key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    download_object(bucket, key, path)
    if not _matching_snapshot(path, job=job, settings=settings, run_key=run_key):
        raise ValueError(f"Invalid Media Cloud S3 snapshot: {key}")
    return path, key
