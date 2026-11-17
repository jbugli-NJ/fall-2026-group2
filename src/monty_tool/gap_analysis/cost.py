"""
Estimate Bedrock Sonnet 4.5 costs from saved gap analysis token counts.
"""

# Imports

import json
from pathlib import Path

from monty_tool.gap_analysis.schemas import SavedAnalysisRun


# Pricing
# See https://aws.amazon.com/bedrock/pricing/

MODEL_ID = 'us.anthropic.claude-sonnet-4-5-20250929-v1:0'
INPUT_PRICE = 3.30
OUTPUT_PRICE = 16.50


# Entrypoint

def main():
    path = Path('benchmarks/gap_analysis/results.json')
    runs = [
        SavedAnalysisRun.model_validate(record)
        for record in json.loads(path.read_text())['runs']
    ]
    totals = {'core': [0, 0], 'external': [0, 0], 'judge': [0, 0]}
    missing = 0
    for run in runs:
        for pair in run.results:
            usage: list[tuple[str, int | None, int | None]] = []
            if run.metadata['answer_model']['id'] == MODEL_ID:
                usage.extend([
                    ('core', pair.core.input_tokens, pair.core.output_tokens),
                    ('external', pair.external.input_tokens, pair.external.output_tokens),
                ])
            if run.metadata['judge_model']['id'] == MODEL_ID:
                usage.append(('judge', pair.judge_input_tokens, pair.judge_output_tokens))
            for stage, input_tokens, output_tokens in usage:
                missing += int(input_tokens is None or output_tokens is None)
                totals[stage][0] += input_tokens or 0
                totals[stage][1] += output_tokens or 0

    print(f'Model: {MODEL_ID}')
    print('Pricing: US cross-region (standard)')
    print(f'USD per million tokens: input ${INPUT_PRICE:.2f}, output ${OUTPUT_PRICE:.2f}')
    print(
        f'{"Stage":<12} {"Input tokens":>14} {"Output tokens":>14} '
        f'{"Input cost":<11} {"Output cost":<11} {"Total":<11}'
    )
    totals['total'] = [
        sum(tokens[0] for tokens in totals.values()),
        sum(tokens[1] for tokens in totals.values()),
    ]
    for stage, (input_tokens, output_tokens) in totals.items():
        input_cost = input_tokens * INPUT_PRICE / 1_000_000
        output_cost = output_tokens * OUTPUT_PRICE / 1_000_000
        print(
            f'{stage:<12} {input_tokens:>14,} {output_tokens:>14,} '
            f'${input_cost:>8.4f}   ${output_cost:>8.4f}   '
            f'${input_cost + output_cost:>8.4f}'
        )
    print(f'Attempts with missing token counts: {missing} (unrecorded usage excluded).')


if __name__ == '__main__':
    main()
