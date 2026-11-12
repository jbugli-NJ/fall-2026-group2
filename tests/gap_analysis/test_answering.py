"""
Tests for answering gap analysis questions.
"""

import json
from contextlib import nullcontext
from unittest.mock import Mock

import pytest
from neo4j.time import DateTime

from monty_tool.gap_analysis import answering
from monty_tool.llm.tools import GraphSearchArguments
from monty_tool.gap_analysis.schemas import GapSelection


def test_answer_uses_supplied_records_without_tools(pair):
    """
    Records enter the prompt while only answer text and token counts are retained.
    """
    assistant = Mock()
    assistant.ask.return_value = {
        'answer': 'Answer',
        'input_tokens': 0,
        'output_tokens': 0,
        'tool_results': [],
    }
    started = DateTime(2020, 1, 1, 12, 0, 0, nanosecond=123456789)
    evidence = {
        'events': [{
            'event_id': 'one',
            'impacts': [],
            'start_datetime': started,
            'weather': {'mean_temperature': 20},
        }],
        'responses': [],
    }
    result = answering.answer_question(
        assistant,
        pair.question,
        external_source=None,
        evidence=evidence,
    )
    assert result.error is None
    assert result.answer == 'Answer'
    assert set(result.model_dump()) == {
        'answer', 'input_tokens', 'output_tokens', 'error', 'duration_seconds',
    }
    assert assistant.ask.call_args.kwargs == {'use_tools': False}
    assert json.loads(assistant.ask.call_args.args[0])['records'] == {
        'events': [{
            'event_id': 'one',
            'impacts': [],
            'start_datetime': str(started),
        }],
        'responses': [],
    }
    assert evidence['events'][0]['start_datetime'] is started


def test_model_error_is_saved(pair):
    """
    Verify model exceptions are saved as answer errors.
    """
    assistant = Mock()
    assistant.ask.side_effect = ValueError('Model failed')
    result = answering.answer_question(
        assistant,
        pair.question,
        external_source=None,
        evidence={
            'events': [],
            'responses': [],
        },
    )
    assert result.error == 'Model failed'


@pytest.mark.parametrize('require_external_data', [True, False])
@pytest.mark.parametrize('sources', [None, ['news', 'weather'], ['shakemap_footprints']])
def test_country_hazard_selection_uses_one_graph_query(
    monkeypatch,
    require_external_data,
    sources,
):
    """
    Pass country, hazard, limit, and source requirements to the selection query.
    """
    driver = Mock()
    driver.execute_query.return_value = (
        [Mock(
            data=lambda: {
                'filters': {'country_code': 'JPN'},
                'hazard_name': 'Earthquake',
            },
        )], None, None,
    )
    monkeypatch.setattr(answering, 'get_graph_db_driver', lambda: nullcontext(driver))
    assert answering.select_country_hazards(
        GraphSearchArguments(country_code='JPN'),
        2,
        hazard_name='Earthquake',
        require_external_data=require_external_data,
        external_sources=sources,
    ) == [GapSelection(
        filters=GraphSearchArguments(country_code='JPN'),
        hazard_name='Earthquake',
    )]
    driver.execute_query.assert_called_once()
    assert driver.execute_query.call_args.kwargs['parameters_']['limit'] == 2
    assert (
        driver.execute_query.call_args.kwargs['parameters_']['require_external_data']
        is require_external_data
    )
    assert driver.execute_query.call_args.kwargs['parameters_']['external_sources'] == (
        ['news', 'weather', 'shakemap_footprints'] if sources is None else sources
    )

    assert driver.execute_query.call_args.kwargs['parameters_']['country_code'] == 'JPN'
    assert (
        driver.execute_query.call_args.kwargs['parameters_']['hazard_name']
        == 'Earthquake'
    )
    names = driver.execute_query.call_args.kwargs['parameters_']['hazard_code_name_map']
    assert names['GH0001'] == 'Earthquake'
    assert names['GH009'] == 'Volcanic eruption'
    assert 'unknown-code' not in names
