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
    'news': ExternalSource('Articles returned by get_event_news.'),
    'weather': ExternalSource('Weather fields in graph query results.'),
    'shakemap_footprints': ExternalSource(
        'USGS ShakeMap footprints in external_footprints in graph query results. '
        'The footprints field contains Montandon evidence and remains core evidence.',
    ),
}
