"""
Tests for the QueryAssistant client.
"""

# Imports

from typing import Any
from unittest.mock import Mock, call

import pytest

from monty_tool.llm import client


# Test object helpers

def _query_assistant(monkeypatch: pytest.MonkeyPatch) -> client.QueryAssistant:
    """
    Creates a QueryAssistant without loading a tokenizer or model.
    """
    monkeypatch.setattr(
        client.AutoTokenizer,
        'from_pretrained',
        Mock(return_value=Mock()),
    )
    monkeypatch.setattr(
        client.AutoModelForCausalLM,
        'from_pretrained',
        Mock(return_value=Mock()),
    )
    return client.QueryAssistant()


# Tests

@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('A normal answer.', []),
        (
            '<tool_call>'
            '{"name": "search_disaster_events", "arguments": {"country_code": "JPN"}}'
            '</tool_call>',
            [{'name': 'search_disaster_events', 'arguments': {'country_code': 'JPN'}}],
        ),
        (
            '<tool_call>{"name": "search_disaster_events", "arguments": {}}</tool_call>'
            '<tool_call>{"name": "get_disaster_context", '
            '"arguments": {"event_id": "event-1"}}</tool_call>',
            [
                {'name': 'search_disaster_events', 'arguments': {}},
                {'name': 'get_disaster_context', 'arguments': {'event_id': 'event-1'}},
            ],
        ),
    ],
)
def test_parse_tool_calls_returns_valid_calls(text: str, expected: list[dict[str, Any]]):
    """
    Ensures valid Qwen tool call markup is parsed.
    """
    assert client.parse_tool_calls(text) == expected


@pytest.mark.parametrize(
    'text',
    [
        '<tool_call>{"name": "search_disaster_events", "arguments": {}}',
        '<tool_call>not json</tool_call>',
        '<tool_call>{"name": "search_disaster_events"}</tool_call>',
        '<tool_call>{"name": 1, "arguments": {}}</tool_call>',
        '<tool_call>{"name": "search_disaster_events", "arguments": []}</tool_call>',
    ],
)
def test_parse_tool_calls_rejects_malformed_calls(text: str):
    """
    Ensures malformed Qwen tool call markup is rejected.
    """
    with pytest.raises(ValueError):
        client.parse_tool_calls(text)


def test_parse_tool_call_requires_at_most_one_call():
    """
    Ensures the tool call parser returns one call and rejects multiple calls.
    """
    text = '<tool_call>{"name": "search_disaster_events", "arguments": {}}</tool_call>'
    assert client.parse_tool_call(text) == {
        'name': 'search_disaster_events',
        'arguments': {},
    }
    with pytest.raises(ValueError, match='exactly one'):
        client.parse_tool_call(text + text)


def test_query_assistant_returns_an_answer_without_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
    ):
    """
    Ensures a direct model answer needs no tool execution.
    """
    assistant = _query_assistant(monkeypatch)
    execute = Mock()
    monkeypatch.setattr(assistant.tools, 'execute', execute)
    assistant._generate = Mock(return_value='A direct answer.')

    result = assistant.ask('What happened?')

    assert result == {'answer': 'A direct answer.', 'tool_results': []}
    execute.assert_not_called()


def test_query_assistant_executes_tool_calls_before_answering(
    monkeypatch: pytest.MonkeyPatch,
    ):
    """
    Checks that all tool calls run before the final answer is returned.
    """
    assistant = _query_assistant(monkeypatch)
    tool_results = {
        'search_disaster_events': {
            'status': 'ok',
            'rows': [{'event_id': 'event-1'}],
        },
        'get_disaster_context': {
            'status': 'ok',
            'rows': [{'event_id': 'event-1', 'title': 'Flood'}],
        },
    }
    execute = Mock(side_effect=lambda name, arguments: tool_results[name])
    monkeypatch.setattr(assistant.tools, 'execute', execute)
    assistant._generate = Mock(side_effect=[
        '<tool_call>{"name": "search_disaster_events", '
        '"arguments": {"country_code": "JPN"}}</tool_call>'
        '<tool_call>{"name": "get_disaster_context", '
        '"arguments": {"event_id": "event-1"}}</tool_call>',
        'The graph returned one matching event.',
    ])

    result = assistant.ask('Find disasters in Japan.')

    assert result == {
        'answer': 'The graph returned one matching event.',
        'tool_results': [
            {
                'call': {
                    'name': 'search_disaster_events',
                    'arguments': {'country_code': 'JPN'},
                },
                'result': tool_results['search_disaster_events'],
            },
            {
                'call': {
                    'name': 'get_disaster_context',
                    'arguments': {'event_id': 'event-1'},
                },
                'result': tool_results['get_disaster_context'],
            },
        ],
    }
    assert execute.call_args_list == [
        call('search_disaster_events', {'country_code': 'JPN'}),
        call('get_disaster_context', {'event_id': 'event-1'}),
    ]


@pytest.mark.parametrize(
    'response',
    [
        '',
        '<tool_call>{"name": "search_disaster_events"}</tool_call>',
    ],
)
def test_query_assistant_rejects_empty_or_malformed_model_responses(
    monkeypatch: pytest.MonkeyPatch,
    response: str,
    ):
    """
    Ensures the assistant does not accept empty or malformed model output.
    """
    assistant = _query_assistant(monkeypatch)
    assistant._generate = Mock(return_value=response)

    with pytest.raises(ValueError):
        assistant.ask('What happened?')
