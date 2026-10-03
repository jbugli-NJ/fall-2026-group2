# The front-end library is run via `uv run --with chainlit` rather than
# added to pyproject.toml, so CI never installs it and pyright cannot
# resolve its attributes. Suppressed here rather than in the project
# config, to keep the spike self-contained.
# pyright: reportAttributeAccessIssue=false, reportMissingImports=false

"""
Spike: a Chainlit front end for QueryAssistant, for issue #44.

The question this exists to answer is not "can we put a chat box on it"
but "can the UI show where an answer came from". A Red Cross reader has
to be able to see the graph records behind a claim, so every tool call
is rendered as an inspectable step and the record IDs are listed under
the answer.

Runs in two modes:

    stub (default)  Replays a scripted exchange built from real GDACS
                    records in fixtures.json. Needs no Neo4j, no model
                    download, and no GPU, so the UI can be evaluated
                    before the stack is stood up.

    real            Drives the actual QueryAssistant. Requires Neo4j on
                    bolt://localhost:7687 with the graph populated, and
                    downloads Qwen3-1.7B on first run.

    uv run --with chainlit chainlit run spikes/chainlit/app.py -w
    MONTY_UI_REAL=1 uv run --with chainlit chainlit run spikes/chainlit/app.py
"""

# Imports

import asyncio
import json
import os
import socket
from pathlib import Path
from typing import Any

import chainlit as cl


# Constants

FIXTURES_PATH = Path(__file__).parent / 'fixtures.json'

# Rows shown inline before the step collapses to a count; a graph query
# can return far more than a reader wants unfolded by default.
MAX_ROWS_SHOWN = 5

BOLT_HOST, BOLT_PORT = 'localhost', 7687


# Mode selection

def graph_is_up() -> bool:
    """
    Whether something is listening on the Neo4j bolt port.
    """
    with socket.socket() as probe:
        probe.settimeout(0.3)
        return probe.connect_ex((BOLT_HOST, BOLT_PORT)) == 0


def use_real_assistant() -> bool:
    """
    Real mode is opt-in, and still refuses when the graph is absent:
    loading a 3.4GB model to then fail every tool call helps nobody.
    """
    if os.environ.get('MONTY_UI_REAL') != '1':
        return False
    return graph_is_up()


# Rendering

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

    # A list of plain values (country codes, hazard codes) reads fine in
    # a cell once joined. Only nested records -- an impact list, an
    # embedding -- genuinely need the JSON fallback.
    def cell(value: Any) -> str:
        if isinstance(value, list) and all(
            not isinstance(item, (dict, list)) for item in value
        ):
            return ', '.join(str(item) for item in value)
        return str(value)

    tabular = all(
        isinstance(row, dict) and all(
            not isinstance(value, dict)
            and not (
                isinstance(value, list)
                and any(isinstance(item, (dict, list)) for item in value)
            )
            for value in row.values()
        )
        for row in shown
    )
    if not tabular:
        body = json.dumps(shown, indent=2, default=str)
        return f"```json\n{body[:3000]}\n```"

    headers = list(shown[0])
    lines = [
        '| ' + ' | '.join(headers) + ' |',
        '|' + '|'.join(' --- ' for _ in headers) + '|',
    ]
    for row in shown:
        cells = [cell(row.get(h, ''))[:60].replace('|', '\\|') for h in headers]
        lines.append('| ' + ' | '.join(cells) + ' |')

    if len(rows) > MAX_ROWS_SHOWN:
        lines.append(f'\n*{len(rows) - MAX_ROWS_SHOWN} more row(s) not shown.*')
    return '\n'.join(lines)


def provenance(tool_results: list[dict[str, Any]]) -> str:
    """
    Collect the record IDs an answer was built from.

    This is the part a stakeholder checks: the claim is only as good as
    the records behind it, so they are named rather than implied.
    """
    ids: list[str] = []

    def collect(value: Any) -> None:
        # Impact records arrive nested inside their event row, and they
        # are the evidence for any figure the answer quotes, so the walk
        # has to reach them rather than stopping at the top level.
        if isinstance(value, dict):
            for key, inner in value.items():
                if key in ('event_id', 'impact_id', 'item_id'):
                    if isinstance(inner, str) and inner not in ids:
                        ids.append(inner)
                else:
                    collect(inner)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    for entry in tool_results:
        collect(entry.get('result', {}).get('rows') or [])
    if not ids:
        return ''
    listed = '\n'.join(f'- `{record}`' for record in ids[:12])
    extra = f'\n- *(+{len(ids) - 12} more)*' if len(ids) > 12 else ''
    return f'\n\n---\n**Records consulted ({len(ids)}):**\n{listed}{extra}'


async def show_step(name: str, arguments: dict, result: dict) -> None:
    """
    Render one tool call as an expandable step.
    """
    async with cl.Step(name=name, type='tool') as step:
        step.input = f"```json\n{json.dumps(arguments, indent=2)}\n```"
        step.output = render_rows(result)


# Stub mode

async def run_stub(question: str) -> None:
    """
    Replay the scripted exchange, pacing the steps so the sequence of
    graph queries is legible rather than appearing all at once.
    """
    fixtures = json.loads(FIXTURES_PATH.read_text(encoding='utf-8'))
    tool_results = []

    for step in fixtures['steps']:
        await asyncio.sleep(0.6)
        await show_step(step['name'], step['arguments'], step['result'])
        tool_results.append({'call': step, 'result': step['result']})

    await asyncio.sleep(0.4)
    await cl.Message(
        content=fixtures['answer'] + provenance(tool_results)
    ).send()


# Real mode

async def run_real(question: str) -> None:
    """
    Drive the real assistant, streaming each tool call as it happens.

    `QueryAssistant.ask` is synchronous and owns its own tool loop, so
    it runs in a worker thread while its tool executions are forwarded
    back onto the event loop through a queue. Wrapping the bound
    `execute` avoids touching `llm/client.py`, which is Jehan's.
    """
    assistant = cl.user_session.get('assistant')
    if assistant is None:
        async with cl.Step(name='loading Qwen3-1.7B', type='run') as step:
            from monty_tool.llm.client import QueryAssistant
            assistant = await asyncio.to_thread(QueryAssistant)
            step.output = 'Model loaded.'
        cl.user_session.set('assistant', assistant)

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    inner_execute = assistant.tools.execute

    def execute(name: str, arguments: dict) -> dict:
        result = inner_execute(name, arguments)
        loop.call_soon_threadsafe(queue.put_nowait, (name, arguments, result))
        return result

    assistant.tools.execute = execute
    try:
        task = asyncio.create_task(asyncio.to_thread(assistant.ask, question))
        while not task.done() or not queue.empty():
            try:
                name, arguments, result = await asyncio.wait_for(
                    queue.get(), timeout=0.2,
                )
            except asyncio.TimeoutError:
                continue
            await show_step(name, arguments, result)
        response = await task
    finally:
        assistant.tools.execute = inner_execute

    await cl.Message(
        content=response['answer'] + provenance(response.get('tool_results', []))
    ).send()


# Chainlit entrypoints

@cl.on_chat_start
async def start() -> None:
    real = use_real_assistant()
    cl.user_session.set('real', real)

    if real:
        banner = 'Connected to the local graph. Answers come from Neo4j and Qwen3-1.7B.'
    elif os.environ.get('MONTY_UI_REAL') == '1':
        banner = (
            '**Real mode requested but no graph found** on '
            f'`bolt://{BOLT_HOST}:{BOLT_PORT}` - falling back to the '
            'recorded exchange below.'
        )
    else:
        banner = (
            '**Stub mode.** Replaying a recorded exchange built from real '
            'GDACS records, so the provenance view can be judged without '
            'standing up Neo4j. Set `MONTY_UI_REAL=1` with the graph '
            'running to drive the real assistant.'
        )

    await cl.Message(
        content=(
            '### Montandon query assistant — UI spike\n\n'
            f'{banner}\n\n'
            'Every graph query appears as a step you can expand, and the '
            'records behind the answer are listed underneath it.'
        )
    ).send()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    if cl.user_session.get('real'):
        await run_real(message.content)
    else:
        await run_stub(message.content)
