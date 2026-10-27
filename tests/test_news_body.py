"""Offline tests for publisher HTML fetching and body extraction."""

from unittest.mock import Mock
import pytest
from monty_tool.news import body


@pytest.fixture(autouse=True)
def clear_proxy_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in body._PROXY_VARS:
        monkeypatch.delenv(name, raising=False)


def mock_response(
    status: int = 200,
    *,
    headers: dict[str, str] | None = None,
    html: str = "<html>article</html>",
    final_url: str = "https://example.com/story",
    data: bytes | None = None,
) -> Mock:
    response = Mock()
    response.status = status
    response.headers = (
        headers if headers is not None else {"content-type": "text/html"}
    )
    response.html = html
    response.data = data if data is not None else html.encode()
    response.url = final_url
    return response


def test_fetches_publisher_html(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch = Mock(return_value=mock_response())
    monkeypatch.setattr(body, "_is_public_web_url", lambda url: True)
    monkeypatch.setattr(body, "fetch_response", fetch)

    assert body.fetch_publisher_html("https://example.com/story") == (
        "<html>article</html>",
        "https://example.com/story",
    )
    assert fetch.call_args.kwargs["decode"] is True
    config = fetch.call_args.kwargs["config"]
    assert config.getint("DEFAULT", "MAX_FILE_SIZE") == body._MAX_HTML_BYTES
    assert config.getboolean("DEFAULT", "SSRF_PROTECTION")
    assert not config.getboolean("DEFAULT", "INSECURE_SSL_FALLBACK")


def test_private_url_is_not_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch = Mock()
    monkeypatch.setattr(body, "fetch_response", fetch)

    assert body.fetch_publisher_html("http://127.0.0.1/latest/meta-data/") is None
    fetch.assert_not_called()


def test_private_final_url_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch = Mock(return_value=mock_response(
        final_url="http://127.0.0.1/latest/meta-data/"
    ))
    monkeypatch.setattr(
        body,
        "_is_public_web_url",
        lambda url: not url.startswith("http://127.0.0.1/"),
    )
    monkeypatch.setattr(body, "fetch_response", fetch)

    assert body.fetch_publisher_html("https://example.com/story") is None


def test_proxy_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch = Mock()
    monkeypatch.setattr(body, "_is_public_web_url", lambda url: True)
    monkeypatch.setattr(body, "fetch_response", fetch)
    monkeypatch.setenv("https_proxy", "http://proxy.example")

    assert body.fetch_publisher_html("https://example.com/story") is None
    fetch.assert_not_called()


def test_non_html_response_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = mock_response(headers={"content-type": "application/pdf"})
    monkeypatch.setattr(body, "_is_public_web_url", lambda url: True)
    monkeypatch.setattr(body, "fetch_response", Mock(return_value=response))

    assert body.fetch_publisher_html("https://example.com/story") is None


def test_oversized_response_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = mock_response(data=b"x" * (body._MAX_HTML_BYTES + 1))
    monkeypatch.setattr(body, "_is_public_web_url", lambda url: True)
    monkeypatch.setattr(body, "fetch_response", Mock(return_value=response))

    assert body.fetch_publisher_html("https://example.com/story") is None


def test_failed_download_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(body, "_is_public_web_url", lambda url: True)
    monkeypatch.setattr(body, "fetch_response", Mock(return_value=None))

    assert body.fetch_publisher_html("https://example.com/story") is None


def test_download_exception_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(body, "_is_public_web_url", lambda url: True)
    monkeypatch.setattr(body, "fetch_response", Mock(side_effect=OSError))

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