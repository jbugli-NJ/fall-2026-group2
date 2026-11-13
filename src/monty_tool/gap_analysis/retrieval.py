"""
Utilities to retrieve context for country and hazard comparisons.
"""

# Imports

from copy import deepcopy
from datetime import date
from importlib.resources import files
import logging
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

logger = logging.getLogger(__name__)


# Hazard mapping

# NOTE: This maps Montandon hazard names to GO disaster type IDs
RESPONSE_HAZARD_IDS: dict[str, list[int]] = {
    'Earthquake': [2],
    'Cyclone': [4],
    'Civil Unrest': [7],
    'Volcanic eruption': [8],
    'Tsunami': [11],
    'Flooding': [12],
    'Cold Wave': [14],
    'Fire': [15],
    'Heatwave': [19],
    'Drought': [20],
    'Storm Surge': [23],
    'Gravitational Mass Movement (Landslide)': [24],
    'Insect Pest Infestations': [62],
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

DISASTER_LIMIT = 20
EVIDENCE_SCOPE = (
    f'Disaster records cover up to {DISASTER_LIMIT} of the most recent disasters '
    'with the selected external source.'
)

type EvidenceQuery = Literal[
    'retrieve_events.cypher',
    'retrieve_responses.cypher',
    'retrieve_news.cypher',
]

class RetrievalParameters(TypedDict):
    """
    Parameters for source-specific evidence retrieval.
    """
    country_code: str
    from_date: date | None
    to_date: date | None
    hazard_name: str
    hazard_code_name_map: dict[str, str]
    response_hazard_ids: list[int]
    external_source: ExternalSourceName
    disaster_limit: int
    event_ids: list[str]


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
    external_source: ExternalSourceName,
) -> Evidence:
    """
    Retrieve the 20 most recent disasters with the selected external source.
    Include their associated records and up to 20 matching IFRC response events.
    """
    if filters.country_code is None:
        raise ValueError('Evidence retrieval requires a country code.')
    response_hazard_ids = list(RESPONSE_HAZARD_IDS.get(hazard_name, []))
    if not response_hazard_ids:
        logger.warning(f"No GO disaster type mapping for {hazard_name=}")
    parameters: RetrievalParameters = {
        'country_code': filters.country_code,
        'from_date': filters.from_date,
        'to_date': filters.to_date,
        'hazard_name': hazard_name,
        'external_source': external_source,
        'disaster_limit': DISASTER_LIMIT,
        'event_ids': [],
        'hazard_code_name_map': hazard_code_name_map(),
        'response_hazard_ids': response_hazard_ids,
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
        parameters['event_ids'] = [event['event_id'] for event in evidence['events']]
        if external_source == 'news':
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
