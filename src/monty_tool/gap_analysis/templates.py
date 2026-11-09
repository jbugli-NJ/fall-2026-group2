"""
Questions for EAP planning by country and hazard, grounded in historical evidence.

Forecast triggers, operational plans, capacity assessments, evaluation frameworks,
and approvals need information beyond the available sources.
"""

from monty_tool.gap_analysis.schemas import QuestionTemplate


TEMPLATES = [
    QuestionTemplate(
        id='introduction_and_context',
        question=(
            'What do past {hazard} events in {country} tell us about '
            'where and when this hazard occurs? Include relevant weather evidence.'
        ),
    ),
    QuestionTemplate(
        id='risk_and_vulnerability_analysis',
        question=(
            'For {hazard} in {country}, which areas, people, and '
            'infrastructure have been affected? '
            'Which groups are reported as especially vulnerable?'
        ),
    ),
    QuestionTemplate(
        id='stakeholders_and_coordination',
        question=(
            'Which organizations have responded to {hazard} in {country}, '
            'and what roles and coordination are documented?'
        ),
    ),
    QuestionTemplate(
        id='early_action_selection',
        question=(
            'For {hazard} in {country}, what past actions and reported '
            'needs could inform early action choices? '
            'Which actions were taken before impact?'
        ),
    ),
    QuestionTemplate(
        id='budget_and_financing',
        question=(
            'What do past appeals for {hazard} in {country} show about '
            'funding requested, funding received, and people targeted? '
            'Was any funding specifically for early action?'
        ),
    ),
]
