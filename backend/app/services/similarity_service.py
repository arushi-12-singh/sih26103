from __future__ import annotations

from pathlib import Path
from typing import Any

from app.models.similarity_model import SIGNIFICANT_DELAY_THRESHOLD_MONTHS, HistoricalSimilarityModel
from app.schemas.similarity import HistoricalEvidence, SimilarityRequest, SimilarityResponse

MODEL_PATH = Path(__file__).resolve().parents[2] / "trained_models" / "similarity_pipeline" / "historical_similarity.joblib"
DEFAULT_TOP_K = 5


class SimilarityService:
    def __init__(self, model: HistoricalSimilarityModel) -> None:
        self.model = model

    def find_similar(self, payload: SimilarityRequest, top_k: int = DEFAULT_TOP_K) -> SimilarityResponse:
        features = payload.model_dump(exclude={"project_id"})
        matches, evidence = self.model.find_matches(features, top_k=top_k)
        return SimilarityResponse(
            similar_projects=matches,
            historical_evidence=HistoricalEvidence(**evidence),
            historical_summary=_build_summary(evidence),
        )


def _build_summary(evidence: dict[str, Any]) -> str:
    """Deterministic, template-based summary -- no LLM involved."""
    if evidence["projects_analyzed"] == 0:
        return "No similar historical projects were found for comparison."
    count = evidence["projects_analyzed"]
    significant = evidence["projects_with_significant_delay"]
    if significant == 0:
        return (
            f"Among the {count} most similar historical projects, none experienced significant delays "
            f"(>{SIGNIFICANT_DELAY_THRESHOLD_MONTHS} months)."
        )
    return (
        f"Among the {count} most similar historical projects, {significant} experienced significant delays. "
        f"{evidence['most_common_delay_cause']} was the most common contributing factor, "
        f"with an average delay of {evidence['average_actual_delay_months']} months."
    )


def build_similarity_service() -> SimilarityService:
    return SimilarityService(HistoricalSimilarityModel(MODEL_PATH))
