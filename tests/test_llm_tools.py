"""
Tests for LLM tools.
"""

# Imports

from typing import get_args

import pytest

from monty_tool.llm import tools


# Tests

def test_load_cypher_query_completes():
    """
    Checks that importlib can be used to load the package with Cypher query templates.
    """
    for template_file in  get_args(tools.CypherTemplateFile):
        test = tools._load_cypher_query(name=template_file)
        assert len(test) > 1
