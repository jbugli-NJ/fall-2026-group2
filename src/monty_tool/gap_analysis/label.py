"""
Script to hand-label gap analysis answer pairs for comparison with LLM judging.
"""

# Imports

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from typing import Any, Literal
from urllib.parse import urlparse

from botocore.exceptions import BotoCoreError, ClientError
from pydantic import BaseModel, Field, TypeAdapter

from monty_tool.boto3_utils.s3_utils import download_object, get_bucket
from monty_tool.gap_analysis.external_sources import EXTERNAL_SOURCES
from monty_tool.gap_analysis.judging import LABELS, RUBRIC, RUBRIC_VERSION
from monty_tool.gap_analysis.plotting import build_judgment_plot
from monty_tool.gap_analysis.schemas import (
    Contribution, SavedAnalysisRun, SavedPairedResult,
)
from monty_tool.llm.schemas import QueryAssistantResponse


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
    return TypeAdapter(list[SavedAnalysisRun]).validate_python(payload['runs'])


def load_judgments(path: Path) -> list[HumanJudgment]:
    """
    Read saved human labels, or start with none if the file does not exist.
    """
    return (
        TypeAdapter(list[HumanJudgment]).validate_json(path.read_text())
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
        'core_context': pair.core_context,
        'core_uri': pair.core.response_s3_uri,
        'external_uri': pair.external.response_s3_uri,
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
    current_runs = [run for run in runs if run.metadata.get('rubric_version') == RUBRIC_VERSION]
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


def load_response(uri: str | None) -> QueryAssistantResponse:
    """
    Retrieve an archived answer using the bucket and key in its S3 URI.
    """
    parsed = urlparse(uri or '')
    if parsed.scheme != 's3' or not parsed.netloc or not parsed.path.lstrip('/'):
        raise ValueError(f'Invalid response S3 URI: {uri}')
    with TemporaryDirectory(prefix='gap-label-') as directory:
        path = Path(directory, 'response.json')
        download_object(
            bucket=get_bucket(bucket_name=parsed.netloc),
            key=parsed.path.lstrip('/'),
            output_path=path,
        )
        return TypeAdapter(QueryAssistantResponse).validate_json(path.read_text())


def evidence_markdown(value: Any) -> str:
    """
    Unwrap tool results and show nonempty evidence fields as Markdown.
    """
    if isinstance(value, list):
        items = [item for item in value if item not in (None, '', [], {})]
        if not items:
            return '*No evidence returned.*'
        if all(not isinstance(item, (dict, list)) for item in items):
            return ', '.join(str(item) for item in items)
        return '\n\n---\n\n'.join(evidence_markdown(value=item) for item in items)
    if isinstance(value, dict):
        if 'result' in value:
            call = value.get('call', {})
            name = call.get('name', 'Retrieved evidence')
            sections = [f'#### {name}']
            if call.get('arguments'):
                sections.append('Query:\n\n' + evidence_markdown(value=call['arguments']))
            sections.append(evidence_markdown(value=value['result']))
            return '\n\n'.join(sections)
        if 'rows' in value and value.get('status') == 'ok':
            return evidence_markdown(value=value['rows'])
        fields = []
        for key, item in value.items():
            if item in (None, '', [], {}) or (key == 'status' and item == 'ok'):
                continue
            name = key.replace('_', ' ').capitalize()
            text = evidence_markdown(value=item)
            nested = isinstance(item, dict) or (
                isinstance(item, list) and any(isinstance(v, dict) for v in item)
            )
            if nested:
                fields.append(f'**{name}**\n\n{text}')
            else:
                fields.append(f'- **{name}:** ' + text.replace('\n', '\n  '))
        return '\n\n'.join(fields) or '*No evidence returned.*'
    return str(value)


def format_pair(
    pair: SavedPairedResult,
    core: QueryAssistantResponse,
    external: QueryAssistantResponse,
) -> str:
    """
    Render a pair's question, answers, and evidence for judgement.
    """
    sections = [
        f'# {pair.question.country_code} / {pair.question.hazard_name}',
        f'## Question\n\n{pair.question.question}',
        f'## External source: {pair.external_source}\n\n'
        + EXTERNAL_SOURCES[pair.external_source].evidence_description,
        '## Core context\n\n' + evidence_markdown(value=pair.core_context),
    ]
    for heading, response in [
        ('Core-only answer', core),
        ('External-enabled answer', external),
    ]:
        sections.extend([
            f'## {heading}\n\n{response["answer"]}',
            '### Retrieved evidence',
            evidence_markdown(value=response['tool_results']),
        ])
    return '\n\n'.join(sections)


def read_choice() -> str:
    """
    Prompt until the user enters a category, skip, or quit.
    """
    while True:
        choice = input('Label [A–G / s / q]: ').strip().upper()
        if choice in LABELS or choice in ('S', 'Q'):
            return choice
        print('Enter A–G, s, or q.')


def review_pair(pair: SavedPairedResult) -> Contribution | Literal['skip', 'quit']:
    """
    Show one pair's answers and evidence, then ask the user to label it.
    """
    try:
        core = load_response(uri=pair.core.response_s3_uri)
        external = load_response(uri=pair.external.response_s3_uri)
    except (OSError, ValueError, BotoCoreError, ClientError) as exc:
        print(f'Skipping pair: {exc}')
        return 'skip'
    print(format_pair(pair=pair, core=core, external=external))
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
    for run in runs:
        if run.metadata.get('rubric_version') != RUBRIC_VERSION:
            print(f'Skipping unsupported rubric: {run.metadata.get("rubric_version")}')

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
                print(f'Automated: {pair.contribution}; human: {contribution} — {agreement}.')
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
