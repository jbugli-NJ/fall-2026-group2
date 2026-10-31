"""
Tests for answer attempt failure counts.
"""

from monty_tool.gap_analysis.schemas import AnswerAttempt


def test_tool_failure_count():
    """
    Verify error and limit_reached tool results count as failures.
    """
    attempt = AnswerAttempt(
        duration_seconds=0,
        response={
            'answer': 'Answer',
            'tool_results': [
                {'result': {'status': status}}
                for status in ('ok', 'error', 'limit_reached')
            ],
        },
    )
    assert attempt.tool_failures == 2
