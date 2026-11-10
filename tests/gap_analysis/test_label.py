"""
Tests for human gap analyis judgement labeling.
"""

# Imports

import json
from pathlib import Path

import pytest

from monty_tool.gap_analysis import label
from monty_tool.gap_analysis.judging import RUBRIC_VERSION
from monty_tool.gap_analysis.plotting import build_judgment_plot
from monty_tool.gap_analysis.schemas import (
    Contribution, PairedResult, SavedAnalysisRun, SavedAnswerAttempt, SavedPairedResult,
)


# Test helpers

@pytest.fixture
def saved_run(pair: PairedResult) -> SavedAnalysisRun:
    """
    A judged answer pair with archived responses.
    """
    attempt = SavedAnswerAttempt(
        response_s3_uri='s3://answers/core.json', input_tokens=0,
        output_tokens=0, tool_failure_count=0, duration_seconds=0,
    )
    return SavedAnalysisRun(
        metadata={'started_at': '2026-11-09', 'rubric_version': RUBRIC_VERSION},
        results=[SavedPairedResult(
            **pair.model_dump(exclude={'core', 'external'}),
            core=attempt,
            external=attempt.model_copy(update={'response_s3_uri': 's3://answers/external.json'}),
        )],
    )


# Tests

def test_pair_display(saved_run: SavedAnalysisRun, pair: PairedResult) -> None:
    """
    Test that the question, both answers, and retrieved evidence are displayed.
    """
    assert pair.core.response is not None
    core = pair.core.response.copy()
    core['answer'] = 'Core answer text'
    external = core.copy()
    external['answer'] = 'External answer text'
    external['tool_results'] = [{
        'call': {'name': 'get_event_news', 'arguments': {'event_id': 'event-1'}},
        'result': {'rows': [{'evidence_excerpt': 'Homes were damaged.'}]},
    }]
    rendered = label.format_pair(pair=saved_run.results[0], core=core, external=external)
    assert pair.question.question in rendered
    assert 'Core answer text' in rendered
    assert 'External answer text' in rendered
    assert 'get_event_news' in rendered
    assert 'event-1' in rendered
    assert 'Homes were damaged.' in rendered


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
    monkeypatch.setattr(label, 'review_pair', lambda pair: Contribution.UNRESOLVED)
    label.main(argv=[])
    labels = results.with_name('human_judgments.json')
    records = json.loads(labels.read_text())
    assert records == [{
        'pair_id': label.pair_id(run=saved_run, pair=saved_run.results[0]),
        'contribution': 'unresolved',
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
    pair.external.response_s3_uri = 's3://answers/replacement.json'
    results = tmp_path / 'results.json'
    results.write_text(json.dumps({'runs': [saved_run.model_dump(mode='json')]}))
    labels = tmp_path / 'human_judgments.json'
    labels.write_text(json.dumps([
        {'pair_id': identity, 'contribution': 'qualifies', 'email': 'reviewer@example.com'}
        for identity in [old_id, 'deleted-pair']
    ]))
    monkeypatch.setattr(label, 'git_email', lambda: 'reviewer@example.com')
    monkeypatch.setattr(label, 'review_pair', lambda pair: 'quit')
    label.run_labeling(results_path=results)
    assert json.loads(labels.read_text()) == []
