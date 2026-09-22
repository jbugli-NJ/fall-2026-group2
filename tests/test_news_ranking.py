"""Check ranking behavior without downloading models or calling NewsAPI."""

from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

import monty_tool.news.ranking as ranking
from monty_tool.news.schemas import NewsArticle, NewsSource


def _article(number: int, description: str | None = None) -> NewsArticle:
    return NewsArticle(
        source=NewsSource(name="Example source"),
        title=f"Example article {number}",
        description=description,
        publishedAt=datetime(2026, 9, 20, tzinfo=timezone.utc),
        url=f"https://example.com/articles/{number}",
    )


def test_ranking_preserves_candidates_and_original_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    articles = [_article(1), _article(2, "Some reporting."), _article(3)]
    original = [article.model_dump() for article in articles]
    model = Mock()
    # All scores are negative; none of the articles should be discarded.
    # Equal scores must preserve the original relative order.
    model.predict.return_value = [-9.0, -1.0, -1.0]
    monkeypatch.setattr(ranking, "_get_ranker", lambda: model)

    ranked = ranking.rank_news_articles(articles, reference_text="flood reports")

    assert [id(article) for article in ranked] == [
        id(articles[1]), id(articles[2]), id(articles[0]),
    ]
    assert [article.model_dump() for article in articles] == original
    assert ranked is not articles
    pairs = model.predict.call_args.args[0]
    assert pairs[0] == ("flood reports", "Example article 1\n")
    assert pairs[1] == ("flood reports", "Example article 2\nSome reporting.")


@pytest.mark.parametrize("count", [0, 1])
def test_small_lists_do_not_load_model(
    monkeypatch: pytest.MonkeyPatch,
    count: int,
) -> None:
    loader = Mock(side_effect=AssertionError("Model loading is unnecessary"))
    monkeypatch.setattr(ranking, "_get_ranker", loader)
    articles = [_article(1)][:count]

    ranked = ranking.rank_news_articles(articles, reference_text="flood reports")

    assert ranked == articles
    assert ranked is not articles
    loader.assert_not_called()


def test_score_count_mismatch_does_not_silently_drop_articles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = Mock()
    model.predict.return_value = [1.0]
    monkeypatch.setattr(ranking, "_get_ranker", lambda: model)

    with pytest.raises(ValueError):
        ranking.rank_news_articles(
            [_article(1), _article(2)], reference_text="flood reports",
        )


def test_empty_search_purpose_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    loader = Mock(side_effect=AssertionError("Invalid input must not load a model"))
    monkeypatch.setattr(ranking, "_get_ranker", loader)

    with pytest.raises(ValueError, match="non-empty"):
        ranking.rank_news_articles([_article(1), _article(2)], reference_text="  ")
    loader.assert_not_called()
