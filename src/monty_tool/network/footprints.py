"""
Utilities to summarise hazard footprints and link them to existing graph events.
"""

# Imports

from collections.abc import Iterator
from pathlib import Path

from neo4j import Driver, ManagedTransaction
from pydantic import TypeAdapter, ValidationError

from monty_tool.geo.collect import ORIGINS, severity_levels
from monty_tool.geo.schemas import Footprint
from monty_tool.network.schemas import FootprintNodeData
from monty_tool.tools.resources import read_gzip


# Resources

FOOTPRINT_NODE_DATA_ADAPTER = TypeAdapter(FootprintNodeData)


# Utilities

def footprint_to_node_data(footprint: Footprint, corr_id: str) -> FootprintNodeData:
    """
    Convert a footprint into flat graph properties, dropping its outline.
    """
    return {
        'id': f'{footprint.item_id}#{footprint.layer}',
        'item_id': footprint.item_id,
        'corr_id': corr_id,
        'source': footprint.source,
        'origin': ORIGINS[footprint.source],
        'layer': footprint.layer,
        'label': footprint.label,
        'detail': footprint.detail,
        'datetime': footprint.datetime,
        'area_name': footprint.area_name,
        'size_km': footprint.size_km,
        'feature_count': footprint.feature_count,
        'severity_property': footprint.severity_property,
        'severity_levels': severity_levels(footprint),
        'data_url': footprint.data_url,
        'source_url': footprint.source_url,
    }


def validated_footprint_node_data(path: Path) -> Iterator[FootprintNodeData]:
    """
    Read footprint rows with validation errors identifying their path and line.
    """
    for line_number, record in enumerate(read_gzip(path), start=1):
        try:
            yield FOOTPRINT_NODE_DATA_ADAPTER.validate_python(record)
        except ValidationError as error:
            raise ValueError(f'Invalid footprint data in {path} at line {line_number}') from error


def insert_footprints(driver: Driver, node_data: list[FootprintNodeData]) -> tuple[int, int]:
    """
    Link footprints to every event sharing their correlation ID, returning
    the linked and unmatched counts. Unmatched footprints are left out,
    since their source's events may not be loaded.
    """
    def attach(tx: ManagedTransaction) -> tuple[int, int]:
        result = tx.run(
            """
            UNWIND $items AS item
            OPTIONAL MATCH (event:Event {corr_id: item.corr_id})
            WITH item, collect(event) AS events
            FOREACH (event IN events |
                MERGE (footprint:Footprint {id: item.id})
                SET footprint += item
                MERGE (footprint)-[:FOOTPRINT_OF]->(event)
            )
            RETURN count(CASE WHEN size(events) > 0 THEN 1 END) AS linked,
                   count(CASE WHEN size(events) = 0 THEN 1 END) AS unmatched
            """,
            items=node_data,
        )
        record = result.single(strict=True)
        return record['linked'], record['unmatched']

    with driver.session(database='neo4j') as session:
        return session.execute_write(attach)
