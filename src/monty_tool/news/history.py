"""Reuse completed Media Cloud runs with the same collection settings."""

import json
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TypedDict

from monty_tool.boto3_utils.s3_protocols import S3Bucket
from monty_tool.boto3_utils.s3_utils import download_object
from monty_tool.news.pipeline import MediaCloudCollectionJob
from monty_tool.news.s3_storage import (
    current_snapshot_key,
    current_snapshot_path,
    read_media_cloud_snapshot,
)


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
    """Reuse only the event's current result with identical run settings."""
    path = current_snapshot_path(
        output_dir, collection=job.event.collection, event_id=job.event.item_id,
    )
    if _matching_snapshot(path, job=job, settings=settings, run_key=run_key):
        return path
    return None


def download_current_media_cloud_snapshot(
    bucket: S3Bucket,
    *,
    prefix: str,
    output_dir: Path,
    job: MediaCloudCollectionJob,
) -> tuple[Path, str] | None:
    """Refresh the local current file from S3; the caller checks run settings.

    Returning None means no current S3 object exists. A downloaded current file
    supersedes older local settings, so the caller cannot revive a stale run.
    """
    key = current_snapshot_key(
        prefix=prefix, collection=job.event.collection, event_id=job.event.item_id,
    )
    if not any(obj.key == key for obj in bucket.objects.filter(Prefix=key)):
        return None

    path = current_snapshot_path(
        output_dir, collection=job.event.collection, event_id=job.event.item_id,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path.parent) as temporary:
        staged = Path(temporary) / "snapshot.json"
        download_object(bucket, key, staged)
        run = read_media_cloud_snapshot(staged)
        if (run.job.event.item_id, run.job.event.collection) != (job.event.item_id, job.event.collection):
            raise ValueError(f"Media Cloud snapshot event does not match its key: {key}")
        staged.replace(path)
    return path, key
