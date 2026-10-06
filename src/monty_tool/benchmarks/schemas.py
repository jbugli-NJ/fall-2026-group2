"""
Schemas for benchmark questions and their verification data.
"""

# Imports

import json

from pydantic import BaseModel, ConfigDict, Field

from monty_tool.llm.schemas import QueryAssistantResponse


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


class BenchmarkOutput(BaseModel):
    """
    One question's score, assistant response, and error if present.
    """
    model_config = ConfigDict(extra='forbid')

    benchmark_input: BenchmarkInput
    score: float = Field(ge=0, le=1)
    duration_seconds: float = Field(ge=0)
    response: QueryAssistantResponse | None = None
    error: str | None = None

    def to_md(self, num: int) -> str:
        """
        Render the answer output as markdown (indented bullets).
        """
        benchmark = self.benchmark_input
        tool_calls = len(self.response['tool_results']) if self.response is not None else 'unavailable'
        bullets = [
            f'- **Question {num}: {benchmark.research_question}**',
            f'  - **Score:** {self.score:.2%} ({self.duration_seconds:.2f}s)  \n'
            f'  - **Tool calls:** {tool_calls}',
        ]
        if self.response is not None:
            for tool_result in self.response['tool_results']:
                call = tool_result['call']
                bullets.append(
                    f'    - {call["name"]}: {json.dumps(call["arguments"], ensure_ascii=False)}'
                )
            bullets.append(f'  - **Answer:** {self.response['answer']}')
        bullets.append('  - **Expected:** ' + '; '.join(benchmark.full_answer_substrings))
        if benchmark.partial_answer_substrings:
            bullets.append('    - **Partial credit:** ' + '; '.join(benchmark.partial_answer_substrings))
        if self.error is not None:
            bullets.append(f'**Error:** {self.error}')
        return '\n\n'.join(bullets)
