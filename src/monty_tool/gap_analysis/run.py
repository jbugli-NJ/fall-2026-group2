"""
Command-line runner and result summaries for gap analysis.
"""

# Imports

import argparse
from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timezone
import json
from pathlib import Path

from transformers import set_seed

from monty_tool.gap_analysis.answering import (
    MODEL_CHOICES,
    answer_question,
    construct_gap_questions,
    make_answerer,
    select_country_hazards,
)
from monty_tool.gap_analysis.external_sources import EXTERNAL_SOURCES, ExternalSourceName
from monty_tool.gap_analysis.judging import (
    RUBRIC, RUBRIC_VERSION, BedrockContributionJudge, LocalContributionJudge,
)
from monty_tool.gap_analysis.protocols import ContributionJudge
from monty_tool.gap_analysis.plotting import build_summary_plot
from monty_tool.gap_analysis.schemas import (
    Contribution,
    Evidence,
    GapQuestion,
    GapSelection,
    ModelInfo,
    ModelSummary,
    PairedResult,
    QuestionSummary,
    SavedAnalysisRun,
    SavedPairedResult,
)
from monty_tool.gap_analysis.templates import TEMPLATES
from monty_tool.gap_analysis.retrieval import DISASTER_LIMIT, retrieve_evidence
from monty_tool.gap_analysis.storage import save_attempt, save_evidence
from monty_tool.llm.client import DEFAULT_BEDROCK_MODEL_ID, BedrockQueryAssistant
from monty_tool.llm.protocols import QueryAssistantProtocol
from monty_tool.llm.tools import GraphSearchArguments
from monty_tool.utils.versions import SEED, FrozenModel


# Paired evaluation

def run_pair(
    question: GapQuestion,
    selection: GapSelection,
    assistant: QueryAssistantProtocol,
    judge: ContributionJudge,
    *,
    external_source: ExternalSourceName,
    evidence: Evidence,
    evidence_s3_uri: str,
) -> PairedResult:
    """
    Answer with and without an external source, then judge what it adds.
    """
    pair = PairedResult(
        external_source=external_source,
        question=question,
        selection=selection,
        evidence_s3_uri=evidence_s3_uri,
        core=answer_question(
            assistant=assistant,
            question=question,
            external_source=None,
            evidence=evidence,
        ),
        external=answer_question(
            assistant=assistant,
            question=question,
            external_source=external_source,
            evidence=evidence,
        ),
    )
    if pair.core.error is None and pair.external.error is None:
        try:
            pair.contribution = judge.judge(pair, evidence)
        except Exception as exc:
            pair.judge_error = str(exc)
    return pair


# Results and summaries

def summarize(results: Sequence[SavedPairedResult]) -> dict[str, QuestionSummary]:
    """
    Count categories and failures for each question template and source.
    """
    summary: dict[str, QuestionSummary] = {}
    groups: list[tuple[str, ExternalSourceName]] = sorted({
        (pair.question.template_id, pair.external_source) for pair in results
    })
    for template_id, source in groups:
        pairs = [
            pair for pair in results
            if pair.question.template_id == template_id and pair.external_source == source
        ]
        counts = Counter(
            pair.contribution.value for pair in pairs if pair.contribution is not None
        )
        evaluated = sum(counts.values())
        improved = counts[Contribution.IMPROVEMENT.value]
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
            'categories': {
                category.value: counts[category.value] for category in Contribution
            },
        }
    return summary


def load_runs(output_dir: Path) -> list[SavedAnalysisRun]:
    """
    Select the current rubric before validating saved runs.
    """
    path = output_dir.joinpath('results.json')
    if not path.exists():
        return []
    payload = json.loads(path.read_text())
    return [
        SavedAnalysisRun.model_validate(record)
        for record in payload['runs']
        if record.get('metadata', {}).get('rubric_version') == RUBRIC_VERSION
    ]


def summarize_runs(runs: Sequence[SavedAnalysisRun]) -> list[ModelSummary]:
    """
    Summarize the current rubric separately for each answer and judge model.
    """
    groups: dict[tuple[str, str], list[SavedPairedResult]] = {}
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


def save_results(
    output_dir: Path,
    runs: Sequence[SavedAnalysisRun],
) -> None:
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


# Command-line helpers

def _positive_int(value: str) -> int:
    """
    Parse a CLI argument as an integer greater than zero.
    """
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError('Value must be positive.')
    return number


def _non_negative_int(value: str) -> int:
    """
    Parse a CLI argument as an integer greater than or equal to zero.
    """
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError('Value must be non-negative.')
    return number


def build_parser() -> argparse.ArgumentParser:
    """
    Define command-line options for running gap analysis.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--answer-model', required=True, choices=MODEL_CHOICES)
    parser.add_argument('--judge-model', required=True, choices=MODEL_CHOICES)
    parser.add_argument('--country-code')
    parser.add_argument(
        '--hazard-name',
        help='Hazard name, such as Cyclone or Earthquake.',
    )
    parser.add_argument('--from-date')
    parser.add_argument('--to-date')
    parser.add_argument(
        '--limit', type=_positive_int, default=1,
        help='Maximum number of country and hazard combinations (default: 1).',
    )
    parser.add_argument(
        '--offset',
        type=_non_negative_int,
        default=0,
        help='Country–hazard groups to skip (default: 0).',
    )
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
        help='Require stored data for every selected source (default: enabled).',
    )
    parser.add_argument(
        '--output-dir',
        type=Path,
        default=Path('benchmarks/gap_analysis'),
        help='Analysis directory; defaults to benchmarks/gap_analysis/.',
    )
    return parser


def _model_metadata(model: str) -> ModelInfo:
    """
    Return the selected model's ID and revision for the run metadata.
    """
    if model == 'bedrock':
        return {
            'id': DEFAULT_BEDROCK_MODEL_ID,
            'revision': 'not applicable',
        }
    return {
        'id': model,
        'revision': FrozenModel(model).revision,
    }


# Runner

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
        country_code=args.country_code,
        from_date=args.from_date, to_date=args.to_date,
        offset=args.offset,
    )
    selections = select_country_hazards(
        filters,
        args.limit,
        hazard_name=args.hazard_name,
        require_external_data=args.require_external_data,
        external_sources=external_sources,
    )
    if not selections:
        parser.error('No countries and hazards matched the selection.')
    selection_questions = [
        (selection, construct_gap_questions(selection, TEMPLATES))
        for selection in selections
    ]
    total_questions = sum(len(questions) for _, questions in selection_questions)
    seed = (
        SEED if args.answer_model != 'bedrock' or args.judge_model != 'bedrock'
        else None
    )
    if seed is not None:
        set_seed(seed)
    assistant = make_answerer(args.answer_model)
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
        'selection': {
            **filters.model_dump(mode='json'),
            'hazard_name': args.hazard_name,
        },
        'limit': args.limit,
        'disaster_limit': DISASTER_LIMIT,
        'require_external_data': args.require_external_data,
        'selections': [selection.model_dump(mode='json') for selection in selections],
        'templates': [template.model_dump() for template in TEMPLATES],
        'rubric_version': RUBRIC_VERSION,
        'rubric': RUBRIC,
        'total_questions': total_questions,
        'external_sources': external_sources,
        'comparisons': [f'core_vs_{name}' for name in external_sources],
    }
    current_run = SavedAnalysisRun(metadata=metadata)
    runs.append(current_run)
    save_results(output_dir, runs)
    index = 0
    total_comparisons = total_questions * len(external_sources)
    for selection, questions in selection_questions:
        for external_source in external_sources:
            evidence = retrieve_evidence(
                selection.filters,
                hazard_name=selection.hazard_name,
                external_source=external_source,
            )
            evidence_s3_uri = save_evidence(evidence)
            for question in questions:
                index += 1
                print(
                    f'{index}/{total_comparisons}: '
                    f'{question.country_code} / {question.hazard_name} / '
                    f'{question.template_id} / {external_source}'
                )
                pair = run_pair(
                    question=question,
                    selection=selection,
                    assistant=assistant,
                    judge=judge,
                    external_source=external_source,
                    evidence=evidence,
                    evidence_s3_uri=evidence_s3_uri,
                )
                current_run.results.append(SavedPairedResult(
                    external_source=pair.external_source,
                    question=pair.question,
                    selection=pair.selection,
                    evidence_s3_uri=pair.evidence_s3_uri,
                    core=save_attempt(pair.core),
                    external=save_attempt(pair.external),
                    contribution=pair.contribution,
                    judge_error=pair.judge_error,
                    judge_input_tokens=pair.judge_input_tokens,
                    judge_output_tokens=pair.judge_output_tokens,
                ))
                save_results(output_dir, runs)
    print(f'Results: {output_dir}')
    return int(any(pair.contribution is None for pair in current_run.results))


if __name__ == '__main__':
    raise SystemExit(main())
