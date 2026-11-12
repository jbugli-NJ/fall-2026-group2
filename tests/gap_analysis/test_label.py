"""
Tests for human gap analyis judgement labeling.
"""

# Imports

import json
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest

from monty_tool.gap_analysis import label
from monty_tool.gap_analysis.judging import RUBRIC_VERSION
from monty_tool.gap_analysis.plotting import build_judgment_plot
from monty_tool.gap_analysis.schemas import (
    Evidence,
    Contribution, PairedResult, SavedAnalysisRun, SavedAnswerAttempt, SavedPairedResult,
)


# Test helpers

@pytest.fixture
def saved_run(pair: PairedResult) -> SavedAnalysisRun:
    """
    A judged answer pair with archived responses.
    """
    attempt = SavedAnswerAttempt(
        answer_s3_uri='s3://answers/core.txt', input_tokens=0,
        output_tokens=0, duration_seconds=0,
    )
    return SavedAnalysisRun(
        metadata={
            'started_at': '2026-11-09',
            'rubric_version': RUBRIC_VERSION,
        },
        results=[SavedPairedResult(
            **pair.model_dump(exclude={'core', 'external'}),
            core=attempt,
            external=attempt.model_copy(
                update={'answer_s3_uri': 's3://answers/external.txt'},
            ),
        )],
    )


# Tests

def test_pair_display(saved_run: SavedAnalysisRun, pair: PairedResult) -> None:
    """
    Test evidence before the answers and a code recap after them.
    """
    core = 'Core answer text'
    external = 'External answer text'
    evidence = cast(
        Evidence,
        {
            'events': [{
                'description': 'Supplied disaster record',
                'impacts': [],
            }],
            'responses': [],
            'responses': [],
            'news': [{'evidence_excerpt': 'Homes were damaged.'}],
        },
    )
    saved_run.results[0].external_source = 'news'
    rendered = label.format_pair(
        pair=saved_run.results[0],
        core=core,
        external=external,
        evidence=evidence,
    )
    assert pair.question.question in rendered
    assert 'Core answer text' in rendered
    assert 'External answer text' in rendered
    assert 'RECORDS' in rendered
    assert rendered.index(
        'Supplied disaster record',
    ) < rendered.index('External answer text')
    assert 'Homes were damaged.' in rendered
    assert rendered.index('Homes were damaged.') < rendered.index('Core answer text')
    assert rendered.index('Core answer text') < rendered.index('External answer text')
    assert rendered.index('External answer text') < rendered.index('improvement')
    assert 'regression' in rendered


def test_label_and_resume(
    saved_run: SavedAnalysisRun,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Test label saving and resuming, making sure that the same pair isn't labeled twice.
    """
    monkeypatch.chdir(tmp_path)
    results = tmp_path / 'benchmarks/gap_analysis/results.json'
    results.parent.mkdir(parents=True)
    results.write_text(json.dumps({'runs': [saved_run.model_dump(mode='json')]}))
    monkeypatch.setattr(label, 'git_email', lambda: 'reviewer@example.com')
    monkeypatch.setattr(label, 'review_pair', lambda pair: Contribution.NO_CHANGE)
    label.main(argv=[])
    labels = results.with_name('human_judgments.json')
    records = json.loads(labels.read_text())
    assert records == [{
        'pair_id': label.pair_id(run=saved_run, pair=saved_run.results[0]),
        'contribution': 'no_change',
        'email': 'reviewer@example.com',
    }]
    assert results.with_name('human_judgments.svg').is_file()

    monkeypatch.setattr(label, 'review_pair', lambda pair: pytest.fail('Already labeled'))
    label.main(argv=[])
    assert json.loads(labels.read_text()) == records


def test_cleanup_before_quitting(
    saved_run: SavedAnalysisRun,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Check that stale labels are removed when the tool is run.
    """
    pair = saved_run.results[0]
    old_id = label.pair_id(run=saved_run, pair=pair)
    pair.external.answer_s3_uri = 's3://answers/replacement.json'
    results = tmp_path / 'results.json'
    results.write_text(json.dumps({'runs': [saved_run.model_dump(mode='json')]}))
    labels = tmp_path / 'human_judgments.json'
    labels.write_text(json.dumps([
        {
            'pair_id': identity,
            'contribution': 'improvement',
            'email': 'reviewer@example.com',
        }
        for identity in [old_id, 'deleted-pair']
    ]))
    monkeypatch.setattr(label, 'git_email', lambda: 'reviewer@example.com')
    monkeypatch.setattr(label, 'review_pair', lambda pair: 'quit')
    label.run_labeling(results_path=results)
    assert json.loads(labels.read_text()) == []


def test_old_rubric_labels_are_not_reinterpreted(saved_run, tmp_path):
    """
    Old results and human labels can be loaded without assigning new meanings.
    """
    old = saved_run.model_dump(mode='json')
    old['metadata']['rubric_version'] = 'source-contribution-v2'
    old['results'][0]['contribution'] = 'extends_coverage'
    results = tmp_path / 'results.json'
    results.write_text(json.dumps({'runs': [old, saved_run.model_dump(mode='json')]}))
    assert label.load_runs(results) == [saved_run]
    judgments = tmp_path / 'human_judgments.json'
    judgments.write_text(json.dumps([{
        'pair_id': 'old-pair',
        'contribution': 'unsupported',
        'email': 'reviewer@example.com',
    }]))
    assert label.load_judgments(judgments) == []


def test_review_loads_evidence_from_saved_path(saved_run, monkeypatch):
    """
    Review combines archived answers with the evidence snapshot used for answering.
    """
    pair = saved_run.results[0]
    answers = Mock(side_effect=['Core answer', 'External answer'])
    monkeypatch.setattr(label, 'load_answer', answers)
    retrieve = Mock(
        return_value={
            'events': [{
                'event_id': 'current',
                'impacts': [],
            }],
            'responses': [],
        },
    )
    monkeypatch.setattr(label, 'load_evidence', retrieve)
    display = Mock(return_value='Pair for review')
    monkeypatch.setattr(label, 'format_pair', display)
    monkeypatch.setattr(label, 'read_choice', lambda: 'B')
    assert label.review_pair(pair) == Contribution.NO_CHANGE
    assert [call.kwargs['uri'] for call in answers.call_args_list] == [
        pair.core.answer_s3_uri, pair.external.answer_s3_uri,
    ]
    retrieve.assert_called_once_with(pair.evidence_s3_uri)
    display.assert_called_once_with(
        pair=pair,
        core='Core answer',
        external='External answer',
        evidence=retrieve.return_value,
    )


def test_evidence_path_is_part_of_pair_identity(saved_run):
    """
    A different evidence snapshot requires a new human judgment.
    """
    pair = saved_run.results[0]
    original = label.pair_id(saved_run, pair)
    pair.evidence_s3_uri = 's3://answers/gap_analysis/evidence/replacement.json'
    assert label.pair_id(saved_run, pair) != original
