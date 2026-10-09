"""
Tests for raw collection updates.
"""

from pathlib import Path
from unittest.mock import Mock

import pytest

from monty_tool.tools import update_raw_data


@pytest.mark.parametrize(("args", "collection", "geometry", "uploads"), [
    ([], "gdacs-events", True, 1),
    (["--collection", "emdat-events", "--no-geometry", "--no-upload"], "emdat-events", False, 0),
])
def test_raw_data_update(monkeypatch, args, collection, geometry, uploads):
    client = Mock()
    client.get_collections.return_value = [Mock(id="gdacs-events")]
    pull = Mock(return_value=Path("collection.jsonl.gz"))
    upload = Mock()
    monkeypatch.setattr(update_raw_data, "get_pystac_client", lambda: client)
    monkeypatch.setattr(update_raw_data, "pull_collection", pull)
    monkeypatch.setattr(update_raw_data, "get_bucket", Mock())
    monkeypatch.setattr(update_raw_data, "upload_object", upload)
    monkeypatch.setenv("AWS_BUCKET", "test-bucket")
    monkeypatch.setenv("AWS_BUCKET_PREFIX", "team/")

    assert update_raw_data.main(args) == 0
    pull.assert_called_once_with(collection, client=client, geometry=geometry)
    assert upload.call_count == uploads
