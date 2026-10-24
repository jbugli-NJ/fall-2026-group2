"""Schemas for Media Cloud search candidates and event queries."""

from datetime import date

from pydantic import BaseModel


class NewsQuery(BaseModel):
    """
    Search parameters generated from a Montandon disaster record.
    """

    item_id: str
    query: str
    from_date: date
    to_date: date


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
