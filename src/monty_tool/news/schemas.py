"""
Schemas for news search parameters and NewsAPI responses.

Flow:
MontandonItem -> NewsQuery -> news_api.py -> NewsAPI -> NewsAPIResponse -> NewsSearchResult
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