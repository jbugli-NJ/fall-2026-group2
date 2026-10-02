"""
Test an LLM against one question requiring graph database queries
and save the result for review.

NOTE: Assumes the graph database is set up! See:
    - monty_tool.network.initialize
    - monty_tool.network.insert
"""

# Imports

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from transformers import set_seed

from monty_tool.llm.client import QueryAssistant
from monty_tool.utils.versions import SEED


# Constants

OUTPUT_DIRECTORY = Path('outputs')


# Entrypoint

def main():
    """
    Ask a supplied question or the default graph-demo question and save the result.
    """
    set_seed(SEED)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        'question', nargs='?',
        default=(
            'Use the graph to find earthquake events and summarize the recorded '
            'impacts for the most extreme one.'
        ),
    )
    question = parser.parse_args().question
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    output_path = OUTPUT_DIRECTORY.joinpath(f"query_assistant_graph_demo_{timestamp}.json")
    assistant = QueryAssistant()
    result = assistant.ask(question)
    output_path.parent.mkdir(exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding='utf-8',
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    main()
