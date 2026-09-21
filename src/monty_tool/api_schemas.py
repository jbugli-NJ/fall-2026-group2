"""
Pydantic schemas for Montandon STAC and IFRC GO API records.
"""


# Imports

from datetime import datetime as DateTime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


# Helpers

class MontandonModel(BaseModel):
    """
    Base model for schemas that ignores extra fields and validates
    using provided aliases.
    """
    model_config = ConfigDict(
        extra="ignore",
        validate_by_alias=True,
    )


class GOModel(BaseModel):
    """
    Base model for records returned by the IFRC GO API.
    """
    model_config = ConfigDict(extra="ignore")


# Geometry schemas

Position = tuple[float, float] | tuple[float, float, float]
BBox = (
    tuple[float, float, float, float]
    | tuple[float, float, float, float, float, float]
)


class PolygonGeometry(MontandonModel):
    type: Literal["Polygon"]
    coordinates: list[list[Position]]


class MultiPolygonGeometry(MontandonModel):
    type: Literal["MultiPolygon"]
    coordinates: list[list[list[Position]]]


class PointGeometry(MontandonModel):
    type: Literal["Point"]
    coordinates: Position


Geometry = PointGeometry | PolygonGeometry | MultiPolygonGeometry


# Item component schemas

class MontandonLink(MontandonModel):
    """
    A link from a Montandon Item.
    """
    rel: str
    href: str
    roles: list[str] | None = None


class HazardDetail(MontandonModel):
    """
    The required hazard metadata present on hazard Items.
    """
    estimate_type: str
    severity_unit: str
    severity_value: int | float | str


# Property schemas

NonBlankText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]

class CommonMontandonProperties(MontandonModel):
    """
    Properties shared by the documented event and hazard Item payloads.
    """
    roles: list[str]
    title: NonBlankText
    datetime: DateTime
    keywords: list[str] = Field(default_factory=list)
    description: NonBlankText
    start_datetime: DateTime
    end_datetime: DateTime
    monty_corr_id: str = Field(alias="monty:corr_id")
    monty_hazard_codes: list[str] = Field(alias="monty:hazard_codes")
    monty_src_event_id: str | None = Field(
        default=None,
        alias="monty:src_event_id",
    )
    monty_country_codes: list[str] = Field(alias="monty:country_codes")
    monty_episode_number: int = Field(alias="monty:episode_number")


class MontandonEventProperties(CommonMontandonProperties):
    """
    Properties on event Items, such as `emdat-events` and `gfd-events`.
    """


class MontandonHazardProperties(CommonMontandonProperties):
    """
    Properties on hazard Items, which additionally include severity details.
    """
    monty_hazard_detail: HazardDetail = Field(alias="monty:hazard_detail")


class SeverityData(MontandonModel):
    """
    GDACS's source severity data on impact Items.
    """
    severity: float
    severitytext: str
    severityunit: str


class ImpactDetail(MontandonModel):
    """
    The required `monty:impact_detail` payload on impact Items.
    """
    type: str
    unit: str | None = None
    value: int | float
    category: str
    estimate_type: str


class MontandonImpactProperties(CommonMontandonProperties):
    """
    Properties on impact Items, such as `gdacs-impacts`.

    `created`, `forecasted`, `severitydata`, and `advisory_number` are
    GDACS-specific; EM-DAT and IFRC impact Items omit them.
    """
    created: DateTime | None = None
    forecasted: bool | None = None
    severitydata: SeverityData | None = None
    advisory_number: str | None = None
    monty_impact_detail: ImpactDetail = Field(alias="monty:impact_detail")


MontandonProperties = (
    MontandonEventProperties
    | MontandonHazardProperties
    | MontandonImpactProperties
)


# Item schema

class MontandonItem(MontandonModel):
    """
    A fully populated Item document returned by the Montandon STAC API.
    """
    id: str
    bbox: BBox
    links: list[MontandonLink]
    geometry: Geometry | None = None
    collection: str
    properties: MontandonProperties


# IFRC GO schemas

class GODisasterType(GOModel):
    """
    A disaster type nested in a GO event or appeal.
    """
    id: int
    name: str
    summary: str = ""
    translation_module_original_language: str | None = None


class GOCountry(GOModel):
    """
    A country nested in a GO event or appeal.
    """
    id: int
    name: str
    iso: str | None = None
    iso3: str | None = None
    record_type: int | None = None
    record_type_display: str | None = None
    region: int | None = None
    independent: bool | None = None
    is_deprecated: bool | None = None
    fdrs: str | None = None
    average_household_size: float | None = None
    society_name: str | None = None
    translation_module_original_language: str | None = None


class GORegion(GOModel):
    """
    A region nested in a GO appeal.
    """
    id: int
    region_name: str
    label: str
    name: int | str | None = None
    translation_module_original_language: str | None = None


class GOEmbeddedAppeal(GOModel):
    """
    Appeal data embedded in a GO event response.

    This is not the same as `GOAppeal`: the event endpoint
    does not include the appeal's event, country, or disaster type.
    """
    id: int
    aid: str
    atype: int
    atype_display: str
    status: int
    status_display: str
    code: str
    sector: str
    num_beneficiaries: int
    amount_requested: float
    amount_funded: float
    start_date: DateTime
    end_date: DateTime
    translation_module_original_language: str | None = None


class GOAppeal(GOModel):
    """
    A complete IFRC GO appeal record.
    """
    id: str
    aid: str
    name: str
    dtype: GODisasterType
    atype: int
    atype_display: str
    status: int
    status_display: str
    code: str
    sector: str
    num_beneficiaries: int
    amount_requested: float
    amount_funded: float
    start_date: DateTime
    end_date: DateTime
    real_data_update: DateTime | None = None
    created_at: DateTime
    modified_at: DateTime
    event: int | None = None
    needs_confirmation: bool
    country: GOCountry
    region: GORegion
    event_details: dict[str, Any] | None = None


class GOEvent(GOModel):
    """
    An IFRC GO emergency event record.
    """
    id: int
    name: str
    dtype: GODisasterType | None = None
    countries: list[GOCountry]
    num_affected: int | None = None
    ifrc_severity_level: int
    ifrc_severity_level_display: str
    ifrc_severity_level_update_date: DateTime | None = None
    glide: str
    disaster_start_date: DateTime
    created_at: DateTime
    auto_generated: bool
    appeals: list[GOEmbeddedAppeal] = Field(default_factory=list)
    is_featured: bool
    is_featured_region: bool
    field_reports: list[dict[str, Any]] = Field(default_factory=list)
    updated_at: DateTime
    slug: str | None = None
    parent_event: int | None = None
    tab_one_title: str
    tab_two_title: str | None = None
    tab_three_title: str | None = None
    emergency_response_contact_email: str | None = None
    active_deployments: int
    summary: str
    translation_module_original_language: str | None = None
