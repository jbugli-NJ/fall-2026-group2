"""
Classify prompts using token probabilities over explicit labels.
"""

from typing import Any

from monty_tool.llm.client import QueryAssistant


def classify_labels(
    assistant: QueryAssistant,
    messages: list[dict[str, Any]],
    labels: tuple[str, ...],
) -> str:
    """
    Return the highest-probability label following the provided input messages.
    The labels should be requested within the prompt.
    """
    import torch

    if not labels or len(set(labels)) != len(labels):
        raise ValueError('Classification labels must be nonempty and unique.')
    choice_ids: list[int] = []
    for choice in labels:
        token_ids = assistant.tokenizer.encode(choice, add_special_tokens=False)
        if len(token_ids) != 1:
            raise ValueError(f"Choice {choice!r} must encode to exactly one token.")
        choice_ids.append(token_ids[0])

    inputs = assistant.tokenizer.apply_chat_template(
        messages,
        tokenize=True,
        add_generation_prompt=True,
        enable_thinking=False,
        return_dict=True,
        return_tensors="pt",
    ).to(assistant.device)
    prompt_length = inputs["input_ids"].shape[-1]

    if prompt_length > assistant._CONTEXT_LIMIT:
        raise ValueError(
            "Conversation exceeds the context limit (32,768 tokens)."
        )

    with torch.inference_mode():
        output = assistant.model(**inputs, logits_to_keep=1, use_cache=False)
        choice_logits = output.logits[0, -1, choice_ids]
        if not torch.isfinite(choice_logits).all().item():
            # NOTE: Catches potential errors/bugs
            raise ValueError("Model returned non-finite choice logits.")
        best_choice_index = int(choice_logits.argmax().item())

    return labels[best_choice_index]
