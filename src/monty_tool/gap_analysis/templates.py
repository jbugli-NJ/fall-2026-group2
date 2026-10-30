"""
Question templates for exploring disaster records and external evidence.
"""

from monty_tool.gap_analysis.schemas import QuestionTemplate


TEMPLATES = [
    QuestionTemplate(
        id='human_impacts',
        question=(
            'What is reported about deaths, injuries, and displacement for {title} '
            '(event ID {event_id}, starting {start_datetime})? '
            'Identify which figures are available and their estimates or uncertainty.'
        ),
    ),
    QuestionTemplate(
        id='physical_damage',
        question=(
            'What physical damage to buildings and infrastructure is reported '
            'for {title} '
            '(event ID {event_id}, starting {start_datetime})? '
            'Include quantities and descriptions where available.'
        ),
    ),
    QuestionTemplate(
        id='weather_conditions',
        question=(
            'What precipitation and temperature conditions are recorded around {title} '
            '(event ID {event_id}, starting {start_datetime})? '
            'State the measurement period and available coverage.'
        ),
    ),
]
