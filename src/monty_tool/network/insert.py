"""
Insert Montandon records into a local Neo4j instance.
"""

# Imports

from datetime import datetime, timezone
from itertools import batched
from pathlib import Path
from typing import LiteralString, cast

from neo4j import Query
from pydantic import ValidationError
from sentence_transformers import SentenceTransformer

from monty_tool.api_schemas import GOAppeal, GOEvent, MontandonItem
from monty_tool.embeddings.generate import EMBEDDING_MODEL_NAME
from monty_tool.network.node_data import (
    go_appeals_to_node_data,
    go_events_to_node_data,
    montandon_items_to_node_data,
)
from monty_tool.network.resources import (
    MONTANDON_DESCRIPTION_VECTOR_INDEX,
    MONTANDON_IMPACT_VECTOR_INDEX,
    MONTANDON_KEYWORDS_VECTOR_INDEX,
    NETWORK_INSERT_BATCH_SIZE,
    SIMILARITY_NEIGHBOR_LIMIT,
    SIMILARITY_THRESHOLD,
    get_graph_db_driver,
)
from monty_tool.network.schemas import (
    GOAppealNodeData,
    GOEventNodeData,
    MontandonItemNodeData,
)


# Montandon insertion helpers

def insert_montandon_nodes(driver, node_data: list[MontandonItemNodeData]):
    """
    Insert one bounded batch of Montandon nodes and apply their labels.
    """
    driver.execute_query(
        """
        UNWIND $items AS item
        MERGE (node:MontandonItem {id: item.id})
        SET node += item
        """,
        items=node_data,
        database_='neo4j',
    )
    driver.execute_query(
        """
        UNWIND $items AS item
        WITH item
        WHERE 'event' IN item.roles
        MATCH (node:MontandonItem {id: item.id})
        SET node:Event
        """,
        items=node_data,
        database_='neo4j',
    )
    driver.execute_query(
        """
        UNWIND $items AS item
        WITH item
        WHERE 'impact' IN item.roles
        MATCH (node:MontandonItem {id: item.id})
        SET node:Impact
        """,
        items=node_data,
        database_='neo4j',
    )


def create_montandon_deterministic_relationships():
    """
    Create links derived from record metadata without pairwise matching.
    """
    with get_graph_db_driver() as driver:
        driver.execute_query(
            """
            MATCH (impact:Impact)
            MATCH (event:Event {corr_id: impact.corr_id})
            MERGE (impact)-[:IMPACT_OF]->(event)
            """,
            database_='neo4j',
        )
        driver.execute_query(
            """
            MATCH (event:Event)
            UNWIND event.country_codes AS code
            MERGE (country:Country {code: code})
            MERGE (event)-[:IN_COUNTRY]->(country)
            """,
            database_='neo4j',
        )
        driver.execute_query(
            """
            MATCH (event:Event)
            UNWIND event.hazard_codes AS code
            MERGE (hazard:Hazard {code: code})
            MERGE (event)-[:HAS_HAZARD]->(hazard)
            """,
            database_='neo4j',
        )
        driver.execute_query(
            """
            MATCH (event:Event)
            MERGE (day:EventDay {date: date(event.start_datetime)})
            MERGE (event)-[:STARTED_ON]->(day)
            """,
            database_='neo4j',
        )


def _similarity_query(
    index_name: str,
    embedding_property: str,
    relationship_type: str,
    ) -> Query:
    """
    Build a bounded vector-neighbor relationship query.
    """
    return Query(
        cast(
            LiteralString,
            f"""
            CYPHER 25
            UNWIND $item_ids AS item_id
            MATCH (source:MontandonItem {{id: item_id}})
            WHERE source.`{embedding_property}` IS NOT NULL
            MATCH (target:MontandonItem)
            SEARCH target IN (
                VECTOR INDEX {index_name}
                FOR source.`{embedding_property}`
                LIMIT $neighbor_count
            ) SCORE AS score
            WHERE source.id <> target.id
              AND source.corr_id <> target.corr_id
              AND score >= $minimum_similarity
            MERGE (source)-[relation:`{relationship_type}`]-(target)
            SET relation.similarity = score
            """,
        ),
    )


def create_montandon_similarity_relationships():
    """
    Create bounded semantic links from the three vector indexes.
    """
    similarity_indexes = [
        (
            MONTANDON_DESCRIPTION_VECTOR_INDEX,
            'description_embedding',
            'SIMILAR_TO',
        ),
        (
            MONTANDON_KEYWORDS_VECTOR_INDEX,
            'keywords_embedding',
            'SIMILAR_KEYWORDS',
        ),
        (
            MONTANDON_IMPACT_VECTOR_INDEX,
            'impact_severity_embedding',
            'SIMILAR_IMPACT',
        ),
    ]
    with get_graph_db_driver() as driver:
        records, _, _ = driver.execute_query(
            'MATCH (node:MontandonItem) RETURN node.id AS id',
            database_='neo4j',
        )
        item_ids = [record['id'] for record in records]
        for item_id_batch in batched(item_ids, NETWORK_INSERT_BATCH_SIZE):
            for index_name, embedding_property, relationship_type in similarity_indexes:
                driver.execute_query(
                    _similarity_query(
                        index_name,
                        embedding_property,
                        relationship_type,
                    ),
                    item_ids=list(item_id_batch),
                    neighbor_count=SIMILARITY_NEIGHBOR_LIMIT + 1,
                    minimum_similarity=SIMILARITY_THRESHOLD,
                    database_='neo4j',
                )


def insert_go_event_nodes(driver, node_data: list[GOEventNodeData]):
    """
    Insert IFRC GO event nodes and connect them to their countries.
    """
    driver.execute_query(
        """
        UNWIND $items AS item
        MERGE (node:GOEvent {id: item.id})
        SET node += item
        """,
        items=node_data,
        database_='neo4j',
    )
    driver.execute_query(
        """
        UNWIND $items AS item
        MATCH (event:GOEvent {id: item.id})
        UNWIND event.country_codes AS code
        MERGE (country:Country {code: code})
        MERGE (event)-[:IN_COUNTRY]->(country)
        """,
        items=node_data,
        database_='neo4j',
    )


def insert_go_appeal_nodes(driver, node_data: list[GOAppealNodeData]):
    """
    Insert IFRC GO appeal nodes.
    """
    driver.execute_query(
        """
        UNWIND $items AS item
        MERGE (node:GOAppeal {id: item.id})
        SET node += item
        """,
        items=node_data,
        database_='neo4j',
    )


def insert_go_appeal_links(driver, node_data: list[GOAppealNodeData]):
    """
    Connect each GO appeal to the GO event referenced by its API record.
    """
    driver.execute_query(
        """
        UNWIND $items AS item
        MATCH (appeal:GOAppeal {id: item.id})
        MATCH (event:GOEvent {go_event_id: appeal.go_event_id})
        MERGE (appeal)-[:FOR_EVENT]->(event)
        """,
        items=node_data,
        database_='neo4j',
    )


def insert_go_records_into_graph_db(
    event_data: list[GOEventNodeData],
    appeal_data: list[GOAppealNodeData],
    ):
    """
    Insert IFRC GO event and appeal records plus their source linkage.
    """
    with get_graph_db_driver() as driver:
        insert_go_event_nodes(driver, event_data)
        insert_go_appeal_nodes(driver, appeal_data)
        insert_go_appeal_links(driver, appeal_data)


# Temporary local insertion
# TODO: Point at bucket once set up

def insert_from_local_data():
    """
    Helper to initialize a network using JSONL files under `data/`
    as a temporary placeholder for bucket access.

    Filters by an arbitrary date range for testing purposes.
    """
    data_folder = Path('data')
    montandon_items: list[MontandonItem] = []
    go_events: list[GOEvent] = []
    go_appeals: list[GOAppeal] = []
    for file in sorted(data_folder.rglob('*.jsonl')):
        with file.open(encoding='utf-8') as lines:
            for line_number, line in enumerate(lines, start=1):
                if not line.strip():
                    continue
                try:
                    montandon_items.append(
                        MontandonItem.model_validate_json(line, by_name=True)
                    )
                    continue
                except ValidationError:
                    pass
                try:
                    go_events.append(GOEvent.model_validate_json(line))
                    continue
                except ValidationError:
                    pass
                go_appeals.append(GOAppeal.model_validate_json(line))

    filtered_items = [
        item
        for item in montandon_items
        if (
            datetime(2025, 7, 1, tzinfo=timezone.utc)
            <=item.properties.start_datetime
            <=datetime(2025, 12, 31, tzinfo=timezone.utc)
        )
    ]
    embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    montandon_nodes = montandon_items_to_node_data(
        items=filtered_items,
        embedding_model=embedding_model,
    )
    with get_graph_db_driver() as driver:
        insert_montandon_nodes(driver, montandon_nodes)
    create_montandon_deterministic_relationships()

    filtered_go_events = [
        event
        for event in go_events
        if (
            datetime(2025, 7, 1, tzinfo=timezone.utc)
            <=event.disaster_start_date
            <=datetime(2025, 12, 31, tzinfo=timezone.utc)
        )
    ]
    filtered_go_appeals = [
        appeal
        for appeal in go_appeals
        if (
            datetime(2025, 7, 1, tzinfo=timezone.utc)
            <=appeal.start_date
            <=datetime(2025, 12, 31, tzinfo=timezone.utc)
        )
    ]

    insert_go_records_into_graph_db(
        event_data=go_events_to_node_data(filtered_go_events),
        appeal_data=go_appeals_to_node_data(filtered_go_appeals),
    )
    print('Insertion from local data complete!')


if __name__ == '__main__':
    insert_from_local_data()
