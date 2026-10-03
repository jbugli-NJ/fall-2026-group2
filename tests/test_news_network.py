from datetime import datetime, timezone
from typing import cast
from unittest.mock import MagicMock, Mock

import pytest
from neo4j import Driver

from monty_tool.network.insert import insert_news_articles
from monty_tool.network.schemas import NewsArticleInsertData


def _article_row() -> NewsArticleInsertData:
    return {
        "url": "https://example.com/article",
        "title": "Earthquake report",
        "description": "Test article",
        "source_id": None,
        "source_name": "Test News",
        "published_at": datetime(2026, 9, 10, tzinfo=timezone.utc),
        "event_id": "event-1",
        "snapshot_s3_uri": "s3://test-bucket/newsapi_articles/by-job/one.json",
    }


def _driver_for(tx: Mock) -> MagicMock:
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    session.execute_write.side_effect = lambda callback: callback(tx)
    return driver


def test_insert_news_articles_links_existing_event() -> None:
    tx = Mock()
    result = Mock()
    result.single.return_value = {"processed": 1}
    tx.run.side_effect = [[], result]
    driver = _driver_for(tx)

    processed = insert_news_articles(cast(Driver, driver), [_article_row()])

    assert processed == 1
    assert tx.run.call_count == 2
    write_query = tx.run.call_args_list[1].args[0]
    assert "MERGE (article:NewsArticle {url: item.url})" in write_query
    assert "MERGE (article)-[link:RETRIEVED_FOR]->(event)" in write_query
    assert "link.snapshot_s3_uris" in write_query
    driver.session.assert_called_once_with(database="neo4j")


def test_insert_news_articles_rejects_missing_event_before_write() -> None:
    tx = Mock()
    tx.run.return_value = [{"event_id": "event-1"}]
    driver = _driver_for(tx)

    with pytest.raises(ValueError, match="no matching Event node"):
        insert_news_articles(cast(Driver, driver), [_article_row()])

    tx.run.assert_called_once()