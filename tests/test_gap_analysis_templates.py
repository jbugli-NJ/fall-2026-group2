"""
Tests for gap analysis templates.
"""

# Imports

from monty_tool.gap_analysis import templates


# Tests

def test_templates_have_unique_ids():
    """
    Confirm that the IDs used for question templates are unique.
    """
    id_set = set([qt.id for qt in templates.TEMPLATES])
    question_set = set([qt.question for qt in templates.TEMPLATES])
    assert len(id_set) == len(templates.TEMPLATES)
    assert len(question_set) == len(templates.TEMPLATES)
    for q in question_set:
        assert q.count('{') > 0
        assert q.count('{') == q.count('}')
