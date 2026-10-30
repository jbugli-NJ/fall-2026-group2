"""
Tests for the contribution summary chart.
"""

from matplotlib.patches import Rectangle

from monty_tool.gap_analysis.plotting import build_summary_plot
from monty_tool.gap_analysis.schemas import Contribution


def test_chart_includes_every_category_and_failed_pairs():
    """
    Verify chart segment sizes match category and failure counts.
    """
    categories = {category.value: index for index, category in enumerate(Contribution, 1)}
    total = sum(categories.values())
    figure = build_summary_plot(
        {
            'impacts': {
                'categories': categories, 'evaluated': total, 'attempted': total + 2,
            },
        }
    )
    assert [
        bar.get_width() for bar in figure.axes[0].patches if isinstance(bar, Rectangle)
    ] == [*categories.values(), 2]
