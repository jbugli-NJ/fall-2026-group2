"""
Expose NewsAPI and Neo4j queries as LLM tools.
"""

from __future__ import annotations

from datetime import date, datetime, time
from importlib.resources import files
from typing import Any, Literal, LiteralString, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)
from requests import RequestException
from neo4j import RoutingControl
from neo4j.time import Date as Neo4jDate, DateTime as Neo4jDateTime

from monty_tool.event_context import EventContext
from monty_tool.news.query import build_news_query
from monty_tool.news.retrieval import search_ranked_news
from monty_tool.network.resources import get_graph_db_driver


class NewsArguments(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    item_id: str = Field(min_length=1)
    query: str = Field(min_length=1, max_length=500)


class NewsTools:
    def __init__(self, items: list[EventContext]):
        if not 1 <= len(items) <= 10:
            raise ValueError("Provide between 1 and 10 sample records.")

        self.items = {item.item_id: item for item in items}

        # This describes the tool to the model.
        self.definitions = [{
            "type": "function",
            "function": {
                "name": "search_event_news",
                "description": (
                    "Search news for one of the supplied Montandon records."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "item_id": {
                            "type": "string",
                            "enum": list(self.items),
                        },
                        "query": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 500,
                            "description": (
                                "English news search keywords based on the supplied "
                                "event: hazard, location, and useful identifying details. "
                                "Use AND/OR when helpful. Do not invent facts."
                            ),
                        },
                    },
                    "required": ["item_id", "query"],
                    "additionalProperties": False,
                },
            },
        }]


    def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        ) -> dict[str, Any]:
        """
        Validate a NewsAPI tool call and retrieve articles.
        """
        if name != "search_event_news":
            return {
                "status": "error",
                "message": "Unknown tool name.",
            }

        try:
            args = NewsArguments.model_validate(arguments)
        except ValidationError:
            return {
                "status": "error",
                "message": (
                    "Supply item_id and a non-empty query of at most "
                    "500 characters; no other arguments."
                ),
            }

        item = self.items.get(args.item_id)

        if item is None:
            return {
                "status": "error",
                "message": "Unknown Montandon item_id.",
            }

        # Reuse the existing Montandon -> NewsQuery function.
        query = build_news_query(
            item,
            search_query=args.query,
        )

        if query.from_date > query.to_date:
            return {
                "status": "error",
                "message": "Invalid news date range.",
            }

        try:
            # Rank candidates before selecting articles for the assistant.
            result = search_ranked_news(query, event_context=item)

        except RequestException as exc:
            return {
                "status": "error",
                "message": (
                    f"NewsAPI connection failed: {type(exc).__name__}"
                ),
            }

        except (RuntimeError, ValueError) as exc:
            return {
                "status": "error",
                "message": str(exc),
            }

        # Convert dates and nested Pydantic objects to JSON-compatible values.
        return {
            "status": "ok" if result.articles else "empty",
            **result.model_dump(mode="json"),
        }


_OMIT_VALUE = object()
_CYPHER_QUERY_PACKAGE = 'monty_tool.llm.cypher_queries'

type CypherTemplateFile = Literal[
    'search_disaster_events.cypher',
    'get_disaster_context.cypher',
    'find_related_disaster_events.cypher',
    'search_response_events.cypher',
    'search_appeals.cypher',
    'get_response_context.cypher',
    'get_event_news.cypher',
]

def _load_cypher_query(name: CypherTemplateFile) -> LiteralString:
    """
    Read a Cypher query by filename.
    """
    return cast(
        LiteralString,
        files(_CYPHER_QUERY_PACKAGE).joinpath(name).read_text(encoding='utf-8'),
    )


class GraphSearchArguments(BaseModel):
    """
    Filters shared by graph searches.
    """
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    country_code: str | None = Field(default=None, min_length=3, max_length=3)
    from_date: date | None = None
    to_date: date | None = None

    @model_validator(mode="after")
    def validate_date_range(self) -> GraphSearchArguments:
        """
        Require both dates or neither in the expected order.
        """
        if (self.from_date is None) != (self.to_date is None):
            raise ValueError("Supply both from_date and to_date, or neither.")
        if self.from_date is not None and self.to_date is not None:
            if self.from_date > self.to_date:
                raise ValueError("from_date must be on or before to_date.")
        return self


class DisasterEventSearchArguments(GraphSearchArguments):
    """
    Filters for Montandon event searches.
    """
    hazard_code: str | None = Field(default=None, min_length=1, max_length=100)
    text: str | None = Field(default=None, min_length=1, max_length=200)
    min_elevation: float | None = Field(default=None, allow_inf_nan=False)
    max_elevation: float | None = Field(default=None, allow_inf_nan=False)
    min_mean_temperature: float | None = Field(default=None, allow_inf_nan=False)
    max_mean_temperature: float | None = Field(default=None, allow_inf_nan=False)
    min_precipitation_total: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    max_precipitation_total: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode='after')
    def validate_weather_ranges(self) -> DisasterEventSearchArguments:
        """
        Require each supplied weather range to run from lower to higher values.
        """
        ranges = (
            ('elevation', self.min_elevation, self.max_elevation),
            ('mean_temperature', self.min_mean_temperature, self.max_mean_temperature),
            ('precipitation_total', self.min_precipitation_total, self.max_precipitation_total),
        )
        for name, lower, upper in ranges:
            if lower is not None and upper is not None and lower > upper:
                raise ValueError(f'min_{name} must be on or below max_{name}.')
        return self


class EventIdArguments(BaseModel):
    """
    Identify an event returned by a graph tool.
    """
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    event_id: str = Field(min_length=1, max_length=200)


class RelatedEventArguments(EventIdArguments):
    """
    Select one relationship for a related event search.
    """
    relation_kind: Literal[
        'semantic_similarity',
        'same_hazard',
        'same_country',
        'same_start_day',
        'same_incident',
    ]


class ResponseEventSearchArguments(GraphSearchArguments):
    """
    Filters for IFRC response event searches.
    """
    disaster_type: str | None = Field(default=None, min_length=1, max_length=100)
    text: str | None = Field(default=None, min_length=1, max_length=200)


class AppealSearchArguments(GraphSearchArguments):
    """
    Filter IFRC appeals by country, disaster type, title, and launch dates.
    """
    disaster_type: str | None = Field(default=None, min_length=1, max_length=100)
    text: str | None = Field(default=None, min_length=1, max_length=200)


class QueryNewsArguments(BaseModel):
    """
    Arguments accepted by the direct NewsAPI tool.
    """
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=500)
    from_date: date
    to_date: date
    location: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_date_range(self) -> QueryNewsArguments:
        """
        Require the NewsAPI date range to run forward in time.
        """
        if self.from_date > self.to_date:
            raise ValueError("from_date must be on or before to_date.")
        return self


def _json_value(value: Any) -> Any:
    """
    Convert Neo4j outputs into JSON, stripping embeddings.
    Checks recursively for nested objects.
    """
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            serialized = _json_value(item)
            if serialized is not _OMIT_VALUE:
                output[key] = serialized
        return output

    if isinstance(value, (list, tuple)):
        if len(value) > 100:
            return _OMIT_VALUE
        output = []
        for item in value:
            serialized = _json_value(item)
            if serialized is not _OMIT_VALUE:
                output.append(serialized)
        return output

    if isinstance(value, (date, datetime, time, Neo4jDate, Neo4jDateTime)):
        return value.isoformat()

    return value


def _validation_error_message(error: ValidationError) -> str:
    """
    Convert a Pydantic validation error into a message for the LLM.
    """
    messages = []
    for detail in error.errors(include_url=False, include_context=False, include_input=False):
        field = '.'.join(str(part) for part in detail['loc'])
        message = (
            'unsupported argument' if detail['type'] == 'extra_forbidden'
            else detail['msg']
        )
        messages.append(f'- {field}: {message}' if field else f'- {message}')
    return 'Invalid arguments:\n' + '\n'.join(messages)


class QueryTools:
    """
    Graph and news tools available to QueryAssistant.
    """

    def __init__(self, max_tool_calls: int = 10):
        if max_tool_calls < 1:
            raise ValueError("max_tool_calls must be at least 1.")

        self.max_tool_calls = max_tool_calls
        self.tool_call_count = 0
        self.definitions = [
            {
                "type": "function",
                "function": {
                    "name": "search_disaster_events",
                    "description": (
                        "Find up to 10 newest matching disaster events. Filters are optional; supply both dates or neither. "
                        "Dates filter event starts; use get_disaster_context for impacts and stored weather."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "country_code": {
                                "type": "string",
                                "description": "3-letter country code (e.g. CHN for China).",
                            },
                            "hazard_code": {
                                "type": "string",
                                "description": (
                                    "Exact Montandon hazard code (e.g. nat-hyd-flo-flo for Flood (General)). "
                                    "If only the hazard name is known, omit this filter and use text."
                                ),
                            },
                            "from_date": {
                                "type": "string", "format": "date",
                                "description": "Earliest event start date, inclusive.",
                            },
                            "to_date": {
                                "type": "string", "format": "date",
                                "description": "Latest event start date, inclusive. Set both dates equal to match one day.",
                            },
                            "text": {
                                "type": "string",
                                "description": (
                                    "Case-insensitive substring of the event title or description; "
                                    "use for hazard names (e.g. Flood (General))."
                                ),
                            },
                            "min_elevation": {
                                "type": "number", "description": "Minimum terrain elevation in meters above sea level.",
                            },
                            "max_elevation": {
                                "type": "number", "description": "Maximum terrain elevation in meters above sea level.",
                            },
                            "min_mean_temperature": {
                                "type": "number", "description": "Minimum retrieval-period mean temperature in degrees C.",
                            },
                            "max_mean_temperature": {
                                "type": "number", "description": "Maximum retrieval-period mean temperature in degrees C.",
                            },
                            "min_precipitation_total": {
                                "type": "number", "minimum": 0,
                                "description": "Minimum retrieval-period precipitation total in mm.",
                            },
                            "max_precipitation_total": {
                                "type": "number", "minimum": 0,
                                "description": "Maximum retrieval-period precipitation total in mm.",
                            },
                        },
                        "dependentRequired": {"from_date": ["to_date"], "to_date": ["from_date"]},
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_disaster_context",
                    "description": (
                        "Fetch impacts and stored weather for an event_id from search_disaster_events, "
                        "including counts, temperature extremes, precipitation, and wind speed when available."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {"event_id": {"type": "string"}},
                        "required": ["event_id"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "find_related_disaster_events",
                    "description": "Find events related to one Montandon event by one named relationship.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "event_id": {"type": "string"},
                            "relation_kind": {
                                "type": "string",
                                "enum": ["semantic_similarity", "same_hazard", "same_country", "same_start_day", "same_incident"],
                            },
                        },
                        "required": ["event_id", "relation_kind"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_response_events",
                    "description": (
                        "Find up to 10 newest matching IFRC events. Filters are optional; supply both dates or neither. "
                        "Dates filter event starts; use search_appeals to search by appeal launch date."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "country_code": {
                                "type": "string", "description": "3-letter country code (e.g. MWI for Malawi).",
                            },
                            "disaster_type": {
                                "type": "string", "description": "Case-insensitive substring of the recorded disaster type.",
                            },
                            "from_date": {
                                "type": "string", "format": "date",
                                "description": "Earliest event start date, inclusive; not the appeal launch date.",
                            },
                            "to_date": {
                                "type": "string", "format": "date",
                                "description": "Latest event start date, inclusive. Set both dates equal to match one day.",
                            },
                            "text": {
                                "type": "string", "description": "Case-insensitive substring of the event title or summary.",
                            },
                        },
                        "dependentRequired": {"from_date": ["to_date"], "to_date": ["from_date"]},
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_appeals",
                    "description": (
                        "Find up to 10 newest matching IFRC appeals, including beneficiaries and funding. "
                        "Filters are optional; supply both dates or neither. Dates filter appeal launches."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "country_code": {
                                "type": "string", "description": "3-letter country code (e.g. MWI for Malawi).",
                            },
                            "disaster_type": {
                                "type": "string", "description": "Case-insensitive substring of the recorded disaster type.",
                            },
                            "text": {
                                "type": "string", "description": "Case-insensitive substring of the appeal title.",
                            },
                            "from_date": {
                                "type": "string", "format": "date",
                                "description": "Earliest appeal launch date, inclusive.",
                            },
                            "to_date": {
                                "type": "string", "format": "date",
                                "description": "Latest appeal launch date, inclusive. Set both dates equal to match one day.",
                            },
                        },
                        "dependentRequired": {"from_date": ["to_date"], "to_date": ["from_date"]},
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_response_context",
                    "description": (
                        "Fetch an IFRC event and linked appeals using an event_id from search_response_events. "
                        "Includes affected population, appeal launch dates, beneficiaries, and funding."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {"event_id": {"type": "string"}},
                        "required": ["event_id"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_event_news",
                    "description": (
                        "Get saved NewsAPI article candidates retrieved for an "
                        "existing disaster event by event_id. These are search "
                        "results, not verified reports about the event."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "event_id": {"type": "string"},
                        },
                        "required": ["event_id"],
                        "additionalProperties": False,
                    },
                },
            },
        ]

    def _run_graph_query(
        self,
        arguments: dict[str, Any],
        argument_model: type[BaseModel],
        query_file: CypherTemplateFile,
        ) -> dict[str, Any]:
        """
        Validate and run a graph query.
        """
        try:
            args = argument_model.model_validate(arguments)
        except ValidationError as error:
            return {"status": "error", "message": _validation_error_message(error)}

        try:
            with get_graph_db_driver() as driver:
                records, _, _ = driver.execute_query(
                    _load_cypher_query(name=query_file),
                    parameters_=args.model_dump(),
                    database_="neo4j",
                    routing_=RoutingControl.READ,
                )
        except Exception as exc:
            return {"status": "error", "message": f"Graph query failed: {exc}"}

        return {
            "status": "ok",
            "rows": [_json_value(record.data()) for record in records],
        }

    def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        ) -> dict[str, Any]:
        """
        Validate and execute one query tool call.
        """
        if self.tool_call_count >= self.max_tool_calls:
            return {
                "status": "limit_reached",
                "message": (
                    "The tool call limit has been reached. Answer now using "
                    "the results already collected. Do not call another tool."
                ),
            }

        self.tool_call_count += 1
        try:
            if name == "search_disaster_events":
                return self._run_graph_query(
                    arguments,
                    DisasterEventSearchArguments,
                    "search_disaster_events.cypher",
                )
            if name == "get_disaster_context":
                return self._run_graph_query(
                    arguments,
                    EventIdArguments,
                    "get_disaster_context.cypher",
                )
            if name == "find_related_disaster_events":
                return self._run_graph_query(
                    arguments,
                    RelatedEventArguments,
                    "find_related_disaster_events.cypher",
                )
            if name == "search_response_events":
                return self._run_graph_query(
                    arguments,
                    ResponseEventSearchArguments,
                    "search_response_events.cypher",
                )
            if name == "search_appeals":
                return self._run_graph_query(
                    arguments,
                    AppealSearchArguments,
                    "search_appeals.cypher",
                )
            if name == "get_response_context":
                return self._run_graph_query(
                    arguments,
                    EventIdArguments,
                    "get_response_context.cypher",
                )
            if name == "get_event_news":
                return self._run_graph_query(
                    arguments,
                    EventIdArguments,
                    "get_event_news.cypher",
                )
        except Exception as e:
            return {
                "status": "error",
                "message": f"Tool call failed! Error: {str(e) or 'Unknown'}",
            }
        return {
            "status": "error",
            "message": "Unknown tool name.",
        }
