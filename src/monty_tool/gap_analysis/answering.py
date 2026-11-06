"""
Model selection, event retrieval, question construction, and answer collection.
"""

# Imports

from time import perf_counter
from importlib.resources import files
from typing import Any, LiteralString, cast

from neo4j import RoutingControl
import pycountry

from monty_tool.features import hazard_code_lookup
from monty_tool.gap_analysis.external_sources import EXTERNAL_SOURCES, ExternalSourceName
from monty_tool.gap_analysis.schemas import AnswerAttempt, GapQuestion, QuestionTemplate
from monty_tool.llm.client import BedrockQueryAssistant, QueryAssistant
from monty_tool.llm.protocols import QueryAssistantProtocol
from monty_tool.llm.tools import DisasterEventSearchArguments
from monty_tool.network.resources import get_graph_db_driver
from monty_tool.static_codes import HAZARD_NAME_FALLBACKS
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


def select_contexts(
    filters: DisasterEventSearchArguments,
    limit: int,
    *,
    require_external_data: bool = True,
    external_sources: list[ExternalSourceName] | None = None,
) -> list[dict[str, Any]]:
    """
    Select country–hazard pairs from historical events, up to the supplied limit.
    By default, each pair must have evidence for every selected external source.
    Different events within the pair may supply different sources.
    """
    if limit < 1:
        raise ValueError('Limit must be positive.')
    query = cast(
        LiteralString,
        files('monty_tool.gap_analysis').joinpath('select_contexts.cypher').read_text(),
    )
    with get_graph_db_driver() as driver:
        records, _, _ = driver.execute_query(
            query,
            parameters_={
                **filters.model_dump(),
                'limit': limit,
                'require_external_data': require_external_data,
                'external_sources': (
                    list(EXTERNAL_SOURCES) if external_sources is None else external_sources
                ),
            },
            database_='neo4j',
            routing_=RoutingControl.READ,
        )
    return [record.data() for record in records]


def construct_gap_questions(
    context: dict[str, Any],
    templates: list[QuestionTemplate],
) -> list[GapQuestion]:
    """
    Fill questions for a country and hazard, using events as historical evidence.
    """
    question_params = {
        key: context[key] for key in ('country_code', 'hazard_code')
    }
    country = pycountry.countries.get(alpha_3=question_params['country_code'])
    hazard_names = {
        **HAZARD_NAME_FALLBACKS,
        **dict(hazard_code_lookup().select('hazard_code', 'hazard_label').iter_rows()),
    }
    labels = {
        'country': (
            f"{country.name} ({question_params['country_code']})"
            if country else question_params['country_code']
        ),
        'hazard': (
            f"{hazard_names[question_params['hazard_code']]} ({question_params['hazard_code']})"
            if question_params['hazard_code'] in hazard_names else question_params['hazard_code']
        ),
    }
    scope = ''
    if context.get('from_date') or context.get('to_date'):
        lower = context.get('from_date') or 'the earliest available date'
        upper = context.get('to_date') or 'the latest available date'
        scope += f' Limit disaster event start dates to {lower} through {upper}.'
    return [
        GapQuestion(
            **question_params,
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
) -> AnswerAttempt:
    """
    Answer a question using the selected data sources.
    Return the answer, elapsed time, and any retrieval or model error.
    Use external_source=None to restrict the answer to Montandon records.
    """
    if external_source is not None and external_source not in EXTERNAL_SOURCES:
        raise ValueError(f'Unknown external source: {external_source}')
    started = perf_counter()
    try:
        response = assistant.ask(
            question.question,
            use_tools=True,
            enable_news=external_source == 'news',
            enable_weather=external_source == 'weather',
            enable_external_footprints=external_source == 'shakemap_footprints',
        )
        attempt = AnswerAttempt(
            response=response,
            duration_seconds=perf_counter() - started,
        )
        if not any(
            step['result'].get('status') == 'ok' for step in response['tool_results']
        ):
            attempt.error = 'No graph query completed successfully.'
        return attempt
    except Exception as exc:
        return AnswerAttempt(error=str(exc), duration_seconds=perf_counter() - started)
