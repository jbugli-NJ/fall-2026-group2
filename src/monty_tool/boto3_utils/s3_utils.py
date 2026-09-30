"""
Helpers for working with S3 bucket resources using typing protocols.
"""

# Imports

from pathlib import Path
from typing import cast

import boto3

from monty_tool.boto3_utils.s3_protocols import S3Bucket, S3Resource


# Bucket helpers

def get_bucket(bucket_name: str) -> S3Bucket:
    """
    Retrieve an S3 bucket resource.
    """
    s3 = cast(S3Resource, boto3.resource('s3'))
    return s3.Bucket(bucket_name)


def download_object(bucket: S3Bucket, key: str, output_path: Path) -> None:
    """
    Download an object from an S3 bucket.
    """
    bucket.download_file(key, output_path)


def upload_object(bucket: S3Bucket, input_path: Path, key: str) -> None:
    """
    Upload an object to an S3 bucket.
    """
    bucket.upload_file(input_path, key)
