"""Offline tests for disaster selection in the news pipeline."""

from copy import deepcopy
from datetime import date
from typing import Any

import pytest

from monty_tool.event_context import build_event_context
from monty_tool.news import query as news_query_module
from monty_tool.news.pipeline import (
    prepare_news_jobs,
    select_disaster_records,
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