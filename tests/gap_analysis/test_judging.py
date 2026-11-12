"""
Tests for contribution labels and judge adapters.
"""

import json
from unittest.mock import Mock

import pytest

from monty_tool.gap_analysis import judging
from monty_tool.gap_analysis.schemas import Contribution


def test_label_meanings_and_coverage():
    """
    Verify letter mappings cover every category and match the rubric.
    """
    assert judging.LABELS == {
        'A': 'improvement',
        'B': 'no_change',
        'C': 'regression',
    }
    assert set(judging.LABELS.values()) == set(Contribution)
    for letter, category in judging.LABELS.items():
        assert f'{letter}. {category.value}:' in judging.RUBRIC


@pytest.fixture
def records():
    """
    Supply the shared evidence snapshot held by the runner.
    """
    return {
        'events': [{
            'event_id': 'current',
            'impacts': [],
            'weather': {'mean_temperature': 20},
        }],
        'responses': [],
        'news': [{'evidence_excerpt': 'Damage reported'}],
    }


def test_local_judge_uses_label_classifier(monkeypatch, pair, records):
    """
    Verify the local judge maps the classifier's letter to a contribution.
    """
    classify = Mock(return_value='C')
    monkeypatch.setattr(judging, 'classify_labels', classify)
    assert judging.LocalContributionJudge(Mock()).judge(
        pair,
        records,
    ) == Contribution.REGRESSION
    assert classify.call_args.args[2] == tuple('ABC')
    payload = json.loads(classify.call_args.args[1][1]['content'])
    assert payload['core_answer'] == pair.core.answer
    assert payload['records'] == {
        'events': [{
            'event_id': 'current',
            'impacts': [],
        }],
        'responses': [],
    }


@pytest.mark.parametrize(
    'source, evidence',
    [
        ('weather', 'Weather fields in supplied disaster and impact records.'),
        (
            'shakemap_footprints',
            'USGS ShakeMap footprints in external_footprints in graph query results. '
            'The footprints field contains Montandon evidence and remains core evidence.',
        ),
    ],
)
def test_bedrock_judge_reads_label_and_rejects_incomplete_response(
    pair,
    source,
    evidence,
    records,
):
    """
    Verify Bedrock labels are parsed and incomplete responses raise an error.
    """
    pair.external_source = source
    assistant = Mock()
    response = {
        'stopReason': 'end_turn',
        'output': {'message': {'content': [{'text': '{"label":"C"}'}]}},
    }
    assistant.client.converse.return_value = response
    judge = judging.BedrockContributionJudge(assistant)
    assert judge.judge(pair, records) == Contribution.REGRESSION
    request = assistant.client.converse.call_args.kwargs
    assert request['system'] == [{'text': judging.RUBRIC}]
    payload = json.loads(request['messages'][0]['content'][0]['text'])
    assert payload['selection'] == pair.selection.model_dump(mode='json')
    assert payload['external_source'] == source
    assert payload['external_source_evidence'] == evidence
    expected_event = records['events'][0].copy()
    if source != 'weather':
        expected_event.pop('weather')
    assert payload['records'] == {
        'events': [expected_event],
        'responses': [],
    }
    assert 'core_records' not in payload
    assert 'external_enabled_records' not in payload
    response['stopReason'] = 'max_tokens'
    with pytest.raises(ValueError):
        judge.judge(pair, records)


@pytest.mark.parametrize('source', ['news', 'weather', 'shakemap_footprints'])
def test_judging_uses_supplied_records_without_storage_or_graph_access(
    monkeypatch,
    pair,
    records,
    source,
):
    """
    Judging uses the in-memory snapshot and includes only the selected source.
    """
    from monty_tool.gap_analysis import retrieval, storage
    graph = Mock(side_effect=AssertionError('Unexpected graph access'))
    archive = Mock(side_effect=AssertionError('Unexpected archive access'))
    monkeypatch.setattr(retrieval, 'get_graph_db_driver', graph)
    monkeypatch.setattr(storage, 'load_evidence', archive)
    pair.external_source = source
    content = judging.judgment_messages(pair, records)[1]['content']
    payload = json.loads(content)
    assert content.count('current') == 1
    assert ('news' in payload['records']) == (source == 'news')
    assert ('weather' in payload['records']['events'][0]) == (source == 'weather')
    graph.assert_not_called()
    archive.assert_not_called()


def test_judge_does_not_classify_incomplete_answers(monkeypatch, pair, records):
    """
    Missing answers prevent classification.
    """
    pair.core.answer = None
    classify = Mock()
    monkeypatch.setattr(judging, 'classify_labels', classify)
    with pytest.raises(ValueError, match='Both answers must complete'):
        judging.LocalContributionJudge(Mock()).judge(pair, records)
    classify.assert_not_called()
