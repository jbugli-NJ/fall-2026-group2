"""
Shared answer pair for gap analysis tests.
"""

# Imports

import pytest

from monty_tool.llm.tools import GraphSearchArguments

from monty_tool.gap_analysis.schemas import (
    AnswerAttempt, Contribution, GapQuestion, GapSelection, PairedResult,
)


# Fixtures

@pytest.fixture
def pair():
    attempt = AnswerAttempt(
        answer='Answer', input_tokens=0, output_tokens=0,
        duration_seconds=0,
    )
    return PairedResult(
        external_source='shakemap_footprints',
        question=GapQuestion(
            country_code='JPN', hazard_name='Earthquake',
            template_id='impacts', question='Impacts?',
        ),
        selection=GapSelection(
            filters=GraphSearchArguments(country_code='JPN'),
            hazard_name='Earthquake',
        ),
        evidence_s3_uri='s3://test-bucket/gap_analysis/evidence/context.json',
        core=attempt,
        external=attempt,
        contribution=Contribution.IMPROVEMENT,
    )
