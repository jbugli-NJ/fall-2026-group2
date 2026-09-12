"""
Core utilities for Montanodon API access.
"""

# Imports

import os

from pystac_client import Client


# Resources

STAC_API_URL = 'https://montandon-eoapi-stage.ifrc.org/stac'


# API utilities

def _get_headers() -> dict[str, str]:
    """
    Retrieve headers for the Montandon API from the environment.
    """
    api_token = os.getenv('MONTANDON_API_TOKEN')
    if api_token is None:
        raise ValueError('MONTANDON_API_TOKEN is unset!')
    auth_headers = {'Authorization': f'Bearer {api_token}'}
    return auth_headers


def get_pystac_client():
    """
    Instantiate a PySTAC client using an API token in the environment.
    """
    auth_headers = _get_headers()
    client = Client.open(STAC_API_URL, headers=auth_headers)
    return client
