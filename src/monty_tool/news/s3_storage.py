"""Transfer disaster input and Media Cloud snapshots through S3."""
import json
import re
from collections.abc import Iterator
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from monty_tool.boto3_utils.s3_protocols import S3Bucket
from monty_tool.boto3_utils.s3_utils import download_object, upload_object
from monty_tool.data_cache import raw_cache_path
from monty_tool.tools.resources import RAW_BUCKET_PREFIX

if TYPE_CHECKING:
    from monty_tool.news.history import MediaCloudRunSettings
    from monty_tool.news.runner import MediaCloudJobRun

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
    """Save candidates, available bodies, and relevance decisions."""
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
    report = json.loads(snapshot.read_text(encoding="utf-8"))
    run_key = report.get("run_key")
    if run_key is not None and not (
        isinstance(run_key, str) and re.fullmatch(r"[0-9a-f]{64}", run_key)
    ):
        raise ValueError("Invalid Media Cloud run key.")
    digest = sha256(snapshot.read_bytes()).hexdigest()
    if run_key is None:
        identifier = digest
    else:
        saved_at = datetime.fromisoformat(report["saved_at"])
        if saved_at.utcoffset() is None:
            raise ValueError("Media Cloud snapshot timestamp must include a timezone.")
        timestamp = saved_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        identifier = f"{run_key}-{timestamp}-{digest}"
    key = f"{folder}/media_cloud_articles/snapshots/{identifier}.json"

    upload_object(bucket, snapshot, key)
    return key

def list_media_cloud_snapshot_keys(
    bucket: S3Bucket, *, prefix: str
) -> list[str]:
    """List Media Cloud snapshots under the same prefix used for upload."""
    folder = prefix.strip().strip("/")
    if not folder:
        raise ValueError("An explicit S3 folder prefix is required.")

    snapshot_prefix = f"{folder}/media_cloud_articles/snapshots/"
    return sorted(
        obj.key
        for obj in bucket.objects.filter(Prefix=snapshot_prefix)
        if obj.key.endswith(".json")
    )


def iter_media_cloud_snapshots(
    bucket: S3Bucket, *, prefix: str
) -> Iterator[tuple[str, "MediaCloudJobRun"]]:
    """Download and validate Media Cloud snapshots one at a time."""
    with TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "snapshot.json"

        for key in list_media_cloud_snapshot_keys(bucket, prefix=prefix):
            download_object(bucket, key, path)
            yield key, read_media_cloud_snapshot(path)
