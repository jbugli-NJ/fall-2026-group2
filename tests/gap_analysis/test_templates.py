"""
Tests for gap analysis templates.
"""

# Imports

import pytest

from monty_tool.gap_analysis import answering, templates


# Tests

@pytest.mark.parametrize('hazard_code, hazard_name', [
    ('GH0001', 'Earthquake'),
    ('GH0002', 'Earthquake ground shaking'),
    ('GH009', 'Volcanic eruption'),
    ('nat-gem-ear-gro', 'Earthquake ground movement'),
    ('tec-ind-col-col', 'Industrial collapse'),
    ('unknown-code', None),
])
def test_templates_have_unique_ids(hazard_code, hazard_name):
    """
    Verify templates are unique and render with the country, hazard, and date scope.
    """
    id_set = {qt.id for qt in templates.TEMPLATES}
    question_set = {qt.question for qt in templates.TEMPLATES}
    assert len(id_set) == len(templates.TEMPLATES)
    assert len(question_set) == len(templates.TEMPLATES)
    questions = answering.construct_gap_questions({
        'country_code': 'JPN', 'hazard_code': hazard_code,
        'from_date': '2000-01-01', 'to_date': '2025-12-31',
    }, templates.TEMPLATES)
    if hazard_name is None:
        assert questions == []
        return
    assert len(questions) == len(templates.TEMPLATES)
    for question in questions:
        assert question.country_code == 'JPN'
        assert question.hazard_code == hazard_code
        assert 'Japan (JPN)' in question.question
        hazard = f'{hazard_name} ({hazard_code})'
        assert hazard in question.question
        assert '2000-01-01 through 2025-12-31' in question.question
        assert '{' not in question.question
