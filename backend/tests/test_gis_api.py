"""API tests for the GIS REST endpoints (Feature 6).

Each test runs against a real FastAPI app wired to isolated temp stores -- no mocks of the
spatial engine, so every assertion here exercises the same computational-geometry path the
production service uses. Boundary geometry is constructed at exact metre offsets from the
test point (as in tests/test_spatial_analysis.py), so expected outcomes are derived from
the geometry rather than typed in.

Authentication is exercised with a real token registry: three principals, one per role.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import box, mapping

from app.api.security import TokenRegistry
from app.config import spatial_config as spatial_config
from app.config.auth_config import Permission, Role
from app.main import app
from app.models.projection import LocalProjection
from app.repositories.assessment_repository import JSONFileAssessmentRepository
from app.repositories.gis_boundary_repository import GeoJSONFileBoundaryRepository
from app.services.assessment_service import AssessmentService
from app.services.gis_boundary_service import GISBoundaryService
from app.services.spatial_analysis_service import SpatialAnalysisService

BASE = "/api/v1/gis"
SITE_LAT, SITE_LON = 21.0, 78.0

VIEWER_TOKEN = "test-token-viewer"
ANALYST_TOKEN = "test-token-analyst"
ADMIN_TOKEN = "test-token-admin"

VIEWER = {"Authorization": f"Bearer {VIEWER_TOKEN}"}
ANALYST = {"Authorization": f"Bearer {ANALYST_TOKEN}"}
ADMIN = {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def square_boundary(west_m: float, south_m: float, east_m: float, north_m: float) -> dict:
    """A rectangle at an exact metre offset from the test site, expressed in degrees."""
    projection = LocalProjection.for_point(SITE_LAT, SITE_LON)
    return mapping(projection.to_geographic(box(west_m, south_m, east_m, north_m)))


def multipolygon_boundary(*rectangles: tuple[float, float, float, float]) -> dict:
    """A MultiPolygon of disjoint metre-offset rectangles around the test site."""
    return {
        "type": "MultiPolygon",
        "coordinates": [square_boundary(*rectangle)["coordinates"] for rectangle in rectangles],
    }


@pytest.fixture
def boundaries(tmp_path: Path) -> GISBoundaryService:
    return GISBoundaryService(GeoJSONFileBoundaryRepository(tmp_path / "boundaries.geojson"))


@pytest.fixture
def assessments(tmp_path: Path) -> AssessmentService:
    return AssessmentService(JSONFileAssessmentRepository(tmp_path / "assessments.json"))


@pytest.fixture
def gis_client(boundaries: GISBoundaryService, assessments: AssessmentService) -> Iterator[TestClient]:
    """A client whose app.state points at isolated stores and a known token registry.

    Overriding app.state after startup (rather than patching modules) is exactly how the
    production app is wired, so these tests exercise the real dependency path.
    """
    with TestClient(app) as client:
        previous = {
            key: getattr(app.state, key, None)
            for key in ("gis_boundary_service", "spatial_analysis_service", "assessment_service",
                        "token_registry", "auth_mode")
        }
        app.state.gis_boundary_service = boundaries
        app.state.spatial_analysis_service = SpatialAnalysisService(boundaries)
        app.state.assessment_service = assessments
        app.state.token_registry = TokenRegistry.for_testing([
            (VIEWER_TOKEN, "viewer-1", [Role.GIS_VIEWER.value]),
            (ANALYST_TOKEN, "analyst-1", [Role.GIS_ANALYST.value]),
            (ADMIN_TOKEN, "admin-1", [Role.GIS_ADMIN.value]),
        ])
        app.state.auth_mode = "token"
        try:
            yield client
        finally:
            for key, value in previous.items():
                setattr(app.state, key, value)


def add(service: GISBoundaryService, name: str, geometry: dict, category: str = "FOREST") -> str:
    return service.create_boundary(
        name=name, category=category, state="DEMO STATE", district="Demo District",
        geometry=geometry, source="test fixture", last_updated=date(2026, 1, 1), is_demo=True,
    ).id


@pytest.fixture
def populated(boundaries: GISBoundaryService) -> GISBoundaryService:
    """A site inside a park, overlapping a forest, near a sanctuary, and far from a wetland."""
    add(boundaries, "Enclosing Park", square_boundary(-4_000, -4_000, 4_000, 4_000), "NATIONAL_PARK")
    add(boundaries, "Overlapping Forest", square_boundary(600, -3_000, 6_000, 3_000), "FOREST")
    add(boundaries, "Nearby Sanctuary", square_boundary(2_500, -3_000, 7_000, 3_000), "WILDLIFE_SANCTUARY")
    add(boundaries, "Distant Wetland", square_boundary(80_000, 80_000, 85_000, 90_000), "RAMSAR_WETLAND")
    return boundaries


# ---------------------------------------------------------------------------------
# API 1 -- collision check
# ---------------------------------------------------------------------------------


class TestCheckCollision:
    def test_valid_collision(self, gis_client, populated):
        response = gis_client.post(
            f"{BASE}/check-collision",
            json={"project_id": "PRJ-1", "latitude": SITE_LAT, "longitude": SITE_LON, "buffer_meters": 2000},
            headers=ANALYST,
        )
        assert response.status_code == 200
        body = response.json()

        assert body["project_id"] == "PRJ-1"
        assert body["project_location"] == {"latitude": SITE_LAT, "longitude": SITE_LON}
        assert body["buffer_meters"] == 2000
        assert body["overall_status"] == "DIRECT_COLLISION"
        assert body["overall_severity"] == "CRITICAL"
        assert body["clearance_required"] is True
        assert body["boundaries_checked"] == 4
        assert body["collision_count"] == len(body["collisions"]) == 3
        assert body["candidates_examined"] < body["boundaries_checked"], "the spatial index must prefilter"

    def test_response_values_come_from_the_engine_not_from_defaults(self, gis_client, populated):
        """Changing only the buffer must change the computed numbers."""
        def check(buffer_meters: int) -> dict:
            return gis_client.post(
                f"{BASE}/check-collision",
                json={"latitude": SITE_LAT, "longitude": SITE_LON, "buffer_meters": buffer_meters},
                headers=ANALYST,
            ).json()

        small, large = check(500), check(5000)
        assert small["buffer_meters"] != large["buffer_meters"]
        assert small["proximity_threshold_meters"] != large["proximity_threshold_meters"]

        def overlap(body: dict, name: str) -> float:
            return next(c["buffer_overlap_percentage"] for c in body["collisions"] if c["boundary_name"] == name)

        # The park fully encloses the small buffer but only part of the large one.
        assert overlap(small, "Enclosing Park") == pytest.approx(100.0, abs=0.1)
        assert overlap(large, "Enclosing Park") < 100.0

    def test_clear_result(self, gis_client, boundaries):
        add(boundaries, "Very Distant Forest", square_boundary(200_000, 200_000, 210_000, 210_000))
        body = gis_client.post(
            f"{BASE}/check-collision",
            json={"latitude": SITE_LAT, "longitude": SITE_LON, "buffer_meters": 1000},
            headers=ANALYST,
        ).json()

        assert body["overall_status"] == "CLEAR"
        assert body["overall_severity"] is None
        assert body["clearance_required"] is False
        assert body["collisions"] == [] and body["clearance_flags"] == []
        assert "No restricted boundary intersects" in body["summary"]

    def test_buffer_collision_and_nearby_are_distinguished(self, gis_client, boundaries):
        add(boundaries, "Reachable Forest", square_boundary(800, -3_000, 5_000, -1_000))
        add(boundaries, "Distant-But-Near Forest", square_boundary(3_000, 1_000, 8_000, 3_000))
        body = gis_client.post(
            f"{BASE}/check-collision",
            json={"latitude": SITE_LAT, "longitude": SITE_LON, "buffer_meters": 2000},
            headers=ANALYST,
        ).json()

        by_name = {c["boundary_name"]: c["collision_type"] for c in body["collisions"]}
        assert by_name["Reachable Forest"] == "BUFFER_COLLISION"
        assert by_name["Distant-But-Near Forest"] == "NEARBY"
        assert body["overall_status"] == "BUFFER_COLLISION"

    def test_multiple_collisions_are_all_reported_most_severe_first(self, gis_client, populated):
        body = gis_client.post(
            f"{BASE}/check-collision",
            json={"latitude": SITE_LAT, "longitude": SITE_LON, "buffer_meters": 1000},
            headers=ANALYST,
        ).json()

        assert {c["boundary_name"] for c in body["collisions"]} == {
            "Enclosing Park", "Overlapping Forest", "Nearby Sanctuary"
        }
        order = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
        ranks = [order.index(c["severity"]) for c in body["collisions"]]
        assert ranks == sorted(ranks, reverse=True)

    def test_clearance_flags_explain_themselves(self, gis_client, populated):
        body = gis_client.post(
            f"{BASE}/check-collision",
            json={"latitude": SITE_LAT, "longitude": SITE_LON, "buffer_meters": 1000},
            headers=ANALYST,
        ).json()

        flagged = {f["boundary_name"] for f in body["clearance_flags"]}
        assert {c["boundary_name"] for c in body["collisions"] if c["clearance_required"]} == flagged
        for flag in body["clearance_flags"]:
            assert flag["boundary_name"] in flag["reason"] and len(flag["reason"]) > 20

    def test_geojson_is_map_ready(self, gis_client, populated):
        body = gis_client.post(
            f"{BASE}/check-collision",
            json={"latitude": SITE_LAT, "longitude": SITE_LON, "buffer_meters": 1000},
            headers=ANALYST,
        ).json()
        collection = body["geojson"]

        assert collection["type"] == "FeatureCollection"
        roles = [feature["properties"]["role"] for feature in collection["features"]]
        assert roles.count("project_location") == 1
        assert roles.count("analysis_buffer") == 1
        assert roles.count("boundary") == body["collision_count"]

        point = next(f for f in collection["features"] if f["properties"]["role"] == "project_location")
        assert point["geometry"] == {"type": "Point", "coordinates": [SITE_LON, SITE_LAT]}

        buffer_feature = next(f for f in collection["features"] if f["properties"]["role"] == "analysis_buffer")
        assert buffer_feature["geometry"]["type"] == "Polygon"
        # A real metre circle projected back to degrees spans ~0.018 deg at 1 km, not 1000.
        lons = [position[0] for position in buffer_feature["geometry"]["coordinates"][0]]
        assert max(lons) - min(lons) < 0.05, "buffer must be metre-based, not degree-based"

        for feature in collection["features"]:
            if feature["properties"]["role"] == "boundary":
                assert feature["geometry"]["type"] in {"Polygon", "MultiPolygon"}
                assert "severity" in feature["properties"]

    def test_geometry_can_be_omitted_for_lighter_payloads(self, gis_client, populated):
        body = gis_client.post(
            f"{BASE}/check-collision",
            json={"latitude": SITE_LAT, "longitude": SITE_LON, "buffer_meters": 1000, "include_geometry": False},
            headers=ANALYST,
        ).json()
        assert all(collision["geometry"] is None for collision in body["collisions"])
        # The FeatureCollection still carries geometry -- that is what the map consumes.
        assert any(f["properties"]["role"] == "boundary" for f in body["geojson"]["features"])

    def test_demo_notice_travels_with_the_response(self, gis_client, populated):
        body = gis_client.post(
            f"{BASE}/check-collision",
            json={"latitude": SITE_LAT, "longitude": SITE_LON, "buffer_meters": 1000},
            headers=ANALYST,
        ).json()
        assert body["contains_demo_data"] is True
        assert "NOT OFFICIAL" in body["notice"]
        assert "NOT OFFICIAL" in body["geojson"]["notice"]

    def test_project_id_is_optional(self, gis_client, populated):
        body = gis_client.post(
            f"{BASE}/check-collision", json={"latitude": SITE_LAT, "longitude": SITE_LON}, headers=ANALYST
        ).json()
        assert body["project_id"] is None

    def test_category_filter_narrows_the_scan(self, gis_client, populated):
        body = gis_client.post(
            f"{BASE}/check-collision",
            json={"latitude": SITE_LAT, "longitude": SITE_LON, "buffer_meters": 1000, "categories": ["FOREST"]},
            headers=ANALYST,
        ).json()
        assert [c["boundary_name"] for c in body["collisions"]] == ["Overlapping Forest"]

    def test_repeated_calls_are_identical(self, gis_client, populated):
        payload = {"latitude": SITE_LAT, "longitude": SITE_LON, "buffer_meters": 1000}
        first = gis_client.post(f"{BASE}/check-collision", json=payload, headers=ANALYST).json()
        second = gis_client.post(f"{BASE}/check-collision", json=payload, headers=ANALYST).json()
        first.pop("analyzed_at"), second.pop("analyzed_at")
        assert first == second


# ---------------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------------


class TestValidation:
    @pytest.mark.parametrize(
        "payload",
        [
            {"latitude": 91, "longitude": 78},
            {"latitude": -90.5, "longitude": 78},
            {"latitude": "north", "longitude": 78},
            {"latitude": None, "longitude": 78},
            {"longitude": 78},
        ],
    )
    def test_invalid_latitude(self, gis_client, payload):
        response = gis_client.post(f"{BASE}/check-collision", json=payload, headers=ANALYST)
        assert response.status_code == 422
        assert "latitude" in response.text

    @pytest.mark.parametrize(
        "payload",
        [
            {"latitude": 21, "longitude": 181},
            {"latitude": 21, "longitude": -180.5},
            {"latitude": 21, "longitude": [78]},
            {"latitude": 21},
        ],
    )
    def test_invalid_longitude(self, gis_client, payload):
        response = gis_client.post(f"{BASE}/check-collision", json=payload, headers=ANALYST)
        assert response.status_code == 422
        assert "longitude" in response.text

    @pytest.mark.parametrize(
        "buffer_meters", [-1, -0.5, spatial_config.MAX_BUFFER_METERS + 1, "wide", None, [100]]
    )
    def test_invalid_buffer(self, gis_client, buffer_meters):
        response = gis_client.post(
            f"{BASE}/check-collision",
            json={"latitude": SITE_LAT, "longitude": SITE_LON, "buffer_meters": buffer_meters},
            headers=ANALYST,
        )
        assert response.status_code == 422
        assert "buffer_meters" in response.text

    def test_unsupported_category_lists_the_valid_ones(self, gis_client):
        response = gis_client.post(
            f"{BASE}/check-collision",
            json={"latitude": SITE_LAT, "longitude": SITE_LON, "categories": ["MOON_BASE"]},
            headers=ANALYST,
        )
        assert response.status_code == 422
        assert "WILDLIFE_SANCTUARY" in response.text

    def test_unsupported_category_on_query_endpoints(self, gis_client):
        for path, params in (
            (f"{BASE}/boundaries", {"category": "NOT_A_CATEGORY"}),
            (f"{BASE}/nearby-boundaries",
             {"latitude": SITE_LAT, "longitude": SITE_LON, "category": "NOT_A_CATEGORY"}),
        ):
            response = gis_client.get(path, params=params, headers=VIEWER)
            assert response.status_code == 422, path

    def test_unknown_fields_are_rejected_rather_than_ignored(self, gis_client):
        response = gis_client.post(
            f"{BASE}/check-collision",
            json={"latitude": SITE_LAT, "longitude": SITE_LON, "buffer_metres": 1000},
            headers=ANALYST,
        )
        assert response.status_code == 422, "a misspelled field must not silently fall back to a default"

    def test_missing_project_history_returns_404(self, gis_client):
        response = gis_client.get(f"{BASE}/assessments/NO-SUCH-PROJECT", headers=VIEWER)
        assert response.status_code == 404
        assert "NO-SUCH-PROJECT" in response.json()["detail"]

    def test_missing_boundary_returns_404(self, gis_client):
        response = gis_client.get(f"{BASE}/boundaries/no-such-id", headers=VIEWER)
        assert response.status_code == 404
        assert "no-such-id" in response.json()["detail"]

    @pytest.mark.parametrize(
        "geometry, expected_fragment",
        [
            ({"type": "Polygon", "coordinates": [[[0, 0], [2, 2], [2, 0], [0, 2], [0, 0]]]}, "Self-intersection"),
            # A non-areal type is caught by the schema's Literal before the geometry
            # validator runs; the message names the two accepted types, which is clearer
            # than the engine's own wording.
            ({"type": "Point", "coordinates": [78.0, 21.0]}, "Polygon' or 'MultiPolygon"),
            ({"type": "Polygon", "coordinates": []}, "non-empty"),
            ({"type": "Polygon", "coordinates": [[[200, 21], [201, 21], [201, 22], [200, 21]]]}, "longitude"),
        ],
    )
    def test_malformed_geometry_is_rejected_with_a_reason(self, gis_client, geometry, expected_fragment):
        response = gis_client.post(
            f"{BASE}/boundaries",
            json={
                "name": "Bad Geometry", "category": "FOREST", "state": "DEMO STATE",
                "geometry": geometry, "source": "test", "last_updated": "2026-01-01",
            },
            headers=ADMIN,
        )
        assert response.status_code == 422
        assert expected_fragment in response.text

    def test_malformed_last_updated_is_rejected(self, gis_client):
        response = gis_client.post(
            f"{BASE}/boundaries",
            json={
                "name": "Bad Date", "category": "FOREST", "state": "DEMO STATE",
                "geometry": square_boundary(0, 0, 1_000, 1_000), "source": "test",
                "last_updated": "01-01-2026",
            },
            headers=ADMIN,
        )
        assert response.status_code == 422
        assert "ISO date" in response.json()["detail"]


# ---------------------------------------------------------------------------------
# API 2 -- nearby boundaries
# ---------------------------------------------------------------------------------


class TestNearbyBoundaries:
    def test_nearby_result(self, gis_client, populated):
        response = gis_client.get(
            f"{BASE}/nearby-boundaries",
            params={"latitude": SITE_LAT, "longitude": SITE_LON, "radius_meters": 5000},
            headers=VIEWER,
        )
        assert response.status_code == 200
        body = response.json()

        assert body["radius_meters"] == 5000
        assert body["count"] == len(body["boundaries"]) == 3
        assert [b["distance_meters"] for b in body["boundaries"]] == sorted(
            b["distance_meters"] for b in body["boundaries"]
        )
        enclosing = next(b for b in body["boundaries"] if b["name"] == "Enclosing Park")
        assert enclosing["contains_point"] is True and enclosing["distance_meters"] == 0.0

    def test_radius_controls_the_result_set(self, gis_client, populated):
        def names(radius: int) -> set[str]:
            body = gis_client.get(
                f"{BASE}/nearby-boundaries",
                params={"latitude": SITE_LAT, "longitude": SITE_LON, "radius_meters": radius},
                headers=VIEWER,
            ).json()
            return {b["name"] for b in body["boundaries"]}

        assert names(100) == {"Enclosing Park"}
        assert "Nearby Sanctuary" in names(5000)
        assert "Distant Wetland" not in names(5000)

    def test_measured_distances_not_bounding_box_membership(self, gis_client, boundaries):
        """A boundary whose bbox overlaps the radius but whose geometry does not is excluded."""
        add(boundaries, "Diagonal Forest", square_boundary(4_000, 4_000, 9_000, 9_000))
        body = gis_client.get(
            f"{BASE}/nearby-boundaries",
            params={"latitude": SITE_LAT, "longitude": SITE_LON, "radius_meters": 5000},
            headers=VIEWER,
        ).json()
        # True corner distance is sqrt(4000^2 + 4000^2) ~ 5657 m, beyond the 5 km radius.
        assert body["count"] == 0

    def test_category_filter(self, gis_client, populated):
        body = gis_client.get(
            f"{BASE}/nearby-boundaries",
            params={"latitude": SITE_LAT, "longitude": SITE_LON, "radius_meters": 5000,
                    "category": ["FOREST", "WILDLIFE_SANCTUARY"]},
            headers=VIEWER,
        ).json()
        assert {b["category"] for b in body["boundaries"]} == {"FOREST", "WILDLIFE_SANCTUARY"}

    def test_geometry_is_opt_in(self, gis_client, populated):
        params = {"latitude": SITE_LAT, "longitude": SITE_LON, "radius_meters": 5000}
        default = gis_client.get(f"{BASE}/nearby-boundaries", params=params, headers=VIEWER).json()
        assert all(b["geometry"] is None for b in default["boundaries"])

        with_geometry = gis_client.get(
            f"{BASE}/nearby-boundaries", params={**params, "include_geometry": True}, headers=VIEWER
        ).json()
        assert all(b["geometry"]["type"] in {"Polygon", "MultiPolygon"} for b in with_geometry["boundaries"])

    def test_invalid_coordinates_and_radius(self, gis_client):
        for params in (
            {"latitude": 91, "longitude": SITE_LON},
            {"latitude": SITE_LAT, "longitude": 181},
            {"latitude": SITE_LAT, "longitude": SITE_LON, "radius_meters": -1},
            {"latitude": SITE_LAT},
        ):
            assert gis_client.get(f"{BASE}/nearby-boundaries", params=params, headers=VIEWER).status_code == 422


# ---------------------------------------------------------------------------------
# API 3 / 4 -- boundary list and detail
# ---------------------------------------------------------------------------------


class TestBoundaryListAndDetail:
    def test_list_returns_every_boundary(self, gis_client, populated):
        body = gis_client.get(f"{BASE}/boundaries", headers=VIEWER).json()
        assert body["count"] == body["total"] == 4
        assert {b["name"] for b in body["boundaries"]} == {
            "Enclosing Park", "Overlapping Forest", "Nearby Sanctuary", "Distant Wetland"
        }

    @pytest.mark.parametrize(
        "params, expected",
        [
            ({"category": "FOREST"}, {"Overlapping Forest"}),
            ({"category": ["FOREST", "NATIONAL_PARK"]}, {"Overlapping Forest", "Enclosing Park"}),
            ({"state": "demo state"}, None),
            ({"state": "Nowhere"}, set()),
            ({"district": "Demo District"}, None),
            ({"search": "sanctuary"}, {"Nearby Sanctuary"}),
            ({"search": "SANCTUARY"}, {"Nearby Sanctuary"}),
            ({"search": "nothing-matches"}, set()),
        ],
    )
    def test_filters(self, gis_client, populated, params, expected):
        body = gis_client.get(f"{BASE}/boundaries", params=params, headers=VIEWER).json()
        names = {b["name"] for b in body["boundaries"]}
        assert names == (expected if expected is not None else names)
        if expected is not None:
            assert body["count"] == len(expected)

    def test_paging_reports_total_before_the_window(self, gis_client, populated):
        body = gis_client.get(f"{BASE}/boundaries", params={"limit": 2, "offset": 1}, headers=VIEWER).json()
        assert body["count"] == 2 and body["total"] == 4
        assert body["limit"] == 2 and body["offset"] == 1

    def test_geojson_is_opt_in(self, gis_client, populated):
        assert gis_client.get(f"{BASE}/boundaries", headers=VIEWER).json()["geojson"] is None
        body = gis_client.get(f"{BASE}/boundaries", params={"include_geometry": True}, headers=VIEWER).json()
        assert body["geojson"]["type"] == "FeatureCollection" and len(body["geojson"]["features"]) == 4

    def test_detail_returns_full_geometry_and_provenance(self, gis_client, populated, boundaries):
        boundary_id = boundaries.find()[0].id
        body = gis_client.get(f"{BASE}/boundaries/{boundary_id}", headers=VIEWER).json()

        assert body["boundary"]["id"] == boundary_id
        assert body["geometry"]["type"] in {"Polygon", "MultiPolygon"}
        assert body["feature"]["type"] == "Feature"
        assert body["is_demo"] is True and "NOT OFFICIAL" in body["notice"]
        assert body["boundary"]["source"] == "test fixture"


# ---------------------------------------------------------------------------------
# API 5 -- assessments
# ---------------------------------------------------------------------------------


class TestAssessments:
    def test_assessment_storage_and_retrieval(self, gis_client, populated):
        created = gis_client.post(
            f"{BASE}/assessments",
            json={"project_id": "PRJ-7", "latitude": SITE_LAT, "longitude": SITE_LON,
                  "buffer_meters": 1500, "notes": "pre-tender screening"},
            headers=ANALYST,
        )
        assert created.status_code == 201
        record = created.json()

        # Everything API 5 is required to store.
        assert record["project_id"] == "PRJ-7"
        assert record["location"] == {"latitude": SITE_LAT, "longitude": SITE_LON}
        assert record["buffer_meters"] == 1500
        assert record["created_at"] and record["created_by"] == "analyst-1"
        assert record["overall_status"] == "DIRECT_COLLISION"
        assert record["overall_severity"] == "CRITICAL"
        assert record["clearance_required"] is True
        assert len(record["intersecting_boundaries"]) == 3
        assert len(record["clearance_flags"]) >= 1
        assert record["result"]["collision_count"] == 3
        assert record["notes"] == "pre-tender screening"

        fetched = gis_client.get(f"{BASE}/assessments/PRJ-7", headers=VIEWER)
        assert fetched.status_code == 200
        history = fetched.json()
        assert history["project_id"] == "PRJ-7" and history["total"] == history["count"] == 1
        assert history["assessments"][0]["id"] == record["id"]

    def test_history_is_newest_first_and_pageable(self, gis_client, populated):
        for buffer_meters in (500, 1_000, 1_500):
            gis_client.post(
                f"{BASE}/assessments",
                json={"project_id": "PRJ-8", "latitude": SITE_LAT, "longitude": SITE_LON,
                      "buffer_meters": buffer_meters},
                headers=ANALYST,
            )
        history = gis_client.get(f"{BASE}/assessments/PRJ-8", headers=VIEWER).json()
        assert history["total"] == 3
        timestamps = [a["created_at"] for a in history["assessments"]]
        assert timestamps == sorted(timestamps, reverse=True)

        page = gis_client.get(f"{BASE}/assessments/PRJ-8", params={"limit": 2}, headers=VIEWER).json()
        assert page["count"] == 2 and page["total"] == 3

    def test_assessments_are_isolated_per_project(self, gis_client, populated):
        for project_id in ("PRJ-A", "PRJ-B"):
            gis_client.post(
                f"{BASE}/assessments",
                json={"project_id": project_id, "latitude": SITE_LAT, "longitude": SITE_LON},
                headers=ANALYST,
            )
        assert gis_client.get(f"{BASE}/assessments/PRJ-A", headers=VIEWER).json()["total"] == 1

    def test_stored_result_is_a_snapshot_not_a_live_view(self, gis_client, populated, boundaries):
        """Deleting a boundary afterwards must not rewrite what the assessment concluded."""
        created = gis_client.post(
            f"{BASE}/assessments",
            json={"project_id": "PRJ-SNAP", "latitude": SITE_LAT, "longitude": SITE_LON, "buffer_meters": 1000},
            headers=ANALYST,
        ).json()
        assert len(created["intersecting_boundaries"]) == 3

        boundaries.clear()
        history = gis_client.get(f"{BASE}/assessments/PRJ-SNAP", headers=VIEWER).json()
        assert len(history["assessments"][0]["intersecting_boundaries"]) == 3

    def test_assessment_survives_a_repository_reload(self, gis_client, populated, tmp_path):
        gis_client.post(
            f"{BASE}/assessments",
            json={"project_id": "PRJ-DISK", "latitude": SITE_LAT, "longitude": SITE_LON},
            headers=ANALYST,
        )
        reloaded = JSONFileAssessmentRepository(tmp_path / "assessments.json")
        assert reloaded.count("PRJ-DISK") == 1

    def test_project_id_is_required(self, gis_client):
        response = gis_client.post(
            f"{BASE}/assessments", json={"latitude": SITE_LAT, "longitude": SITE_LON}, headers=ANALYST
        )
        assert response.status_code == 422 and "project_id" in response.text

    def test_invalid_coordinates_are_rejected_before_anything_is_stored(self, gis_client, assessments):
        response = gis_client.post(
            f"{BASE}/assessments", json={"project_id": "PRJ-BAD", "latitude": 91, "longitude": SITE_LON},
            headers=ANALYST,
        )
        assert response.status_code == 422
        assert assessments.count() == 0


# ---------------------------------------------------------------------------------
# Authentication and authorization
# ---------------------------------------------------------------------------------


ALL_ENDPOINTS = [
    ("post", f"{BASE}/check-collision", {"json": {"latitude": SITE_LAT, "longitude": SITE_LON}}),
    ("get", f"{BASE}/nearby-boundaries", {"params": {"latitude": SITE_LAT, "longitude": SITE_LON}}),
    ("get", f"{BASE}/boundaries", {}),
    ("get", f"{BASE}/boundaries/some-id", {}),
    ("post", f"{BASE}/boundaries", {"json": {}}),
    ("delete", f"{BASE}/boundaries/some-id", {}),
    ("post", f"{BASE}/assessments", {"json": {"project_id": "P", "latitude": SITE_LAT, "longitude": SITE_LON}}),
    ("get", f"{BASE}/assessments/P", {}),
]


class TestAuthentication:
    @pytest.mark.parametrize("method, path, kwargs", ALL_ENDPOINTS)
    def test_every_endpoint_requires_a_token(self, gis_client, method, path, kwargs):
        response = getattr(gis_client, method)(path, **kwargs)
        assert response.status_code == 401, path
        assert response.headers.get("WWW-Authenticate") == "Bearer"

    @pytest.mark.parametrize(
        "headers",
        [
            {"Authorization": "Bearer wrong-token"},
            {"Authorization": "Bearer "},
            {"Authorization": "Basic dXNlcjpwYXNz"},
            {"Authorization": "token-without-scheme"},
        ],
    )
    def test_bad_credentials_are_rejected(self, gis_client, headers):
        response = gis_client.get(f"{BASE}/boundaries", headers=headers)
        assert response.status_code == 401

    def test_error_never_echoes_the_presented_token(self, gis_client):
        response = gis_client.get(f"{BASE}/boundaries", headers={"Authorization": "Bearer super-secret-guess"})
        assert response.status_code == 401
        assert "super-secret-guess" not in response.text

    def test_unconfigured_registry_fails_closed_with_instructions(self, gis_client):
        app.state.token_registry = TokenRegistry()
        response = gis_client.get(f"{BASE}/boundaries", headers=VIEWER)
        assert response.status_code == 503
        assert "generate_api_tokens" in response.json()["detail"]

    def test_disabled_mode_is_an_explicit_opt_in(self, gis_client, populated):
        app.state.auth_mode = "disabled"
        assert gis_client.get(f"{BASE}/boundaries").status_code == 200


class TestAuthorization:
    def test_role_permission_mapping_is_what_the_spec_asks_for(self):
        from app.config.auth_config import ROLE_PERMISSIONS

        assert ROLE_PERMISSIONS[Role.GIS_VIEWER.value] == frozenset({Permission.VIEW_GIS.value})
        assert Permission.RUN_GIS_ANALYSIS.value in ROLE_PERMISSIONS[Role.GIS_ANALYST.value]
        assert Permission.MANAGE_GIS_DATA.value not in ROLE_PERMISSIONS[Role.GIS_ANALYST.value]
        assert ROLE_PERMISSIONS[Role.GIS_ADMIN.value] == frozenset(p.value for p in Permission)

    @pytest.mark.parametrize(
        "path, params",
        [(f"{BASE}/boundaries", {}), (f"{BASE}/nearby-boundaries", {"latitude": SITE_LAT, "longitude": SITE_LON})],
    )
    def test_viewer_can_read(self, gis_client, populated, path, params):
        assert gis_client.get(path, params=params, headers=VIEWER).status_code == 200

    def test_viewer_cannot_run_analysis(self, gis_client, populated):
        response = gis_client.post(
            f"{BASE}/check-collision", json={"latitude": SITE_LAT, "longitude": SITE_LON}, headers=VIEWER
        )
        assert response.status_code == 403
        assert "RUN_GIS_ANALYSIS" in response.json()["detail"]

    def test_viewer_cannot_record_an_assessment(self, gis_client, populated):
        response = gis_client.post(
            f"{BASE}/assessments",
            json={"project_id": "PRJ-X", "latitude": SITE_LAT, "longitude": SITE_LON},
            headers=VIEWER,
        )
        assert response.status_code == 403

    def test_analyst_can_run_analysis(self, gis_client, populated):
        assert gis_client.post(
            f"{BASE}/check-collision", json={"latitude": SITE_LAT, "longitude": SITE_LON}, headers=ANALYST
        ).status_code == 200

    @pytest.mark.parametrize("headers", [VIEWER, ANALYST])
    def test_only_an_admin_may_modify_boundary_data(self, gis_client, populated, boundaries, headers):
        boundary_id = boundaries.find()[0].id
        create = gis_client.post(
            f"{BASE}/boundaries",
            json={
                "name": "Unauthorized Area", "category": "FOREST", "state": "DEMO STATE",
                "geometry": square_boundary(0, 0, 2_000, 2_000), "source": "test",
                "last_updated": "2026-01-01",
            },
            headers=headers,
        )
        assert create.status_code == 403 and "MANAGE_GIS_DATA" in create.json()["detail"]
        assert gis_client.delete(f"{BASE}/boundaries/{boundary_id}", headers=headers).status_code == 403
        # The refused calls changed nothing.
        assert boundaries.count() == 4

    def test_admin_can_create_and_delete_boundaries(self, gis_client, boundaries):
        created = gis_client.post(
            f"{BASE}/boundaries",
            json={
                "name": "Admin Created Area", "category": "ECO_SENSITIVE_ZONE", "state": "DEMO STATE",
                "district": "Demo District", "geometry": square_boundary(0, 0, 3_000, 3_000),
                "source": "administrative entry", "last_updated": "2026-01-01",
            },
            headers=ADMIN,
        )
        assert created.status_code == 201
        body = created.json()
        assert body["boundary"]["name"] == "Admin Created Area"
        assert body["is_demo"] is True, "records default to demo data unless explicitly marked official"
        assert body["metadata"]["created_by"] == "admin-1"

        boundary_id = body["boundary"]["id"]
        assert gis_client.get(f"{BASE}/boundaries/{boundary_id}", headers=VIEWER).status_code == 200
        assert gis_client.delete(f"{BASE}/boundaries/{boundary_id}", headers=ADMIN).status_code == 204
        assert gis_client.get(f"{BASE}/boundaries/{boundary_id}", headers=VIEWER).status_code == 404
        assert boundaries.count() == 0

    def test_duplicate_creation_conflicts(self, gis_client):
        payload = {
            "name": "Duplicate Area", "category": "FOREST", "state": "DEMO STATE",
            "geometry": square_boundary(0, 0, 3_000, 3_000), "source": "administrative entry",
            "last_updated": "2026-01-01",
        }
        assert gis_client.post(f"{BASE}/boundaries", json=payload, headers=ADMIN).status_code == 201
        assert gis_client.post(f"{BASE}/boundaries", json=payload, headers=ADMIN).status_code == 409
        assert gis_client.post(
            f"{BASE}/boundaries", json={**payload, "overwrite": True}, headers=ADMIN
        ).status_code == 201

    def test_admin_holds_every_read_and_analysis_permission_too(self, gis_client, populated):
        assert gis_client.get(f"{BASE}/boundaries", headers=ADMIN).status_code == 200
        assert gis_client.post(
            f"{BASE}/check-collision", json={"latitude": SITE_LAT, "longitude": SITE_LON}, headers=ADMIN
        ).status_code == 200


class TestServiceAvailability:
    def test_missing_boundary_store_returns_503(self, gis_client):
        app.state.gis_boundary_service = None
        response = gis_client.get(f"{BASE}/boundaries", headers=VIEWER)
        assert response.status_code == 503 and "unavailable" in response.json()["detail"]

    def test_missing_spatial_engine_returns_503(self, gis_client):
        app.state.spatial_analysis_service = None
        response = gis_client.post(
            f"{BASE}/check-collision", json={"latitude": SITE_LAT, "longitude": SITE_LON}, headers=ANALYST
        )
        assert response.status_code == 503

    def test_health_reports_the_gis_subsystem(self, gis_client):
        body = gis_client.get("/health").json()
        assert body["gis_status"] == "geojson-file"
        assert body["spatial_engine_status"] == "ready"
        assert body["gis_auth_status"] in {"configured", "unconfigured", "disabled"}


class TestCollisionGeometryLayer:
    """The map's layers come from the API's FeatureCollection, so its shape is a contract."""

    def test_feature_collection_carries_a_collision_area_per_intersecting_boundary(self, gis_client, populated):
        body = gis_client.post(
            f"{BASE}/check-collision",
            json={"latitude": SITE_LAT, "longitude": SITE_LON, "buffer_meters": 1000},
            headers=ANALYST,
        ).json()

        intersecting = [c for c in body["collisions"] if c["collision_type"] != "NEARBY"]
        areas = [f for f in body["geojson"]["features"] if f["properties"]["role"] == "collision_area"]
        assert len(areas) == len(intersecting) > 0
        for feature in areas:
            assert feature["geometry"]["type"] in {"Polygon", "MultiPolygon"}
            assert feature["properties"]["boundary_id"]
            assert feature["properties"]["intersection_area_sqm"] > 0

    def test_collision_detail_exposes_the_same_overlap_geometry(self, gis_client, populated):
        body = gis_client.post(
            f"{BASE}/check-collision",
            json={"latitude": SITE_LAT, "longitude": SITE_LON, "buffer_meters": 1000},
            headers=ANALYST,
        ).json()

        for collision in body["collisions"]:
            if collision["collision_type"] == "NEARBY":
                assert collision["intersection_geometry"] is None
            else:
                assert collision["intersection_geometry"]["type"] in {"Polygon", "MultiPolygon"}

    def test_clear_result_has_no_boundary_or_collision_layers(self, gis_client, boundaries):
        add(boundaries, "Very Distant Forest", square_boundary(200_000, 200_000, 210_000, 210_000))
        body = gis_client.post(
            f"{BASE}/check-collision",
            json={"latitude": SITE_LAT, "longitude": SITE_LON, "buffer_meters": 1000},
            headers=ANALYST,
        ).json()
        roles = [f["properties"]["role"] for f in body["geojson"]["features"]]
        assert roles == ["project_location", "analysis_buffer"]

    def test_boundary_features_carry_every_popup_field(self, gis_client, populated):
        """The map popup shows name, category, state, district, source, last updated."""
        body = gis_client.post(
            f"{BASE}/check-collision",
            json={"latitude": SITE_LAT, "longitude": SITE_LON, "buffer_meters": 1000},
            headers=ANALYST,
        ).json()

        for feature in body["geojson"]["features"]:
            if feature["properties"]["role"] != "boundary":
                continue
            props = feature["properties"]
            for field in ("name", "category", "category_label", "state", "district", "source", "last_updated"):
                assert field in props, field
            assert props["is_demo"] is True
            assert "NOT OFFICIAL" in props["data_notice"]

    def test_boundary_list_geojson_also_carries_popup_fields(self, gis_client, populated):
        """The context layer is fetched separately, so it needs the same properties."""
        body = gis_client.get(
            f"{BASE}/boundaries", params={"include_geometry": True}, headers=VIEWER
        ).json()
        assert len(body["geojson"]["features"]) == 4
        for feature in body["geojson"]["features"]:
            props = feature["properties"]
            for field in ("id", "name", "category", "state", "district", "source", "last_updated"):
                assert field in props, field
            assert feature["geometry"]["type"] in {"Polygon", "MultiPolygon"}

    def test_changing_the_buffer_rebuilds_the_collision_layer(self, gis_client, boundaries):
        """Buffer changes must re-run the analysis, not reuse a previous footprint."""
        add(boundaries, "Forest At 2km", square_boundary(2_000, -10_000, 12_000, 10_000))

        def areas(buffer_meters: int) -> list[float]:
            body = gis_client.post(
                f"{BASE}/check-collision",
                json={"latitude": SITE_LAT, "longitude": SITE_LON, "buffer_meters": buffer_meters},
                headers=ANALYST,
            ).json()
            return [
                f["properties"]["intersection_area_sqm"]
                for f in body["geojson"]["features"]
                if f["properties"]["role"] == "collision_area"
            ]

        assert areas(1000) == []          # buffer does not reach the boundary
        small = areas(3000)
        large = areas(5000)
        assert len(small) == len(large) == 1
        assert large[0] > small[0]

    def test_multipolygon_boundary_renders_as_multipolygon(self, gis_client, boundaries):
        add(boundaries, "Two-Part Reserve",
            multipolygon_boundary((-3_000, -400, -600, 400), (600, -400, 3_000, 400)), "TIGER_RESERVE")
        body = gis_client.post(
            f"{BASE}/check-collision",
            json={"latitude": SITE_LAT, "longitude": SITE_LON, "buffer_meters": 1000},
            headers=ANALYST,
        ).json()

        boundary_feature = next(f for f in body["geojson"]["features"] if f["properties"]["role"] == "boundary")
        collision_feature = next(f for f in body["geojson"]["features"] if f["properties"]["role"] == "collision_area")
        assert boundary_feature["geometry"]["type"] == "MultiPolygon"
        assert collision_feature["geometry"]["type"] == "MultiPolygon"
