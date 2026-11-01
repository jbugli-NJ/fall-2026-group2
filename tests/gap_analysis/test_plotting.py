"""
Tests for the improvement chart.
"""

# Imports

from monty_tool.gap_analysis.plotting import build_summary_plot


# Tests


def test_chart_preserves_rates_and_separates_models():
    """
    Separate overlapping models, preserve zero rates, and omit failed evaluations.
    """
    summary = [
        {
            'answer_model': {'id': 'model-a'}, 'judge_model': {'id': 'judge-a'},
            'results': [
                {'template_id': 'human_impacts', 'external_source': 'news',
                 'attempted': 12, 'evaluated': 10, 'improvement_rate': 0.6},
                {'template_id': 'human_impacts', 'external_source': 'weather',
                 'attempted': 3, 'evaluated': 0, 'improvement_rate': None},
            ],
        },
        {
            'answer_model': {'id': 'model-b'}, 'judge_model': {'id': 'judge-b'},
            'results': [
                {'template_id': 'human_impacts', 'external_source': 'news',
                 'attempted': 5, 'evaluated': 5, 'improvement_rate': 0.6},
                {'template_id': 'human_impacts', 'external_source': 'weather',
                 'attempted': 8, 'evaluated': 8, 'improvement_rate': 0.0},
            ],
        },
    ]
    figure = build_summary_plot(summary)
    axes = figure.axes[0]
    first, second = [points.get_offsets() for points in axes.collections]
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
