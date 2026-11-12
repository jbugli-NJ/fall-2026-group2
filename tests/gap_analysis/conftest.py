"""
Shared answer pair for gap analysis tests.
"""

# Imports

import pytest

from monty_tool.gap_analysis.schemas import (
    AnswerAttempt, Contribution, GapQuestion, PairedResult,
)


# Fixtures

@pytest.fixture
def pair():
    attempt = AnswerAttempt(
        response={'answer': 'Answer', 'input_tokens': 0, 'output_tokens': 0, 'tool_results': [{'result': {'status': 'ok'}}]},
        duration_seconds=0,
    )
    return PairedResult(
        external_source='shakemap_footprints',
        question=GapQuestion(
            country_code='JPN', hazard_name='Earthquake',
            template_id='impacts', question='Impacts?',
        ),
        core_context={'country_code': 'JPN', 'hazard_name': 'Earthquake'},
        core=attempt,
        external=attempt,
        contribution=Contribution.IMPROVEMENT,
    )
