"""
Command-line runner and result summaries for gap analysis.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from monty_tool.gap_analysis.answering import (
    MODEL_CHOICES,
    answer_question,
    construct_gap_questions,
    make_answerer,
    select_contexts,
)
from monty_tool.gap_analysis.judging import (
    RUBRIC, RUBRIC_VERSION, BedrockContributionJudge, LocalContributionJudge,
)
from monty_tool.gap_analysis.protocols import ContributionJudge
from monty_tool.gap_analysis.plotting import build_summary_plot
from monty_tool.gap_analysis.schemas import (
    AnalysisRun,
    Contribution,
    GapQuestion,
    PairedResult,
)
from monty_tool.gap_analysis.templates import TEMPLATES
from monty_tool.llm.client import DEFAULT_BEDROCK_MODEL_ID, BedrockQueryAssistant
from monty_tool.llm.protocols import QueryAssistantProtocol
from monty_tool.llm.tools import GraphSearchArguments
from monty_tool.utils.versions import FrozenModel


def run_pair(
    question: GapQuestion,
    core_context: dict[str, Any],
    assistant: QueryAssistantProtocol,
    judge: ContributionJudge,
) -> PairedResult:
    """
    Answer a question with and without external data, then judge the external
    contribution.
    """
    pair = PairedResult(
        question=question,
        core_context=core_context,
        core=answer_question(assistant, question, enable_external=False),
        external=answer_question(assistant, question, enable_external=True),
    )
    if pair.core.error is None and pair.external.error is None:
        try:
            pair.contribution = judge.judge(pair)
        except Exception as exc:
            pair.judge_error = str(exc)
    return pair


def summarize(results: list[PairedResult]) -> dict[str, Any]:
    """
    Count categories and answer, tool, and judge failures for each question template.
    """
    summary = {}
    for template_id in sorted({pair.question.template_id for pair in results}):
        pairs = [pair for pair in results if pair.question.template_id == template_id]
        counts = Counter(
            pair.contribution.value for pair in pairs if pair.contribution is not None
        )
        summary[template_id] = {
            'attempted': len(pairs),
            'evaluated': sum(counts.values()),
            'answer_failures': sum(
                pair.core.error is not None or pair.external.error is not None
                for pair in pairs
            ),
            'judge_failures': sum(pair.judge_error is not None for pair in pairs),
            'tool_failures': sum(
                pair.core.tool_failures + pair.external.tool_failures for pair in pairs
            ),
            'pairs_with_tool_failures': sum(
                bool(pair.core.tool_failures or pair.external.tool_failures)
                for pair in pairs
            ),
            'categories': {
                category.value: counts[category.value] for category in Contribution
            },
        }
    return summary


def load_runs(output_dir: Path) -> list[AnalysisRun]:
    """
    Read the invocations already saved in the analysis results file.
    """
    path = output_dir.joinpath('results.json')
    if not path.exists():
        return []
    payload = json.loads(path.read_text())
    records = payload['runs'] if 'runs' in payload else [payload]
    return [AnalysisRun.model_validate(record) for record in records]


def save_results(output_dir: Path, runs: list[AnalysisRun]) -> None:
    """
    Save all invocations, combined counts, and the summary SVG for an analysis.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = summarize([pair for batch in runs for pair in batch.results])
    payload = {
        'runs': [batch.model_dump(mode='json') for batch in runs],
        'summary': summary,
    }
    pending_path = output_dir.joinpath('results.json.tmp')
    pending_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + '\n'
    )
    pending_path.replace(output_dir.joinpath('results.json'))
    build_summary_plot(summary).savefig(output_dir.joinpath('summary.svg'))


def _positive_int(value: str) -> int:
    """
    Parse a CLI argument as an integer greater than zero.
    """
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError('Value must be positive.')
    return number


def build_parser() -> argparse.ArgumentParser:
    """
    Define command-line options for running gap analysis.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--answer-model', required=True, choices=MODEL_CHOICES)
    parser.add_argument('--judge-model', required=True, choices=MODEL_CHOICES)
    parser.add_argument('--country-code')
    parser.add_argument('--from-date')
    parser.add_argument('--to-date')
    parser.add_argument('--event-limit', type=_positive_int, default=1)
    parser.add_argument('--max-tool-calls', type=_positive_int, default=10)
    parser.add_argument(
        '--output-dir',
        type=Path,
        default=Path('benchmarks/gap_analysis'),
        help='Analysis directory; defaults to benchmarks/gap_analysis/.',
    )
    return parser


def _model_metadata(model: str) -> dict[str, str]:
    """
    Return the selected model's ID and revision for the run metadata.
    """
    if model == 'bedrock':
        return {'id': DEFAULT_BEDROCK_MODEL_ID, 'revision': 'not applicable'}
    return {'id': model, 'revision': FrozenModel(model).revision}


def main(argv: list[str] | None = None) -> int:
    """
    Run gap analysis using the command-line options and save its results.
    Return 1 if any pair fails; otherwise return 0.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    started = datetime.now(timezone.utc)
    output_dir = args.output_dir
    runs = load_runs(output_dir)
    filters = GraphSearchArguments(
        country_code=args.country_code, from_date=args.from_date, to_date=args.to_date,
    )
    contexts = select_contexts(filters, args.event_limit)
    if not contexts:
        parser.error('No disaster events matched the selection.')
    questions = [
        (question, context)
        for context in contexts
        for question in construct_gap_questions(context, TEMPLATES)
    ]
    assistant = make_answerer(args.answer_model)
    assistant.max_tool_calls = args.max_tool_calls
    judge_assistant = (
        assistant
        if args.judge_model == args.answer_model
        else make_answerer(args.judge_model)
    )
    judge = (
        BedrockContributionJudge(judge_assistant)
        if isinstance(judge_assistant, BedrockQueryAssistant)
        else LocalContributionJudge(judge_assistant)
    )
    metadata = {
        'started_at': started.isoformat(),
        'answer_model': _model_metadata(args.answer_model),
        'judge_model': _model_metadata(args.judge_model),
        'judge_method': (
            'structured_output'
            if args.judge_model == 'bedrock'
            else 'token_probabilities'
        ),
        'max_tool_calls': args.max_tool_calls,
        'selection': filters.model_dump(mode='json'),
        'event_limit': args.event_limit,
        'event_ids': [context['event_id'] for context in contexts],
        'templates': [template.model_dump() for template in TEMPLATES],
        'rubric_version': RUBRIC_VERSION,
        'rubric': RUBRIC,
        'total_questions': len(questions),
        'external_sources': ['stored_news', 'stored_weather'],
        'retrieval_policy': (
            'require_successful_query_per_answer; report_recovered_tool_failures'
        ),
    }
    current_run = AnalysisRun(metadata=metadata)
    runs.append(current_run)
    save_results(output_dir, runs)
    for question, context in questions:
        print(
            f'{len(current_run.results) + 1}/{len(questions)}: '
            f'{question.event_id} / {question.template_id}'
        )
        current_run.results.append(run_pair(question, context, assistant, judge))
        save_results(output_dir, runs)
    print(f'Results: {output_dir}')
    return int(any(pair.contribution is None for pair in current_run.results))


if __name__ == '__main__':
    raise SystemExit(main())
