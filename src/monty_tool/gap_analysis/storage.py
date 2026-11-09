"""
Utilities to save gap analysis evaluation answer records.
"""

# Imports

import hashlib
import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory

from monty_tool.boto3_utils.s3_utils import get_bucket, upload_object
from monty_tool.gap_analysis.schemas import AnswerAttempt, SavedAnswerAttempt
from monty_tool.tools.resources import get_env_bucket_name, get_env_bucket_prefix

logger = logging.getLogger(__name__)


# Archive helper

def save_attempt(attempt: AnswerAttempt) -> SavedAnswerAttempt:
    """
    Upload one LLM answer attempt to S3, returning save data.
    This uses the response hash as the object name.
    """
    uri = None
    if attempt.response is not None:
        content = json.dumps(
            attempt.response,
            ensure_ascii=False,
            sort_keys=True,
        ).encode('utf-8')
        digest = hashlib.sha256(content).hexdigest()
        bucket_name = get_env_bucket_name()
        key = f'{get_env_bucket_prefix()}gap_analysis/responses/{digest}.json'
        with TemporaryDirectory(prefix='gap-analysis-') as directory:
            path = Path(directory).joinpath('response.json')
            path.write_bytes(content)
            upload_object(get_bucket(bucket_name), path, key)
            logger.debug(f"Uploaded response: {key}")
        uri = f's3://{bucket_name}/{key}'
    return SavedAnswerAttempt(
        response_s3_uri=uri,
        input_tokens=attempt.response['input_tokens'] if attempt.response is not None else None,
        output_tokens=attempt.response['output_tokens'] if attempt.response is not None else None,
        tool_failure_count=attempt.tool_failures,
        error=attempt.error,
        duration_seconds=attempt.duration_seconds,
    )
