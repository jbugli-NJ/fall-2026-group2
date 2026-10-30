"""
Schemas for question templates, answer attempts, and paired results.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from monty_tool.llm.schemas import QueryAssistantResponse


class Contribution(StrEnum):
    """
    Categories describing how external data contributes to answering a question.
    """
    FILLS_FIELD = 'fills_field'
    EXTENDS_COVERAGE = 'extends_coverage'
    QUALIFIES = 'qualifies'
    CORROBORATES = 'corroborates'
    CORE_SUFFICIENT = 'core_sufficient'
    UNRESOLVED = 'unresolved'
    UNSUPPORTED = 'unsupported'


class QuestionTemplate(BaseModel):
    """
    A gap analysis question template with placeholders for event details.
    """
    id: str = Field(min_length=1)
    question: str = Field(min_length=1)


class GapQuestion(BaseModel):
    """
    A question generated for a specific disaster event.
    """
    event_id: str
    template_id: str
    question: str


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


class PairedResult(BaseModel):
    """
    Answers to the same question with and without external data, and their
    contribution judgment.
    """
    question: GapQuestion
    core_context: dict[str, Any]
    core: AnswerAttempt
    external: AnswerAttempt
    contribution: Contribution | None = None
    judge_error: str | None = None
