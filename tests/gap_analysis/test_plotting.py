"""
Tests for the improvement chart.
"""

# Imports

import numpy as np

from monty_tool.gap_analysis.plotting import build_summary_plot
from monty_tool.gap_analysis.schemas import ModelSummary


# Tests


def test_chart_preserves_rates_and_separates_models():
    """
    Separate overlapping models, preserve zero rates, and omit failed evaluations.
    """
    summary: list[ModelSummary] = [
        {
            'answer_model': {'id': 'model-a'},
            'judge_model': {'id': 'judge-a'},
            'results': [
                {
                    'template_id': 'human_impacts',
                    'external_source': 'news',
                    'attempted': 12,
                    'evaluated': 10,
                    'improvement_rate': 0.6,
                    'improved': 6,
                    'answer_failures': 2,
                    'judge_failures': 0,
                    'categories': {
                        'improvement': 6,
                        'no_change': 4,
                        'regression': 0,
                    },
                },
                {
                    'template_id': 'human_impacts',
                    'external_source': 'weather',
                    'attempted': 3,
                    'evaluated': 0,
                    'improvement_rate': None,
                    'improved': 0,
                    'answer_failures': 3,
                    'judge_failures': 0,
                    'categories': {
                        'improvement': 0,
                        'no_change': 0,
                        'regression': 0,
                    },
                },
            ],
        },
        {
            'answer_model': {'id': 'model-b'},
            'judge_model': {'id': 'judge-b'},
            'results': [
                {
                    'template_id': 'human_impacts',
                    'external_source': 'news',
                    'attempted': 5,
                    'evaluated': 5,
                    'improvement_rate': 0.6,
                    'improved': 3,
                    'answer_failures': 0,
                    'judge_failures': 0,
                    'categories': {
                        'improvement': 3,
                        'no_change': 2,
                        'regression': 0,
                    },
                },
                {
                    'template_id': 'human_impacts',
                    'external_source': 'weather',
                    'attempted': 8,
                    'evaluated': 8,
                    'improvement_rate': 0.0,
                    'improved': 0,
                    'answer_failures': 0,
                    'judge_failures': 0,
                    'categories': {
                        'improvement': 0,
                        'no_change': 8,
                        'regression': 0,
                    },
                },
            ],
        },
    ]
    figure = build_summary_plot(summary)
    axes = figure.axes[0]
    first, second = [np.asarray(points.get_offsets()) for points in axes.collections]
    assert list(first[:, 0]) == [0.6]
    assert list(second[:, 0]) == [0.6, 0.0]
    assert first[0, 1] > second[0, 1]
    texts = [text.get_text() for text in axes.texts]
    assert '10 · 2' in texts
    assert '0 · 3' in texts
    assert 'No evaluations' in texts
    assert [text.get_text() for text in figure.legends[0].get_texts()] == [
        'model-a\nJudge: judge-a', 'model-b\nJudge: judge-b',
    ]


def test_empty_chart():
    """
    Render an empty summary without model points.
    """
    axes = build_summary_plot([]).axes[0]
    assert not axes.collections
    assert axes.texts[0].get_text() == 'No results'
