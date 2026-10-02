"""Run planned news collection jobs sequentially."""

import json
from collections.abc import Iterable
from datetime import timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from monty_tool.news.collector import collect_news_job
from monty_tool.news.history import load_recent_snapshots, news_job_key
from monty_tool.news.pipeline import NewsCollectionJob
from monty_tool.boto3_utils.s3_protocols import S3Bucket
from monty_tool.news.s3_storage import download_news_snapshot


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