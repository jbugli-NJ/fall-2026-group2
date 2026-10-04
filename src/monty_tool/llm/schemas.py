"""
Schemas for assistant responses.
"""

# Imports

from typing import Any, TypedDict


# Schemas

class QueryAssistantResponse(TypedDict):
    """
    A query assistant's final answer and tool calls.
    """
    answer: str
    tool_results: list[dict[str, Any]]
