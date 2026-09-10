"""Pydantic schemas for the GIS Spatial Analysis Engine (Feature 5).

The request is exactly the three specified inputs (latitude, longitude, buffer_meters);
the boundary dataset comes from the configured repository, not from the caller.

Every response field is a computed geometric measurement -- there is no field here that
a model, an LLM, or a name-matching heuristic could populate.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import spatial_config as config
from app.models.projection import validate_buffer_meters, validate_coordinates
from app.schemas.boundary import BoundaryCategory


class CollisionType(str, Enum):
    """How a boundary relates to the project point and its buffer.

    Mutually exclusive and evaluated in this order, so a boundary is classified exactly
    once:
      DIRECT_COLLISION -- the project point itself lies inside the boundary polygon.
      BUFFER_COLLISION -- the point is outside, but the metre buffer around it intersects.
      NEARBY           -- no intersection, but the boundary is within the proximity threshold.
      CLEAR            -- nothing relevant intersected or nearby (an overall verdict only;
                          never attached to an individual reported boundary).
    """

    DIRECT_COLLISION = "DIRECT_COLLISION"
    BUFFER_COLLISION = "BUFFER_COLLISION"
    NEARBY = "NEARBY"
    CLEAR = "CLEAR"

    @property
    def precedence(self) -> int:
        return config.COLLISION_TYPE_PRECEDENCE.index(self.value)


class Severity(str, Enum):
    """Configured severity band. Ranks and rules live in app/config/spatial_config.py."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @property
    def precedence(self) -> int:
        return config.SEVERITY_PRECEDENCE.index(self.value)


class SpatialAnalysisRequest(BaseModel):
    """A project location and the buffer to test around it."""

    model_config = ConfigDict(extra="forbid")

    latitude: float = Field(ge=-90, le=90, description="WGS84 latitude in decimal degrees.")
    longitude: float = Field(ge=-180, le=180, description="WGS84 longitude in decimal degrees.")
    buffer_meters: float = Field(
        default=config.DEFAULT_BUFFER_METERS,
        ge=config.MIN_BUFFER_METERS,
        le=config.MAX_BUFFER_METERS,
        description="Buffer radius around the project point, in METRES (never degrees).",
    )

    # The Field bounds above already reject out-of-range values and NaN. These validators
    # route the same values through the engine's own canonical checks, so the HTTP layer
    # and a direct in-process call can never diverge on what counts as a valid coordinate.
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
    def _finite_buffer(cls, value: float) -> float:
        return validate_buffer_meters(value)


class BoundaryCollision(BaseModel):
    """One boundary's computed relationship to the project point."""

    boundary_id: str
    boundary_name: str
    category: BoundaryCategory
    category_label: str
    state: str
    district: str | None = None

    distance_meters: float = Field(
        ge=0, description="Shortest distance from the project point to the boundary edge; 0 when inside."
    )
    intersection_area_sqm: float = Field(
        ge=0, description="Area of the overlap between the project buffer and the boundary polygon."
    )
    buffer_overlap_percentage: float = Field(
        ge=0, le=100, description="intersection_area_sqm as a percentage of the buffer's own area."
    )
    boundary_area_sqm: float = Field(ge=0, description="The boundary's own area in the local projected CRS.")

    intersection_geometry: dict[str, Any] | None = Field(
        default=None,
        description=(
            "The EXACT overlap between the analysis buffer and this boundary, as a WGS84 GeoJSON "
            "Polygon/MultiPolygon. Computed in the projected metre CRS and converted back, so it is "
            "the same geometry `intersection_area_sqm` was measured from -- not a client-side "
            "approximation. None when the buffer does not overlap (NEARBY) or the buffer is zero."
        ),
    )

    collision_type: CollisionType
    severity: Severity
    severity_rank: int = Field(ge=1, description="The additive rank the severity band was derived from.")
    clearance_required: bool
    is_demo: bool = Field(description="True when this boundary is fictional demo data, not an official boundary.")


class SpatialAnalysisResult(BaseModel):
    """The overall verdict plus every relevant boundary, most severe first."""

    latitude: float
    longitude: float
    buffer_meters: float
    proximity_threshold_meters: float

    collision_type: CollisionType = Field(description="The most severe collision type found; CLEAR when none.")
    severity: Severity | None = Field(default=None, description="Highest severity found; null when CLEAR.")
    clearance_required: bool
    collision_count: int = Field(ge=0)
    collisions: list[BoundaryCollision]

    buffer_geometry: dict[str, Any] | None = Field(
        default=None,
        description=(
            "The analysis buffer as a WGS84 GeoJSON Polygon -- the true metre circle projected back "
            "to degrees, so a map can draw exactly the shape that was tested. None when buffer_meters is 0."
        ),
    )
    projected_crs: str = Field(description="The metre-based CRS every measurement was computed in.")
    boundaries_indexed: int = Field(ge=0, description="Total boundaries in the dataset.")
    candidates_examined: int = Field(
        ge=0, description="Boundaries the spatial index returned for exact testing (<= boundaries_indexed)."
    )
    contains_demo_data: bool
    notice: str | None = None

    def summary(self) -> str:
        """A deterministic, template-built one-liner. No language model is involved."""
        if self.collision_type is CollisionType.CLEAR:
            return (
                f"No restricted boundary intersects the {self.buffer_meters:,.0f} m buffer or lies within "
                f"{self.proximity_threshold_meters:,.0f} m of the project location."
            )
        nearest = self.collisions[0]
        return (
            f"{self.collision_type.value} with {nearest.boundary_name} ({nearest.category_label}) at "
            f"{nearest.distance_meters:,.0f} m -- severity {nearest.severity.value}. "
            f"{self.collision_count} relevant boundar{'y' if self.collision_count == 1 else 'ies'} found; "
            f"clearance {'required' if self.clearance_required else 'not indicated'}."
        )


class SeverityRuleExplanation(BaseModel):
    """The exact terms that produced one severity, so a result is never a black box."""

    model_config = ConfigDict(extra="forbid")

    collision_type_rank: int
    category_sensitivity: int
    overlap_escalation: int
    total_rank: int
    clamped_rank: int
    severity: Severity
    terms: dict[str, Any] = Field(default_factory=dict)
