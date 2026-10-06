from typing import Any, cast
from monty_tool.llm.tools import QueryTools

def test_query_tools_offer_saved_news_but_not_live_search() -> None:
    tools = QueryTools()
    names = {
        cast(dict[str, Any], definition["function"])["name"]
        for definition in tools.definitions
    }

    assert "get_event_news" in names
    assert "search_news" not in names
    assert tools.execute("search_news", {})["status"] == "error"