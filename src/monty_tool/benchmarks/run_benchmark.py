"""
Run a fact retrieval benchmark and save a markdown report for the model.
"""

# Imports

import argparse
from datetime import datetime, timezone
from importlib.resources import files
import logging
from pathlib import Path
import re
from time import perf_counter

from pydantic import TypeAdapter
from transformers import set_seed

from monty_tool.benchmarks.schemas import BenchmarkInput, BenchmarkOutput
from monty_tool.llm.client import QueryAssistant
from monty_tool.llm.schemas import QueryAssistantResponse
from monty_tool.utils.versions import FrozenModel, SEED


# Logging
logger = logging.getLogger(__name__)
logger.setLevel(level=logging.INFO)


# Helpers

def build_parser() -> argparse.ArgumentParser:
    """
    Define model selection and optional benchmark size controls.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--model', type=FrozenModel, choices=list(FrozenModel),
        default=FrozenModel.QWEN3_1_7B,
        help='Model ID to evaluate (in the supported set)',
    )
    parser.add_argument(
        '--limit', type=int,
        help='Run only the first N questions',
    )
    return parser


def score_answer(benchmark: BenchmarkInput, answer: str) -> float:
    """
    Award full, partial, or zero credit using stored answers.
    """
    answer = answer.lower()
    if any(value.lower() in answer for value in benchmark.full_answer_substrings):
        return 1.0
    if any(value.lower() in answer for value in benchmark.partial_answer_substrings):
        return 0.5
    return 0.0


def run_question(
    assistant: QueryAssistant,
    benchmark: BenchmarkInput,
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
        response = assistant.ask(benchmark.research_question)
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
    model: FrozenModel,
    outputs: list[BenchmarkOutput],
    started_at: datetime,
    total_questions: int,
    ) -> str:
    """
    Build a markdown report with summary stats and all question outputs.
    """
    average = sum(output.score for output in outputs) / len(outputs) if outputs else 0.0
    sections = [
        f'# Benchmark: {model.value}',
        f'Model revision: {model.revision}\n\nStart time (UTC): {started_at.isoformat()}',
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


def main(argv: list[str] | None = None) -> None:
    """
    Run the benchmark and save a model-specific report.
    """
    args = build_parser().parse_args(argv)
    benchmarks = TypeAdapter(list[BenchmarkInput]).validate_json(
        files('monty_tool.benchmarks').joinpath('benchmark_inputs.json').read_text(
            encoding='utf-8',
        ),
    )
    if args.limit is not None:
        benchmarks = benchmarks[:args.limit]

    set_seed(SEED)
    assistant = QueryAssistant(model_id=args.model)
    model_slug = re.sub(r'[^a-z0-9]+', '_', args.model.value.lower()).strip('_')
    output_path = Path('benchmarks').joinpath(f'{model_slug}.md')
    output_path.parent.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(timezone.utc)
    outputs = []
    for number, benchmark in enumerate(benchmarks, start=1):
        logger.info(f'Question {number}/{len(benchmarks)}: {benchmark.research_question}')
        output = run_question(assistant, benchmark)
        outputs.append(output)
        output_path.write_text(render_report(
            model=args.model,
            outputs=outputs,
            started_at=started_at,
            total_questions=len(benchmarks),
        ), encoding='utf-8')
        logger.info(f'Question {number} qcore: {output.score:.2%}')
    logger.info(f'Saved benchmark report: {output_path}')


if __name__ == '__main__':
    main()
