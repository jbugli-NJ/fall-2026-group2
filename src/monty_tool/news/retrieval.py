"""Retrieve and rank news candidates for assistant tool responses."""

import logging

from monty_tool import news_api
from monty_tool.news.schemas import NewsQuery, NewsSearchResult
from monty_tool.event_context import EventContext
from monty_tool.news.relevance import assess_article


logger = logging.getLogger(__name__)


def search_ranked_news(
    news_query: NewsQuery,
    *,
    event_context: EventContext | None = None,
) -> NewsSearchResult:
    """Fetch up to 20 candidates and return up to five in relevance order.

    Search failures propagate to the caller's existing error handling.
    Ranking failures retain the original API order. Total results remains
    the API's match count, not a count of verified relevant articles.
    """
    result = news_api.search_news(news_query, page_size=20)
    articles = result.articles

    if event_context is not None:
        articles = [
            article
            for article in articles
            if assess_article(article, event_context).strong_match
        ]
    if len(articles) > 1:
        try:
            from monty_tool.news.ranking import rank_news_articles

            articles = rank_news_articles(articles, reference_text=news_query.query)
        except Exception as exc:
            logger.warning(
                "News ranking failed (%s); keeping NewsAPI order.",
                type(exc).__name__,
            )
    if event_context is not None:
        articles = sorted(
            articles,
            key=lambda article: assess_article(
                article, event_context
            ).country_match,
            reverse=True,
        )

    return result.model_copy(update={"articles": articles[:5]})
