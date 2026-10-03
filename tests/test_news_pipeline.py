"""Offline tests for disaster selection in the news pipeline."""

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest

from monty_tool.event_context import build_event_context
from monty_tool.news import query as news_query_module
from monty_tool.news.pipeline import (prepare_news_jobs, select_disaster_records,)

import json
from unittest.mock import Mock
from requests.exceptions import Timeout

from monty_tool import news_api
from monty_tool.news import collector
from monty_tool.news.pipeline import NewsCollectionJob
from monty_tool.news.schemas import NewsArticle, NewsQuery, NewsSearchResult
from monty_tool.news.history import load_recent_snapshots, news_job_key
from monty_tool.news import runner
from monty_tool.news import cli
from pathlib import Path
from typing import cast

from monty_tool.boto3_utils.s3_protocols import S3Bucket
from monty_tool.news.s3_storage import (
    download_news_snapshot,
    news_snapshot_key,
    upload_news_snapshot,
    read_news_snapshot,
    iter_news_snapshots,
)
from monty_tool.network.node_data import news_result_to_node_data

def make_record(
    item_id: str,
    *,
    source_id: str | None,
    episode: int = 1,
    day: int = 10,
    collection: str = "gdacs-events",
) -> dict[str, Any]:
    timestamp = f"2026-09-{day:02d}T00:00:00Z"
    return {
        "id": item_id,
        "collection": collection,
        "bbox": [130.0, 30.0, 140.0, 40.0],
        "geometry": None,
        "links": [],
        "properties": {
            "roles": ["source", "event"],
            "title": "Earthquake in Japan",
            "description": "A test earthquake record.",
            "keywords": ["earthquake"],
            "datetime": timestamp,
            "start_datetime": timestamp,
            "end_datetime": timestamp,
            "monty:corr_id": f"corr-{item_id}",
            "monty:country_codes": ["JPN"],
            "monty:hazard_codes": ["EQ"],
            "monty:src_event_id": source_id,
            "monty:episode_number": episode,
        },
    }


def select(records, *, max_records=5):
    return select_disaster_records(
        records,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 10),
        max_records=max_records,
    )


@pytest.mark.parametrize("reverse_input", [False, True])
def test_groups_episodes_before_applying_limit(reverse_input):
    records = [
        make_record("a-old", source_id="a", episode=2),
        make_record("a-new", source_id="a", episode=10),
        make_record("b", source_id="b", day=9),
    ]
    if reverse_input:
        records.reverse()

    result = select(records, max_records=2)

    assert [event.item_id for event in result] == ["a-new", "b"]


def test_filters_dates_inclusively_and_sorts_newest_first():
    records = [
        make_record("first-day", source_id="a", day=1),
        make_record("outside", source_id="b", day=11),
        make_record("last-day", source_id="c", day=10),
    ]

    result = select(records)

    assert [event.item_id for event in result] == [
        "last-day",
        "first-day",
    ]


@pytest.mark.parametrize("source_id", [None, "", "   "])
def test_missing_source_id_keeps_distinct_records(source_id):
    records = [
        make_record("record-a", source_id=source_id),
        make_record("record-b", source_id=source_id),
    ]

    result = select(records)

    assert {event.item_id for event in result} == {
        "record-a",
        "record-b",
    }


def test_same_source_id_in_different_collections_stays_separate():
    records = [
        make_record("record-a", source_id="123", collection="gdacs-events"),
        make_record("record-b", source_id="123", collection="emdat-events"),
    ]

    assert len(select(records)) == 2


def test_source_id_does_not_collide_with_fallback_record_id():
    records = [
        make_record("record-a", source_id="123"),
        make_record("123", source_id=None),
    ]

    assert len(select(records)) == 2


def test_non_event_records_are_not_selected():
    record = make_record("hazard-record", source_id="a")
    record["properties"]["roles"] = ["source", "hazard"]
    record["properties"]["monty:hazard_detail"] = {
        "estimate_type": "primary",
        "severity_unit": "magnitude",
        "severity_value": 5.0,
    }

    assert select([record]) == []


def test_selection_does_not_modify_input():
    records = [
        make_record("a-old", source_id="a", episode=1),
        make_record("a-new", source_id="a", episode=2),
    ]
    original = deepcopy(records)

    select(records)

    assert records == original


@pytest.mark.parametrize("max_records", [0, -1, True, 1.5])
def test_invalid_record_limit_is_rejected(max_records):
    with pytest.raises(ValueError, match="max_records"):
        select([], max_records=max_records)


def test_reversed_date_range_is_rejected():
    with pytest.raises(ValueError, match="start_date"):
        select_disaster_records(
            [],
            start_date=date(2026, 9, 10),
            end_date=date(2026, 9, 1),
        )


def test_empty_input_returns_empty_selection():
    assert select([]) == []

@pytest.fixture
def fixed_news_today(monkeypatch):
    """Keep date-dependent tests reproducible."""

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 9, 29)

    monkeypatch.setattr(news_query_module, "date", FixedDate)


def test_prepare_jobs_preserves_event_and_builds_query(fixed_news_today):
    event = build_event_context(make_record("event-a", source_id="a"))
    original = event.model_dump()

    jobs = prepare_news_jobs([event])

    assert len(jobs) == 1
    assert jobs[0].event.model_dump() == original
    assert jobs[0].query.item_id == "event-a"
    assert jobs[0].query.query == "Earthquake in Japan"
    assert jobs[0].query.from_date == date(2026, 9, 9)
    assert jobs[0].query.to_date == date(2026, 9, 13)
    assert event.model_dump() == original


def test_query_override_is_scoped_to_collection_and_id(fixed_news_today):
    events = [
        build_event_context(make_record(
            "same-id", source_id="a", collection="gdacs-events",
        )),
        build_event_context(make_record(
            "same-id", source_id="a", collection="emdat-events",
        )),
    ]

    jobs = prepare_news_jobs(
        events,
        query_overrides={
            ("gdacs-events", "same-id"): "Japan AND earthquake",
        },
    )

    queries = {
        job.event.collection: job.query.query
        for job in jobs
    }
    assert queries == {
        "gdacs-events": "Japan AND earthquake",
        "emdat-events": "Earthquake in Japan",
    }


@pytest.mark.parametrize(
    ("days_before", "days_after", "expected_start", "expected_end"),
    [
        (0, 0, date(2026, 9, 10), date(2026, 9, 10)),
        (2, 4, date(2026, 9, 8), date(2026, 9, 14)),
    ],
)
def test_custom_search_window(
    fixed_news_today,
    days_before,
    days_after,
    expected_start,
    expected_end,
):
    event = build_event_context(make_record("event-a", source_id="a"))

    job = prepare_news_jobs(
        [event],
        days_before=days_before,
        days_after=days_after,
    )[0]

    assert job.query.from_date == expected_start
    assert job.query.to_date == expected_end


def test_search_end_is_capped_at_today(fixed_news_today):
    event = build_event_context(
        make_record("event-a", source_id="a", day=29)
    )

    job = prepare_news_jobs([event])[0]

    assert job.query.from_date == date(2026, 9, 28)
    assert job.query.to_date == date(2026, 9, 29)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("days_before", -1),
        ("days_before", True),
        ("days_before", 1.5),
        ("days_after", -1),
        ("days_after", True),
        ("days_after", 1.5),
    ],
)
def test_invalid_day_settings_are_rejected(name, value):
    with pytest.raises(ValueError, match=name):
        prepare_news_jobs([], **{name: value})


@pytest.mark.parametrize("query_text", ["", "   ", "x" * 501])
def test_invalid_query_override_is_rejected(fixed_news_today, query_text):
    event = build_event_context(make_record("event-a", source_id="a"))

    with pytest.raises(ValueError, match="Search query"):
        prepare_news_jobs(
            [event],
            query_overrides={
                ("gdacs-events", "event-a"): query_text,
            },
        )


def test_reversed_event_dates_are_rejected(fixed_news_today):
    record = make_record("event-a", source_id="a")
    record["properties"]["end_datetime"] = "2026-09-09T00:00:00Z"
    event = build_event_context(record)

    with pytest.raises(ValueError, match="Event end precedes"):
        prepare_news_jobs([event])


def test_invalid_search_period_is_rejected(fixed_news_today):
    event = build_event_context(
        make_record("future-event", source_id="a", day=30)
    )

    with pytest.raises(ValueError, match="Invalid news search period"):
        prepare_news_jobs([event], days_before=0)


def test_prepare_jobs_accepts_empty_input():
    assert prepare_news_jobs([]) == []

@pytest.fixture
def collection_job():
    event = build_event_context(
        make_record("test-event", source_id="test-source")
    )
    return NewsCollectionJob(
        event=event,
        query=NewsQuery(
            item_id=event.item_id,
            query="earthquake Japan",
            from_date=date(2026, 9, 9),
            to_date=date(2026, 9, 13),
        ),
    )


def make_search_result(
    job: NewsCollectionJob,
    article_count: int,
) -> NewsSearchResult:
    return NewsSearchResult(
        **job.query.model_dump(),
        total_results=250 if article_count else 0,
        articles=[
            NewsArticle.model_validate({
                "source": {"name": "Test News"},
                "title": f"Earthquake report {index}",
                "description": f"Test description {index}",
                "publishedAt": "2026-09-10T12:00:00Z",
                "url": f"https://example.com/articles/{index}",
            })
            for index in range(article_count)
        ],
    )

def test_news_result_to_node_data_preserves_article_and_source(collection_job):
    result = make_search_result(collection_job, 1)
    s3_uri = f"s3://test-bucket/{news_snapshot_key(collection_job)}"

    rows = news_result_to_node_data(
        collection_job, result, snapshot_s3_uri=s3_uri
    )

    assert rows == [
        {
            "url": "https://example.com/articles/0",
            "title": "Earthquake report 0",
            "description": "Test description 0",
            "source_id": None,
            "source_name": "Test News",
            "published_at": datetime(2026, 9, 10, 12, tzinfo=timezone.utc),
            "event_id": collection_job.event.item_id,
            "snapshot_s3_uri": s3_uri,
        }
    ]


def test_news_result_to_node_data_rejects_different_event(collection_job):
    result = make_search_result(collection_job, 1).model_copy(
        update={"item_id": "different-event"}
    )

    with pytest.raises(ValueError, match="does not match"):
        news_result_to_node_data(
            collection_job,
            result,
            snapshot_s3_uri="s3://test-bucket/news.json",
        )

@pytest.mark.parametrize("article_count", [0, 100])
def test_collection_preserves_complete_result(
    monkeypatch, tmp_path, collection_job, article_count
):
    result = make_search_result(collection_job, article_count)
    search = Mock(return_value=result)
    monkeypatch.setattr(news_api, "search_news", search)

    output = collector.collect_news_job(
        collection_job,
        output_dir=tmp_path / "results",
    )
    report = json.loads(output.read_text(encoding="utf-8"))

    search.assert_called_once_with(
        collection_job.query,
        page_size=100,
        language="en",
        sort_by="relevancy",
    )
    assert report["status"] == ("ok" if article_count else "empty")
    assert report["request_attempted"] is True
    assert report["job"] == collection_job.model_dump(mode="json")
    assert report["result"] == result.model_dump(mode="json")
    assert len(report["result"]["articles"]) == article_count
    assert report["error"] is None
    assert not list(output.parent.glob("*.tmp"))


def test_collector_does_not_enforce_request_limit(
    monkeypatch, tmp_path, collection_job
):
    search = Mock(return_value=make_search_result(collection_job, 1))
    monkeypatch.setattr(news_api, "search_news", search)
    output_dir = tmp_path / "results"

    first = collector.collect_news_job(collection_job, output_dir=output_dir)
    second = collector.collect_news_job(collection_job, output_dir=output_dir)

    assert first != second
    assert len(list(output_dir.glob("*.json"))) == 2
    assert search.call_count == 2


@pytest.mark.parametrize("error_type", [Timeout, RuntimeError, ValueError])
def test_collection_records_search_failure(
    monkeypatch, tmp_path, collection_job, error_type
):
    search = Mock(side_effect=error_type("sensitive-test-message"))
    monkeypatch.setattr(news_api, "search_news", search)
    settings = {
        "output_dir": tmp_path / "results",
    }

    output = collector.collect_news_job(collection_job, **settings)
    contents = output.read_text(encoding="utf-8")
    report = json.loads(contents)

    assert report["status"] == "error"
    assert report["request_attempted"] is True
    assert report["result"] is None
    assert report["error"] == {"type": error_type.__name__}
    assert "sensitive-test-message" not in contents

    search.assert_called_once()


def test_collection_propagates_storage_failure(
    monkeypatch, tmp_path, collection_job
):
    search = Mock(return_value=make_search_result(collection_job, 1))
    monkeypatch.setattr(news_api, "search_news", search)
    monkeypatch.setattr(
        collector.os,
        "fsync",
        Mock(side_effect=OSError("Simulated storage failure")),
    )
    output_dir = tmp_path / "results"

    with pytest.raises(OSError, match="Simulated storage failure"):
        collector.collect_news_job(
            collection_job,
            output_dir=output_dir,
        )

    search.assert_called_once()
    assert not list(output_dir.glob("*.json"))
    assert not list(output_dir.glob("*.tmp"))

@pytest.mark.parametrize("page_size", [0, 101, -1, True, 1.5])
def test_collection_rejects_invalid_page_size_before_request(
    monkeypatch, tmp_path, collection_job, page_size
):
    search = Mock()
    monkeypatch.setattr(news_api, "search_news", search)

    with pytest.raises(ValueError, match="page_size"):
        collector.collect_news_job(
            collection_job,
            output_dir=tmp_path / "results",
            page_size=page_size,
        )

    search.assert_not_called()

@pytest.fixture
def history_now():
    return datetime(2026, 9, 29, 12, tzinfo=timezone.utc)


def write_history_snapshot(path, job, *, status, finished_at):
    """Write a synthetic collection report without calling NewsAPI."""
    result = None
    if status in {"ok", "empty"}:
        result = make_search_result(
            job,
            1 if status == "ok" else 0,
        ).model_dump(mode="json")

    report = {
        "schema_version": 1,
        "job": job.model_dump(mode="json"),
        "search_parameters": {
            "page_size": 100,
            "language": "en",
            "sort_by": "relevancy",
            "page": 1,
        },
        "started_at": finished_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "request_attempted": status != "budget_exhausted",
        "status": status,
        "result": result,
        "error": {"type": "Timeout"} if status == "error" else None,
    }
    path.write_text(json.dumps(report), encoding="utf-8")
    return path

def test_s3_upload_uses_stable_search_key(tmp_path, collection_job):
    snapshot = write_history_snapshot(
        tmp_path / "news-result.json",
        collection_job,
        status="ok",
        finished_at=datetime(2026, 9, 29, tzinfo=timezone.utc),
    )
    bucket = Mock()

    key = upload_news_snapshot(cast(S3Bucket, bucket), snapshot)

    assert key == news_snapshot_key(collection_job)
    bucket.upload_file.assert_called_once_with(snapshot, key)

def test_read_news_snapshot_accepts_matching_key(
    tmp_path, collection_job, history_now
):
    snapshot = write_history_snapshot(
        tmp_path / "news-result.json",
        collection_job,
        status="ok",
        finished_at=history_now,
    )
    key = news_snapshot_key(collection_job)

    job, result = read_news_snapshot(snapshot, key=key)

    assert job == collection_job
    assert result.item_id == collection_job.event.item_id
    assert len(result.articles) == 1


def test_read_news_snapshot_rejects_wrong_key(
    tmp_path, collection_job, history_now
):
    snapshot = write_history_snapshot(
        tmp_path / "news-result.json",
        collection_job,
        status="ok",
        finished_at=history_now,
    )

    with pytest.raises(ValueError, match="Invalid S3 news snapshot"):
        read_news_snapshot(snapshot, key="wrong/by-job/result.json")

def test_iter_news_snapshots_downloads_saved_result(
    tmp_path, collection_job, history_now
):
    snapshot = write_history_snapshot(
        tmp_path / "news-result.json",
        collection_job,
        status="ok",
        finished_at=history_now,
    )
    key = news_snapshot_key(collection_job)
    bucket = Mock()
    bucket.objects.filter.return_value = [Mock(key=key)]
    bucket.download_file.side_effect = (
        lambda _key, filename: Path(filename).write_bytes(snapshot.read_bytes())
    )

    found = list(iter_news_snapshots(cast(S3Bucket, bucket)))

    assert len(found) == 1
    assert found[0][0] == key
    assert found[0][1] == collection_job
    assert len(found[0][2].articles) == 1
    assert bucket.download_file.call_args.args[0] == key

def test_s3_news_snapshot_missing(tmp_path, collection_job):
    bucket = Mock()
    bucket.objects.filter.return_value = []
    key = news_snapshot_key(collection_job)

    found = download_news_snapshot(
        cast(S3Bucket, bucket),
        job=collection_job,
        output_dir=tmp_path,
    )

    assert found is None
    bucket.objects.filter.assert_called_once_with(Prefix=key)
    bucket.download_file.assert_not_called()


def test_s3_news_snapshot_reuses_old_result(tmp_path, collection_job):
    source = write_history_snapshot(
        tmp_path / "source.json",
        collection_job,
        status="ok",
        finished_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    bucket = Mock()
    key = news_snapshot_key(collection_job)
    bucket.objects.filter.return_value = [Mock(key=key)]
    bucket.download_file.side_effect = (
        lambda _key, destination: Path(destination).write_bytes(
            source.read_bytes()
        )
    )

    found = download_news_snapshot(
        cast(S3Bucket, bucket),
        job=collection_job,
        output_dir=tmp_path,
    )

    assert found is not None
    assert found.read_bytes() == source.read_bytes()
    bucket.objects.filter.assert_called_once_with(Prefix=key)
    bucket.download_file.assert_called_once_with(key, found)

def test_s3_news_snapshot_rejects_invalid_json(tmp_path, collection_job):
    bucket = Mock()
    key = news_snapshot_key(collection_job)
    bucket.objects.filter.return_value = [Mock(key=key)]
    bucket.download_file.side_effect = (
        lambda _key, destination: Path(destination).write_text(
            "{invalid", encoding="utf-8"
        )
    )

    with pytest.raises(ValueError, match="Invalid S3 news snapshot"):
        download_news_snapshot(
            cast(S3Bucket, bucket),
            job=collection_job,
            output_dir=tmp_path,
        )


def test_s3_news_snapshot_does_not_hide_permission_error(
    tmp_path, collection_job
):
    bucket = Mock()
    bucket.objects.filter.side_effect = PermissionError("access denied")

    with pytest.raises(PermissionError, match="access denied"):
        download_news_snapshot(
            cast(S3Bucket, bucket),
            job=collection_job,
            output_dir=tmp_path,
        )

    bucket.download_file.assert_not_called()


@pytest.mark.parametrize(
    "changed_field",
    ["query", "from_date", "to_date", "collection", "page_size"],
)
def test_history_key_distinguishes_search_settings(
    collection_job, changed_field
):
    original = news_job_key(collection_job)
    changed = collection_job.model_copy(deep=True)
    page_size = 100

    if changed_field == "query":
        changed.query.query = "earthquake Tokyo"
    elif changed_field == "from_date":
        changed.query.from_date -= timedelta(days=1)
    elif changed_field == "to_date":
        changed.query.to_date += timedelta(days=1)
    elif changed_field == "collection":
        changed.event.collection = "emdat-events"
    else:
        page_size = 20

    assert news_job_key(changed, page_size=page_size) != original
    assert news_job_key(collection_job) == original


@pytest.mark.parametrize(
    ("status", "reusable"),
    [
        ("ok", True),
        ("empty", True),
        ("error", False),
        ("budget_exhausted", False),
    ],
)
def test_history_reuses_only_successful_searches(
    tmp_path, collection_job, history_now, status, reusable
):
    path = write_history_snapshot(
        tmp_path / "news-status.json",
        collection_job,
        status=status,
        finished_at=history_now - timedelta(hours=1),
    )

    recent = load_recent_snapshots(tmp_path, now=history_now)

    expected = {news_job_key(collection_job): path} if reusable else {}
    assert recent == expected
    assert path.exists()


@pytest.mark.parametrize(
    ("age_hours", "reusable"),
    [(23, True), (24, False), (25, False)],
)
def test_history_expires_without_deleting_snapshots(
    tmp_path, collection_job, history_now, age_hours, reusable
):
    path = write_history_snapshot(
        tmp_path / "news-age.json",
        collection_job,
        status="ok",
        finished_at=history_now - timedelta(hours=age_hours),
    )

    recent = load_recent_snapshots(tmp_path, now=history_now)

    assert (news_job_key(collection_job) in recent) is reusable
    assert path.exists()


def test_history_selects_newest_successful_snapshot(
    tmp_path, collection_job, history_now
):
    newest = write_history_snapshot(
        tmp_path / "news-a-newest.json",
        collection_job,
        status="ok",
        finished_at=history_now - timedelta(hours=1),
    )
    older = write_history_snapshot(
        tmp_path / "news-z-older.json",
        collection_job,
        status="ok",
        finished_at=history_now - timedelta(hours=3),
    )

    recent = load_recent_snapshots(tmp_path, now=history_now)

    assert recent == {news_job_key(collection_job): newest}
    assert older.exists()


def test_history_handles_missing_output_directory(tmp_path, history_now):
    recent = load_recent_snapshots(
        tmp_path / "not-created",
        now=history_now,
    )

    assert recent == {}


def test_history_reports_invalid_json(tmp_path, history_now):
    path = tmp_path / "news-broken.json"
    path.write_text("{invalid json", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid news snapshot"):
        load_recent_snapshots(tmp_path, now=history_now)

@pytest.fixture
def fake_collection_search(monkeypatch):
    def respond(query, **kwargs):
        return NewsSearchResult(
            **query.model_dump(),
            total_results=0,
            articles=[],
        )

    search = Mock(side_effect=respond)
    monkeypatch.setattr(news_api, "search_news", search)
    return search


def make_runner_jobs(job, count):
    """Create distinct search jobs from one test event."""
    jobs = []
    for index in range(count):
        copied = job.model_copy(deep=True)
        copied.query.query = f"earthquake Japan report {index}"
        jobs.append(copied)
    return jobs


def test_runner_reuses_results_across_runs(
    tmp_path, collection_job, fake_collection_search
):
    settings = {
        "output_dir": tmp_path / "results",
        "request_limit": 1,
    }
    first = runner.run_news_collection([collection_job], **settings)
    second = runner.run_news_collection([collection_job], **settings)

    assert first.stop_reason == "completed"
    assert len(first.collected) == 1
    assert first.reused == []
    assert second.stop_reason == "completed"
    assert second.collected == []
    assert second.reused == first.collected
    assert fake_collection_search.call_count == 1

def test_runner_reuses_s3_result_without_newsapi(
    monkeypatch, tmp_path, collection_job, fake_collection_search
):
    stored = write_history_snapshot(
        tmp_path / "stored.json",
        collection_job,
        status="ok",
        finished_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    bucket = Mock()
    download = Mock(return_value=stored)
    monkeypatch.setattr(runner, "download_news_snapshot", download)
    output_dir = tmp_path / "results"

    summary = runner.run_news_collection(
        [collection_job],
        output_dir=output_dir,
        request_limit=1,
        s3_bucket=cast(S3Bucket, bucket),
    )

    assert summary.stop_reason == "completed"
    assert summary.collected == []
    assert summary.reused == [stored]
    fake_collection_search.assert_not_called()
    download.assert_called_once_with(
        bucket,
        job=collection_job,
        output_dir=output_dir,
        page_size=100,
    )

def test_runner_request_limit_resets_each_run(
    tmp_path, collection_job, fake_collection_search
):
    jobs = make_runner_jobs(collection_job, 2)
    output_dir = tmp_path / "results"

    first = runner.run_news_collection(
        [jobs[0]],
        output_dir=output_dir,
        request_limit=1,
    )
    second = runner.run_news_collection(
        [jobs[1]],
        output_dir=output_dir,
        request_limit=1,
    )

    assert first.stop_reason == "completed"
    assert second.stop_reason == "completed"
    assert len(first.collected) == 1
    assert len(second.collected) == 1
    assert fake_collection_search.call_count == 2

def test_runner_skips_duplicates_within_one_run(
    tmp_path, collection_job, fake_collection_search
):
    summary = runner.run_news_collection(
        [collection_job, collection_job],
        output_dir=tmp_path / "results",
        request_limit=1,
    )

    assert summary.stop_reason == "completed"
    assert len(summary.collected) == 1
    assert summary.reused == summary.collected
    assert fake_collection_search.call_count == 1

def test_runner_stops_at_per_run_request_limit(
    tmp_path, collection_job, fake_collection_search
):
    jobs = make_runner_jobs(collection_job, 3)
    output_dir = tmp_path / "results"

    summary = runner.run_news_collection(
        jobs,
        output_dir=output_dir,
        request_limit=1,
    )

    assert summary.stop_reason == "budget_exhausted"
    assert len(summary.collected) == 1
    assert summary.stop_report is None
    assert fake_collection_search.call_count == 1
    assert len(list(output_dir.glob("news-*.json"))) == 1

@pytest.mark.parametrize("error_type", [Timeout, RuntimeError])
def test_runner_stops_on_search_error(
    tmp_path, collection_job, fake_collection_search, error_type
):
    jobs = make_runner_jobs(collection_job, 3)
    fake_collection_search.side_effect = error_type("Test failure")

    summary = runner.run_news_collection(
        jobs,
        output_dir=tmp_path / "results",
        request_limit=3,
    )

    assert summary.stop_reason == "error"
    assert summary.collected == []
    assert summary.stop_report is not None
    assert fake_collection_search.call_count == 1

    report = json.loads(
        summary.stop_report.read_text(encoding="utf-8")
    )
    assert report["status"] == "error"
    assert report["error"] == {"type": error_type.__name__}


def test_runner_refreshes_expired_snapshot(
    tmp_path, collection_job, fake_collection_search
):
    output_dir = tmp_path / "results"
    output_dir.mkdir()
    old_path = write_history_snapshot(
        output_dir / "news-old.json",
        collection_job,
        status="empty",
        finished_at=datetime.now(timezone.utc) - timedelta(hours=25),
    )

    summary = runner.run_news_collection(
        [collection_job],
        output_dir=output_dir,
        request_limit=1,
    )

    assert summary.stop_reason == "completed"
    assert len(summary.collected) == 1
    assert summary.reused == []
    assert fake_collection_search.call_count == 1
    assert old_path.exists()
    assert summary.collected[0] != old_path


def test_runner_propagates_storage_failure(
    monkeypatch, tmp_path, collection_job, fake_collection_search
):
    collect = Mock(side_effect=OSError("Test storage failure"))
    monkeypatch.setattr(runner, "collect_news_job", collect)

    with pytest.raises(OSError, match="Test storage failure"):
        runner.run_news_collection(
            make_runner_jobs(collection_job, 3),
            output_dir=tmp_path / "results",
            request_limit=3,
        )

    assert collect.call_count == 1
    fake_collection_search.assert_not_called()


def test_runner_handles_empty_job_list(tmp_path, fake_collection_search):
    summary = runner.run_news_collection(
        [],
        output_dir=tmp_path / "results",
        request_limit=1,
    )

    assert summary.stop_reason == "completed"
    assert summary.collected == []
    assert summary.reused == []
    assert summary.stop_report is None
    fake_collection_search.assert_not_called()

@pytest.fixture
def cli_environment(monkeypatch, tmp_path, fixed_news_today):
    load = Mock(return_value=[
        make_record("cli-event", source_id="cli-source")
    ])
    run = Mock(side_effect=AssertionError("Unexpected collection run"))
    search = Mock(side_effect=AssertionError("Unexpected NewsAPI call"))

    monkeypatch.setattr(cli, "load_collection", load)
    monkeypatch.setattr(cli, "run_news_collection", run)
    monkeypatch.setattr(news_api, "search_news", search)

    arguments = [
        "--collection", "gdacs-events",
        "--cache-dir", str(tmp_path / "raw"),
        "--start-date", "2026-09-01",
        "--end-date", "2026-09-29",
        "--max-records", "1",
        "--output-dir", str(tmp_path / "results"),
    ]
    return arguments, load, run, search


def test_cli_dry_run_does_not_collect_or_write(
    cli_environment, tmp_path, capsys
):
    arguments, load, run, search = cli_environment

    exit_code = cli.main([*arguments, "--dry-run"])
    plan = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert plan["mode"] == "dry_run"
    assert plan["planned_jobs"] == 1
    assert plan["news_api_requests_made"] == 0
    assert plan["jobs"][0]["item_id"] == "cli-event"
    assert plan["jobs"][0]["from_date"] == "2026-09-09"
    assert plan["jobs"][0]["to_date"] == "2026-09-13"

    load.assert_called_once()
    run.assert_not_called()
    search.assert_not_called()
    assert list(tmp_path.iterdir()) == []


def test_cli_execute_requires_explicit_budget(cli_environment):
    arguments, load, run, search = cli_environment

    with pytest.raises(SystemExit) as error:
        cli.main([*arguments, "--execute"])

    assert error.value.code == 2
    load.assert_not_called()
    run.assert_not_called()
    search.assert_not_called()


def test_cli_requires_explicit_mode(cli_environment):
    arguments, load, run, search = cli_environment

    with pytest.raises(SystemExit) as error:
        cli.main(arguments)

    assert error.value.code == 2
    load.assert_not_called()
    run.assert_not_called()
    search.assert_not_called()


@pytest.mark.parametrize(
    ("stop_reason", "expected_exit"),
    [
        ("completed", 0),
        ("budget_exhausted", 0),
        ("error", 1),
    ],
)
def test_cli_passes_execution_settings_and_reports_status(
    cli_environment, tmp_path, capsys, stop_reason, expected_exit
):
    arguments, load, run, search = cli_environment
    run.side_effect = None
    run.return_value = runner.NewsCollectionRun(stop_reason=stop_reason)

    exit_code = cli.main([
        *arguments,
        "--execute",
        "--request-limit", "2",
        "--page-size", "50",
        "--refresh-hours", "12",
    ])

    assert exit_code == expected_exit
    run.assert_called_once()

    jobs = run.call_args.args[0]
    assert len(jobs) == 1
    assert jobs[0].event.item_id == "cli-event"
    assert run.call_args.kwargs == {
        "output_dir": tmp_path / "results",
        "request_limit": 2,
        "page_size": 50,
        "refresh_after": timedelta(hours=12),
        "s3_bucket": None,
    }

    output = json.loads(capsys.readouterr().out)
    assert output["stop_reason"] == stop_reason
    search.assert_not_called()


def test_cli_rejects_oversized_article_request(cli_environment):
    arguments, load, run, search = cli_environment

    with pytest.raises(SystemExit) as error:
        cli.main([*arguments, "--dry-run", "--page-size", "101"])

    assert error.value.code == 2
    load.assert_not_called()
    run.assert_not_called()
    search.assert_not_called()


def test_cli_supports_no_geometry_cache(
    cli_environment, tmp_path, capsys
):
    arguments, load, run, search = cli_environment

    assert cli.main([*arguments, "--dry-run", "--no-geometry"]) == 0

    load.assert_called_once_with(
        "gdacs-events",
        cache_dir=tmp_path / "raw",
        geometry=False,
    )
    run.assert_not_called()
    search.assert_not_called()
    capsys.readouterr()

def test_cli_dry_run_uses_s3_source(cli_environment, monkeypatch, capsys):
    arguments, load, run, search = cli_environment
    bucket = Mock()
    get_bucket = Mock(return_value=bucket)
    download = Mock()

    monkeypatch.setattr(cli, "get_env_bucket_name", lambda: "test-bucket")
    monkeypatch.setattr(cli, "get_bucket", get_bucket)
    monkeypatch.setattr(cli, "download_collection_cache", download)

    source_key = "aidan.carlisle@gwu.edu/raw/gdacs-events.jsonl.gz"
    exit_code = cli.main([
        *arguments, "--dry-run", "--s3-source-key", source_key,
    ])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["planned_jobs"] == 1
    get_bucket.assert_called_once_with("test-bucket")
    download.assert_called_once()
    assert download.call_args.args == (bucket,)
    assert download.call_args.kwargs["source_key"] == source_key

    cache_dir = download.call_args.kwargs["cache_dir"]
    load.assert_called_once_with(
        "gdacs-events", cache_dir=cache_dir, geometry=True,
    )
    assert not cache_dir.exists()
    run.assert_not_called()
    search.assert_not_called()

def test_cli_execute_uploads_news_snapshots(
    cli_environment, monkeypatch, tmp_path, capsys
):
    arguments, load, run, search = cli_environment
    bucket = Mock()
    get_bucket = Mock(return_value=bucket)
    upload = Mock()
    new = tmp_path / "new.json"
    reused = tmp_path / "reused.json"

    run.side_effect = None
    run.return_value = runner.NewsCollectionRun(
        collected=[new],
        reused=[reused],
    )
    monkeypatch.setattr(cli, "get_env_bucket_name", lambda: "test-bucket")
    monkeypatch.setattr(cli, "get_bucket", get_bucket)
    monkeypatch.setattr(cli, "upload_news_snapshot", upload)

    assert cli.main([
        *arguments, "--execute", "--request-limit", "2", "--s3-upload",
    ]) == 0

    get_bucket.assert_called_once_with("test-bucket")
    assert run.call_args.kwargs["s3_bucket"] is bucket
    assert [item.args for item in upload.call_args_list] == [
        (bucket, new),
        (bucket, reused),
    ]
    run.assert_called_once()
    search.assert_not_called()
    capsys.readouterr()

def test_cli_reuses_s3_result_with_fresh_output_dir(
    monkeypatch, tmp_path, capsys, fixed_news_today
):
    objects: dict[str, bytes] = {}
    bucket = Mock()
    bucket.objects.filter.side_effect = lambda Prefix: [
        Mock(key=key) for key in objects if key.startswith(Prefix)
    ]
    bucket.upload_file.side_effect = lambda filename, key: (
        objects.__setitem__(key, Path(filename).read_bytes())
    )
    bucket.download_file.side_effect = lambda key, filename: (
        Path(filename).write_bytes(objects[key])
    )

    monkeypatch.setattr(cli, "get_env_bucket_name", lambda: "test-bucket")
    monkeypatch.setattr(cli, "get_bucket", lambda _name: bucket)
    monkeypatch.setattr(
        cli,
        "load_collection",
        lambda *_args, **_kwargs: [
            make_record("cli-event", source_id="cli-source")
        ],
    )
    search = Mock(
        side_effect=lambda query, **_kwargs: NewsSearchResult(
            **query.model_dump(),
            total_results=0,
            articles=[],
        )
    )
    monkeypatch.setattr(news_api, "search_news", search)

    arguments = [
        "--execute", "--s3-upload",
        "--collection", "gdacs-events",
        "--start-date", "2026-09-01",
        "--end-date", "2026-09-29",
        "--max-records", "1",
        "--request-limit", "1",
    ]

    assert cli.main([
        *arguments, "--output-dir", str(tmp_path / "first")
    ]) == 0
    first = json.loads(capsys.readouterr().out)
    assert len(first["collected"]) == 1
    assert first["reused"] == []
    assert search.call_count == 1
    assert len(objects) == 1

    assert cli.main([
        *arguments, "--output-dir", str(tmp_path / "second")
    ]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["collected"] == []
    assert len(second["reused"]) == 1
    assert search.call_count == 1
    assert len(objects) == 1
