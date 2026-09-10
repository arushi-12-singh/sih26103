from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from app.schemas.intelligence import (
    IntelligenceHistoricalEvidence,
    IntelligenceSimilarProject,
    ProjectIntelligenceResponse,
)
from app.schemas.intelligence import ProjectIntelligenceRequest
from app.services.gis_intelligence_service import build_gis_signal
from app.services.intervention_service import InterventionService
from app.services.prediction_service import SavedPredictionService
from app.schemas.priority import PriorityRequest
from app.services.priority_service import PriorityService
from app.services.similarity_service import SimilarityService
from app.services.spatial_analysis_service import InvalidCoordinateError, SpatialAnalysisService

router = APIRouter(tags=["intelligence"])


@router.post(
    "/project-intelligence",
    response_model=ProjectIntelligenceResponse,
    status_code=status.HTTP_200_OK,
    summary="Combined delay-risk prediction and historical similarity",
    description=(
        "Runs the full intelligence pipeline for one project in a single call: XGBoost risk "
        "prediction, SHAP explanation, historical similarity, GIS boundary screening (when "
        "coordinates are supplied), the priority engine, and rule-based intervention "
        "recommendations. Every stage reuses its service directly in-process -- no internal "
        "HTTP requests. Coordinates are optional; without them the response is exactly what it "
        "was before the GIS module existed, plus priority and interventions."
    ),
)
def project_intelligence(request: Request, payload: ProjectIntelligenceRequest) -> ProjectIntelligenceResponse:
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

    # --- GIS boundary screening (only when the caller supplied a location) ---
    # Optional stage: a project with no surveyed coordinates still gets the full
    # non-spatial pipeline rather than an error.
    gis_signal = None
    if payload.has_location:
        spatial_svc: SpatialAnalysisService | None = getattr(request.app.state, "spatial_analysis_service", None)
        if spatial_svc is None:
            raise HTTPException(
                status_code=503,
                detail="Coordinates were supplied but the GIS spatial engine is unavailable.",
            )
        try:
            gis_signal = build_gis_signal(
                spatial_svc.analyze(payload.latitude, payload.longitude, payload.buffer_meters)
            )
        except InvalidCoordinateError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    # --- Build flat response ---
    similar_projects = _build_similar_projects(similarity_result)
    historical_evidence = _build_evidence(similarity_result)
    historical_summary = _build_historical_summary(historical_evidence)

    # --- Priority, then interventions ---
    # Both read already-computed evidence; neither re-runs a model. The GIS signal is
    # passed through as structured features only -- no geometry crosses this boundary.
    priority_result = None
    interventions = []
    priority_svc: PriorityService | None = getattr(request.app.state, "priority_service", None)
    if priority_svc is not None:
        priority_result = priority_svc.assess(
            PriorityRequest.model_validate(payload.model_dump()),
            prediction_result,
            similarity_result,
            gis_signal=gis_signal,
        )
        intervention_svc: InterventionService | None = getattr(request.app.state, "intervention_service", None)
        if intervention_svc is not None:
            interventions = intervention_svc.recommend(
                payload,
                similarity_result,
                priority_result,
                prediction_result.project_risk.delay_probability,
                gis_signal=gis_signal,
            )

    return ProjectIntelligenceResponse(
        project_risk=prediction_result.project_risk,
        top_risk_factors=prediction_result.top_risk_factors,
        risk_summary=prediction_result.summary,
        similar_projects=similar_projects,
        historical_evidence=historical_evidence,
        historical_summary=historical_summary,
        gis_screening=gis_signal,
        priority=priority_result,
        interventions=interventions,
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
