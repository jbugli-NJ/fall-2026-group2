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
        'A': 'fills_field', 'B': 'extends_coverage', 'C': 'qualifies',
        'D': 'corroborates', 'E': 'core_sufficient',
        'F': 'unresolved', 'G': 'unsupported',
    }
    assert set(judging.LABELS.values()) == set(Contribution)
    for letter, category in judging.LABELS.items():
        assert f'{letter}. {category.value}:' in judging.RUBRIC


def test_local_judge_uses_label_classifier(monkeypatch, pair):
    """
    Verify the local judge maps the classifier's letter to a contribution.
    """
    classify = Mock(return_value='C')
    monkeypatch.setattr(judging, 'classify_labels', classify)
    assert judging.LocalContributionJudge(Mock()).judge(pair) == Contribution.QUALIFIES
    assert classify.call_args.args[2] == tuple('ABCDEFG')


def test_bedrock_judge_reads_label_and_rejects_incomplete_response(pair):
    """
    Verify Bedrock labels are parsed and incomplete responses raise an error.
    """
    assistant = Mock()
    response = {
        'stopReason': 'end_turn',
        'output': {'message': {'content': [{'text': '{"label":"C"}'}]}},
    }
    assistant.client.converse.return_value = response
    judge = judging.BedrockContributionJudge(assistant)
    assert judge.judge(pair) == Contribution.QUALIFIES
    request = assistant.client.converse.call_args.kwargs
    assert request['system'] == [{'text': judging.RUBRIC}]
    assert json.loads(request['messages'][0]['content'][0]['text'])['core_context'] == {
        'event_id': 'e',
    }
    response['stopReason'] = 'max_tokens'
    with pytest.raises(ValueError):
        judge.judge(pair)
