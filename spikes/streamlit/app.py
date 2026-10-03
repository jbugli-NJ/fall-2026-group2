# The front-end library is run via `uv run --with streamlit` rather than
# added to pyproject.toml, so CI never installs it and pyright cannot
# resolve its attributes. Suppressed here rather than in the project
# config, to keep the spike self-contained.
# pyright: reportAttributeAccessIssue=false, reportMissingImports=false

"""
Spike: a Streamlit front end for QueryAssistant, for issue #44.

Chainlit was the first attempt (see spikes/chainlit/) but cannot serve
its own frontend assets on Python 3.14: starlette's FileResponse raises
anyio NoEventLoopError inside Chainlit's request path, on every 2.9-2.12
release tested, so the page loads blank. Streamlit runs on Tornado
rather than starlette/anyio and is unaffected.

The point of either is the same: show where an answer came from. Every
graph query is rendered with its arguments and returned rows, and the
record IDs behind the answer are listed underneath, so a reader can see
when prose outruns its evidence.

    uv run --with streamlit streamlit run spikes/streamlit/app.py
    MONTY_UI_REAL=1 uv run --with streamlit streamlit run spikes/streamlit/app.py
"""

# Imports

import json
import os
import socket
import sys
import time
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from render import id_kinds, provenance, render_rows  # noqa: E402


# Constants

FIXTURES_PATH = Path(__file__).resolve().parent.parent / 'chainlit' / 'fixtures.json'
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
    Real mode is opt-in, and still refuses when the graph is absent.
    """
    return os.environ.get('MONTY_UI_REAL') == '1' and graph_is_up()


@st.cache_resource(show_spinner=False)
def load_assistant():
    """
    Load the model once per server process, not once per rerun.

    Streamlit re-executes this script on every interaction, so an
    uncached QueryAssistant would reload 3.4GB of weights per message.
    """
    from monty_tool.llm.client import QueryAssistant
    return QueryAssistant()


# Rendering

def show_step(name: str, arguments: dict, result: dict) -> None:
    """
    Render one tool call as an expandable step.
    """
    rows = result.get('rows') or []
    label = f'{name} — {len(rows)} row(s)'
    with st.expander(label, expanded=False):
        st.caption('arguments')
        st.code(json.dumps(arguments, indent=2), language='json')
        st.caption('returned')
        st.markdown(render_rows(result))


def show_provenance(tool_results: list) -> None:
    """
    List the records behind an answer, and flag an evidence gap.
    """
    text = provenance(tool_results)
    if not text:
        st.warning('No source records were retrieved for this answer.')
        return

    kinds = id_kinds(tool_results)
    st.markdown(text)

    # The failure this UI exists to catch: prose about impacts when no
    # impact record was ever read.
    if kinds.get('impact', 0) == 0 and kinds.get('event', 0) > 0:
        st.warning(
            f"{kinds.get('event', 0)} event record(s) consulted and "
            '**no impact records**. Any impact described above is not '
            'supported by a retrieved record.'
        )


# Answering

def answer_stub(question: str) -> tuple[str, list]:
    """
    Replay the recorded exchange, pacing steps so the sequence is legible.
    """
    fixtures = json.loads(FIXTURES_PATH.read_text(encoding='utf-8'))
    tool_results = []
    for step in fixtures['steps']:
        time.sleep(0.5)
        show_step(step['name'], step['arguments'], step['result'])
        tool_results.append({'call': step, 'result': step['result']})
    return fixtures['answer'], tool_results


def answer_real(question: str) -> tuple[str, list]:
    """
    Drive the real assistant, rendering each tool call as it is made.

    Wrapping the bound `execute` keeps the live step rendering out of
    llm/client.py. Streamlit runs synchronously, so a wrapper can draw
    straight into the page with no event-loop plumbing.
    """
    assistant = load_assistant()
    inner = assistant.tools.execute

    def execute(name: str, arguments: dict) -> dict:
        result = inner(name, arguments)
        show_step(name, arguments, result)
        return result

    assistant.tools.execute = execute
    try:
        response = assistant.ask(question)
    finally:
        assistant.tools.execute = inner
    return response['answer'], response.get('tool_results', [])


# Page

st.set_page_config(page_title='Montandon query assistant', layout='centered')
st.title('Montandon query assistant')
st.caption('UI spike for issue #44 — graph answers with visible provenance')

real = use_real_assistant()
if real:
    st.success('Connected to the local graph. Answers come from Neo4j and Qwen3-1.7B.')
elif os.environ.get('MONTY_UI_REAL') == '1':
    st.error(
        f'Real mode requested but nothing is listening on '
        f'bolt://{BOLT_HOST}:{BOLT_PORT}. Replaying the recorded exchange instead.'
    )
else:
    st.info(
        'Stub mode — replaying a recorded exchange built from real GDACS '
        'records. Set MONTY_UI_REAL=1 with the graph running to drive the '
        'real assistant.'
    )

if 'history' not in st.session_state:
    st.session_state.history = []

for entry in st.session_state.history:
    with st.chat_message('user'):
        st.write(entry['question'])
    with st.chat_message('assistant'):
        for call in entry['tool_results']:
            show_step(
                call['call']['name'],
                call['call'].get('arguments', {}),
                call['result'],
            )
        st.markdown(entry['answer'])
        show_provenance(entry['tool_results'])

question = st.chat_input('Ask about disaster records...')
if question:
    with st.chat_message('user'):
        st.write(question)
    with st.chat_message('assistant'):
        with st.spinner('Querying the graph...'):
            answer, tool_results = (
                answer_real(question) if real else answer_stub(question)
            )
        st.markdown(answer)
        show_provenance(tool_results)
    st.session_state.history.append({
        'question': question,
        'answer': answer,
        'tool_results': tool_results,
    })
