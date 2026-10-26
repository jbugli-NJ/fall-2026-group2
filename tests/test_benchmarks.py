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
from unittest.mock import Mock, call

import numpy as np
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
INPUT_BYTES = TypeAdapter(list[BenchmarkInput]).dump_json([FIRST_INPUT]) + b'\r\n'
GIT_COMMIT = 'a' * 40


# Fixtures and helpers

@pytest.fixture
def benchmark_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Prepare local inputs and replace model inference for command-line tests.
    """
    input_dir = tmp_path / 'inputs'
    input_dir.mkdir()
    for name in ('20261006_demo', 'custom'):
        (input_dir / f'{name}.json').write_bytes(INPUT_BYTES)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(run_benchmark, '_INPUT_MODULE', input_dir)
    monkeypatch.setattr(run_benchmark, 'get_git_commit', lambda: GIT_COMMIT)
    monkeypatch.setattr(run_benchmark, 'set_seed', lambda seed: None)
    assistant = Mock(spec=run_benchmark.QueryAssistant)
    assistant.ask.return_value = {
        'answer': f'<{FIRST_INPUT.full_answer_substrings[0]}>',
        'tool_results': [],
    }
    monkeypatch.setattr(run_benchmark, 'QueryAssistant', Mock(return_value=assistant))
    return tmp_path


def read_summary(output_dir: Path) -> list[dict[str, str]]:
    """
    Read completed run metadata from an input's output directory.
    """
    with (output_dir / 'summary.csv').open(encoding='utf-8', newline='') as file:
        return list(csv.DictReader(file))


# Tests

def test_benchmark_input_length():
    """
    Confirms that the benchmark question listing is above a set minimum.
    """
    assert len(INPUTS) > 25


@pytest.mark.parametrize('use_tools', [True, False])
def test_main_saves_results_and_metadata(benchmark_workspace: Path, use_tools: bool):
    """
    Save reports and comparison files under the selected input's directory.
    """
    args = ['--input-json', 'custom.json', '--seed', str(SEED)]
    run_benchmark.main(args)

    output_dir = benchmark_workspace / 'benchmarks' / 'custom'
    reports = list(output_dir.joinpath('reports').glob('*.md'))
    assert len(reports) == 2
    model = FrozenModel.QWEN3_5_4B.value + ('' if use_tools else ' (no tools)')
    text, = [report.read_text(encoding='utf-8') for report in reports
             if report.read_text(encoding='utf-8').startswith(f'# Benchmark: {model}\n')]
    assert text.startswith(f'# Benchmark: {model}\n')
    assert FIRST_INPUT.research_question in text
    assert f'Random seed: {SEED}' in text
    assert f'Input SHA-256: {sha256(INPUT_BYTES).hexdigest()}' in text
    assert f'Git commit: {GIT_COMMIT}' in text
    rows = read_summary(output_dir)
    assert len(rows) == 2
    row, = [row for row in rows if row['model'] == model]
    assert row['model'] == model
    assert row['questions_completed'] == '1'
    assert row['average_score'] == '1.0'
    assert row['input_sha256'] == sha256(INPUT_BYTES).hexdigest()
    assert row['git_commit'] == GIT_COMMIT
    assert row['seed'] == str(SEED)
    assert (output_dir / 'summary.svg').is_file()


@pytest.mark.parametrize('args, expected_seeds', [
    ([], list(BENCHMARK_SEEDS)),
    (['--seed', '2028'], [2028]),
])
def test_main_seed_selection(
    benchmark_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
    expected_seeds: list[int],
):
    """
    Run all seeds by default, or only the explicitly selected seed.
    """
    seeds: list[int] = []
    monkeypatch.setattr(run_benchmark, 'set_seed', seeds.append)

    run_benchmark.main(args)

    output_dir = benchmark_workspace / 'benchmarks' / '20261006_demo'
    assert seeds == [expected_seeds[0], *(seed for seed in expected_seeds for _ in (True, False))]
    assistant_factory = run_benchmark.QueryAssistant
    assert isinstance(assistant_factory, Mock)
    assistant_factory.assert_called_once_with(model_id=FrozenModel.QWEN3_5_4B)
    assert [row['seed'] for row in read_summary(output_dir)] == [str(seed) for seed in expected_seeds for _ in (True, False)]
    assert len(list((output_dir / 'reports').glob('*.md'))) == 2 * len(expected_seeds)


@pytest.mark.parametrize('use_tools', [True, False])
def test_main_uses_bedrock(benchmark_workspace: Path, monkeypatch: pytest.MonkeyPatch, use_tools: bool):
    """
    Select the Bedrock assistant and record its actual model ID in the results.
    """
    assistant = Mock(spec=run_benchmark.BedrockQueryAssistant)
    assistant.ask.return_value = {'answer': f'<{FIRST_INPUT.full_answer_substrings[0]}>', 'tool_results': []}
    bedrock = Mock(return_value=assistant)
    monkeypatch.setattr(run_benchmark, 'BedrockQueryAssistant', bedrock)
    args = ['--model', 'bedrock', '--seed', str(SEED)]

    run_benchmark.main(args)

    bedrock.assert_called_once_with()
    assert assistant.ask.call_args_list == [
        call(FIRST_INPUT.research_question, use_tools=True),
        call(FIRST_INPUT.research_question, use_tools=False),
    ]
    output_dir = benchmark_workspace / 'benchmarks' / '20261006_demo'
    rows = read_summary(output_dir)
    assert len(rows) == 2
    model_id = run_benchmark.DEFAULT_BEDROCK_MODEL_ID + ('' if use_tools else ' (no tools)')
    row, = [row for row in rows if row['model'] == model_id]
    assert row['model'] == model_id
    assert row['revision'] == 'not applicable'
    assert row['average_score'] == '1.0'
    report, = [report for report in output_dir.joinpath('reports').glob('bedrock_*.md')
               if report.read_text().startswith(f'# Benchmark: {model_id}\n')]
    assert report.read_text().startswith(f'# Benchmark: {model_id}\n')


def test_main_preserves_reports_on_rerun(benchmark_workspace: Path):
    """
    Repeating a configuration creates another report without replacing earlier results.
    """
    run_benchmark.main(['--seed', str(SEED)])
    output_dir = benchmark_workspace / 'benchmarks' / '20261006_demo'
    original_reports = {report: report.read_text() for report in output_dir.joinpath('reports').glob('*.md')}
    assert len(original_reports) == 2

    run_benchmark.main(['--seed', str(SEED)])

    assert len(list(output_dir.joinpath('reports').glob('*.md'))) == 4
    assert all(report.read_text() == text for report, text in original_reports.items())
    assert len(read_summary(output_dir)) == 4


def test_summary_plot_groups_model_configurations():
    """
    Repeated runs share a color, with one legend entry per configuration.
    """
    model = 'Qwen/Qwen3.5-4B'
    rows = [
        {'model': model, 'total_seconds': '10', 'average_score': '0.5'},
        {'model': model + ' (no tools)', 'total_seconds': '20', 'average_score': '0.3'},
        {'model': model, 'total_seconds': '12', 'average_score': '0.6'},
    ]

    figure = run_benchmark.build_summary_plot(rows)

    legend = figure.axes[0].get_legend()
    assert legend is not None
    assert legend.get_title().get_text() == 'Model configuration'
    assert [label.get_text() for label in legend.get_texts()] == [
        model, model + ' (no tools)',
    ]
    full, no_tools = figure.axes[0].collections
    assert not np.array_equal(full.get_facecolor(), no_tools.get_facecolor())
    assert not figure.axes[0].texts


def test_get_git_commit_uses_source_checkout(monkeypatch: pytest.MonkeyPatch):
    """
    Read the source commit independently of the benchmark output directory.
    """
    commit = 'b' * 40

    check_output = Mock(return_value=commit + '\n')
    monkeypatch.setattr(run_benchmark.subprocess, 'check_output', check_output)
    assert run_benchmark.get_git_commit() == commit
    check_output.assert_called_once_with(
        ['git', 'rev-parse', 'HEAD'],
        cwd=Path(run_benchmark.__file__).resolve().parent,
        text=True,
        stderr=subprocess.DEVNULL,
    )


@pytest.mark.parametrize('error', [FileNotFoundError(), subprocess.CalledProcessError(128, 'git')])
def test_get_git_commit_unavailable(monkeypatch: pytest.MonkeyPatch, error: Exception):
    """
    Allow benchmarks to run when Git or checkout metadata is unavailable.
    """
    monkeypatch.setattr(run_benchmark.subprocess, 'check_output', Mock(side_effect=error))
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
        response={'answer': '<answer>3,000</answer>', 'tool_results': [{
            'call': {'name': 'search_disaster_events', 'arguments': {'text': 'Sri Lanka'}},
            'result': 'private trace',
        }]},
    )
    assert output.response is not None
    assert run_benchmark.score_answer(FIRST_INPUT, output.response['answer']) == 1.0
    markdown = output.to_md(1)
    assert FIRST_INPUT.research_question in markdown
    assert '**Answer:** <answer>3,000</answer>' in markdown
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


def test_summary_plot_filters_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    Plot current full runs and exclude stale revisions, old modes, and partial runs.
    """
    model = FrozenModel.QWEN3_1_7B
    other_model = FrozenModel.QWEN3_5_4B
    rows = [
        (model.value, model.revision, 3, 3),
        (model.value + ' (no tools)', model.revision, 3, 3),
        (other_model.value, other_model.revision, 3, 3),
        (model.value, 'old-revision', 3, 3),
        (model.value + ' (no tools)', 'old-revision', 3, 3),
        (model.value, model.revision, 2, 2),
        (model.value, model.revision, 2, 3),
        (model.value, model.revision, 4, 4),
        (model.value + ' (core only)', model.revision, 3, 3),
        ('unknown-model', model.revision, 3, 3),
    ]
    path = tmp_path.joinpath('summary.csv')
    with path.open('w', encoding='utf-8', newline='') as file:
        writer = csv.writer(file)
        writer.writerow([
            'model', 'revision', 'questions_completed', 'total_questions',
            'total_seconds', 'average_score',
        ])
        for index, row in enumerate(rows):
            writer.writerow([*row, index + 1, 0.5])
    build_plot = Mock()
    monkeypatch.setattr(run_benchmark, 'build_summary_plot', build_plot)

    assert run_benchmark.save_summary_plot(path, 3) == path.with_suffix('.svg')

    build_plot.assert_called_once()
    plotted_rows = build_plot.call_args.args[0]
    assert [row['model'] for row in plotted_rows] == [
        model.value, model.value + ' (no tools)', other_model.value,
    ]
    assert [float(row['total_seconds']) for row in plotted_rows] == [1.0, 2.0, 3.0]
    build_plot.return_value.savefig.assert_called_once_with(path.with_suffix('.svg'))
