"""
Compare fact retrieval with and without tools, saving reports, a CSV, and a plot.
"""

# Imports

import argparse
import csv
from datetime import datetime, timezone
from hashlib import sha256
from importlib.resources import files
import logging
from pathlib import Path
import re
import subprocess
from time import perf_counter
from typing import Literal

from matplotlib.figure import Figure
from matplotlib.ticker import PercentFormatter
from pydantic import TypeAdapter
from transformers import set_seed

from monty_tool.benchmarks.schemas import BenchmarkInput, BenchmarkOutput
from monty_tool.llm.client import DEFAULT_BEDROCK_MODEL_ID, BedrockQueryAssistant, QueryAssistant
from monty_tool.llm.schemas import QueryAssistantResponse
from monty_tool.utils.versions import BENCHMARK_SEEDS, FrozenModel, SEED


# Logging

logger = logging.getLogger(__name__)
logger.setLevel(level=logging.INFO)


# Benchmark inputs

_INPUT_MODULE = files('monty_tool.benchmarks.inputs')
type BenchmarkModel = FrozenModel | Literal['bedrock']


# Helpers

def model_metadata(model: BenchmarkModel) -> tuple[str, str]:
    """
    Identify the evaluated model and its revision when available.
    """
    if isinstance(model, FrozenModel):
        return model.value, model.revision
    return DEFAULT_BEDROCK_MODEL_ID, 'not applicable'


def get_git_commit() -> str:
    """
    Identify the source checkout's commit, or report unavailable Git metadata.
    """
    try:
        return subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'],
            cwd=Path(__file__).resolve().parent,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        logger.warning('Git commit unavailable; recording unknown.')
        return 'unknown'


def build_parser() -> argparse.ArgumentParser:
    """
    Define model and seed selection and optional benchmark size controls.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--model', type=str, choices=[*FrozenModel, 'bedrock'],
        default=FrozenModel.QWEN3_5_4B,
        help='Model ID to evaluate (in the supported set)',
    )
    parser.add_argument(
        '--seed', type=int, choices=BENCHMARK_SEEDS,
        help=(
            'Run a seed available in the whitelisted set. '
            'Defaults to running all benchmark seeds. '
        ),
    )
    parser.add_argument(
        '--input-json', type=str,
        default='20261006_demo.json',
        help='JSON file with benchmark inputs',
    )
    parser.add_argument(
        '--limit', type=int,
        help='Run only the first N questions',
    )
    return parser


def score_answer(benchmark: BenchmarkInput, answer: str) -> float:
    """
    Score the last complete answer tag, ignoring commas and case.
    """
    matches = re.findall(r'<answer>.*?</answer>', answer, flags=re.IGNORECASE | re.DOTALL)
    if not matches:
        return 0.0
    answer = matches[-1].lower().replace(',', '')
    if any(
        re.search(rf"\b{re.escape(value.lower().replace(',', ''))}\b", answer)
        for value in benchmark.full_answer_substrings
    ):
        return 1.0
    if any(
        re.search(rf"\b{re.escape(value.lower().replace(',', ''))}\b", answer)
        for value in benchmark.partial_answer_substrings
    ):
        return 0.5
    return 0.0


def run_question(
    assistant: QueryAssistant | BedrockQueryAssistant,
    benchmark: BenchmarkInput,
    *,
    use_tools: bool = True,
    ) -> BenchmarkOutput:
    """
    Ask one question and return the LLM response, score, and potential failure.
    """
    started = perf_counter()
    response: QueryAssistantResponse | None = None
    answer = ''
    error = None
    score = 0.0
    try:
        response = assistant.ask(benchmark.research_question, use_tools=use_tools)
        answer = response['answer']
        score = score_answer(benchmark, answer)
    except Exception as e:
        logger.exception('Query failed unexpectedly!')
        error = str(e)
    return BenchmarkOutput(
        benchmark_input=benchmark,
        score=score,
        duration_seconds=perf_counter() - started,
        response=response,
        error=error,
    )


def render_report(
    model: BenchmarkModel,
    outputs: list[BenchmarkOutput],
    started_at: datetime,
    total_questions: int,
    *,
    input_sha256: str,
    git_commit: str,
    use_tools: bool = True,
    seed: int = SEED,
    ) -> str:
    """
    Build a markdown report with summary stats and all question outputs.
    """
    average = sum(output.score for output in outputs) / len(outputs) if outputs else 0.0
    model_id, revision = model_metadata(model)
    sections = [
        f'# Benchmark: {model_id}' + ('' if use_tools else ' (no tools)'),
        f'Model revision: {revision}\n\nStart time (UTC): {started_at.isoformat()}',
        f'Random seed: {seed}',
        f'Input SHA-256: {input_sha256}',
        f'Git commit: {git_commit}',
        '## Summary',
        f'- Questions completed: {len(outputs)} / {total_questions}\n'
        f'- Average score: {average:.2%}\n'
        f'- Full-credit questions: {sum(output.score == 1.0 for output in outputs)}\n'
        f'- Partial-credit questions: {sum(output.score == 0.5 for output in outputs)}\n'
        f'- Zero-credit questions: {sum(output.score == 0.0 for output in outputs)}\n'
        f'- Failed invocations: {sum(output.error is not None for output in outputs)}\n'
        f'- Total time: {sum(output.duration_seconds for output in outputs):.2f}s',
        '\n',
    ]
    sections.extend(output.to_md(number) for number, output in enumerate(outputs, start=1))
    return '\n\n'.join(sections) + '\n'


def append_summary(
    path: Path,
    model: BenchmarkModel,
    outputs: list[BenchmarkOutput],
    started_at: datetime,
    total_questions: int,
    *,
    input_sha256: str,
    git_commit: str,
    use_tools: bool = True,
    seed: int = SEED,
    ):
    """
    Append one run's aggregate statistics to a CSV covering all runs.
    """
    model_id, revision = model_metadata(model)
    row = {
        'model': model_id + ('' if use_tools else ' (no tools)'),
        'revision': revision,
        'seed': seed,
        'input_sha256': input_sha256,
        'git_commit': git_commit,
        'started_at_utc': started_at.isoformat(),
        'questions_completed': len(outputs),
        'total_questions': total_questions,
        'average_score': sum(output.score for output in outputs) / len(outputs) if outputs else 0.0,
        'full_credit': sum(output.score == 1.0 for output in outputs),
        'partial_credit': sum(output.score == 0.5 for output in outputs),
        'zero_credit': sum(output.score == 0.0 for output in outputs),
        'failures': sum(output.error is not None for output in outputs),
        'total_seconds': sum(output.duration_seconds for output in outputs),
    }
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open('a', encoding='utf-8', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=list(row))
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def random_chance_score(benchmarks: list[BenchmarkInput]) -> float:
    """
    Average uniform guessing credit, assigning zero to questions without choices.
    """
    if not benchmarks:
        return 0.0
    return sum(
        1 / benchmark.choice_count if benchmark.choice_count is not None else 0.0
        for benchmark in benchmarks
    ) / len(benchmarks)


def build_summary_plot(
    rows: list[dict[str, str]],
    *,
    chance_score: float = 0.0,
    ) -> Figure:
    """
    Build a scatterplot displaying score svs. runtime colored by model.
    """
    figure = Figure(figsize=(11, 6), layout='constrained')
    axes = figure.subplots()
    for model in dict.fromkeys(row['model'] for row in rows):
        runs = [row for row in rows if row['model'] == model]
        axes.scatter(
            [float(row['total_seconds']) for row in runs],
            [float(row['average_score']) for row in runs],
            label=model,
        )
    if chance_score > 0:
        axes.axhline(
            chance_score, color='gray', linestyle='--',
            label=f'Random chance ({chance_score:.2%})',
        )
    if rows or chance_score > 0:
        axes.legend(title='Model configuration', loc='upper left', bbox_to_anchor=(1.02, 1))
    axes.set_title('Fact retrieval benchmark')
    axes.set_xlabel('Total runtime (seconds)')
    axes.set_ylabel('Average score')
    axes.yaxis.set_major_formatter(PercentFormatter(xmax=1))
    axes.set_ylim(-0.05, 1.1)
    axes.margins(x=0.3)
    axes.grid(alpha=0.25)
    return figure


def save_summary_plot(
    summary_path: Path,
    benchmarks: list[BenchmarkInput],
    *,
    input_sha256: str,
    ) -> Path:
    """
    Read the comparison CSV and save its figure alongside it.
    """
    total_questions = len(benchmarks)
    current_revisions = {
        model_id + suffix: revision
        for model_id, revision in [
            *(model_metadata(model) for model in FrozenModel),
            model_metadata('bedrock'),
        ]
        for suffix in ('', ' (no tools)')
    }
    with summary_path.open(encoding='utf-8', newline='') as file:
        rows = [
            row for row in csv.DictReader(file)
            if row['input_sha256'] == input_sha256
            and row['revision'] == current_revisions.get(row['model'])
            and int(row['total_questions']) == total_questions
            and int(row['questions_completed']) == total_questions
        ]
    figure = build_summary_plot(rows, chance_score=random_chance_score(benchmarks))
    plot_path = summary_path.with_suffix('.svg')
    figure.savefig(plot_path)
    return plot_path


def run_seed(
    benchmarks: list[BenchmarkInput],
    model: BenchmarkModel,
    output_dir: Path,
    *,
    assistant: QueryAssistant | BedrockQueryAssistant,
    input_sha256: str,
    git_commit: str,
    seed: int,
    use_tools: bool = True,
    ) -> Path:
    """
    Evaluate one seed, updating its report and appending its summary row.
    """
    logger.info(f'Running benchmark with seed {seed}')
    set_seed(seed)
    if isinstance(model, FrozenModel):
        model_slug = re.sub(r'[^a-z0-9]+', '_', model.value.lower()).strip('_')
    else:
        model_slug = 'bedrock'
    if not use_tools:
        model_slug += '_no_tools'
    reports_dir = output_dir.joinpath('reports')
    reports_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(timezone.utc)
    timestamp = started_at.strftime('%Y%m%dT%H%M%S.%fZ')
    output_path = reports_dir.joinpath(f'{model_slug}_{timestamp}.md')
    outputs: list[BenchmarkOutput] = []

    def write_report() -> None:
        """
        Save progress using the same run metadata on every update.
        """
        output_path.write_text(render_report(
            model=model,
            outputs=outputs,
            started_at=started_at,
            total_questions=len(benchmarks),
            input_sha256=input_sha256,
            git_commit=git_commit,
            use_tools=use_tools,
            seed=seed,
        ), encoding='utf-8')

    write_report()
    for number, benchmark in enumerate(benchmarks, start=1):
        logger.info(f'Question {number}/{len(benchmarks)}: {benchmark.research_question}')
        output = run_question(assistant, benchmark, use_tools=use_tools)
        outputs.append(output)
        write_report()
        logger.info(f'Question {number} score: {output.score:.2%}')
    summary_path = output_dir.joinpath('summary.csv')
    append_summary(
        summary_path, model, outputs, started_at, len(benchmarks),
        input_sha256=input_sha256,
        git_commit=git_commit,
        use_tools=use_tools,
        seed=seed,
    )
    logger.info(f'Saved benchmark report: {output_path}')
    logger.info(f'Appended benchmark summary: {summary_path}')
    return output_path


def main(argv: list[str] | None = None) -> None:
    """
    Load benchmark inputs, evaluate selected seeds, and save the comparison plot.
    """
    args = build_parser().parse_args(argv)
    model: BenchmarkModel = 'bedrock' if args.model == 'bedrock' else FrozenModel(args.model)
    input_file = _INPUT_MODULE.joinpath(args.input_json)
    if not input_file.is_file():
        raise ValueError(f"Selected input not detected: {input_file.name}")
    logger.info(f"Detected input file: {input_file.name}")
    input_bytes = input_file.read_bytes()
    input_sha256 = sha256(input_bytes).hexdigest()
    all_benchmarks = TypeAdapter(list[BenchmarkInput]).validate_json(input_bytes)
    benchmarks = all_benchmarks if args.limit is None else all_benchmarks[:args.limit]
    git_commit = get_git_commit()
    output_dir = Path('benchmarks').joinpath(Path(input_file.name).stem)
    seeds = BENCHMARK_SEEDS if args.seed is None else (args.seed,)
    set_seed(seeds[0])
    assistant = (
        QueryAssistant(model_id=model) if isinstance(model, FrozenModel)
        else BedrockQueryAssistant()
    )
    for seed in seeds:
        for use_tools in (True, False):
            run_seed(
                benchmarks, model, output_dir,
                assistant=assistant,
                input_sha256=input_sha256,
                git_commit=git_commit,
                use_tools=use_tools,
                seed=seed,
            )
    plot_path = save_summary_plot(
        output_dir.joinpath('summary.csv'), all_benchmarks, input_sha256=input_sha256,
    )
    logger.info(f'Saved benchmark plot: {plot_path}')


if __name__ == '__main__':
    main()
