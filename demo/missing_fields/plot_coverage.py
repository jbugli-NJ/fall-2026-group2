"""
Coverage by source and year: how many records each Montandon source holds
per year, drawn from the missing-field results as an SVG for the report.

Run after missing_fields.py:

    uv run python demo/missing_fields/plot_coverage.py
"""

# Imports

import csv
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use('svg')
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import BoundaryNorm, ListedColormap  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402


# Constants

RESULTS_DIR = Path(__file__).parent / 'results'
CSV_PATH = RESULTS_DIR / 'missing_fields.csv'
SVG_PATH = RESULTS_DIR / 'coverage.svg'

FIRST_YEAR = 1900
LAST_YEAR = 2026

# One record type per source: events where the source has them. USGS is
# M4.5+, as counted by missing_fields.py; PDC was not counted.
SOURCES = {
    'IBTrACS (storms)': 'ibtracs-events',
    'EM-DAT': 'emdat-events',
    'GLIDE': 'glide-events',
    'USGS (M4.5+)': 'usgs-events',
    'DesInventar': 'desinventar-events',
    'IFRC GO': 'ifrcevent-events',
    'IDMC GIDD': 'idmc-gidd-events',
    'GDACS': 'gdacs-events',
    'GFD (floods)': 'gfd-events',
    'CEMS rapid mapping': 'cems-events',
    'IDMC IDU': 'idmc-idu-events',
    'Charter': 'charter-events',
}

# Records per year, banded by order of magnitude; zero stays the surface.
BOUNDS = [1, 10, 100, 1_000, 10_000, 10**9]
BAND_LABELS = ['1–9', '10–99', '100–999', '1,000–9,999', '10,000+']
BLUES = ['#86b6ef', '#3987e5', '#256abf', '#184f95', '#0d366b']
SURFACE = '#ffffff'
INK = '#1f1f1f'
MUTED = '#6b6b6b'
GRID = '#e4e4e1'


# Helpers

def yearly_counts() -> dict[str, dict[int, int]]:
    """
    Records per year for each collection, ignoring placeholder dates.
    """
    counts: dict[str, dict[int, int]] = defaultdict(dict)
    with CSV_PATH.open() as file:
        for row in csv.DictReader(file):
            if row['year'].isdigit():
                counts[row['collection']][int(row['year'])] = int(row['records'])
    return counts


def plot() -> None:
    counts = yearly_counts()
    years = list(range(FIRST_YEAR, LAST_YEAR + 1))
    names = list(SOURCES)
    grid = [[counts[SOURCES[name]].get(year, 0) for year in years] for name in names]
    masked = [[value if value > 0 else float('nan') for value in row] for row in grid]

    plt.rcParams.update({
        'font.family': 'sans-serif',
        'font.size': 9,
        'svg.fonttype': 'none',
        'text.color': INK,
        'axes.labelcolor': MUTED,
        'xtick.color': MUTED,
        'ytick.color': INK,
    })
    figure, axes = plt.subplots(figsize=(9, 4.2))
    cmap = ListedColormap(BLUES)
    cmap.set_bad((0, 0, 0, 0))
    axes.imshow(
        masked, aspect='auto', interpolation='nearest', cmap=cmap,
        norm=BoundaryNorm(BOUNDS, cmap.N),
        extent=(FIRST_YEAR - 0.5, LAST_YEAR + 0.5, len(names) - 0.5, -0.5),
        zorder=2,
    )
    # A surface-coloured gap between rows keeps the bands separable.
    for edge in range(len(names) + 1):
        axes.axhline(edge - 0.5, color=SURFACE, linewidth=2.5, zorder=3)

    axes.set_yticks(range(len(names)), names)
    axes.set_xticks(range(FIRST_YEAR, LAST_YEAR + 1, 10))
    axes.set_xticks(range(FIRST_YEAR, LAST_YEAR + 1, 5), minor=True)
    axes.grid(axis='x', which='major', color=GRID, linewidth=0.6, zorder=1)
    axes.tick_params(axis='both', length=0)
    axes.tick_params(axis='x', which='minor', length=0)
    for spine in axes.spines.values():
        spine.set_visible(False)
    axes.set_xlabel('Year of record')

    for row, name in enumerate(names):
        total = sum(grid[row])
        axes.text(
            LAST_YEAR + 2, row, f'{total:,}', va='center', ha='left',
            fontsize=8, color=MUTED,
        )
    axes.text(LAST_YEAR + 2, -1.1, f'{FIRST_YEAR}–{LAST_YEAR}', va='center', ha='left', fontsize=8, color=MUTED)

    axes.legend(
        handles=[Patch(color=colour, label=label) for colour, label in zip(BLUES, BAND_LABELS)],
        title='Records per year', loc='upper left', bbox_to_anchor=(0, -0.13),
        ncol=len(BLUES), frameon=False, fontsize=8, title_fontsize=8,
        alignment='left', handlelength=1.2, columnspacing=1.2,
    )
    axes.set_title(
        'Montandon records per source and year', loc='left', fontsize=11, pad=12,
    )
    figure.tight_layout()
    figure.savefig(SVG_PATH, facecolor=SURFACE)
    print(f'Wrote {SVG_PATH}')


if __name__ == '__main__':
    plot()
