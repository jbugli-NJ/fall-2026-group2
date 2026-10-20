"""
News provider clients.

Media Cloud supports the new collection pipeline.
The existing NewsAPI client remains until its callers are migrated.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import date

import requests
from dotenv import load_dotenv

from monty_tool.news.schemas import (
    MediaCloudPage,
    NewsAPIResponse,
    NewsQuery,
    NewsSearchResult,
)


MEDIA_CLOUD_STORY_LIST_URL = "https://search.mediacloud.org/api/search/story-list"
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
    if news_query.search_in is not None:
        params["searchIn"] = news_query.search_in

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


def get_media_cloud_api_key() -> str:
    """Load the Media Cloud key from the local environment."""
    load_dotenv()
    api_key = os.getenv("MEDIACLOUD_API_KEY")
    if not api_key:
        raise RuntimeError("MEDIACLOUD_API_KEY is missing.")
    return api_key


def search_media_cloud_page(
    *,
    query: str,
    start_date: date,
    end_date: date,
    collection_ids: Sequence[int],
    page_size: int = 100,
    pagination_token: str | None = None,
) -> MediaCloudPage:
    """Fetch one page of unverified Media Cloud story candidates.

    end_date is exclusive. Pagination is handled by the caller.
    """
    if not query.strip():
        raise ValueError("query must not be empty.")
    if start_date >= end_date:
        raise ValueError("start_date must be before end_date.")
    if not collection_ids or any(
        type(collection_id) is not int or collection_id <= 0
        for collection_id in collection_ids
    ):
        raise ValueError("collection_ids must contain positive integers.")
    if type(page_size) is not int or not 1 <= page_size <= 100:
        raise ValueError("page_size must be an integer between 1 and 100.")

    params: dict[str, str | int] = {
        "q": query,
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "platform": "onlinenews-mediacloud",
        "cs": ",".join(str(collection_id) for collection_id in collection_ids),
        "page_size": page_size,
    }
    if pagination_token:
        params["pagination_token"] = pagination_token

    try:
        response = requests.get(
            MEDIA_CLOUD_STORY_LIST_URL,
            params=params,
            headers={
                "Authorization": f"Token {get_media_cloud_api_key()}",
                "Accept": "application/json",
            },
            timeout=30,
        )
    except requests.RequestException:
        raise RuntimeError("Media Cloud request failed.") from None

    if response.status_code != 200:
        raise RuntimeError(f"Media Cloud HTTP {response.status_code}")

    try:
        payload = response.json()
    except ValueError:
        raise ValueError("Media Cloud response is not JSON.") from None

    if (
        not isinstance(payload, dict)
        or payload.get("status") == "error"
        or not isinstance(payload.get("stories"), list)
    ):
        raise ValueError("Media Cloud response has no valid stories list.")

    return MediaCloudPage.model_validate(payload)
