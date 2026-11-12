"""
Utilities to retrieve context for country and hazard comparisons.
"""

# Imports

from copy import deepcopy
from datetime import date
from importlib.resources import files
from typing import Literal, LiteralString, TypedDict, cast

from neo4j import Driver, RoutingControl

from monty_tool.features import hazard_code_lookup

from monty_tool.llm.tools import GraphSearchArguments
from monty_tool.network.resources import get_graph_db_driver
from monty_tool.static_codes import HAZARD_NAME_FALLBACKS

from monty_tool.gap_analysis.external_sources import ExternalSourceName
from monty_tool.gap_analysis.schemas import (
    EventEvidence, Evidence, NewsEvidence, ResponseEvidence,
)


# Name mapping

# NOTE: IFRC uses disaster type names rather than Montandon hazard codes.
RESPONSE_HAZARD_NAMES = {
    'Wildfires': ['fire', 'forest fire', 'wildfire', 'wildfires'],
    'Cyclone': ['cyclone', 'tropical cyclone', 'extratropical cyclone'],
    'Flooding': ['flood', 'floods', 'flooding', 'flash flood'],
    'Earthquake': ['earthquake'],
    'Volcanic eruption': ['volcanic eruption', 'volcano'],
    'Drought': ['drought'],
}


def hazard_code_name_map() -> dict[str, str]:
    """
    Resolve nonempty hazard names, preferring the hazard code table over static fallbacks.
    """
    return {
        **HAZARD_NAME_FALLBACKS,
        **{
            code: name
            for code, name in hazard_code_lookup().select(
                'hazard_code',
                'hazard_label',
            ).iter_rows()
            if name and name.strip()
        },
    }


# Query boundary

type EvidenceQuery = Literal[
    'retrieve_events.cypher',
    'retrieve_responses.cypher',
    'retrieve_news.cypher',
]

class RetrievalParameters(TypedDict):
    """
    Parameters shared by the three evidence queries.
    """
    country_code: str
    from_date: date | None
    to_date: date | None
    hazard_name: str
    hazard_code_name_map: dict[str, str]
    response_hazard_names: list[str]


def _query_rows(
    driver: Driver,
    filename: EvidenceQuery,
    parameters: RetrievalParameters,
) -> list[dict[str, object]]:
    """
    Execute an evidence retrieval query against the Neo4j database.
    """
    query = cast(
        LiteralString,
        files('monty_tool.gap_analysis.cypher_queries').joinpath(filename).read_text(),
    )
    records, _, _ = driver.execute_query(
        query,
        parameters_=dict(parameters),
        database_='neo4j',
        routing_=RoutingControl.READ,
    )
    return [record.data() for record in records]


# Evidence retrieval

def retrieve_evidence(
    filters: GraphSearchArguments,
    hazard_name: str,
    sources: list[ExternalSourceName],
) -> Evidence:
    """
    Retrieve records for a selected country, hazard, and event start date range.
    This can optionally include external data for comparison.
    """
    if filters.country_code is None:
        raise ValueError('Evidence retrieval requires a country code.')
    parameters: RetrievalParameters = {
        'country_code': filters.country_code,
        'from_date': filters.from_date,
        'to_date': filters.to_date,
        'hazard_name': hazard_name,
        'hazard_code_name_map': hazard_code_name_map(),
        'response_hazard_names': RESPONSE_HAZARD_NAMES.get(
            hazard_name, [hazard_name.lower()],
        ),
    }
    with get_graph_db_driver() as driver:
        evidence: Evidence = {
            'events': cast(
                list[EventEvidence],
                _query_rows(driver, 'retrieve_events.cypher', parameters),
            ),
            'responses': cast(
                list[ResponseEvidence],
                _query_rows(driver, 'retrieve_responses.cypher', parameters),
            ),
        }
        if 'news' in sources:
            evidence['news'] = cast(
                list[NewsEvidence],
                _query_rows(driver, 'retrieve_news.cypher', parameters),
            )
    return evidence


# Source selection

def evidence_for_source(
    evidence: Evidence,
    source: ExternalSourceName | None,
) -> Evidence:
    """
    Return a copy of the provided evidence removing unused external source fields.
    """
    selected = deepcopy(evidence)
    if source != 'news':
        selected.pop('news', None)
    for event in selected['events']:
        if source != 'shakemap_footprints':
            event.pop('external_footprints', None)
        if source != 'weather':
            event.pop('weather', None)
            for impact in event['impacts']:
                impact.pop('weather', None)
    return selected
