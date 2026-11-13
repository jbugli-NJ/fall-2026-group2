"""
Tests for selections and answer attempt metadata.
"""

# Imports

from datetime import date

import pytest
from pydantic import ValidationError

from monty_tool.gap_analysis.schemas import AnswerAttempt, GapSelection


# Tests

@pytest.mark.parametrize('field', ['input_tokens', 'output_tokens'])
def test_answer_attempt_rejects_negative_token_counts(field):
    """
    Generation metadata must contain valid token counts.
    """
    with pytest.raises(ValidationError):
        AnswerAttempt.model_validate({
            'duration_seconds': 0,
            field: -1,
        })


def test_selection_reuses_graph_filter_validation():
    """
    Selected countries and date bounds use the existing graph-search validators.
    """
    selection = GapSelection.model_validate({
        'filters': {
            'country_code': 'Japan',
            'from_date': '2020-01-01',
            'to_date': '2020-12-31',
        },
        'hazard_name': 'Earthquake',
    })
    assert selection.filters.country_code == 'JPN'
    assert selection.filters.from_date == date(2020, 1, 1)
    assert selection.filters.to_date == date(2020, 12, 31)
    assert GapSelection.model_validate_json(selection.model_dump_json()) == selection
    with pytest.raises(ValidationError, match='from_date must be on or before to_date'):
        GapSelection.model_validate({
            'filters': {
                'country_code': 'JPN',
                'from_date': '2021-01-01',
                'to_date': '2020-01-01',
            },
            'hazard_name': 'Earthquake',
        })


@pytest.mark.parametrize('missing', ['filters', 'hazard_name'])
def test_selection_requires_filters_and_hazard(missing):
    """
    The selection boundary requires explicit graph filters and a hazard.
    """
    payload = {
        'filters': {'country_code': 'JPN'},
        'hazard_name': 'Earthquake',
    }
    del payload[missing]
    with pytest.raises(ValidationError):
        GapSelection.model_validate(payload)
