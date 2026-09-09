from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.schemas.similarity import SimilarityRequest, SimilarityResponse
from app.services.similarity_service import SimilarityService

router = APIRouter(tags=["similarity"])


@router.post(
    "/similar-projects",
    response_model=SimilarityResponse,
    status_code=status.HTTP_200_OK,
    summary="Find historically similar infrastructure projects",
    description=(
        "Given a current project's characteristics, finds the top_k most similar historical "
        "projects (scikit-learn NearestNeighbors over a shared preprocessing pipeline) and "
        "returns their real outcomes plus deterministic aggregated evidence."
    ),
)
def find_similar_projects(
    request: Request,
    payload: SimilarityRequest,
    top_k: int = Query(default=5, ge=1, le=10, description="Number of similar historical projects to return (1-10)."),
) -> SimilarityResponse:
    service: SimilarityService | None = getattr(request.app.state, "similarity_service", None)
    if service is None:
        detail = getattr(request.app.state, "similarity_error", None) or "Similarity model is unavailable"
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)
    try:
        return service.find_similar(payload, top_k=top_k)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Similarity service failed") from exc
