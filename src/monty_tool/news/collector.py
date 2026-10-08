"""Collect and save one planned news search."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from uuid import uuid4

from requests.exceptions import RequestException

from monty_tool import news_api
from monty_tool.news.pipeline import NewsCollectionJob
from monty_tool.news.schemas import news_search_parameters


def _save_snapshot(
    output_dir: Path,
    report: dict[str, Any],
) -> Path:
    """Publish a complete JSON file without overwriting earlier runs."""
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"news-{uuid4().hex}.json"
    temporary: Path | None = None

    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_dir,
            prefix=".news-",
            suffix=".tmp",
            delete=False,
        ) as file:
            temporary = Path(file.name)
            json.dump(report, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())

        temporary.replace(destination)
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def collect_news_job(
    job: NewsCollectionJob,
    *,
    output_dir: Path,
    page_size: int = 100,
) -> Path:
    """Collect one search and save its outcome."""
    if type(page_size) is not int or not 1 <= page_size <= 100:
        raise ValueError("page_size must be an integer between 1 and 100.")

    if job.query.item_id != job.event.item_id:
        raise ValueError("The query and event must have the same item_id.")

    if job.query.from_date > job.query.to_date:
        raise ValueError("The query date range is invalid.")

    if not job.query.query.strip() or len(job.query.query) > 500:
        raise ValueError("Search query must contain 1 to 500 characters.")

    output_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "schema_version": 1,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "job": job.model_dump(mode="json"),
        "search_parameters": news_search_parameters(job.query, page_size=page_size),
        "request_attempted": True,
        "status": "error",
        "result": None,
        "error": None,
    }

    try:
        result = news_api.search_news(
            job.query,
            page_size=page_size,
            language="en",
            sort_by="relevancy",
        )
        report["status"] = "ok" if result.articles else "empty"
        report["result"] = result.model_dump(mode="json")

    except (RequestException, RuntimeError, ValueError) as exc:
        # Do not persist potentially sensitive exception messages.
        report["error"] = {"type": type(exc).__name__}

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    return _save_snapshot(output_dir, report)
