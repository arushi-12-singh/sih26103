"""Schemas for stored GIS assessments (Feature 6, API 5).

An assessment is the durable record of one spatial analysis: who ran it, where, with what
buffer, when, and what the engine concluded. It is an audit artifact, so it stores the
*outcome as computed at that moment* rather than a pointer to live data -- re-running the
same coordinates after a boundary dataset update must not silently rewrite history.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.boundary import BoundaryCategory
from app.schemas.spatial import CollisionType, Severity, SpatialAnalysisResult


class ProjectLocation(BaseModel):
    """A project's coordinates, as returned by the API."""

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class IntersectingBoundary(BaseModel):
    """One boundary a project collided with, flattened for storage and listing."""

    boundary_id: str
    boundary_name: str
    category: BoundaryCategory
    category_label: str
    collision_type: CollisionType
    severity: Severity
    distance_meters: float = Field(ge=0)
    intersection_area_sqm: float = Field(ge=0)
    buffer_overlap_percentage: float = Field(ge=0, le=100)
    clearance_required: bool
    is_demo: bool


class ClearanceFlag(BaseModel):
    """A boundary that obliges the project to seek clearance, and why.

    `reason` is built from a deterministic template over the computed values -- the same
    inputs always yield the same sentence. No language model is involved.
    """

    boundary_id: str
    boundary_name: str
    category: BoundaryCategory
    collision_type: CollisionType
    severity: Severity
    distance_meters: float = Field(ge=0)
    reason: str


class AssessmentRecord(BaseModel):
    """One stored assessment.

    Covers everything API 5 is required to keep: project, coordinates, buffer, timestamp,
    user, result, intersecting boundaries, severity, and clearance flags.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)

    location: ProjectLocation
    buffer_meters: float = Field(ge=0)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: str = Field(min_length=1, max_length=128, description="Principal id of the caller who ran it.")
    created_by_name: str = Field(default="", max_length=256)

    overall_status: CollisionType
    overall_severity: Severity | None = None
    clearance_required: bool
    boundaries_checked: int = Field(ge=0)

    intersecting_boundaries: list[IntersectingBoundary] = Field(default_factory=list)
    clearance_flags: list[ClearanceFlag] = Field(default_factory=list)

    #: The complete engine output as computed at the time, so an audit can reconstruct
    #: the decision without re-running anything.
    result: SpatialAnalysisResult

    notes: str | None = Field(default=None, max_length=2000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AssessmentSummary(BaseModel):
    """An assessment without its full result payload -- for history listings."""

    id: str
    project_id: str
    location: ProjectLocation
    buffer_meters: float
    created_at: datetime
    created_by: str
    overall_status: CollisionType
    overall_severity: Severity | None
    clearance_required: bool
    collision_count: int = Field(ge=0)
    clearance_flag_count: int = Field(ge=0)
