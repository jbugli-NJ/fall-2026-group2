"""
S3 typing protocols.
"""

# Imports

from collections.abc import Iterable
from os import PathLike
from typing import Protocol


# Helper protocols

class S3ObjectSummary(Protocol):
    """
    One object returned when listing bucket contents.
    """
    key: str


class S3ObjectCollection(Protocol):
    """
    An object collection for an S3 bucket.
    The object-listing collection exposed by an S3 bucket resource.
    """
    def all(self) -> Iterable[S3ObjectSummary]: ...

    def filter(self, *, Prefix: str) -> Iterable[S3ObjectSummary]: ...


class S3Bucket(Protocol):
    """
    An S3 bucket resource.
    """
    objects: S3ObjectCollection

    def download_file(
        self,
        Key: str,
        Filename: str | PathLike[str],
    ) -> None:
        """Download an object from this bucket to a local file."""
        ...

    def upload_file(
        self,
        Filename: str | PathLike[str],
        Key: str,
    ) -> None:
        """Upload a local file to this bucket."""
        ...


class S3Resource(Protocol):
    """
    The full S3 resource.
    """
    def Bucket(self, bucket_name: str) -> S3Bucket: ...
