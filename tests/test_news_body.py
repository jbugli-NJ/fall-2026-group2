"""Offline tests for publisher HTML fetching and body extraction."""

from unittest.mock import Mock

import pytest
from requests.exceptions import Timeout

from monty_tool.news import body


def mock_response(
    status_code: int = 200,
    *,
    headers: dict[str, str] | None = None,
    chunks: list[bytes] | None = None,
) -> Mock:
    response = Mock()
    response.status_code = status_code
    response.headers = headers if headers is not None else {"Content-Type": "text/html"}
    response.encoding = "utf-8"
    response.iter_content.return_value = (
        chunks if chunks is not None else [b"<html>article</html>"]
    )
    return response


def test_fetches_publisher_html(monkeypatch: pytest.MonkeyPatch) -> None:
    response = mock_response()
    get = Mock(return_value=response)
    monkeypatch.setattr(body, "_is_public_web_url", lambda url: True)
    monkeypatch.setattr(body.requests, "get", get)

    result = body.fetch_publisher_html("https://example.com/story")

    assert result == ("<html>article</html>", "https://example.com/story")
    assert get.call_args.kwargs["timeout"] == (3, 10)
    assert get.call_args.kwargs["stream"] is True
    assert get.call_args.kwargs["allow_redirects"] is False
    response.close.assert_called_once()


def test_private_url_is_not_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    get = Mock()
    monkeypatch.setattr(body.requests, "get", get)

    assert body.fetch_publisher_html("http://127.0.0.1/latest/meta-data/") is None
    get.assert_not_called()


def test_private_redirect_is_not_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = mock_response(
        302, headers={"Location": "http://127.0.0.1/latest/meta-data/"}
    )
    get = Mock(return_value=response)
    monkeypatch.setattr(
        body,
        "_is_public_web_url",
        lambda url: not url.startswith("http://127.0.0.1/"),
    )
    monkeypatch.setattr(body.requests, "get", get)

    assert body.fetch_publisher_html("https://example.com/story") is None
    assert get.call_count == 1
    response.close.assert_called_once()


def test_non_html_response_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = mock_response(headers={"Content-Type": "application/pdf"})
    monkeypatch.setattr(body, "_is_public_web_url", lambda url: True)
    monkeypatch.setattr(body.requests, "get", Mock(return_value=response))

    assert body.fetch_publisher_html("https://example.com/story") is None


def test_oversized_response_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = mock_response(
        chunks=[b"x" * (body._MAX_HTML_BYTES + 1)]
    )
    monkeypatch.setattr(body, "_is_public_web_url", lambda url: True)
    monkeypatch.setattr(body.requests, "get", Mock(return_value=response))

    assert body.fetch_publisher_html("https://example.com/story") is None
    response.close.assert_called_once()


def test_timeout_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(body, "_is_public_web_url", lambda url: True)
    monkeypatch.setattr(body.requests, "get", Mock(side_effect=Timeout))

    assert body.fetch_publisher_html("https://example.com/story") is None


def test_extracted_text_is_cleaned(monkeypatch: pytest.MonkeyPatch) -> None:
    extract = Mock(return_value="  Article text.  ")
    monkeypatch.setattr(body, "extract", extract)

    assert (
        body.extract_publisher_body(
            "<html>article</html>", url="https://example.com/story"
        )
        == "Article text."
    )
    assert extract.call_args.kwargs["include_comments"] is False
