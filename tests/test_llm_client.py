"""
Tests for the query assistant clients.
"""

# Imports

import json
from typing import Any
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
import torch
from transformers import AutoConfig, AutoModelForCausalLM, AutoTokenizer
from transformers.cli.serving.utils import get_response_template
from transformers.utils.chat_parsing import parse_response

from monty_tool.llm import client
from monty_tool.utils.versions import FrozenModel


# Test object helpers

def _query_assistant(
    monkeypatch: pytest.MonkeyPatch,
    model_id: FrozenModel = FrozenModel.QWEN3_1_7B,
) -> client.QueryAssistant:
    """
    Creates a QueryAssistant without loading a tokenizer or model.
    """
    model_type = 'qwen3' if model_id == FrozenModel.QWEN3_1_7B else 'qwen3_5'
    config = AutoConfig.for_model(model_type)
    monkeypatch.setattr(
        AutoTokenizer,
        'from_pretrained',
        Mock(return_value=Mock(
            spec=['response_template', 'apply_chat_template', 'decode', 'parse_response', 'eos_token_id'],
            response_template=None,
        )),
    )
    monkeypatch.setattr(
        AutoModelForCausalLM,
        'from_pretrained',
        Mock(return_value=Mock(config=config.get_text_config())),
    )
    return client.QueryAssistant(model_id=model_id)


def _parsed(text: str, model_id: FrozenModel = FrozenModel.QWEN3_1_7B) -> dict[str, Any]:
    """
    Parse mocked generation output with the same Transformers templates as the client.
    """
    model_type = 'qwen3' if model_id == FrozenModel.QWEN3_1_7B else 'qwen3_5'
    template = get_response_template(
        SimpleNamespace(response_template=None),
        Mock(config=Mock(model_type=model_type)),
    )
    assert template is not None
    return parse_response(text, template, prefix='')


def _bedrock_assistant(
    monkeypatch: pytest.MonkeyPatch,
    ) -> tuple[client.BedrockQueryAssistant, Mock]:
    """
    Create an assistant with a mocked Bedrock client.
    """
    runtime = Mock()
    monkeypatch.setattr(client.boto3, 'client', Mock(return_value=runtime))
    return client.BedrockQueryAssistant(), runtime.converse


def _bedrock_response(content: list[dict[str, Any]], stop_reason: str = 'end_turn') -> dict[str, Any]:
    """
    Build a model response.
    """
    return {'output': {'message': {'role': 'assistant', 'content': content}}, 'stopReason': stop_reason}


# Tests

@pytest.mark.parametrize('requests_tool', [False, True])
def test_query_assistant_no_tools(monkeypatch: pytest.MonkeyPatch, requests_tool: bool):
    """
    Hide tool definitions and prevent execution even if the model requests a tool.
    """
    assistant = _query_assistant(monkeypatch)
    execute = Mock()
    monkeypatch.setattr(assistant.tools, 'execute', execute)
    response = (
        _parsed('<tool_call>{"name": "search_disaster_events", "arguments": {}}</tool_call>')
        if requests_tool else {'content': 'Answer from memory'}
    )
    generate = Mock(return_value=response)
    monkeypatch.setattr(assistant, '_generate', generate)

    if requests_tool:
        with pytest.raises(ValueError, match='after the tool call limit'):
            assistant.ask('Question', use_tools=False)
    else:
        assert assistant.ask('Question', use_tools=False) == {
            'answer': 'Answer from memory', 'tool_results': [],
        }
    execute.assert_not_called()
    assert generate.call_args.kwargs['use_tools'] is False
    prompt = generate.call_args.args[0][0]['content']
    assert 'own knowledge' in prompt
    assert 'tool' not in prompt.lower()

    generate.return_value = {'content': 'Answer with tools available'}
    assistant.ask('Next question')
    assert generate.call_args.kwargs['use_tools'] is True


def test_query_assistant_resolves_qwen35_text_model(monkeypatch: pytest.MonkeyPatch):
    """
    Resolve the checkpoint template when the loaded model exposes its text sub-config.
    """
    assistant = _query_assistant(monkeypatch, FrozenModel.QWEN3_5_4B)
    assert assistant.model.config.model_type == 'qwen3_5_text'
    message = parse_response(
        '<tool_call><function=search_disaster_events>'
        '<parameter=country_code>CRI</parameter></function></tool_call>',
        assistant.tokenizer.response_template,
        prefix='',
    )
    assert message['tool_calls'][0]['function'] == {
        'name': 'search_disaster_events', 'arguments': {'country_code': 'CRI'},
    }

def test_query_assistant_returns_an_answer_without_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
    ):
    """
    Ensures a direct model answer needs no tool execution.
    """
    assistant = _query_assistant(monkeypatch)
    execute = Mock()
    monkeypatch.setattr(assistant.tools, 'execute', execute)
    assistant._generate = Mock(return_value=_parsed('A direct answer.'))

    result = assistant.ask('What happened?')

    assert result == {'answer': 'A direct answer.', 'tool_results': []}
    execute.assert_not_called()


@pytest.mark.parametrize('call_format', ['json', 'xml'])
def test_query_assistant_executes_tool_calls_before_answering(
    monkeypatch: pytest.MonkeyPatch,
    call_format: str,
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
    json_calls = (
        '<tool_call>{"name": "search_disaster_events", '
        '"arguments": {"country_code": "JPN"}}</tool_call>'
        '<tool_call>{"name": "get_disaster_context", '
        '"arguments": {"event_id": "event-1"}}</tool_call>'
    )
    xml_calls = (
        '<tool_call><function=search_disaster_events>'
        '<parameter=country_code>JPN</parameter></function></tool_call>'
        '<tool_call><function=get_disaster_context>'
        '<parameter=event_id>event-1</parameter></function></tool_call>'
    )
    assistant._generate = Mock(side_effect=[
        _parsed(json_calls) if call_format == 'json' else _parsed(xml_calls, FrozenModel.QWEN3_5_4B),
        _parsed('The graph returned one matching event.'),
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
    messages = assistant._generate.call_args.args[0]
    assert [message['name'] for message in messages if message['role'] == 'tool'] == [
        'search_disaster_events', 'get_disaster_context',
    ]

def test_query_assistant_executes_saved_news_tool(
    monkeypatch: pytest.MonkeyPatch,
):
    assistant = _query_assistant(monkeypatch)
    saved_news = {
        'status': 'ok',
        'rows': [{'event_id': 'event-1', 'title': 'Flood update'}],
    }
    execute = Mock(return_value=saved_news)
    monkeypatch.setattr(assistant.tools, 'execute', execute)
    assistant._generate = Mock(side_effect=[
        _parsed('<tool_call>{"name": "get_event_news", '
                '"arguments": {"event_id": "event-1"}}</tool_call>'),
        _parsed('One saved article candidate was found.'),
    ])

    result = assistant.ask('Show saved news for event-1.')

    execute.assert_called_once_with('get_event_news', {'event_id': 'event-1'})
    assert result['tool_results'][0]['result'] == saved_news
    assert result['answer'] == 'One saved article candidate was found.'


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
    assistant._generate = Mock(return_value=_parsed(response))

    with pytest.raises(ValueError):
        assistant.ask('What happened?')


@pytest.mark.parametrize('requests_another_tool', [False, True])
def test_query_assistant_stops_tools_at_limit(
    monkeypatch: pytest.MonkeyPatch,
    requests_another_tool: bool,
    ):
    """
    Allow one final answer after the budget, failing if it requests another tool.
    """
    assistant = _query_assistant(monkeypatch)
    assistant.tools.max_tool_calls = 1
    execute = Mock(return_value={'status': 'ok', 'rows': []})
    monkeypatch.setattr(assistant.tools, 'execute', execute)
    request = (
        '<tool_call>{"name": "search_disaster_events", '
        '"arguments": {"country_code": "CRI"}}</tool_call>'
    )
    assistant._generate = Mock(side_effect=[
        _parsed(request), _parsed(request if requests_another_tool else 'Insufficient data.'),
    ])

    if requests_another_tool:
        with pytest.raises(ValueError, match='after the tool call limit'):
            assistant.ask('Find floods in Costa Rica.')
    else:
        assert assistant.ask('Find floods in Costa Rica.')['answer'] == 'Insufficient data.'

    assert execute.call_count == 1
    assert assistant._generate.call_count == 2
    assert assistant._generate.call_args.kwargs['use_tools'] is False
    first_messages = assistant._generate.call_args_list[0].args[0]
    final_messages = assistant._generate.call_args_list[1].args[0]
    assert 'budget is exhausted' not in first_messages[0]['content']
    assert 'budget is exhausted' in final_messages[0]['content']
    assert [message['role'] for message in first_messages] == ['system', 'user']


def test_query_generation_uses_transformers_parser(monkeypatch: pytest.MonkeyPatch):
    """
    Preserve output tokens and prompt context for the shared parser.
    """
    assistant = _query_assistant(monkeypatch)
    assistant.device = 'cpu'
    inputs = {'input_ids': torch.tensor([[1]])}
    assistant.tokenizer.apply_chat_template.return_value.to.return_value = inputs
    assistant.model.generate.return_value = torch.tensor([[1, 2]])
    assistant.tokenizer.decode.return_value = '3000<|im_end|>'
    assistant.tokenizer.parse_response.side_effect = lambda text, **kwargs: parse_response(
        text, assistant.tokenizer.response_template, prefix='', tools=kwargs['tools'],
    )

    assert assistant._generate([], use_tools=False)['content'] == '3000'
    assert assistant.tokenizer.decode.call_args.kwargs['skip_special_tokens'] is False
    assert assistant.tokenizer.parse_response.call_args.kwargs['prefix'].tolist() == [1]
    assert assistant.tokenizer.parse_response.call_args.kwargs['tools'] is None


def test_bedrock_assistant_returns_answer(monkeypatch: pytest.MonkeyPatch):
    """
    Return an answer without executing tools.
    """
    assistant, converse = _bedrock_assistant(monkeypatch)
    converse.return_value = _bedrock_response([{'text': 'An answer.'}])
    execute = Mock()
    monkeypatch.setattr(assistant.tools, 'execute', execute)

    assert assistant.ask('Question') == {'answer': 'An answer.', 'tool_results': []}
    execute.assert_not_called()


@pytest.mark.parametrize('requests_tool', [False, True])
def test_bedrock_assistant_no_tools(monkeypatch: pytest.MonkeyPatch, requests_tool: bool):
    """
    Omit tool definitions and reject tool requests when tools are disabled.
    """
    assistant, converse = _bedrock_assistant(monkeypatch)
    if requests_tool:
        converse.return_value = _bedrock_response([{'toolUse': {
            'toolUseId': 'search-1', 'name': 'search_disaster_events', 'input': {},
        }}], 'tool_use')
    else:
        converse.return_value = _bedrock_response([{'text': 'An answer.'}])
    execute = Mock()
    monkeypatch.setattr(assistant.tools, 'execute', execute)

    if requests_tool:
        with pytest.raises(ValueError, match='tool call limit'):
            assistant.ask('Question', use_tools=False)
    else:
        assert assistant.ask('Question', use_tools=False) == {'answer': 'An answer.', 'tool_results': []}
    execute.assert_not_called()
    assert 'toolConfig' not in converse.call_args.kwargs
    prompt = converse.call_args.kwargs['system'][0]['text']
    assert 'own knowledge' in prompt and 'tool' not in prompt.lower()

    converse.return_value = _bedrock_response([{'text': 'Next answer.'}])
    assistant.ask('Next question')
    assert 'toolConfig' in converse.call_args.kwargs


def test_bedrock_assistant_executes_tool_call(monkeypatch: pytest.MonkeyPatch):
    """
    Execute a tool call and send its result back with the same ID.
    """
    assistant, converse = _bedrock_assistant(monkeypatch)
    converse.side_effect = [
        _bedrock_response([{'toolUse': {
            'toolUseId': 'search-1', 'name': 'search_disaster_events',
            'input': {'country_code': 'FRA'},
        }}], 'tool_use'),
        _bedrock_response([{'text': 'Found an event.'}]),
    ]
    rows = {'status': 'ok', 'rows': [{'event_id': 'event-1'}]}
    execute = Mock(return_value=rows)
    monkeypatch.setattr(assistant.tools, 'execute', execute)

    result = assistant.ask('Find disasters in France.')

    assert result['answer'] == 'Found an event.'
    assert result['tool_results'][0]['result'] == rows
    execute.assert_called_once_with('search_disaster_events', {'country_code': 'FRA'})
    returned = converse.call_args.kwargs['messages'][-1]['content'][0]['toolResult']
    assert returned['toolUseId'] == 'search-1'
    assert json.loads(returned['content'][0]['text']) == rows


def test_bedrock_assistant_limits_calls(monkeypatch: pytest.MonkeyPatch):
    """
    Skip calls beyond the budget and return an error for each skipped call.
    """
    assistant, converse = _bedrock_assistant(monkeypatch)
    assistant.tools.max_tool_calls = 1
    converse.side_effect = [
        _bedrock_response([
            {'toolUse': {'toolUseId': name, 'name': 'get_disaster_context', 'input': {}}}
            for name in ['first', 'second']
        ], 'tool_use'),
        _bedrock_response([{'text': 'Answer'}]),
    ]
    execute = Mock(return_value={'status': 'ok', 'rows': []})
    monkeypatch.setattr(assistant.tools, 'execute', execute)

    result = assistant.ask('Question')

    execute.assert_called_once()
    assert len(result['tool_results']) == 1
    returned = converse.call_args.kwargs['messages'][-1]['content']
    assert [block['toolResult']['toolUseId'] for block in returned] == ['first', 'second']
    assert returned[1]['toolResult']['status'] == 'error'
