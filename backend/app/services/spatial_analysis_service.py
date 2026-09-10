"""GIS Spatial Analysis Engine (Feature 5).

Given a project's latitude/longitude and a buffer radius in metres, computes the actual
geometric relationship between that project and every environmental/restricted boundary
in the store.

Pipeline
--------
    lat/lon
      -> validate coordinates                    (app/models/projection.py)
      -> build a local metre-based CRS           (AEQD centred on the point, or UTM)
      -> project the point                       (exact centre under AEQD)
      -> buffer it IN METRES                     (never in degrees)
      -> project the search envelope back to degrees
      -> spatial index lookup for candidates     (STRtree / PostGIS GiST via bbox query)
      -> exact geometry intersection per candidate, in metres
      -> distance, intersection area, overlap %
      -> classify collision type, severity, clearance

This is deterministic computational geometry. No model, no LLM, no text or name matching,
no hardcoded coordinates, no hardcoded verdicts: every number is derived from the input
coordinates and the stored geometry, and identical inputs always produce identical
output. Every threshold and rule it applies is read from app/config/spatial_config.py.

Two properties are worth stating explicitly because they are easy to get wrong:

1. **Metres are never applied to degrees.** The buffer is constructed only after the
   point has been projected into a metre-based CRS. Degrees appear exactly once, in the
   bounding box handed to the spatial index -- and that box is obtained by projecting the
   real metre buffer *back* to WGS84, not by converting metres to degrees by hand.

2. **The index prefilter cannot change a verdict.** STRtree and PostGIS `&&` both compare
   bounding boxes, so they are over-inclusive by design. Every candidate they return is
   then tested against the real geometry; boundaries they exclude cannot intersect the
   search envelope at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from shapely.geometry import GeometryCollection, MultiPolygon, Point, Polygon, mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from app.config import gis_config as gis
from app.config import spatial_config as config
from app.models.boundary_geometry import to_shapely
from app.models.projection import (
    InvalidCoordinateError,
    LocalProjection,
    validate_buffer_meters,
    validate_coordinates,
)
from app.schemas.boundary import BoundaryQuery, GISBoundary
from app.schemas.spatial import (
    BoundaryCollision,
    CollisionType,
    SeverityRuleExplanation,
    Severity,
    SpatialAnalysisResult,
)
from app.services.gis_boundary_service import GISBoundaryService


@dataclass(frozen=True)
class _Measured:
    """One candidate boundary's raw geometric measurements, before classification."""

    boundary: GISBoundary
    distance_meters: float
    intersection_area_sqm: float
    buffer_overlap_percentage: float
    boundary_area_sqm: float
    point_inside: bool
    buffer_intersects: bool
    #: The overlap polygon itself, in the local projected CRS. Retained rather than
    #: discarded after measuring its area, so the API can render the exact conflict
    #: footprint instead of a client-side re-derivation of it.
    intersection: BaseGeometry | None


# ---------------------------------------------------------------------------------
# Classification rules -- pure functions over configuration, independently testable
# ---------------------------------------------------------------------------------


def classify_collision_type(
    *, point_inside: bool, buffer_intersects: bool, distance_meters: float, proximity_meters: float
) -> CollisionType | None:
    """Assign one collision type, or None when the boundary is not relevant at all.

    Evaluated most-severe-first so the categories stay mutually exclusive: a point inside
    a boundary is DIRECT_COLLISION even though its buffer also intersects.
    """
    if point_inside:
        return CollisionType.DIRECT_COLLISION
    if buffer_intersects:
        return CollisionType.BUFFER_COLLISION
    if distance_meters <= proximity_meters:
        return CollisionType.NEARBY
    return None


def overlap_escalation(buffer_overlap_percentage: float) -> int:
    """Extra severity rank earned by how much of the buffer sits inside the boundary."""
    escalation = 0
    for threshold, bump in config.OVERLAP_ESCALATION_RULES:
        if buffer_overlap_percentage >= threshold:
            escalation = bump
    return escalation


def calculate_severity(
    *, collision_type: CollisionType, category: str, buffer_overlap_percentage: float
) -> SeverityRuleExplanation:
    """Compute severity from the three configured additive terms, and show the working.

    Returning the terms alongside the band means any severity in a response can be
    reconstructed from the rules in spatial_config.py -- there is no opaque step.
    """
    base = config.COLLISION_TYPE_BASE_RANK.get(collision_type.value, config.MIN_SEVERITY_RANK)
    sensitivity = config.CATEGORY_SENSITIVITY.get(category, config.CATEGORY_SENSITIVITY_DEFAULT)
    escalation = overlap_escalation(buffer_overlap_percentage)

    total = base + sensitivity + escalation
    clamped = max(config.MIN_SEVERITY_RANK, min(config.MAX_SEVERITY_RANK, total))
    return SeverityRuleExplanation(
        collision_type_rank=base,
        category_sensitivity=sensitivity,
        overlap_escalation=escalation,
        total_rank=total,
        clamped_rank=clamped,
        severity=Severity(config.SEVERITY_BY_RANK[clamped]),
        terms={
            "collision_type": collision_type.value,
            "category": category,
            "buffer_overlap_percentage": buffer_overlap_percentage,
        },
    )


def requires_clearance(*, collision_type: CollisionType, category: str) -> bool:
    """Whether this boundary's collision flags the project for environmental clearance."""
    if collision_type.value in config.CLEARANCE_REQUIRED_COLLISION_TYPES:
        return True
    return (
        collision_type is CollisionType.NEARBY
        and category in config.CLEARANCE_ON_PROXIMITY_CATEGORIES
    )


def proximity_threshold_for(buffer_meters: float) -> float:
    """The NEARBY band's outer radius: the larger of the floor and a multiple of the buffer."""
    return max(config.NEARBY_THRESHOLD_METERS, buffer_meters * config.NEARBY_BUFFER_MULTIPLIER)


# ---------------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------------


class SpatialAnalysisService:
    """Computes project-versus-boundary spatial collisions."""

    def __init__(self, boundary_service: GISBoundaryService, *, projection_strategy: str | None = None) -> None:
        self.boundary_service = boundary_service
        self.projection_strategy = projection_strategy or config.PROJECTION_STRATEGY

    def analyze(
        self,
        latitude: float,
        longitude: float,
        buffer_meters: float = config.DEFAULT_BUFFER_METERS,
        *,
        proximity_meters: float | None = None,
        categories: Iterable[str] | None = None,
        include_demo: bool = True,
        use_index: bool = True,
    ) -> SpatialAnalysisResult:
        """Run the full pipeline for one project location.

        Raises InvalidCoordinateError for unusable coordinates or buffer values. Boundary
        geometry itself is already validated at write time (nothing invalid can be in the
        store), so no repair or re-validation happens on this hot path.

        `use_index=False` skips the spatial-index prefilter and tests every boundary
        exactly. It exists as an audit mode: because the prefilter is over-inclusive by
        construction, both paths must always agree, and the test suite asserts that on
        real datasets. Never use it in production -- it is O(n) in the whole store.
        """
        # 1. Validate.
        latitude, longitude = validate_coordinates(latitude, longitude)
        buffer_meters = validate_buffer_meters(buffer_meters)
        proximity_meters = (
            proximity_threshold_for(buffer_meters)
            if proximity_meters is None
            else validate_buffer_meters(proximity_meters)
        )

        # 2-3. Local metre CRS, and the point projected into it.
        projection = LocalProjection.for_point(latitude, longitude, self.projection_strategy)
        point = projection.to_projected(Point(longitude, latitude))

        # 4. The buffer, built in METRES on the projected point.
        buffer_geom = point.buffer(buffer_meters, quad_segs=config.BUFFER_QUAD_SEGMENTS)
        buffer_area = buffer_geom.area

        # 5. Candidate lookup. The search envelope covers the buffer AND the proximity
        #    band, and is obtained by projecting that metre circle back to degrees -- so
        #    no metre-to-degree arithmetic is ever done by hand.
        search_radius = max(buffer_meters, proximity_meters)
        search_geom = point.buffer(search_radius, quad_segs=config.BUFFER_QUAD_SEGMENTS)
        search_bbox = list(projection.to_geographic(search_geom).bounds)

        query = BoundaryQuery(
            bbox=search_bbox if use_index else None,
            include_demo=include_demo,
            categories=list(categories) if categories else None,
        )
        candidates = self.boundary_service.find(query)

        # 6-8. Exact geometry, in metres, per candidate.
        collisions = [
            collision
            for candidate in candidates
            for collision in self._evaluate(
                candidate,
                projection=projection,
                point=point,
                buffer_geom=buffer_geom,
                buffer_area=buffer_area,
                proximity_meters=proximity_meters,
            )
        ]

        # Most severe first, then nearest; ties broken by id so ordering is total and
        # therefore reproducible across runs.
        collisions.sort(
            key=lambda c: (-c.severity.precedence, -c.collision_type.precedence, c.distance_meters, c.boundary_id)
        )

        overall_type = max(
            (c.collision_type for c in collisions), key=lambda t: t.precedence, default=CollisionType.CLEAR
        )
        overall_severity = max((c.severity for c in collisions), key=lambda s: s.precedence, default=None)
        contains_demo = any(c.is_demo for c in collisions)

        # The tested buffer, in degrees, for map rendering. Derived by projecting the real
        # metre circle back -- the same direction as the index envelope, never by treating
        # metres as degrees.
        buffer_geometry = (
            None if buffer_geom.is_empty else _to_json_geometry(projection.to_geographic(buffer_geom))
        )

        return SpatialAnalysisResult(
            latitude=latitude,
            longitude=longitude,
            buffer_meters=buffer_meters,
            proximity_threshold_meters=proximity_meters,
            collision_type=overall_type,
            severity=overall_severity,
            clearance_required=any(c.clearance_required for c in collisions),
            collision_count=len(collisions),
            collisions=collisions,
            buffer_geometry=buffer_geometry,
            projected_crs=projection.crs,
            boundaries_indexed=self.boundary_service.count(),
            candidates_examined=len(candidates),
            contains_demo_data=contains_demo,
            notice=gis.DEMO_DATA_NOTICE if contains_demo else None,
        )

    # --- per-boundary evaluation ----------------------------------------------------

    def _evaluate(
        self,
        boundary: GISBoundary,
        *,
        projection: LocalProjection,
        point: BaseGeometry,
        buffer_geom: BaseGeometry,
        buffer_area: float,
        proximity_meters: float,
    ) -> list[BoundaryCollision]:
        """Measure and classify one candidate. Returns [] when it is not relevant.

        A list rather than an Optional so the caller can flatten candidates in one
        comprehension without a None filter.
        """
        measured = self._measure(
            boundary,
            projection=projection,
            point=point,
            buffer_geom=buffer_geom,
            buffer_area=buffer_area,
        )

        collision_type = classify_collision_type(
            point_inside=measured.point_inside,
            buffer_intersects=measured.buffer_intersects,
            distance_meters=measured.distance_meters,
            proximity_meters=proximity_meters,
        )
        if collision_type is None:
            return []

        category = boundary.category.value
        explanation = calculate_severity(
            collision_type=collision_type,
            category=category,
            buffer_overlap_percentage=measured.buffer_overlap_percentage,
        )

        return [
            BoundaryCollision(
                boundary_id=boundary.id,
                boundary_name=boundary.name,
                category=boundary.category,
                category_label=boundary.category.label,
                state=boundary.state,
                district=boundary.district,
                distance_meters=round(measured.distance_meters, config.DISTANCE_DECIMALS),
                intersection_area_sqm=round(measured.intersection_area_sqm, config.AREA_DECIMALS),
                buffer_overlap_percentage=round(measured.buffer_overlap_percentage, config.PERCENTAGE_DECIMALS),
                boundary_area_sqm=round(measured.boundary_area_sqm, config.AREA_DECIMALS),
                intersection_geometry=(
                    None
                    if measured.intersection is None
                    else _areal_geojson(projection.to_geographic(measured.intersection))
                ),
                collision_type=collision_type,
                severity=explanation.severity,
                severity_rank=explanation.clamped_rank,
                clearance_required=requires_clearance(collision_type=collision_type, category=category),
                is_demo=boundary.is_demo,
            )
        ]

    def _measure(
        self,
        boundary: GISBoundary,
        *,
        projection: LocalProjection,
        point: BaseGeometry,
        buffer_geom: BaseGeometry,
        buffer_area: float,
    ) -> _Measured:
        """The exact geometry step: project the boundary, then measure in metres.

        `covers` rather than `contains` for the inside test, so a project sitting exactly
        on a boundary's edge counts as inside -- the conservative reading, and the one
        that keeps DIRECT_COLLISION and BUFFER_COLLISION from both being false at the
        boundary line. EDGE_TOLERANCE_METERS extends that to points the coordinate
        transform left a sub-micrometre off the line, which is otherwise decided by
        floating-point rounding rather than by geography.
        """
        geometry = projection.to_projected(to_shapely(boundary.geometry.as_mapping()))

        distance_to_edge = point.distance(geometry)
        point_inside = geometry.covers(point) or distance_to_edge <= config.EDGE_TOLERANCE_METERS
        # 0.0 by definition when inside -- including the on-the-edge case above, whose
        # raw distance is nonzero only because of transform rounding.
        distance_meters = 0.0 if point_inside else distance_to_edge

        intersection = buffer_geom.intersection(geometry)
        intersection_area = 0.0 if intersection.is_empty else intersection.area
        overlap_percentage = (intersection_area / buffer_area * 100.0) if buffer_area > 0 else 0.0

        return _Measured(
            boundary=boundary,
            distance_meters=distance_meters,
            intersection_area_sqm=intersection_area,
            # Floating-point intersection can exceed the buffer area by ~1e-9 %; clamping
            # keeps the value inside the schema's [0, 100] contract.
            buffer_overlap_percentage=min(overlap_percentage, 100.0),
            boundary_area_sqm=geometry.area,
            point_inside=point_inside,
            buffer_intersects=intersection_area > 0.0 or buffer_geom.intersects(geometry),
            intersection=None if intersection.is_empty else intersection,
        )


def _areal_geojson(geometry: BaseGeometry) -> dict | None:
    """Serialize an intersection, keeping only its areal parts.

    A buffer that merely touches a boundary's edge intersects in a line or a point, and a
    mixed result comes back as a GeometryCollection. None of those describe a conflict
    *footprint*, so they are dropped rather than emitted as a degenerate polygon a map
    would draw as an invisible sliver.
    """
    if geometry.is_empty:
        return None
    if isinstance(geometry, (Polygon, MultiPolygon)):
        return _to_json_geometry(geometry)
    if isinstance(geometry, GeometryCollection):
        areal = [part for part in geometry.geoms if isinstance(part, (Polygon, MultiPolygon))]
        if not areal:
            return None
        merged = unary_union(areal)
        return _to_json_geometry(merged) if not merged.is_empty else None
    return None


def _to_json_geometry(geometry: BaseGeometry) -> dict:
    """Shapely's mapping() returns nested tuples; JSON and Pydantic want plain lists."""
    from app.models.boundary_geometry import _to_lists  # shared list coercion

    raw = mapping(geometry)
    return {"type": raw["type"], "coordinates": _to_lists(raw["coordinates"])}


def build_spatial_analysis_service(boundary_service: GISBoundaryService | None = None) -> SpatialAnalysisService:
    """Construct the engine, defaulting to the configured boundary backend.

    Mirrors the other build_* factories: called once at startup, never per request.
    """
    from app.services.gis_boundary_service import build_gis_boundary_service  # local: avoids a cycle

    return SpatialAnalysisService(boundary_service or build_gis_boundary_service())


__all__ = [
    "InvalidCoordinateError",
    "SpatialAnalysisService",
    "build_spatial_analysis_service",
    "calculate_severity",
    "classify_collision_type",
    "proximity_threshold_for",
    "requires_clearance",
]
