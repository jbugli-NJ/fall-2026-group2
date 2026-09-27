"""
Tests for LLM tools.
"""

# Imports

from datetime import date, datetime, time
from typing import Any, get_args

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


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        ('flood', 'flood'),
        (date(2026, 9, 27), '2026-09-27'),
        (
            datetime(2026, 9, 27, 14, 30, 15),
            '2026-09-27T14:30:15',
        ),
        (time(14, 30, 15), '14:30:15'),
        (
            {
                'occurred_on': date(2026, 9, 27),
                'details': [
                    ('flood', datetime(2026, 9, 27, 14, 30)),
                    list(range(101)),
                ],
            },
            {
                'occurred_on': '2026-09-27',
                'details': [['flood', '2026-09-27T14:30:00']],
            },
        ),
        (list(range(101)), tools._OMIT_VALUE),
    ],
)
def test_json_value_serializes_and_omits_large_values(value: Any, expected: Any):
    """
    Checks JSON serialization for various Neo4j values to ensure compatibility and that large lists
    (representing embeddings) are omitted.
    """
    result = tools._json_value(value)

    if expected is tools._OMIT_VALUE:
        assert result is tools._OMIT_VALUE
    else:
        assert result == expected
