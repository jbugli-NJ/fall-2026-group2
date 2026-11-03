"""
Command-line runner and result summaries for gap analysis.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from huggingface_hub import accept_access_request
from transformers import set_seed

from monty_tool.gap_analysis.answering import (
    MODEL_CHOICES,
    answer_question,
    construct_gap_questions,
    make_answerer,
    select_contexts,
)
from monty_tool.gap_analysis.external_sources import EXTERNAL_SOURCES, ExternalSourceName
from monty_tool.gap_analysis.judging import (
    RUBRIC, RUBRIC_VERSION, BedrockContributionJudge, LocalContributionJudge,
)
from monty_tool.gap_analysis.protocols import ContributionJudge
from monty_tool.gap_analysis.plotting import build_summary_plot
from monty_tool.gap_analysis.schemas import (
    AnalysisRun,
    AnswerAttempt,
    Contribution,
    GapQuestion,
    PairedResult,
)
from monty_tool.gap_analysis.templates import TEMPLATES
from monty_tool.llm.client import DEFAULT_BEDROCK_MODEL_ID, BedrockQueryAssistant
from monty_tool.llm.protocols import QueryAssistantProtocol
from monty_tool.llm.tools import GraphSearchArguments
from monty_tool.utils.versions import SEED, FrozenModel


def run_pair(
    question: GapQuestion,
    core_context: dict[str, Any],
    assistant: QueryAssistantProtocol,
    judge: ContributionJudge,
    *,
    external_source: ExternalSourceName,
    core: AnswerAttempt | None = None,
) -> PairedResult:
    """
    Answer with and without an external source, then judge what it adds.
    """
    pair = PairedResult(
        external_source=external_source,
        question=question,
        core_context=core_context,
        core=core if core is not None else answer_question(
            assistant=assistant,
            question=question,
            external_source=None,
        ),
        external=answer_question(
            assistant=assistant,
            question=question,
            external_source=external_source,
        ),
    )
    if pair.core.error is None and pair.external.error is None:
        try:
            pair.contribution = judge.judge(pair)
        except Exception as exc:
            pair.judge_error = str(exc)
    return pair


def summarize(results: list[PairedResult]) -> dict[str, Any]:
    """
    Count categories and failures for each question template and source.
    """
    summary = {}
    groups = sorted({(pair.question.template_id, pair.external_source) for pair in results})
    for template_id, source in groups:
        pairs = [
            pair for pair in results
            if pair.question.template_id == template_id and pair.external_source == source
        ]
        counts = Counter(
            pair.contribution.value for pair in pairs if pair.contribution is not None
        )
        evaluated = sum(counts.values())
        improved = sum(counts[category.value] for category in (
            Contribution.FILLS_FIELD, Contribution.EXTENDS_COVERAGE, Contribution.QUALIFIES,
        ))
        summary[f'{template_id} / {source}'] = {
            'template_id': template_id,
            'external_source': source,
            'attempted': len(pairs),
            'evaluated': evaluated,
            'improved': improved,
            'improvement_rate': improved / evaluated if evaluated else None,
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


def summarize_runs(runs: list[AnalysisRun]) -> list[dict[str, Any]]:
    """
    Summarize the current rubric separately for each answer and judge model.
    """
    groups: dict[tuple[str, str], list[PairedResult]] = {}
    for batch in runs:
        if batch.metadata.get('rubric_version') != RUBRIC_VERSION:
            continue
        answer = batch.metadata.get('answer_model', {})
        judge = batch.metadata.get('judge_model', {})
        models = (
            answer.get('id', 'unknown'), judge.get('id', 'unknown'),
        )
        groups.setdefault(models, []).extend(batch.results)
    return [
        {
            'answer_model': {'id': answer_id},
            'judge_model': {'id': judge_id},
            'results': list(summarize(results).values()),
        }
        for (answer_id, judge_id), results in sorted(groups.items())
    ]


def save_results(output_dir: Path, runs: list[AnalysisRun]) -> None:
    """
    Save all runs and summarize those using the current rubric version.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = summarize_runs(runs)
    payload = {
        'runs': [batch.model_dump(mode='json') for batch in runs],
        'summary': summary,
        'summary_rubric_version': RUBRIC_VERSION,
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
    parser.add_argument(
        '--external-sources',
        nargs='+',
        choices=tuple(EXTERNAL_SOURCES),
        default=list(EXTERNAL_SOURCES),
        help='Sources to compare separately against Montandon (default: all).',
    )
    parser.add_argument(
        '--require-external-data',
        action=argparse.BooleanOptionalAction,
        default=True,
        help='Filters events to require stored external data (default: enabled).',
    )
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
    external_sources = list(dict.fromkeys(args.external_sources))
    started = datetime.now(timezone.utc)
    output_dir = args.output_dir
    runs = load_runs(output_dir)
    filters = GraphSearchArguments(
        country_code=args.country_code, from_date=args.from_date, to_date=args.to_date,
    )
    contexts = select_contexts(
        filters,
        args.event_limit,
        require_external_data=args.require_external_data,
        external_sources=external_sources,
    )
    if not contexts:
        parser.error('No disaster events matched the selection.')
    questions = [
        (question, context)
        for context in contexts
        for question in construct_gap_questions(context, TEMPLATES)
    ]
    seed = SEED if args.answer_model != 'bedrock' or args.judge_model != 'bedrock' else None
    if seed is not None:
        set_seed(seed)
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
        'seed': seed,
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
        'require_external_data': args.require_external_data,
        'event_ids': [context['event_id'] for context in contexts],
        'templates': [template.model_dump() for template in TEMPLATES],
        'rubric_version': RUBRIC_VERSION,
        'rubric': RUBRIC,
        'total_questions': len(questions),
        'external_sources': external_sources,
        'comparisons': [f'core_vs_{name}' for name in external_sources],
        'retrieval_policy': (
            'require_successful_query_per_answer; report_recovered_tool_failures'
        ),
    }
    current_run = AnalysisRun(metadata=metadata)
    runs.append(current_run)
    save_results(output_dir, runs)
    for index, (question, context) in enumerate(questions, 1):
        print(
            f'{index}/{len(questions)}: '
            f'{question.event_id} / {question.template_id}'
        )
        core = answer_question(
            assistant,
            question,
            external_source=None,
        )
        for external_source in external_sources:
            current_run.results.append(run_pair(
                question=question,
                core_context=context,
                assistant=assistant,
                judge=judge,
                external_source=external_source,
                core=core,
            ))
            save_results(output_dir, runs)
    print(f'Results: {output_dir}')
    return int(any(pair.contribution is None for pair in current_run.results))


if __name__ == '__main__':
    raise SystemExit(main())
