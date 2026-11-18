"""
External data sources available to gap analysis.
"""

# Imports

from dataclasses import dataclass
from typing import Literal


# Schemas

type ExternalSourceName = Literal['news', 'weather', 'shakemap_footprints']


@dataclass(frozen=True)
class ExternalSource:
    """
    The evidence provided by an external data source.
    """
    evidence_description: str


# Sources

EXTERNAL_SOURCES: dict[ExternalSourceName, ExternalSource] = {
    'news': ExternalSource('Stored articles verified for matching disaster events.'),
    'weather': ExternalSource('Weather fields in supplied disaster and impact records.'),
    'shakemap_footprints': ExternalSource(
        'USGS ShakeMap footprints in external_footprints in graph query results. '
        'The footprints field contains Montandon evidence and remains core evidence.',
    ),
}
