"""
Schemas for data stored in the Neo4j graph.
"""

# Imports

from datetime import datetime
from typing import NotRequired, TypedDict

from monty_tool.embeddings.schemas import Embedding


# Schema

class NodeData(TypedDict):
    """
    Data from one Montandon record for graph insert.
    """
    id: str
    title: str
    description: str
    roles: list[str]
    description_embedding: Embedding
    keywords_embedding: Embedding | None
    impact_severity_embedding: Embedding | None
    corr_id: str
    country_codes: list[str]
    hazard_codes: list[str]
    start_datetime: datetime
    end_datetime: datetime
    impact_type: NotRequired[str]
    impact_value: NotRequired[int | float]
    impact_unit: NotRequired[str]
    impact_category: NotRequired[str]
    impact_estimate_type: NotRequired[str]
