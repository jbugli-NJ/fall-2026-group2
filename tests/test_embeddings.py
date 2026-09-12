"""
Tests for embedding generation.
"""

# Imports

from unittest.mock import Mock

import numpy as np

from monty_tool.api_schemas import MontandonItem
from monty_tool.embeddings.generate import (
    _generate_item_embeddings,
)
from monty_tool.embeddings.schemas import MontandonItemEmbeddings


# Test object helpers

def _item(properties: dict) -> MontandonItem:
    return MontandonItem.model_validate({
        'id': 'event-1',
        'collection': 'events',
        'bbox': [0, 0, 1, 1],
        'geometry': {'type': 'Point', 'coordinates': [0, 0]},
        'links': [],
        'properties': {
            'roles': ['event'],
            'title': ' Flood ',
            'description': ' River flooding. ',
            'keywords': ['river', 'flood'],
            'datetime': '2026-09-01T00:00:00Z',
            'start_datetime': '2026-09-01T00:00:00Z',
            'end_datetime': '2026-09-02T00:00:00Z',
            'monty:corr_id': 'correlation-1',
            'monty:hazard_codes': ['FL'],
            'monty:country_codes': ['USA'],
            'monty:src_event_id': 'source-1',
            'monty:episode_number': 1,
            **properties,
        },
    })


# Tests

def test_generate_item_embeddings_uses_item_inputs():
    """
    Checks to make sure that the embedding method is called as expected
    given inputs from the test item.
    """
    item = _item({})
    model = Mock()
    model.encode.return_value = np.array([[0.0, 1.0], [1.0, 0.0], [2.0, 1.0]])
    result = _generate_item_embeddings(item, model)
    model.encode.assert_called_once_with(
        ['Flood', 'River flooding.', 'river\nflood'],
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    assert result == MontandonItemEmbeddings(
        item_id='event-1',
        title=[0.0, 1.0],
        description=[1.0, 0.0],
        keywords=[2.0, 1.0],
    )
