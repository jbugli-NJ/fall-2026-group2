"""
Query assistant typing protocols.
"""

# Imports

from typing import Protocol

from monty_tool.llm.schemas import QueryAssistantResponse


# Assistant protocol

class QueryAssistantProtocol(Protocol):
    """
    A query assistant for disaster records.
    """
    max_tool_calls: int

    def ask(
        self, question: str, *, use_tools: bool = True, enable_external: bool = True,
    ) -> QueryAssistantResponse:
        """
        Answer a question and return the tool results.
        """
        ...
