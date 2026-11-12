"""
Schemas for question templates, answer attempts, and paired results.
"""

# Imports

from datetime import date, datetime
from enum import StrEnum
from typing import Any, NotRequired, TypedDict

from neo4j.time import Date as Neo4jDate, DateTime as Neo4jDateTime
from pydantic import BaseModel, Field

from monty_tool.gap_analysis.external_sources import ExternalSourceName
from monty_tool.llm.tools import GraphSearchArguments


# Evidence schemas

# Retrieval keeps native temporal values; JSON archives contain their string forms.
type EvidenceDate = date | datetime | Neo4jDate | Neo4jDateTime | str


class WeatherEvidence(TypedDict):
    """
    Weather projection shared by event and impact queries.
    """
    source: str
    sources: list[str] | None
    latitude: float | None
    longitude: float | None
    elevation: float | None
    elevation_unit: str | None
    start_date: EvidenceDate | None
    end_date: EvidenceDate | None
    expected_days: int | None
    precipitation_measured_days: int | None
    temperature_mean_measured_days: int | None
    temperature_max_measured_days: int | None
    temperature_min_measured_days: int | None
    wind_speed_measured_days: int | None
    observed_precipitation_total: float | None
    peak_daily_precipitation: float | None
    peak_daily_precipitation_date: EvidenceDate | None
    mean_temperature: float | None
    max_temperature: float | None
    min_temperature: float | None
    max_daily_mean_wind_speed: float | None
    precipitation_unit: str | None
    observed_precipitation_total_unit: str | None
    temperature_mean_unit: str | None
    temperature_max_unit: str | None
    temperature_min_unit: str | None
    wind_speed_unit: str | None


class FootprintEvidence(TypedDict):
    """
    Footprint summary projected by retrieve_events.cypher.
    """
    source: str
    layer: str
    label: str
    detail: str
    area_name: str | None
    size_km: float | None
    severity_property: str | None
    severity_levels: list[str] | None
    data_url: str


class ImpactEvidence(TypedDict):
    """
    Impact projection, with weather omitted from passes that exclude it.
    """
    impact_id: str
    title: str | None
    description: str | None
    impact_type: str | None
    impact_category: str | None
    impact_value: int | float | None
    impact_unit: str | None
    estimate_type: str | None
    weather: NotRequired[WeatherEvidence | None]


class EventEvidence(TypedDict):
    """
    Event projection with optional external fields for source-specific passes.
    """
    event_id: str
    title: str | None
    description: str | None
    start_datetime: EvidenceDate | None
    end_datetime: EvidenceDate | None
    correlation_id: str | None
    country_codes: list[str]
    hazard_codes: list[str]
    footprints: list[FootprintEvidence]
    impacts: list[ImpactEvidence]
    weather: NotRequired[WeatherEvidence | None]
    external_footprints: NotRequired[list[FootprintEvidence]]


class AppealEvidence(TypedDict):
    """
    Appeal projection returned with an IFRC response event.
    """
    appeal_id: str
    title: str | None
    code: str | None
    sector: str | None
    country_name: str | None
    country_society_name: str | None
    status: str | None
    appeal_type: str | None
    beneficiaries: int | None
    amount_requested: float | None
    amount_funded: float | None
    start_datetime: EvidenceDate | None
    end_datetime: EvidenceDate | None


class ResponseEvidence(TypedDict):
    """
    IFRC event projection returned by retrieve_responses.cypher.
    """
    event_id: str
    title: str | None
    disaster_type: str | None
    start_datetime: EvidenceDate | None
    summary: str | None
    severity: str | None
    people_affected: int | None
    active_deployments: int | None
    appeals: list[AppealEvidence]


class NewsEvidence(TypedDict):
    """
    Verified article projection returned by retrieve_news.cypher.
    """
    event_id: str
    title: str | None
    url: str
    source_name: str | None
    published_at: EvidenceDate | None
    evidence_excerpt: str | None
    body_excerpt: str | None


class Evidence(TypedDict):
    """
    Retrieved or archived records available to a country–hazard comparison.
    """
    events: list[EventEvidence]
    responses: list[ResponseEvidence]
    news: NotRequired[list[NewsEvidence]]


# Contribution categories

class Contribution(StrEnum):
    """
    Categories describing how external data contributes to answering a question.
    """
    IMPROVEMENT = 'improvement'
    NO_CHANGE = 'no_change'
    REGRESSION = 'regression'


# Selection schema

class GapSelection(BaseModel):
    """
    A selected country and hazard with validated event start date bounds.
    """
    filters: GraphSearchArguments
    hazard_name: str = Field(min_length=1)


# Question schemas

class QuestionTemplate(BaseModel):
    """
    A gap analysis question template with country and hazard placeholders.
    """
    id: str = Field(min_length=1)
    question: str = Field(min_length=1)


class GapQuestion(BaseModel):
    """
    A question generated for EAP planning in a country for a hazard.
    """
    country_code: str
    hazard_name: str
    template_id: str
    question: str


# Answer schemas

class AnswerAttempt(BaseModel):
    """
    The answer, errors, and elapsed time from attempting one question.
    """
    answer: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    error: str | None = None
    duration_seconds: float


class SavedAnswerAttempt(BaseModel):
    """
    Save data for an answer attempt, including an S3 answer URI
    if an answer was produced.
    """
    answer_s3_uri: str | None = None
    input_tokens: int | None = Field(ge=0)
    output_tokens: int | None = Field(ge=0)
    error: str | None = None
    duration_seconds: float


# Result schemas

class PairedResult(BaseModel):
    """
    Two answers to the same question and a judgment of what external data adds.

    The core answer uses Montandon records alone. The external answer also has
    access to the specified external source.
    """
    external_source: ExternalSourceName
    question: GapQuestion
    selection: GapSelection
    evidence_s3_uri: str = Field(min_length=1)
    core: AnswerAttempt
    external: AnswerAttempt
    contribution: Contribution | None = None
    judge_error: str | None = None


class SavedPairedResult(BaseModel):
    """
    A question, archived answer references, and the contribution judgment.
    """
    external_source: ExternalSourceName
    question: GapQuestion
    selection: GapSelection
    evidence_s3_uri: str = Field(min_length=1)
    core: SavedAnswerAttempt
    external: SavedAnswerAttempt
    contribution: Contribution | None = None
    judge_error: str | None = None


# Summary schemas

class ModelInfo(TypedDict):
    """
    Model identity included in run metadata and summary legends.
    """
    id: str
    revision: NotRequired[str]


class QuestionSummary(TypedDict):
    """
    Counts for one question template and external source.
    """
    template_id: str
    external_source: ExternalSourceName
    attempted: int
    evaluated: int
    improved: int
    improvement_rate: float | None
    answer_failures: int
    judge_failures: int
    categories: dict[str, int]


class ModelSummary(TypedDict):
    """
    Per-question counts for an answering and judging model combination.
    """
    answer_model: ModelInfo
    judge_model: ModelInfo
    results: list[QuestionSummary]


# Run schemas

class SavedAnalysisRun(BaseModel):
    """
    Metadata and source comparisons written to the result JSON for one evaluation.
    """
    metadata: dict[str, Any]
    results: list[SavedPairedResult] = Field(default_factory=list)
