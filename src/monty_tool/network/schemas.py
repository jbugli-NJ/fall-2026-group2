"""
Schemas for data stored in the Neo4j graph.
"""

# Imports

from datetime import date, datetime
from typing import NotRequired, TypedDict

from monty_tool.embeddings.schemas import Embedding


# Schema

class WeatherNodeProperties(TypedDict):
    """
    Flat weather properties attached to an existing Montandon node.
    """
    weather_source: str
    weather_sources: list[str]
    weather_latitude: float
    weather_longitude: float
    weather_elevation: float | None
    weather_elevation_unit: str | None
    weather_start_date: date
    weather_end_date: date
    weather_expected_days: int
    weather_precipitation_measured_days: int
    weather_temperature_mean_measured_days: int
    weather_temperature_max_measured_days: int
    weather_temperature_min_measured_days: int
    weather_wind_speed_measured_days: int
    weather_observed_precipitation_total: float | None
    weather_peak_daily_precipitation: float | None
    weather_peak_daily_precipitation_date: date | None
    weather_mean_temperature: float | None
    weather_max_temperature: float | None
    weather_min_temperature: float | None
    weather_max_daily_mean_wind_speed: float | None
    weather_precipitation_unit: str | None
    weather_observed_precipitation_total_unit: str | None
    weather_temperature_mean_unit: str | None
    weather_temperature_max_unit: str | None
    weather_temperature_min_unit: str | None
    weather_wind_speed_unit: str | None


class WeatherNodeData(TypedDict):
    """
    Identify an existing Montandon node and its weather properties.
    """
    item_id: str
    properties: WeatherNodeProperties

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

class NewsArticleInsertData(TypedDict):
    """One article and the event and S3 snapshot it came from."""

    url: str
    title: str
    description: str | None
    source_id: str | None
    source_name: str
    published_at: datetime
    event_id: str
    snapshot_s3_uri: str


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
