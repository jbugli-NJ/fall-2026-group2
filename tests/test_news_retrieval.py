"""Verify both assistant news tools and ranking failure behavior offline."""

from datetime import date, datetime, timezone
from unittest.mock import Mock

import pytest

from monty_tool import news_api
from monty_tool.event_context import EventContext
from monty_tool.llm.tools import NewsTools, QueryTools
from monty_tool.news import ranking
from monty_tool.news.schemas import NewsArticle, NewsQuery, NewsSearchResult, NewsSource


def _execute(tool_kind: str) -> dict:
    if tool_kind == "query":
        return QueryTools().execute("search_news", {
            "query": "earthquake", "location": "Japan",
            "from_date": "2026-09-08", "to_date": "2026-09-22",
        })

    event = EventContext(
        item_id="event-1", collection="events", correlation_id="correlation-1",
        roles=["event"], title="Japan earthquake", description=None,
        keywords=[], country_codes=["JPN"], hazard_codes=["EQ"],
        start_datetime=datetime(2026, 9, 10, tzinfo=timezone.utc),
        end_datetime=datetime(2026, 9, 10, tzinfo=timezone.utc),
        geometry_type=None, bbox=(130.0, 30.0, 140.0, 40.0),
    )
    return NewsTools([event]).execute("search_event_news", {
        "item_id": "event-1", "query": "earthquake Japan",
    })


@pytest.mark.parametrize("tool_kind", ["query", "event"])
@pytest.mark.parametrize("ranking_fails", [False, True])
def test_tools_fetch_twenty_and_return_five_with_fallback(
    monkeypatch: pytest.MonkeyPatch, tool_kind: str, ranking_fails: bool,
) -> None:
    articles = [NewsArticle(
        source=NewsSource(name="Example source"), title=f"Article {number}",
        publishedAt=datetime(2026, 9, 10, tzinfo=timezone.utc),
        url=f"https://example.com/{number}",
    ) for number in range(7)]
    fetched: list[NewsSearchResult] = []
    queries: list[NewsQuery] = []

    def fake_search(query: NewsQuery, *, page_size: int) -> NewsSearchResult:
        assert page_size == 20
        queries.append(query)
        result = NewsSearchResult(
            item_id=query.item_id, query=query.query, from_date=query.from_date,
            to_date=query.to_date, total_results=30, articles=articles,
        )
        fetched.append(result)
        return result

    rank = Mock(return_value=list(reversed(articles)))
    if ranking_fails:
        rank.side_effect = RuntimeError("Ranking model unavailable")
    monkeypatch.setattr(news_api, "search_news", fake_search)
    monkeypatch.setattr(ranking, "rank_news_articles", rank)

    result = _execute(tool_kind)

    assert result["status"] == "ok"
    assert result["total_results"] == 30
    assert result["query"] == "earthquake Japan"
    assert result["item_id"] == queries[0].item_id
    assert result["from_date"] == queries[0].from_date.isoformat()
    assert result["to_date"] == queries[0].to_date.isoformat()
    expected = articles[:5] if ranking_fails else list(reversed(articles))[:5]
    assert [a["url"] for a in result["articles"]] == [a.url for a in expected]
    assert fetched[0].articles == articles
    rank.assert_called_once_with(articles, reference_text="earthquake Japan")
    if tool_kind == "query":
        assert queries[0].from_date == date(2026, 9, 8)
    else:
        assert queries[0].from_date == date(2026, 9, 9)


@pytest.mark.parametrize("tool_kind", ["query", "event"])
@pytest.mark.parametrize("search_fails", [False, True])
def test_empty_search_and_search_error_remain_distinct(
    monkeypatch: pytest.MonkeyPatch, tool_kind: str, search_fails: bool,
) -> None:
    def fake_search(query: NewsQuery, *, page_size: int) -> NewsSearchResult:
        if search_fails:
            raise RuntimeError("NewsAPI failed")
        return NewsSearchResult(
            item_id=query.item_id, query=query.query, from_date=query.from_date,
            to_date=query.to_date, total_results=0, articles=[],
        )

    rank = Mock(side_effect=AssertionError("No candidates to rank"))
    monkeypatch.setattr(news_api, "search_news", fake_search)
    monkeypatch.setattr(ranking, "rank_news_articles", rank)

    result = _execute(tool_kind)

    assert result["status"] == ("error" if search_fails else "empty")
    if search_fails:
        assert result["message"] == "NewsAPI failed"
    else:
        assert result["articles"] == []
    rank.assert_not_called()
