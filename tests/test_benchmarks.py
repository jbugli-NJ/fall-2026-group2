"""
Tests for benchmark scoring and reports.
"""

# Imports

import csv
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path

from pydantic import TypeAdapter
import pytest

from monty_tool.benchmarks.schemas import BenchmarkInput, BenchmarkOutput
from monty_tool.benchmarks import run_benchmark
from monty_tool.utils.versions import FrozenModel


# Benchmark inputs

_INPUT_TEXT = (
    files('monty_tool.benchmarks.inputs')
    .joinpath('20261006_demo.json')
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


def test_main_separates_results_by_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    Confirm that benchmark outputs are saved in dedicated folders.
    """
    input_dir = tmp_path / 'inputs'
    input_dir.mkdir()
    input_text = TypeAdapter(list[BenchmarkInput]).dump_json([FIRST_INPUT]).decode()
    for name in ('20261006_demo', 'custom'):
        (input_dir / f'{name}.json').write_text(input_text, encoding='utf-8')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(run_benchmark, '_INPUT_MODULE', input_dir)
    monkeypatch.setattr(run_benchmark, 'set_seed', lambda seed: None)
    monkeypatch.setattr(run_benchmark, 'QueryAssistant', lambda **kwargs: None)
    monkeypatch.setattr(run_benchmark, 'run_question', lambda assistant, benchmark: BenchmarkOutput(
        benchmark_input=benchmark, score=1, duration_seconds=1,
    ))

    run_benchmark.main([])
    run_benchmark.main(['--input-json', 'custom.json'])

    for name in ('20261006_demo', 'custom'):
        output_dir = tmp_path / 'benchmarks' / name
        reports = list(output_dir.glob('*.md'))
        assert len(reports) == 1
        assert FIRST_INPUT.research_question in reports[0].read_text(encoding='utf-8')
        with (output_dir / 'summary.csv').open(encoding='utf-8', newline='') as file:
            rows = list(csv.DictReader(file))
        assert len(rows) == 1
        assert rows[0]['questions_completed'] == '1'
        assert (output_dir / 'summary.svg').is_file()
    assert not (tmp_path / 'benchmarks' / 'summary.csv').exists()


def test_scoring():
    """
    Check full, partial, and zero credit calculation logic.
    """
    benchmark = FIRST_INPUT.model_copy(update={
        'full_answer_substrings': ['Exact 1700.3'],
        'partial_answer_substrings': ['Exact 1700'],
    })
    for answer, score in [
        ('EXACT 1700.3', 1), ('exact 1,700.3', 1),
        ('exact 1700', .5), ('exact 1700.9', .5), ('exact 1,700.9', .5),
        ('exact 11700.9', 0), ('unknown', 0),
        ]:
        assert run_benchmark.score_answer(benchmark, answer) == score


@pytest.mark.parametrize(('expected', 'answer', 'matches'), [
    ('cat', 'A CAT, indeed.', True),
    ('cat', 'concatenate cats cat_1', False),
    ('1700.3', 'The answer is **1700.3**.', True),
    ('1,851,409', 'The answer is 1,851,409.', True),
    ('1851409', '1,851,409', True),
    ('1,851,409', '1851409', True),
    ('1851409', 'The answer is 1,851,409, according to the report.', True),
    ('1700.3', '1,700.3', True),
    ('1,700.3', '1700.3', True),
    ('1700.3', '1700x3', False),
    ('1700.3', '11700.3', False),
    ('1700.3', '1700.34', False),
    ('3', '13 30 3people', False),
    ('3', '3,000', False),
    ('3', '3.5', True),
    ('3', '3. Next sentence.', True),
    ('3', '3, 4', True),
    ('3000', '30,00', True),
    ('34', '3,4', True),
    ('1234567', '12,34,567', True),
])
@pytest.mark.parametrize('credit', [1.0, 0.5])
def test_bounded_scoring(expected: str, answer: str, matches: bool, credit: float):
    """
    Check word boundaries and comma variants for both credit levels.
    """
    benchmark = FIRST_INPUT.model_copy(update={
        'full_answer_substrings': [expected] if credit == 1.0 else ['unmatched'],
        'partial_answer_substrings': [expected] if credit == 0.5 else [],
    })
    assert run_benchmark.score_answer(benchmark, answer) == (credit if matches else 0.0)


def test_output():
    """
    Confirms that expected LLM response elements are in the rendered markdown.
    """
    output = BenchmarkOutput(
        benchmark_input=FIRST_INPUT, score=1, duration_seconds=0,
        response={'answer': '3,000', 'tool_results': [{
            'call': {'name': 'search_disaster_events', 'arguments': {'text': 'Sri Lanka'}},
            'result': 'private trace',
        }]},
    )
    assert output.response is not None
    assert run_benchmark.score_answer(FIRST_INPUT, output.response['answer']) == 1.0
    markdown = output.to_md(1)
    assert FIRST_INPUT.research_question in markdown
    assert '**Answer:** 3,000' in markdown
    assert '**Tool calls:** 1' in markdown
    assert '    - search_disaster_events: {"text": "Sri Lanka"}' in markdown
    assert 'private trace' not in markdown


def test_summary_appends_runs(tmp_path: Path):
    """
    Preserve earlier runs and aggregate mixed scores, failures, and empty runs.
    """
    path = tmp_path / 'summary.csv'
    started_at = datetime(2026, 10, 4, tzinfo=timezone.utc)
    outputs = [
        BenchmarkOutput(benchmark_input=FIRST_INPUT, score=1, duration_seconds=2),
        BenchmarkOutput(benchmark_input=FIRST_INPUT, score=.5, duration_seconds=3),
        BenchmarkOutput(benchmark_input=FIRST_INPUT, score=0, duration_seconds=4, error='failed'),
    ]
    model = FrozenModel.QWEN3_1_7B
    run_benchmark.append_summary(path, model, outputs, started_at, 3)
    run_benchmark.append_summary(path, model, [], started_at, 0)
    with path.open(newline='') as file:
        rows = list(csv.DictReader(file))
    assert len(rows) == 2
    assert rows[0] == {
        'model': model.value,
        'revision': model.revision,
        'started_at_utc': started_at.isoformat(),
        'questions_completed': '3',
        'total_questions': '3',
        'average_score': '0.5',
        'full_credit': '1',
        'partial_credit': '1',
        'zero_credit': '1',
        'failures': '1',
        'total_seconds': '9.0',
    }
    assert rows[1]['questions_completed'] == '0'
    assert rows[1]['average_score'] == '0.0'
