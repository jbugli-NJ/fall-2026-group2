"""
Utilities to initialize the local Neo4j graph database.
"""

# Imports

from monty_tool.network.resources import get_graph_db_driver


# Initialization helper

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

if __name__ == '__main__':
    initialize_db()
