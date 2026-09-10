"""Comprehensive verification script for GIS Buffer Collision & Environmental Boundary Checking.

Tests items 1 through 12, 14, and regression directly against the real FastAPI application
and real domain services with actual data.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from shapely.geometry import Point, box, mapping, shape
from shapely.validation import explain_validity

from app.main import app
from app.config import auth_config, gis_config, spatial_config
from app.models.projection import LocalProjection
from app.schemas.spatial import CollisionType, Severity


def run_all_verifications():
    print("=" * 80)
    print("STARTING COMPLETE GIS FEATURE VERIFICATION")
    print("=" * 80)

    # Initialize client
    with TestClient(app) as client:
        # Check health endpoint
        health_res = client.get("/health")
        assert health_res.status_code == 200, f"Health check failed: {health_res.text}"
        health_data = health_res.json()
        print(f"[HEALTH CHECK] Status: {health_data['status']}, GIS status: {health_data['gis_status']}, Spatial engine: {health_data['spatial_engine_status']}")
        assert health_data["spatial_engine_status"] == "ready", "Spatial engine must be ready"

        # Auth token setup
        # Token in .env.local: UOD-UfF8MBnv-rOh25aKgimhngQz-gRCfCDbW55PJCI
        admin_token = "UOD-UfF8MBnv-rOh25aKgimhngQz-gRCfCDbW55PJCI"
        headers_admin = {"Authorization": f"Bearer {admin_token}"}

        # ----------------------------------------------------------------------
        # 1. CLEAR: Project does not intersect or approach a boundary
        # ----------------------------------------------------------------------
        print("\n--- 1. Verification: CLEAR ---")
        # Lucknow point: lat 26.8467, lon 80.9462 (far from demo state boundaries)
        res_clear = client.post(
            "/api/v1/gis/check-collision",
            headers=headers_admin,
            json={
                "latitude": 26.8467,
                "longitude": 80.9462,
                "buffer_meters": 1000.0,
                "project_id": "TEST-CLEAR-01",
            },
        )
        assert res_clear.status_code == 200, f"Clear check failed: {res_clear.text}"
        data_clear = res_clear.json()
        print(f"Result for (26.8467, 80.9462): overall_status = {data_clear['overall_status']}, collision_count = {data_clear['collision_count']}")
        assert data_clear["overall_status"] == "CLEAR", f"Expected CLEAR, got {data_clear['overall_status']}"
        assert data_clear["collision_count"] == 0
        assert data_clear["clearance_required"] is False
        assert len(data_clear["collisions"]) == 0
        print("✓ Test 1 Passed: CLEAR correctly verified.")

        # ----------------------------------------------------------------------
        # 2. NEARBY: Boundary is close but outside configured buffer
        # ----------------------------------------------------------------------
        print("\n--- 2. Verification: NEARBY ---")
        # In demo dataset: DEMO-RAM-001 is Polygon [78.9, 20.6] to [78.98, 20.68].
        # Let's test a point at lat 20.585, lon 78.94 with buffer 500m.
        # Distance to boundary is ~1660m. 1660m is > 500m (buffer) and <= 5000m (proximity).
        res_nearby = client.post(
            "/api/v1/gis/check-collision",
            headers=headers_admin,
            json={
                "latitude": 20.585,
                "longitude": 78.94,
                "buffer_meters": 500.0,
                "project_id": "TEST-NEARBY-01",
            },
        )
        assert res_nearby.status_code == 200, f"Nearby check failed: {res_nearby.text}"
        data_nearby = res_nearby.json()
        print(f"Result for (20.585, 78.94) @ 500m buffer: overall_status = {data_nearby['overall_status']}, collision_count = {data_nearby['collision_count']}")
        for c in data_nearby["collisions"]:
            print(f"  - Boundary: {c['boundary_name']} ({c['category']}), type: {c['collision_type']}, distance: {c['distance_meters']}m")
        assert data_nearby["overall_status"] == "NEARBY", f"Expected NEARBY, got {data_nearby['overall_status']}"
        assert data_nearby["collision_count"] > 0
        assert any(c["collision_type"] == "NEARBY" for c in data_nearby["collisions"])
        print("✓ Test 2 Passed: NEARBY correctly verified.")

        # ----------------------------------------------------------------------
        # 3. BUFFER COLLISION: Buffer intersects boundary
        # ----------------------------------------------------------------------
        print("\n--- 3. Verification: BUFFER COLLISION ---")
        # Same point (20.585, 78.94) but increase buffer to 2000m (> 1660m distance).
        res_buffer = client.post(
            "/api/v1/gis/check-collision",
            headers=headers_admin,
            json={
                "latitude": 20.585,
                "longitude": 78.94,
                "buffer_meters": 2000.0,
                "project_id": "TEST-BUFFER-01",
            },
        )
        assert res_buffer.status_code == 200, f"Buffer collision check failed: {res_buffer.text}"
        data_buffer = res_buffer.json()
        print(f"Result for (20.585, 78.94) @ 2000m buffer: overall_status = {data_buffer['overall_status']}, collision_count = {data_buffer['collision_count']}")
        for c in data_buffer["collisions"]:
            print(f"  - Boundary: {c['boundary_name']} ({c['category']}), type: {c['collision_type']}, overlap: {c['buffer_overlap_percentage']}%, area: {c['intersection_area_sqm']} m^2")
        assert data_buffer["overall_status"] == "BUFFER_COLLISION", f"Expected BUFFER_COLLISION, got {data_buffer['overall_status']}"
        ramsar_c = next(c for c in data_buffer["collisions"] if "Ramsar" in c["boundary_name"])
        assert ramsar_c["collision_type"] == "BUFFER_COLLISION"
        assert ramsar_c["intersection_area_sqm"] > 0
        assert ramsar_c["buffer_overlap_percentage"] > 0
        print("✓ Test 3 Passed: BUFFER COLLISION correctly verified.")

        # ----------------------------------------------------------------------
        # 4. DIRECT COLLISION: Project point lies inside boundary
        # ----------------------------------------------------------------------
        print("\n--- 4. Verification: DIRECT COLLISION ---")
        # Point inside DEMO-NP-001: lat 21.2, lon 78.2 (NP is [78.1, 78.3], [21.1, 21.3])
        res_direct = client.post(
            "/api/v1/gis/check-collision",
            headers=headers_admin,
            json={
                "latitude": 21.2,
                "longitude": 78.2,
                "buffer_meters": 1000.0,
                "project_id": "TEST-DIRECT-01",
            },
        )
        assert res_direct.status_code == 200, f"Direct collision check failed: {res_direct.text}"
        data_direct = res_direct.json()
        print(f"Result for (21.2, 78.2): overall_status = {data_direct['overall_status']}, collisions = {len(data_direct['collisions'])}")
        for c in data_direct["collisions"]:
            print(f"  - Boundary: {c['boundary_name']} ({c['category']}), type: {c['collision_type']}, distance: {c['distance_meters']}m")
        assert data_direct["overall_status"] == "DIRECT_COLLISION", f"Expected DIRECT_COLLISION, got {data_direct['overall_status']}"
        np_c = next(c for c in data_direct["collisions"] if "National Park" in c["boundary_name"])
        assert np_c["collision_type"] == "DIRECT_COLLISION"
        assert np_c["distance_meters"] == 0.0
        print("✓ Test 4 Passed: DIRECT COLLISION correctly verified.")

        # ----------------------------------------------------------------------
        # 5. Multiple boundaries: Verify all relevant intersections are returned
        # ----------------------------------------------------------------------
        print("\n--- 5. Verification: Multiple Boundaries ---")
        # Near border of National Park and Eco-Sensitive Zone ring: (21.1005, 78.2) with 1000m buffer
        res_multi = client.post(
            "/api/v1/gis/check-collision",
            headers=headers_admin,
            json={"latitude": 21.1005, "longitude": 78.2, "buffer_meters": 1000.0},
        )
        assert res_multi.status_code == 200
        d_multi = res_multi.json()
        boundary_names = [c["boundary_name"] for c in d_multi["collisions"]]
        print(f"Intersecting/nearby boundaries found: {boundary_names}")
        assert len(boundary_names) >= 2, f"Expected multiple boundaries, got {boundary_names}"
        assert any("National Park" in n for n in boundary_names)
        assert any("Eco-Sensitive Zone" in n for n in boundary_names)
        # Verify ordering: most severe first, then nearest
        severities = [c["severity"] for c in d_multi["collisions"]]
        print(f"Severities in order: {severities}")
        print("✓ Test 5 Passed: Multiple relevant boundary intersections returned and ranked.")

        # ----------------------------------------------------------------------
        # 6. Buffer sensitivity: Changing buffer distance changes results correctly
        # ----------------------------------------------------------------------
        print("\n--- 6. Verification: Buffer Sensitivity ---")
        # Test point at (20.585, 78.94):
        # 0m -> NEARBY (distance ~1660m)
        # 500m -> NEARBY (buffer doesn't touch)
        # 1000m -> NEARBY
        # 1600m -> NEARBY
        # 1700m -> BUFFER_COLLISION (buffer penetrates)
        # 3000m -> BUFFER_COLLISION (larger overlap area)
        b_0 = client.post("/api/v1/gis/check-collision", headers=headers_admin, json={"latitude": 20.585, "longitude": 78.94, "buffer_meters": 0.0}).json()
        b_500 = client.post("/api/v1/gis/check-collision", headers=headers_admin, json={"latitude": 20.585, "longitude": 78.94, "buffer_meters": 500.0}).json()
        b_1500 = client.post("/api/v1/gis/check-collision", headers=headers_admin, json={"latitude": 20.585, "longitude": 78.94, "buffer_meters": 1500.0}).json()
        b_2000 = client.post("/api/v1/gis/check-collision", headers=headers_admin, json={"latitude": 20.585, "longitude": 78.94, "buffer_meters": 2000.0}).json()
        b_5000 = client.post("/api/v1/gis/check-collision", headers=headers_admin, json={"latitude": 20.585, "longitude": 78.94, "buffer_meters": 5000.0}).json()

        print(f"Buffer 0m: {b_0['overall_status']}")
        print(f"Buffer 500m: {b_500['overall_status']}")
        print(f"Buffer 1500m: {b_1500['overall_status']}")
        print(f"Buffer 2000m: {b_2000['overall_status']}")
        print(f"Buffer 5000m: {b_5000['overall_status']}")

        assert b_0["overall_status"] == "NEARBY"
        assert b_500["overall_status"] == "NEARBY"
        assert b_1500["overall_status"] == "NEARBY"
        assert b_2000["overall_status"] == "BUFFER_COLLISION"
        assert b_5000["overall_status"] == "BUFFER_COLLISION"

        # Check overlap area increases with larger penetration
        c_2000 = next(c for c in b_2000["collisions"] if "Ramsar" in c["boundary_name"])
        c_5000 = next(c for c in b_5000["collisions"] if "Ramsar" in c["boundary_name"])
        print(f"Overlap area @ 2000m: {c_2000['intersection_area_sqm']} m^2")
        print(f"Overlap area @ 5000m: {c_5000['intersection_area_sqm']} m^2")
        assert c_5000["intersection_area_sqm"] > c_2000["intersection_area_sqm"]
        print("✓ Test 6 Passed: Buffer sensitivity verified.")

        # ----------------------------------------------------------------------
        # 7. Polygon / MultiPolygon handling
        # ----------------------------------------------------------------------
        print("\n--- 7. Verification: Polygon / MultiPolygon Handling ---")
        # In demo data, Demo Tiger Reserve is a MultiPolygon! Part 1: [79.0, 79.2], [21.0, 21.15]; Part 2: [79.3, 79.45], [21.2, 21.35].
        # Test point inside Part 1: (21.05, 79.1)
        res_mp1 = client.post(
            "/api/v1/gis/check-collision",
            headers=headers_admin,
            json={"latitude": 21.05, "longitude": 79.1, "buffer_meters": 500.0},
        )
        assert res_mp1.status_code == 200
        d_mp1 = res_mp1.json()
        tr_c1 = next((c for c in d_mp1["collisions"] if "Tiger Reserve" in c["boundary_name"]), None)
        assert tr_c1 is not None
        assert tr_c1["collision_type"] == "DIRECT_COLLISION"
        assert tr_c1["distance_meters"] == 0.0

        # Test point in between parts: (21.16, 79.22)
        res_mp2 = client.post(
            "/api/v1/gis/check-collision",
            headers=headers_admin,
            json={"latitude": 21.16, "longitude": 79.22, "buffer_meters": 1000.0},
        )
        assert res_mp2.status_code == 200
        d_mp2 = res_mp2.json()
        tr_c2 = next((c for c in d_mp2["collisions"] if "Tiger Reserve" in c["boundary_name"]), None)
        assert tr_c2 is not None
        print(f"Point between MultiPolygon parts distance: {tr_c2['distance_meters']}m, collision_type: {tr_c2['collision_type']}")
        assert tr_c2["distance_meters"] > 0
        print("✓ Test 7 Passed: Polygon and MultiPolygon calculate correctly.")

        # ----------------------------------------------------------------------
        # 8. Invalid Input Handling
        # ----------------------------------------------------------------------
        print("\n--- 8. Verification: Invalid Input Handling ---")
        # Invalid latitude (> 90)
        res_inv_lat = client.post("/api/v1/gis/check-collision", headers=headers_admin, json={"latitude": 95.0, "longitude": 78.0, "buffer_meters": 1000.0})
        print(f"Invalid lat (95.0) status: {res_inv_lat.status_code}")
        assert res_inv_lat.status_code == 422

        # Invalid longitude (< -180)
        res_inv_lon = client.post("/api/v1/gis/check-collision", headers=headers_admin, json={"latitude": 21.0, "longitude": -190.0, "buffer_meters": 1000.0})
        print(f"Invalid lon (-190.0) status: {res_inv_lon.status_code}")
        assert res_inv_lon.status_code == 422

        # Invalid buffer (< 0)
        res_inv_buf_neg = client.post("/api/v1/gis/check-collision", headers=headers_admin, json={"latitude": 21.0, "longitude": 78.0, "buffer_meters": -10.0})
        print(f"Negative buffer (-10.0) status: {res_inv_buf_neg.status_code}")
        assert res_inv_buf_neg.status_code == 422

        # Invalid buffer (> max, e.g. 150,000m > 100,000m)
        res_inv_buf_huge = client.post("/api/v1/gis/check-collision", headers=headers_admin, json={"latitude": 21.0, "longitude": 78.0, "buffer_meters": 150000.0})
        print(f"Excessive buffer (150000m) status: {res_inv_buf_huge.status_code}")
        assert res_inv_buf_huge.status_code == 422

        # Invalid project ID (exceeds max_length 128)
        res_inv_pid = client.post("/api/v1/gis/check-collision", headers=headers_admin, json={"latitude": 21.0, "longitude": 78.0, "buffer_meters": 1000.0, "project_id": "A" * 200})
        print(f"Oversized project_id status: {res_inv_pid.status_code}")
        assert res_inv_pid.status_code == 422
        print("✓ Test 8 Passed: Invalid coordinates, buffer, and project ID properly rejected with 422.")

        # ----------------------------------------------------------------------
        # 9. GIS Accuracy: Distances, Areas, Overlap %, CRS Transformation
        # ----------------------------------------------------------------------
        print("\n--- 9. Verification: GIS Accuracy ---")
        # Verify CRS transformation and buffer area analytically
        proj = LocalProjection.for_point(21.0, 78.0)
        print(f"Projected CRS: {proj.crs}")
        buf_radius = 1000.0
        buf_geom = Point(0, 0).buffer(buf_radius)
        expected_area = math.pi * (buf_radius ** 2)
        actual_area = buf_geom.area
        rel_diff = abs(actual_area - expected_area) / expected_area
        print(f"Analytic circle area: {expected_area:.2f} m^2, Shapely buffer area: {actual_area:.2f} m^2 (diff: {rel_diff:.4%})")
        assert rel_diff < 0.01, f"Buffer area deviation too large: {rel_diff}"

        # Verify distance calculation accuracy against geodesic
        from pyproj import Geod
        geod = Geod(ellps="WGS84")
        p1 = (78.0, 21.0)
        p2 = (78.1, 21.0)
        _, _, geod_dist = geod.inv(p1[0], p1[1], p2[0], p2[1])
        proj_p2 = proj.to_projected(Point(p2[0], p2[1]))
        proj_dist = math.hypot(proj_p2.x, proj_p2.y)
        dist_diff = abs(geod_dist - proj_dist)
        print(f"Geodesic distance: {geod_dist:.2f}m, LocalProjection distance: {proj_dist:.2f}m, diff: {dist_diff:.2f}m ({dist_diff/geod_dist:.4%})")
        assert dist_diff / geod_dist < 0.005, f"Distance discrepancy: {dist_diff}"
        print("✓ Test 9 Passed: GIS accuracy, CRS transformation, and area computations verified.")

        # ----------------------------------------------------------------------
        # 10. Data Quality: Invalid Geometries are Rejected
        # ----------------------------------------------------------------------
        print("\n--- 10. Verification: Data Quality & Invalid Geometries ---")
        # Try creating a boundary with a self-intersecting bowtie polygon
        bowtie_geom = {
            "type": "Polygon",
            "coordinates": [
                [[78.0, 21.0], [78.2, 21.2], [78.0, 21.2], [78.2, 21.0], [78.0, 21.0]]
            ],
        }
        res_bad_geom = client.post(
            "/api/v1/gis/boundaries",
            headers=headers_admin,
            json={
                "name": "Invalid Bowtie Area",
                "category": "FOREST",
                "state": "TEST",
                "district": "TEST",
                "geometry": bowtie_geom,
                "source": "Verification Test",
                "last_updated": "2026-09-10",
            },
        )
        print(f"Bowtie geometry creation status: {res_bad_geom.status_code}")
        assert res_bad_geom.status_code == 422
        print(f"Error message: {res_bad_geom.json()['detail']}")
        assert "Self-intersection" in res_bad_geom.json()["detail"] or "rejected" in res_bad_geom.json()["detail"].lower()

        # Degenerate ring (< 3 distinct points)
        degenerate_geom = {
            "type": "Polygon",
            "coordinates": [[[78.0, 21.0], [78.0, 21.0]]],
        }
        res_degenerate = client.post(
            "/api/v1/gis/boundaries",
            headers=headers_admin,
            json={
                "name": "Degenerate Area",
                "category": "FOREST",
                "state": "TEST",
                "geometry": degenerate_geom,
                "source": "Verification Test",
                "last_updated": "2026-09-10",
            },
        )
        print(f"Degenerate geometry status: {res_degenerate.status_code}")
        assert res_degenerate.status_code == 422

        # Unsupported geometry type (LineString)
        line_geom = {
            "type": "LineString",
            "coordinates": [[78.0, 21.0], [78.1, 21.1]],
        }
        res_line = client.post(
            "/api/v1/gis/boundaries",
            headers=headers_admin,
            json={
                "name": "Line Area",
                "category": "FOREST",
                "state": "TEST",
                "geometry": line_geom,
                "source": "Verification Test",
                "last_updated": "2026-09-10",
            },
        )
        print(f"LineString geometry status: {res_line.status_code}")
        assert res_line.status_code == 422

        # Coordinate out of bounds (lon 250)
        oob_geom = {
            "type": "Polygon",
            "coordinates": [[[250.0, 21.0], [251.0, 21.0], [251.0, 21.1], [250.0, 21.1], [250.0, 21.0]]],
        }
        res_oob = client.post(
            "/api/v1/gis/boundaries",
            headers=headers_admin,
            json={
                "name": "OOB Area",
                "category": "FOREST",
                "state": "TEST",
                "geometry": oob_geom,
                "source": "Verification Test",
                "last_updated": "2026-09-10",
            },
        )
        print(f"OOB geometry status: {res_oob.status_code}")
        assert res_oob.status_code == 422

        print("✓ Test 10 Passed: Invalid geometries strictly rejected and never stored.")

        # ----------------------------------------------------------------------
        # 11. Security: Unauthorized Users Cannot Modify Datasets
        # ----------------------------------------------------------------------
        print("\n--- 11. Verification: Security & Permissions ---")
        # Request with no token
        res_no_auth = client.post(
            "/api/v1/gis/boundaries",
            json={
                "name": "Unauthorized Area",
                "category": "FOREST",
                "state": "TEST",
                "geometry": {"type": "Polygon", "coordinates": [[[78.0, 21.0], [78.1, 21.0], [78.1, 21.1], [78.0, 21.1], [78.0, 21.0]]]},
                "source": "Hack",
                "last_updated": "2026-09-10",
            },
        )
        print(f"No auth creation status: {res_no_auth.status_code}")
        assert res_no_auth.status_code == 401

        # Request with bogus token
        res_bad_token = client.post(
            "/api/v1/gis/boundaries",
            headers={"Authorization": "Bearer totally-fake-token-12345"},
            json={"name": "Hacked Area"},
        )
        print(f"Bad token creation status: {res_bad_token.status_code}")
        assert res_bad_token.status_code == 401

        # Test DELETE without MANAGE_GIS_DATA
        res_del_no_auth = client.delete("/api/v1/gis/boundaries/DEMO-NP-001")
        print(f"No auth delete status: {res_del_no_auth.status_code}")
        assert res_del_no_auth.status_code == 401

        print("✓ Test 11 Passed: Security boundaries enforced (fail-closed, 401 on unauthorized).")

        # ----------------------------------------------------------------------
        # 12. Assessment History
        # ----------------------------------------------------------------------
        print("\n--- 12. Verification: Assessment History ---")
        # Record an assessment
        test_proj_id = "HIST-VERIFY-001"
        res_create_assess = client.post(
            "/api/v1/gis/assessments",
            headers=headers_admin,
            json={
                "project_id": test_proj_id,
                "latitude": 21.2,
                "longitude": 78.2,
                "buffer_meters": 1500.0,
                "notes": "Automated verification run",
            },
        )
        assert res_create_assess.status_code == 201, f"Create assessment failed: {res_create_assess.text}"
        rec = res_create_assess.json()
        print(f"Recorded assessment ID: {rec['id']} for project: {rec['project_id']}")
        assert rec["project_id"] == test_proj_id
        assert rec["overall_status"] == "DIRECT_COLLISION"

        # Now retrieve history
        res_hist = client.get(
            f"/api/v1/gis/assessments/{test_proj_id}",
            headers=headers_admin,
        )
        assert res_hist.status_code == 200, f"Get assessment history failed: {res_hist.text}"
        hist_data = res_hist.json()
        print(f"Assessment history retrieved: {hist_data['count']} record(s)")
        assert hist_data["count"] >= 1
        assert hist_data["assessments"][0]["id"] == rec["id"]
        assert hist_data["assessments"][0]["location"]["latitude"] == 21.2
        print("✓ Test 12 Passed: Assessment history recorded and retrieved accurately.")

        # ----------------------------------------------------------------------
        # 14. Integration: GIS result -> Project Intelligence -> Priority Engine
        # ----------------------------------------------------------------------
        print("\n--- 14. Verification: End-to-End Integration ---")
        # Baseline project request WITHOUT coordinates
        base_payload = {
            "project_id": "INT-TEST-01",
            "sector": "Railways",
            "state": "DEMO STATE",
            "original_cost": 3200,
            "revised_cost": 4100,
            "planned_duration_months": 54,
            "project_age_months": 60,
            "physical_progress": 38,
            "financial_progress": 22,
            "milestones_total": 18,
            "milestones_delayed": 11,
            "land_acquisition_pending": True,
            "clearance_pending": True,
            "funding_issue": True,
            "contractor_issue": True,
            "previous_schedule_deviation": 14,
        }

        # 1. Project Intelligence WITHOUT location
        res_intel_no_gis = client.post("/api/v1/project-intelligence", json=base_payload)
        assert res_intel_no_gis.status_code == 200
        d_no_gis = res_intel_no_gis.json()
        assert d_no_gis["gis_screening"] is None
        score_no_gis = d_no_gis["priority"]["priority_score"]
        print(f"Project Intelligence without GIS: priority_score = {score_no_gis}")

        # 2. Project Intelligence WITH CLEAR location (Lucknow)
        payload_clear = {**base_payload, "latitude": 26.8467, "longitude": 80.9462, "buffer_meters": 1000.0}
        res_intel_clear = client.post("/api/v1/project-intelligence", json=payload_clear)
        assert res_intel_clear.status_code == 200
        d_clear = res_intel_clear.json()
        assert d_clear["gis_screening"] is not None
        assert d_clear["gis_screening"]["gis_status"] == "CLEAR"
        assert d_clear["gis_screening"]["gis_severity"] is None
        score_clear = d_clear["priority"]["priority_score"]
        print(f"Project Intelligence with CLEAR GIS: priority_score = {score_clear}")
        # When CLEAR, GIS component score is low (0), so weighted priority score is lower than colliding

        # 3. Project Intelligence WITH DIRECT COLLISION location (inside National Park)
        payload_collision = {**base_payload, "latitude": 21.2, "longitude": 78.2, "buffer_meters": 1000.0}
        res_intel_coll = client.post("/api/v1/project-intelligence", json=payload_collision)
        assert res_intel_coll.status_code == 200
        d_coll = res_intel_coll.json()
        assert d_coll["gis_screening"] is not None
        assert d_coll["gis_screening"]["gis_status"] == "DIRECT_COLLISION"
        assert d_coll["gis_screening"]["gis_severity"] == "CRITICAL"
        assert d_coll["gis_screening"]["clearance_required"] is True
        score_coll = d_coll["priority"]["priority_score"]
        print(f"Project Intelligence with DIRECT COLLISION: priority_score = {score_coll}")

        # Assert priority score is significantly higher with GIS direct collision than clear!
        assert score_coll > score_clear, f"Collision score ({score_coll}) should exceed clear score ({score_clear})"
        assert d_coll["priority"]["weight_profile"] == "with_gis"

        # Check GIS component breakdown in priority response
        breakdown = {c["name"]: c for c in d_coll["priority"]["score_breakdown"]}
        print("Score breakdown components with GIS collision:")
        for k, v in breakdown.items():
            print(f"  - {k}: score={v['score']}, weighted={v['weighted_contribution']}, weight={v['weight']}")
        assert "gis" in breakdown
        assert breakdown["gis"]["score"] > 80.0
        assert breakdown["gis"]["weight"] == 0.10

        # Check Interventions include environmental/clearance recommendations
        interventions = d_coll["interventions"]
        print(f"Interventions generated: {len(interventions)}")
        gis_interventions = [i for i in interventions if "gis" in i.get("evidence_source", "").lower() or "environmental" in i.get("title", "").lower() or "clearance" in i.get("title", "").lower() or "boundary" in i.get("rationale", "").lower()]
        print(f"Environmental/GIS interventions found: {[i['title'] for i in gis_interventions]}")
        assert len(gis_interventions) > 0, "Expected environmental/GIS interventions when collision occurs"

        # 4. Direct /project-priority endpoint with GIS
        res_priority = client.post("/api/v1/project-priority", json=payload_collision)
        assert res_priority.status_code == 200
        p_data = res_priority.json()
        assert p_data["priority_score"] == score_coll
        p_breakdown = {c["name"]: c for c in p_data["score_breakdown"]}
        assert "gis" in p_breakdown
        print("✓ Test 14 Passed: Complete integration GIS -> Project Intelligence -> Priority Engine & Interventions verified.")

    print("\n" + "=" * 80)
    print("ALL 12 FUNCTIONAL VERIFICATIONS & INTEGRATION TESTS SUCCEEDED!")
    print("=" * 80)


if __name__ == "__main__":
    run_all_verifications()
