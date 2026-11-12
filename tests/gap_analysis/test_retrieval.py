"""
Tests for fixed evidence retrieval and source isolation.
"""

# Imports

from datetime import date
from contextlib import nullcontext
from typing import cast
from unittest.mock import Mock

import pytest

from monty_tool.gap_analysis import retrieval
from monty_tool.gap_analysis.schemas import Evidence
from monty_tool.gap_analysis.external_sources import EXTERNAL_SOURCES
from monty_tool.llm.tools import GraphSearchArguments


# Tests


def test_source_isolation_preserves_core_and_nested_weather():
    """
    Each external pass changes only its source, including impact weather.
    """
    evidence = cast(
        Evidence,
        {
            'events': [{
                'event_id': 'event',
                'weather': {'mean_temperature': 20},
                'impacts': [{
                    'impact_value': 10,
                    'weather': {'mean_temperature': 21},
                }],
                'footprints': [{'source': 'montandon'}],
                'external_footprints': [{'source': 'usgs'}],
            }],
            'responses': [{'appeals': [{'amount_requested': 100}]}],
            'news': [{'evidence_excerpt': 'Damage reported'}],
        },
    )
    core = retrieval.evidence_for_source(evidence, None)
    assert core == {
        'events': [{
            'event_id': 'event',
            'impacts': [{'impact_value': 10}],
            'footprints': [{'source': 'montandon'}],
        }],
        'responses': evidence['responses'],
    }
    for source in EXTERNAL_SOURCES:
        selected = retrieval.evidence_for_source(evidence, source)
        assert retrieval.evidence_for_source(selected, None) == core
        assert ('news' in selected) == (source == 'news')
        assert ('weather' in selected['events'][0]['impacts'][0]) == (source == 'weather')
        assert ('external_footprints' in selected['events'][0]) == (
            source == 'shakemap_footprints'
        )
    assert 'weather' in evidence['events'][0]


@pytest.mark.parametrize('source', ['news', 'weather', 'shakemap_footprints'])
def test_queries_share_source_scope_and_preserve_associated_records(monkeypatch, source):
    """
    Limit disasters at retrieval, keeping their associated records intact.
    """
    driver = Mock()
    event = {
        'event_id': 'event',
        'impacts': [{'impact_value': n} for n in range(105)],
    }
    driver.execute_query.side_effect = [
        ([Mock(data=lambda: event)], None, None),
        ([], None, None), ([], None, None),
    ]
    monkeypatch.setattr(retrieval, 'get_graph_db_driver', lambda: nullcontext(driver))
    filters = GraphSearchArguments.model_validate({
        'country_code': 'Brazil',
        'from_date': '2026-08-01',
        'to_date': '2026-09-30',
        'offset': 5,
    })
    monkeypatch.setattr(retrieval, 'hazard_code_name_map', lambda: {'WF': 'Wildfires'})
    result = retrieval.retrieve_evidence(
        filters,
        hazard_name='Wildfires',
        external_source=source,
    )
    assert len(result['events'][0]['impacts']) == 105
    assert result['responses'] == []
    assert ('news' in result) == (source == 'news')
    assert driver.execute_query.call_count == (3 if source == 'news' else 2)
    for call in driver.execute_query.call_args_list:
        params = call.kwargs['parameters_']
        assert params['country_code'] == 'BRA'
        assert params['from_date'] == date(2026, 8, 1)
        assert params['to_date'] == date(2026, 9, 30)
        assert params['hazard_name'] == 'Wildfires'
        assert 'offset' not in params
        assert params['hazard_code_name_map'] == {'WF': 'Wildfires'}
        assert 'fire' in params['response_hazard_names']
        assert params['disaster_limit'] == 20
        assert params['external_source'] == source
    if source == 'news':
        news_call = driver.execute_query.call_args_list[-1]
        assert news_call.kwargs['parameters_']['event_ids'] == ['event']


def test_query_failure_is_not_treated_as_empty_evidence(monkeypatch):
    """
    A database failure prevents answering from an incomplete bundle.
    """
    driver = Mock()
    driver.execute_query.side_effect = RuntimeError('Database unavailable')
    monkeypatch.setattr(retrieval, 'get_graph_db_driver', lambda: nullcontext(driver))
    with pytest.raises(RuntimeError, match='Database unavailable'):
        retrieval.retrieve_evidence(
            GraphSearchArguments(country_code='JPN'),
            hazard_name='Earthquake',
            external_source='weather',
        )


def test_retrieval_requires_a_selected_country(monkeypatch):
    """
    Country-wide selection filters must be narrowed before retrieving evidence.
    """
    driver = Mock()
    monkeypatch.setattr(retrieval, 'get_graph_db_driver', driver)
    with pytest.raises(ValueError, match='requires a country code'):
        retrieval.retrieve_evidence(
            GraphSearchArguments(),
            hazard_name='Earthquake',
            external_source='weather',
        )
    driver.assert_not_called()


def test_retrieval_preserves_query_order_and_nested_arrays(monkeypatch):
    """
    Retrieved records retain native temporal values and their original list order.
    """
    driver = Mock()
    rows = [
        {
            'event_id': 'z-first',
            'start_datetime': date(2020, 1, 1),
            'footprints': [{'severity_levels': [3, 1, 2]}],
        },
        {
            'event_id': 'a-second',
            'start_datetime': date(2021, 1, 1),
        },
    ]
    driver.execute_query.side_effect = [
        ([Mock(data=lambda row=row: row) for row in rows], None, None),
        ([], None, None),
    ]
    monkeypatch.setattr(retrieval, 'get_graph_db_driver', lambda: nullcontext(driver))
    evidence = retrieval.retrieve_evidence(
        GraphSearchArguments(country_code='JPN'),
        hazard_name='Earthquake',
        external_source='weather',
    )
    assert [row['event_id'] for row in evidence['events']] == ['z-first', 'a-second']
    assert evidence['events'][0]['footprints'][0]['severity_levels'] == [3, 1, 2]
    assert evidence['events'][0]['start_datetime'] is rows[0]['start_datetime']


def test_source_selection_does_not_mutate_shared_snapshot():
    """
    Editing one pass cannot change the records used by another pass or the archive.
    """
    evidence = cast(
        Evidence,
        {
            'events': [{
                'event_id': 'one',
                'impacts': [{
                    'impact_value': 10,
                    'weather': None,
                }],
                'footprints': [{'severity_levels': ['high', 'low']}],
                'weather': None,
                'external_footprints': [],
            }],
            'responses': [],
            'news': [],
        },
    )
    selected = retrieval.evidence_for_source(evidence, None)
    selected['events'][0]['impacts'][0]['impact_value'] = 99
    levels = selected['events'][0]['footprints'][0]['severity_levels']
    assert levels is not None
    levels.reverse()
    assert evidence['events'][0]['impacts'][0]['impact_value'] == 10
    assert evidence['events'][0]['footprints'][0]['severity_levels'] == ['high', 'low']
    assert 'weather' in evidence['events'][0]
    assert 'weather' in evidence['events'][0]['impacts'][0]
