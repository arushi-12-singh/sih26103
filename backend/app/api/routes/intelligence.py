from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from app.schemas.intelligence import (
    IntelligenceHistoricalEvidence,
    IntelligenceSimilarProject,
    ProjectIntelligenceResponse,
)
from app.schemas.project import ProjectRiskRequest
from app.services.prediction_service import SavedPredictionService
from app.services.similarity_service import SimilarityService

router = APIRouter(tags=["intelligence"])


@router.post(
    "/project-intelligence",
    response_model=ProjectIntelligenceResponse,
    status_code=status.HTTP_200_OK,
    summary="Combined delay-risk prediction and historical similarity",
    description=(
        "Runs risk prediction (XGBoost + SHAP) and historical similarity search "
        "(NearestNeighbors) for one project in a single call, reusing PredictionService "
        "and SimilarityService directly -- no internal HTTP requests."
    ),
)
def project_intelligence(request: Request, payload: ProjectRiskRequest) -> ProjectIntelligenceResponse:
    """Run risk prediction and historical similarity search in a single call.

    Reuses PredictionService and SimilarityService directly — no internal HTTP
    requests.  The input is validated once by Pydantic and both services receive
    the same consistent feature dict.
    """
    prediction_svc: SavedPredictionService | None = getattr(request.app.state, "prediction_service", None)
    similarity_svc: SimilarityService | None = getattr(request.app.state, "similarity_service", None)

    if prediction_svc is None and similarity_svc is None:
        raise HTTPException(status_code=503, detail="Both prediction and similarity models are unavailable")

    # --- Risk Prediction ---
    prediction_result = None
    errors: list[str] = []
    if prediction_svc is not None:
        try:
            prediction_result = prediction_svc.predict(payload)
        except (ValueError, RuntimeError) as exc:
            errors.append(f"Prediction failed: {exc}")
    else:
        errors.append("Prediction model is unavailable")

    # --- Historical Similarity ---
    similarity_result = None
    if similarity_svc is not None:
        try:
            similarity_result = similarity_svc.find_similar(payload)
        except (ValueError, RuntimeError) as exc:
            errors.append(f"Similarity search failed: {exc}")
    else:
        errors.append("Similarity model is unavailable")

    if prediction_result is None or similarity_result is None:
        raise HTTPException(status_code=503, detail="; ".join(errors))

    # --- Build flat response ---
    similar_projects = _build_similar_projects(similarity_result)
    historical_evidence = _build_evidence(similarity_result)
    historical_summary = _build_historical_summary(historical_evidence)

    return ProjectIntelligenceResponse(
        project_risk=prediction_result.project_risk,
        top_risk_factors=prediction_result.top_risk_factors,
        risk_summary=prediction_result.summary,
        similar_projects=similar_projects,
        historical_evidence=historical_evidence,
        historical_summary=historical_summary,
    )


def _build_similar_projects(similarity_result) -> list[IntelligenceSimilarProject]:  # noqa: ANN001
    """Transform rich similarity matches into the slimmer intelligence response shape."""
    return [
        IntelligenceSimilarProject(
            project_id=match.project_id,
            project_name=match.project_name,
            similarity_score=match.similarity_score,
            sector=match.sector,
            state=match.state,
            actual_delay_months=match.actual_delay_months,
            actual_cost_overrun_percentage=match.actual_cost_overrun_percentage,
            primary_delay_cause=match.primary_delay_cause,
        )
        for match in similarity_result.similar_projects
    ]


def _build_evidence(similarity_result) -> IntelligenceHistoricalEvidence:  # noqa: ANN001
    """Reuse the evidence SimilarityService already computed -- never recompute it here."""
    evidence = similarity_result.historical_evidence
    return IntelligenceHistoricalEvidence(
        projects_analyzed=evidence.projects_analyzed,
        significant_delay_percentage=evidence.significant_delay_percentage,
        average_actual_delay_months=evidence.average_actual_delay_months,
        most_common_delay_cause=evidence.most_common_delay_cause,
    )


def _build_historical_summary(evidence: IntelligenceHistoricalEvidence) -> str:
    """Generate a deterministic summary sentence from the evidence."""
    if evidence.projects_analyzed == 0:
        return "No similar historical projects were found for comparison."
    pct = int(evidence.significant_delay_percentage)
    if pct == 0:
        return (
            f"Among the {evidence.projects_analyzed} most similar historical projects, "
            f"none experienced significant delays (>6 months)."
        )
    return (
        f"Among the {evidence.projects_analyzed} most similar historical projects, "
        f"{pct}% experienced significant delays, primarily associated with "
        f"{evidence.most_common_delay_cause.lower()} issues."
    )
