from types import SimpleNamespace
from typing import Any

import pytest

import monty_tool.news_api as news_api
from monty_tool.llm.tools import QueryTools

def test_query_news_uses_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests_seen: list[dict[str, Any]] = []

    def fake_get(
        _url: str,
        *,
        params: dict[str, Any],
        headers: dict[str, str],
        timeout: int,
    ) -> SimpleNamespace:
        requests_seen.append(params.copy())
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"status": "ok", "totalResults": 0, "articles": []},
        )

    monkeypatch.setattr(news_api, "get_news_api_key", lambda: "test-key")
    monkeypatch.setattr(news_api.requests, "get", fake_get)

    result = QueryTools().execute(
        "search_news",
        {
            "query": "earthquake",
            "location": "Japan",
            "from_date": "2026-09-08",
            "to_date": "2026-09-22",
        },
    )

    assert result["status"] == "empty"
    assert requests_seen[0]["q"] == "earthquake Japan"
