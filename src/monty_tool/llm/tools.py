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

from monty_tool.event_context import EventContext
from monty_tool.news.query import build_news_query
from monty_tool.news.schemas import NewsQuery
from monty_tool.news.retrieval import search_ranked_news
from monty_tool.network.resources import get_graph_db_driver
from monty_tool.weather.query import build_weather_query
from monty_tool.weather.retrieval import get_event_weather


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
            result = search_ranked_news(query)

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


def _load_cypher_query(name: LiteralString) -> LiteralString:
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

    if isinstance(value, (date, datetime, time)):
        return value.isoformat()

    return value


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
                    "description": "Find Montandon disaster events by place, hazard, date, or text.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "country_code": {
                                "type": "string",
                                "description": "3-letter country code (e.g. CHN for China).",
                            },
                            "hazard_code": {
                                "type": "string",
                                "description": "Montandon hazard code.",
                            },
                            "from_date": {"type": "string", "format": "date"},
                            "to_date": {"type": "string", "format": "date"},
                            "text": {
                                "type": "string",
                                "description": "Words to find in event titles and descriptions.",
                            },
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_disaster_context",
                    "description": "Get one Montandon event and its impacts using an event_id returned by search_disaster_events.",
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
                    "description": "Find IFRC response events by place, disaster type, date, or text.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "country_code": {"type": "string"},
                            "disaster_type": {"type": "string"},
                            "from_date": {"type": "string", "format": "date"},
                            "to_date": {"type": "string", "format": "date"},
                            "text": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_response_context",
                    "description": "Get one IFRC response event and its linked appeals using an event_id returned by search_response_events.",
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
                    "name": "search_news",
                    "description": "Search NewsAPI with an English query and a date range.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "minLength": 1, "maxLength": 500},
                            "from_date": {"type": "string", "format": "date"},
                            "to_date": {"type": "string", "format": "date"},
                            "location": {"type": "string", "description": "Place named in the disaster question, when available.",},
                        },
                        "required": ["query", "from_date", "to_date"],
                        "additionalProperties": False,
                    },
                },
            },
        ]

    def _run_graph_query(
        self,
        arguments: dict[str, Any],
        argument_model: type[BaseModel],
        query_file: LiteralString,
        error_message: str,
        ) -> dict[str, Any]:
        """
        Validate and run a graph query.
        """
        try:
            args = argument_model.model_validate(arguments)
        except ValidationError:
            return {"status": "error", "message": error_message}

        try:
            with get_graph_db_driver() as driver:
                records, _, _ = driver.execute_query(
                    _load_cypher_query(query_file),
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

    def _search_news(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """
        Run a direct NewsAPI search using provided inputs.
        """
        try:
            args = QueryNewsArguments.model_validate(arguments)
        except ValidationError:
            return {
                "status": "error",
                "message": "Supply query, from_date, and to_date as ISO dates.",
            }

        try:
            news_query = args.query
            if args.location and args.location.casefold() not in news_query.casefold():
                news_query = f"{news_query} {args.location}"
            if len(news_query) > 500:
                return {
                    "status": "error",
                    "message": "News query exceeds 500 characters after adding location.",
                }

            result = search_ranked_news(
                NewsQuery(
                    item_id="query-assistant",
                    query=news_query,
                    from_date=args.from_date,
                    to_date=args.to_date,
                ),
            )
        except RequestException as e:
            return {
                "status": "error",
                "message": f"NewsAPI connection failed: {type(e).__name__}",
            }
        except (RuntimeError, ValueError) as e:
            return {"status": "error", "message": str(e)}

        return {
            "status": "ok" if result.articles else "empty",
            **result.model_dump(mode="json"),
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
                    "Supply optional country_code, hazard_code, dates, or text.",
                )
            if name == "get_disaster_context":
                return self._run_graph_query(
                    arguments,
                    EventIdArguments,
                    "get_disaster_context.cypher",
                    "Supply a non-empty event_id.",
                )
            if name == "find_related_disaster_events":
                return self._run_graph_query(
                    arguments,
                    RelatedEventArguments,
                    "find_related_disaster_events.cypher",
                    "Supply event_id and one supported relation_kind.",
                )
            if name == "search_response_events":
                return self._run_graph_query(
                    arguments,
                    ResponseEventSearchArguments,
                    "search_response_events.cypher",
                    "Supply optional country_code, disaster_type, dates, or text.",
                )
            if name == "get_response_context":
                return self._run_graph_query(
                    arguments,
                    EventIdArguments,
                    "get_response_context.cypher",
                    "Supply a non-empty event_id.",
                )
            if name == "search_news":
                return self._search_news(arguments)
        except Exception as e:
            return {
                "status": "error",
                "message": f"Tool call failed! Error: {str(e) or 'Unknown'}",
            }
        return {
            "status": "error",
            "message": "Unknown tool name.",
        }


class WeatherArguments(BaseModel):
    """
    Arguments accepted by the record-scoped weather tool.
    """
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    item_id: str = Field(min_length=1)
    # The useful window depends on the hazard: a flood is explained by
    # the rain of the preceding week, a heatwave by the days themselves.
    # Bounded so a tool call cannot ask POWER for years of data.
    days_before: int = Field(default=7, ge=0, le=30)
    days_after: int = Field(default=1, ge=0, le=30)


class WeatherTools:
    """
    Weather for supplied Montandon records, as an LLM tool.

    Mirrors `NewsTools`: the model chooses among records it was given
    and never supplies coordinates itself, so a hallucinated point
    cannot return real-looking weather for somewhere else.
    """

    def __init__(self, items: list[EventContext]):
        if not 1 <= len(items) <= 10:
            raise ValueError("Provide between 1 and 10 sample records.")

        self.items = {item.item_id: item for item in items}

        # Records with no point geometry are advertised as unavailable
        # rather than offered and then refused: most collections are
        # country-level polygons, and POWER needs a single point.
        self.available = {
            item_id: item
            for item_id, item in self.items.items()
            if item.longitude is not None and item.latitude is not None
        }

        # This describes the tool to the model.
        self.definitions = [{
            "type": "function",
            "function": {
                "name": "get_event_weather",
                "description": (
                    "Get observed daily weather around one of the supplied "
                    "Montandon records, from NASA POWER."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "item_id": {
                            "type": "string",
                            "enum": list(self.available),
                        },
                        "days_before": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 30,
                            "description": (
                                "Days of weather before the event starts. "
                                "Use more for floods, where the preceding "
                                "rain is the cause."
                            ),
                        },
                        "days_after": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 30,
                            "description": "Days of weather after the event ends.",
                        },
                    },
                    "required": ["item_id"],
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
        Validate a weather tool call and retrieve observations.
        """
        if name != "get_event_weather":
            return {
                "status": "error",
                "message": "Unknown tool name.",
            }

        try:
            args = WeatherArguments.model_validate(arguments)
        except ValidationError:
            return {
                "status": "error",
                "message": (
                    "Supply item_id, and optionally days_before and "
                    "days_after between 0 and 30; no other arguments."
                ),
            }

        item = self.items.get(args.item_id)

        if item is None:
            return {
                "status": "error",
                "message": "Unknown Montandon item_id.",
            }

        try:
            # Reuse the existing Montandon -> WeatherQuery function,
            # which refuses records POWER cannot answer for.
            query = build_weather_query(
                item,
                days_before=args.days_before,
                days_after=args.days_after,
            )
            result = get_event_weather(query)

        except RequestException as exc:
            return {
                "status": "error",
                "message": (
                    f"NASA POWER connection failed: {type(exc).__name__}"
                ),
            }

        except (RuntimeError, ValueError) as exc:
            return {
                "status": "error",
                "message": str(exc),
            }

        # The summary, not the daily series: a row per day per
        # measurement would crowd out the rest of the conversation.
        return {
            "status": "empty" if result.missing_days == len(result.days) else "ok",
            "item_id": result.item_id,
            "latitude": result.latitude,
            "longitude": result.longitude,
            "elevation": result.elevation,
            "sources": result.sources,
            **result.summary(),
        }
