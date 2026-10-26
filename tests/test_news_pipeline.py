"""Offline tests for disaster selection in the news pipeline."""

from copy import deepcopy
from datetime import date
import json
from pathlib import Path
from typing import Any
from typing import cast
from unittest.mock import Mock

import pytest

from monty_tool.event_context import build_event_context
from monty_tool.news import query as news_query_module
from monty_tool.news.pipeline import (
    prepare_media_cloud_jobs,
    select_disaster_records,
)
from monty_tool.news.schemas import MediaCloudPage, MediaCloudStory
from monty_tool.news import collector
from monty_tool.news import runner
from monty_tool.news import verification

from monty_tool.boto3_utils.s3_protocols import S3Bucket
from monty_tool.news.s3_storage import (
    save_media_cloud_snapshot,
    read_media_cloud_snapshot,
    upload_media_cloud_snapshot,
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
    monkeypatch.setattr(collector.media_cloud_api, "search_media_cloud_page", search)

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
    monkeypatch.setattr(collector.media_cloud_api, "search_media_cloud_page", search)

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
    assistant = Mock()
    assistant._generate.return_value = {
        "content": json.dumps({
            "status": status,
            "reason": "Test decision based on the article.",
            "evidence_id": None if status == "uncertain" else 1,
        })
    }

    judge = verification.make_qwen_event_judge(assistant)
    decision = verification.decide_media_cloud_candidate(
        event, candidate, judge=judge
    )

    assert decision.status == status
    if status == "uncertain":
        assert decision.evidence_excerpt is None
    else:
        assert decision.evidence_excerpt == candidate.body_text
    assert decision.url == candidate.story.url
    assistant._generate.assert_called_once()
    assert assistant._generate.call_args.kwargs["use_tools"] is False

def test_qwen_event_judge_rejects_unknown_passage_id():
    event = build_event_context(make_record("event-a", source_id="a"))
    story = MediaCloudStory(id=1, url="https://example.com/one")
    assistant = Mock()
    assistant._generate.return_value = {
        "content": json.dumps({
            "status": "same_event",
            "reason": "Test reason.",
            "evidence_id": 999,
        })
    }

    judge = verification.make_qwen_event_judge(assistant)
    status, reason, excerpt = judge(
        event, story, "An earthquake struck Japan."
    )

    assert status == "uncertain"
    assert reason == "The model did not select a valid article passage."
    assert excerpt is None

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
