"""
Tests for the weather API client's validated responses.
"""

from datetime import date
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from monty_tool import weather_api
from monty_tool.weather.schemas import POWERResponse


def test_client_returns_validated_response(monkeypatch):
    """
    Confirms the client returns a validated response that dumps to a dictionary.
    """
    payload = {
        'geometry': {'type': 'Point', 'coordinates': [-74.006, 40.713, 10.17]},
        'properties': {'parameter': {'T2M': {'20250101': 7.28}}},
        'header': {
            'sources': ['MERRA2'],
            'fill_value': -999.0,
            'start': '20250101',
            'end': '20250101',
        },
        'parameters': {'T2M': {'units': 'C', 'longname': 'Temperature at 2 Meters'}},
    }
    monkeypatch.setattr(
        weather_api.requests, 'get',
        Mock(return_value=Mock(status_code=200, json=Mock(return_value=payload))),
    )

    response = weather_api.get_weather_data(
        40.7128, -74.006, date(2025, 1, 1), date(2025, 1, 1),
    )

    assert isinstance(response, POWERResponse)
    assert response.model_dump() == payload


def test_client_rejects_invalid_response(monkeypatch):
    """
    Confirms the client rejects an invalid response even when HTTP succeeds.
    """
    monkeypatch.setattr(
        weather_api.requests, 'get',
        Mock(return_value=Mock(status_code=200, json=Mock(return_value={}))),
    )

    with pytest.raises(ValidationError):
        weather_api.get_weather_data(0, 0, date(2025, 1, 1), date(2025, 1, 7))
