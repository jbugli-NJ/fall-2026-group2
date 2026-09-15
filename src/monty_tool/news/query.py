"""
Utilities to build NewsAPI search queries from Montandon records.
"""

import re
from datetime import date, timedelta

from monty_tool.api_schemas import MontandonItem
from monty_tool.news.schemas import NewsQuery


def _clean_title(title: str) -> str:
    """
    Remove trailing years and unnecessary whitespace from event titles.
    """
    title = title.strip()
    title = re.sub(r",?\s*20\d{2}\s*$", "", title)
    return title.strip()


def build_news_query(
    item: MontandonItem,
    days_before: int = 1,
    days_after: int = 3,
) -> NewsQuery:
    """
    Build NewsAPI search parameters from a Montandon event.
    """
    properties = item.properties

    query = _clean_title(properties.title)

    from_date = (
        properties.start_datetime.date()
        - timedelta(days=days_before)
    )

    to_date = min(
        properties.end_datetime.date() + timedelta(days=days_after),
        date.today(),
    )

    return NewsQuery(
        item_id=item.id,
        query=query,
        from_date=from_date,
        to_date=to_date,
    )