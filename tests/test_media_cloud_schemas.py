"""Tests for Media Cloud search-response schemas."""

import pytest
from pydantic import ValidationError

from monty_tool.news.schemas import MediaCloudPage


def test_media_cloud_page_parses_story_and_next_token() -> None:
    page = MediaCloudPage.model_validate(
        {
            "stories": [
                {
                    "id": 123,
                    "title": "Earthquake reported in Japan",
                    "url": "https://example.com/story",
                    "publish_date": "2026-09-10T12:00:00Z",
                    "media_name": "Example News",
                }
            ],
            "pagination_token": "next-page",
        }
    )

    assert len(page.stories) == 1
    assert page.stories[0].id == 123
    assert page.stories[0].title == "Earthquake reported in Japan"
    assert page.pagination_token == "next-page"


def test_media_cloud_page_accepts_empty_last_page() -> None:
    page = MediaCloudPage.model_validate(
        {"stories": [], "pagination_token": None}
    )

    assert page.stories == []
    assert page.pagination_token is None


def test_error_response_is_not_treated_as_empty_page() -> None:
    with pytest.raises(ValidationError):
        MediaCloudPage.model_validate({"status": "error"})
