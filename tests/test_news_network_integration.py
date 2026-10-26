"""Opt-in Neo4j refresh tests; set NEWS_TEST_NEO4J_URI to a test database.

Only uniquely marked fixture nodes are created and removed. No AWS or model
access is used. Credentials default to the project's local Docker setup.
"""

import os
from typing import cast
from uuid import uuid4

import pytest
from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError

from monty_tool.network.insert import insert_media_cloud_articles
from monty_tool.network.schemas import MediaCloudArticleInsertData


@pytest.fixture
def graph():
    uri = os.getenv("NEWS_TEST_NEO4J_URI")
    if not uri:
        pytest.skip("Set NEWS_TEST_NEO4J_URI to run against a test Neo4j database.")
    token = uuid4().hex
    event_id = f"media-cloud-test-{token}"
    url = f"https://example.invalid/{token}"
    source = f"s3://test-bucket/{token}/media_cloud_articles/"
    snapshot = f"{source}current/emdat-events/{event_id}.json"
    with GraphDatabase.driver(uri, auth=(
        os.getenv("NEWS_TEST_NEO4J_USER", "neo4j"),
        os.getenv("NEWS_TEST_NEO4J_PASSWORD", "password"),
    )) as driver:
        driver.verify_connectivity()
        try:
            driver.execute_query(
                """
                CREATE (event:Event:MediaCloudRefreshTest {id: $event_id, test_run: $token})
                CREATE (other:Event:MediaCloudRefreshTest {id: $other_id, test_run: $token})
                CREATE (article:NewsArticle:MediaCloudRefreshTest {url: $url, test_run: $token})
                CREATE (article)-[:VERIFIED_FOR {
                    snapshot_s3_uris: [$legacy_uri]
                }]->(event)
                CREATE (article)-[:CONTEXT_FOR {source_prefix: 'other-source'}]->(event)
                CREATE (article)-[:VERIFIED_FOR {source_prefix: $source}]->(other)
                """,
                event_id=event_id, other_id=event_id + "-other", token=token,
                url=url, source=source, legacy_uri=source + "snapshots/old.json",
                database_="neo4j",
            )
            row: MediaCloudArticleInsertData = {
                "url": url, "title": "Test report", "source_name": "Test publisher",
                "published_at": None, "body_text": "Test evidence.", "story_id": token,
                "event_id": event_id, "relationship_type": "VERIFIED_FOR",
                "decision_reason": "Test classification.", "evidence_excerpt": "Test evidence.",
                "snapshot_s3_uri": snapshot,
            }
            yield driver, row, source
        finally:
            driver.execute_query(
                "MATCH (n:MediaCloudRefreshTest {test_run: $token}) DETACH DELETE n",
                token=token, database_="neo4j",
            )


def test_refresh_changes_links_retracts_empty_result_and_preserves_other_sources(graph):
    driver, row, source = graph
    event_id = row["event_id"]
    snapshot = row["snapshot_s3_uri"]

    # Start with a legacy VERIFIED_FOR link, then move in both directions.
    # The final empty classified result represents uncertain/different_event.
    for relationship in ("CONTEXT_FOR", "VERIFIED_FOR", "VERIFIED_FOR", None):
        rows = []
        if relationship is not None:
            row["relationship_type"] = relationship
            rows = [row]
        assert insert_media_cloud_articles(
            driver, rows, event_id=event_id, snapshot_s3_uri=snapshot,
        ) == len(rows)
        records, _, _ = driver.execute_query(
            """
            MATCH (:NewsArticle {url: $url})-[link]->(event:Event)
            RETURN event.id AS event_id, type(link) AS kind,
                   link.source_prefix AS source, link.snapshot_s3_uris AS snapshots
            """,
            url=row["url"], database_="neo4j",
        )
        owned = [record for record in records if record["event_id"] == event_id and record["source"] == source]
        assert [record["kind"] for record in owned] == ([] if relationship is None else [relationship])
        assert all(record["snapshots"] == [snapshot] for record in owned)
        assert len(records) == len(rows) + 2  # Other event and other source survive.
        assert any(record["event_id"] == event_id + "-other" for record in records)
        assert any(record["source"] == "other-source" for record in records)


def test_insert_failure_rolls_back_removal_of_old_relationships(graph):
    driver, row, _ = graph
    # Neo4j rejects map-valued properties during insertion, after cleanup.
    row["title"] = cast(str, {"invalid": "property"})
    with pytest.raises(Neo4jError):
        insert_media_cloud_articles(
            driver, [row], event_id=row["event_id"], snapshot_s3_uri=row["snapshot_s3_uri"],
        )
    records, _, _ = driver.execute_query(
        """
        MATCH (:NewsArticle {url: $url})-[link:VERIFIED_FOR]->(:Event {id: $event_id})
        RETURN link.snapshot_s3_uris AS snapshots
        """,
        url=row["url"], event_id=row["event_id"], database_="neo4j",
    )
    assert len(records) == 1
    assert records[0]["snapshots"][0].endswith("snapshots/old.json")
