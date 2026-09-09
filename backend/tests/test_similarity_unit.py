"""Unit tests for HistoricalSimilarityModel internals that the API-level
integration tests in test_integration.py can't easily exercise directly:
empty-dataset handling, feature-weighting scope, and leakage guards.
"""

from __future__ import annotations

from pathlib import Path

import joblib
import pandas as pd
import pytest

from app.models.similarity_model import (
    BASE_NUMERIC_FEATURES,
    CATEGORICAL_FEATURES,
    RAW_FEATURES,
    HistoricalSimilarityModel,
    build_historical_evidence,
)

OUTCOME_COLUMNS = {
    "actual_delay_months", "actual_cost_overrun_percentage", "final_status",
    "primary_delay_cause", "intervention_taken", "intervention_outcome",
}


def test_raw_features_never_include_outcome_columns() -> None:
    """Guards against information leakage: outcome columns must never be similarity inputs."""
    assert set(RAW_FEATURES).isdisjoint(OUTCOME_COLUMNS)
    assert set(BASE_NUMERIC_FEATURES + CATEGORICAL_FEATURES).isdisjoint(OUTCOME_COLUMNS)


def test_empty_historical_dataset_raises_clear_error(tmp_path: Path) -> None:
    """An artifact whose historical data is empty must fail to load, not crash at request time."""
    empty_historical = pd.DataFrame(columns=RAW_FEATURES + ["project_id", "project_name", *OUTCOME_COLUMNS])
    artifact_path = tmp_path / "empty_similarity.joblib"
    joblib.dump({"pipeline": object(), "neighbors": object(), "historical": empty_historical}, artifact_path)

    with pytest.raises(ValueError, match="empty"):
        HistoricalSimilarityModel(artifact_path)


def test_missing_artifact_file_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        HistoricalSimilarityModel(tmp_path / "does_not_exist.joblib")


def test_incompatible_artifact_raises_value_error(tmp_path: Path) -> None:
    artifact_path = tmp_path / "bad_artifact.joblib"
    joblib.dump({"pipeline": object()}, artifact_path)  # missing "neighbors" / "historical"
    with pytest.raises(ValueError, match="not compatible"):
        HistoricalSimilarityModel(artifact_path)


def test_feature_weighter_targets_only_intended_columns() -> None:
    """The documented weighting must apply to exactly the 5 named risk features."""
    model_path = Path(__file__).resolve().parents[1] / "trained_models" / "similarity_pipeline" / "historical_similarity.joblib"
    model = HistoricalSimilarityModel(model_path)
    weighter = model.pipeline.named_steps["weighting"]
    preprocessor = model.pipeline.named_steps["preprocessing"]
    weighted_columns = {
        name.split("__", 1)[-1]: weight
        for name, weight in zip(preprocessor.get_feature_names_out(), weighter.weight_vector_)
        if weight != 1.0
    }
    assert weighted_columns == {
        "milestone_delay_ratio": 2.5,
        "land_acquisition_pending": 2.0,
        "funding_issue": 2.0,
        "previous_schedule_deviation": 1.75,
        "financial_physical_gap": 1.75,
    }


def test_build_historical_evidence_empty_matches_does_not_crash() -> None:
    evidence = build_historical_evidence([])
    assert evidence["projects_analyzed"] == 0
    assert evidence["significant_delay_percentage"] == 0.0
    assert evidence["most_common_delay_cause"] == "N/A"


def test_build_historical_evidence_json_serializable_types() -> None:
    """Every value must be a native Python type -- numpy scalars break JSON serialization."""
    matches = [
        {"actual_delay_months": 8, "actual_cost_overrun_percentage": 12.5, "similarity_score": 91.2, "primary_delay_cause": "Land acquisition"},
        {"actual_delay_months": 2, "actual_cost_overrun_percentage": 3.0, "similarity_score": 80.0, "primary_delay_cause": "Funding release"},
    ]
    evidence = build_historical_evidence(matches)
    for value in evidence.values():
        assert type(value) in (int, float, str), f"Non-native type leaked into evidence: {value!r} ({type(value)})"


def test_find_matches_returns_only_native_json_types() -> None:
    model_path = Path(__file__).resolve().parents[1] / "trained_models" / "similarity_pipeline" / "historical_similarity.joblib"
    model = HistoricalSimilarityModel(model_path)
    project = {
        "sector": "Roads", "state": "Bihar", "original_cost": 500.0, "revised_cost": 600.0,
        "planned_duration_months": 36, "project_age_months": 30, "physical_progress": 40.0,
        "financial_progress": 35.0, "milestones_total": 10, "milestones_delayed": 4,
        "land_acquisition_pending": True, "clearance_pending": False, "funding_issue": True,
        "contractor_issue": False, "previous_schedule_deviation": 5.0,
    }
    matches, evidence = model.find_matches(project, top_k=3)
    for match in matches:
        for key, value in match.items():
            assert type(value) in (int, float, str), f"{key}={value!r} is not a native JSON-safe type ({type(value)})"
    for key, value in evidence.items():
        assert type(value) in (int, float, str), f"{key}={value!r} is not a native JSON-safe type ({type(value)})"


def test_similarity_scores_sorted_descending_and_bounded() -> None:
    model_path = Path(__file__).resolve().parents[1] / "trained_models" / "similarity_pipeline" / "historical_similarity.joblib"
    model = HistoricalSimilarityModel(model_path)
    project = {
        "sector": "Railways", "state": "Maharashtra", "original_cost": 1200.0, "revised_cost": 1500.0,
        "planned_duration_months": 48, "project_age_months": 40, "physical_progress": 45.0,
        "financial_progress": 30.0, "milestones_total": 15, "milestones_delayed": 7,
        "land_acquisition_pending": True, "clearance_pending": True, "funding_issue": True,
        "contractor_issue": False, "previous_schedule_deviation": 10.0,
    }
    matches, _ = model.find_matches(project, top_k=10)
    scores = [m["similarity_score"] for m in matches]
    assert scores == sorted(scores, reverse=True)
    assert all(0 <= s <= 100 for s in scores)


def test_identical_project_scores_near_100_percent() -> None:
    """Feeding a historical project's own features back in should score ~100% similar to itself."""
    model_path = Path(__file__).resolve().parents[1] / "trained_models" / "similarity_pipeline" / "historical_similarity.joblib"
    model = HistoricalSimilarityModel(model_path)
    row = model.historical.iloc[0]
    project = {feature: row[feature] for feature in RAW_FEATURES}
    matches, _ = model.find_matches(project, top_k=1)
    assert matches[0]["similarity_score"] >= 95.0
