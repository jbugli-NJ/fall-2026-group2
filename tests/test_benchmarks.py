"""
Tests for benchmark scoring and reports.
"""

# Imports

from importlib.resources import files

from pydantic import TypeAdapter

from monty_tool.benchmarks.schemas import BenchmarkInput, BenchmarkOutput
from monty_tool.benchmarks import run_benchmark


# Benchmark inputs

_INPUT_TEXT = (
    files('monty_tool.benchmarks')
    .joinpath('benchmark_inputs.json')
    .read_text(encoding='utf-8')
)
INPUTS = TypeAdapter(list[BenchmarkInput]).validate_json(_INPUT_TEXT)
FIRST_INPUT =  INPUTS[0]


# Tests

def test_benchmark_input_length():
    """
    Confirms that the benchmark question listing is above a set minimum.
    """
    assert len(INPUTS) > 25


def test_scoring():
    """
    Check full, partial, and zero credit calculation logic.
    """
    benchmark = FIRST_INPUT.model_copy(update={
        'full_answer_substrings': ['Exact 1700.3', 'Exact 1,700.3'],
        'partial_answer_substrings': ['Exact 1700'],
    })
    for answer, score in [('EXACT 1700.3', 1), ('exact 1,700.3', 1), ('exact 1700', .5), ('unknown', 0)]:
        assert run_benchmark.score_answer(benchmark, answer) == score


def test_output():
    """
    Confirms that expected LLM response elements are in the rendered markdown.
    """
    output = BenchmarkOutput(
        benchmark_input=FIRST_INPUT, score=1, duration_seconds=0,
        response={'answer': '3000', 'tool_results': [{'result': 'private trace'}]},
    )
    markdown = output.to_md(1)
    assert FIRST_INPUT.research_question in markdown
    assert '**Answer:** 3000' in markdown
    assert '**Tool calls:** 1' in markdown
    assert 'private trace' not in markdown
