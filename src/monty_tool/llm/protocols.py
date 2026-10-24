"""
Query assistant typing protocols.
"""

# Imports

from typing import Protocol

from monty_tool.llm.schemas import QueryAssistantResponse
from monty_tool.llm.tools import QueryTools


# Assistant protocol

class QueryAssistantProtocol(Protocol):
    """
    A query assistant for disaster records.
    """
    tools: QueryTools

    def ask(self, question: str) -> QueryAssistantResponse:
        """
        Answer a question and return the tool results.
        """
        ...
