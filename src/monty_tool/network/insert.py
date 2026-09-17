"""
Insert Montandon records into a local Neo4j instance.
"""

# Imports

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
        # Connect records with the same correlation ID
        driver.execute_query(
            _BATCH_PAIR_MATCH + """
            WHERE source.corr_id = target.corr_id
            MERGE (source)-[relation:SAME_EVENT]->(target)
            SET relation.corr_id = source.corr_id
            """,
            items=node_data,
            database_='neo4j',
        )
        # Connect records using the various computed embeddings
        driver.execute_query(
            _BATCH_PAIR_MATCH + """
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
            WHERE source.keywords_embedding IS NOT NULL
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
            WHERE source.impact_severity_embedding IS NOT NULL
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
        # Connect records that share country codes
        driver.execute_query(
            _BATCH_PAIR_MATCH + """
            WITH source, target,
                 [code IN source.country_codes
                  WHERE code IN target.country_codes] AS country_codes
            WHERE size(country_codes) > 0
            MERGE (source)-[relation:SAME_COUNTRY]->(target)
            SET relation.country_codes = country_codes,
                relation.strength = toFloat(size(country_codes)) /
                    (size(source.country_codes) + size(target.country_codes)
                     - size(country_codes))
            """,
            items=node_data,
            database_='neo4j',
        )
        # Connect records that share hazard codes
        driver.execute_query(
            _BATCH_PAIR_MATCH + """
            WITH source, target,
                 [code IN source.hazard_codes
                  WHERE code IN target.hazard_codes] AS hazard_codes
            WHERE size(hazard_codes) > 0
            MERGE (source)-[relation:SAME_HAZARD]->(target)
            SET relation.hazard_codes = hazard_codes,
                relation.strength = toFloat(size(hazard_codes)) /
                    (size(source.hazard_codes) + size(target.hazard_codes)
                     - size(hazard_codes))
            """,
            items=node_data,
            database_='neo4j',
        )
        # Connect records with overlapping date ranges
        driver.execute_query(
            _BATCH_PAIR_MATCH + """
            WHERE source.start_datetime <= target.end_datetime
              AND target.start_datetime <= source.end_datetime
            MERGE (source)-[relation:OVERLAPPING_TIME]->(target)
            """,
            items=node_data,
            database_='neo4j',
        )
