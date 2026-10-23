"""
Make one Sonnet 4.5 test call using the configured AWS credentials.
"""

import json
import os
from typing import cast

import boto3

from monty_tool.boto3_utils.llm_protocols import BedrockRuntimeClient


def main():
    """
    Send a small prompt and print the model response.
    """
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    client = cast(
        BedrockRuntimeClient,
        boto3.client("bedrock-runtime", region_name=region),
    )
    response = client.converse(
        modelId="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        messages=[{
            "role": "user",
            "content": [{"text": "Reply with: Bedrock works."}],
        }],
        inferenceConfig={"maxTokens": 256},
    )
    print(json.dumps(response["output"]["message"], indent=2))
    print("Stop reason:", response["stopReason"])
    print("Usage:", response["usage"])


if __name__ == "__main__":
    main()
