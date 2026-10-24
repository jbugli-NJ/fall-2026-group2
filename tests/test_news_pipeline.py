"""Offline tests for disaster selection in the news pipeline."""

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest

from monty_tool.event_context import build_event_context
from monty_tool.news import query as news_query_module
from monty_tool.news.pipeline import (
    prepare_media_cloud_jobs,
    prepare_news_jobs,
    select_disaster_records,
)
from monty_tool.news.schemas import MediaCloudPage, MediaCloudStory

import json
from unittest.mock import Mock
from requests.exceptions import Timeout

from monty_tool import news_api
from monty_tool.news import collector
from monty_tool.news.pipeline import NewsCollectionJob
from monty_tool.news.schemas import NewsArticle, NewsQuery, NewsSearchResult
from monty_tool.news.history import load_recent_snapshots, news_job_key
from monty_tool.news import runner
from monty_tool.news import verification
from pathlib import Path
from typing import cast

from monty_tool.boto3_utils.s3_protocols import S3Bucket
from monty_tool.news.s3_storage import (
    save_media_cloud_snapshot,
    read_media_cloud_snapshot,
    upload_media_cloud_snapshot,
    download_news_snapshot,
    news_snapshot_key,
    upload_news_snapshot,
    read_news_snapshot,
    iter_news_snapshots,
)

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


def test_random_selection_calls_shuffle(monkeypatch):
    shuffle = Mock()
    monkeypatch.setattr("monty_tool.news.pipeline.shuffle", shuffle)

    events = select_disaster_records(
        [make_record("event", source_id="a")],
        start_date=date(2026, 9, 1), end_date=date(2026, 9, 10),
        randomize=True,
    )

    shuffle.assert_called_once_with(events)


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
    assert jobs[0].query.query == "Earthquake AND Japan"
    assert jobs[0].query.search_in == "title,description"
    assert jobs[0].query.from_date == date(2026, 9, 9)
    assert jobs[0].query.to_date == date(2026, 9, 13)
    assert event.model_dump() == original


@pytest.mark.parametrize(
    ("hazards", "countries", "expected"),
    [
        (["GH0101"], ["NPL"], "Earthquake AND Nepal"),
        (["EQ"], ["USA"], 'Earthquake AND "United States"'),
        (["EN0205"], ["COD", "ZMB", "COD"], "Wildfires AND (Congo OR Zambia)"),
    ],
)
def test_constructed_search(collection_job, hazards, countries, expected):
    event = collection_job.event
    event.hazard_codes = hazards
    event.country_codes = countries

    query = news_query_module.build_news_query(event)

    assert query.query == expected
    assert query.search_in == "title,description"


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
        "emdat-events": "Earthquake AND Japan",
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
    ["query", "from_date", "to_date", "collection", "page_size", "search_in"],
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
    elif changed_field == "search_in":
        changed.query.search_in = "title,description"
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

def test_media_cloud_query_dates_and_terms(fixed_news_today):
    event = build_event_context(make_record("event-a", source_id="a"))

    query = news_query_module.build_media_cloud_query(event)

    assert query.query == "Earthquake AND Japan AND language:en"
    assert query.from_date == date(2026, 9, 9)
    assert query.to_date == date(2026, 9, 13)


def test_media_cloud_query_rejects_blank_override(fixed_news_today):
    event = build_event_context(make_record("event-a", source_id="a"))

    with pytest.raises(ValueError, match="Search query"):
        news_query_module.build_media_cloud_query(event, search_query="   ")

def test_prepare_media_cloud_jobs_records_search_scope(fixed_news_today):
    event = build_event_context(make_record("event-a", source_id="a"))

    jobs = prepare_media_cloud_jobs(
        [event],
        collection_ids=[456, 123, 456],
    )

    assert len(jobs) == 1
    assert jobs[0].event.model_dump() == event.model_dump()
    assert jobs[0].collection_ids == (123, 456)
    assert jobs[0].query.query == "Earthquake AND Japan AND language:en"
    assert jobs[0].query.from_date == date(2026, 9, 9)
    assert jobs[0].query.to_date == date(2026, 9, 13)

def test_media_cloud_query_can_search_title_only(fixed_news_today):
    event = build_event_context(make_record("event-a", source_id="a"))

    query = news_query_module.build_media_cloud_query(event, title_only=True)

    assert query.query == (
        "article_title:Earthquake AND article_title:Japan AND language:en")

def test_prepare_media_cloud_jobs_rejects_missing_collections():
    with pytest.raises(ValueError, match="collection_ids"):
        prepare_media_cloud_jobs([], collection_ids=[])

def test_media_cloud_collector_pages_and_deduplicates(monkeypatch):
    event = build_event_context(make_record("event", source_id="event"))
    job = prepare_media_cloud_jobs([event], collection_ids=[123])[0]

    search = Mock(
        side_effect=[
            MediaCloudPage(
                stories=[
                    MediaCloudStory(id=1, url="https://example.com/one"),
                    MediaCloudStory(id=2, url="https://example.com/two"),
                ],
                pagination_token="next",
            ),
            MediaCloudPage(
                stories=[
                    MediaCloudStory(id=2, url="https://example.com/two"),
                    MediaCloudStory(id=3, url="https://example.com/three"),
                ],
                pagination_token="more",
            ),
        ]
    )
    monkeypatch.setattr(collector.news_api, "search_media_cloud_page", search)

    result = collector.collect_media_cloud_candidates(
        job, max_pages=2, page_interval_seconds=0
    )

    assert [story.id for story in result.stories] == [1, 2, 3]
    assert result.pages_fetched == 2
    assert result.duplicates_removed == 1
    assert result.next_page_token == "more"
    assert search.call_args_list[1].kwargs["pagination_token"] == "next"


def test_media_cloud_collector_stops_when_no_next_page(monkeypatch):
    event = build_event_context(make_record("event", source_id="event"))
    job = prepare_media_cloud_jobs([event], collection_ids=[123])[0]

    search = Mock(
        return_value=MediaCloudPage(
            stories=[MediaCloudStory(id=1, url="https://example.com/one")],
            pagination_token=None,
        )
    )
    monkeypatch.setattr(collector.news_api, "search_media_cloud_page", search)

    result = collector.collect_media_cloud_candidates(
        job, max_pages=5, page_interval_seconds=0
    )

    assert result.pages_fetched == 1
    assert result.next_page_token is None
    search.assert_called_once()

def test_media_cloud_evidence_keeps_all_candidates(monkeypatch):
    event = build_event_context(make_record("event-a", source_id="a"))
    job = prepare_media_cloud_jobs([event], collection_ids=[123])[0]
    batch = collector.MediaCloudCandidateBatch(
        stories=[
            MediaCloudStory(id=1, url=None),
            MediaCloudStory(id=2, url="https://example.com/two"),
            MediaCloudStory(id=3, url="https://example.com/three"),
            MediaCloudStory(id=4, url="https://example.com/four"),
        ],
        pages_fetched=1,
        next_page_token=None,
        duplicates_removed=0,
    )

    fetch = Mock(
        side_effect=[
            ("<html>article</html>", "https://example.com/two"),
            None,
        ]
    )
    extract = Mock(return_value="Publisher article text")
    monkeypatch.setattr(runner, "fetch_publisher_html", fetch)
    monkeypatch.setattr(runner, "extract_publisher_body", extract)

    evidence = runner.gather_media_cloud_evidence(
        job, batch, max_body_attempts=2
    )

    assert len(evidence) == 4
    assert [item.body_status for item in evidence] == [
        "missing_url",
        "body_available",
        "html_unavailable",
        "not_attempted",
    ]
    assert [item.story.id for item in evidence] == [1, 2, 3, 4]
    assert all(item.event_id == "event-a" for item in evidence)
    assert evidence[1].body_text == "Publisher article text"
    assert "body_text" not in evidence[1].model_dump()
    assert fetch.call_count == 2
    extract.assert_called_once_with(
        "<html>article</html>", url="https://example.com/two"
    )


def test_media_cloud_evidence_keeps_candidate_when_body_extraction_fails(
    monkeypatch,
):
    event = build_event_context(make_record("event-a", source_id="a"))
    job = prepare_media_cloud_jobs([event], collection_ids=[123])[0]
    batch = collector.MediaCloudCandidateBatch(
        stories=[MediaCloudStory(id=1, url="https://example.com/one")],
        pages_fetched=1,
        next_page_token=None,
        duplicates_removed=0,
    )

    monkeypatch.setattr(
        runner,
        "fetch_publisher_html",
        Mock(return_value=("<html></html>", "https://example.com/one")),
    )
    monkeypatch.setattr(
        runner, "extract_publisher_body", Mock(return_value=None)
    )

    evidence = runner.gather_media_cloud_evidence(
        job, batch, max_body_attempts=1
    )

    assert len(evidence) == 1
    assert evidence[0].body_status == "body_unavailable"
    assert evidence[0].story.url == "https://example.com/one"


def test_media_cloud_verification_requires_body_and_real_excerpt():
    event = build_event_context(make_record("event-a", source_id="a"))
    candidate = runner.MediaCloudCandidateEvidence(
        event_id=event.item_id,
        event_collection=event.collection,
        story=MediaCloudStory(
            id=1,
            title="Earthquake in Japan",
            url="https://example.com/one",
        ),
        body_status="body_available",
        body_text="An earthquake struck Japan on September 10.",
    )
    judge = Mock(
        return_value=(
            "same_event",
            "The article describes this event.",
            "earthquake struck Japan on September 10",
        )
    )

    decision = verification.decide_media_cloud_candidate(
        event, candidate, judge=judge
    )
    assert decision.status == "same_event"
    assert decision.event_id == event.item_id
    assert decision.url == candidate.story.url
    judge.assert_called_once_with(event, candidate.story, candidate.body_text)

    unavailable = candidate.model_copy(
        update={"body_status": "html_unavailable", "body_text": None}
    )
    judge.reset_mock()
    decision = verification.decide_media_cloud_candidate(
        event, unavailable, judge=judge
    )
    assert decision.status == "uncertain"
    assert decision.url == candidate.story.url
    judge.assert_not_called()

    judge.return_value = (
        "same_event",
        "The article describes this event.",
        "a sentence that is not in the article",
    )
    decision = verification.decide_media_cloud_candidate(
        event, candidate, judge=judge
    )
    assert decision.status == "uncertain"
    assert decision.evidence_excerpt is None

    wrong_event = candidate.model_copy(update={"event_id": "another-event"})
    with pytest.raises(ValueError, match="identities do not match"):
        verification.decide_media_cloud_candidate(
            event, wrong_event, judge=judge
        )

@pytest.mark.parametrize(
    "status",
    ["same_event", "related_context", "different_event", "uncertain"],
)
def test_qwen_event_judge_returns_all_four_statuses(status):
    event = build_event_context(make_record("event-a", source_id="a"))
    candidate = runner.MediaCloudCandidateEvidence(
        event_id=event.item_id,
        event_collection=event.collection,
        story=MediaCloudStory(
            id=1,
            title="Earthquake in Japan",
            url="https://example.com/one",
        ),
        body_status="body_available",
        body_text="An earthquake struck Japan on September 10.",
    )
    excerpt = (
        None if status == "uncertain"
        else "earthquake struck Japan on September 10"
    )
    assistant = Mock()
    assistant._generate.return_value = {
        "content": json.dumps({
            "status": status,
            "reason": "Test decision based on the article.",
            "evidence_excerpt": excerpt,
        })
    }

    judge = verification.make_qwen_event_judge(assistant)
    decision = verification.decide_media_cloud_candidate(
        event, candidate, judge=judge
    )

    assert decision.status == status
    assert decision.url == candidate.story.url
    assistant._generate.assert_called_once()
    assert assistant._generate.call_args.kwargs["use_tools"] is False


def test_qwen_event_judge_keeps_candidate_when_output_is_invalid():
    event = build_event_context(make_record("event-a", source_id="a"))
    candidate = runner.MediaCloudCandidateEvidence(
        event_id=event.item_id,
        event_collection=event.collection,
        story=MediaCloudStory(id=1, url="https://example.com/one"),
        body_status="body_available",
        body_text="An earthquake struck Japan.",
    )
    assistant = Mock()
    assistant._generate.return_value = {"content": "not JSON"}

    decision = verification.decide_media_cloud_candidate(
        event,
        candidate,
        judge=verification.make_qwen_event_judge(assistant),
    )

    assert decision.status == "uncertain"
    assert decision.url == candidate.story.url

def test_run_media_cloud_job_keeps_all_candidates_and_decisions(monkeypatch):
    event = build_event_context(make_record("event-a", source_id="a"))
    job = prepare_media_cloud_jobs([event], collection_ids=[123])[0]
    first_story = MediaCloudStory(
        id=1,
        title="Earthquake in Japan",
        url="https://example.com/one",
    )
    second_story = MediaCloudStory(
        id=2,
        title="Another article",
        url="https://example.com/two",
    )
    batch = collector.MediaCloudCandidateBatch(
        stories=[first_story, second_story],
        pages_fetched=1,
        next_page_token="next-page",
        duplicates_removed=0,
    )
    evidence = [
        runner.MediaCloudCandidateEvidence(
            event_id=event.item_id,
            event_collection=event.collection,
            story=first_story,
            body_status="body_available",
            body_text="An earthquake struck Japan.",
        ),
        runner.MediaCloudCandidateEvidence(
            event_id=event.item_id,
            event_collection=event.collection,
            story=second_story,
            body_status="html_unavailable",
        ),
    ]

    collect = Mock(return_value=batch)
    gather = Mock(return_value=evidence)
    judge = Mock(
        return_value=(
            "same_event",
            "The article describes the event.",
            "earthquake struck Japan",
        )
    )
    monkeypatch.setattr(runner, "collect_media_cloud_candidates", collect)
    monkeypatch.setattr(runner, "gather_media_cloud_evidence", gather)

    result = runner.run_media_cloud_job(
        job,
        judge=judge,
        max_body_attempts=1,
        max_pages=1,
        page_interval_seconds=0,
    )

    assert result.pages_fetched == 1
    assert result.next_page_token == "next-page"
    assert [item.decision.status for item in result.assessments] == [
        "same_event",
        "uncertain",
    ]
    assert [item.evidence.story.id for item in result.assessments] == [1, 2]
    collect.assert_called_once_with(
        job, page_size=100, max_pages=1, page_interval_seconds=0
    )
    gather.assert_called_once_with(job, batch, max_body_attempts=1)
    judge.assert_called_once_with(
        event, first_story, "An earthquake struck Japan."
    )

def test_media_cloud_snapshot_preserves_evidence(
    tmp_path: Path,
) -> None:
    event = build_event_context(
        make_record("event-a", source_id="a")
    )
    job = prepare_media_cloud_jobs(
        [event],
        collection_ids=[123],
    )[0]

    body = "The earthquake affected communities in Japan."
    assessments: list[runner.MediaCloudCandidateAssessment] = []

    for story_id, text in (
        ("with-body", body),
        ("without-body", None),
    ):
        story = MediaCloudStory(
            id=story_id,
            title="Earthquake report",
            url=f"https://example.com/{story_id}",
        )
        evidence = runner.MediaCloudCandidateEvidence(
            event_id=event.item_id,
            event_collection=event.collection,
            story=story,
            body_status=(
                "body_available"
                if text is not None
                else "html_unavailable"
            ),
            body_text=text,
        )
        decision = verification.EventMatchDecision(
            event_id=event.item_id,
            event_collection=event.collection,
            story_id=story.id,
            url=story.url,
            status="uncertain",
            reason="Storage test; no model judgment performed.",
        )
        assessments.append(
            runner.MediaCloudCandidateAssessment(
                evidence=evidence,
                decision=decision,
            )
        )

    run = runner.MediaCloudJobRun(
        job=job,
        pages_fetched=1,
        next_page_token="next-page",
        duplicates_removed=2,
        assessments=assessments,
    )

    snapshot = save_media_cloud_snapshot(
        run,
        output_dir=tmp_path,
    )
    restored = read_media_cloud_snapshot(snapshot)

    assert snapshot.is_file()
    assert restored.job == job
    assert restored.pages_fetched == 1
    assert restored.next_page_token == "next-page"
    assert restored.duplicates_removed == 2
    assert len(restored.assessments) == 2

    assert restored.assessments[0].evidence.body_text == body
    assert restored.assessments[1].evidence.body_text is None
    assert (
        restored.assessments[1].evidence.story.url
        == "https://example.com/without-body"
    )
    assert (
        restored.assessments[0].decision
        == assessments[0].decision
    )

    bucket = Mock()
    key = upload_media_cloud_snapshot(
        cast(S3Bucket, bucket),
        snapshot,
        prefix="test-owner",
    )

    assert key.startswith(
        "test-owner/media_cloud_articles/snapshots/"
    )
    bucket.upload_file.assert_called_once_with(snapshot, key)

    with pytest.raises(ValueError, match="explicit S3"):
        upload_media_cloud_snapshot(
            cast(S3Bucket, bucket),
            snapshot,
            prefix="",
        )
