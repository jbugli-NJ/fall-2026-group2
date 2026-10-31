"""
Model selection, event retrieval, question construction, and answer collection.
"""

# Imports

from time import perf_counter
from importlib.resources import files
from typing import Any, LiteralString, cast

from neo4j import RoutingControl

from monty_tool.gap_analysis.schemas import AnswerAttempt, GapQuestion, QuestionTemplate
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


def select_contexts(filters: GraphSearchArguments, limit: int) -> list[dict[str, Any]]:
    """
    Fetch the newest matching event records and their impacts, up to the supplied limit.
    """
    if limit < 1:
        raise ValueError('Event limit must be positive.')
    query = cast(
        LiteralString,
        files('monty_tool.gap_analysis').joinpath('select_contexts.cypher').read_text(),
    )
    with get_graph_db_driver() as driver:
        records, _, _ = driver.execute_query(
            query,
            parameters_={**filters.model_dump(), 'limit': limit},
            database_='neo4j',
            routing_=RoutingControl.READ,
        )
    return [record.data() for record in records]


def construct_gap_questions(
    event_data: dict[str, Any],
    templates: list[QuestionTemplate],
) -> list[GapQuestion]:
    """
    Fill question templates with an event's ID, title, and start date.
    """
    question_params = {
        key: event_data[key] for key in ('event_id', 'title', 'start_datetime')
    }
    return [
        GapQuestion(
            event_id=question_params['event_id'],
            template_id=template.id,
            question=template.question.format(**question_params),
        )
        for template in templates
    ]


def answer_question(
    assistant: QueryAssistantProtocol,
    question: GapQuestion,
    *,
    enable_external: bool,
) -> AnswerAttempt:
    """
    Answer a gap analysis question with external data enabled or disabled.
    Return the answer, elapsed time, and any retrieval or model error.
    """
    started = perf_counter()
    try:
        response = assistant.ask(
            question.question,
            use_tools=True,
            enable_external=enable_external,
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
