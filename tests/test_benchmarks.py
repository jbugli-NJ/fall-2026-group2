"""
Tests for benchmark scoring and reports.
"""

# Imports

import csv
from datetime import datetime, timezone
from hashlib import sha256
from importlib.resources import files
from pathlib import Path
import subprocess

from pydantic import TypeAdapter
import pytest

from monty_tool.benchmarks.schemas import BenchmarkInput, BenchmarkOutput
from monty_tool.benchmarks import run_benchmark
from monty_tool.utils.versions import BENCHMARK_SEEDS, FrozenModel, SEED


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
    Run every default seed and preserve timestamped reports for explicit reruns.
    """
    input_dir = tmp_path / 'inputs'
    input_dir.mkdir()
    input_text = TypeAdapter(list[BenchmarkInput]).dump_json([FIRST_INPUT]).decode()
    input_bytes = (input_text + '\r\n').encode('utf-8')
    expected_hash = sha256(input_bytes).hexdigest()
    for name in ('20261006_demo', 'custom'):
        (input_dir / f'{name}.json').write_bytes(input_bytes)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(run_benchmark, '_INPUT_MODULE', input_dir)
    git_commit = 'a' * 40
    monkeypatch.setattr(run_benchmark, 'get_git_commit', lambda: git_commit)
    seeds = []
    monkeypatch.setattr(run_benchmark, 'set_seed', seeds.append)
    monkeypatch.setattr(run_benchmark, 'QueryAssistant', lambda **kwargs: None)
    monkeypatch.setattr(run_benchmark, 'run_question', lambda assistant, benchmark: BenchmarkOutput(
        benchmark_input=benchmark, score=1, duration_seconds=1,
    ))

    run_benchmark.main([])
    run_benchmark.main(['--input-json', 'custom.json'])

    for name in ('20261006_demo', 'custom'):
        output_dir = tmp_path / 'benchmarks' / name
        reports = list((output_dir / 'reports').glob('*.md'))
        assert len(reports) == len(BENCHMARK_SEEDS)
        assert not list(output_dir.glob('*.md'))
        report_seeds = set()
        for report in reports:
            text = report.read_text(encoding='utf-8')
            assert FIRST_INPUT.research_question in text
            assert f'Input SHA-256: {expected_hash}' in text
            assert f'Git commit: {git_commit}' in text
            report_seeds.add(next(line for line in text.splitlines() if line.startswith('Random seed:')))
            assert '_seed_' not in report.name
            timestamp = report.stem.rsplit('_', 1)[1]
            datetime.strptime(timestamp, '%Y%m%dT%H%M%S.%fZ')
        assert report_seeds == {f'Random seed: {seed}' for seed in BENCHMARK_SEEDS}
        with (output_dir / 'summary.csv').open(encoding='utf-8', newline='') as file:
            rows = list(csv.DictReader(file))
        assert len(rows) == len(BENCHMARK_SEEDS)
        assert all(row['questions_completed'] == '1' for row in rows)
        assert all(row['input_sha256'] == expected_hash for row in rows)
        assert all(row['git_commit'] == git_commit for row in rows)
        assert [row['seed'] for row in rows] == list(map(str, BENCHMARK_SEEDS))
        assert (output_dir / 'summary.svg').is_file()
    assert not (tmp_path / 'benchmarks' / 'summary.csv').exists()

    run_benchmark.main(['--no-enable-external'])
    output_dir = tmp_path / 'benchmarks' / '20261006_demo'
    reports_dir = output_dir / 'reports'
    report = next(reports_dir.glob('*_core_only_*.md'))
    assert '(core only)' in report.read_text(encoding='utf-8').splitlines()[0]
    assert len(list(reports_dir.glob('*.md'))) == 2 * len(BENCHMARK_SEEDS)
    with (output_dir / 'summary.csv').open(encoding='utf-8', newline='') as file:
        rows = list(csv.DictReader(file))
    assert len(rows) == 2 * len(BENCHMARK_SEEDS)
    assert rows[len(BENCHMARK_SEEDS)]['model'] == rows[0]['model'] + ' (core only)'

    for seed in BENCHMARK_SEEDS:
        previous_reports = set(reports_dir.glob('*.md'))
        run_benchmark.main(['--seed', str(seed)])
        new_reports = set(reports_dir.glob('*.md')) - previous_reports
        assert len(new_reports) == 1
        report = new_reports.pop()
        assert f'Random seed: {seed}' in report.read_text(encoding='utf-8')
    assert len(list(reports_dir.glob('*.md'))) == 3 * len(BENCHMARK_SEEDS)
    assert seeds == list(BENCHMARK_SEEDS) * 4
    with (output_dir / 'summary.csv').open(encoding='utf-8', newline='') as file:
        rows = list(csv.DictReader(file))
    assert [row['seed'] for row in rows] == list(map(str, BENCHMARK_SEEDS)) * 3
    plot = (output_dir / 'summary.svg').read_text(encoding='utf-8')
    assert 'seed 2028' in plot
    assert 'seed 2030' in plot
    assert expected_hash not in plot


def test_get_git_commit_uses_source_checkout(monkeypatch: pytest.MonkeyPatch):
    """
    Read the source commit independently of the benchmark output directory.
    """
    commit = 'b' * 40

    def check_output(command, *, cwd, text, stderr):
        assert command == ['git', 'rev-parse', 'HEAD']
        assert cwd == Path(run_benchmark.__file__).resolve().parent
        assert text is True
        assert stderr == subprocess.DEVNULL
        return commit + '\n'

    monkeypatch.setattr(run_benchmark.subprocess, 'check_output', check_output)
    assert run_benchmark.get_git_commit() == commit


@pytest.mark.parametrize('error', [FileNotFoundError(), subprocess.CalledProcessError(128, 'git')])
def test_get_git_commit_unavailable(monkeypatch: pytest.MonkeyPatch, error: Exception):
    """
    Allow benchmarks to run when Git or checkout metadata is unavailable.
    """
    def check_output(*args, **kwargs):
        raise error

    monkeypatch.setattr(run_benchmark.subprocess, 'check_output', check_output)
    assert run_benchmark.get_git_commit() == 'unknown'


def test_parser_rejects_unsupported_seed():
    """
    Reject seeds outside the supported reproducible benchmark set.
    """
    with pytest.raises(SystemExit) as error:
        run_benchmark.build_parser().parse_args(['--seed', '2027'])
    assert error.value.code == 2


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
    input_hash = sha256(_INPUT_TEXT.encode('utf-8')).hexdigest()
    git_commit = 'c' * 40
    run_benchmark.append_summary(
        path, model, outputs, started_at, 3, input_sha256=input_hash, git_commit=git_commit,
    )
    run_benchmark.append_summary(
        path, model, [], started_at, 0, input_sha256=input_hash, git_commit=git_commit,
    )
    with path.open(newline='') as file:
        rows = list(csv.DictReader(file))
    assert len(rows) == 2
    assert rows[0] == {
        'model': model.value,
        'revision': model.revision,
        'seed': str(SEED),
        'input_sha256': input_hash,
        'git_commit': git_commit,
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
