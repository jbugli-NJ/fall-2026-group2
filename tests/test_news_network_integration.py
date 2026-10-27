"""Opt-in Neo4j insertion tests; set NEWS_TEST_NEO4J_URI to a test database.

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
                """,
                event_id=event_id, other_id=event_id + "-other", token=token,
                url=url,
                database_="neo4j",
            )
            row: MediaCloudArticleInsertData = {
                "url": url, "title": "Test report", "source_name": "Test publisher",
                "published_at": None, "body_text": "Test evidence.", "story_id": token,
                "event_id": event_id,
                "decision_reason": "Test classification.", "evidence_excerpt": "Test evidence.",
                "snapshot_s3_uri": snapshot,
            }
            yield driver, row, source
        finally:
            driver.execute_query(
                "MATCH (n:MediaCloudRefreshTest {test_run: $token}) DETACH DELETE n",
                token=token, database_="neo4j",
            )


def test_insert_same_event_link_is_idempotent(graph):
    driver, row, source = graph
    event_id = row["event_id"]
    snapshot = row["snapshot_s3_uri"]

    for _ in range(2):
        assert insert_media_cloud_articles(
            driver, [row], event_id=event_id, snapshot_s3_uri=snapshot,
        ) == 1

    records, _, _ = driver.execute_query(
        """
        MATCH (:NewsArticle {url: $url})-[link]->(event:Event)
        RETURN event.id AS event_id, type(link) AS kind,
               link.source_prefix AS source, link.snapshot_s3_uris AS snapshots
        """,
        url=row["url"], database_="neo4j",
    )
    assert len(records) == 1
    assert records[0]["event_id"] == event_id
    assert records[0]["kind"] == "VERIFIED_FOR"
    assert records[0]["source"] == source
    assert records[0]["snapshots"] == [snapshot]


def test_insert_failure_leaves_no_link(graph):
    driver, row, _ = graph
    # Neo4j rejects map-valued properties during insertion.
    row["title"] = cast(str, {"invalid": "property"})
    with pytest.raises(Neo4jError):
        insert_media_cloud_articles(
            driver, [row], event_id=row["event_id"], snapshot_s3_uri=row["snapshot_s3_uri"],
        )
    records, _, _ = driver.execute_query(
        """
        MATCH (:NewsArticle {url: $url})-[link]->(:Event {id: $event_id})
        RETURN count(link) AS links
        """,
        url=row["url"], event_id=row["event_id"], database_="neo4j",
    )
    assert len(records) == 1
    assert records[0]["links"] == 0
