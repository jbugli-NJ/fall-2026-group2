"""
Tests for gap analysis templates.
"""

# Imports

from monty_tool.gap_analysis import templates


# Tests

def test_templates_have_unique_ids():
    """
    Verify template IDs and questions are unique and question braces are balanced.
    """
    id_set = {qt.id for qt in templates.TEMPLATES}
    question_set = {qt.question for qt in templates.TEMPLATES}
    assert len(id_set) == len(templates.TEMPLATES)
    assert len(question_set) == len(templates.TEMPLATES)
    for q in question_set:
        assert q.count('{') > 0
        assert q.count('{') == q.count('}')
