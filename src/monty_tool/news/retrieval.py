"""Retrieve and rank news candidates for assistant tool responses."""

import logging
from dataclasses import dataclass

from monty_tool import news_api
from monty_tool.news.schemas import NewsQuery, NewsSearchResult
from monty_tool.event_context import EventContext


logger = logging.getLogger(__name__)
@dataclass(frozen=True)
class NewsCandidateBatch:
    """Keep fetched candidates separate from the LLM shortlist."""

    retrieved: NewsSearchResult
    for_llm: NewsSearchResult


def collect_ranked_news(
    news_query: NewsQuery,
    *,
    event_context: EventContext | None = None,
    candidate_limit: int = 100,
    llm_limit: int = 20,
) -> NewsCandidateBatch:
    """Fetch candidates and return both the original result and an LLM shortlist.

    Defaults to fetching up to 100 articles and forwarding up to 20.
    Search failures propagate to the caller.
    Ranking failures retain the original API order.
    total_results remains the API's reported match count.
    """
    if type(candidate_limit) is not int or not 1 <= candidate_limit <= 100:
        raise ValueError("candidate_limit must be an integer between 1 and 100.")

    if type(llm_limit) is not int or not 1 <= llm_limit <= candidate_limit:
        raise ValueError(
            "llm_limit must be an integer between 1 and candidate_limit."
        )

    result = news_api.search_news(news_query, page_size=candidate_limit)
    articles = result.articles
    if len(articles) > 1:
        try:
            from monty_tool.news.ranking import rank_news_articles

            articles = rank_news_articles(articles, reference_text=news_query.query)
        except Exception as exc:
            logger.warning(
                "News ranking failed (%s); keeping NewsAPI order.",
                type(exc).__name__,
            )
    return NewsCandidateBatch(
        retrieved=result,
        for_llm=result.model_copy(
            update={"articles": articles[:llm_limit]},
        ),
    )

def search_ranked_news(
    news_query: NewsQuery,
    *,
    event_context: EventContext | None = None,
) -> NewsSearchResult:
    """Return only the shortlist expected by existing assistant tools."""
    batch = collect_ranked_news(
        news_query,
        event_context=event_context,
    )
    return batch.for_llm
