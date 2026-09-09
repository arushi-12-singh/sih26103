from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.neighbors import NearestNeighbors
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from app.models.preprocessing import BASE_NUMERIC_FEATURES, CATEGORICAL_FEATURES, ENGINEERED_FEATURES, ProjectFeatureEngineer

RAW_FEATURES = BASE_NUMERIC_FEATURES + CATEGORICAL_FEATURES
BOOLEAN_FEATURES = ["land_acquisition_pending", "clearance_pending", "funding_issue", "contractor_issue"]

REQUIRED_HISTORICAL_COLUMNS = RAW_FEATURES + [
    "project_id", "project_name", "actual_delay_months", "actual_cost_overrun_percentage",
    "final_status", "primary_delay_cause", "intervention_taken", "intervention_outcome",
]

# Not every feature is an equally strong signal of what actually happens to a project.
# These weights emphasise the characteristics that scripts/validate_historical_projects.py
# confirms are genuinely associated with delay/cost-overrun outcomes in the historical
# data, so two projects differing mainly in a low-signal field (e.g. sector) are not
# scored as more different than two projects differing in, say, whether land
# acquisition is still pending. Values are a documented priority ordering (1.75-2.5x
# the unweighted baseline of 1.0), not a fitted or optimised parameter.
FEATURE_WEIGHTS: dict[str, float] = {
    "milestone_delay_ratio": 2.5,
    "land_acquisition_pending": 2.0,
    "funding_issue": 2.0,
    "previous_schedule_deviation": 1.75,
    "financial_physical_gap": 1.75,
}
DEFAULT_FEATURE_WEIGHT = 1.0
SIGNIFICANT_DELAY_THRESHOLD_MONTHS = 6


def build_historical_evidence(matches: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate statistics from a set of matched historical projects.

    A single shared implementation so /similar-projects and /project-intelligence
    never compute "significant delay", "average delay", etc. two different ways.
    """
    count = len(matches)
    if count == 0:
        return {
            "projects_analyzed": 0,
            "average_similarity": 0.0,
            "average_actual_delay_months": 0.0,
            "average_cost_overrun_percentage": 0.0,
            "projects_with_significant_delay": 0,
            "significant_delay_percentage": 0.0,
            "most_common_delay_cause": "N/A",
        }
    delays = [m["actual_delay_months"] for m in matches]
    overruns = [m["actual_cost_overrun_percentage"] for m in matches]
    similarities = [m["similarity_score"] for m in matches]
    causes = [m["primary_delay_cause"] for m in matches]
    significant = sum(1 for d in delays if d > SIGNIFICANT_DELAY_THRESHOLD_MONTHS)
    return {
        "projects_analyzed": count,
        "average_similarity": round(sum(similarities) / count, 1),
        "average_actual_delay_months": round(sum(delays) / count, 1),
        "average_cost_overrun_percentage": round(sum(overruns) / count, 1),
        "projects_with_significant_delay": significant,
        "significant_delay_percentage": round(significant / count * 100, 1),
        "most_common_delay_cause": Counter(causes).most_common(1)[0][0],
    }


class FeatureWeighter(BaseEstimator, TransformerMixin):
    """Scale a preprocessed feature matrix by FEATURE_WEIGHTS before distance search.

    Must sit directly after a ColumnTransformer with `set_output(transform="pandas")`,
    so incoming columns are named `<block>__<feature>` (e.g. `numeric__funding_issue`);
    any column without an explicit weight keeps DEFAULT_FEATURE_WEIGHT (unweighted).
    Being a pipeline step means the exact same weighting is applied whether fitting
    on historical data or transforming an incoming project at inference time.
    """

    def __init__(self, weights: dict[str, float]) -> None:
        self.weights = weights

    def fit(self, X: pd.DataFrame, y: Any = None) -> "FeatureWeighter":
        self.weight_vector_ = np.array(
            [self.weights.get(column.split("__", 1)[-1], DEFAULT_FEATURE_WEIGHT) for column in X.columns]
        )
        return self

    def transform(self, X: pd.DataFrame) -> np.ndarray:
        return X.to_numpy(dtype=float) * self.weight_vector_


class HistoricalSimilarityModel:
    def __init__(self, artifact_path: Path) -> None:
        if not artifact_path.exists():
            raise FileNotFoundError(f"Similarity model not found: {artifact_path}")
        artifact = joblib.load(artifact_path)
        if not isinstance(artifact, dict) or not {"pipeline", "neighbors", "historical"}.issubset(artifact):
            raise ValueError("Saved similarity artifact is not compatible")
        historical: pd.DataFrame = artifact["historical"]
        if historical.empty:
            raise ValueError("Historical dataset is empty; similarity search is unavailable")
        self.pipeline: Pipeline = artifact["pipeline"]
        self.neighbors: NearestNeighbors = artifact["neighbors"]
        self.historical = historical

    @classmethod
    def train(cls, historical: pd.DataFrame, artifact_path: Path) -> None:
        missing = set(REQUIRED_HISTORICAL_COLUMNS).difference(historical.columns)
        if missing:
            raise ValueError(f"Historical data is missing columns: {sorted(missing)}")
        numeric_features = BASE_NUMERIC_FEATURES + ENGINEERED_FEATURES
        preprocessor = ColumnTransformer([
            ("numeric", Pipeline([
                ("imputer", SimpleImputer(strategy="median")),
                ("scale", StandardScaler()),
            ]), numeric_features),
            ("categorical", Pipeline([
                ("imputer", SimpleImputer(strategy="most_frequent")),
                ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
            ]), CATEGORICAL_FEATURES),
        ]).set_output(transform="pandas")
        pipeline = Pipeline([
            ("feature_engineering", ProjectFeatureEngineer()),
            ("preprocessing", preprocessor),
            ("weighting", FeatureWeighter(FEATURE_WEIGHTS)),
        ])
        features = historical[RAW_FEATURES].copy()
        matrix = pipeline.fit_transform(features)
        neighbors = NearestNeighbors(n_neighbors=min(10, len(historical)), metric="euclidean", algorithm="brute")
        neighbors.fit(matrix)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"pipeline": pipeline, "neighbors": neighbors, "historical": historical.reset_index(drop=True)}, artifact_path)

    def find_matches(self, project: dict[str, Any], top_k: int = 5) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        frame = pd.DataFrame([project], columns=RAW_FEATURES)
        for column in BOOLEAN_FEATURES:
            frame[column] = frame[column].astype(int)
        try:
            transformed = self.pipeline.transform(frame)
        except Exception as exc:  # noqa: BLE001 - surface as a client-facing validation error
            raise ValueError(f"Failed to preprocess project features: {exc}") from exc
        distances, indices = self.neighbors.kneighbors(transformed, n_neighbors=min(top_k, len(self.historical)))
        matches = []
        for distance, index in zip(distances[0], indices[0]):
            record = self.historical.iloc[int(index)]
            similarity = float(np.clip(100 / (1 + distance / 4), 0, 100))
            matches.append({
                "project_id": str(record["project_id"]),
                "project_name": str(record["project_name"]),
                "sector": str(record["sector"]),
                "state": str(record["state"]),
                "similarity_score": round(similarity, 1),
                "actual_delay_months": int(record["actual_delay_months"]),
                "actual_cost_overrun_percentage": float(record["actual_cost_overrun_percentage"]),
                "final_status": str(record["final_status"]),
                "primary_delay_cause": str(record["primary_delay_cause"]),
                "intervention_taken": str(record["intervention_taken"]),
                "intervention_outcome": str(record["intervention_outcome"]),
            })
        return matches, build_historical_evidence(matches)


def build_similarity_model(artifact_path: Path) -> HistoricalSimilarityModel:
    return HistoricalSimilarityModel(artifact_path)
