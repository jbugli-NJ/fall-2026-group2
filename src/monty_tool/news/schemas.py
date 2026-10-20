"""
Schemas for news search parameters and provider responses.

Media Cloud stories are search candidates, not verified event matches.
NewsAPI schemas remain until their existing callers are migrated.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class NewsQuery(BaseModel):
    """
    Search parameters generated from a Montandon disaster record.
    """

    item_id: str
    query: str
    from_date: date
    to_date: date
    search_in: Literal["title,description"] | None = None


def news_search_parameters(query: NewsQuery, *, page_size: int) -> dict[str, str | int]:
    """
    Describe the collection request settings stored with a snapshot.
    """
    parameters: dict[str, str | int] = {
        "page_size": page_size,
        "language": "en",
        "sort_by": "relevancy",
        "page": 1,
    }
    if query.search_in is not None:
        parameters["search_in"] = query.search_in
    return parameters


class NewsSource(BaseModel):
    """
    News source returned by NewsAPI.
    """

    id: str | None = None
    name: str


class NewsArticle(BaseModel):
    """
    A single article returned by NewsAPI.
    """

    model_config = ConfigDict(populate_by_name=True)

    source: NewsSource
    title: str
    description: str | None = None
    published_at: datetime = Field(alias="publishedAt")
    url: str


class NewsAPIResponse(BaseModel):
    """
    Validated response returned directly by NewsAPI.
    """

    model_config = ConfigDict(populate_by_name=True)

    status: Literal["ok"]
    total_results: int = Field(alias="totalResults")
    articles: list[NewsArticle]


class NewsSearchResult(BaseModel):
    """
    NewsAPI results linked back to the originating Montandon record.
    """

    item_id: str
    query: str
    from_date: date
    to_date: date
    total_results: int
    articles: list[NewsArticle]


class MediaCloudStory(BaseModel):
    """One unverified story candidate from Media Cloud."""

    id: int | str | None = None
    title: str | None = None
    url: str | None = None
    publish_date: str | None = None
    media_name: str | None = None


class MediaCloudPage(BaseModel):
    """One story-list response, including the next-page token."""

    stories: list[MediaCloudStory]
    pagination_token: str | None = None
