"""
Tests for LLM tools.
"""

# Imports

from datetime import date
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


@pytest.mark.parametrize('country_code', ['', 'US', 'China'])
def test_graph_search_arguments_validates_country_code(country_code: str):
    """
    Ensures that graph search arguments must provide a three-letter country code.
    """
    with pytest.raises(ValueError, match='3'):
        tools.GraphSearchArguments(country_code=country_code)


def test_graph_search_arguments_validates_dates():
    """
    Ensures that graph search arguments must place the starting date before the ending date
    if both are provided.
    """
    with pytest.raises(ValueError, match='from_date'):
        tools.GraphSearchArguments(
            from_date=date(2026, 9, 20),
            to_date=date(2026, 9, 19),
        )
