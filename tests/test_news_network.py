from datetime import datetime, timezone
from typing import cast
from unittest.mock import MagicMock, Mock

import pytest
from neo4j import Driver

from monty_tool.boto3_utils.s3_protocols import S3Bucket
from monty_tool.network import news as news_network
from monty_tool.network import insert as network_insert
from monty_tool.network.insert import insert_media_cloud_articles
from monty_tool.network.schemas import MediaCloudArticleInsertData
from monty_tool.news.runner import MediaCloudJobRun


def _article_row() -> MediaCloudArticleInsertData:
    return {
        "url": "https://example.com/article",
        "title": "Earthquake report",
        "source_name": "Test News",
        "published_at": datetime(2026, 9, 10, tzinfo=timezone.utc),
        "body_text": "An earthquake affected the region.",
        "story_id": 123,
        "event_id": "event-1",
        "decision_reason": "The article discusses this event.",
        "evidence_excerpt": "An earthquake affected the region.",
        "snapshot_s3_uri": (
            "s3://test-bucket/person/media_cloud_articles/current/emdat-events/event-1.json"
        ),
    }


def _driver_for(tx: Mock) -> MagicMock:
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    session.execute_write.side_effect = lambda callback: callback(tx)
    return driver


def test_insert_media_cloud_articles_links_existing_event() -> None:
    tx = Mock()
    result = Mock()
    result.single.return_value = {"processed": 1}
    tx.run.side_effect = [Mock(single=Mock(return_value={"event_id": "event-1"})), result]
    driver = _driver_for(tx)
    row = _article_row()

    processed = insert_media_cloud_articles(
        cast(Driver, driver), [row], event_id="event-1", snapshot_s3_uri=row["snapshot_s3_uri"],
    )

    assert processed == 1
    assert tx.run.call_count == 2
    query = tx.run.call_args_list[1].args[0]
    assert "MERGE (article:NewsArticle {url: item.url})" in query
    assert "MERGE (article)-[verified:VERIFIED_FOR {source_prefix: $source_prefix}]->(event)" in query
    assert "CONTEXT_FOR" not in query
    assert tx.run.call_args_list[1].kwargs["items"] == [row]
    driver.session.assert_called_once_with(database="neo4j")


def test_insert_media_cloud_articles_rejects_missing_event() -> None:
    tx = Mock()
    tx.run.return_value.single.return_value = None
    driver = _driver_for(tx)

    with pytest.raises(ValueError, match="no matching Event node"):
        insert_media_cloud_articles(
            cast(Driver, driver), [_article_row()], event_id="event-1",
            snapshot_s3_uri=_article_row()["snapshot_s3_uri"],
        )

    tx.run.assert_called_once()


def test_empty_rows_insert_nothing() -> None:
    tx = Mock()
    tx.run.return_value.single.return_value = {"event_id": "event-1"}
    driver = _driver_for(tx)
    assert insert_media_cloud_articles(
        driver, [], event_id="event-1", snapshot_s3_uri=_article_row()["snapshot_s3_uri"],
    ) == 0
    assert tx.run.call_count == 1


def test_insert_batches_share_one_transaction(monkeypatch) -> None:
    monkeypatch.setattr(network_insert, "NETWORK_INSERT_BATCH_SIZE", 1)
    tx = Mock()
    tx.run.return_value.single.return_value = {"event_id": "event-1", "processed": 1}
    driver = _driver_for(tx)
    rows = [_article_row(), {**_article_row(), "url": "https://example.com/second"}]
    assert insert_media_cloud_articles(
        driver, rows, event_id="event-1", snapshot_s3_uri=rows[0]["snapshot_s3_uri"],
    ) == 2
    session = driver.session.return_value.__enter__.return_value
    session.execute_write.assert_called_once()
    assert [call.kwargs["items"] for call in tx.run.call_args_list[1:]] == [[rows[0]], [rows[1]]]


def test_mixed_event_rows_rejected_before_database_changes() -> None:
    driver = MagicMock()
    row = _article_row()
    row["event_id"] = "other-event"
    with pytest.raises(ValueError, match="same event and snapshot"):
        insert_media_cloud_articles(
            driver, [row], event_id="event-1", snapshot_s3_uri=row["snapshot_s3_uri"],
        )
    driver.session.assert_not_called()


def test_media_cloud_conversion_keeps_only_same_event_articles() -> None:
    run = Mock()
    run.job.event.item_id = "event-1"
    run.job.event.collection = "emdat-events"
    assessments = []

    for status in ("same_event", "related_context", "uncertain", "different_event"):
        evidence = Mock(
            event_id="event-1",
            event_collection="emdat-events",
            body_status="body_available",
            body_text="An earthquake affected the region.",
        )
        evidence.story = Mock(
            id=status,
            title=f"Article about {status}",
            media_name="Test News",
            publish_date="2026-09-10T12:00:00Z",
        )
        decision = Mock(
            event_id="event-1",
            event_collection="emdat-events",
            status=status,
            url=f"https://example.com/{status}",
            reason="Test decision",
            evidence_excerpt="An earthquake affected the region.",
        )
        assessments.append(Mock(evidence=evidence, decision=decision))

    run.assessments = assessments

    rows = news_network.media_cloud_run_to_node_data(
        cast(MediaCloudJobRun, run),
        snapshot_s3_uri="s3://test-bucket/person/media_cloud_articles/snapshots/one.json",
    )

    assert [row["url"] for row in rows] == [
        "https://example.com/same_event",
    ]
    assert all(row["body_text"] for row in rows)


@pytest.mark.parametrize("empty", [False, True])
def test_load_news_into_graph_passes_whole_event_including_empty_results(monkeypatch, empty) -> None:
    bucket = Mock()
    driver = Mock()
    run = Mock()
    run.job.event.item_id = "event-1"
    prefix = news_network.BUCKET_DATA_PREFIX
    key = (
        f"{prefix.rstrip('/')}/"
        "media_cloud_articles/current/emdat-events/event-1.json"
    )

    first_row = _article_row()
    second_row = _article_row()
    second_row["url"] = "https://example.com/second"
    rows = [] if empty else [first_row, second_row]

    snapshots = Mock(return_value=iter([(key, run)]))
    convert = Mock(return_value=rows)
    insert = Mock(return_value=len(rows))
    monkeypatch.setattr(
        news_network, "iter_media_cloud_snapshots", snapshots
    )
    monkeypatch.setattr(
        news_network, "media_cloud_run_to_node_data", convert
    )
    monkeypatch.setattr(
        news_network, "insert_media_cloud_articles", insert
    )

    processed = news_network.load_news_into_graph(
        cast(S3Bucket, bucket),
        bucket_name="test-bucket",
        driver=cast(Driver, driver),
    )

    assert processed == len(rows)
    snapshots.assert_called_once_with(bucket, prefix=prefix)
    convert.assert_called_once_with(
        run,
        snapshot_s3_uri=f"s3://test-bucket/{key}",
    )
    insert.assert_called_once_with(
        driver, rows, event_id="event-1", snapshot_s3_uri=f"s3://test-bucket/{key}",
    )
