"""
Tests for gap analysis templates.
"""

# Imports

import pytest

from monty_tool.gap_analysis import answering, retrieval, templates
from monty_tool.gap_analysis.schemas import GapSelection


# Tests

def test_hazard_name_mapping():
    """
    Resolve table and fallback codes, combining cyclone classifications.
    """
    names = retrieval.hazard_code_name_map()
    cyclone_codes = (
        'MH0306', 'MH0030', 'MH0307', 'MH0031', 'MH0308', 'MH0032',
        'MH0309', 'MH0057', 'TC', 'EC', 'nat-met-sto-tro', 'nat-met-sto-ext',
    )
    assert {names[code] for code in cyclone_codes} == {'Cyclone'}
    assert names['GH0001'] == 'Earthquake'
    assert names['GH009'] == 'Volcanic eruption'
    assert 'unknown-code' not in names


@pytest.mark.parametrize('hazard_name', ['Cyclone', 'Earthquake', 'Volcanic eruption'])
def test_templates_have_unique_ids(hazard_name):
    """
    Verify templates are unique and render with the country, hazard, and date scope.
    """
    id_set = {qt.id for qt in templates.TEMPLATES}
    question_set = {qt.question for qt in templates.TEMPLATES}
    assert len(id_set) == len(templates.TEMPLATES)
    assert len(question_set) == len(templates.TEMPLATES)
    questions = answering.construct_gap_questions(GapSelection.model_validate({
        'filters': {
            'country_code': 'JPN',
            'from_date': '2000-01-01',
            'to_date': '2025-12-31',
        },
        'hazard_name': hazard_name,
    }), templates.TEMPLATES)
    assert len(questions) == len(templates.TEMPLATES)
    for question in questions:
        assert question.country_code == 'JPN'
        assert question.hazard_name == hazard_name
        assert 'Japan (JPN)' in question.question
        assert hazard_name in question.question
        assert '2000-01-01 through 2025-12-31' in question.question
        assert '{' not in question.question
