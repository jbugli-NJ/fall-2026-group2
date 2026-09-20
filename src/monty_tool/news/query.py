"""Build NewsAPI queries from Montandon records or shared event context."""

import re
from datetime import date, timedelta

from monty_tool.api_schemas import MontandonItem
from monty_tool.event_context import EventContext, build_event_context
from monty_tool.news.schemas import NewsQuery


def _clean_title(title: str) -> str:
    """Remove a trailing year and unnecessary whitespace."""
    title = title.strip()
    title = re.sub(r",?\s*20\d{2}\s*$", "", title)
    return title.strip()


def build_news_query(
    item: MontandonItem | EventContext,
    days_before: int = 1,
    days_after: int = 3,
    *,
    search_query: str | None = None,
) -> NewsQuery:
    """Build a news query using the supplied keywords and event dates."""

    if isinstance(item, EventContext):
        context = item
    else:
        context = build_event_context(item)

    # Run this for both input types, outside the if/else block.
    query_text = (
        _clean_title(context.title)
        if search_query is None
        else search_query.strip()
    )

    if not query_text or len(query_text) > 500:
        raise ValueError(
            "Search query must contain 1 to 500 characters."
        )

    from_date = (
        context.start_datetime.date()
        - timedelta(days=days_before)
    )

    to_date = min(
        context.end_datetime.date() + timedelta(days=days_after),
        date.today(),
    )

    return NewsQuery(
        item_id=context.item_id,
        query=query_text,
        from_date=from_date,
        to_date=to_date,
    )