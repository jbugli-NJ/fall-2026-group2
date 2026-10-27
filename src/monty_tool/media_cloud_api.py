"""Media Cloud story-list client."""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import date

import requests
from dotenv import load_dotenv

from monty_tool.news.schemas import MediaCloudPage


MEDIA_CLOUD_STORY_LIST_URL = "https://search.mediacloud.org/api/search/story-list"


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

    end_date is inclusive. Pagination is handled by the caller.
    """
    if not query.strip():
        raise ValueError("query must not be empty.")
    if start_date > end_date:
        raise ValueError("start_date must be on or before end_date.")
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
