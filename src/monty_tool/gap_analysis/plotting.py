"""
SVG summary chart of external data contributions by question template.
"""

from typing import Any

from matplotlib import colormaps
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator

from monty_tool.gap_analysis.schemas import Contribution


# Plotting


def build_summary_plot(summary: dict[str, Any]) -> Figure:
    """
    Plot classification and failure counts for each question template.
    """
    figure = Figure(figsize=(11, max(4, len(summary) * 0.6 + 2)))
    axes = figure.subplots()
    axes.set_title('External data contribution by question template')
    axes.set_xlabel('Question pairs (all saved attempts)')
    axes.xaxis.set_major_locator(MaxNLocator(integer=True))
    axes.set_axisbelow(True)
    axes.grid(axis='x', alpha=0.2)
    if not summary:
        axes.text(
            0.5, 0.5, 'No completed question pairs',
            ha='center', va='center', transform=axes.transAxes,
        )
        axes.set_yticks([])
        return figure

    templates = list(summary)
    series = {
        category.value: [
            counts['categories'][category.value] for counts in summary.values()
        ]
        for category in Contribution
    }
    series['failed_pair'] = [
        counts['attempted'] - counts['evaluated'] for counts in summary.values()
    ]
    palette = colormaps['Pastel2'].resampled(len(Contribution))
    left = [0] * len(templates)
    for index, (category, values) in enumerate(series.items()):
        bars = axes.barh(
            templates,
            values,
            left=left,
            label=category.replace('_', ' ').capitalize(),
            color='#bdbdbd' if category == 'failed_pair' else palette(index),
        )
        axes.bar_label(
            bars,
            labels=[str(value) if value else '' for value in values],
            label_type='center',
            color='#202020',
        )
        left = [offset + value for offset, value in zip(left, values)]
    axes.invert_yaxis()
    figure.legend(loc='lower center', ncol=4, frameon=False, fontsize=9)
    figure.text(
        0.5, 0.17, 'Counts include repeated questions across saved invocations.',
        ha='center', fontsize=9,
    )
    figure.tight_layout(rect=(0, 0.2, 1, 1))
    return figure
