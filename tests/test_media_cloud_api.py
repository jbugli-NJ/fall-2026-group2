"""Offline tests for the Media Cloud story-list client."""

from datetime import date
from unittest.mock import Mock

import pytest
from requests.exceptions import Timeout

from monty_tool import media_cloud_api


def mock_media_cloud_get(
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
    *,
    status_code: int = 200,
) -> Mock:
    response = Mock(status_code=status_code)
    response.json.return_value = payload
    get = Mock(return_value=response)
    monkeypatch.setattr(media_cloud_api.requests, "get", get)
    monkeypatch.setattr(media_cloud_api, "get_media_cloud_api_key", lambda: "test-key")
    return get


def test_story_list_request_and_pagination_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get = mock_media_cloud_get(
        monkeypatch,
        {
            "stories": [
                {
                    "id": 123,
                    "title": "Example",
                    "url": "https://example.com/story",
                    "publish_date": "2026-09-10T12:00:00Z",
                }
            ],
            "pagination_token": "next-token",
        },
    )

    page = media_cloud_api.search_media_cloud_page(
        query='"climate change"',
        start_date=date(2026, 9, 1),
        end_date=date(2026, 10, 1),
        collection_ids=[123, 456],
        page_size=100,
        pagination_token="previous-token",
    )

    assert len(page.stories) == 1
    assert page.stories[0].id == 123
    assert page.pagination_token == "next-token"
    assert get.call_args.args[0] == media_cloud_api.MEDIA_CLOUD_STORY_LIST_URL
    assert get.call_args.kwargs["params"] == {
        "q": '"climate change"',
        "start": "2026-09-01",
        "end": "2026-10-01",
        "platform": "onlinenews-mediacloud",
        "cs": "123,456",
        "page_size": 100,
        "pagination_token": "previous-token",
    }
    assert get.call_args.kwargs["headers"]["Authorization"] == "Token test-key"
    assert get.call_args.kwargs["timeout"] == 30


def test_first_page_omits_pagination_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get = mock_media_cloud_get(
        monkeypatch, {"stories": [], "pagination_token": None}
    )

    media_cloud_api.search_media_cloud_page(
        query="earthquake",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 2),
        collection_ids=[123],
    )

    assert "pagination_token" not in get.call_args.kwargs["params"]


@pytest.mark.parametrize("status_code", [422, 429])
def test_http_error_is_not_an_empty_page(
    monkeypatch: pytest.MonkeyPatch, status_code: int
) -> None:
    mock_media_cloud_get(
        monkeypatch, {"status": "error"}, status_code=status_code
    )

    with pytest.raises(RuntimeError, match=f"HTTP {status_code}"):
        media_cloud_api.search_media_cloud_page(
            query="earthquake",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 2),
            collection_ids=[123],
        )


@pytest.mark.parametrize(
    "payload",
    [{"status": "error", "stories": []}, {"status": "error"}],
)
def test_error_payload_is_not_an_empty_page(
    monkeypatch: pytest.MonkeyPatch, payload: object
) -> None:
    mock_media_cloud_get(monkeypatch, payload)

    with pytest.raises(ValueError, match="stories list"):
        media_cloud_api.search_media_cloud_page(
            query="earthquake",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 2),
            collection_ids=[123],
        )


@pytest.mark.parametrize("page_size", [0, 101, True])
def test_invalid_page_size_is_rejected(page_size: int) -> None:
    with pytest.raises(ValueError, match="page_size"):
        media_cloud_api.search_media_cloud_page(
            query="earthquake",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 2),
            collection_ids=[123],
            page_size=page_size,
        )


def test_network_failure_does_not_return_empty_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(media_cloud_api.requests, "get", Mock(side_effect=Timeout))
    monkeypatch.setattr(media_cloud_api, "get_media_cloud_api_key", lambda: "test-key")

    with pytest.raises(RuntimeError, match="request failed"):
        media_cloud_api.search_media_cloud_page(
            query="earthquake",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 2),
            collection_ids=[123],
        )

def test_single_day_media_cloud_search_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get = mock_media_cloud_get(
        monkeypatch, {"stories": [], "pagination_token": None}
    )

    media_cloud_api.search_media_cloud_page(
        query="earthquake",
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 10),
        collection_ids=[123],
    )

    assert get.call_args.kwargs["params"]["end"] == "2026-09-10"