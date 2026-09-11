"""
Minimal NewsAPI.org client.

This file only handles the NewsAPI connection.
It does NOT know anything about Montandon or data cleaning.

Later, pass it a prepared query and date range.
"""

from __future__ import annotations

import os
from datetime import date

import requests
from dotenv import load_dotenv


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
    query: str,
    from_date: str | date,
    to_date: str | date,
    *,
    language: str = "en",
    page_size: int = 20,
    sort_by: str = "relevancy",
) -> dict:
    """
    Search NewsAPI.org.

    Example:
        result = search_news(
            query='"Flood" AND ("Pakistan" OR "Sindh")',
            from_date="2026-08-01",
            to_date="2026-08-08",
        )
    """
    if isinstance(from_date, date):
        from_date = from_date.isoformat()

    if isinstance(to_date, date):
        to_date = to_date.isoformat()

    params = {
        "q": query,
        "from": from_date,
        "to": to_date,
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

    articles = []

    for article in payload.get("articles", []):
        source = article.get("source") or {}

        articles.append(
            {
                "source": source.get("name"),
                "title": article.get("title"),
                "description": article.get("description"),
                "published_at": article.get("publishedAt"),
                "url": article.get("url"),
            }
        )

    return {
        "query": query,
        "from_date": from_date,
        "to_date": to_date,
        "total_results": payload.get("totalResults", 0),
        "articles": articles,
    }
