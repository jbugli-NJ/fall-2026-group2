"""
Unit tests for Montandon API schemas.
"""

# Imports

import pytest
from pydantic import ValidationError

from monty_tool.api_schemas import MontandonImpactProperties, MontandonItem


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

@pytest.mark.parametrize('field', ['title', 'description'])
def test_montandon_item_title_description_nonblank(field):
    """
    Ensures that blank title and description fields are rejected.
    """
    with pytest.raises(ValidationError):
        _item({field: " \t\n "})


def test_montandon_item_impact_without_gdacs_fields():
    """
    Ensures that EM-DAT/IFRC impact Items, which lack the GDACS-only
    `created`/`forecasted`/`severitydata`/`advisory_number` fields, still
    resolve to the impact properties model and keep `monty:impact_detail`.
    """
    item = _item({
        'roles': ['impact'],
        'monty:impact_detail': {
            'type': 'death',
            'value': 12,
            'category': 'people',
            'estimate_type': 'primary',
        },
    })
    assert isinstance(item.properties, MontandonImpactProperties)
    assert item.properties.monty_impact_detail.value == 12
    assert item.properties.severitydata is None
