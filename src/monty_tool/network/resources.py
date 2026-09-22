"""
Resources for connecting to the local Neo4j instance.
"""

# Imports

from neo4j import GraphDatabase


# Resources

_DATABASE_URI = 'neo4j://localhost:7687'


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
