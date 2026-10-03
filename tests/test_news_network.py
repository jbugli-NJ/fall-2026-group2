from datetime import datetime, timezone
from typing import cast
from unittest.mock import MagicMock, Mock

import pytest
from neo4j import Driver

from monty_tool.network.insert import insert_news_articles
from monty_tool.network.schemas import NewsArticleInsertData
from monty_tool.boto3_utils.s3_protocols import S3Bucket
from monty_tool.network import news as news_network
from monty_tool.tools import update_news_network

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

def test_load_news_into_graph_passes_s3_uri_and_batches(monkeypatch) -> None:
    bucket = Mock()
    driver = Mock()
    job = Mock()
    result = Mock()
    key = "person/newsapi_articles/by-job/one.json"

    first_row = _article_row()
    second_row = _article_row()
    second_row["url"] = "https://example.com/second"
    rows = [first_row, second_row]

    snapshots = Mock(return_value=iter([(key, job, result)]))
    convert = Mock(return_value=rows)
    insert = Mock(side_effect=[1, 1])
    monkeypatch.setattr(news_network, "iter_news_snapshots", snapshots)
    monkeypatch.setattr(news_network, "news_result_to_node_data", convert)
    monkeypatch.setattr(news_network, "insert_news_articles", insert)
    monkeypatch.setattr(news_network, "NETWORK_INSERT_BATCH_SIZE", 1)

    processed = news_network.load_news_into_graph(
        cast(S3Bucket, bucket),
        bucket_name="test-bucket",
        driver=cast(Driver, driver),
    )

    assert processed == 2
    snapshots.assert_called_once_with(bucket)
    convert.assert_called_once_with(
        job,
        result,
        snapshot_s3_uri=f"s3://test-bucket/{key}",
    )
    assert insert.call_args_list[0].args == (driver, [first_row])
    assert insert.call_args_list[1].args == (driver, [second_row])

def test_update_news_network_requires_own_s3_prefix(monkeypatch) -> None:
    monkeypatch.delenv("AWS_BUCKET_PREFIX", raising=False)
    get_bucket = Mock()
    monkeypatch.setattr(update_news_network, "get_bucket", get_bucket)

    with pytest.raises(ValueError, match="AWS_BUCKET_PREFIX"):
        update_news_network.main()

    get_bucket.assert_not_called()

def test_update_news_network_loads_saved_results(monkeypatch) -> None:
    monkeypatch.setenv("AWS_BUCKET_PREFIX", "person/")
    bucket = Mock()
    context = MagicMock()
    driver = context.__enter__.return_value
    get_bucket = Mock(return_value=bucket)
    initialize = Mock()
    load = Mock(return_value=6)

    monkeypatch.setattr(
        update_news_network, "get_env_bucket_name", Mock(return_value="test-bucket")
    )
    monkeypatch.setattr(update_news_network, "get_bucket", get_bucket)
    monkeypatch.setattr(update_news_network, "initialize_db", initialize)
    monkeypatch.setattr(
        update_news_network, "get_graph_db_driver", Mock(return_value=context)
    )
    monkeypatch.setattr(update_news_network, "load_news_into_graph", load)

    assert update_news_network.main() == 6
    get_bucket.assert_called_once_with("test-bucket")
    initialize.assert_called_once_with()
    load.assert_called_once_with(
        bucket, bucket_name="test-bucket", driver=driver
    )