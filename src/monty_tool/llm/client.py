"""
A local query assistant using Qwen tool calls.
"""

from __future__ import annotations

import json
from typing import Any

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from transformers.cli.serving.utils import get_response_template

from monty_tool.llm.schemas import QueryAssistantResponse
from monty_tool.llm.tools import QueryTools
from monty_tool.utils.versions import FrozenModel


class QueryAssistant:
    """
    Answer questions by querying the local graph and NewsAPI.
    """

    _CONTEXT_LIMIT = 32_768
    def __init__(
        self,
        model_id: FrozenModel = FrozenModel.QWEN3_5_4B,
        *,
        enable_external: bool = True,
        ):
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
                        "Distinguish disaster event facts from news article candidates. "
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
