"""
Resources for connecting to the local Neo4j instance.
"""

# Imports

from neo4j import GraphDatabase


# Resources

_DATABASE_URI = 'neo4j://localhost:7687'

MONTANDON_DESCRIPTION_VECTOR_INDEX = (
    'montandon_description_embedding_index'
)
MONTANDON_KEYWORDS_VECTOR_INDEX = 'montandon_keywords_embedding_index'
MONTANDON_IMPACT_VECTOR_INDEX = 'montandon_impact_embedding_index'

SIMILARITY_NEIGHBOR_LIMIT = 20
SIMILARITY_THRESHOLD = 0.95
NETWORK_INSERT_BATCH_SIZE = 1000


# Connection helper

def get_graph_db_driver():
    """
    Instantiate a driver connected to the local Neo4j instance.
    """
    driver = GraphDatabase.driver(
        _DATABASE_URI,
        auth=('neo4j', 'password'),
    )
    return driver
