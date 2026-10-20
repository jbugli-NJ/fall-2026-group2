"""Run planned news collection jobs sequentially."""

import json
from collections.abc import Iterable
from datetime import timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from monty_tool.news.history import load_recent_snapshots, news_job_key
from monty_tool.boto3_utils.s3_protocols import S3Bucket
from monty_tool.news.s3_storage import download_news_snapshot

from monty_tool.news.body import fetch_publisher_html, extract_publisher_body
from monty_tool.news.collector import (
    MediaCloudCandidateBatch,
    collect_news_job,
)
from monty_tool.news.pipeline import (
    MediaCloudCollectionJob,
    NewsCollectionJob,
)
from monty_tool.news.schemas import MediaCloudStory


class NewsCollectionRun(BaseModel):
    """Summarize new snapshots, reused snapshots, and any early stop."""

    collected: list[Path] = Field(default_factory=list)
    reused: list[Path] = Field(default_factory=list)
    stop_reason: Literal[
        "completed", "budget_exhausted", "error"
    ] = "completed"
    stop_report: Path | None = None


def run_news_collection(
    jobs: Iterable[NewsCollectionJob],
    *,
    output_dir: Path,
    request_limit: int,
    page_size: int = 100,
    refresh_after: timedelta = timedelta(hours=24),
    s3_bucket: S3Bucket | None = None,
) -> NewsCollectionRun:
    """Collect searches within one run's request limit."""
    if type(request_limit) is not int or request_limit < 1:
        raise ValueError("request_limit must be a positive integer.")

    if type(page_size) is not int or not 1 <= page_size <= 100:
        raise ValueError("page_size must be an integer between 1 and 100.")

    recent = load_recent_snapshots(
        output_dir,
        refresh_after=refresh_after,
    )
    summary = NewsCollectionRun()
    requests_made = 0

    for job in jobs:
        key = news_job_key(job, page_size=page_size)

        if key in recent:
            summary.reused.append(recent[key])
            continue
        if s3_bucket is not None:
            stored = download_news_snapshot(
                s3_bucket,
                job=job,
                output_dir=output_dir,
                page_size=page_size,
            )
            if stored is not None:
                summary.reused.append(stored)
                recent[key] = stored
                continue
        if requests_made >= request_limit:
            summary.stop_reason = "budget_exhausted"
            break

        requests_made += 1
        output = collect_news_job(
            job,
            output_dir=output_dir,
            page_size=page_size,
        )
        report = json.loads(output.read_text(encoding="utf-8"))
        status = report["status"]

        if status in {"ok", "empty"}:
            summary.collected.append(output)
            recent[key] = output
            continue

        if status == "error":
            summary.stop_reason = "error"
            summary.stop_report = output
            break

        raise ValueError(f"Unexpected collection status: {status!r}")

    return summary

class MediaCloudCandidateEvidence(BaseModel):
    """Keep every candidate, even when its publisher body is unavailable."""

    event_id: str
    event_collection: str
    story: MediaCloudStory
    body_status: Literal[
        "missing_url",
        "not_attempted",
        "html_unavailable",
        "body_unavailable",
        "body_available",
    ]
    final_url: str | None = None
    # Used in memory for verification; excluded from saved JSON.
    body_text: str | None = Field(default=None, exclude=True, repr=False)


def gather_media_cloud_evidence(
    job: MediaCloudCollectionJob,
    batch: MediaCloudCandidateBatch,
    *,
    max_body_attempts: int,
) -> list[MediaCloudCandidateEvidence]:
    """Attempt bounded publisher reads without dropping any search candidate."""
    if type(max_body_attempts) is not int or max_body_attempts < 0:
        raise ValueError("max_body_attempts must be a non-negative integer.")

    results: list[MediaCloudCandidateEvidence] = []
    attempts = 0

    for story in batch.stories:
        status: Literal[
            "missing_url",
            "not_attempted",
            "html_unavailable",
            "body_unavailable",
            "body_available",
        ] = "not_attempted"
        final_url: str | None = None
        body_text: str | None = None

        if not story.url or not story.url.strip():
            status = "missing_url"
        elif attempts < max_body_attempts:
            attempts += 1
            page = fetch_publisher_html(story.url)

            if page is None:
                status = "html_unavailable"
            else:
                html, final_url = page
                try:
                    body_text = extract_publisher_body(html, url=final_url)
                except Exception:
                    # Malformed publisher HTML must not discard the candidate.
                    body_text = None
                status = "body_available" if body_text else "body_unavailable"

        results.append(
            MediaCloudCandidateEvidence(
                event_id=job.event.item_id,
                event_collection=job.event.collection,
                story=story,
                body_status=status,
                final_url=final_url,
                body_text=body_text,
            )
        )

    return results