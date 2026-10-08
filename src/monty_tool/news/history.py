"""Find reusable news snapshots from previous collection runs."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from monty_tool.news.pipeline import NewsCollectionJob
from monty_tool.news.schemas import news_search_parameters


def news_job_key(
    job: NewsCollectionJob,
    *,
    page_size: int = 100,
) -> str:
    """Identify an exact source-record search and its request settings."""
    if type(page_size) is not int or not 1 <= page_size <= 100:
        raise ValueError("page_size must be an integer between 1 and 100.")

    identity = {
        "collection": job.event.collection,
        "query": job.query.model_dump(mode="json", exclude_none=True),
        "search_parameters": news_search_parameters(job.query, page_size=page_size),
    }
    encoded = json.dumps(
        identity,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    return hashlib.sha256(encoded).hexdigest()


def load_recent_snapshots(
    output_dir: Path,
    *,
    refresh_after: timedelta = timedelta(hours=24),
    now: datetime | None = None,
) -> dict[str, Path]:
    """Index recent successful snapshots once at the start of a run.

    Empty search results count as successful searches.
    Malformed snapshots raise an error rather than silently causing
    additional API requests.
    """
    if refresh_after <= timedelta(0):
        raise ValueError("refresh_after must be positive.")

    current = now if now is not None else datetime.now(timezone.utc)
    if current.utcoffset() is None:
        raise ValueError("now must include timezone information.")

    recent: dict[str, tuple[datetime, Path]] = {}

    for path in sorted(output_dir.glob("news-*.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))

            if report["schema_version"] != 1:
                raise ValueError("Unsupported snapshot schema.")

            if report["status"] not in {"ok", "empty"}:
                continue

            finished = datetime.fromisoformat(report["finished_at"])
            if finished.utcoffset() is None:
                raise ValueError("Snapshot timestamp must include a timezone.")

            age = current - finished
            if not timedelta(0) <= age < refresh_after:
                continue

            job = NewsCollectionJob.model_validate(report["job"])
            parameters = report["search_parameters"]
            page_size = parameters["page_size"]

            expected_parameters = news_search_parameters(job.query, page_size=page_size)
            if parameters != expected_parameters:
                continue

            key = news_job_key(job, page_size=page_size)
            previous = recent.get(key)

            if previous is None or finished > previous[0]:
                recent[key] = (finished, path)

        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"Invalid news snapshot: {path.name}"
            ) from exc

    return {
        key: path
        for key, (_, path) in recent.items()
    }
