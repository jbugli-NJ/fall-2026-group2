"""Run planned news collection jobs sequentially."""

from typing import Literal

from pydantic import BaseModel


from monty_tool.news.body import fetch_publisher_html, extract_publisher_body
from monty_tool.news.collector import (
    MediaCloudCandidateBatch,
    collect_media_cloud_candidates,
)
from monty_tool.news.pipeline import (
    MediaCloudCollectionJob,
)

from monty_tool.news.verification import (
    EventJudge,
    EventMatchDecision,
    MediaCloudCandidateEvidence,
    decide_media_cloud_candidate,
)


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
