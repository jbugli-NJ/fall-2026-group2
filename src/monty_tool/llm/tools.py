"""
Expose NewsAPI and Neo4j queries as LLM tools.
"""

from __future__ import annotations

from datetime import date, datetime, time
import re
from typing import Any, LiteralString, cast

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
from monty_tool.news_api import search_news
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
            # Keep the first demo small.
            result = search_news(query, page_size=5)

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


_BLOCKED_CYPHER = re.compile(
    r"\b(?:"
    r"CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|ALTER|RENAME|"
    r"GRANT|DENY|REVOKE|FOREACH|CALL"
    r")\b|\bLOAD\s+CSV\b",
    re.IGNORECASE,
)
_MAX_CYPHER_ROWS = 25
_OMIT_VALUE = object()


class CypherArguments(BaseModel):
    """
    Arguments accepted by the graph query tool.
    """
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)


class QueryNewsArguments(BaseModel):
    """
    Arguments accepted by the direct NewsAPI tool.
    """
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=500)
    from_date: date
    to_date: date

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
    Tools available to QueryAssistant.
    """

    def __init__(self):
        self.schema = self._get_schema()
        self.definitions = [
            {
                "type": "function",
                "function": {
                    "name": "run_cypher",
                    "description": (
                        "Run a read-only Cypher query against the disaster graph. "
                        "Use parameters for values when useful."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "parameters": {"type": "object"},
                        },
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_news",
                    "description": (
                        "Search NewsAPI with an English query and a date range."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "minLength": 1, "maxLength": 500},
                            "from_date": {"type": "string", "format": "date"},
                            "to_date": {"type": "string", "format": "date"},
                        },
                        "required": ["query", "from_date", "to_date"],
                        "additionalProperties": False,
                    },
                },
            },
        ]

    def _get_schema(self) -> dict[str, Any]:
        """
        Read the current graph schema for the assistant prompt.
        """
        node_query = """
        CALL db.schema.nodeTypeProperties()
        YIELD nodeType, propertyName
        RETURN nodeType, propertyName
        ORDER BY nodeType, propertyName
        """
        direction_query = """
        MATCH (source)-[relationship]->(target)
        RETURN DISTINCT labels(source) AS source_labels,
               type(relationship) AS relationship_type,
               labels(target) AS target_labels,
               keys(relationship) AS property_names
        ORDER BY relationship_type, source_labels, target_labels
        """
        with get_graph_db_driver() as driver:
            node_records, _, _ = driver.execute_query(
                node_query,
                database_="neo4j",
                routing_=RoutingControl.READ,
            )
            direction_records, _, _ = driver.execute_query(
                direction_query,
                database_="neo4j",
                routing_=RoutingControl.READ,
            )

        node_types: dict[str, list[str]] = {}
        for record in node_records:
            data = record.data()
            node_types.setdefault(data["nodeType"], []).append(
                data["propertyName"]
            )

        return {
            "node_types": node_types,
            "relationships": [
                record.data()
                for record in direction_records
            ],
        }

    def _run_cypher(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """
        Run a Cypher query and return at most 25 rows as JSON.
        """
        try:
            args = CypherArguments.model_validate(arguments)
        except ValidationError:
            return {
                "status": "error",
                "message": "Supply a query and an optional parameters object.",
            }

        if _BLOCKED_CYPHER.search(args.query):
            return {
                "status": "error",
                "message": "That Cypher query contains a blocked write or administration keyword.",
            }

        try:
            with get_graph_db_driver() as driver:
                records, _, _ = driver.execute_query(
                    cast(LiteralString, args.query),
                    parameters_=args.parameters,
                    database_="neo4j",
                    routing_=RoutingControl.READ,
                )
        except Exception as e:
            return {
                "status": "error",
                "message": f"Cypher query failed: {e}",
            }

        rows = [_json_value(record.data()) for record in records[:_MAX_CYPHER_ROWS]]
        return {
            "status": "ok",
            "rows": rows,
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
            result = search_news(
                NewsQuery(
                    item_id="query-assistant",
                    query=args.query,
                    from_date=args.from_date,
                    to_date=args.to_date,
                ),
                page_size=5,
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
        if name == "run_cypher":
            return self._run_cypher(arguments)
        if name == "search_news":
            return self._search_news(arguments)
        return {"status": "error", "message": "Unknown tool name."}
