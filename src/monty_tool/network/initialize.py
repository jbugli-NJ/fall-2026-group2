"""
Utilities to initialize the local Neo4j graph database.
"""

# Imports

from typing import LiteralString, cast

from neo4j import Query

from monty_tool.embeddings.generate import EMBEDDING_DIMENSIONS
from monty_tool.network.resources import (
    MONTANDON_DESCRIPTION_VECTOR_INDEX,
    MONTANDON_IMPACT_VECTOR_INDEX,
    MONTANDON_KEYWORDS_VECTOR_INDEX,
    NETWORK_INSERT_BATCH_SIZE,
    get_graph_db_driver,
)


# Initialization helper

def clear_db():
    """
    Delete every node and relationship from the local network database.
    """
    with get_graph_db_driver() as driver:
        while True:
            _, summary, _ = driver.execute_query(
                """
                MATCH (node)
                WITH node
                LIMIT $batch_size
                DETACH DELETE node
                """,
                batch_size=NETWORK_INSERT_BATCH_SIZE,
                database_='neo4j',
            )
            if summary.counters.nodes_deleted == 0:
                break


def initialize_db():
    """
    Create constraints required by the local Neo4j graph database.
    """
    with get_graph_db_driver() as driver:
        driver.execute_query(
            """
            CREATE CONSTRAINT montandon_item_id_unique IF NOT EXISTS
            FOR (node:MontandonItem)
            REQUIRE node.id IS UNIQUE
            """,
            database_='neo4j',
        )
        driver.execute_query(
            """
            CREATE CONSTRAINT event_day_date_unique IF NOT EXISTS
            FOR (node:EventDay)
            REQUIRE node.date IS UNIQUE
            """,
            database_='neo4j',
        )
        driver.execute_query(
            """
            CREATE INDEX event_corr_id IF NOT EXISTS
            FOR (node:Event)
            ON (node.corr_id)
            """,
            database_='neo4j',
        )
        driver.execute_query(
            """
            CREATE CONSTRAINT news_article_url_unique IF NOT EXISTS
            FOR (node:NewsArticle)
            REQUIRE node.url IS UNIQUE
            """,
            database_='neo4j',
        )

def initialize_vector_indexes():
    """
    Create and await the vector indexes used for similarity links.
    """
    index_definitions = [
        (MONTANDON_DESCRIPTION_VECTOR_INDEX, 'description_embedding'),
        (MONTANDON_KEYWORDS_VECTOR_INDEX, 'keywords_embedding'),
        (MONTANDON_IMPACT_VECTOR_INDEX, 'impact_severity_embedding'),
    ]
    with get_graph_db_driver() as driver:
        for index_name, property_name in index_definitions:
            driver.execute_query(
                Query(
                    cast(
                        LiteralString,
                        f"""
                        CREATE VECTOR INDEX `{index_name}` IF NOT EXISTS
                        FOR (node:MontandonItem)
                        ON (node.`{property_name}`)
                        OPTIONS {{indexConfig: {{
                            `vector.dimensions`: {EMBEDDING_DIMENSIONS},
                            `vector.similarity_function`: 'cosine'
                        }}}}
                        """,
                    ),
                ),
                database_='neo4j',
            )
        driver.execute_query(
            'CALL db.awaitIndexes($timeout_seconds)',
            timeout_seconds=300,
            database_='neo4j',
        )

if __name__ == '__main__':
    initialize_db()
