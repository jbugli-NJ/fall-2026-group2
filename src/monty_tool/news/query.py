"""
Build NewsAPI queries from Montandon records or shared event context.
"""

import json
import logging
from datetime import date, timedelta
from functools import lru_cache

import pycountry

from monty_tool.api_schemas import MontandonItem
from monty_tool.event_context import EventContext, build_event_context
from monty_tool.features import HAZARD_CODE_FIXUPS, hazard_code_lookup
from monty_tool.news.schemas import NewsQuery


logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _hazard_labels() -> dict[str, str]:
    """
    Cache canonical hazard labels across the supported code vocabularies.
    """
    return {
        row["hazard_code"]: row["hazard_label"]
        for row in hazard_code_lookup().to_dicts()
    }


def _search_term(name: str) -> str:
    """
    Quote multiword names for use in a Boolean query.
    """
    if any(character.isspace() for character in name) or any(
        character in name for character in '"()'
    ):
        return json.dumps(name, ensure_ascii=False)
    return name


def _default_search(context: EventContext) -> str:
    """
    Resolve hazard and country names, using the stored title as a fallback.
    """
    labels = _hazard_labels()
    hazard = next(
        (
            labels[code]
            for raw_code in context.hazard_codes
            if (code := HAZARD_CODE_FIXUPS.get(raw_code, raw_code)) in labels
        ),
        None,
    )
    countries = []
    reason = "hazard lookup fallback"
    if hazard:
        reason = "country lookup fallback"
        for code in context.country_codes:
            country = pycountry.countries.get(alpha_3=code)
            if country is None:
                break
            name = getattr(country, "common_name", country.name)
            name = name.split(",", 1)[0].strip()
            if not name:
                break
            countries.append(name)

    if hazard and countries and len(countries) == len(context.country_codes):
        terms = sorted({_search_term(name) for name in countries})
        location = terms[0] if len(terms) == 1 else f"({' OR '.join(terms)})"
        return f"{_search_term(hazard)} AND {location}"

    logger.warning(
        "Using stored title for news search %s: %s; hazard_codes=%s; country_codes=%s",
        context.item_id,
        reason,
        context.hazard_codes,
        context.country_codes,
    )
    return context.title.strip()


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

    query_text = (
        _default_search(context)
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
        search_in="title,description",
    )
