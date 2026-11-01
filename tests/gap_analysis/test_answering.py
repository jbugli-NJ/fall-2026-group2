"""
Tests for answering gap analysis questions.
"""

from contextlib import nullcontext
from unittest.mock import Mock

import pytest

from monty_tool.gap_analysis import answering
from monty_tool.llm.tools import GraphSearchArguments


@pytest.mark.parametrize(
    'statuses, failed',
    [([], True), (['error'], True), (['ok'], False), (['error', 'ok'], False)],
)
def test_answer_requires_successful_retrieval(pair, statuses, failed):
    """
    Verify answers fail when no tool query succeeds.
    """
    assistant = Mock()
    assistant.ask.return_value = {
        'answer': 'Answer',
        'tool_results': [{'result': {'status': status}} for status in statuses],
    }
    result = answering.answer_question(assistant, pair.question, external_source=None)
    assert (result.error is not None) == failed
    assert result.response == assistant.ask.return_value


def test_model_error_is_saved(pair):
    """
    Verify model exceptions are saved as answer errors.
    """
    assistant = Mock()
    assistant.ask.side_effect = ValueError('Model failed')
    result = answering.answer_question(assistant, pair.question, external_source=None)
    assert result.error == 'Model failed'


@pytest.mark.parametrize('require_external_data', [True, False])
def test_event_selection_uses_one_graph_query(monkeypatch, require_external_data):
    """
    Pass the event limit and external data requirement to the selection query.
    """
    driver = Mock()
    driver.execute_query.return_value = (
        [Mock(data=lambda: {'event_id': 'e'})], None, None,
    )
    monkeypatch.setattr(answering, 'get_graph_db_driver', lambda: nullcontext(driver))
    assert answering.select_contexts(
        GraphSearchArguments(), 2, require_external_data=require_external_data,
    ) == [{'event_id': 'e'}]
    driver.execute_query.assert_called_once()
    assert driver.execute_query.call_args.kwargs['parameters_']['limit'] == 2
    assert (
        driver.execute_query.call_args.kwargs['parameters_']['require_external_data']
        is require_external_data
    )
