"""
Script to hand-label gap analysis answer pairs for comparison with LLM judging.
"""

# Imports

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Literal

from botocore.exceptions import BotoCoreError, ClientError
from pydantic import BaseModel, Field, TypeAdapter

from monty_tool.gap_analysis.external_sources import EXTERNAL_SOURCES
from monty_tool.gap_analysis.judging import LABELS, RUBRIC, RUBRIC_VERSION
from monty_tool.gap_analysis.plotting import build_judgment_plot
from monty_tool.gap_analysis.schemas import (
    Contribution, Evidence, SavedAnalysisRun, SavedPairedResult,
)
from monty_tool.gap_analysis.retrieval import evidence_for_source
from monty_tool.gap_analysis.storage import load_answer, load_evidence


# Schemas

class HumanJudgment(BaseModel):
    """
    One classification and the user's email for reference.
    """
    pair_id: str = Field(min_length=1)
    contribution: Contribution
    email: str = Field(min_length=1)


def load_runs(path: Path) -> list[SavedAnalysisRun]:
    """
    Read analysis runs from the results JSON.
    """
    payload = json.loads(path.read_text())
    return TypeAdapter(list[SavedAnalysisRun]).validate_python([
        run for run in payload['runs']
        if run.get('metadata', {}).get('rubric_version') == RUBRIC_VERSION
    ])


def load_judgments(path: Path) -> list[HumanJudgment]:
    """
    Read saved human labels, or start with none if the file does not exist.
    """
    return (
        TypeAdapter(list[HumanJudgment]).validate_python([
            record for record in json.loads(path.read_text())
            if record.get('contribution') in set(Contribution)
        ])
        if path.exists()
        else []
    )


def pair_id(run: SavedAnalysisRun, pair: SavedPairedResult) -> str:
    """
    Generate an answer pair ID for the human label to use.
    """
    content = {
        'started_at': run.metadata['started_at'],
        'question': pair.question.model_dump(mode='json'),
        'external_source': pair.external_source,
        'selection': pair.selection.model_dump(mode='json'),
        'evidence_s3_uri': pair.evidence_s3_uri,
        'core_uri': pair.core.answer_s3_uri,
        'external_uri': pair.external.answer_s3_uri,
    }
    return hashlib.sha256(
        json.dumps(content, sort_keys=True, ensure_ascii=False).encode('utf-8')
    ).hexdigest()


def index_pairs(runs: list[SavedAnalysisRun]) -> dict[str, SavedPairedResult]:
    """
    Give each answer pair a stable ID for matching it to saved human labels.
    """
    return {
        pair_id(run=run, pair=pair): pair
        for run in runs for pair in run.results
    }


def remove_stale_judgments(
    judgments: list[HumanJudgment],
    pairs: dict[str, SavedPairedResult],
) -> list[HumanJudgment]:
    """
    Drop human labels for deleted or replaced answer pairs, rejecting duplicates.
    """
    if len({record.pair_id for record in judgments}) != len(judgments):
        raise ValueError('Duplicate pair IDs in human judgments.')
    return [record for record in judgments if record.pair_id in pairs]


def pairs_for_comparison(runs: list[SavedAnalysisRun]) -> dict[str, SavedPairedResult]:
    """
    Select pairs with an LLM label from the current rubric to compare with humans.
    """
    current_runs = [
        run for run in runs
        if run.metadata.get('rubric_version') == RUBRIC_VERSION
    ]
    return {
        identity: pair for identity, pair in index_pairs(runs=current_runs).items()
        if pair.contribution is not None and pair.judge_error is None
    }


def filter_unlabeled_pairs(
    pairs: dict[str, SavedPairedResult],
    judgments: list[HumanJudgment],
) -> dict[str, SavedPairedResult]:
    """
    Skip pairs a human has already labeled when resuming a labeling session.
    """
    labeled = {record.pair_id for record in judgments}
    return {identity: pair for identity, pair in pairs.items() if identity not in labeled}


def format_section(title: str, body: str) -> str:
    """
    Mark the start and end of a section in the terminal scrollback.
    """
    border = '=' * 72
    return f'{border}\n{title}\n{border}\n\n{body}\n\n--- END {title} ---'


def format_pair(
    pair: SavedPairedResult,
    core: str,
    external: str,
    evidence: Evidence,
) -> str:
    """
    Show archived records, both answers, and a labeling code recap.
    """
    context = (
        f'{pair.question.country_code} / {pair.question.hazard_name} / '
        f'{pair.question.template_id} / {pair.external_source}'
    )
    sections = [
        format_section(
            title=f'{context} | QUESTION',
            body=pair.question.question + '\n\n'
            + EXTERNAL_SOURCES[pair.external_source].evidence_description,
        ),
        format_section(
            title=f'{context} | SELECTION',
            body=json.dumps(
                pair.selection.model_dump(mode='json'),
                ensure_ascii=False,
                separators=(',', ':'),
            ),
        ),
    ]
    for heading, records in [
        ('CORE ONLY', evidence_for_source(evidence, None)),
        ('WITH EXTERNAL SOURCE', evidence_for_source(evidence, pair.external_source)),
    ]:
        sections.append(format_section(
            title=f'{context} | {heading} | RECORDS',
            body=json.dumps(records, ensure_ascii=False),
        ))
    for heading, answer in [('CORE ONLY', core), ('WITH EXTERNAL SOURCE', external)]:
        sections.append(format_section(
            title=f'{context} | {heading} | ANSWER', body=answer,
        ))
    sections.append(f'END PAIR: {context} — ENTER YOUR LABEL')
    sections.append('Codes: ' + '; '.join(
        f'{code}: {category.value.replace("_", " ")}' for code, category in LABELS.items()
    ))
    return '\n\n'.join(sections)


def read_choice() -> str:
    """
    Prompt until the user enters a category, skip, or quit.
    """
    while True:
        choice = input('Label [A–C / s / q]: ').strip().upper()
        if choice in LABELS or choice in ('S', 'Q'):
            return choice
        print('Enter A–C, s, or q.')


def review_pair(pair: SavedPairedResult) -> Contribution | Literal['skip', 'quit']:
    """
    Show one pair's answers and evidence, then ask the user to label it.
    """
    try:
        core = load_answer(uri=pair.core.answer_s3_uri)
        external = load_answer(uri=pair.external.answer_s3_uri)
        evidence = load_evidence(pair.evidence_s3_uri)
    except (OSError, ValueError, BotoCoreError, ClientError) as exc:
        print(f'Skipping pair: {exc}')
        return 'skip'
    print(format_pair(pair=pair, core=core, external=external, evidence=evidence))
    choice = read_choice()
    if choice == 'S':
        return 'skip'
    if choice == 'Q':
        return 'quit'
    return LABELS[choice]


def save_judgments(path: Path, judgments: list[HumanJudgment]) -> None:
    """
    Rewrite and save a file with human label judgements.
    """
    pending = path.with_suffix(path.suffix + '.tmp')
    pending.write_text(json.dumps(
        [record.model_dump(mode='json') for record in judgments],
        indent=2, ensure_ascii=False,
    ) + '\n')
    pending.replace(path)


def compare_judgments(
    judgments: list[HumanJudgment],
    pairs: dict[str, SavedPairedResult],
) -> list[tuple[Contribution, Contribution]]:
    """
    Match human labels with the current automated judgments.
    """
    return [
        (record.contribution, automated)
        for record in judgments
        if (pair := pairs.get(record.pair_id)) is not None
        and (automated := pair.contribution) is not None
    ]


def save_progress(
    path: Path, judgments: list[HumanJudgment], pairs: dict[str, SavedPairedResult],
) -> None:
    """
    Save labels first, then refresh their comparison chart.
    """
    save_judgments(path=path, judgments=judgments)
    comparisons = compare_judgments(judgments=judgments, pairs=pairs)
    build_judgment_plot(comparisons=comparisons).savefig(path.with_suffix('.svg'))


def report_agreement(
    judgments: list[HumanJudgment], pairs: dict[str, SavedPairedResult],
) -> None:
    """
    Print how often the human and LLM labels agree.
    """
    comparisons = compare_judgments(judgments=judgments, pairs=pairs)
    matches = sum(human == automated for human, automated in comparisons)
    rate = f'{matches / len(comparisons):.0%}' if comparisons else 'N/A'
    print(f'Agreement: {matches}/{len(comparisons)} ({rate})')


def git_email() -> str:
    """
    Read the configured email in git for output saves.
    """
    email = subprocess.run(
        ['git', 'config', 'user.email'], check=False, capture_output=True, text=True,
    ).stdout.strip()
    if not email:
        raise ValueError('Set git user.email before labeling.')
    return email


def run_labeling(results_path: Path) -> None:
    """
    Load results, resume human labeling, and report agreement with the LLM.
    """
    labels_path = results_path.with_name('human_judgments.json')
    runs = load_runs(path=results_path)
    judgments = load_judgments(path=labels_path)

    judgments = remove_stale_judgments(judgments=judgments, pairs=index_pairs(runs=runs))
    judged_pairs = pairs_for_comparison(runs=runs)
    pending_pairs = filter_unlabeled_pairs(pairs=judged_pairs, judgments=judgments)
    save_progress(path=labels_path, judgments=judgments, pairs=judged_pairs)
    if pending_pairs:
        email = git_email()
        print(RUBRIC)
        try:
            for index, (identity, pair) in enumerate(pending_pairs.items(), 1):
                print(f'\nPair {index}/{len(pending_pairs)}')
                contribution = review_pair(pair=pair)
                if contribution == 'quit':
                    break
                if contribution == 'skip':
                    continue
                judgments.append(HumanJudgment(
                    pair_id=identity, contribution=contribution, email=email,
                ))
                save_progress(path=labels_path, judgments=judgments, pairs=judged_pairs)
                agreement = 'agree' if contribution == pair.contribution else 'disagree'
                print(
                    f'Automated: {pair.contribution}; '
                    f'human: {contribution} — {agreement}.',
                )
        except (EOFError, KeyboardInterrupt):
            print('Labeling stopped.')

    report_agreement(judgments=judgments, pairs=judged_pairs)
    print(f'Labels: {labels_path}\nChart: {labels_path.with_suffix(".svg")}')


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description='Label LLM gap analysis judgments')
    parser.add_argument(
        'results', nargs='?', type=Path,
        default=Path('benchmarks/gap_analysis/results.json'),
        help='Results JSON; defaults to benchmarks/gap_analysis/results.json.',
    )
    args = parser.parse_args(argv)
    try:
        run_labeling(results_path=args.results)
    except Exception as e:
        parser.exit(status=1, message=f'Cannot label results: {e}\n')


if __name__ == '__main__':
    main()
