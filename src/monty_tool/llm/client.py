"""
Query assistants using local models or Bedrock.
"""

from __future__ import annotations

import json
import os
from typing import Any, cast

import boto3

from monty_tool.boto3_utils.llm_protocols import (
    BedrockRuntimeClient,
    ContentBlock,
    ConverseMessage,
    ConverseRequest,
    ConverseResponse,
    SystemContentBlock,
    ToolConfiguration,
)
from monty_tool.llm.schemas import QueryAssistantResponse
from monty_tool.llm.tools import QueryTools
from monty_tool.utils.versions import FrozenModel


class QueryAssistant:
    """
    Answer questions by querying the local graph and saved news.
    """

    _CONTEXT_LIMIT = 32_768
    def __init__(
        self,
        model_id: FrozenModel = FrozenModel.QWEN3_5_4B,
        *,
        enable_external: bool = True,
        ):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from transformers.cli.serving.utils import get_response_template

        self.tools = QueryTools(enable_external=enable_external)
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
        use_tools: bool = True,
        ) -> dict[str, Any]:
        """
        Generate one response while enforcing the context limit.
        """
        import torch

        inputs = self.tokenizer.apply_chat_template(
            messages,
            tools=self.tools.definitions if use_tools else None,
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
            tools=self.tools.definitions if use_tools else None,
        )

    def ask(self, question: str) -> QueryAssistantResponse:
        """
        Answer one question with up to ten tool calls.
        This exposes graph query tools.
        """
        self.tools.tool_call_count = 0
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "You answer questions about disaster records. Use graph search "
                    "tools to find events, then use returned event IDs for details "
                    "or related records. Use response tools for IFRC events and "
                    "appeals. "
                    + (
                        "get_disaster_context returns disaster info, including weather data. "
                        "Use get_event_news only when the question requires information "
                        "from news articles. "
                        "Distinguish disaster-record facts from news coverage; treat CONTEXT_FOR articles as background, not direct evidence of the event. "
                        if self.tools.enable_external else ""
                    )
                    + "Base your answer on tool results. Answer in the user's language."
                ),
            },
            {"role": "user", "content": question},
        ]
        tool_results: list[dict[str, Any]] = []

        while True:
            remaining_calls = self.tools.max_tool_calls - len(tool_results)
            use_tools = remaining_calls > 0
            if not use_tools:
                messages[0]['content'] += (
                    ' The tool call budget is exhausted. '
                    'Provide your final answer using the results already collected.'
                )
            response = self._generate(messages, use_tools=use_tools)
            calls = [tool_call['function'] for tool_call in response.get('tool_calls', [])]
            if any(
                not isinstance(call.get('name'), str)
                or not isinstance(call.get('arguments'), dict)
                for call in calls
            ):
                raise ValueError("Tool call must contain a name and an arguments dictionary.")

            if calls and not use_tools:
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
                result = self.tools.execute(call["name"], call["arguments"])
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
        model_id: str = 'us.anthropic.claude-sonnet-4-5-20250929-v1:0',
        *,
        region: str | None = None,
        enable_external: bool = True,
        ):
        self.tools = QueryTools(max_tool_calls=25, enable_external=enable_external)
        self.model_id = model_id
        region = (
            region
            or os.environ.get('AWS_REGION')
            or os.environ.get('AWS_DEFAULT_REGION')
            or 'us-east-1'
        )
        self.client = cast(
            BedrockRuntimeClient,
            boto3.client('bedrock-runtime', region_name=region),
        )
        self.tool_config: ToolConfiguration = {
            'tools': [
                {'toolSpec': {
                    'name': definition['function']['name'],
                    'description': definition['function']['description'],
                    'inputSchema': {'json': definition['function']['parameters']},
                }}
                for definition in self.tools.definitions
            ],
        }

    def _generate(
        self,
        messages: list[ConverseMessage],
        system: list[SystemContentBlock],
        max_new_tokens: int = 1024,
        ) -> ConverseResponse:
        """
        Generate one response through Bedrock Converse.
        """
        request: ConverseRequest = {
            'modelId': self.model_id,
            'messages': messages,
            'system': system,
            'inferenceConfig': {'maxTokens': max_new_tokens, 'temperature': 0.0},
            'toolConfig': self.tool_config,
        }
        return self.client.converse(**request)

    def ask(self, question: str) -> QueryAssistantResponse:
        """
        Answer one question with up to 25 tool calls.
        """
        self.tools.tool_call_count = 0
        system: list[SystemContentBlock] = [{'text': (
            "You answer questions about disaster records. Use graph search "
            "tools to find events, then use returned event IDs for details "
            "or related records. Use response tools for IFRC events and "
            "appeals. "
            + (
                "get_disaster_context returns disaster info, including weather data. "
                "Use get_event_news only when the question requires information "
                "from news articles. "
                "Distinguish disaster-record facts from news coverage; treat CONTEXT_FOR articles as background, not direct evidence of the event. "
                if self.tools.enable_external else ""
            )
            + "Base your answer on tool results. Answer in the user's language."
        )}]
        messages: list[ConverseMessage] = [
            {'role': 'user', 'content': [{'text': question}]},
        ]
        tool_results: list[dict[str, Any]] = []

        while True:
            remaining_calls = self.tools.max_tool_calls - len(tool_results)
            use_tools = remaining_calls > 0
            if not use_tools:
                system[0]['text'] += (
                    ' The tool call budget is exhausted. '
                    'Provide your final answer using the results already collected.'
                )
            response = self._generate(messages, system)
            stop_reason = response['stopReason']
            if stop_reason not in ('end_turn', 'stop_sequence', 'tool_use'):
                raise ValueError(f'Model response stopped with {stop_reason}.')

            message = response['output']['message']
            calls = [block['toolUse'] for block in message['content'] if 'toolUse' in block]
            if bool(calls) != (stop_reason == 'tool_use'):
                raise ValueError('Tool calls do not match the model stop reason.')
            if calls and not use_tools:
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
                if len(tool_results) >= self.tools.max_tool_calls:
                    result = {
                        'status': 'limit_reached',
                        'message': 'The tool call limit has been reached.',
                    }
                else:
                    print('Tool call:', json.dumps(call, ensure_ascii=False, default=str))
                    result = self.tools.execute(call['name'], call['arguments'])
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
