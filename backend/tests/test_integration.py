"""End-to-end integration tests for the PAIMANA Project Intelligence API.

Covers:
  TC1 — High-risk railway project
  TC2 — Low-risk project
  TC3 — Milestone sensitivity (milestones_delayed = 0, 5, 10)
  TC4 — Identical historical project lookup
  TC5 — Invalid input validation
  Smoke tests for /health, /predict-risk, /similar-projects, /project-intelligence
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Shared payloads
# ---------------------------------------------------------------------------

HIGH_RISK_PROJECT = {
    "sector": "Railways",
    "state": "Uttar Pradesh",
    "original_cost": 5000,
    "revised_cost": 6200,
    "planned_duration_months": 48,
    "project_age_months": 42,
    "physical_progress": 35,
    "financial_progress": 18,
    "milestones_total": 20,
    "milestones_delayed": 12,
    "land_acquisition_pending": True,
    "clearance_pending": True,
    "funding_issue": True,
    "contractor_issue": True,
    "previous_schedule_deviation": 15,
}

LOW_RISK_PROJECT = {
    "sector": "Roads",
    "state": "Kerala",
    "original_cost": 200,
    "revised_cost": 205,
    "planned_duration_months": 24,
    "project_age_months": 10,
    "physical_progress": 55,
    "financial_progress": 53,
    "milestones_total": 10,
    "milestones_delayed": 0,
    "land_acquisition_pending": False,
    "clearance_pending": False,
    "funding_issue": False,
    "contractor_issue": False,
    "previous_schedule_deviation": -1,
}


def _base_milestone_project(milestones_delayed: int) -> dict:
    """Return a project payload with only milestones_delayed varying."""
    return {
        "sector": "Railways",
        "state": "Maharashtra",
        "original_cost": 1000,
        "revised_cost": 1050,
        "planned_duration_months": 36,
        "project_age_months": 18,
        "physical_progress": 50,
        "financial_progress": 48,
        "milestones_total": 15,
        "milestones_delayed": milestones_delayed,
        "land_acquisition_pending": False,
        "clearance_pending": False,
        "funding_issue": False,
        "contractor_issue": False,
        "previous_schedule_deviation": 2,
    }


# ===================================================================
# Smoke tests — basic endpoint availability
# ===================================================================


class TestSmoke:
    """Verify every endpoint responds without crashing."""

    def test_health(self, client: TestClient) -> None:
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["model_status"] == "loaded"
        assert data["similarity_status"] == "loaded"

    def test_predict_risk(self, client: TestClient) -> None:
        r = client.post("/api/v1/predict-risk", json=HIGH_RISK_PROJECT)
        assert r.status_code == 200
        assert "project_risk" in r.json()

    def test_similar_projects(self, client: TestClient) -> None:
        r = client.post("/api/v1/similar-projects", json=HIGH_RISK_PROJECT)
        assert r.status_code == 200
        assert "similar_projects" in r.json()

    def test_project_intelligence(self, client: TestClient) -> None:
        r = client.post("/api/v1/project-intelligence", json=HIGH_RISK_PROJECT)
        assert r.status_code == 200
        data = r.json()
        assert "project_risk" in data
        assert "similar_projects" in data
        assert "historical_evidence" in data


# ===================================================================
# TC1 — High-risk railway project
# ===================================================================


class TestTC1HighRisk:
    """A project with every risk signal active should score HIGH or CRITICAL."""

    @pytest.fixture(scope="class")
    def risk_result(self, client: TestClient) -> dict:
        r = client.post("/api/v1/predict-risk", json=HIGH_RISK_PROJECT)
        assert r.status_code == 200
        return r.json()

    @pytest.fixture(scope="class")
    def similarity_result(self, client: TestClient) -> dict:
        r = client.post("/api/v1/similar-projects", json=HIGH_RISK_PROJECT)
        assert r.status_code == 200
        return r.json()

    def test_risk_level_high_or_critical(self, risk_result: dict) -> None:
        level = risk_result["project_risk"]["risk_level"]
        assert level in ("HIGH", "CRITICAL"), f"Expected HIGH or CRITICAL, got {level}"

    def test_risk_percentage_above_60(self, risk_result: dict) -> None:
        pct = risk_result["project_risk"]["risk_percentage"]
        assert pct > 60, f"Expected risk >60%, got {pct}%"

    def test_similar_projects_returned(self, similarity_result: dict) -> None:
        assert len(similarity_result["similar_projects"]) > 0

    def test_similarity_sorted_descending(self, similarity_result: dict) -> None:
        sims = [m["similarity_score"] for m in similarity_result["similar_projects"]]
        assert sims == sorted(sims, reverse=True), f"Not sorted desc: {sims}"

    def test_evidence_calculations_correct(self, similarity_result: dict) -> None:
        matches = similarity_result["similar_projects"]
        evidence = similarity_result["historical_evidence"]
        assert evidence["projects_analyzed"] == len(matches)
        significant = sum(1 for m in matches if m["actual_delay_months"] > 6)
        assert evidence["projects_with_significant_delay"] == significant
        expected_pct = round(significant / len(matches) * 100, 1) if matches else 0.0
        assert evidence["significant_delay_percentage"] == expected_pct
        expected_avg_delay = round(sum(m["actual_delay_months"] for m in matches) / len(matches), 1) if matches else 0.0
        assert evidence["average_actual_delay_months"] == expected_avg_delay

    def test_summary_matches_data(self, similarity_result: dict) -> None:
        evidence = similarity_result["historical_evidence"]
        summary = similarity_result["historical_summary"]
        assert str(evidence["projects_analyzed"]) in summary
        assert str(evidence["projects_with_significant_delay"]) in summary


# ===================================================================
# TC2 — Low-risk project
# ===================================================================


class TestTC2LowRisk:
    """A clean project should produce a lower risk score than the high-risk one."""

    @pytest.fixture(scope="class")
    def low_result(self, client: TestClient) -> dict:
        r = client.post("/api/v1/predict-risk", json=LOW_RISK_PROJECT)
        assert r.status_code == 200
        return r.json()

    @pytest.fixture(scope="class")
    def high_result(self, client: TestClient) -> dict:
        r = client.post("/api/v1/predict-risk", json=HIGH_RISK_PROJECT)
        assert r.status_code == 200
        return r.json()

    @pytest.fixture(scope="class")
    def low_similarity(self, client: TestClient) -> dict:
        r = client.post("/api/v1/similar-projects", json=LOW_RISK_PROJECT)
        assert r.status_code == 200
        return r.json()

    def test_lower_risk_than_tc1(self, low_result: dict, high_result: dict) -> None:
        low_pct = low_result["project_risk"]["risk_percentage"]
        high_pct = high_result["project_risk"]["risk_percentage"]
        assert low_pct < high_pct, f"Low-risk ({low_pct}%) should be < high-risk ({high_pct}%)"

    def test_different_similar_projects(self, low_similarity: dict, client: TestClient) -> None:
        high_r = client.post("/api/v1/similar-projects", json=HIGH_RISK_PROJECT)
        high_sim = high_r.json()
        low_ids = {m["project_id"] for m in low_similarity["similar_projects"]}
        high_ids = {m["project_id"] for m in high_sim["similar_projects"]}
        # At minimum they should not be exactly the same set
        assert low_ids != high_ids or len(low_ids) == 0, "Low and high risk matched identical projects"

    def test_evidence_generated(self, low_similarity: dict) -> None:
        ev = low_similarity["historical_evidence"]
        assert ev["projects_analyzed"] > 0
        summary = low_similarity["historical_summary"]
        assert isinstance(summary, str)
        assert len(summary) > 10


# ===================================================================
# TC3 — Milestone sensitivity
# ===================================================================


class TestTC3MilestoneSensitivity:
    """Varying milestones_delayed (0, 5, 10) should change returned results."""

    @pytest.fixture(scope="class")
    def results(self, client: TestClient) -> dict[int, dict]:
        out: dict[int, dict] = {}
        for n in (0, 5, 10):
            r = client.post("/api/v1/project-intelligence", json=_base_milestone_project(n))
            assert r.status_code == 200
            out[n] = r.json()
        return out

    def test_risk_increases_with_milestones(self, results: dict[int, dict]) -> None:
        pcts = {n: results[n]["project_risk"]["risk_percentage"] for n in (0, 5, 10)}
        assert pcts[0] <= pcts[5] <= pcts[10], f"Risk should increase: {pcts}"

    def test_similarity_results_change(self, results: dict[int, dict]) -> None:
        ids_0 = [m["project_id"] for m in results[0]["similar_projects"]]
        ids_10 = [m["project_id"] for m in results[10]["similar_projects"]]
        # With such different milestone profiles, at least one match should differ
        # (or at minimum the similarity scores should differ)
        sims_0 = [m["similarity_score"] for m in results[0]["similar_projects"]]
        sims_10 = [m["similarity_score"] for m in results[10]["similar_projects"]]
        assert ids_0 != ids_10 or sims_0 != sims_10, (
            "Similarity results should change when milestones_delayed goes from 0 to 10"
        )


# ===================================================================
# TC4 — Identical historical project lookup
# ===================================================================


class TestTC4IdenticalHistorical:
    """Sending a historical project's own features should return it (or very similar) at the top."""

    @pytest.fixture(scope="class")
    def historical_row(self) -> pd.Series:
        csv_path = Path(__file__).resolve().parents[1] / "data" / "historical_projects.csv"
        df = pd.read_csv(csv_path)
        return df.iloc[0]

    @pytest.fixture(scope="class")
    def similarity_result(self, client: TestClient, historical_row: pd.Series) -> dict:
        payload = {
            "sector": str(historical_row["sector"]),
            "state": str(historical_row["state"]),
            "original_cost": float(historical_row["original_cost"]),
            "revised_cost": float(historical_row["revised_cost"]),
            "planned_duration_months": int(historical_row["planned_duration_months"]),
            "project_age_months": int(historical_row["project_age_months"]),
            "physical_progress": float(historical_row["physical_progress"]),
            "financial_progress": float(historical_row["financial_progress"]),
            "milestones_total": max(1, int(historical_row["milestones_total"])),
            "milestones_delayed": int(historical_row["milestones_delayed"]),
            "land_acquisition_pending": bool(historical_row["land_acquisition_pending"]),
            "clearance_pending": bool(historical_row["clearance_pending"]),
            "funding_issue": bool(historical_row["funding_issue"]),
            "contractor_issue": bool(historical_row["contractor_issue"]),
            "previous_schedule_deviation": float(historical_row["previous_schedule_deviation"]),
        }
        r = client.post("/api/v1/similar-projects", json=payload)
        assert r.status_code == 200
        return r.json()

    def test_top_match_very_high_similarity(self, similarity_result: dict) -> None:
        top = similarity_result["similar_projects"][0]
        assert top["similarity_score"] >= 80, (
            f"Expected top match >= 80% similarity for identical input, got {top['similarity_score']}%"
        )

    def test_self_or_near_identical_in_results(self, similarity_result: dict, historical_row: pd.Series) -> None:
        # The exact project or one with the same sector/state should appear
        project_id = str(historical_row["project_id"])
        match_ids = [m["project_id"] for m in similarity_result["similar_projects"]]
        match_sectors = [m["sector"] for m in similarity_result["similar_projects"]]
        # Either the exact project is in the top matches, or the top match shares sector
        assert project_id in match_ids or historical_row["sector"] in match_sectors


# ===================================================================
# TC5 — Invalid input validation
# ===================================================================


class TestTC5InvalidInput:
    """Invalid payloads should return 422 and never crash the server."""

    def test_physical_progress_out_of_range(self, client: TestClient) -> None:
        payload = {**LOW_RISK_PROJECT, "physical_progress": 150}
        r = client.post("/api/v1/predict-risk", json=payload)
        assert r.status_code == 422

    def test_financial_progress_negative(self, client: TestClient) -> None:
        payload = {**LOW_RISK_PROJECT, "financial_progress": -10}
        r = client.post("/api/v1/predict-risk", json=payload)
        assert r.status_code == 422

    def test_milestones_total_zero(self, client: TestClient) -> None:
        payload = {**LOW_RISK_PROJECT, "milestones_total": 0}
        r = client.post("/api/v1/predict-risk", json=payload)
        assert r.status_code == 422

    def test_milestones_delayed_exceeds_total(self, client: TestClient) -> None:
        payload = {**LOW_RISK_PROJECT, "milestones_total": 5, "milestones_delayed": 10}
        r = client.post("/api/v1/predict-risk", json=payload)
        assert r.status_code == 422

    def test_invalid_on_similarity_endpoint(self, client: TestClient) -> None:
        payload = {**LOW_RISK_PROJECT, "physical_progress": 150}
        r = client.post("/api/v1/similar-projects", json=payload)
        assert r.status_code == 422

    def test_top_k_above_maximum_rejected(self, client: TestClient) -> None:
        r = client.post("/api/v1/similar-projects?top_k=50", json=LOW_RISK_PROJECT)
        assert r.status_code == 422

    def test_top_k_zero_rejected(self, client: TestClient) -> None:
        r = client.post("/api/v1/similar-projects?top_k=0", json=LOW_RISK_PROJECT)
        assert r.status_code == 422

    def test_top_k_negative_rejected(self, client: TestClient) -> None:
        r = client.post("/api/v1/similar-projects?top_k=-3", json=LOW_RISK_PROJECT)
        assert r.status_code == 422

    def test_top_k_non_integer_rejected(self, client: TestClient) -> None:
        r = client.post("/api/v1/similar-projects?top_k=abc", json=LOW_RISK_PROJECT)
        assert r.status_code == 422

    def test_invalid_on_intelligence_endpoint(self, client: TestClient) -> None:
        payload = {**LOW_RISK_PROJECT, "milestones_total": 0}
        r = client.post("/api/v1/project-intelligence", json=payload)
        assert r.status_code == 422

    def test_server_still_healthy_after_invalid(self, client: TestClient) -> None:
        """After all invalid requests, the server should still work normally."""
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_valid_request_still_works(self, client: TestClient) -> None:
        """After invalid requests, a valid request should still succeed."""
        r = client.post("/api/v1/predict-risk", json=LOW_RISK_PROJECT)
        assert r.status_code == 200
