"""
Schemas for benchmark questions and their verification data.
"""

# Imports

from pydantic import BaseModel, ConfigDict, Field, RootModel


# Schemas

class BenchmarkSourceNode(BaseModel):
    """
    The label and ID needed to locate a benchmark's source node.
    """
    model_config = ConfigDict(extra='forbid')

    label: str = Field(min_length=1)
    id: str = Field(min_length=1)


class BenchmarkInput(BaseModel):
    """
    One research question with acceptable answers and graph references.
    """
    model_config = ConfigDict(extra='forbid')

    research_question: str = Field(min_length=1)
    full_answer_substrings: list[str] = Field(min_length=1)
    partial_answer_substrings: list[str] = Field(default_factory=list)
    source_nodes: list[BenchmarkSourceNode] = Field(min_length=1)
    verification_query: str = Field(min_length=1)
