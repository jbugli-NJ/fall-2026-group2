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
    state_path: Path,
    request_limit: int,
    page_size: int = 100,
    refresh_after: timedelta = timedelta(hours=24),
) -> NewsCollectionRun:
    """Collect missing or expired searches within a shared request budget.

    Run only one collection worker for a given output directory at a time.
    Storage failures propagate to the caller.
    """
    if type(request_limit) is not int or request_limit < 1:
        raise ValueError("request_limit must be a positive integer.")

    if type(page_size) is not int or not 1 <= page_size <= 100:
        raise ValueError("page_size must be an integer between 1 and 100.")

    recent = load_recent_snapshots(
        output_dir,
        refresh_after=refresh_after,
    )
    summary = NewsCollectionRun()

    for job in jobs:
        key = news_job_key(job, page_size=page_size)

        if key in recent:
            summary.reused.append(recent[key])
            continue

        output = collect_news_job(
            job,
            output_dir=output_dir,
            state_path=state_path,
            request_limit=request_limit,
            page_size=page_size,
        )
        report = json.loads(output.read_text(encoding="utf-8"))
        status = report["status"]

        if status in {"ok", "empty"}:
            summary.collected.append(output)
            # Also prevent duplicate searches within this run.
            recent[key] = output
            continue

        if status == "budget_exhausted":
            summary.stop_reason = "budget_exhausted"
        elif status == "error":
            summary.stop_reason = "error"
        else:
            raise ValueError(f"Unexpected collection status: {status!r}")

        summary.stop_report = output
        break

    return summary
