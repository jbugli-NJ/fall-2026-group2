"""
Core utilities for Montanodon API access.
"""

# Imports

import os
from typing import Any

from pydantic import ValidationError
from pystac_client import Client

from monty_tool.api_schemas import MontandonItem


# Resources

STAC_API_URL = 'https://montandon-eoapi-stage.ifrc.org/stac'

# Page size for paging through whole collections. The API accepts up to
# 10,000, but the staging server has a ~30s gateway timeout that large
# pages hit under load.
DEFAULT_PAGE_SIZE = 1_000

# STAC `fields` extension payload that strips Items down to their IDs.
COUNT_ONLY_FIELDS = {
    'include': ['id'],
    'exclude': ['geometry', 'bbox', 'properties', 'links', 'assets'],
}


# API utilities

def _get_headers() -> dict[str, str]:
    """
    Retrieve headers for the Montandon API from the environment.
    """
    api_token = os.getenv('MONTANDON_API_TOKEN')
    if api_token is None:
        raise ValueError('MONTANDON_API_TOKEN is unset!')
    auth_headers = {'Authorization': f'Bearer {api_token}'}
    return auth_headers


def get_pystac_client():
    """
    Instantiate a PySTAC client using an API token in the environment.
    """
    auth_headers = _get_headers()
    client = Client.open(STAC_API_URL, headers=auth_headers)
    return client


def get_collection_items(
    collection_id: str,
    max_items: int | None = None,
    ) -> list[MontandonItem]:
    """
    Retrieve and validate every Item in a Montandon collection.
    """
    client = get_pystac_client()
    search = client.search(collections=[collection_id], max_items=max_items)
    return [
        MontandonItem.model_validate(item.to_dict())
        for item in search.items()
    ]


def get_collection_items_raw(
    collection_id: str,
    max_items: int | None = None,
    validate: bool = False,
    ) -> list[dict[str, Any]]:
    """
    Retrieve every Item in a Montandon collection as raw STAC dictionaries.

    Unlike `get_collection_items`, nothing is dropped: fields absent from
    `MontandonItem` are kept, which is what exploratory work needs.

    With `validate=True`, each Item is additionally checked against
    `MontandonItem` and the first failure is raised with the offending
    Item's ID, while the returned dictionaries stay raw.
    """
    client = get_pystac_client()
    search = client.search(collections=[collection_id], max_items=max_items)
    items = list(search.items_as_dicts())
    if validate:
        for item in items:
            try:
                MontandonItem.model_validate(item)
            except ValidationError as error:
                raise ValueError(
                    f'Item {item.get("id")!r} in {collection_id!r} '
                    f'failed schema validation'
                ) from error
    return items


def count_collection_items(
    collection_id: str,
    client: Client | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    ) -> int:
    """
    Count the Items in a Montandon collection.

    The API does not report `numberMatched`, so this pages through the
    collection and sums `numberReturned`. Only Item IDs are requested so
    pages stay small.
    """
    if client is None:
        client = get_pystac_client()
    search = client.search(
        collections=[collection_id],
        limit=page_size,
        fields=COUNT_ONLY_FIELDS,
    )
    return sum(
        page.get('numberReturned', len(page.get('features', [])))
        for page in search.pages_as_dicts()
    )


def get_collection_counts() -> dict[str, int]:
    """
    Count the Items in every Montandon collection, keyed by collection ID.
    """
    client = get_pystac_client()
    return {
        collection.id: count_collection_items(collection.id, client=client)
        for collection in client.get_collections()
    }
