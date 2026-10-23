"""
Spike: a Streamlit front end for QueryAssistant, for issue #44.

Graph queries are rendered with their arguments and returned rows.
The record IDs behind the answer are listed underneath.

    uv run streamlit run spikes/streamlit/app.py
    MONTY_UI_LOCAL=1 uv run streamlit run spikes/streamlit/app.py
    MONTY_UI_REPLAY=1 uv run streamlit run spikes/streamlit/app.py
"""

# Imports

import json
import os
import socket
import sys
import time
from pathlib import Path

import streamlit as st

from monty_tool.llm.protocols import QueryAssistantProtocol
from monty_tool.utils.versions import FrozenModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from render import id_kinds, provenance, render_rows  # noqa: E402


# Constants

FIXTURES_PATH = Path(__file__).resolve().parent / 'fixtures.json'
BOLT_HOST, BOLT_PORT = 'localhost', 7687

# Markdown tables do not scroll in Streamlit, so a wide result table
# spills past its expander's border. Making the table its own scroll
# box keeps it inside, as st.code blocks already are.
TABLE_CSS = """
<style>
[data-testid="stMarkdownContainer"] table {
    display: block;
    max-width: 100%;
    overflow-x: auto;
}
</style>
"""


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
    Check whether real mode is enabled and the graph is reachable.
    """
    return os.environ.get('MONTY_UI_REPLAY', '0') != '1' and graph_is_up()


@st.cache_resource(show_spinner=False)
def load_assistant(assistant_kind: str) -> QueryAssistantProtocol:
    """
    Load and cache the selected assistant.
    """
    from monty_tool.llm.client import BedrockQueryAssistant, QueryAssistant
    if assistant_kind == 'bedrock':
        return BedrockQueryAssistant()
    return QueryAssistant(model_id=FrozenModel.QWEN3_5_4B)


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


def answer_real(question: str, assistant_kind: str) -> tuple[str, list]:
    """
    Drive the real assistant, rendering each tool call as it is made.

    Wrapping the bound `execute` keeps the live step rendering out of
    llm/client.py. Streamlit runs synchronously, so a wrapper can draw
    straight into the page with no event-loop plumbing.
    """
    assistant = load_assistant(assistant_kind)
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

assistant_kind = 'local' if os.environ.get('MONTY_UI_LOCAL', '0') == '1' else 'bedrock'

st.set_page_config(page_title='Montandon query assistant', layout='centered')
st.markdown(TABLE_CSS, unsafe_allow_html=True)
st.title('Montandon query assistant')

real = use_real_assistant()
if real:
    model_name = 'Bedrock' if assistant_kind == 'bedrock' else 'Qwen3.5-4B'
    st.success(f'Connected to the local graph. Answers come from Neo4j and {model_name}.')
elif os.environ.get('MONTY_UI_REPLAY', '0') != '1':
    st.error(
        f'Real mode requested but nothing is listening on '
        f'bolt://{BOLT_HOST}:{BOLT_PORT}. Replaying the recorded exchange instead.'
    )
else:
    st.info(
        'Stub mode — replaying a recorded exchange built from real GDACS '
        'records. Set MONTY_UI_REPLAY=0 with the graph running to drive the '
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
                answer_real(question, assistant_kind) if real else answer_stub(question)
            )
        st.markdown(answer)
        show_provenance(tool_results)
    st.session_state.history.append({
        'question': question,
        'answer': answer,
        'tool_results': tool_results,
    })
