"""
Insert Montandon records into a local Neo4j instance.
"""

# Imports

from datetime import datetime, timezone
from pathlib import Path

from monty_tool.api_schemas import MontandonItem
from monty_tool.network.node_data import items_to_node_data
from monty_tool.network.resources import get_graph_db_driver
from monty_tool.network.schemas import NodeData


# Constants

_MINIMUM_SIMILARITY = 0.95

# NOTE: Returns item pairs that need relationships calculated
_BATCH_PAIR_MATCH = """
UNWIND $items AS item
MATCH (item_node:MontandonItem {id: item.id})
MATCH (other:MontandonItem)
WHERE item_node.id <> other.id
WITH CASE WHEN item_node.id < other.id THEN item_node ELSE other END AS source,
     CASE WHEN item_node.id < other.id THEN other ELSE item_node END AS target
WITH DISTINCT source, target
"""


# Insertion helper

def insert_records_into_graph_db(node_data: list[NodeData]):
    """
    Insert Montandon records as nodes and build node relationships.
    """
    with get_graph_db_driver() as driver:
        # Insert the record nodes and their properties
        driver.execute_query(
            """
            UNWIND $items AS item
            MERGE (node:MontandonItem {id: item.id})
            SET node += item
            """,
            items=node_data,
            database_='neo4j',
        )
        # Apply labels from the source record roles
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
        # Remove every existing relationship for records if present
        driver.execute_query(
            """
            UNWIND $items AS item
            MATCH (node:MontandonItem {id: item.id})
            MATCH (node)-[relation]-()
            WITH DISTINCT relation
            DELETE relation
            """,
            items=node_data,
            database_='neo4j',
        )
        # Connect each impact to the event it measures
        driver.execute_query(
            """
            UNWIND $items AS item
            MATCH (impact:Impact {id: item.id})
            MATCH (event:Event {corr_id: impact.corr_id})
            MERGE (impact)-[:IMPACT_OF]->(event)
            """,
            items=node_data,
            database_='neo4j',
        )
        # Connect records using the various computed embeddings
        driver.execute_query(
            _BATCH_PAIR_MATCH + """
            WHERE source.corr_id <> target.corr_id
            WITH source, target,
                 vector.similarity.cosine(
                     source.description_embedding,
                     target.description_embedding
                 ) AS similarity
            WHERE similarity >= $minimum_similarity
            MERGE (source)-[relation:SIMILAR_TO]->(target)
            SET relation.similarity = similarity
            """,
            items=node_data,
            minimum_similarity=_MINIMUM_SIMILARITY,
            database_='neo4j',
        )
        driver.execute_query(
            _BATCH_PAIR_MATCH + """
            WHERE source.corr_id <> target.corr_id
              AND source.keywords_embedding IS NOT NULL
              AND target.keywords_embedding IS NOT NULL
            WITH source, target,
                 vector.similarity.cosine(
                     source.keywords_embedding,
                     target.keywords_embedding
                 ) AS similarity
            WHERE similarity >= $minimum_similarity
            MERGE (source)-[relation:SIMILAR_KEYWORDS]->(target)
            SET relation.similarity = similarity
            """,
            items=node_data,
            minimum_similarity=_MINIMUM_SIMILARITY,
            database_='neo4j',
        )
        driver.execute_query(
            _BATCH_PAIR_MATCH + """
            WHERE source.corr_id <> target.corr_id
              AND source.impact_severity_embedding IS NOT NULL
              AND target.impact_severity_embedding IS NOT NULL
            WITH source, target,
                 vector.similarity.cosine(
                     source.impact_severity_embedding,
                     target.impact_severity_embedding
                 ) AS similarity
            WHERE similarity >= $minimum_similarity
            MERGE (source)-[relation:SIMILAR_IMPACT]->(target)
            SET relation.similarity = similarity
            """,
            items=node_data,
            minimum_similarity=_MINIMUM_SIMILARITY,
            database_='neo4j',
        )
        # Connect events to their countries
        driver.execute_query(
            """
            UNWIND $items AS item
            MATCH (event:Event {id: item.id})
            UNWIND event.country_codes AS code
            MERGE (country:Country {code: code})
            MERGE (event)-[:IN_COUNTRY]->(country)
            """,
            items=node_data,
            database_='neo4j',
        )
        # Connect events to their hazards
        driver.execute_query(
            """
            UNWIND $items AS item
            MATCH (event:Event {id: item.id})
            UNWIND event.hazard_codes AS code
            MERGE (hazard:Hazard {code: code})
            MERGE (event)-[:HAS_HAZARD]->(hazard)
            """,
            items=node_data,
            database_='neo4j',
        )
        # Connect distinct events with the same start date
        driver.execute_query(
            """
            UNWIND $items AS item
            MATCH (item_node:Event {id: item.id})
            MATCH (other:Event)
            WHERE item_node.id <> other.id
            WITH CASE WHEN item_node.id < other.id THEN item_node ELSE other END AS source,
                 CASE WHEN item_node.id < other.id THEN other ELSE item_node END AS target
            WITH DISTINCT source, target
            WHERE source.corr_id <> target.corr_id
              AND date(source.start_datetime) = date(target.start_datetime)
            MERGE (source)-[relation:SAME_DAY_START]->(target)
            """,
            items=node_data,
            database_='neo4j',
        )


# Temporary local insertion
# TODO: Point at bucket once set up

def insert_from_local_data():
    """
    Helper to initialize a network using JSONL files under `data/`
    as a temporary placeholder for bucket access.

    Filters by an arbitrary date range for testing purposes.
    """
    data_folder = Path('data')
    valid_items: list[MontandonItem] = []
    for file in data_folder.rglob('*.jsonl'):
        with file.open(encoding='utf-8') as lines:
            for line in lines:
                if not line.strip():
                    continue
                valid_items.append(
                    MontandonItem.model_validate_json(line, by_name=True)
                )

    filtered_items = [
        item
        for item in valid_items
        if (
            datetime(2025, 12, 1, tzinfo=timezone.utc)
            <=item.properties.start_datetime
            <=datetime(2025, 12, 31, tzinfo=timezone.utc)
        )
    ]
    nodes = items_to_node_data(items=filtered_items)
    insert_records_into_graph_db(node_data=nodes)
    print('Insertion from local data complete!')


if __name__ == '__main__':
    insert_from_local_data()
