"""GIS Boundary Screening -- the pipeline stage between similarity and priority.

Reduces a `SpatialAnalysisResult` (geometry, exact intersections, per-boundary detail)
to the compact `GISIntelligenceSignal` the rest of the intelligence pipeline consumes.

Purely a projection of values the spatial engine already computed: it re-derives no
verdict, re-measures nothing, and never re-classifies a boundary. If this module and the
map ever disagreed, the map would be right -- so this module only ever reads.

Deterministic and template-driven throughout; no model and no LLM is involved.
"""

from __future__ import annotations

from app.config import gis_config
from app.config import priority_config as config
from app.schemas.gis_signal import GISIntelligenceSignal, NearestBoundary
from app.schemas.spatial import BoundaryCollision, CollisionType, SpatialAnalysisResult


def _clearance_flag_label(collision: BoundaryCollision) -> str:
    """A short label for one clearance obligation.

    Deliberately describes the REQUIREMENT, not an outcome: "Environmental review
    required for X" is a screening finding; anything phrased as a permit decision would
    exceed what this system is entitled to say.
    """
    return (
        f"Environmental review required: {collision.boundary_name} "
        f"({collision.category_label}, {collision.collision_type.value.replace('_', ' ').lower()})"
    )


def build_gis_signal(result: SpatialAnalysisResult) -> GISIntelligenceSignal:
    """Summarise a spatial analysis into the structured signal used downstream."""
    collisions = result.collisions
    # Already ordered most-severe-then-nearest by the engine, so the first entry is the
    # highest-risk boundary and re-sorting here could only introduce disagreement.
    highest = collisions[0] if collisions else None
    nearest = min(collisions, key=lambda c: (c.distance_meters, c.boundary_id)) if collisions else None
    flagged = [c for c in collisions if c.clearance_required]

    return GISIntelligenceSignal(
        gis_status=result.collision_type,
        gis_severity=result.severity,
        buffer_meters=result.buffer_meters,
        collision_count=result.collision_count,
        boundaries_checked=result.boundaries_indexed,
        highest_risk_category=highest.category if highest else None,
        highest_risk_category_label=highest.category_label if highest else None,
        nearest_boundary=(
            None
            if nearest is None
            else NearestBoundary(
                boundary_id=nearest.boundary_id,
                name=nearest.boundary_name,
                category=nearest.category,
                category_label=nearest.category_label,
                distance_meters=nearest.distance_meters,
                collision_type=nearest.collision_type,
                severity=nearest.severity,
            )
        ),
        clearance_required=result.clearance_required,
        clearance_flag_count=len(flagged),
        clearance_flags=[_clearance_flag_label(c) for c in flagged],
        max_buffer_overlap_percentage=max((c.buffer_overlap_percentage for c in collisions), default=0.0),
        total_intersection_area_sqm=round(sum(c.intersection_area_sqm for c in collisions), 2),
        advisory=(
            config.NO_CONFLICT_HEADLINE
            if result.collision_type is CollisionType.CLEAR
            else config.SPATIAL_CONFLICT_HEADLINE
        ),
        disclaimer=config.CLEARANCE_DISCLAIMER,
        contains_demo_data=result.contains_demo_data,
        notice=gis_config.DEMO_DATA_NOTICE if result.contains_demo_data else None,
    )


__all__ = ["GISIntelligenceSignal", "build_gis_signal"]
