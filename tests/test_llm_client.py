"""
Tests for the query assistant clients.
"""

# Imports

import json
from typing import Any
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


def _tool_request(name: str, **arguments: Any) -> dict[str, Any]:
    """
    Build a parsed local-model tool request.
    """
    return {'tool_calls': [{'function': {'name': name, 'arguments': arguments}}]}


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
    monkeypatch.setattr(client.QueryTools, 'execute', execute)
    response = (
        _tool_request('search_disaster_events')
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
    assert generate.call_args.kwargs['tool_definitions'] is None


def test_query_assistant_returns_an_answer_without_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
    ):
    """
    Ensures a direct model answer needs no tool execution.
    """
    assistant = _query_assistant(monkeypatch)
    execute = Mock()
    monkeypatch.setattr(client.QueryTools, 'execute', execute)
    assistant._generate = Mock(return_value={'content': 'A direct answer.'})

    result = assistant.ask('What happened?')

    assert result == {'answer': 'A direct answer.', 'tool_results': []}
    execute.assert_not_called()


def test_query_assistant_executes_tools_before_answering(monkeypatch):
    """
    Execute a batch of tools and pass each result back before answering.
    """
    assistant = _query_assistant(monkeypatch)
    calls = [
        {'name': 'search_disaster_events', 'arguments': {'country_code': 'JPN'}},
        {'name': 'get_event_news', 'arguments': {'event_id': 'event-1'}},
    ]
    rows = {'status': 'ok', 'rows': [{'event_id': 'event-1'}]}
    execute = Mock(return_value=rows)
    monkeypatch.setattr(client.QueryTools, 'execute', execute)
    generate = Mock(side_effect=[
        {'tool_calls': [{'function': request} for request in calls]}, {'content': 'Answer'},
    ])
    assistant._generate = generate

    result = assistant.ask('Question')

    assert execute.call_args_list == [call(request['name'], request['arguments']) for request in calls]
    assert result == {
        'answer': 'Answer',
        'tool_results': [{'call': request, 'result': rows} for request in calls],
    }
    tool_messages = generate.call_args.args[0][-2:]
    assert [message['name'] for message in tool_messages] == [request['name'] for request in calls]
    assert [json.loads(message['content']) for message in tool_messages] == [rows, rows]


@pytest.mark.parametrize(
    'response',
    [
        {'content': ''},
        {'tool_calls': [{'function': {'name': 'search_disaster_events'}}]},
    ],
)
def test_query_assistant_rejects_empty_or_malformed_model_responses(
    monkeypatch: pytest.MonkeyPatch,
    response: dict[str, Any],
    ):
    """
    Ensures the assistant does not accept empty or malformed model output.
    """
    assistant = _query_assistant(monkeypatch)
    assistant._generate = Mock(return_value=response)

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
    assistant.max_tool_calls = 1
    execute = Mock(return_value={'status': 'ok', 'rows': []})
    monkeypatch.setattr(client.QueryTools, 'execute', execute)
    request = _tool_request('search_disaster_events', country_code='CRI')
    assistant._generate = Mock(side_effect=[
        request, request if requests_another_tool else {'content': 'Insufficient data.'},
    ])

    if requests_another_tool:
        with pytest.raises(ValueError, match='after the tool call limit'):
            assistant.ask('Find floods in Costa Rica.')
    else:
        assert assistant.ask('Find floods in Costa Rica.')['answer'] == 'Insufficient data.'

    assert execute.call_count == 1
    assert assistant._generate.call_count == 2
    assert assistant._generate.call_args.kwargs['tool_definitions'] is None


@pytest.mark.parametrize('model_id, raw_response, expected', [
    (FrozenModel.QWEN3_1_7B, '3000<|im_end|>', {'content': '3000'}),
    (FrozenModel.QWEN3_1_7B,
     '<tool_call>{"name": "search_disaster_events", "arguments": {"country_code": "CRI"}}</tool_call>',
     _tool_request('search_disaster_events', country_code='CRI')),
    (FrozenModel.QWEN3_5_4B,
     '<tool_call><function=search_disaster_events><parameter=country_code>CRI</parameter></function></tool_call>',
     _tool_request('search_disaster_events', country_code='CRI')),
])
def test_query_generation_uses_transformers_parser(monkeypatch, model_id, raw_response, expected):
    """
    Parse answer text and JSON/XML tool calls using the checkpoint template.
    """
    assistant = _query_assistant(monkeypatch, model_id)
    assistant.device = 'cpu'
    inputs = {'input_ids': torch.tensor([[1]])}
    assistant.tokenizer.apply_chat_template.return_value.to.return_value = inputs
    assistant.model.generate.return_value = torch.tensor([[1, 2]])
    assistant.tokenizer.decode.return_value = raw_response
    assistant.tokenizer.parse_response.side_effect = lambda text, **kwargs: parse_response(
        text, assistant.tokenizer.response_template, prefix='', tools=kwargs['tools'],
    )

    result = assistant._generate([])
    if 'tool_calls' in expected:
        assert result['tool_calls'][0]['function'] == expected['tool_calls'][0]['function']
    else:
        assert result['content'] == expected['content']
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
    monkeypatch.setattr(client.QueryTools, 'execute', execute)

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
    monkeypatch.setattr(client.QueryTools, 'execute', execute)

    if requests_tool:
        with pytest.raises(ValueError, match='tool call limit'):
            assistant.ask('Question', use_tools=False)
    else:
        assert assistant.ask('Question', use_tools=False) == {'answer': 'An answer.', 'tool_results': []}
    execute.assert_not_called()
    assert 'toolConfig' not in converse.call_args.kwargs


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
    monkeypatch.setattr(client.QueryTools, 'execute', execute)

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
    assistant.max_tool_calls = 1
    converse.side_effect = [
        _bedrock_response([
            {'toolUse': {'toolUseId': name, 'name': 'get_disaster_context', 'input': {}}}
            for name in ['first', 'second']
        ], 'tool_use'),
        _bedrock_response([{'text': 'Answer'}]),
    ]
    execute = Mock(return_value={'status': 'ok', 'rows': []})
    monkeypatch.setattr(client.QueryTools, 'execute', execute)

    result = assistant.ask('Question')

    execute.assert_called_once()
    assert len(result['tool_results']) == 1
    returned = converse.call_args.kwargs['messages'][-1]['content']
    assert [block['toolResult']['toolUseId'] for block in returned] == ['first', 'second']
    assert returned[1]['toolResult']['status'] == 'error'
    assert 'toolConfig' not in converse.call_args.kwargs


@pytest.mark.parametrize('backend', ['local', 'bedrock'])
def test_each_question_uses_its_own_tool_configuration(monkeypatch, backend):
    """
    Build tools for each question and pass that question's definitions to generation.
    """
    if backend == 'local':
        assistant = _query_assistant(monkeypatch)
        response = {'content': 'Answer'}
    else:
        assistant, _ = _bedrock_assistant(monkeypatch)
        response = _bedrock_response([{'text': 'Answer'}])
    generate = Mock(return_value=response)
    monkeypatch.setattr(assistant, '_generate', generate)
    factory = Mock(wraps=client.QueryTools)
    monkeypatch.setattr(client, 'QueryTools', factory)
    assistant.max_tool_calls = 3

    assistant.ask('Core question', enable_external=False)
    core_definitions = generate.call_args.kwargs['tool_definitions']
    assistant.ask('External question')
    external_definitions = generate.call_args.kwargs['tool_definitions']

    assert factory.call_args_list == [
        call(max_tool_calls=3, enable_external=False), call(max_tool_calls=3, enable_external=True),
    ]
    assert 'get_event_news' not in {item['function']['name'] for item in core_definitions}
    assert 'get_event_news' in {item['function']['name'] for item in external_definitions}
