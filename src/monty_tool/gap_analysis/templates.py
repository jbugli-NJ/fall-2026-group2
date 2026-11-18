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
            'Provide context on past {hazard} events in {country} using available data. '
            'Where and when has this hazard occurred?'
        ),
    ),
    QuestionTemplate(
        id='risk_and_vulnerability_analysis',
        question=(
            'For {hazard} in {country}, what does the available data suggest '
            'about which groups are especially vulnerable?'
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
            'If no actions or needs are clear from available data, say so.'
        ),
    ),
    QuestionTemplate(
        id='budget_and_financing',
        question=(
            'What do past appeals for {hazard} in {country} show about '
            'funding requested, funding received, and people targeted? '
            'If no appeal data is provided, say so.'
        ),
    ),
]
