"""Build a shared view of a Montandon record for search and LLM tools."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from monty_tool.api_schemas import (
    BBox,
    MontandonItem,
    MontandonLink,
    PointGeometry,
)
from monty_tool.features import strip_html


class EventContext(BaseModel):
    """A view of one source record, not a merged cross-source disaster."""

    # Identify the original record.
    item_id: str
    collection: str
    correlation_id: str
    roles: list[str]

    # Describe the event.
    title: str
    description: str | None
    keywords: list[str]
    country_codes: list[str]
    hazard_codes: list[str]

    # Preserve the event's time range.
    start_datetime: datetime
    end_datetime: datetime

    # Preserve location information.
    geometry_type: str
    bbox: BBox
    longitude: float | None = None
    latitude: float | None = None

    # Keep provenance and available source-specific information.
    source_links: list[MontandonLink] = Field(default_factory=list)
    source_details: dict[str, Any] = Field(default_factory=dict)


def build_event_context(
    data: MontandonItem | dict[str, Any],
) -> EventContext:
    """Accept a validated Item or raw API JSON and preserve available details."""

    if isinstance(data, MontandonItem):
        item = data

        raw_properties = item.properties.model_dump(
            mode="json",
            by_alias=True,
        )

    else:
        # Validate the record while retaining its original properties.
        item = MontandonItem.model_validate(data)
        raw_properties = data["properties"]

    properties = item.properties

    # Preserve available values and units without guessing missing values.
    detail_keys = (
        "severitydata",
        "monty:hazard_detail",
        "monty:impact_detail",
        "monty:response_detail",
    )

    source_details = {
        key: raw_properties[key]
        for key in detail_keys
        if raw_properties.get(key) is not None
    }

    longitude = None
    latitude = None

    # Copy coordinates only when the source provides a Point.
    # A polygon's bounding-box centre is not necessarily the event location.
    if isinstance(item.geometry, PointGeometry):
        longitude = item.geometry.coordinates[0]
        latitude = item.geometry.coordinates[1]

    return EventContext(
        item_id=item.id,
        collection=item.collection,
        correlation_id=properties.monty_corr_id,
        roles=list(properties.roles),

        title=properties.title,
        description=strip_html(properties.description),
        keywords=list(properties.keywords),
        country_codes=list(properties.monty_country_codes),
        hazard_codes=list(properties.monty_hazard_codes),

        start_datetime=properties.start_datetime,
        end_datetime=properties.end_datetime,

        geometry_type=item.geometry.type,
        bbox=item.bbox,
        longitude=longitude,
        latitude=latitude,

        source_links=list(item.links),
        source_details=source_details,
    )