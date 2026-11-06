"""
Shared answer pair for gap analysis tests.
"""

import pytest

from monty_tool.gap_analysis.schemas import (
    AnswerAttempt, Contribution, GapQuestion, PairedResult,
)


@pytest.fixture
def pair():
    attempt = AnswerAttempt(
        response={'answer': 'Answer', 'tool_results': [{'result': {'status': 'ok'}}]},
        duration_seconds=0,
    )
    return PairedResult(
        question=GapQuestion(
            country_code='JPN', hazard_code='GH0001',
            template_id='impacts', question='Impacts?',
        ),
        core_context={'country_code': 'JPN', 'hazard_code': 'GH0001'},
        core=attempt,
        external=attempt,
        contribution=Contribution.QUALIFIES,
    )
