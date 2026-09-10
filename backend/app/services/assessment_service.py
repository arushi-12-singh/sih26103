"""Records and retrieves GIS assessments (Feature 6, API 5).

Turns a `SpatialAnalysisResult` -- the engine's live computation -- into a durable
`AssessmentRecord` capturing who ran it, where, when, and what was concluded.

Clearance-flag reasons are built from a deterministic template over the engine's computed
values. Given the same result, the same sentence comes out every time; nothing here
generates text with a model, and nothing re-derives a verdict the engine already made.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.config import spatial_config as config
from app.schemas.assessment import (
    AssessmentRecord,
    AssessmentSummary,
    ClearanceFlag,
    IntersectingBoundary,
    ProjectLocation,
)
from app.schemas.spatial import BoundaryCollision, CollisionType, SpatialAnalysisResult
from app.repositories.assessment_repository import (
    AssessmentNotFoundError,
    AssessmentRepository,
    build_assessment_repository,
)


def clearance_reason(collision: BoundaryCollision) -> str:
    """Explain, from the computed geometry, why a boundary triggers a clearance flag."""
    if collision.collision_type is CollisionType.DIRECT_COLLISION:
        return (
            f"The project location lies inside {collision.boundary_name} "
            f"({collision.category_label}); {collision.buffer_overlap_percentage:.1f}% of the "
            f"analysis buffer falls within it."
        )
    if collision.collision_type is CollisionType.BUFFER_COLLISION:
        return (
            f"The analysis buffer intersects {collision.boundary_name} "
            f"({collision.category_label}) at {collision.distance_meters:,.0f} m, overlapping "
            f"{collision.intersection_area_sqm:,.0f} sq m "
            f"({collision.buffer_overlap_percentage:.1f}% of the buffer)."
        )
    return (
        f"{collision.boundary_name} ({collision.category_label}) lies "
        f"{collision.distance_meters:,.0f} m away; proximity to this category requires a formal "
        f"clearance check."
    )


def to_intersecting_boundary(collision: BoundaryCollision) -> IntersectingBoundary:
    """Flatten one engine collision into its stored form."""
    return IntersectingBoundary(
        boundary_id=collision.boundary_id,
        boundary_name=collision.boundary_name,
        category=collision.category,
        category_label=collision.category_label,
        collision_type=collision.collision_type,
        severity=collision.severity,
        distance_meters=collision.distance_meters,
        intersection_area_sqm=collision.intersection_area_sqm,
        buffer_overlap_percentage=collision.buffer_overlap_percentage,
        clearance_required=collision.clearance_required,
        is_demo=collision.is_demo,
    )


def build_clearance_flags(result: SpatialAnalysisResult) -> list[ClearanceFlag]:
    """Every boundary whose collision obliges a clearance check, most severe first.

    Reads the engine's own `clearance_required` decision rather than re-applying the
    rules, so the flag list can never disagree with the analysis it came from.
    """
    return [
        ClearanceFlag(
            boundary_id=collision.boundary_id,
            boundary_name=collision.boundary_name,
            category=collision.category,
            collision_type=collision.collision_type,
            severity=collision.severity,
            distance_meters=collision.distance_meters,
            reason=clearance_reason(collision),
        )
        for collision in result.collisions
        if collision.clearance_required
    ]


class AssessmentService:
    """Persists and reads back GIS assessment history."""

    def __init__(self, repository: AssessmentRepository) -> None:
        self.repository = repository

    def record(
        self,
        *,
        project_id: str,
        result: SpatialAnalysisResult,
        principal_id: str,
        principal_name: str = "",
        notes: str | None = None,
        assessment_id: str | None = None,
    ) -> AssessmentRecord:
        """Store one assessment.

        The id is a uuid4 rather than a hash of the inputs: two analyses of the same
        coordinates at different times are distinct historical events, and collapsing
        them would destroy exactly the history this endpoint exists to keep.
        """
        record = AssessmentRecord(
            id=assessment_id or str(uuid.uuid4()),
            project_id=project_id,
            location=ProjectLocation(latitude=result.latitude, longitude=result.longitude),
            buffer_meters=result.buffer_meters,
            created_at=datetime.now(timezone.utc),
            created_by=principal_id,
            created_by_name=principal_name,
            overall_status=result.collision_type,
            overall_severity=result.severity,
            clearance_required=result.clearance_required,
            boundaries_checked=result.boundaries_indexed,
            intersecting_boundaries=[to_intersecting_boundary(c) for c in result.collisions],
            clearance_flags=build_clearance_flags(result),
            result=result,
            notes=notes,
            metadata={
                "projected_crs": result.projected_crs,
                "proximity_threshold_meters": result.proximity_threshold_meters,
                "contains_demo_data": result.contains_demo_data,
                "severity_ruleset": {
                    "collision_type_base_rank": dict(config.COLLISION_TYPE_BASE_RANK),
                    "category_sensitivity": dict(config.CATEGORY_SENSITIVITY),
                    "overlap_escalation_rules": [list(rule) for rule in config.OVERLAP_ESCALATION_RULES],
                },
            },
        )
        return self.repository.add(record)

    def get(self, assessment_id: str) -> AssessmentRecord:
        return self.repository.get(assessment_id)

    def history(self, project_id: str, *, limit: int | None = None, offset: int = 0) -> list[AssessmentRecord]:
        """A project's assessments, newest first. Empty list when the project has none."""
        return self.repository.for_project(project_id, limit=limit, offset=offset)

    def summaries(self, project_id: str, *, limit: int | None = None, offset: int = 0) -> list[AssessmentSummary]:
        return [
            AssessmentSummary(
                id=record.id,
                project_id=record.project_id,
                location=record.location,
                buffer_meters=record.buffer_meters,
                created_at=record.created_at,
                created_by=record.created_by,
                overall_status=record.overall_status,
                overall_severity=record.overall_severity,
                clearance_required=record.clearance_required,
                collision_count=len(record.intersecting_boundaries),
                clearance_flag_count=len(record.clearance_flags),
            )
            for record in self.history(project_id, limit=limit, offset=offset)
        ]

    def count(self, project_id: str | None = None) -> int:
        return self.repository.count(project_id)


def build_assessment_service(repository: AssessmentRepository | None = None) -> AssessmentService:
    """Construct the service with the configured backend. Called once at startup."""
    return AssessmentService(repository or build_assessment_repository())


__all__ = [
    "AssessmentNotFoundError",
    "AssessmentService",
    "build_assessment_service",
    "build_clearance_flags",
    "clearance_reason",
]
