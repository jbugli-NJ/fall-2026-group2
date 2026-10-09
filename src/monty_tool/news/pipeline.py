"""Prepare disaster records for scheduled news collection."""

from collections.abc import Iterable, Mapping
from datetime import date
from random import shuffle
from typing import Any

from monty_tool.api_schemas import MontandonItem
from monty_tool.event_context import EventContext, build_event_context
from pydantic import BaseModel

from monty_tool.news.query import build_news_query
from monty_tool.news.schemas import NewsQuery

def select_disaster_records(
    records: Iterable[dict[str, Any]],
    *,
    start_date: date,
    end_date: date,
    max_records: int | None = None,
    randomize: bool = False,
) -> list[EventContext]:
    """
    Select event representatives in date or random order with an optional limit.
    """
    if start_date > end_date:
        raise ValueError("start_date must not be later than end_date.")

    if max_records is not None and (type(max_records) is not int or max_records < 1):
        raise ValueError("max_records must be a positive integer.")

    representatives: dict[
        tuple[str, str, str],
        tuple[int, EventContext],
    ] = {}

    for record in records:
        item = MontandonItem.model_validate(record)
        event = build_event_context(item)

        if "event" not in event.roles:
            continue

        event_date = event.start_datetime.date()
        if not start_date <= event_date <= end_date:
            continue

        source_id = (item.properties.monty_src_event_id or "").strip()

        if source_id:
            identity = (event.collection, "source", source_id)
        else:
            identity = (event.collection, "record", event.item_id)

        episode = item.properties.monty_episode_number
        previous = representatives.get(identity)

        # Prefer the highest episode number; break ties by record ID.
        # This is a selection policy, not a verified update timestamp.
        if previous is None or (
            episode,
            event.item_id,
        ) > (
            previous[0],
            previous[1].item_id,
        ):
            representatives[identity] = (episode, event)

    selected = [
        event
        for _, event in representatives.values()
    ]
    selected.sort(
        key=lambda event: (
            event.start_datetime,
            event.collection,
            event.item_id,
        ),
        reverse=True,
    )
    if randomize:
        shuffle(selected)
    return selected[:max_records]

class NewsCollectionJob(BaseModel):
    """Keep a source event together with its planned news query."""

    event: EventContext
    query: NewsQuery


def prepare_news_jobs(
    events: Iterable[EventContext],
    *,
    days_before: int = 1,
    days_after: int = 3,
    query_overrides: Mapping[tuple[str, str], str] | None = None,
) -> list[NewsCollectionJob]:
    """Prepare collection jobs without calling NewsAPI."""
    for name, value in (
        ("days_before", days_before),
        ("days_after", days_after),
    ):
        if type(value) is not int or value < 0:
            raise ValueError(f"{name} must be a non-negative integer.")

    overrides = query_overrides if query_overrides is not None else {}
    jobs: list[NewsCollectionJob] = []

    for event in events:
        if event.end_datetime < event.start_datetime:
            raise ValueError(
                f"Event end precedes its start: {event.item_id!r}."
            )

        identity = (event.collection, event.item_id)
        query = build_news_query(
            event,
            days_before=days_before,
            days_after=days_after,
            search_query=overrides.get(identity),
        )

        if query.from_date > query.to_date:
            raise ValueError(
                f"Invalid news search period for event {event.item_id!r}."
            )

        jobs.append(NewsCollectionJob(event=event, query=query))

    return jobs
