from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from app.schemas.priority import PriorityRequest, PriorityResponse
from app.services.prediction_service import SavedPredictionService
from app.services.priority_service import PriorityService
from app.services.similarity_service import SimilarityService

router = APIRouter(tags=["priority"])


@router.post(
    "/project-priority",
    response_model=PriorityResponse,
    status_code=status.HTTP_200_OK,
    summary="Deterministic project priority and decision engine",
    description=(
        "Combines Feature 1 (XGBoost risk prediction) and Feature 2 (historical similarity) "
        "evidence into a transparent, deterministic priority score, category, and recommended "
        "attention level. Reuses PredictionService and SimilarityService directly in-process -- "
        "no internal HTTP calls, no additional ML model, no LLM."
    ),
)
def get_project_priority(request: Request, payload: PriorityRequest) -> PriorityResponse:
    prediction_svc: SavedPredictionService | None = getattr(request.app.state, "prediction_service", None)
    similarity_svc: SimilarityService | None = getattr(request.app.state, "similarity_service", None)
    priority_svc: PriorityService | None = getattr(request.app.state, "priority_service", None)

    if priority_svc is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Priority engine is unavailable")
    if prediction_svc is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cannot compute priority: prediction service is unavailable",
        )
    if similarity_svc is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cannot compute priority: similarity service is unavailable",
        )

    try:
        project_risk = prediction_svc.predict(payload)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Prediction failed: {exc}") from exc
    try:
        similarity = similarity_svc.find_similar(payload)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Similarity search failed: {exc}") from exc

    return priority_svc.assess(payload, project_risk, similarity)
