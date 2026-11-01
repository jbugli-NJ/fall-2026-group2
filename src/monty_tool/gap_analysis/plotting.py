"""
Plot external-data improvement rates by question type and model.
"""

# Imports

from typing import Any

from matplotlib import colormaps
from matplotlib.figure import Figure
from matplotlib.ticker import PercentFormatter


# Plotting


def build_summary_plot(summary: list[dict[str, Any]]) -> Figure:
    """
    Plot model improvement rates with fixed vertical offsets and evaluation counts.
    """
    records = [counts for group in summary for counts in group['results']]
    if not records:
        figure = Figure(figsize=(10, 4))
        axes = figure.subplots()
        axes.text(
            0.5, 0.5, 'No results',
            ha='center', va='center', transform=axes.transAxes,
        )
        axes.set_axis_off()
        return figure

    model_count = len(summary)
    row_spacing = max(1.0, model_count * 0.24 + 0.25)
    rows, headings = [], []
    y = 0.0
    for template in sorted({counts['template_id'] for counts in records}):
        headings.append((template.replace('_', ' ').capitalize(), y + row_spacing * 0.65))
        sources = sorted({counts['external_source'] for counts in records if counts['template_id'] == template})
        for source in sources:
            rows.append((template, source, y))
            y -= row_spacing
        y -= 0.8
    positions = {(template, source): row_y for template, source, row_y in rows}
    judges = {group['judge_model']['id'] for group in summary}
    legend_rows = (model_count + 1) // 2
    bottom_space = 1.1 + legend_rows * 0.5
    height = max(5.0, (rows[0][2] - rows[-1][2] + 1.5) * 0.65 + bottom_space + 0.7)
    figure = Figure(figsize=(13, height))
    axes = figure.subplots()
    figure.subplots_adjust(left=0.13, right=0.79, top=1 - 0.7 / height, bottom=bottom_space / height)
    axes.set_title('Where does external data improve answers?', loc='left', fontsize=17, pad=30)

    for _, _, row_y in rows:
        axes.axhline(row_y, color='#e6e9ed', linewidth=1, zorder=0)
    for heading, heading_y in headings:
        axes.text(
            -0.105, heading_y, heading, transform=axes.get_yaxis_transform(),
            fontsize=12, weight='bold', clip_on=False,
        )
    axes.text(1.04, 1.01, 'Evaluated · failed', transform=axes.transAxes, fontsize=10)

    palette = colormaps['tab10']
    markers = ('o', 'D', 's', '^', 'v', 'P', 'X')
    for index, group in enumerate(summary):
        color = palette(index % palette.N)
        offset = ((model_count - 1) / 2 - index) * 0.24
        x_values, y_values = [], []
        for counts in group['results']:
            point_y = positions[(counts['template_id'], counts['external_source'])] + offset
            evaluated = counts['evaluated']
            failed = counts['attempted'] - evaluated
            axes.text(
                1.04, point_y, f'{evaluated} · {failed}', transform=axes.get_yaxis_transform(),
                va='center', fontsize=9, color=color, clip_on=False,
            )
            if not evaluated:
                axes.text(0.02, point_y, 'No evaluations', va='center', color=color, fontsize=9)
                continue
            rate = counts['improvement_rate']
            x_values.append(rate)
            y_values.append(point_y)
            axes.annotate(
                f'{rate:.0%}', (rate, point_y),
                xytext=(-9 if rate >= 0.9 else 9, 0), textcoords='offset points',
                ha='right' if rate >= 0.9 else 'left', va='center', color=color, fontsize=10,
            )
        label = group['answer_model']['id']
        if len(judges) > 1:
            label += f"\nJudge: {group['judge_model']['id']}"
        axes.scatter(
            x_values, y_values, s=85, color=color,
            marker=markers[index % len(markers)], label=label, zorder=3,
        )

    axes.set_yticks([row[2] for row in rows], [row[1].replace('_', ' ').capitalize() for row in rows], fontsize=12)
    axes.set_ylim(rows[-1][2] - row_spacing * 0.6, headings[0][1] + row_spacing * 0.6)
    axes.set_xlim(-0.02, 1.02)
    axes.set_xticks([0, 0.25, 0.5, 0.75, 1])
    axes.xaxis.set_major_formatter(PercentFormatter(xmax=1))
    axes.set_xlabel('Answers improved by adding the external source', fontsize=12, labelpad=12)
    axes.grid(axis='x', alpha=0.18)
    axes.set_axisbelow(True)
    axes.tick_params(axis='both', length=0, pad=8)
    for spine in axes.spines.values():
        spine.set_visible(False)
    figure.legend(
        loc='lower center', ncol=2, frameon=False, fontsize=9,
        title=f'Judge: {next(iter(judges))}' if len(judges) == 1 else None,
    )
    return figure
