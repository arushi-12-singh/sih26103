"""Tests for the GIS integration into the intelligence and priority pipeline (Feature 7).

Three things matter most here and are tested hardest:

  1. **Backward compatibility.** A project assessed without coordinates must score
     exactly what it scored before the GIS module existed -- byte-identical, not close.
  2. **No geometry crosses into the pipeline.** The priority engine and the intelligence
     response consume structured features only.
  3. **No permitting determinations.** This system screens; it does not adjudicate. The
     approved wording is asserted, and prohibited determinative wording fails the build.
"""

from __future__ import annotations

import itertools
import re
from datetime import date
from pathlib import Path

import pytest

from app.config import intervention_config
from app.config import priority_config as config
from app.repositories.gis_boundary_repository import GeoJSONFileBoundaryRepository
from app.schemas.gis_signal import GISIntelligenceSignal
from app.schemas.priority import PriorityRequest, PriorityResponse
from app.schemas.spatial import CollisionType, Severity
from app.services.gis_boundary_service import GISBoundaryService
from app.services.gis_intelligence_service import build_gis_signal
from app.services.intervention_service import build_intervention_service
from app.services.priority_service import calculate_gis_component, calculate_priority_score
from app.services.spatial_analysis_service import SpatialAnalysisService

from tests.test_spatial_analysis import SITE_LAT, SITE_LON, square_boundary

PROJECT = {
    "project_id": "EFC-04",
    "sector": "Railways",
    "state": "Bihar",
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

#: Wording that would assert a permitting outcome this system has no authority to make.
PROHIBITED_PHRASES = [
    "legally rejected",
    "is rejected",
    "not permitted",
    "prohibited",
    "illegal",
    "must be cancelled",
    "cannot proceed",
    "approval denied",
    "violates",
]

#: The modules that produce user-facing GIS wording.
RESPONSE_SOURCES = [
    "app/config/priority_config.py",
    "app/config/intervention_config.py",
    "app/services/gis_intelligence_service.py",
    "app/services/intervention_service.py",
    "app/services/priority_service.py",
    "app/services/assessment_service.py",
]


@pytest.fixture
def signal_for():
    """Builds a GIS signal from a purpose-made boundary store."""

    counter = itertools.count()

    def factory(tmp_path: Path, boundaries: list[tuple[str, str, dict]], buffer_meters: float):
        # A fresh store per call: boundary ids are deterministic by design, so reusing one
        # store would (correctly) reject the second identical boundary as a duplicate.
        service = GISBoundaryService(GeoJSONFileBoundaryRepository(tmp_path / f"b{next(counter)}.geojson"))
        for name, category, geometry in boundaries:
            service.create_boundary(
                name=name, category=category, state="DEMO STATE", district="Demo District",
                geometry=geometry, source="test fixture", last_updated=date(2026, 1, 1), is_demo=True,
            )
        result = SpatialAnalysisService(service).analyze(SITE_LAT, SITE_LON, buffer_meters)
        return build_gis_signal(result)

    return factory


# ---------------------------------------------------------------------------------
# Backward compatibility -- the original scoring system must be untouched
# ---------------------------------------------------------------------------------


class TestNoSilentRescoring:
    def test_score_without_gis_matches_the_original_formula(self):
        """The exact legacy expression, spelled out so a weight change cannot pass silently."""
        risk, delay, financial, historical, urgency = 98.3, 36.0, 70.4, 50.7, 74.6
        expected = round(
            risk * 0.35 + delay * 0.20 + financial * 0.20 + historical * 0.15 + urgency * 0.10, 1
        )
        assert calculate_priority_score(risk, delay, financial, historical, urgency) == expected

    def test_base_weight_profile_is_unchanged(self):
        assert config.COMPONENT_WEIGHTS == {
            "risk": 0.35, "delay": 0.20, "financial": 0.20, "historical": 0.15, "urgency": 0.10,
        }

    def test_both_profiles_sum_to_one(self):
        for profile in (config.COMPONENT_WEIGHTS, config.COMPONENT_WEIGHTS_WITH_GIS):
            assert sum(profile.values()) == pytest.approx(1.0)

    def test_gis_profile_adds_exactly_one_component(self):
        assert set(config.COMPONENT_WEIGHTS_WITH_GIS) - set(config.COMPONENT_WEIGHTS) == {"gis"}
        assert not set(config.COMPONENT_WEIGHTS) - set(config.COMPONENT_WEIGHTS_WITH_GIS)

    def test_profile_selection_is_driven_only_by_gis_presence(self):
        assert config.weights_for(False) is config.COMPONENT_WEIGHTS
        assert config.weights_for(True) is config.COMPONENT_WEIGHTS_WITH_GIS

    def test_supplying_gis_changes_the_score_and_says_so(self, client):
        without = client.post("/api/v1/project-priority", json=PROJECT).json()
        with_gis = client.post(
            "/api/v1/project-priority",
            json={**PROJECT, "latitude": 21.20, "longitude": 78.20, "buffer_meters": 2000},
        ).json()

        assert without["weight_profile"] == "standard"
        assert with_gis["weight_profile"] == "with_gis"
        assert {c["name"] for c in without["score_breakdown"]} == {
            "risk", "delay", "financial", "historical", "urgency"
        }
        assert "gis" in {c["name"] for c in with_gis["score_breakdown"]}

    def test_component_weights_in_the_breakdown_match_the_profile_used(self, client):
        for payload, profile in (
            (PROJECT, config.COMPONENT_WEIGHTS),
            ({**PROJECT, "latitude": 21.20, "longitude": 78.20}, config.COMPONENT_WEIGHTS_WITH_GIS),
        ):
            body = client.post("/api/v1/project-priority", json=payload).json()
            for component in body["score_breakdown"]:
                assert component["weight"] == profile[component["name"]], component["name"]
            assert sum(c["weight"] for c in body["score_breakdown"]) == pytest.approx(1.0)


# ---------------------------------------------------------------------------------
# The GIS signal -- structured features only
# ---------------------------------------------------------------------------------


class TestGISSignal:
    def test_signal_exposes_no_geometry(self):
        """Guards the rule: no raw geometry is fed into the scoring pipeline."""
        fields = set(GISIntelligenceSignal.model_fields)
        assert not any(
            re.search(r"geometry|coordinates|polygon|geojson|bbox|wkt", name, re.I) for name in fields
        ), fields

    def test_signal_carries_every_required_field(self):
        required = {
            "gis_status", "gis_severity", "collision_count", "highest_risk_category",
            "nearest_boundary", "clearance_flags", "max_buffer_overlap_percentage",
        }
        assert required <= set(GISIntelligenceSignal.model_fields)

    def test_clear_screening(self, tmp_path, signal_for):
        signal = signal_for(tmp_path, [("Distant", "FOREST", square_boundary(200_000, 200_000, 210_000, 210_000))], 1000)
        assert signal.gis_status is CollisionType.CLEAR
        assert signal.gis_severity is None
        assert signal.collision_count == 0
        assert signal.highest_risk_category is None and signal.nearest_boundary is None
        assert signal.clearance_required is False and signal.clearance_flags == []
        assert signal.max_buffer_overlap_percentage == 0.0

    def test_direct_collision_screening(self, tmp_path, signal_for):
        signal = signal_for(tmp_path, [("Park", "NATIONAL_PARK", square_boundary(-5_000, -5_000, 5_000, 5_000))], 1000)
        assert signal.gis_status is CollisionType.DIRECT_COLLISION
        assert signal.gis_severity is Severity.CRITICAL
        assert signal.highest_risk_category.value == "NATIONAL_PARK"
        assert signal.nearest_boundary.distance_meters == 0.0
        assert signal.clearance_required and signal.clearance_flag_count == 1
        assert signal.max_buffer_overlap_percentage == pytest.approx(100.0, abs=0.1)

    def test_buffer_collision_screening(self, tmp_path, signal_for):
        signal = signal_for(tmp_path, [("Forest", "FOREST", square_boundary(500, -5_000, 6_000, 5_000))], 1000)
        assert signal.gis_status is CollisionType.BUFFER_COLLISION
        assert 0 < signal.max_buffer_overlap_percentage < 100
        assert signal.total_intersection_area_sqm > 0

    def test_nearby_screening(self, tmp_path, signal_for):
        signal = signal_for(tmp_path, [("Forest", "FOREST", square_boundary(3_000, -5_000, 8_000, 5_000))], 1000)
        assert signal.gis_status is CollisionType.NEARBY
        assert signal.max_buffer_overlap_percentage == 0.0
        assert signal.total_intersection_area_sqm == 0.0

    def test_highest_risk_and_nearest_can_differ(self, tmp_path, signal_for):
        """The most severe boundary is not always the closest one."""
        signal = signal_for(
            tmp_path,
            [
                ("Close Forest", "FOREST", square_boundary(400, -800, 3_000, 800)),
                ("Farther Park", "NATIONAL_PARK", square_boundary(900, -4_000, 6_000, 4_000)),
            ],
            1000,
        )
        assert signal.highest_risk_category.value == "NATIONAL_PARK"
        assert signal.nearest_boundary.name == "Close Forest"

    def test_signal_is_deterministic(self, tmp_path, signal_for):
        args = (tmp_path, [("Forest", "FOREST", square_boundary(500, -5_000, 6_000, 5_000))], 1000)
        assert signal_for(*args).model_dump() == signal_for(*args).model_dump()


# ---------------------------------------------------------------------------------
# The GIS priority component
# ---------------------------------------------------------------------------------


class TestGISComponent:
    def _signal(self, **overrides) -> GISIntelligenceSignal:
        base = dict(
            gis_status=CollisionType.CLEAR, gis_severity=None, buffer_meters=1000, collision_count=0,
            boundaries_checked=5, clearance_required=False, clearance_flag_count=0,
            max_buffer_overlap_percentage=0.0, total_intersection_area_sqm=0.0,
            advisory=config.NO_CONFLICT_HEADLINE, disclaimer=config.CLEARANCE_DISCLAIMER,
        )
        return GISIntelligenceSignal(**{**base, **overrides})

    def test_clear_scores_zero(self):
        assert calculate_gis_component(self._signal()) == 0.0

    def test_direct_collision_scores_maximum(self):
        signal = self._signal(
            gis_status=CollisionType.DIRECT_COLLISION, gis_severity=Severity.CRITICAL,
            collision_count=1, clearance_required=True, clearance_flag_count=1,
            max_buffer_overlap_percentage=100.0,
        )
        assert calculate_gis_component(signal) == 100.0

    def test_component_is_the_configured_weighted_blend(self):
        signal = self._signal(
            gis_status=CollisionType.BUFFER_COLLISION, gis_severity=Severity.HIGH,
            collision_count=1, max_buffer_overlap_percentage=20.0,
        )
        expected = (
            config.GIS_STATUS_SCORES["BUFFER_COLLISION"] * config.GIS_SUBWEIGHTS["status"]
            + config.GIS_SEVERITY_SCORES["HIGH"] * config.GIS_SUBWEIGHTS["severity"]
            + 20.0 * config.GIS_SUBWEIGHTS["overlap"]
        )
        assert calculate_gis_component(signal) == pytest.approx(expected)

    def test_clearance_requirement_applies_the_configured_floor(self):
        """A tiny overlap that still triggers clearance cannot score near zero."""
        signal = self._signal(
            gis_status=CollisionType.NEARBY, gis_severity=Severity.LOW, collision_count=1,
            clearance_required=True, clearance_flag_count=1, max_buffer_overlap_percentage=0.0,
        )
        assert calculate_gis_component(signal) == config.GIS_CLEARANCE_FLOOR

    def test_floor_never_lowers_a_higher_score(self):
        signal = self._signal(
            gis_status=CollisionType.DIRECT_COLLISION, gis_severity=Severity.CRITICAL,
            collision_count=1, clearance_required=True, clearance_flag_count=1,
            max_buffer_overlap_percentage=100.0,
        )
        assert calculate_gis_component(signal) > config.GIS_CLEARANCE_FLOOR

    @pytest.mark.parametrize("status", ["NEARBY", "BUFFER_COLLISION", "DIRECT_COLLISION"])
    def test_severity_ordering_is_monotonic(self, status):
        low = self._signal(gis_status=CollisionType(status), gis_severity=Severity.LOW, collision_count=1)
        high = self._signal(gis_status=CollisionType(status), gis_severity=Severity.CRITICAL, collision_count=1)
        assert calculate_gis_component(high) >= calculate_gis_component(low)

    def test_component_stays_in_range(self):
        signal = self._signal(
            gis_status=CollisionType.DIRECT_COLLISION, gis_severity=Severity.CRITICAL,
            collision_count=9, clearance_required=True, clearance_flag_count=9,
            max_buffer_overlap_percentage=100.0,
        )
        assert 0.0 <= calculate_gis_component(signal) <= 100.0


# ---------------------------------------------------------------------------------
# Statutory language
# ---------------------------------------------------------------------------------


class TestLegalWording:
    def test_approved_phrasing_is_used_for_a_conflict(self, tmp_path, signal_for):
        signal = signal_for(tmp_path, [("Park", "NATIONAL_PARK", square_boundary(-5_000, -5_000, 5_000, 5_000))], 1000)
        assert signal.advisory == "Potential spatial conflict detected."
        assert signal.disclaimer == (
            "Final clearance requirements must be verified by the competent authority and "
            "applicable regulations."
        )

    def test_clear_screening_still_carries_the_disclaimer(self, tmp_path, signal_for):
        signal = signal_for(tmp_path, [("Distant", "FOREST", square_boundary(200_000, 200_000, 210_000, 210_000))], 1000)
        assert "competent authority" in signal.disclaimer
        assert "conflict" in signal.advisory.lower()

    @pytest.mark.parametrize("source_path", RESPONSE_SOURCES)
    def test_no_determinative_wording_in_source(self, source_path):
        """The system screens; it does not adjudicate. Fails the build if that slips."""
        text = Path(source_path).read_text(encoding="utf-8").lower()
        # Strip comments and docstrings: prose ABOUT prohibited wording is not the wording.
        text = re.sub(r'"""(?:.|\n)*?"""', "", text)
        text = "\n".join(line.split("#")[0] for line in text.splitlines())
        found = [phrase for phrase in PROHIBITED_PHRASES if phrase in text]
        assert not found, f"{source_path} contains determinative wording: {found}"

    def test_no_determinative_wording_in_a_live_response(self, client):
        body = client.post(
            "/api/v1/project-intelligence",
            json={**PROJECT, "latitude": 21.20, "longitude": 78.20, "buffer_meters": 2000},
        ).json()
        serialized = str(body).lower()
        found = [phrase for phrase in PROHIBITED_PHRASES if phrase in serialized]
        assert not found, f"response contains determinative wording: {found}"
        assert "potential spatial conflict detected" in serialized
        assert "competent authority" in serialized


# ---------------------------------------------------------------------------------
# Intervention recommendations
# ---------------------------------------------------------------------------------


class TestInterventions:
    def _recommend(self, client, **overrides):
        body = client.post("/api/v1/project-intelligence", json={**PROJECT, **overrides}).json()
        return body["interventions"]

    def test_recommendations_are_ranked_and_bounded(self, client):
        items = self._recommend(client)
        assert 1 <= len(items) <= intervention_config.MAX_RECOMMENDATIONS
        assert [item["rank"] for item in items] == list(range(1, len(items) + 1))

    def test_gis_clearance_outranks_managerial_actions(self, client):
        items = self._recommend(client, latitude=21.20, longitude=78.20, buffer_meters=2000)
        assert items[0]["id"] == "spatial_clearance"
        assert items[0]["category"] == "Statutory"
        assert items[0]["evidence_source"] == "GIS boundary screening"

    def test_no_gis_means_no_spatial_recommendations(self, client):
        ids = {item["id"] for item in self._recommend(client)}
        assert "spatial_clearance" not in ids and "site_review" not in ids

    def test_every_recommendation_cites_its_evidence(self, client):
        for item in self._recommend(client, latitude=21.20, longitude=78.20):
            assert item["rationale"] and item["expected_impact"] and item["evidence_source"]
            assert "{" not in item["rationale"], "an unformatted template placeholder leaked"

    def test_recommendations_are_deterministic(self, client):
        first = self._recommend(client, latitude=21.20, longitude=78.20)
        second = self._recommend(client, latitude=21.20, longitude=78.20)
        assert first == second

    def test_a_clean_project_still_gets_a_recommendation(self, client):
        clean = {
            **PROJECT, "revised_cost": 3200, "milestones_delayed": 0, "physical_progress": 60,
            "financial_progress": 58, "project_age_months": 10, "land_acquisition_pending": False,
            "clearance_pending": False, "funding_issue": False, "contractor_issue": False,
            "previous_schedule_deviation": 0,
        }
        items = self._recommend(client, **clean)
        assert len(items) >= 1
        assert items[0]["id"] == "monitoring"


# ---------------------------------------------------------------------------------
# Endpoint behaviour
# ---------------------------------------------------------------------------------


class TestIntelligenceEndpoint:
    def test_response_without_coordinates_is_unchanged_plus_new_optional_sections(self, client):
        body = client.post("/api/v1/project-intelligence", json=PROJECT).json()
        for field in ("project_risk", "top_risk_factors", "risk_summary", "similar_projects",
                      "historical_evidence", "historical_summary"):
            assert field in body
        assert body["gis_screening"] is None
        assert body["priority"]["weight_profile"] == "standard"

    def test_response_with_coordinates_includes_the_gis_section(self, client):
        body = client.post(
            "/api/v1/project-intelligence",
            json={**PROJECT, "latitude": 21.20, "longitude": 78.20, "buffer_meters": 2000},
        ).json()
        gis = body["gis_screening"]
        assert gis["gis_status"] in {"DIRECT_COLLISION", "BUFFER_COLLISION", "NEARBY", "CLEAR"}
        assert gis["buffer_meters"] == 2000
        assert body["priority"]["weight_profile"] == "with_gis"
        assert body["interventions"]

    def test_latitude_without_longitude_is_rejected(self, client):
        for payload in ({**PROJECT, "latitude": 21.2}, {**PROJECT, "longitude": 78.2}):
            response = client.post("/api/v1/project-intelligence", json=payload)
            assert response.status_code == 422
            assert "together" in response.text

    @pytest.mark.parametrize(
        "payload",
        [
            {"latitude": 91, "longitude": 78},
            {"latitude": 21, "longitude": 181},
            {"latitude": 21, "longitude": 78, "buffer_meters": -1},
        ],
    )
    def test_invalid_location_is_rejected(self, client, payload):
        assert client.post("/api/v1/project-intelligence", json={**PROJECT, **payload}).status_code == 422

    def test_pipeline_is_deterministic_end_to_end(self, client):
        payload = {**PROJECT, "latitude": 21.20, "longitude": 78.20, "buffer_meters": 2000}
        first = client.post("/api/v1/project-intelligence", json=payload).json()
        second = client.post("/api/v1/project-intelligence", json=payload).json()
        assert first == second
