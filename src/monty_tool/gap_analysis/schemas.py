"""
Schemas for question templates, answer attempts, and paired results.
"""

# Imports

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from monty_tool.gap_analysis.external_sources import ExternalSourceName
from monty_tool.llm.schemas import QueryAssistantResponse


# Contribution categories

class Contribution(StrEnum):
    """
    Categories describing how external data contributes to answering a question.
    """
    IMPROVEMENT = 'improvement'
    NO_CHANGE = 'no_change'
    REGRESSION = 'regression'


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
    response: QueryAssistantResponse | None = None
    error: str | None = None
    duration_seconds: float

    @property
    def tool_failures(self) -> int:
        """
        Count failed tool calls made while answering the question.
        """
        if self.response is None:
            return 0
        return sum(
            step['result'].get('status') in ('error', 'limit_reached')
            for step in self.response['tool_results']
        )


class SavedAnswerAttempt(BaseModel):
    """
    Save data for an answer attempt, including an S3 response URI
    if a response was present.
    """
    response_s3_uri: str | None = None
    input_tokens: int | None = Field(ge=0)
    output_tokens: int | None = Field(ge=0)
    tool_failure_count: int = Field(ge=0)
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
    core_context: dict[str, Any]
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
    core_context: dict[str, Any]
    core: SavedAnswerAttempt
    external: SavedAnswerAttempt
    contribution: Contribution | None = None
    judge_error: str | None = None


# Run schemas

class SavedAnalysisRun(BaseModel):
    """
    Metadata and source comparisons written to the result JSON for one evaluation.
    """
    metadata: dict[str, Any]
    results: list[SavedPairedResult] = Field(default_factory=list)
