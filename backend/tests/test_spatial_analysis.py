"""Tests for the GIS Spatial Analysis Engine (Feature 5).

Test geometry is CONSTRUCTED, never hardcoded: `square_boundary` builds a boundary at an
exact metre offset from the test point by working in the projected CRS and converting
back to degrees, so each test's expected distance is derived from the same geometry the
engine sees rather than from a number typed in by hand. Distances are then independently
cross-checked against pyproj's geodesic solver (`Geod.inv`), which shares no code with
the engine's projection path.

Covers the eleven required cases: CLEAR, DIRECT_COLLISION, BUFFER_COLLISION, NEARBY,
multiple intersections, varying buffer sizes, Polygon, MultiPolygon, invalid coordinates,
invalid geometry, and CRS transformation.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pyproj import Geod
from shapely.geometry import box, mapping

from app.config import spatial_config as config
from app.models.boundary_geometry import GeometryValidationError, to_shapely
from app.models.projection import (
    InvalidCoordinateError,
    LocalProjection,
    utm_epsg,
    validate_buffer_meters,
    validate_coordinates,
)
from app.repositories.gis_boundary_repository import GeoJSONFileBoundaryRepository
from app.schemas.boundary import BoundaryCategory
from app.schemas.spatial import CollisionType, Severity, SpatialAnalysisRequest
from app.services.gis_boundary_service import GISBoundaryService
from app.services.spatial_analysis_service import (
    SpatialAnalysisService,
    calculate_severity,
    classify_collision_type,
    overlap_escalation,
    proximity_threshold_for,
    requires_clearance,
)

# An arbitrary land location used as the project site. Nothing about the tests depends on
# this being a real place -- every boundary is positioned relative to it in metres.
SITE_LAT, SITE_LON = 21.0, 78.0
GEOD = Geod(ellps="WGS84")


def square_boundary(west_m: float, south_m: float, east_m: float, north_m: float,
                    *, lat: float = SITE_LAT, lon: float = SITE_LON) -> dict:
    """A rectangle placed at an exact METRE offset from (lat, lon), returned in degrees.

    Built in the same local projected CRS the engine uses, then converted back, so the
    offsets a test asks for are the offsets the engine measures.
    """
    projection = LocalProjection.for_point(lat, lon)
    return mapping(projection.to_geographic(box(west_m, south_m, east_m, north_m)))


def multipolygon_boundary(*rectangles: tuple[float, float, float, float]) -> dict:
    """A MultiPolygon of disjoint metre-offset rectangles."""
    return {
        "type": "MultiPolygon",
        "coordinates": [square_boundary(*rectangle)["coordinates"] for rectangle in rectangles],
    }


def geodesic_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Independent geodesic distance, for cross-checking the engine's projected maths."""
    return abs(GEOD.inv(lon1, lat1, lon2, lat2)[2])


@pytest.fixture
def boundaries(tmp_path: Path) -> GISBoundaryService:
    """An empty, isolated boundary store."""
    return GISBoundaryService(GeoJSONFileBoundaryRepository(tmp_path / "boundaries.geojson"))


@pytest.fixture
def engine(boundaries: GISBoundaryService) -> SpatialAnalysisService:
    return SpatialAnalysisService(boundaries)


def add(service: GISBoundaryService, name: str, geometry: dict,
        category: str = "FOREST") -> None:
    service.create_boundary(
        name=name, category=category, state="DEMO STATE", district="Demo District",
        geometry=geometry, source="test fixture", last_updated=date(2026, 1, 1), is_demo=True,
    )


# ---------------------------------------------------------------------------------
# 1-4. The four collision types
# ---------------------------------------------------------------------------------


class TestCollisionTypes:
    def test_1_point_outside_everything_is_clear(self, boundaries, engine):
        """A boundary far beyond the proximity threshold produces CLEAR, not a collision."""
        far = proximity_threshold_for(1_000.0) * 4
        add(boundaries, "Distant Forest", square_boundary(far, far, far + 5_000, far + 5_000))

        result = engine.analyze(SITE_LAT, SITE_LON, 1_000)
        assert result.collision_type is CollisionType.CLEAR
        assert result.collisions == [] and result.collision_count == 0
        assert result.severity is None and result.clearance_required is False
        assert "No restricted boundary intersects" in result.summary()

    def test_2_point_inside_boundary_is_a_direct_collision(self, boundaries, engine):
        """The project point lies within the polygon: distance 0, buffer fully enclosed."""
        add(boundaries, "Enclosing Park", square_boundary(-5_000, -5_000, 5_000, 5_000), "NATIONAL_PARK")

        result = engine.analyze(SITE_LAT, SITE_LON, 1_000)
        collision = result.collisions[0]
        assert collision.collision_type is CollisionType.DIRECT_COLLISION
        assert collision.distance_meters == 0.0
        assert collision.buffer_overlap_percentage == pytest.approx(100.0, abs=0.01)
        assert collision.clearance_required is True

    def test_3_buffer_intersecting_boundary_is_a_buffer_collision(self, boundaries, engine):
        """Point outside, but the 1 km buffer reaches a boundary whose edge is 500 m away."""
        add(boundaries, "Adjacent Forest", square_boundary(500, -5_000, 6_000, 5_000))

        result = engine.analyze(SITE_LAT, SITE_LON, 1_000)
        collision = result.collisions[0]
        assert collision.collision_type is CollisionType.BUFFER_COLLISION
        assert collision.distance_meters == pytest.approx(500.0, abs=1.0)
        assert 0 < collision.buffer_overlap_percentage < 50
        assert collision.intersection_area_sqm > 0

    def test_4_boundary_beyond_the_buffer_but_within_proximity_is_nearby(self, boundaries, engine):
        """3 km away with a 1 km buffer: no intersection, but inside the 5 km NEARBY band."""
        add(boundaries, "Nearby Forest", square_boundary(3_000, -5_000, 8_000, 5_000))

        result = engine.analyze(SITE_LAT, SITE_LON, 1_000)
        collision = result.collisions[0]
        assert collision.collision_type is CollisionType.NEARBY
        assert collision.distance_meters == pytest.approx(3_000.0, abs=2.0)
        assert collision.intersection_area_sqm == 0.0
        assert collision.buffer_overlap_percentage == 0.0

    def test_boundary_exactly_at_the_proximity_edge_is_included(self, boundaries, engine):
        """The NEARBY band is inclusive: distance <= threshold counts."""
        threshold = proximity_threshold_for(1_000.0)
        add(boundaries, "Edge Forest", square_boundary(threshold, -5_000, threshold + 5_000, 5_000))

        result = engine.analyze(SITE_LAT, SITE_LON, 1_000)
        assert result.collision_type is CollisionType.NEARBY

    def test_point_on_the_boundary_edge_counts_as_inside(self, boundaries, engine):
        """`covers` semantics: a site exactly on the line is a DIRECT_COLLISION, not a gap.

        Built in degrees rather than via `square_boundary`, deliberately: the site sits on
        the boundary's western edge at longitude SITE_LON, which is also the AEQD central
        meridian, so that edge projects to exactly x=0. A metre-offset construction would
        round-trip through two transforms and land the edge within float noise of the
        point, which is not a meaningful thing to assert about.
        """
        add(boundaries, "Edge-Touching Park",
            mapping(box(SITE_LON, SITE_LAT - 0.05, SITE_LON + 0.05, SITE_LAT + 0.05)), "NATIONAL_PARK")

        result = engine.analyze(SITE_LAT, SITE_LON, 1_000)
        assert result.collisions[0].collision_type is CollisionType.DIRECT_COLLISION
        assert result.collisions[0].distance_meters == 0.0

    def test_zero_buffer_still_detects_containment_and_proximity(self, boundaries, engine):
        """buffer_meters=0 tests the bare point; DIRECT and NEARBY still apply."""
        add(boundaries, "Enclosing Park", square_boundary(-5_000, -5_000, 5_000, 5_000), "NATIONAL_PARK")
        result = engine.analyze(SITE_LAT, SITE_LON, 0)
        assert result.collisions[0].collision_type is CollisionType.DIRECT_COLLISION
        # With no buffer there is no area to overlap, so the percentage is 0 by definition.
        assert result.collisions[0].buffer_overlap_percentage == 0.0


# ---------------------------------------------------------------------------------
# 5. Multiple intersections
# ---------------------------------------------------------------------------------


class TestMultipleIntersections:
    @pytest.fixture
    def populated(self, boundaries: GISBoundaryService) -> GISBoundaryService:
        add(boundaries, "Enclosing Tiger Reserve", square_boundary(-4_000, -4_000, 4_000, 4_000), "TIGER_RESERVE")
        add(boundaries, "Overlapping Forest", square_boundary(600, -3_000, 6_000, 3_000), "FOREST")
        add(boundaries, "Nearby Sanctuary", square_boundary(2_500, -3_000, 7_000, 3_000), "WILDLIFE_SANCTUARY")
        add(boundaries, "Distant Wetland", square_boundary(40_000, 40_000, 45_000, 45_000), "RAMSAR_WETLAND")
        return boundaries

    def test_every_relevant_boundary_is_reported_and_the_irrelevant_one_is_not(self, populated, engine):
        result = engine.analyze(SITE_LAT, SITE_LON, 1_000)
        found = {c.boundary_name: c.collision_type for c in result.collisions}
        assert found == {
            "Enclosing Tiger Reserve": CollisionType.DIRECT_COLLISION,
            "Overlapping Forest": CollisionType.BUFFER_COLLISION,
            "Nearby Sanctuary": CollisionType.NEARBY,
        }
        assert "Distant Wetland" not in found

    def test_overall_verdict_takes_the_most_severe_collision(self, populated, engine):
        result = engine.analyze(SITE_LAT, SITE_LON, 1_000)
        assert result.collision_type is CollisionType.DIRECT_COLLISION
        assert result.severity is Severity.CRITICAL
        assert result.clearance_required is True
        assert result.collision_count == 3

    def test_results_are_ordered_most_severe_then_nearest(self, populated, engine):
        collisions = engine.analyze(SITE_LAT, SITE_LON, 1_000).collisions
        keys = [(-c.severity.precedence, -c.collision_type.precedence, c.distance_meters) for c in collisions]
        assert keys == sorted(keys)

    def test_repeated_analysis_is_byte_identical(self, populated, engine):
        """Determinism: no randomness, no time dependence, no model in the path."""
        first = engine.analyze(SITE_LAT, SITE_LON, 1_000)
        second = engine.analyze(SITE_LAT, SITE_LON, 1_000)
        assert first.model_dump() == second.model_dump()

    def test_category_filter_narrows_the_scan(self, populated, engine):
        result = engine.analyze(SITE_LAT, SITE_LON, 1_000, categories=["FOREST"])
        assert [c.boundary_name for c in result.collisions] == ["Overlapping Forest"]


# ---------------------------------------------------------------------------------
# 6. Different buffer sizes
# ---------------------------------------------------------------------------------


class TestBufferSizes:
    @pytest.fixture
    def populated(self, boundaries: GISBoundaryService) -> GISBoundaryService:
        # Near edge exactly 2 km east of the site.
        add(boundaries, "Forest At 2km", square_boundary(2_000, -10_000, 12_000, 10_000))
        return boundaries

    @pytest.mark.parametrize(
        "buffer_meters, expected",
        [
            (0, CollisionType.NEARBY),          # bare point, boundary still inside proximity
            (500, CollisionType.NEARBY),        # buffer falls short of the 2 km edge
            (1_999, CollisionType.NEARBY),      # just short
            (2_001, CollisionType.BUFFER_COLLISION),  # just reaches
            (5_000, CollisionType.BUFFER_COLLISION),
        ],
    )
    def test_buffer_size_changes_the_classification(self, populated, engine, buffer_meters, expected):
        result = engine.analyze(SITE_LAT, SITE_LON, buffer_meters)
        assert result.collisions[0].collision_type is expected

    def test_distance_is_independent_of_buffer_size(self, populated, engine):
        """Distance is a property of the geometry, not of the buffer used to test it."""
        distances = {engine.analyze(SITE_LAT, SITE_LON, b).collisions[0].distance_meters
                     for b in (0, 500, 2_001, 5_000)}
        assert len(distances) == 1
        assert distances.pop() == pytest.approx(2_000.0, abs=2.0)

    def test_overlap_percentage_grows_with_penetration_not_with_buffer_area(self, populated, engine):
        """A buffer reaching further into the boundary covers a larger share of itself."""
        overlaps = [engine.analyze(SITE_LAT, SITE_LON, b).collisions[0].buffer_overlap_percentage
                    for b in (2_500, 4_000, 10_000)]
        assert overlaps == sorted(overlaps)
        assert all(0 <= value <= 100 for value in overlaps)

    def test_proximity_band_widens_with_large_buffers(self):
        """Effective threshold = max(absolute floor, buffer x multiplier)."""
        assert proximity_threshold_for(100) == config.NEARBY_THRESHOLD_METERS
        assert proximity_threshold_for(10_000) == 10_000 * config.NEARBY_BUFFER_MULTIPLIER

    def test_buffer_outside_the_configured_bounds_is_rejected(self, engine):
        for bad in (-1, config.MAX_BUFFER_METERS + 1, float("nan"), float("inf"), "1000", None, True):
            with pytest.raises(InvalidCoordinateError):
                engine.analyze(SITE_LAT, SITE_LON, bad)


# ---------------------------------------------------------------------------------
# 7-8. Polygon and MultiPolygon
# ---------------------------------------------------------------------------------


class TestGeometryTypes:
    def test_7_polygon_with_a_hole_excludes_points_in_the_hole(self, boundaries, engine):
        """A ring-shaped eco-sensitive zone: the site sits in the hole, so it is not inside."""
        projection = LocalProjection.for_point(SITE_LAT, SITE_LON)
        outer = box(-6_000, -6_000, 6_000, 6_000)
        inner = box(-3_000, -3_000, 3_000, 3_000)
        add(boundaries, "Ring Zone", mapping(projection.to_geographic(outer.difference(inner))),
            "ECO_SENSITIVE_ZONE")

        result = engine.analyze(SITE_LAT, SITE_LON, 1_000)
        collision = result.collisions[0]
        assert collision.collision_type is CollisionType.NEARBY, "point in the hole is not inside the polygon"
        assert collision.distance_meters == pytest.approx(3_000.0, abs=2.0)

        # Extending the buffer into the ring turns it into a real intersection.
        reaching = engine.analyze(SITE_LAT, SITE_LON, 4_000)
        assert reaching.collisions[0].collision_type is CollisionType.BUFFER_COLLISION

    def test_8_multipolygon_is_measured_against_its_nearest_part(self, boundaries, engine):
        """Distance and area come from the whole MultiPolygon, not just the first part."""
        add(boundaries, "Two-Part Reserve",
            multipolygon_boundary((1_500, -2_000, 4_000, 2_000), (20_000, 20_000, 25_000, 25_000)),
            "TIGER_RESERVE")

        result = engine.analyze(SITE_LAT, SITE_LON, 2_000)
        collision = result.collisions[0]
        assert collision.collision_type is CollisionType.BUFFER_COLLISION
        assert collision.distance_meters == pytest.approx(1_500.0, abs=2.0)
        # Both parts contribute to the reported boundary area.
        assert collision.boundary_area_sqm == pytest.approx(2_500 * 4_000 + 5_000 * 5_000, rel=0.01)

    def test_8b_multipolygon_containment_uses_the_containing_part(self, boundaries, engine):
        add(boundaries, "Two-Part Reserve",
            multipolygon_boundary((-2_000, -2_000, 2_000, 2_000), (20_000, 20_000, 25_000, 25_000)),
            "TIGER_RESERVE")
        result = engine.analyze(SITE_LAT, SITE_LON, 500)
        assert result.collisions[0].collision_type is CollisionType.DIRECT_COLLISION
        assert result.collisions[0].distance_meters == 0.0

    def test_intersection_area_matches_the_analytic_value(self, boundaries, engine):
        """A half-plane cut through the buffer must overlap almost exactly half its area."""
        add(boundaries, "Half Plane", square_boundary(0, -50_000, 50_000, 50_000))
        result = engine.analyze(SITE_LAT, SITE_LON, 1_000)
        collision = result.collisions[0]
        assert collision.buffer_overlap_percentage == pytest.approx(50.0, abs=0.5)


# ---------------------------------------------------------------------------------
# 9. Invalid coordinates
# ---------------------------------------------------------------------------------


class TestInvalidCoordinates:
    @pytest.mark.parametrize(
        "latitude, longitude",
        [
            (91.0, 78.0), (-90.5, 78.0), (21.0, 181.0), (21.0, -180.5),
            (float("nan"), 78.0), (21.0, float("nan")),
            (float("inf"), 78.0), (21.0, float("-inf")),
            (None, 78.0), (21.0, None), ("21.0", 78.0), (21.0, [78.0]),
            (True, 78.0), (21.0, False),
        ],
    )
    def test_9_invalid_coordinates_are_rejected(self, engine, latitude, longitude):
        with pytest.raises(InvalidCoordinateError):
            engine.analyze(latitude, longitude, 1_000)

    @pytest.mark.parametrize(
        "latitude, longitude", [(90.0, 180.0), (-90.0, -180.0), (0.0, 0.0), (21, 78)]
    )
    def test_extreme_but_valid_coordinates_are_accepted(self, engine, latitude, longitude):
        assert validate_coordinates(latitude, longitude) == (float(latitude), float(longitude))
        assert engine.analyze(latitude, longitude, 1_000).collision_type is CollisionType.CLEAR

    def test_request_schema_rejects_the_same_values(self):
        for payload in (
            {"latitude": 91, "longitude": 78}, {"latitude": 21, "longitude": 181},
            {"latitude": float("nan"), "longitude": 78}, {"latitude": 21, "longitude": 78, "buffer_meters": -1},
        ):
            with pytest.raises(ValueError):
                SpatialAnalysisRequest(**payload)

    def test_validate_buffer_meters_accepts_the_configured_range(self):
        assert validate_buffer_meters(config.MIN_BUFFER_METERS) == config.MIN_BUFFER_METERS
        assert validate_buffer_meters(config.MAX_BUFFER_METERS) == config.MAX_BUFFER_METERS


# ---------------------------------------------------------------------------------
# 10. Invalid geometry
# ---------------------------------------------------------------------------------


class TestInvalidGeometry:
    @pytest.mark.parametrize(
        "geometry",
        [
            {"type": "Polygon", "coordinates": [[[0, 0], [2, 2], [2, 0], [0, 2], [0, 0]]]},   # self-intersecting
            {"type": "Point", "coordinates": [78.0, 21.0]},                                    # not areal
            {"type": "LineString", "coordinates": [[78.0, 21.0], [78.1, 21.1]]},
            {"type": "Polygon", "coordinates": [[[200.0, 21.0], [201.0, 21.0], [201.0, 22.0], [200.0, 21.0]]]},
        ],
    )
    def test_10_invalid_geometry_never_reaches_the_engine(self, boundaries, geometry):
        """Validation happens at write time, so the analysis hot path cannot meet bad geometry."""
        with pytest.raises((GeometryValidationError, ValueError)):
            add(boundaries, "Invalid", geometry)

    def test_the_store_stays_empty_after_a_rejected_write(self, boundaries, engine):
        with pytest.raises(ValueError):
            add(boundaries, "Invalid", {"type": "Polygon", "coordinates": [[[0, 0], [2, 2], [2, 0], [0, 2], [0, 0]]]})
        assert boundaries.count() == 0
        assert engine.analyze(SITE_LAT, SITE_LON, 1_000).collision_type is CollisionType.CLEAR

    def test_a_valid_boundary_alongside_a_rejected_one_still_analyses(self, boundaries, engine):
        add(boundaries, "Valid Forest", square_boundary(500, -3_000, 5_000, 3_000))
        with pytest.raises(ValueError):
            add(boundaries, "Invalid", {"type": "Point", "coordinates": [78.0, 21.0]})
        assert engine.analyze(SITE_LAT, SITE_LON, 1_000).collision_count == 1


# ---------------------------------------------------------------------------------
# 11. CRS transformation
# ---------------------------------------------------------------------------------


class TestCRSTransformation:
    def test_11_engine_distance_matches_an_independent_geodesic_solver(self, boundaries, engine):
        """Cross-check against pyproj's Geod, which shares no code with the engine's path."""
        offset = 2_500.0
        add(boundaries, "Offset Forest", square_boundary(offset, -8_000, offset + 5_000, 8_000))

        result = engine.analyze(SITE_LAT, SITE_LON, 1_000)
        measured = result.collisions[0].distance_meters

        # The nearest point on that boundary lies due east of the site at `offset` metres.
        east_lon, east_lat, _ = GEOD.fwd(SITE_LON, SITE_LAT, 90.0, offset)
        assert measured == pytest.approx(geodesic_distance_m(SITE_LAT, SITE_LON, east_lat, east_lon), abs=2.0)

    def test_projection_is_metre_based_and_reported(self, boundaries, engine):
        result = engine.analyze(SITE_LAT, SITE_LON, 1_000)
        assert "+units=m" in result.projected_crs and "+proj=aeqd" in result.projected_crs

    def test_aeqd_places_the_project_point_at_the_origin(self):
        from shapely.geometry import Point

        projected = LocalProjection.for_point(SITE_LAT, SITE_LON).to_projected(Point(SITE_LON, SITE_LAT))
        assert (projected.x, projected.y) == pytest.approx((0.0, 0.0), abs=1e-6)

    def test_projection_round_trips(self):
        projection = LocalProjection.for_point(SITE_LAT, SITE_LON)
        original = box(-4_000, -4_000, 4_000, 4_000)
        assert projection.to_projected(projection.to_geographic(original)).bounds == pytest.approx(
            original.bounds, abs=1e-3
        )

    def test_utm_strategy_agrees_with_aeqd(self, boundaries):
        """Two independent projected CRSs must not disagree about a real-world distance."""
        add(boundaries, "Offset Forest", square_boundary(3_000, -8_000, 8_000, 8_000))
        aeqd = SpatialAnalysisService(boundaries, projection_strategy="aeqd").analyze(SITE_LAT, SITE_LON, 1_000)
        utm = SpatialAnalysisService(boundaries, projection_strategy="utm").analyze(SITE_LAT, SITE_LON, 1_000)

        assert utm.projected_crs == utm_epsg(SITE_LAT, SITE_LON)
        assert utm.collisions[0].collision_type is aeqd.collisions[0].collision_type
        assert utm.collisions[0].distance_meters == pytest.approx(aeqd.collisions[0].distance_meters, rel=0.005)

    def test_buffer_is_a_true_metre_circle_not_a_degree_shape(self, boundaries, engine):
        """The guard against the classic bug: buffering lon/lat by 1000 'units'.

        A 1 km buffer must reach a boundary 900 m away and must NOT reach one 1.1 km away.
        A degree-based buffer of 1000 would span the planet and match both.
        """
        add(boundaries, "Just Inside", square_boundary(900, -50, 1_200, 50))
        add(boundaries, "Just Outside", square_boundary(1_100, 2_000, 4_000, 4_000))

        collisions = {c.boundary_name: c.collision_type for c in engine.analyze(SITE_LAT, SITE_LON, 1_000).collisions}
        assert collisions["Just Inside"] is CollisionType.BUFFER_COLLISION
        assert collisions["Just Outside"] is CollisionType.NEARBY

    def test_buffer_area_matches_a_true_circle(self, boundaries, engine):
        """pi*r^2 within the discretization error of the configured quad_segs."""
        import math

        add(boundaries, "Half Plane", square_boundary(0, -50_000, 50_000, 50_000))
        collision = engine.analyze(SITE_LAT, SITE_LON, 1_000).collisions[0]
        implied_buffer_area = collision.intersection_area_sqm / (collision.buffer_overlap_percentage / 100.0)
        assert implied_buffer_area == pytest.approx(math.pi * 1_000**2, rel=0.005)

    @pytest.mark.parametrize("latitude", [0.0, 21.0, 45.0, 60.0, 78.0])
    def test_accuracy_holds_across_latitudes(self, tmp_path, latitude):
        """A degree-based buffer would degrade badly with latitude; a projected one does not."""
        service = GISBoundaryService(GeoJSONFileBoundaryRepository(tmp_path / f"{latitude}.geojson"))
        service.create_boundary(
            name="Offset Forest", category="FOREST", state="DEMO STATE",
            geometry=square_boundary(2_000, -8_000, 8_000, 8_000, lat=latitude, lon=10.0),
            source="test fixture", last_updated=date(2026, 1, 1), is_demo=True,
        )
        collision = SpatialAnalysisService(service).analyze(latitude, 10.0, 1_000).collisions[0]
        assert collision.distance_meters == pytest.approx(2_000.0, abs=5.0)


# ---------------------------------------------------------------------------------
# Severity, clearance, and the spatial index
# ---------------------------------------------------------------------------------


class TestSeverityRules:
    def test_severity_is_the_sum_of_the_three_configured_terms(self):
        explanation = calculate_severity(
            collision_type=CollisionType.BUFFER_COLLISION, category="NATIONAL_PARK", buffer_overlap_percentage=0.0
        )
        assert explanation.collision_type_rank == config.COLLISION_TYPE_BASE_RANK["BUFFER_COLLISION"]
        assert explanation.category_sensitivity == config.CATEGORY_SENSITIVITY["NATIONAL_PARK"]
        assert explanation.overlap_escalation == 0
        assert explanation.total_rank == explanation.collision_type_rank + explanation.category_sensitivity
        assert explanation.severity is Severity(config.SEVERITY_BY_RANK[explanation.clamped_rank])

    def test_ranks_are_clamped_into_the_configured_band(self):
        explanation = calculate_severity(
            collision_type=CollisionType.DIRECT_COLLISION, category="TIGER_RESERVE", buffer_overlap_percentage=100.0
        )
        assert explanation.total_rank > config.MAX_SEVERITY_RANK
        assert explanation.clamped_rank == config.MAX_SEVERITY_RANK
        assert explanation.severity is Severity.CRITICAL

    @pytest.mark.parametrize("percentage, expected", [(0.0, 0), (24.9, 0), (25.0, 1), (59.9, 1), (60.0, 2), (100.0, 2)])
    def test_overlap_escalation_follows_the_configured_thresholds(self, percentage, expected):
        assert overlap_escalation(percentage) == expected

    def test_a_more_sensitive_category_never_scores_lower(self):
        sensitive = calculate_severity(
            collision_type=CollisionType.NEARBY, category="NATIONAL_PARK", buffer_overlap_percentage=0.0
        )
        ordinary = calculate_severity(
            collision_type=CollisionType.NEARBY, category="FOREST", buffer_overlap_percentage=0.0
        )
        assert sensitive.clamped_rank >= ordinary.clamped_rank

    def test_no_severity_threshold_is_hardcoded_in_the_service(self):
        """Guards the 'do not scatter thresholds through the code' requirement."""
        import re

        source = Path("app/services/spatial_analysis_service.py").read_text(encoding="utf-8")
        code = "\n".join(
            line.split("#")[0] for line in source.splitlines() if not line.lstrip().startswith("#")
        )
        # Strip docstrings before scanning, so prose about the rules is not mistaken for code.
        code = re.sub(r'"""(?:.|\n)*?"""', "", code)
        numbers = {float(match) for match in re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])", code)}
        assert numbers <= {0.0, 100.0, 1.0}, f"unexpected numeric literals in the engine: {sorted(numbers)}"

    def test_severity_functions_are_pure(self):
        args = dict(collision_type=CollisionType.NEARBY, category="FOREST", buffer_overlap_percentage=10.0)
        assert calculate_severity(**args).model_dump() == calculate_severity(**args).model_dump()


class TestClearanceRules:
    @pytest.mark.parametrize("collision_type", [CollisionType.DIRECT_COLLISION, CollisionType.BUFFER_COLLISION])
    def test_intersections_always_require_clearance(self, collision_type):
        assert requires_clearance(collision_type=collision_type, category="FOREST") is True

    def test_proximity_requires_clearance_only_for_the_configured_categories(self):
        assert requires_clearance(collision_type=CollisionType.NEARBY, category="NATIONAL_PARK") is True
        assert requires_clearance(collision_type=CollisionType.NEARBY, category="FOREST") is False

    def test_overall_flag_is_true_when_any_boundary_requires_it(self, boundaries, engine):
        # Both boundaries sit ~3 km away -- NEARBY, no intersection. Only the national
        # park's category escalates proximity into a clearance flag.
        add(boundaries, "Nearby Forest", square_boundary(3_000, -1_000, 8_000, 1_000), "FOREST")
        first = engine.analyze(SITE_LAT, SITE_LON, 1_000)
        assert first.collision_type is CollisionType.NEARBY and first.clearance_required is False

        add(boundaries, "Nearby Park", square_boundary(-1_000, 3_000, 1_000, 8_000), "NATIONAL_PARK")
        second = engine.analyze(SITE_LAT, SITE_LON, 1_000)
        assert second.collision_count == 2 and second.clearance_required is True


class TestSpatialIndexing:
    def test_the_index_prefilters_instead_of_scanning_every_boundary(self, boundaries, engine):
        """100 far-away boundaries plus one near one: the index must not hand over all 101."""
        for i in range(100):
            offset = 200_000 + i * 20_000
            add(boundaries, f"Far Forest {i}", square_boundary(offset, offset, offset + 10_000, offset + 10_000))
        add(boundaries, "Near Forest", square_boundary(500, -3_000, 5_000, 3_000))

        result = engine.analyze(SITE_LAT, SITE_LON, 1_000)
        assert result.boundaries_indexed == 101
        assert result.candidates_examined < result.boundaries_indexed
        assert result.collision_count == 1

    def test_prefiltering_never_drops_a_real_collision(self, boundaries, engine):
        """Same dataset, index prefilter vs an exhaustive scan: identical verdicts."""
        for index, offset in enumerate((300, 1_500, 4_000, 30_000, 120_000)):
            add(boundaries, f"Forest {index}", square_boundary(offset, -2_000, offset + 3_000, 2_000))

        indexed = engine.analyze(SITE_LAT, SITE_LON, 2_000, use_index=True)
        exhaustive = engine.analyze(SITE_LAT, SITE_LON, 2_000, use_index=False)

        assert indexed.candidates_examined < exhaustive.candidates_examined
        assert exhaustive.candidates_examined == boundaries.count()
        # The prefilter may only remove boundaries that cannot possibly be relevant, so
        # the verdicts themselves must be identical.
        assert indexed.model_dump(exclude={"candidates_examined"}) == exhaustive.model_dump(
            exclude={"candidates_examined"}
        )

    def test_index_is_rebuilt_after_the_store_changes(self, boundaries, engine):
        assert engine.analyze(SITE_LAT, SITE_LON, 1_000).collision_type is CollisionType.CLEAR
        add(boundaries, "New Forest", square_boundary(500, -3_000, 5_000, 3_000))
        assert engine.analyze(SITE_LAT, SITE_LON, 1_000).collision_type is CollisionType.BUFFER_COLLISION
        boundaries.clear()
        assert engine.analyze(SITE_LAT, SITE_LON, 1_000).collision_type is CollisionType.CLEAR

    def test_bbox_candidates_uses_the_strtree(self, boundaries):
        add(boundaries, "Near Forest", square_boundary(500, -3_000, 5_000, 3_000))
        add(boundaries, "Far Forest", square_boundary(300_000, 300_000, 310_000, 310_000))
        repository = boundaries.repository
        near = repository.bbox_candidates((77.9, 20.9, 78.1, 21.1))
        assert len(near) == 1
        assert repository.bbox_candidates((0.0, 0.0, 1.0, 1.0)) == set()


class TestDemoDataProvenance:
    def test_collisions_against_demo_boundaries_carry_the_notice(self, boundaries, engine):
        add(boundaries, "Demo Forest", square_boundary(500, -3_000, 5_000, 3_000))
        result = engine.analyze(SITE_LAT, SITE_LON, 1_000)
        assert result.contains_demo_data is True
        assert result.notice and "NOT OFFICIAL" in result.notice
        assert result.collisions[0].is_demo is True

    def test_demo_boundaries_can_be_excluded(self, boundaries, engine):
        add(boundaries, "Demo Forest", square_boundary(500, -3_000, 5_000, 3_000))
        assert engine.analyze(SITE_LAT, SITE_LON, 1_000, include_demo=False).collision_type is CollisionType.CLEAR


class TestClassificationPrimitives:
    @pytest.mark.parametrize(
        "inside, intersects, distance, expected",
        [
            (True, True, 0.0, CollisionType.DIRECT_COLLISION),
            (True, False, 0.0, CollisionType.DIRECT_COLLISION),   # containment wins outright
            (False, True, 10.0, CollisionType.BUFFER_COLLISION),
            (False, False, 4_000.0, CollisionType.NEARBY),
            (False, False, 5_000.0, CollisionType.NEARBY),        # inclusive at the threshold
            (False, False, 5_000.1, None),
        ],
    )
    def test_classification_is_mutually_exclusive_and_ordered(self, inside, intersects, distance, expected):
        assert classify_collision_type(
            point_inside=inside, buffer_intersects=intersects, distance_meters=distance, proximity_meters=5_000.0
        ) is expected

    def test_category_is_reported_as_the_shared_enum(self, boundaries, engine):
        add(boundaries, "Demo Reserve", square_boundary(500, -3_000, 5_000, 3_000), "TIGER_RESERVE")
        collision = engine.analyze(SITE_LAT, SITE_LON, 1_000).collisions[0]
        assert collision.category is BoundaryCategory.TIGER_RESERVE
        assert collision.category_label == "Tiger Reserve"


class TestIntersectionGeometry:
    """The exact overlap footprint the map draws must come from the engine, not the client."""

    def test_intersecting_boundary_returns_an_areal_overlap(self, boundaries, engine):
        add(boundaries, "Half Plane", square_boundary(0, -50_000, 50_000, 50_000))
        collision = engine.analyze(SITE_LAT, SITE_LON, 1_000).collisions[0]

        assert collision.intersection_geometry is not None
        assert collision.intersection_geometry["type"] in {"Polygon", "MultiPolygon"}

    def test_overlap_geometry_matches_the_reported_overlap_area(self, boundaries, engine):
        """Guards against the polygon and the number drifting apart."""
        add(boundaries, "Half Plane", square_boundary(0, -50_000, 50_000, 50_000))
        collision = engine.analyze(SITE_LAT, SITE_LON, 1_000).collisions[0]

        projection = LocalProjection.for_point(SITE_LAT, SITE_LON)
        drawn = projection.to_projected(to_shapely(collision.intersection_geometry))
        assert drawn.area == pytest.approx(collision.intersection_area_sqm, rel=1e-6)

    def test_overlap_lies_inside_both_the_buffer_and_the_boundary(self, boundaries, engine):
        add(boundaries, "Adjacent Forest", square_boundary(500, -5_000, 6_000, 5_000))
        result = engine.analyze(SITE_LAT, SITE_LON, 1_000)
        collision = result.collisions[0]

        projection = LocalProjection.for_point(SITE_LAT, SITE_LON)
        overlap = projection.to_projected(to_shapely(collision.intersection_geometry))
        buffer_geom = projection.to_projected(to_shapely(result.buffer_geometry))
        boundary = projection.to_projected(to_shapely(boundaries.find()[0].geometry.as_mapping()))

        assert buffer_geom.buffer(1e-6).covers(overlap)
        assert boundary.buffer(1e-6).covers(overlap)

    def test_nearby_boundary_has_no_overlap_geometry(self, boundaries, engine):
        """NEARBY means no intersection, so there is nothing to draw."""
        add(boundaries, "Nearby Forest", square_boundary(3_000, -5_000, 8_000, 5_000))
        assert engine.analyze(SITE_LAT, SITE_LON, 1_000).collisions[0].intersection_geometry is None

    def test_direct_collision_overlap_is_the_whole_buffer(self, boundaries, engine):
        add(boundaries, "Enclosing Park", square_boundary(-5_000, -5_000, 5_000, 5_000), "NATIONAL_PARK")
        result = engine.analyze(SITE_LAT, SITE_LON, 1_000)
        collision = result.collisions[0]

        projection = LocalProjection.for_point(SITE_LAT, SITE_LON)
        overlap = projection.to_projected(to_shapely(collision.intersection_geometry))
        buffer_geom = projection.to_projected(to_shapely(result.buffer_geometry))
        assert overlap.area == pytest.approx(buffer_geom.area, rel=1e-6)

    def test_multipolygon_boundary_can_yield_a_multipolygon_overlap(self, boundaries, engine):
        """Two separate parts both reaching into the buffer produce two overlap pieces."""
        add(boundaries, "Two-Part Reserve",
            multipolygon_boundary((-3_000, -400, -600, 400), (600, -400, 3_000, 400)), "TIGER_RESERVE")
        collision = engine.analyze(SITE_LAT, SITE_LON, 1_000).collisions[0]
        assert collision.intersection_geometry["type"] == "MultiPolygon"
        assert len(collision.intersection_geometry["coordinates"]) == 2

    def test_zero_buffer_produces_no_overlap_geometry(self, boundaries, engine):
        add(boundaries, "Enclosing Park", square_boundary(-5_000, -5_000, 5_000, 5_000), "NATIONAL_PARK")
        assert engine.analyze(SITE_LAT, SITE_LON, 0).collisions[0].intersection_geometry is None

    def test_edge_touching_boundary_yields_no_degenerate_polygon(self, boundaries, engine):
        """A buffer meeting a boundary along a line has no area, so nothing is emitted."""
        add(boundaries, "Edge Forest", square_boundary(1_000, -5_000, 6_000, 5_000))
        collision = engine.analyze(SITE_LAT, SITE_LON, 1_000).collisions[0]
        geometry = collision.intersection_geometry
        assert geometry is None or geometry["type"] in {"Polygon", "MultiPolygon"}

    def test_overlap_geometry_is_deterministic(self, boundaries, engine):
        add(boundaries, "Adjacent Forest", square_boundary(500, -5_000, 6_000, 5_000))
        first = engine.analyze(SITE_LAT, SITE_LON, 1_000).collisions[0].intersection_geometry
        second = engine.analyze(SITE_LAT, SITE_LON, 1_000).collisions[0].intersection_geometry
        assert first == second
