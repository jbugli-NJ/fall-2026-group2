"""
Minimal NewsAPI.org client.

This file handles the NewsAPI connection and validated responses.
It does not inspect or transform Montandon records directly.

Receives a prepared NewsQuery and handles the NewsAPI request.
"""

from __future__ import annotations

import os

import requests
from dotenv import load_dotenv

from monty_tool.news.schemas import (
    NewsAPIResponse,
    NewsQuery,
    NewsSearchResult,
)


NEWS_API_URL = "https://newsapi.org/v2/everything"


def get_news_api_key() -> str:
    """Load NEWS_API_KEY from a local .env file."""
    load_dotenv()

    api_key = os.getenv("NEWS_API_KEY")

    if not api_key:
        raise RuntimeError(
            "NEWS_API_KEY is missing. "
            "Add NEWS_API_KEY=your_key to your local .env file."
        )

    return api_key


def search_news(
    news_query: NewsQuery,
    *,
    language: str = "en",
    page_size: int = 20,
    sort_by: str = "relevancy",
) -> NewsSearchResult:
    """
    Search NewsAPI using prepared search parameters.
    """

    params = {
        "q": news_query.query,
        "from": news_query.from_date.isoformat(),
        "to": news_query.to_date.isoformat(),
        "language": language,
        "sortBy": sort_by,
        "pageSize": page_size,
        "page": 1,
    }

    response = requests.get(
        NEWS_API_URL,
        params=params,
        headers={"X-Api-Key": get_news_api_key()},
        timeout=30,
    )

    payload = response.json()

    if response.status_code != 200 or payload.get("status") != "ok":
        raise RuntimeError(
            f"NewsAPI failed: "
            f"{payload.get('code')} - {payload.get('message')}"
        )

    validated_response = NewsAPIResponse.model_validate(payload)

    return NewsSearchResult(
        item_id=news_query.item_id,
        query=news_query.query,
        from_date=news_query.from_date,
        to_date=news_query.to_date,
        total_results=validated_response.total_results,
        articles=validated_response.articles,
    )