"""Request/response contracts for the GIS REST API (Feature 6).

These are the HTTP-facing shapes only. They wrap -- and never reimplement -- the domain
models in app/schemas/spatial.py and app/schemas/boundary.py: every value a response
carries was computed by the spatial engine or read from the boundary store, and no field
here is populated by a default, a placeholder, or a hardcoded example.

Every response that can carry geometry carries it as RFC 7946 GeoJSON in WGS84, ready to
hand straight to Leaflet/MapLibre without transformation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import spatial_config as config
from app.models.projection import validate_buffer_meters, validate_coordinates
from app.schemas.assessment import (
    AssessmentRecord,
    AssessmentSummary,
    ClearanceFlag,
    IntersectingBoundary,
    ProjectLocation,
)
from app.schemas.boundary import BoundaryCategory, BoundarySummary
from app.schemas.spatial import CollisionType, Severity

#: Role tags on the features of an analysis FeatureCollection, so a map can style the
#: project point, the tested buffer, and the boundaries differently without guessing.
FeatureRole = Literal["project_location", "analysis_buffer", "boundary", "collision_area"]


class ErrorResponse(BaseModel):
    """The error body every GIS endpoint returns. Documented so clients can rely on it."""

    detail: str = Field(description="A human-readable explanation of what went wrong and, where possible, how to fix it.")


class _CoordinateInput(BaseModel):
    """Shared coordinate/buffer validation for the request bodies below.

    Field bounds reject out-of-range values and NaN; the validators additionally route
    them through the engine's own checks, so HTTP and in-process callers can never
    disagree about what a valid coordinate is.
    """

    model_config = ConfigDict(extra="forbid")

    latitude: float = Field(ge=-90, le=90, description="WGS84 latitude in decimal degrees.")
    longitude: float = Field(ge=-180, le=180, description="WGS84 longitude in decimal degrees.")
    buffer_meters: float = Field(
        default=config.DEFAULT_BUFFER_METERS,
        ge=config.MIN_BUFFER_METERS,
        le=config.MAX_BUFFER_METERS,
        description="Buffer radius around the project point, in METRES.",
    )

    @field_validator("latitude")
    @classmethod
    def _valid_latitude(cls, value: float) -> float:
        validate_coordinates(value, 0.0)
        return value

    @field_validator("longitude")
    @classmethod
    def _valid_longitude(cls, value: float) -> float:
        validate_coordinates(0.0, value)
        return value

    @field_validator("buffer_meters")
    @classmethod
    def _valid_buffer(cls, value: float) -> float:
        return validate_buffer_meters(value)


# ---------------------------------------------------------------------------------
# API 1 -- collision check
# ---------------------------------------------------------------------------------


class CollisionCheckRequest(_CoordinateInput):
    """POST /api/v1/gis/check-collision"""

    project_id: str | None = Field(
        default=None, max_length=128, description="Optional. Echoed back; does not have to exist yet."
    )
    categories: list[BoundaryCategory] | None = Field(
        default=None, description="Optional. Restrict the scan to these boundary categories."
    )
    include_demo: bool = Field(default=True, description="Include fictional demo boundaries in the scan.")
    include_geometry: bool = Field(
        default=True, description="Attach each colliding boundary's GeoJSON geometry to the response."
    )


class CollisionDetail(IntersectingBoundary):
    """One colliding boundary, with the location context and geometry a map needs."""

    state: str
    district: str | None = None
    geometry: dict[str, Any] | None = Field(
        default=None, description="The boundary's GeoJSON Polygon/MultiPolygon in WGS84; null when omitted."
    )
    intersection_geometry: dict[str, Any] | None = Field(
        default=None,
        description=(
            "The exact buffer/boundary overlap as WGS84 GeoJSON, computed by the engine in a projected "
            "metre CRS. Null for NEARBY boundaries, which by definition do not overlap."
        ),
    )


class CollisionCheckResponse(BaseModel):
    """The engine's verdict for one project location."""

    project_id: str | None = None
    project_location: ProjectLocation
    buffer_meters: float
    proximity_threshold_meters: float

    overall_status: CollisionType = Field(description="Most severe collision type found; CLEAR when none.")
    overall_severity: Severity | None = Field(default=None, description="Highest severity found; null when CLEAR.")
    clearance_required: bool
    boundaries_checked: int = Field(ge=0, description="Boundaries in the dataset this scan ran against.")
    candidates_examined: int = Field(ge=0, description="Boundaries the spatial index selected for exact testing.")
    collision_count: int = Field(ge=0)

    collisions: list[CollisionDetail]
    clearance_flags: list[ClearanceFlag]

    geojson: dict[str, Any] = Field(
        description=(
            "A GeoJSON FeatureCollection with the project point, the tested buffer, every colliding "
            "boundary, and the exact overlap footprint of each intersecting one. Each feature's `role` "
            "property is one of project_location, analysis_buffer, boundary, collision_area."
        )
    )
    projected_crs: str = Field(description="The metre-based CRS all measurements were computed in.")
    contains_demo_data: bool
    notice: str | None = None
    summary: str
    analyzed_at: datetime


# ---------------------------------------------------------------------------------
# API 2 -- nearby boundaries
# ---------------------------------------------------------------------------------


class NearbyBoundary(BaseModel):
    """A boundary within the requested radius, with its measured distance."""

    boundary_id: str
    name: str
    category: BoundaryCategory
    category_label: str
    state: str
    district: str | None = None
    distance_meters: float = Field(ge=0, description="0 when the point lies inside this boundary.")
    contains_point: bool
    severity: Severity
    clearance_required: bool
    is_demo: bool
    geometry: dict[str, Any] | None = None


class NearbyBoundariesResponse(BaseModel):
    """GET /api/v1/gis/nearby-boundaries"""

    project_location: ProjectLocation
    radius_meters: float
    categories: list[BoundaryCategory] | None = None
    count: int = Field(ge=0)
    boundaries: list[NearbyBoundary]
    geojson: dict[str, Any]
    boundaries_checked: int = Field(ge=0)
    contains_demo_data: bool
    notice: str | None = None


# ---------------------------------------------------------------------------------
# API 3 / 4 -- boundary list and detail
# ---------------------------------------------------------------------------------


class BoundaryListResponse(BaseModel):
    """GET /api/v1/gis/boundaries"""

    count: int = Field(ge=0, description="Boundaries returned in this page.")
    total: int = Field(ge=0, description="Boundaries matching the filters, before limit/offset.")
    limit: int | None = None
    offset: int = Field(ge=0)
    filters: dict[str, Any] = Field(description="The filters actually applied, echoed for clients that cache.")
    boundaries: list[BoundarySummary]
    geojson: dict[str, Any] | None = Field(
        default=None, description="A FeatureCollection of the returned boundaries; null unless requested."
    )
    contains_demo_data: bool
    notice: str | None = None


class BoundaryDetailResponse(BaseModel):
    """GET /api/v1/gis/boundaries/{id}"""

    boundary: BoundarySummary
    geometry: dict[str, Any] = Field(description="The boundary's GeoJSON Polygon/MultiPolygon in WGS84.")
    feature: dict[str, Any] = Field(description="The same record as a complete GeoJSON Feature.")
    metadata: dict[str, Any]
    is_demo: bool
    notice: str | None = None


class BoundaryCreateRequest(BaseModel):
    """POST /api/v1/gis/boundaries -- requires MANAGE_GIS_DATA."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=300)
    category: BoundaryCategory
    state: str = Field(min_length=1, max_length=120)
    district: str | None = Field(default=None, max_length=120)
    geometry: dict[str, Any] = Field(description="GeoJSON Polygon or MultiPolygon in WGS84.")
    source: str = Field(min_length=1, max_length=300, description="Required: no boundary may exist without an origin.")
    source_url: str | None = Field(default=None, max_length=1000)
    last_updated: str = Field(description="ISO date the SOURCE dataset was last updated.")
    metadata: dict[str, Any] = Field(default_factory=dict)
    is_demo: bool = Field(
        default=True,
        description=(
            "Defaults to true. Set false ONLY for a genuinely official or licensed dataset -- "
            "records marked official are presented without the demo-data notice."
        ),
    )
    overwrite: bool = Field(default=False, description="Replace an existing record with the same derived id.")


# ---------------------------------------------------------------------------------
# API 5 -- assessments
# ---------------------------------------------------------------------------------


class AssessmentCreateRequest(_CoordinateInput):
    """POST /api/v1/gis/assessments -- runs the engine and stores the outcome."""

    project_id: str = Field(min_length=1, max_length=128, description="Required: an assessment belongs to a project.")
    categories: list[BoundaryCategory] | None = None
    include_demo: bool = True
    notes: str | None = Field(default=None, max_length=2000)


class AssessmentHistoryResponse(BaseModel):
    """GET /api/v1/gis/assessments/{project_id}"""

    project_id: str
    count: int = Field(ge=0)
    total: int = Field(ge=0)
    limit: int | None = None
    offset: int = Field(ge=0)
    assessments: list[AssessmentRecord]


class AssessmentHistorySummaryResponse(BaseModel):
    """The same history without full result payloads."""

    project_id: str
    count: int = Field(ge=0)
    total: int = Field(ge=0)
    assessments: list[AssessmentSummary]
