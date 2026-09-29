"""Prepare disaster records for scheduled news collection."""

from collections.abc import Iterable
from datetime import date
from typing import Any

from monty_tool.api_schemas import MontandonItem
from monty_tool.event_context import EventContext, build_event_context


def select_disaster_records(
    records: Iterable[dict[str, Any]],
    *,
    start_date: date,
    end_date: date,
    max_records: int = 5,
) -> list[EventContext]:
    """Select one representative per source event within a start-date range."""
    if start_date > end_date:
        raise ValueError("start_date must not be later than end_date.")

    if type(max_records) is not int or max_records < 1:
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
    return selected[:max_records]