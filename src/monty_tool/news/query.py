"""Build provider news queries from Montandon records or shared event context."""

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

def build_media_cloud_query(
    item: MontandonItem | EventContext,
    days_before: int = 1,
    days_after: int = 3,
    *,
    title_only: bool = False,
    language: str | None = "en",
    search_query: str | None = None,
) -> NewsQuery:
    """Build Media Cloud search terms and an inclusive-end date window."""
    for name, value in (
        ("days_before", days_before),
        ("days_after", days_after),
    ):
        if type(value) is not int or value < 0:
            raise ValueError(f"{name} must be a non-negative integer.")
    if type(title_only) is not bool:
        raise ValueError("title_only must be a boolean.")
    if language is not None and (
        len(language) != 2 or not language.isascii() or not language.isalpha()
    ):
        raise ValueError("language must be a two-letter code or None.")

    context = item if isinstance(item, EventContext) else build_event_context(item)
    if context.end_datetime < context.start_datetime:
        raise ValueError("Event end precedes its start.")

    if search_query is None:
        labels = _hazard_labels()
        hazards = {
            labels[code]
            for raw_code in context.hazard_codes
            if (code := HAZARD_CODE_FIXUPS.get(raw_code, raw_code)) in labels
        }

        countries: list[str] = []
        for code in context.country_codes:
            country = pycountry.countries.get(alpha_3=code)
            if country is None:
                break
            name = getattr(country, "common_name", country.name)
            name = name.split(",", 1)[0].strip()
            if not name:
                break
            countries.append(name)

        if hazards and countries and len(countries) == len(context.country_codes):
            def group(names: set[str]) -> str:
                terms = sorted(
                    (
                        f"article_title:{_search_term(name)}"
                        if title_only
                        else _search_term(name)
                    )
                    for name in names
                )
                return terms[0] if len(terms) == 1 else f"({' OR '.join(terms)})"

            query_text = f"{group(hazards)} AND {group(set(countries))}"
        else:
            # Missing code mappings must not prevent all search attempts.
            query_text = _default_search(context)
    else:
        query_text = search_query.strip()

    if not query_text:
        raise ValueError("Search query must contain 1 to 500 characters.")
    if language is not None:
        query_text = f"{query_text} AND language:{language.lower()}"
    if len(query_text) > 500:
        raise ValueError("Search query must contain 1 to 500 characters.")

    from_date = context.start_datetime.date() - timedelta(days=days_before)
    to_date = min(
        context.end_datetime.date() + timedelta(days=days_after),
        date.today(),
    )
    if from_date > to_date:
        raise ValueError("Invalid Media Cloud search period.")

    return NewsQuery(
        item_id=context.item_id,
        query=query_text,
        from_date=from_date,
        to_date=to_date,
    )
