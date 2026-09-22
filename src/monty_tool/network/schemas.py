"""
Schemas for data stored in the Neo4j graph.
"""

# Imports

from datetime import datetime
from typing import NotRequired, TypedDict

from monty_tool.embeddings.schemas import Embedding


# Schema

class BaseNodeData(TypedDict):
    """
    Properties shared by all source-record nodes in the graph.
    """
    id: str
    title: str


class MontandonItemNodeData(BaseNodeData):
    """
    Data from one Montandon record for graph insertion.
    """
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


class GOEventNodeData(BaseNodeData):
    """
    Data from one IFRC GO event for graph insertion.
    """
    go_event_id: int
    disaster_type_id: int | None
    disaster_type_name: str | None
    country_codes: list[str]
    country_names: list[str]
    num_affected: int | None
    ifrc_severity_level: int
    ifrc_severity_level_display: str
    ifrc_severity_level_update_date: datetime | None
    glide: str
    start_datetime: datetime
    created_at: datetime
    updated_at: datetime
    active_deployments: int
    summary: str
    original_language: str | None


class GOAppealNodeData(BaseNodeData):
    """
    Data from one IFRC GO appeal for graph insertion.
    """
    go_appeal_id: str
    aid: str
    go_event_id: int | None
    disaster_type_id: int
    disaster_type_name: str
    appeal_type: int
    appeal_type_display: str
    status: int
    status_display: str
    code: str
    sector: str
    num_beneficiaries: int
    amount_requested: float
    amount_funded: float
    start_datetime: datetime
    end_datetime: datetime
    real_data_update: datetime | None
    created_at: datetime
    modified_at: datetime
    needs_confirmation: bool
    country_code: str | None
    country_name: str
    country_go_id: int
    country_fdrs: str | None
    country_society_name: str | None
    region_go_id: int
    region_name: str
