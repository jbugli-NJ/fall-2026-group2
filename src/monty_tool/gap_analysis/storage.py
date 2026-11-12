"""
Archive gap analysis answers and retrieved evidence in S3.
"""

# Imports

import hashlib
import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
from urllib.parse import urlparse

from monty_tool.boto3_utils.s3_utils import download_object, get_bucket, upload_object
from monty_tool.gap_analysis.schemas import AnswerAttempt, Evidence, SavedAnswerAttempt
from monty_tool.tools.resources import get_env_bucket_name, get_env_bucket_prefix

# Logging

logger = logging.getLogger(__name__)


# Archive helpers

def _save_content(content: bytes, folder: str, extension: str) -> str:
    """
    Store an artifact under its content hash and return its S3 URI.
    """
    digest = hashlib.sha256(content).hexdigest()
    bucket_name = get_env_bucket_name()
    key = f'{get_env_bucket_prefix()}gap_analysis/{folder}/{digest}.{extension}'
    with TemporaryDirectory(prefix='gap-analysis-') as directory:
        path = Path(directory, f'artifact.{extension}')
        path.write_bytes(content)
        upload_object(get_bucket(bucket_name), path, key)
    logger.debug(f'Uploaded artifact: {key}')
    return f's3://{bucket_name}/{key}'


def _load_content(uri: str) -> str:
    """
    Read an archived artifact from its S3 URI.
    """
    parsed = urlparse(uri)
    if parsed.scheme != 's3' or not parsed.netloc or not parsed.path.lstrip('/'):
        raise ValueError(f'Invalid S3 URI: {uri}')
    with TemporaryDirectory(prefix='gap-analysis-') as directory:
        path = Path(directory, 'artifact')
        download_object(get_bucket(parsed.netloc), parsed.path.lstrip('/'), path)
        return path.read_text(encoding='utf-8')


# Evidence archives

def save_evidence(evidence: Evidence) -> str:
    """
    Archive the retrieved records once for answering, judging, and human review.
    """
    content = json.dumps(evidence, ensure_ascii=False, sort_keys=True, default=str).encode('utf-8')
    return _save_content(content, 'evidence', 'json')


def load_evidence(uri: str) -> Evidence:
    """
    Read the Evidence projection written by save_evidence. Temporal values
    remain strings from the JSON archive; no graph query or conversion is needed.
    """
    return cast(Evidence, json.loads(_load_content(uri)))


# Answer archives

def load_answer(uri: str | None) -> str:
    """
    Read archived answer text from its S3 URI.
    """
    if uri is None:
        raise ValueError('No archived answer is available.')
    return _load_content(uri)


def save_attempt(attempt: AnswerAttempt) -> SavedAnswerAttempt:
    """
    Upload answer text and retain generation metadata in the results.
    """
    uri = (
        _save_content(attempt.answer.encode('utf-8'), 'answers', 'txt')
        if attempt.answer is not None else None
    )
    return SavedAnswerAttempt(
        answer_s3_uri=uri,
        input_tokens=attempt.input_tokens,
        output_tokens=attempt.output_tokens,
        error=attempt.error,
        duration_seconds=attempt.duration_seconds,
    )
