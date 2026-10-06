"""
Framework-agnostic rendering for the UI spikes.

Kept separate from any front end because the question issue #44 asks is
which front end to use: the way tool results and provenance are
presented has to stay identical across the options being compared, or
the comparison is between two renderers rather than two frameworks.
"""

# Imports

import json
from typing import Any


# Constants

# Rows shown inline before the rest are summarised; a graph query can
# return far more than a reader wants unfolded by default.
MAX_ROWS_SHOWN = 5

# Keys whose values name a source record, at any depth.
ID_KEYS = ('event_id', 'impact_id', 'item_id')


# Rendering

def _cell(value: Any) -> str:
    """
    Render one table cell, joining lists of plain values.
    """
    if isinstance(value, list) and all(
        not isinstance(item, (dict, list)) for item in value
    ):
        return ', '.join(str(item) for item in value)
    return str(value)


def _is_tabular(rows: list) -> bool:
    """
    Whether rows can be shown as a table.

    A list of plain values (country codes, hazard codes) reads fine in a
    cell once joined. Only nested records -- an impact list, an
    embedding -- genuinely need the JSON fallback.
    """
    return all(
        isinstance(row, dict) and all(
            not isinstance(value, dict)
            and not (
                isinstance(value, list)
                and any(isinstance(item, (dict, list)) for item in value)
            )
            for value in row.values()
        )
        for row in rows
    )


def render_rows(result: dict[str, Any]) -> str:
    """
    Render a tool result as Markdown, preferring a table for row lists.

    Tool results are the evidence for the answer, so they are shown as
    data rather than summarised into prose.
    """
    if result.get('status') == 'error':
        return f"**Error:** {result.get('message', 'unknown error')}"

    rows = result.get('rows')
    if not isinstance(rows, list) or not rows:
        return f"```json\n{json.dumps(result, indent=2, default=str)[:2000]}\n```"

    shown = rows[:MAX_ROWS_SHOWN]
    if not _is_tabular(shown):
        body = json.dumps(shown, indent=2, default=str)
        return f"```json\n{body[:3000]}\n```"

    headers = list(shown[0])
    lines = [
        '| ' + ' | '.join(headers) + ' |',
        '|' + '|'.join(' --- ' for _ in headers) + '|',
    ]
    for row in shown:
        cells = [_cell(row.get(h, ''))[:60].replace('|', '\\|') for h in headers]
        lines.append('| ' + ' | '.join(cells) + ' |')

    if len(rows) > MAX_ROWS_SHOWN:
        lines.append(f'\n*{len(rows) - MAX_ROWS_SHOWN} more row(s) not shown.*')
    return '\n'.join(lines)


def collect_record_ids(tool_results: list[dict[str, Any]]) -> list[str]:
    """
    Every source record ID an answer was built from, in first-seen order.

    Impact records arrive nested inside their event row, and they are the
    evidence for any figure an answer quotes, so the walk has to reach
    them rather than stopping at the top level.
    """
    ids: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, inner in value.items():
                if key in ID_KEYS:
                    if isinstance(inner, str) and inner not in ids:
                        ids.append(inner)
                else:
                    walk(inner)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for entry in tool_results:
        walk(entry.get('result', {}).get('rows') or [])
    return ids


def provenance(tool_results: list[dict[str, Any]]) -> str:
    """
    The record IDs behind an answer, as a Markdown block.

    This is the part a stakeholder checks: a claim is only as good as the
    records behind it, so they are named rather than implied.
    """
    ids = collect_record_ids(tool_results)
    if not ids:
        return ''
    listed = '\n'.join(f'- `{record}`' for record in ids[:12])
    extra = f'\n- *(+{len(ids) - 12} more)*' if len(ids) > 12 else ''
    return f'\n\n---\n**Records consulted ({len(ids)}):**\n{listed}{extra}'


def id_kinds(tool_results: list[dict[str, Any]]) -> dict[str, int]:
    """
    Count consulted records by kind.

    Makes the gap legible when an answer discusses impacts while only
    event records were ever retrieved.
    """
    counts: dict[str, int] = {}
    for record in collect_record_ids(tool_results):
        kind = 'impact' if '-impact-' in record else (
            'event' if '-event-' in record else 'other'
        )
        counts[kind] = counts.get(kind, 0) + 1
    return counts
