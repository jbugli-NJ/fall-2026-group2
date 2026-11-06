"""
Tests for gap analysis templates.
"""

# Imports

from monty_tool.gap_analysis import answering, templates


# Tests

def test_templates_have_unique_ids():
    """
    Verify templates are unique and render with the country, hazard, and date scope.
    """
    id_set = {qt.id for qt in templates.TEMPLATES}
    question_set = {qt.question for qt in templates.TEMPLATES}
    assert len(id_set) == len(templates.TEMPLATES)
    assert len(question_set) == len(templates.TEMPLATES)
    questions = answering.construct_gap_questions({
        'country_code': 'JPN', 'hazard_code': 'GH0001',
        'from_date': '2000-01-01', 'to_date': '2025-12-31',
    }, templates.TEMPLATES)
    for question in questions:
        assert question.country_code == 'JPN'
        assert question.hazard_code == 'GH0001'
        assert 'Japan (JPN)' in question.question
        assert 'Earthquake (GH0001)' in question.question
        assert '2000-01-01 through 2025-12-31' in question.question
        assert '{' not in question.question
