"""Find country and hazard evidence in news article metadata."""

import re
from dataclasses import dataclass
from functools import lru_cache

import pycountry

from monty_tool.event_context import EventContext
from monty_tool.features import hazard_code_lookup
from monty_tool.news.schemas import NewsArticle


_HAZARD_ALIASES = {
    "EQ": ("earthquake", "earthquakes", "quake", "quakes"),
    "FL": ("flood", "floods", "flooding"),
    "WF": ("wildfire", "wildfires", "bushfire", "bushfires"),
}

_COUNTRY_ALIASES = {
    "USA": ("United States", "U.S."),
    "GBR": ("United Kingdom", "Britain"),
}


@dataclass(frozen=True)
class ArticleEvidence:
    country_match: bool
    hazard_match: bool

    @property
    def match_count(self) -> int:
        return int(self.country_match) + int(self.hazard_match)


@lru_cache(maxsize=1)
def _hazard_labels() -> dict[str, str]:
    rows = hazard_code_lookup().select(
        "hazard_code", "hazard_label"
    ).iter_rows()
    return {str(code): str(label) for code, label in rows}


def _mentions_any(text: str, terms: set[str]) -> bool:
    return any(
        re.search(
            rf"(?<!\w){re.escape(term)}(?!\w)",
            text,
            flags=re.IGNORECASE,
        ) is not None
        for term in terms
        if term.strip()
    )


def assess_article(
    article: NewsArticle,
    event: EventContext,
) -> ArticleEvidence:
    """Check article title and description against one disaster record."""
    text = f"{article.title} {article.description or ''}"

    country_terms: set[str] = set()
    for code in event.country_codes:
        country = pycountry.countries.get(alpha_3=code.upper())
        if country is not None:
            country_terms.add(country.name)
            common_name = getattr(country, "common_name", None)
            if common_name:
                country_terms.add(common_name)
        country_terms.update(_COUNTRY_ALIASES.get(code.upper(), ()))

    hazard_terms: set[str] = set()
    labels = _hazard_labels()
    for code in event.hazard_codes:
        if label := labels.get(code):
            hazard_terms.add(label)
        hazard_terms.update(_HAZARD_ALIASES.get(code, ()))

    return ArticleEvidence(
        country_match=_mentions_any(text, country_terms),
        hazard_match=_mentions_any(text, hazard_terms),
    )
