from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.gis import GISBufferRequest
from app.services.gis_service import GISService, build_gis_service


@pytest.fixture
def gis_service() -> GISService:
    return build_gis_service()


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


def test_gis_service_initialization(gis_service: GISService) -> None:
    assert len(gis_service.geometries_metric) > 0
    assert len(gis_service.properties_list) > 0


def test_direct_intersection_dudhwa(gis_service: GISService) -> None:
    # Dudhwa Tiger Reserve coordinates ~ (80.65 E, 28.45 N)
    request = GISBufferRequest(
        project_id="TEST-01",
        latitude=28.45,
        longitude=80.65,
        buffer_distance_km=5.0,
    )
    response = gis_service.check_buffer_collision(request)

    assert response.has_collision is True
    assert response.total_collisions >= 1
    assert response.highest_severity == "CRITICAL"
    assert response.clearance_required is True
    assert any(c.zone_category == "Tiger Reserve" for c in response.collisions)


def test_no_collision_far_away(gis_service: GISService) -> None:
    # Point in ocean / far away from any defined protected zone: (70.0 E, 15.0 N)
    request = GISBufferRequest(
        project_id="TEST-02",
        latitude=15.0,
        longitude=70.0,
        buffer_distance_km=1.0,
    )
    response = gis_service.check_buffer_collision(request)

    assert response.has_collision is False
    assert response.total_collisions == 0
    assert response.highest_severity == "NONE"
    assert response.clearance_required is False


def test_category_filtering(gis_service: GISService) -> None:
    # Dudhwa location (Tiger Reserve & National Park)
    request = GISBufferRequest(
        project_id="TEST-03",
        latitude=28.45,
        longitude=80.65,
        buffer_distance_km=5.0,
        zone_categories=["Ramsar Wetland"],
    )
    response = gis_service.check_buffer_collision(request)
    # Should filter out non-Ramsar Wetland zones
    assert all(c.zone_category == "Ramsar Wetland" for c in response.collisions)


def test_gis_check_collision_endpoint(client: TestClient) -> None:
    payload = {
        "project_id": "API-TEST-01",
        "latitude": 28.45,
        "longitude": 80.65,
        "buffer_distance_km": 5.0,
    }
    res = client.post("/api/v1/gis/check-collision", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["has_collision"] is True
    assert "collisions" in data
    assert "geojson_layers" in data


def test_gis_protected_zones_endpoint(client: TestClient) -> None:
    res = client.get("/api/v1/gis/protected-zones")
    assert res.status_code == 200
    data = res.json()
    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) > 0


def test_invalid_coordinates_rejected(client: TestClient) -> None:
    payload = {
        "latitude": 100.0,  # Invalid latitude (> 37.5)
        "longitude": 80.0,
        "buffer_distance_km": 5.0,
    }
    res = client.post("/api/v1/gis/check-collision", json=payload)
    assert res.status_code == 422
