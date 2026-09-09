"""Tests for Feature 3 -- the deterministic Project Priority & Decision Engine.

Covers each calculate_* component function directly (they're pure and independently
testable by design), the config's self-validation, and the API endpoint end-to-end.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import priority_config as config
from app.services.priority_service import (
    calculate_delay_component,
    calculate_financial_component,
    calculate_historical_component,
    calculate_priority_score,
    calculate_risk_component,
    calculate_urgency_component,
)

HIGH_RISK_PROJECT = {
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


class TestPriorityConfig:
    def test_component_weights_sum_to_one(self) -> None:
        assert abs(sum(config.COMPONENT_WEIGHTS.values()) - 1.0) < 1e-9

    def test_component_weights_match_spec(self) -> None:
        assert config.COMPONENT_WEIGHTS == {
            "risk": 0.35, "delay": 0.20, "financial": 0.20, "historical": 0.15, "urgency": 0.10,
        }

    def test_historical_subweights_sum_to_one(self) -> None:
        assert abs(sum(config.HISTORICAL_SUBWEIGHTS.values()) - 1.0) < 1e-9

    def test_urgency_subweights_sum_to_one(self) -> None:
        assert abs(sum(config.URGENCY_SUBWEIGHTS.values()) - 1.0) < 1e-9

    def test_category_thresholds_ascending(self) -> None:
        thresholds = [t for t, _ in config.PRIORITY_CATEGORY_THRESHOLDS]
        assert thresholds == sorted(thresholds)


class TestCalculateRiskComponent:
    def test_matches_worked_example(self) -> None:
        # Spec's own example: 0.87 -> 87
        assert calculate_risk_component(0.87) == 87.0

    def test_zero_and_one_bounds(self) -> None:
        assert calculate_risk_component(0.0) == 0.0
        assert calculate_risk_component(1.0) == 100.0

    def test_clipped_to_valid_range(self) -> None:
        assert calculate_risk_component(-0.5) == 0.0
        assert calculate_risk_component(1.5) == 100.0


class TestCalculateDelayComponent:
    def test_expected_value_scaling(self) -> None:
        # probability 0.5 x avg delay 12 months = 6 expected months; anchor 24 -> 25.0
        assert calculate_delay_component(0.5, 12.0, max_expected_delay_months=24.0) == 25.0

    def test_configurable_max_changes_result(self) -> None:
        low_anchor = calculate_delay_component(0.5, 12.0, max_expected_delay_months=12.0)
        high_anchor = calculate_delay_component(0.5, 12.0, max_expected_delay_months=48.0)
        assert low_anchor > high_anchor, "A smaller configured max should score the same delay as more severe"

    def test_clipped_at_100(self) -> None:
        assert calculate_delay_component(1.0, 100.0, max_expected_delay_months=24.0) == 100.0

    def test_zero_probability_or_delay_gives_zero(self) -> None:
        assert calculate_delay_component(0.0, 20.0) == 0.0
        assert calculate_delay_component(0.9, 0.0) == 0.0


class TestCalculateFinancialComponent:
    def test_bounded_between_zero_and_hundred(self) -> None:
        score = calculate_financial_component(1000, 1200)
        assert 0 <= score <= 100

    def test_larger_project_scores_higher(self) -> None:
        small = calculate_financial_component(200, 220)
        large = calculate_financial_component(20000, 24000)
        assert large > small

    def test_log_scaling_compresses_extreme_outlier(self) -> None:
        """A 100x larger project must not produce anywhere near a 100x larger score."""
        baseline = calculate_financial_component(500, 550)
        huge = calculate_financial_component(50_000, 55_000)
        assert huge <= 100
        assert huge < baseline * 3, "Log scaling should prevent one huge project from dominating the scale"

    def test_negative_or_zero_overrun_does_not_reduce_below_revised_cost(self) -> None:
        # revised_cost < original_cost (under budget) should not error or go negative
        score = calculate_financial_component(1000, 900)
        assert 0 <= score <= 100

    def test_zero_original_cost_does_not_crash(self) -> None:
        score = calculate_financial_component(0, 500)
        assert 0 <= score <= 100


class TestCalculateHistoricalComponent:
    def test_blends_rate_and_severity(self) -> None:
        score = calculate_historical_component(80.0, 12.0, max_expected_delay_months=24.0)
        # rate_score=80*0.6=48, severity_score=(12/24*100)*0.4=20 -> 68.0
        assert score == 68.0

    def test_zero_evidence_gives_zero(self) -> None:
        assert calculate_historical_component(0.0, 0.0) == 0.0

    def test_bounded_at_100(self) -> None:
        assert calculate_historical_component(100.0, 999.0) == 100.0


class TestCalculateUrgencyComponent:
    def test_on_schedule_project_scores_low(self) -> None:
        score = calculate_urgency_component(
            project_age_months=5, planned_duration_months=48,
            previous_schedule_deviation=-12, milestones_delayed=0, milestones_total=10,
        )
        assert score < 20

    def test_overdue_project_scores_high(self) -> None:
        score = calculate_urgency_component(
            project_age_months=90, planned_duration_months=36,
            previous_schedule_deviation=36, milestones_delayed=9, milestones_total=10,
        )
        assert score > 80

    def test_zero_milestones_total_does_not_crash(self) -> None:
        score = calculate_urgency_component(
            project_age_months=10, planned_duration_months=24,
            previous_schedule_deviation=0, milestones_delayed=0, milestones_total=0,
        )
        assert 0 <= score <= 100

    def test_zero_planned_duration_does_not_crash(self) -> None:
        score = calculate_urgency_component(
            project_age_months=10, planned_duration_months=0,
            previous_schedule_deviation=0, milestones_delayed=0, milestones_total=10,
        )
        assert 0 <= score <= 100


class TestCalculatePriorityScore:
    def test_matches_documented_formula(self) -> None:
        score = calculate_priority_score(
            risk_component=80, delay_component=50, financial_component=60,
            historical_component=70, urgency_component=40,
        )
        expected = 80 * 0.35 + 50 * 0.20 + 60 * 0.20 + 70 * 0.15 + 40 * 0.10
        assert score == round(expected, 1)

    def test_all_zero_components_gives_zero(self) -> None:
        assert calculate_priority_score(0, 0, 0, 0, 0) == 0.0

    def test_all_max_components_gives_hundred(self) -> None:
        assert calculate_priority_score(100, 100, 100, 100, 100) == 100.0

    def test_bounded_regardless_of_input(self) -> None:
        score = calculate_priority_score(999, 999, 999, 999, 999)
        assert score == 100.0


class TestPriorityEndpoint:
    def test_high_risk_project_returns_200(self, client: TestClient) -> None:
        r = client.post("/api/v1/project-priority", json=HIGH_RISK_PROJECT)
        assert r.status_code == 200
        data = r.json()
        assert 0 <= data["priority_score"] <= 100
        assert data["priority_category"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")
        assert len(data["score_breakdown"]) == 5
        assert {c["name"] for c in data["score_breakdown"]} == {
            "risk", "delay", "financial", "historical", "urgency",
        }

    def test_high_risk_scores_higher_than_low_risk(self, client: TestClient) -> None:
        high = client.post("/api/v1/project-priority", json=HIGH_RISK_PROJECT).json()
        low = client.post("/api/v1/project-priority", json=LOW_RISK_PROJECT).json()
        assert high["priority_score"] > low["priority_score"]

    def test_category_matches_score_thresholds(self, client: TestClient) -> None:
        data = client.post("/api/v1/project-priority", json=HIGH_RISK_PROJECT).json()
        score, category = data["priority_score"], data["priority_category"]
        if score <= 30:
            assert category == "LOW"
        elif score <= 55:
            assert category == "MEDIUM"
        elif score <= 75:
            assert category == "HIGH"
        else:
            assert category == "CRITICAL"

    def test_decision_explanation_is_deterministic(self, client: TestClient) -> None:
        first = client.post("/api/v1/project-priority", json=HIGH_RISK_PROJECT).json()
        second = client.post("/api/v1/project-priority", json=HIGH_RISK_PROJECT).json()
        assert first["decision_explanation"] == second["decision_explanation"]
        assert first["priority_score"] == second["priority_score"]

    def test_weighted_contributions_sum_to_priority_score(self, client: TestClient) -> None:
        data = client.post("/api/v1/project-priority", json=HIGH_RISK_PROJECT).json()
        total = round(sum(c["weighted_contribution"] for c in data["score_breakdown"]), 1)
        assert abs(total - data["priority_score"]) <= 0.2  # rounding tolerance across 5 components

    def test_component_weights_exposed_in_response(self, client: TestClient) -> None:
        data = client.post("/api/v1/project-priority", json=HIGH_RISK_PROJECT).json()
        weights = {c["name"]: c["weight"] for c in data["score_breakdown"]}
        assert weights == {"risk": 0.35, "delay": 0.20, "financial": 0.20, "historical": 0.15, "urgency": 0.10}

    def test_recommended_attention_level_present(self, client: TestClient) -> None:
        data = client.post("/api/v1/project-priority", json=HIGH_RISK_PROJECT).json()
        assert isinstance(data["recommended_attention_level"], str)
        assert len(data["recommended_attention_level"]) > 5


class TestPriorityInvalidInput:
    def test_invalid_project_fields_rejected(self, client: TestClient) -> None:
        payload = {**LOW_RISK_PROJECT, "physical_progress": 150}
        r = client.post("/api/v1/project-priority", json=payload)
        assert r.status_code == 422

    def test_milestones_delayed_exceeds_total_rejected(self, client: TestClient) -> None:
        payload = {**LOW_RISK_PROJECT, "milestones_total": 5, "milestones_delayed": 10}
        r = client.post("/api/v1/project-priority", json=payload)
        assert r.status_code == 422

    def test_server_still_healthy_after_invalid(self, client: TestClient) -> None:
        client.post("/api/v1/project-priority", json={**LOW_RISK_PROJECT, "physical_progress": 999})
        r = client.get("/health")
        assert r.status_code == 200
