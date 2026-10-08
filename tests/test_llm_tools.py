"""
Tests for LLM tools.
"""

# Imports

from datetime import date, datetime, time
from typing import Any, cast, get_args
from unittest.mock import Mock, ANY
from neo4j.time import Date as Neo4jDate, DateTime as Neo4jDateTime

import pytest

from monty_tool.llm import tools


# Tests

@pytest.mark.parametrize('metric', ['elevation', 'mean_temperature', 'precipitation_total'])
def test_disaster_search_rejects_inverted_weather_ranges(metric):
    """
    Reject contradictory lower and upper bounds before executing a graph query.
    """
    with pytest.raises(ValueError, match=f'min_{metric}'):
        tools.DisasterEventSearchArguments.model_validate({f'min_{metric}': 10, f'max_{metric}': 5})


@pytest.mark.parametrize('bounds', [
    {'max_elevation': float('inf')}, {'min_mean_temperature': float('nan')},
    {'min_precipitation_total': -1},
])
def test_disaster_search_rejects_invalid_weather_bounds(bounds):
    """
    Require finite weather thresholds and nonnegative precipitation.
    """
    with pytest.raises(ValueError):
        tools.DisasterEventSearchArguments.model_validate(bounds)

def test_load_cypher_query_completes():
    """
    Checks that importlib can be used to load the package with Cypher query templates.
    """
    for template_file in  get_args(tools.CypherTemplateFile):
        test = tools._load_cypher_query(name=template_file)
        assert len(test) > 1


@pytest.mark.parametrize('country_code', ['', 'US'])
def test_graph_search_arguments_validates_country_code(country_code: str):
    """
    Ensures that graph search arguments must provide a three-letter country code.
    """
    with pytest.raises(ValueError, match='3'):
        tools.GraphSearchArguments(country_code=country_code)


def test_graph_search_validates_country_lookup():
    """
    Request an ISO country code when the lookup fails.
    """
    with pytest.raises(ValueError, match='Supply a three-letter ISO country code'):
        tools.GraphSearchArguments(country_code='Unknown country')


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


@pytest.mark.parametrize('arguments', [
    {},
    {'from_date': '2026-09-20', 'to_date': '2026-09-20'},
    {'from_date': '2026-09-19', 'to_date': '2026-09-20'},
])
def test_graph_search_accepts_complete_or_absent_date_ranges(arguments: dict[str, Any]):
    """
    Allow undated searches, exact days, and inclusive date ranges.
    """
    tools.GraphSearchArguments.model_validate(arguments)


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        ('flood', 'flood'),
        (date(2026, 9, 27), '2026-09-27'),
        (Neo4jDate(2026, 9, 27), '2026-09-27'),
        (Neo4jDateTime(2026, 9, 14, 10, 0), '2026-09-14T10:00:00.000000000'),
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


def test_query_tools_has_definitions():
    """
    Checks that `QueryTools` has a full set of definitions when initialized.
    This also does some other checks for definition contents to catch obvious issues.
    """
    query_tools = tools.QueryTools()
    for definition in query_tools.definitions:
        assert definition['type'] == 'function'
        function = cast(dict, definition['function'])
        expected_keys = {'name', 'description', 'parameters'}
        assert expected_keys.issubset(function)
        assert len(function['name'].strip()) >= 1
        parameters = cast(dict, function['parameters'])
        assert len(parameters) >= 1
        assert 'properties' in parameters
        assert 'additionalProperties' in parameters
        assert parameters['additionalProperties'] == False


@pytest.mark.parametrize(
    ('name', 'arguments', 'query_file'),
    [
        ('search_disaster_events', {'country_code': 'JPN'}, 'search_disaster_events.cypher'),
        ('get_disaster_context', {'event_id': 'event-1'}, 'get_disaster_context.cypher'),
        (
            'find_related_disaster_events',
            {'event_id': 'event-1', 'relation_kind': 'same_country'},
            'find_related_disaster_events.cypher',
        ),
        ('search_response_events', {'country_code': 'JPN'}, 'search_response_events.cypher'),
        ('search_appeals', {'text': 'Malawi - Food Insecurity'}, 'search_appeals.cypher'),
        ('get_response_context', {'event_id': 'event-1'}, 'get_response_context.cypher'),
        ('get_event_news', {'event_id': 'event-1'}, 'get_event_news.cypher'),
    ],
)
def test_query_tools_routes_graph_tools(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    arguments: dict[str, Any],
    query_file: str,
    ):
    """
    Ensures each graph tool selects its corresponding Cypher query.
    """
    query_tools = tools.QueryTools()
    run_query = Mock(return_value={'status': 'ok', 'rows': []})
    monkeypatch.setattr(query_tools, '_run_graph_query', run_query)

    assert query_tools.execute(name, arguments) == {'status': 'ok', 'rows': []}
    assert run_query.call_args.args[0] == arguments
    assert run_query.call_args.args[2] == query_file


@pytest.mark.parametrize(
    ('name', 'arguments'),
    [
        ( 'search_disaster_events', {'country_code': 'JP'}),
        ('search_disaster_events', {'from_date': '2026-09-20'}),
        ('search_disaster_events', {'to_date': '2026-09-20'}),
        ('get_disaster_context', {}),
        ('find_related_disaster_events', {'event_id': 'event-1', 'relation_kind': 'nonexistent'}),
        ('search_response_events', {'from_date': '2026-09-20', 'to_date': '2026-09-19'}),
        ('search_response_events', {'from_date': '2026-09-20'}),
        ('search_response_events', {'to_date': '2026-09-20'}),
        ('search_appeals', {'from_date': '2015-09-17'}),
        ('search_appeals', {'to_date': '2015-09-17'}),
        ('search_appeals', {'from_date': '2015-09-18', 'to_date': '2015-09-17'}),
        ('search_appeals', {'event_id': 'event-1'}),
        ('get_response_context', {'event_id': ''}),
        ('get_event_news', {}),
    ],
)
def test_query_tools_reject_invalid_graph_tool_arguments(
    name: str,
    arguments: dict[str, Any],
    ):
    """
    Checks that graph query tools return error messages given invalid inputs.
    """
    result = tools.QueryTools().execute(name, arguments)
    assert result == {
        'status': 'error',
        'message': ANY,
    }


def test_search_appeals_queries_launch_dates_and_returns_details(monkeypatch: pytest.MonkeyPatch):
    """
    Checks that appeal searches pass launch dates and return funding and beneficiary details.
    """
    row = {
        'appeal_id': 'go-appeal-2261', 'title': 'Malawi - Food Insecurity',
        'start_datetime': datetime(2015, 9, 17), 'amount_funded': 873154.34,
        'beneficiaries': 1000, 'event_id': None,
    }
    record = Mock()
    record.data.return_value = row
    driver = Mock()
    driver.execute_query.return_value = ([record], None, None)
    driver_context = Mock()
    driver_context.__enter__ = Mock(return_value=driver)
    driver_context.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(tools, 'get_graph_db_driver', lambda: driver_context)

    result = tools.QueryTools().execute('search_appeals', {
        'text': 'Malawi - Food Insecurity', 'country_code': 'MWI',
        'from_date': '2015-09-17', 'to_date': '2015-09-17',
    })

    assert result == {
        'status': 'ok',
        'rows': [{**row, 'start_datetime': '2015-09-17T00:00:00'}],
    }
    driver.execute_query.assert_called_once()
    parameters = driver.execute_query.call_args.kwargs['parameters_']
    assert parameters['from_date'] == parameters['to_date'] == date(2015, 9, 17)
    assert parameters['text'] == 'Malawi - Food Insecurity'
    assert parameters['country_code'] == 'MWI'
