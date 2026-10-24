from datetime import datetime, timezone
from typing import Literal, cast
from unittest.mock import MagicMock, Mock

import pytest
from neo4j import Driver

from monty_tool.boto3_utils.s3_protocols import S3Bucket
from monty_tool.network import news as news_network
from monty_tool.network.insert import insert_media_cloud_articles
from monty_tool.network.schemas import MediaCloudArticleInsertData
from monty_tool.news.runner import MediaCloudJobRun


def _article_row(
    relationship_type: Literal["VERIFIED_FOR", "CONTEXT_FOR"] = "VERIFIED_FOR",
) -> MediaCloudArticleInsertData:
    return {
        "url": "https://example.com/article",
        "title": "Earthquake report",
        "source_name": "Test News",
        "published_at": datetime(2026, 9, 10, tzinfo=timezone.utc),
        "body_text": "An earthquake affected the region.",
        "story_id": 123,
        "event_id": "event-1",
        "relationship_type": relationship_type,
        "decision_reason": "The article discusses this event.",
        "evidence_excerpt": "An earthquake affected the region.",
        "snapshot_s3_uri": (
            "s3://test-bucket/person/media_cloud_articles/snapshots/one.json"
        ),
    }


def _driver_for(tx: Mock) -> MagicMock:
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    session.execute_write.side_effect = lambda callback: callback(tx)
    return driver


@pytest.mark.parametrize(
    "relationship_type", ["VERIFIED_FOR", "CONTEXT_FOR"]
)
def test_insert_media_cloud_articles_links_existing_event(
    relationship_type: Literal["VERIFIED_FOR", "CONTEXT_FOR"],
) -> None:
    tx = Mock()
    result = Mock()
    result.single.return_value = {"processed": 1}
    tx.run.side_effect = [[], result]
    driver = _driver_for(tx)
    row = _article_row(relationship_type)

    processed = insert_media_cloud_articles(
        cast(Driver, driver), [row]
    )

    assert processed == 1
    assert tx.run.call_count == 2
    query = tx.run.call_args_list[1].args[0]
    assert "MERGE (article:NewsArticle {url: item.url})" in query
    assert "MERGE (article)-[verified:VERIFIED_FOR]->(event)" in query
    assert "MERGE (article)-[context:CONTEXT_FOR]->(event)" in query
    assert "item.relationship_type = 'VERIFIED_FOR'" in query
    assert "item.relationship_type = 'CONTEXT_FOR'" in query
    assert tx.run.call_args_list[1].kwargs["items"] == [row]
    driver.session.assert_called_once_with(database="neo4j")


def test_insert_media_cloud_articles_rejects_missing_event() -> None:
    tx = Mock()
    tx.run.return_value = [{"event_id": "event-1"}]
    driver = _driver_for(tx)

    with pytest.raises(ValueError, match="no matching Event node"):
        insert_media_cloud_articles(
            cast(Driver, driver), [_article_row()]
        )

    tx.run.assert_called_once()


def test_media_cloud_conversion_keeps_only_classified_articles() -> None:
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

    assert [row["relationship_type"] for row in rows] == [
        "VERIFIED_FOR",
        "CONTEXT_FOR",
    ]
    assert [row["url"] for row in rows] == [
        "https://example.com/same_event",
        "https://example.com/related_context",
    ]
    assert all(row["body_text"] for row in rows)


def test_load_news_into_graph_passes_s3_uri_and_batches(monkeypatch) -> None:
    bucket = Mock()
    driver = Mock()
    run = Mock()
    prefix = news_network.BUCKET_DATA_PREFIX
    key = (
        f"{prefix.rstrip('/')}/"
        "media_cloud_articles/snapshots/one.json"
    )

    first_row = _article_row()
    second_row = _article_row("CONTEXT_FOR")
    second_row["url"] = "https://example.com/second"
    rows = [first_row, second_row]

    snapshots = Mock(return_value=iter([(key, run)]))
    convert = Mock(return_value=rows)
    insert = Mock(side_effect=[1, 1])
    monkeypatch.setattr(
        news_network, "iter_media_cloud_snapshots", snapshots
    )
    monkeypatch.setattr(
        news_network, "media_cloud_run_to_node_data", convert
    )
    monkeypatch.setattr(
        news_network, "insert_media_cloud_articles", insert
    )
    monkeypatch.setattr(
        news_network, "NETWORK_INSERT_BATCH_SIZE", 1
    )

    processed = news_network.load_news_into_graph(
        cast(S3Bucket, bucket),
        bucket_name="test-bucket",
        driver=cast(Driver, driver),
    )

    assert processed == 2
    snapshots.assert_called_once_with(bucket, prefix=prefix)
    convert.assert_called_once_with(
        run,
        snapshot_s3_uri=f"s3://test-bucket/{key}",
    )
    assert insert.call_args_list[0].args == (driver, [first_row])
    assert insert.call_args_list[1].args == (driver, [second_row])