"""
Query assistants using local models or Bedrock.
"""

from __future__ import annotations

import json
import os
from typing import Any, cast

import boto3
from botocore.config import Config

from monty_tool.boto3_utils.llm_protocols import (
    BedrockRuntimeClient,
    ContentBlock,
    ConverseMessage,
    ConverseRequest,
    ConverseResponse,
    SystemContentBlock,
)
from monty_tool.llm.schemas import QueryAssistantResponse
from monty_tool.llm.tools import QueryTools
from monty_tool.utils.versions import FrozenModel


DEFAULT_BEDROCK_MODEL_ID = 'us.anthropic.claude-sonnet-4-5-20250929-v1:0'


def _system_prompt(use_tools: bool, enable_external: bool) -> str:
    """
    Describe the evidence available for the current question.
    """
    if not use_tools:
        return (
            "You answer questions about disaster records using your own knowledge. "
            "Answer in the user's language."
        )
    return (
        "You answer questions about disaster records. Use graph search tools to find events, "
        "then use returned event IDs for details or related records. Use response tools for "
        "IFRC events and appeals. Base factual statements on tool results and identify "
        "information that remains unavailable. Answer in the user's language. "
        + (
            "get_disaster_context includes stored weather. Use get_event_news for relevant "
            "stored articles. Distinguish Montandon records from external evidence."
            if enable_external else "The available tools expose Montandon records and impacts."
        )
    )


class QueryAssistant:
    """
    Answer questions by querying the local graph and saved news.
    """

    _CONTEXT_LIMIT = 32_768
    def __init__(
        self,
        model_id: FrozenModel = FrozenModel.QWEN3_5_4B,
        *,
        max_tool_calls: int = 10,
        ):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from transformers.cli.serving.utils import get_response_template

        self.max_tool_calls = max_tool_calls
        self.device = "cuda" if torch.cuda.is_available() else (
            "mps" if torch.backends.mps.is_available() else "cpu"
        )
        dtype = torch.bfloat16 if self.device == "cpu" else torch.float16

        self.tokenizer: Any = AutoTokenizer.from_pretrained(
            model_id.value,
            revision=model_id.revision,
        )
        self.model: Any = AutoModelForCausalLM.from_pretrained(
            model_id.value,
            revision=model_id.revision,
            dtype=dtype,
            device_map=self.device,
        )
        self.tokenizer.response_template = get_response_template(self.tokenizer, self.model)
        if self.tokenizer.response_template is None:
            raise ValueError(f'Transformers has no response template for {model_id.value}.')
        self.model.eval()

    def _generate(
        self,
        messages: list[dict[str, Any]],
        max_new_tokens: int = 1024,
        *,
        tool_definitions: list[dict[str, Any]] | None = None,
        ) -> dict[str, Any]:
        """
        Generate one response while enforcing the context limit.
        """
        import torch

        inputs = self.tokenizer.apply_chat_template(
            messages,
            tools=tool_definitions,
            tokenize=True,
            add_generation_prompt=True,
            enable_thinking=False,
            return_dict=True,
            return_tensors="pt",
        ).to(self.device)
        prompt_length = inputs["input_ids"].shape[-1]

        if prompt_length + max_new_tokens > self._CONTEXT_LIMIT:
            raise ValueError(
                "Conversation exceeds the context limit (32,768 tokens)."
            )

        with torch.inference_mode():
            output = self.model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                pad_token_id=self.tokenizer.eos_token_id,
            )

        response = self.tokenizer.decode(
            output[0][prompt_length:],
            skip_special_tokens=False,
        )
        print("Raw model response:", repr(response))
        return self.tokenizer.parse_response(
            response,
            prefix=inputs['input_ids'][0],
            tools=tool_definitions,
        )

    def ask(
        self, question: str, *, use_tools: bool = True, enable_external: bool = True,
    ) -> QueryAssistantResponse:
        """
        Answer one question with optional access to graph query tools.
        """
        tools = QueryTools(
            max_tool_calls=self.max_tool_calls, enable_external=enable_external,
        )
        system_prompt = _system_prompt(use_tools, enable_external)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ]
        tool_results: list[dict[str, Any]] = []
        tool_budget = self.max_tool_calls if use_tools else 0

        while True:
            remaining_calls = tool_budget - len(tool_results)
            tools_available = use_tools and remaining_calls > 0
            generation_messages = list(messages)
            if use_tools and not tools_available:
                generation_messages = [
                    {
                        "role": "system",
                        "content": system_prompt + (
                            ' The tool call budget is exhausted. '
                            'Provide your final answer using the results already collected.'
                        ),
                    },
                    *messages[1:],
                ]
            response = self._generate(
                generation_messages,
                tool_definitions=tools.definitions if tools_available else None,
            )
            calls = [tool_call['function'] for tool_call in response.get('tool_calls', [])]
            if any(
                not isinstance(call.get('name'), str)
                or not isinstance(call.get('arguments'), dict)
                for call in calls
            ):
                raise ValueError("Tool call must contain a name and an arguments dictionary.")

            if calls and not tools_available:
                raise ValueError("Model requested a tool after the tool call limit was reached.")

            if not calls:
                answer = response.get('content', '').strip()
                if not answer:
                    raise ValueError("Expected a final answer or a tool call.")
                return {
                    "answer": answer,
                    "tool_results": tool_results,
                }

            # Execute only the requests that fit in the remaining budget.
            calls = calls[:remaining_calls]
            results = []
            for call in calls:
                print("Tool call:", json.dumps(call, ensure_ascii=False, default=str))
                result = tools.execute(call["name"], call["arguments"])
                print("Tool result:", json.dumps(result, ensure_ascii=False, default=str))
                results.append(result)
                tool_results.append({"call": call, "result": result})

            messages.extend([
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"type": "function", "function": call}
                        for call in calls
                    ],
                },
                *(
                    {
                        "role": "tool",
                        "name": call["name"],
                        "content": json.dumps(
                            result,
                            ensure_ascii=False,
                            default=str,
                        ),
                    }
                    for call, result in zip(calls, results)
                ),
            ])


class BedrockQueryAssistant:
    """
    A Bedrock assistant for questions about disaster records.
    """

    def __init__(
        self,
        model_id: str = DEFAULT_BEDROCK_MODEL_ID,
        *,
        region: str | None = None,
        max_tool_calls: int = 25,
        ):
        self.max_tool_calls = max_tool_calls
        self.model_id = model_id
        region = (
            region
            or os.environ.get('AWS_REGION')
            or os.environ.get('AWS_DEFAULT_REGION')
            or 'us-east-1'
        )
        self.client = cast(
            BedrockRuntimeClient,
            boto3.client('bedrock-runtime', region_name=region, config=Config(read_timeout=120)),
        )

    def _generate(
        self,
        messages: list[ConverseMessage],
        system: list[SystemContentBlock],
        max_new_tokens: int = 1024,
        *,
        tool_definitions: list[dict[str, Any]] | None = None,
        ) -> ConverseResponse:
        """
        Generate one response through Bedrock Converse.
        """
        request: ConverseRequest = {
            'modelId': self.model_id,
            'messages': messages,
            'system': system,
            'inferenceConfig': {'maxTokens': max_new_tokens, 'temperature': 0.0},
        }
        if tool_definitions is not None:
            request['toolConfig'] = {
                'tools': [
                    {'toolSpec': {
                        'name': definition['function']['name'],
                        'description': definition['function']['description'],
                        'inputSchema': {'json': definition['function']['parameters']},
                    }}
                    for definition in tool_definitions
                ],
            }
        return self.client.converse(**request)

    def ask(
        self, question: str, *, use_tools: bool = True, enable_external: bool = True,
    ) -> QueryAssistantResponse:
        """
        Answer one question with optional tool access.
        """
        tools = QueryTools(
            max_tool_calls=self.max_tool_calls, enable_external=enable_external,
        )
        system_prompt = _system_prompt(use_tools, enable_external)
        system: list[SystemContentBlock] = [{'text': system_prompt}]
        messages: list[ConverseMessage] = [
            {'role': 'user', 'content': [{'text': question}]},
        ]
        tool_results: list[dict[str, Any]] = []
        tool_budget = self.max_tool_calls if use_tools else 0

        while True:
            remaining_calls = tool_budget - len(tool_results)
            tools_available = use_tools and remaining_calls > 0
            generation_system: list[SystemContentBlock] = system
            if use_tools and not tools_available:
                generation_system = [{'text': system_prompt + (
                    ' The tool call budget is exhausted. '
                    'Provide your final answer using the results already collected.'
                )}]
            response = self._generate(
                messages, generation_system,
                tool_definitions=tools.definitions if tools_available else None,
            )
            stop_reason = response['stopReason']
            if stop_reason not in ('end_turn', 'stop_sequence', 'tool_use'):
                raise ValueError(f'Model response stopped with {stop_reason}.')

            message = response['output']['message']
            calls = [block['toolUse'] for block in message['content'] if 'toolUse' in block]
            if bool(calls) != (stop_reason == 'tool_use'):
                raise ValueError('Tool calls do not match the model stop reason.')
            if calls and not tools_available:
                raise ValueError('Model requested a tool after the tool call limit was reached.')
            if any(not isinstance(call['input'], dict) for call in calls):
                raise ValueError('Tool call arguments must be a dictionary.')

            if not calls:
                answer = '\n'.join(
                    block['text'] for block in message['content'] if 'text' in block
                ).strip()
                if not answer:
                    raise ValueError('Expected a final answer or a tool call.')
                return {'answer': answer, 'tool_results': tool_results}

            results: list[ContentBlock] = []
            for tool_call in calls:
                call = {'name': tool_call['name'], 'arguments': tool_call['input']}
                if len(tool_results) >= self.max_tool_calls:
                    result = {
                        'status': 'limit_reached',
                        'message': 'The tool call limit has been reached.',
                    }
                else:
                    print('Tool call:', json.dumps(call, ensure_ascii=False, default=str))
                    result = tools.execute(call['name'], call['arguments'])
                    print('Tool result:', json.dumps(result, ensure_ascii=False, default=str))
                    tool_results.append({'call': call, 'result': result})

                # Converse requires a result for every toolUseId, including skipped calls.
                results.append({'toolResult': {
                    'toolUseId': tool_call['toolUseId'],
                    'content': [{'text': json.dumps(result, ensure_ascii=False, default=str)}],
                    'status': 'error' if result['status'] in ('error', 'limit_reached') else 'success',
                }})

            messages.extend([
                message,
                {'role': 'user', 'content': results},
            ])
