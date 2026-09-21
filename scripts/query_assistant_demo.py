"""
Test an LLM against one question requiring graph database queries
and save the result for review.

NOTE: Assumes the graph database is set up! See:
    - monty_tool.network.initialize
    - monty_tool.network.insert
"""

# Imports

import json
from datetime import datetime, timezone
from pathlib import Path

from monty_tool.llm.client import QueryAssistant


# Constants

OUTPUT_DIRECTORY = Path('outputs')


# Entrypoint

def main():
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    output_path = OUTPUT_DIRECTORY.joinpath(f"query_assistant_graph_demo_{timestamp}.json")
    assistant = QueryAssistant()
    result = assistant.ask(
        'What disasters had the strongest Red Cross response relative to severity? '
        'Reference the available graph database for your response.'
    )
    output_path.parent.mkdir(exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
