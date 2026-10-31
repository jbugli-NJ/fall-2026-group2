"""
Tests for LLM tools.
"""

# Imports

from contextlib import nullcontext
from datetime import date, datetime, time
from typing import Any, get_args
from unittest.mock import Mock
from neo4j.time import Date as Neo4jDate, DateTime as Neo4jDateTime

import pytest

from monty_tool.llm import tools


@pytest.fixture
def graph_driver(monkeypatch):
    """
    Supply an empty graph response that each test can replace.
    """
    driver = Mock()
    driver.execute_query.return_value = ([], None, None)
    monkeypatch.setattr(tools, 'get_graph_db_driver', lambda: nullcontext(driver))
    return driver


# Tests

@pytest.mark.parametrize('enable_external', [False, True])
def test_external_access_controls_tool_definitions(enable_external):
    """
    Advertise news and weather filters only when external access is enabled.
    """
    definitions = {
        item['function']['name']: item['function']
        for item in tools.QueryTools(enable_external=enable_external).definitions
    }
    assert ('get_event_news' in definitions) == enable_external
    filters = definitions['search_disaster_events']['parameters']['properties']
    assert ('min_elevation' in filters) == enable_external


@pytest.mark.parametrize('name, arguments', [
    ('get_event_news', {'event_id': 'event-1'}),
    ('search_disaster_events', {'min_elevation': 0}),
])
def test_core_only_blocks_external_calls(graph_driver, name, arguments):
    """
    Reject external requests before querying the graph.
    """
    result = tools.QueryTools(enable_external=False).execute(name, arguments)
    assert result['status'] == 'error'
    graph_driver.execute_query.assert_not_called()


@pytest.mark.parametrize('enable_external', [False, True])
def test_external_access_controls_event_and_impact_weather(graph_driver, enable_external):
    """
    Preserve core impact values while exposing weather only in external mode.
    """
    row = {'weather': {'source': 'external'}, 'impacts': [{'impact_value': 12, 'weather': {}}]}
    graph_driver.execute_query.return_value = ([Mock(data=Mock(return_value=row))], None, None)

    result = tools.QueryTools(enable_external=enable_external).execute(
        'get_disaster_context', {'event_id': 'event-1'},
    )

    assert result['rows'] == [row if enable_external else {'impacts': [{'impact_value': 12}]}]


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
    for template_file in get_args(tools.CypherTemplateFile):
        assert tools._load_cypher_query(name=template_file).strip()


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


@pytest.mark.parametrize('name', [
    'search_disaster_events', 'search_response_events', 'search_appeals',
])
@pytest.mark.parametrize(('arguments', 'from_date', 'to_date'), [
    ({}, None, None),
    ({'from_date': '2026-09-20'}, date(2026, 9, 20), None),
    ({'to_date': '2026-09-20'}, None, date(2026, 9, 20)),
    ({'from_date': '2026-09-20', 'to_date': '2026-09-20'}, date(2026, 9, 20), date(2026, 9, 20)),
    ({'from_date': '2026-09-19', 'to_date': '2026-09-20'}, date(2026, 9, 19), date(2026, 9, 20)),
])
def test_accept_optional_graph_search_date_bounds(
    graph_driver,
    name: str,
    arguments: dict[str, Any],
    from_date: date | None,
    to_date: date | None,
):
    """
    Checks that graph searches pass optional date bounds to the query.
    """
    result = tools.QueryTools().execute(name, arguments)
    assert result['status'] == 'ok'
    assert result['rows'] == []
    query = graph_driver.execute_query.call_args
    assert query.kwargs['parameters_']['from_date'] == from_date
    assert query.kwargs['parameters_']['to_date'] == to_date


@pytest.mark.parametrize('name', [
    'search_disaster_events', 'search_response_events', 'search_appeals',
])
@pytest.mark.parametrize(('arguments', 'match_count', 'expected_offset', 'next_offset'), [
    ({}, 0, 0, None),
    ({}, 10, 0, None),
    ({'offset': 10}, 11, 10, 20),
    ({'offset': 20}, 9, 20, None),
])
def test_return_paginated_graph_search_results(
    graph_driver,
    name: str,
    arguments: dict[str, Any],
    match_count: int,
    expected_offset: int,
    next_offset: int | None,
):
    """
    Checks that graph searches return at most 10 rows and the next offset when another page exists.
    """
    rows = [{'id': str(index)} for index in range(match_count)]
    records = [Mock(data=Mock(return_value=row)) for row in rows]
    graph_driver.execute_query.return_value = (records, None, None)
    result = tools.QueryTools().execute(name, arguments)
    assert result == {
        'status': 'ok',
        'rows': rows[:10],
        'pagination': {
            'offset': expected_offset,
            'limit': 10,
            'next_offset': next_offset,
        },
    }
    assert graph_driver.execute_query.call_args.kwargs['parameters_']['offset'] == expected_offset


@pytest.mark.parametrize('offset', [-1, 1.5, '10', True, None])
def test_reject_invalid_graph_search_offsets(offset: Any):
    """
    Checks that graph searches reject invalid offset values.
    """
    with pytest.raises(ValueError, match='offset'):
        tools.GraphSearchArguments.model_validate({'offset': offset})


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
    Tool definitions have names and reject unsupported arguments.
    """
    for definition in tools.QueryTools().definitions:
        function = definition['function']
        assert definition['type'] == 'function'
        assert function['name']
        assert function['parameters']['additionalProperties'] is False


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
        ('search_disaster_events', {'from_date': '2026-09-20', 'to_date': '2026-09-19'}),
        ('get_disaster_context', {}),
        ('find_related_disaster_events', {'event_id': 'event-1', 'relation_kind': 'nonexistent'}),
        ('search_response_events', {'from_date': '2026-09-20', 'to_date': '2026-09-19'}),
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
    assert result['status'] == 'error'
    assert result['message']


def test_search_appeals_preserves_details_and_date_parameters(graph_driver):
    """
    Checks that appeal searches pass launch dates and return funding and beneficiary details.
    """
    row = {
        'appeal_id': 'go-appeal-2261', 'title': 'Malawi - Food Insecurity',
        'start_datetime': datetime(2015, 9, 17), 'amount_funded': 873154.34,
        'beneficiaries': 1000, 'event_id': None,
    }
    graph_driver.execute_query.return_value = ([Mock(data=Mock(return_value=row))], None, None)

    result = tools.QueryTools().execute('search_appeals', {
        'text': 'Malawi - Food Insecurity', 'country_code': 'MWI',
        'from_date': '2015-09-17', 'to_date': '2015-09-17',
    })

    assert result == {
        'status': 'ok',
        'rows': [{**row, 'start_datetime': '2015-09-17T00:00:00'}],
        'pagination': {
            'offset': 0,
            'limit': 10,
            'next_offset': None,
        },
    }
    graph_driver.execute_query.assert_called_once()
    parameters = graph_driver.execute_query.call_args.kwargs['parameters_']
    assert parameters['from_date'] == parameters['to_date'] == date(2015, 9, 17)
    assert parameters['text'] == 'Malawi - Food Insecurity'
    assert parameters['country_code'] == 'MWI'

def test_event_news_query_uses_classified_media_cloud_links() -> None:
    query = tools._load_cypher_query("get_event_news.cypher")

    assert "[link:VERIFIED_FOR]" in query
    assert "CONTEXT_FOR" not in query
    assert "type(link) AS relationship_type" in query
    assert "link.evidence_excerpt AS evidence_excerpt" in query
    assert "body_excerpt" in query
    assert "RETRIEVED_FOR" not in query
