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
    collect_media_cloud_candidates,
    collect_news_job,
)
from monty_tool.news.pipeline import (
    MediaCloudCollectionJob,
    NewsCollectionJob,
)
from monty_tool.news.schemas import MediaCloudStory

from monty_tool.news.verification import (
    EventJudge,
    EventMatchDecision,
    decide_media_cloud_candidate,
)

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

class MediaCloudCandidateAssessment(BaseModel):
    """Keep one candidate together with its event-relevance decision."""

    evidence: MediaCloudCandidateEvidence
    decision: EventMatchDecision


class MediaCloudJobRun(BaseModel):
    """Result of collecting and assessing one Media Cloud search job."""

    job: MediaCloudCollectionJob
    pages_fetched: int
    next_page_token: str | None
    duplicates_removed: int
    assessments: list[MediaCloudCandidateAssessment]


def run_media_cloud_job(
    job: MediaCloudCollectionJob,
    *,
    judge: EventJudge,
    max_body_attempts: int,
    page_size: int = 100,
    max_pages: int = 2,
    page_interval_seconds: float = 31.0,
) -> MediaCloudJobRun:
    """Collect candidates, read available bodies, and assess every candidate."""
    if type(max_body_attempts) is not int or max_body_attempts < 0:
        raise ValueError("max_body_attempts must be a non-negative integer.")

    batch = collect_media_cloud_candidates(
        job,
        page_size=page_size,
        max_pages=max_pages,
        page_interval_seconds=page_interval_seconds,
    )
    evidence_items = gather_media_cloud_evidence(
        job,
        batch,
        max_body_attempts=max_body_attempts,
    )

    assessments = [
        MediaCloudCandidateAssessment(
            evidence=evidence,
            decision=decide_media_cloud_candidate(
                job.event,
                evidence,
                judge=judge,
            ),
        )
        for evidence in evidence_items
    ]

    return MediaCloudJobRun(
        job=job,
        pages_fetched=batch.pages_fetched,
        next_page_token=batch.next_page_token,
        duplicates_removed=batch.duplicates_removed,
        assessments=assessments,
    )