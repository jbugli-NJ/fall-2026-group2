"""
Tests for the Montandon API utility module.
"""

# Imports

import pytest

from monty_tool import api_utils


# Tests

def test_get_headers_uses_env(monkeypatch: pytest.MonkeyPatch):
    """
    Confirms that headers use the API token in the environment.
    """
    monkeypatch.setenv('MONTANDON_API_TOKEN', 'test-token')
    headers = api_utils._get_headers()
    assert 'test-token' in headers['Authorization']
