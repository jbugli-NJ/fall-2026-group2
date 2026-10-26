"""Transfer disaster input and Media Cloud snapshots through S3."""
import json
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING
from urllib.parse import quote

from monty_tool.boto3_utils.s3_protocols import S3Bucket
from monty_tool.boto3_utils.s3_utils import download_object, upload_object
from monty_tool.data_cache import raw_cache_path
from monty_tool.tools.resources import RAW_BUCKET_PREFIX

if TYPE_CHECKING:
    from monty_tool.news.history import MediaCloudRunSettings
    from monty_tool.news.runner import MediaCloudJobRun


def current_snapshot_path(output_dir: Path, *, collection: str, event_id: str) -> Path:
    """Use one current file per event, with path-safe identity components."""
    if any(not value.strip() or value in {".", ".."} for value in (collection, event_id)):
        raise ValueError("A collection and event ID are required for a snapshot.")
    return output_dir / "current" / quote(collection, safe="") / f"{quote(event_id, safe='')}.json"


def current_snapshot_key(*, prefix: str, collection: str, event_id: str) -> str:
    """Return the canonical S3 key for an event's current result."""
    folder = prefix.strip().strip("/")
    if not folder:
        raise ValueError("An explicit S3 folder prefix is required.")
    relative = current_snapshot_path(Path(), collection=collection, event_id=event_id)
    return f"{folder}/media_cloud_articles/{relative.as_posix()}"


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


def save_media_cloud_snapshot(
    run: "MediaCloudJobRun",
    *,
    output_dir: Path,
    run_key: str | None = None,
    request_settings: "MediaCloudRunSettings | None" = None,
) -> Path:
    """Atomically replace this event's current candidates and decisions."""
    if (run_key is None) != (request_settings is None):
        raise ValueError("run_key and request_settings must be supplied together.")
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
    if run_key is not None:
        report["run_key"] = run_key
        report["request_settings"] = request_settings
    destination = current_snapshot_path(
        output_dir, collection=run.job.event.collection, event_id=run.job.event.item_id,
    )
    staged = _save_snapshot(destination.parent, report)
    try:
        read_media_cloud_snapshot(staged)
        staged.replace(destination)
    finally:
        staged.unlink(missing_ok=True)
    return destination


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
    """Replace the event's current S3 object after validating the snapshot."""
    run = read_media_cloud_snapshot(snapshot)
    key = current_snapshot_key(
        prefix=prefix, collection=run.job.event.collection, event_id=run.job.event.item_id,
    )

    upload_object(bucket, snapshot, key)
    return key

def list_media_cloud_snapshot_keys(
    bucket: S3Bucket, *, prefix: str
) -> list[str]:
    """List current results only; legacy snapshots are not graph input."""
    folder = prefix.strip().strip("/")
    if not folder:
        raise ValueError("An explicit S3 folder prefix is required.")

    snapshot_prefix = f"{folder}/media_cloud_articles/current/"
    return sorted(
        obj.key
        for obj in bucket.objects.filter(Prefix=snapshot_prefix)
        if obj.key.startswith(snapshot_prefix) and obj.key.endswith(".json")
    )


def iter_media_cloud_snapshots(
    bucket: S3Bucket, *, prefix: str
) -> Iterator[tuple[str, "MediaCloudJobRun"]]:
    """Download current snapshots and validate their event-to-key identity."""
    with TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "snapshot.json"

        for key in list_media_cloud_snapshot_keys(bucket, prefix=prefix):
            download_object(bucket, key, path)
            run = read_media_cloud_snapshot(path)
            expected_key = current_snapshot_key(
                prefix=prefix, collection=run.job.event.collection, event_id=run.job.event.item_id,
            )
            if key != expected_key:
                raise ValueError(f"Media Cloud snapshot event does not match its key: {key}")
            yield key, run
