"""
Model selection, event retrieval, question construction, and answer collection.
"""

# Imports

import json
from time import perf_counter
from importlib.resources import files
from typing import LiteralString, cast

from neo4j import RoutingControl
import pycountry

from monty_tool.gap_analysis.external_sources import EXTERNAL_SOURCES, ExternalSourceName
from monty_tool.gap_analysis.retrieval import (
    EVIDENCE_SCOPE,
    evidence_for_source,
    hazard_code_name_map,
)
from monty_tool.gap_analysis.schemas import (
    AnswerAttempt,
    Evidence,
    GapQuestion,
    GapSelection,
    QuestionTemplate,
)
from monty_tool.llm.client import BedrockQueryAssistant, QueryAssistant
from monty_tool.llm.protocols import QueryAssistantProtocol
from monty_tool.llm.tools import GraphSearchArguments
from monty_tool.network.resources import get_graph_db_driver
from monty_tool.utils.versions import FrozenModel


# Resources

type AnyQueryAssistant = QueryAssistant | BedrockQueryAssistant

LOCAL_MODELS = (
    FrozenModel.QWEN3_1_7B,
    FrozenModel.QWEN3_5_4B,
    FrozenModel.QWEN3_5_9B,
    FrozenModel.GEMMA4_E4B,
)
MODEL_CHOICES = tuple(model.value for model in LOCAL_MODELS) + ('bedrock',)


# Helpers

def make_answerer(model: str) -> AnyQueryAssistant:
    """
    Load the selected local or Bedrock assistant.
    """
    return (
        BedrockQueryAssistant()
        if model == 'bedrock'
        else QueryAssistant(FrozenModel(model))
    )


def select_country_hazards(
    filters: GraphSearchArguments,
    limit: int,
    *,
    hazard_name: str | None = None,
    require_external_data: bool = True,
    external_sources: list[ExternalSourceName] | None = None,
) -> list[GapSelection]:
    """
    Select country–hazard pairs from historical events, up to the supplied limit.
    By default, each pair must have evidence for every selected external source.
    Different events within the pair may supply different sources.
    Hazards without a name are skipped before applying the limit and offset.
    """
    if limit < 1:
        raise ValueError('Limit must be positive.')
    query = cast(
        LiteralString,
        files('monty_tool.gap_analysis.cypher_queries').joinpath(
            'select_country_hazards.cypher',
        ).read_text(),
    )
    with get_graph_db_driver() as driver:
        records, _, _ = driver.execute_query(
            query,
            parameters_={
                **filters.model_dump(),
                'hazard_name': hazard_name,
                'hazard_code_name_map': hazard_code_name_map(),
                'limit': limit,
                'require_external_data': require_external_data,
                'external_sources': (
                    list(EXTERNAL_SOURCES)
                    if external_sources is None else external_sources
                ),
            },
            database_='neo4j',
            routing_=RoutingControl.READ,
        )
    return [GapSelection.model_validate(record.data()) for record in records]


def construct_gap_questions(
    selection: GapSelection,
    templates: list[QuestionTemplate],
) -> list[GapQuestion]:
    """
    Fill question templates for a selected country, hazard, and date range.
    """
    country_code = selection.filters.country_code
    if country_code is None:
        raise ValueError('Question construction requires a selected country.')
    country = pycountry.countries.get(alpha_3=country_code)
    labels = {
        'country': (
            f'{country.name} ({country_code})'
            if country else country_code
        ),
        'hazard': selection.hazard_name,
    }
    scope = ''
    if selection.filters.from_date or selection.filters.to_date:
        lower = selection.filters.from_date or 'the earliest available date'
        upper = selection.filters.to_date or 'the latest available date'
        scope = f' Limit disaster event start dates to {lower} through {upper}.'
    return [
        GapQuestion(
            country_code=country_code,
            hazard_name=selection.hazard_name,
            template_id=template.id,
            question=template.question.format(**labels) + scope,
        )
        for template in templates
    ]


def answer_question(
    assistant: QueryAssistantProtocol,
    question: GapQuestion,
    *,
    external_source: ExternalSourceName | None,
    evidence: Evidence,
) -> AnswerAttempt:
    """
    Answer a question using the selected data sources.
    Return the answer, token counts, elapsed time, and any model error.
    Use external_source=None to restrict the answer to core records.
    """
    if external_source is not None and external_source not in EXTERNAL_SOURCES:
        raise ValueError(f'Unknown external source: {external_source}')
    started = perf_counter()
    try:
        supplied = evidence_for_source(evidence, external_source)
        prompt = json.dumps({
            'question': question.question,
            'scope': EVIDENCE_SCOPE,
            'external_source': external_source,
            'records': supplied,
        }, ensure_ascii=False, default=str)
        response = assistant.ask(prompt, use_tools=False)
        return AnswerAttempt(
            answer=response['answer'],
            input_tokens=response['input_tokens'],
            output_tokens=response['output_tokens'],
            duration_seconds=perf_counter() - started,
        )
    except Exception as exc:
        return AnswerAttempt(error=str(exc), duration_seconds=perf_counter() - started)
