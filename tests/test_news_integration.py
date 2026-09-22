"""
Integration test for the Montandon-to-NewsAPI pipeline.

Setup:
1. Add the following credentials to a local .env file:
   MONTANDON_API_TOKEN=your_token
   NEWS_API_KEY=your_key

2. Run:
   uv run pytest tests/test_news_integration.py -s

The .env file should remain local and should not be committed.
"""

# Imports

import os

import pytest
from dotenv import load_dotenv

from monty_tool.api_utils import get_collection_items
from monty_tool.news.query import build_news_query
from monty_tool.news_api import search_news


# Environment

load_dotenv()


# Tests

@pytest.mark.skipif(
    not os.getenv("MONTANDON_API_TOKEN") or not os.getenv("NEWS_API_KEY"),
    reason="Requires MONTANDON_API_TOKEN and NEWS_API_KEY",
)
def test_news_pipeline():
    """
    Check that a Montandon event can be converted into a NewsAPI query
    and return a validated news search result.
    """

    item = get_collection_items("gdacs-events", max_items=1)[0]

    news_query = build_news_query(item)
    result = search_news(news_query)

    assert result.item_id == item.id
    assert result.query == news_query.query
    assert result.from_date == news_query.from_date
    assert result.to_date == news_query.to_date
    assert result.total_results >= 0
    assert isinstance(result.articles, list)

    print("\nMontandon title:", item.properties.title)
    print("Query:", result.query)
    print("Date range:", result.from_date, "to", result.to_date)
    print("Total results:", result.total_results)

    if result.articles:
        article = result.articles[0]

        print("\nFirst article:")
        print("Source:", article.source.name)
        print("Title:", article.title)
        print("Published:", article.published_at)
        print("URL:", article.url)