"""GIS REST API (Feature 6).

Exposes the Feature 4 boundary store and the Feature 5 spatial engine over HTTP:

    POST   /api/v1/gis/check-collision            RUN_GIS_ANALYSIS
    GET    /api/v1/gis/nearby-boundaries          VIEW_GIS
    GET    /api/v1/gis/boundaries                 VIEW_GIS
    GET    /api/v1/gis/boundaries/{boundary_id}   VIEW_GIS
    POST   /api/v1/gis/boundaries                 MANAGE_GIS_DATA
    DELETE /api/v1/gis/boundaries/{boundary_id}   MANAGE_GIS_DATA
    POST   /api/v1/gis/assessments                RUN_GIS_ANALYSIS
    GET    /api/v1/gis/assessments/{project_id}   VIEW_GIS

This layer is a thin translation between HTTP and the services already on `app.state`.
It calls `SpatialAnalysisService` and `GISBoundaryService` directly, in-process -- never
via internal HTTP requests -- exactly as intelligence.py and priority.py do for Features 1
and 2. No response value is fabricated here: every number comes from the engine or the
store.

Status codes
------------
    200/201  success
    401      missing or invalid bearer token
    403      authenticated but lacking the required permission
    404      unknown boundary id, or a project with no assessment history
    422      malformed input (coordinates, buffer, category, geometry)
    503      a required service or the auth registry is not configured
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Annotated, Any, Iterable

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from pydantic import ValidationError

from app.config import gis_config as gis_config
from app.config import spatial_config as spatial_config
from app.config.auth_config import Permission
from app.api.security import Principal, require_permission
from app.models.boundary_geometry import GeometryValidationError
from app.repositories.gis_boundary_repository import BoundaryNotFoundError, DuplicateBoundaryError
from app.schemas.assessment import AssessmentRecord, ProjectLocation
from app.schemas.boundary import BoundaryCategory, BoundaryQuery, GISBoundary
from app.schemas.gis_api import (
    AssessmentCreateRequest,
    AssessmentHistoryResponse,
    BoundaryCreateRequest,
    BoundaryDetailResponse,
    BoundaryListResponse,
    CollisionCheckRequest,
    CollisionCheckResponse,
    CollisionDetail,
    ErrorResponse,
    NearbyBoundariesResponse,
    NearbyBoundary,
)
from app.schemas.spatial import CollisionType, SpatialAnalysisResult
from app.services.assessment_service import AssessmentService, build_clearance_flags, to_intersecting_boundary
from app.services.gis_boundary_service import GISBoundaryService
from app.services.spatial_analysis_service import InvalidCoordinateError, SpatialAnalysisService

router = APIRouter(prefix="/gis", tags=["gis"])

COMMON_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing or invalid bearer token"},
    403: {"model": ErrorResponse, "description": "Insufficient permission"},
    422: {"model": ErrorResponse, "description": "Malformed input"},
    503: {"model": ErrorResponse, "description": "A required service is unavailable"},
}


#: Starlette renamed this constant; prefer the current name and fall back for older pins.
HTTP_422 = getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", None) or status.HTTP_422_UNPROCESSABLE_ENTITY


def validation_detail(exc: Exception, *, prefix: str) -> str:
    """Flatten a validation failure into one clear, specific sentence.

    Pydantic's ValidationError renders as a multi-line report whose FIRST line is only a
    count ("1 validation error for GISBoundary") -- the actual reason lives on later
    lines. Taking the first line therefore throws away exactly the information the caller
    needs, so each error's field path and message are extracted explicitly instead.
    """
    if isinstance(exc, ValidationError):
        parts = []
        for error in exc.errors():
            location = ".".join(str(item) for item in error.get("loc", ())) or "body"
            # Pydantic prefixes messages raised from custom validators; drop the noise.
            message = str(error.get("msg", "")).removeprefix("Value error, ")
            parts.append(f"{location}: {message}")
        return f"{prefix}: {'; '.join(parts)}"
    return f"{prefix}: {exc}"


# ---------------------------------------------------------------------------------
# Service accessors -- one place that turns "not configured" into a clear 503
# ---------------------------------------------------------------------------------


def get_boundary_service(request: Request) -> GISBoundaryService:
    service = getattr(request.app.state, "gis_boundary_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "GIS boundary store is unavailable: "
                f"{getattr(request.app.state, 'gis_error', None) or 'not initialized'}."
            ),
        )
    return service


def get_spatial_service(request: Request) -> SpatialAnalysisService:
    service = getattr(request.app.state, "spatial_analysis_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Spatial analysis engine is unavailable.",
        )
    return service


def get_assessment_service(request: Request) -> AssessmentService:
    service = getattr(request.app.state, "assessment_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Assessment store is unavailable.",
        )
    return service


# ---------------------------------------------------------------------------------
# GeoJSON assembly -- shared by every endpoint that returns map-ready output
# ---------------------------------------------------------------------------------


def _point_feature(latitude: float, longitude: float, project_id: str | None) -> dict[str, Any]:
    return {
        "type": "Feature",
        "id": "project-location",
        "geometry": {"type": "Point", "coordinates": [longitude, latitude]},
        "properties": {"role": "project_location", "project_id": project_id},
    }


def _buffer_feature(result: SpatialAnalysisResult) -> dict[str, Any] | None:
    if result.buffer_geometry is None:
        return None
    return {
        "type": "Feature",
        "id": "analysis-buffer",
        "geometry": result.buffer_geometry,
        "properties": {
            "role": "analysis_buffer",
            "buffer_meters": result.buffer_meters,
            "projected_crs": result.projected_crs,
        },
    }


def _boundary_feature(boundary: GISBoundary, extra_properties: dict[str, Any]) -> dict[str, Any]:
    """Reuse the store's own Feature rendering, then layer the analysis result on top."""
    feature = boundary.to_feature()
    feature["properties"] = {**feature["properties"], "role": "boundary", **extra_properties}
    return feature


def _feature_collection(features: Iterable[dict[str, Any] | None], *, contains_demo: bool) -> dict[str, Any]:
    collected = [feature for feature in features if feature is not None]
    payload: dict[str, Any] = {"type": "FeatureCollection", "features": collected}
    if contains_demo:
        # The disclaimer travels with the geometry, so a map cannot render fictional
        # boundaries without it being available.
        payload["notice"] = gis_config.DEMO_DATA_NOTICE
    return payload


def _lookup(boundary_service: GISBoundaryService, boundary_id: str) -> GISBoundary | None:
    """Fetch a boundary that the engine just reported, tolerating a concurrent delete."""
    try:
        return boundary_service.get_boundary(boundary_id)
    except BoundaryNotFoundError:
        return None


def _run_analysis(
    spatial_service: SpatialAnalysisService,
    *,
    latitude: float,
    longitude: float,
    buffer_meters: float,
    categories: list[BoundaryCategory] | None,
    include_demo: bool,
    proximity_meters: float | None = None,
) -> SpatialAnalysisResult:
    """Call the engine, translating its input errors into 422 with the engine's own message."""
    try:
        return spatial_service.analyze(
            latitude,
            longitude,
            buffer_meters,
            proximity_meters=proximity_meters,
            categories=[category.value for category in categories] if categories else None,
            include_demo=include_demo,
        )
    except InvalidCoordinateError as exc:
        raise HTTPException(status_code=HTTP_422, detail=str(exc)) from exc


# ---------------------------------------------------------------------------------
# API 1 -- collision check
# ---------------------------------------------------------------------------------


@router.post(
    "/check-collision",
    response_model=CollisionCheckResponse,
    status_code=status.HTTP_200_OK,
    responses=COMMON_ERRORS,
    summary="Check a project location against restricted boundaries",
    description=(
        "Runs the deterministic spatial engine for one coordinate pair and buffer radius. "
        "Every value returned is computed from the stored geometry -- the buffer is built in a "
        "projected metre CRS, candidates come from a spatial index, and intersections are exact. "
        "Includes a GeoJSON FeatureCollection (project point, tested buffer, colliding boundaries) "
        "ready for a map layer."
    ),
)
def check_collision(
    payload: CollisionCheckRequest,
    principal: Annotated[Principal, Depends(require_permission(Permission.RUN_GIS_ANALYSIS))],
    spatial_service: Annotated[SpatialAnalysisService, Depends(get_spatial_service)],
    boundary_service: Annotated[GISBoundaryService, Depends(get_boundary_service)],
) -> CollisionCheckResponse:
    result = _run_analysis(
        spatial_service,
        latitude=payload.latitude,
        longitude=payload.longitude,
        buffer_meters=payload.buffer_meters,
        categories=payload.categories,
        include_demo=payload.include_demo,
    )

    collisions: list[CollisionDetail] = []
    boundary_features: list[dict[str, Any]] = []
    collision_features: list[dict[str, Any]] = []
    for collision in result.collisions:
        boundary = _lookup(boundary_service, collision.boundary_id)
        flattened = to_intersecting_boundary(collision).model_dump()
        detail = CollisionDetail(
            **flattened,
            state=collision.state,
            district=collision.district,
            geometry=(boundary.geometry.as_mapping() if payload.include_geometry and boundary else None),
            intersection_geometry=collision.intersection_geometry,
        )
        collisions.append(detail)
        if boundary is not None:
            boundary_features.append(
                _boundary_feature(
                    boundary,
                    {
                        "collision_type": collision.collision_type.value,
                        "severity": collision.severity.value,
                        "distance_meters": collision.distance_meters,
                        "intersection_area_sqm": collision.intersection_area_sqm,
                        "buffer_overlap_percentage": collision.buffer_overlap_percentage,
                        "clearance_required": collision.clearance_required,
                    },
                )
            )
        # The overlap footprint is its own feature rather than a property of the boundary:
        # it is a different shape covering a different area, and a map has to be able to
        # draw and toggle it independently of the boundary that produced it.
        if collision.intersection_geometry is not None:
            collision_features.append(
                {
                    "type": "Feature",
                    "id": f"collision-{collision.boundary_id}",
                    "geometry": collision.intersection_geometry,
                    "properties": {
                        "role": "collision_area",
                        "boundary_id": collision.boundary_id,
                        "name": collision.boundary_name,
                        "category": collision.category.value,
                        "category_label": collision.category_label,
                        "collision_type": collision.collision_type.value,
                        "severity": collision.severity.value,
                        "intersection_area_sqm": collision.intersection_area_sqm,
                        "buffer_overlap_percentage": collision.buffer_overlap_percentage,
                    },
                }
            )

    return CollisionCheckResponse(
        project_id=payload.project_id,
        project_location=ProjectLocation(latitude=result.latitude, longitude=result.longitude),
        buffer_meters=result.buffer_meters,
        proximity_threshold_meters=result.proximity_threshold_meters,
        overall_status=result.collision_type,
        overall_severity=result.severity,
        clearance_required=result.clearance_required,
        boundaries_checked=result.boundaries_indexed,
        candidates_examined=result.candidates_examined,
        collision_count=result.collision_count,
        collisions=collisions,
        clearance_flags=build_clearance_flags(result),
        geojson=_feature_collection(
            [
                _point_feature(result.latitude, result.longitude, payload.project_id),
                _buffer_feature(result),
                *boundary_features,
                *collision_features,
            ],
            contains_demo=result.contains_demo_data,
        ),
        projected_crs=result.projected_crs,
        contains_demo_data=result.contains_demo_data,
        notice=result.notice,
        summary=result.summary(),
        analyzed_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------------
# API 2 -- nearby boundaries
# ---------------------------------------------------------------------------------


@router.get(
    "/nearby-boundaries",
    response_model=NearbyBoundariesResponse,
    responses=COMMON_ERRORS,
    summary="List boundaries within a radius of a location",
    description=(
        "Measured, not filtered by bounding box: distances are exact metre distances computed in "
        "a projected CRS. Runs the engine with a zero buffer and the requested radius as the "
        "proximity threshold, so a boundary containing the point is reported at distance 0."
    ),
)
def nearby_boundaries(
    principal: Annotated[Principal, Depends(require_permission(Permission.VIEW_GIS))],
    spatial_service: Annotated[SpatialAnalysisService, Depends(get_spatial_service)],
    boundary_service: Annotated[GISBoundaryService, Depends(get_boundary_service)],
    latitude: Annotated[float, Query(ge=-90, le=90, description="WGS84 latitude in decimal degrees.")],
    longitude: Annotated[float, Query(ge=-180, le=180, description="WGS84 longitude in decimal degrees.")],
    radius_meters: Annotated[
        float,
        Query(
            ge=spatial_config.MIN_BUFFER_METERS,
            le=spatial_config.MAX_BUFFER_METERS,
            description="Search radius in METRES.",
        ),
    ] = spatial_config.NEARBY_THRESHOLD_METERS,
    category: Annotated[
        list[BoundaryCategory] | None,
        Query(description="Optional. Repeat to filter on several categories."),
    ] = None,
    include_demo: Annotated[bool, Query(description="Include fictional demo boundaries.")] = True,
    include_geometry: Annotated[bool, Query(description="Attach each boundary's GeoJSON geometry.")] = False,
) -> NearbyBoundariesResponse:
    # A zero buffer with radius as the proximity threshold turns the engine into a pure
    # "what is within N metres" query, reusing the exact same measured geometry path.
    result = _run_analysis(
        spatial_service,
        latitude=latitude,
        longitude=longitude,
        buffer_meters=spatial_config.MIN_BUFFER_METERS,
        categories=category,
        include_demo=include_demo,
        proximity_meters=radius_meters,
    )

    boundaries: list[NearbyBoundary] = []
    features: list[dict[str, Any]] = []
    for collision in sorted(result.collisions, key=lambda c: (c.distance_meters, c.boundary_id)):
        boundary = _lookup(boundary_service, collision.boundary_id)
        boundaries.append(
            NearbyBoundary(
                boundary_id=collision.boundary_id,
                name=collision.boundary_name,
                category=collision.category,
                category_label=collision.category_label,
                state=collision.state,
                district=collision.district,
                distance_meters=collision.distance_meters,
                contains_point=collision.collision_type is CollisionType.DIRECT_COLLISION,
                severity=collision.severity,
                clearance_required=collision.clearance_required,
                is_demo=collision.is_demo,
                geometry=(boundary.geometry.as_mapping() if include_geometry and boundary else None),
            )
        )
        if boundary is not None:
            features.append(
                _boundary_feature(
                    boundary,
                    {"distance_meters": collision.distance_meters, "severity": collision.severity.value},
                )
            )

    return NearbyBoundariesResponse(
        project_location=ProjectLocation(latitude=result.latitude, longitude=result.longitude),
        radius_meters=radius_meters,
        categories=category,
        count=len(boundaries),
        boundaries=boundaries,
        geojson=_feature_collection(
            [_point_feature(result.latitude, result.longitude, None), *features],
            contains_demo=result.contains_demo_data,
        ),
        boundaries_checked=result.boundaries_indexed,
        contains_demo_data=result.contains_demo_data,
        notice=result.notice,
    )


# ---------------------------------------------------------------------------------
# API 3 -- boundary list
# ---------------------------------------------------------------------------------


@router.get(
    "/boundaries",
    response_model=BoundaryListResponse,
    responses=COMMON_ERRORS,
    summary="List and filter stored boundaries",
)
def list_boundaries(
    principal: Annotated[Principal, Depends(require_permission(Permission.VIEW_GIS))],
    boundary_service: Annotated[GISBoundaryService, Depends(get_boundary_service)],
    category: Annotated[list[BoundaryCategory] | None, Query(description="Repeat to filter on several.")] = None,
    state: Annotated[str | None, Query(max_length=120, description="Exact match, case-insensitive.")] = None,
    district: Annotated[str | None, Query(max_length=120, description="Exact match, case-insensitive.")] = None,
    search: Annotated[str | None, Query(max_length=300, description="Case-insensitive substring of the name.")] = None,
    include_demo: Annotated[bool, Query()] = True,
    include_geometry: Annotated[bool, Query(description="Also return a GeoJSON FeatureCollection.")] = False,
    limit: Annotated[int | None, Query(ge=1, le=10_000)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> BoundaryListResponse:
    filters = BoundaryQuery(
        categories=category,
        state=state,
        district=district,
        name_contains=search,
        include_demo=include_demo,
        limit=limit,
        offset=offset,
    )
    boundaries = boundary_service.find(filters)
    # Count without the window, so `total` reflects the filters rather than the page.
    total = boundary_service.count(filters.model_copy(update={"limit": None, "offset": 0}))
    contains_demo = any(boundary.is_demo for boundary in boundaries)

    return BoundaryListResponse(
        count=len(boundaries),
        total=total,
        limit=limit,
        offset=offset,
        filters={
            "category": [c.value for c in category] if category else None,
            "state": state,
            "district": district,
            "search": search,
            "include_demo": include_demo,
        },
        boundaries=[boundary.to_summary() for boundary in boundaries],
        geojson=(
            _feature_collection(
                [_boundary_feature(boundary, {}) for boundary in boundaries], contains_demo=contains_demo
            )
            if include_geometry
            else None
        ),
        contains_demo_data=contains_demo,
        notice=gis_config.DEMO_DATA_NOTICE if contains_demo else None,
    )


# ---------------------------------------------------------------------------------
# API 4 -- boundary detail
# ---------------------------------------------------------------------------------


@router.get(
    "/boundaries/{boundary_id}",
    response_model=BoundaryDetailResponse,
    responses={**COMMON_ERRORS, 404: {"model": ErrorResponse, "description": "No boundary with that id"}},
    summary="Fetch one boundary with its full geometry",
)
def get_boundary(
    principal: Annotated[Principal, Depends(require_permission(Permission.VIEW_GIS))],
    boundary_service: Annotated[GISBoundaryService, Depends(get_boundary_service)],
    boundary_id: Annotated[str, Path(min_length=1, max_length=128)],
) -> BoundaryDetailResponse:
    try:
        boundary = boundary_service.get_boundary(boundary_id)
    except BoundaryNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No boundary with id {boundary_id!r}."
        ) from exc

    return BoundaryDetailResponse(
        boundary=boundary.to_summary(),
        geometry=boundary.geometry.as_mapping(),
        feature=boundary.to_feature(),
        metadata=boundary.metadata,
        is_demo=boundary.is_demo,
        notice=gis_config.DEMO_DATA_NOTICE if boundary.is_demo else None,
    )


# ---------------------------------------------------------------------------------
# Boundary mutation -- MANAGE_GIS_DATA
# ---------------------------------------------------------------------------------


@router.post(
    "/boundaries",
    response_model=BoundaryDetailResponse,
    status_code=status.HTTP_201_CREATED,
    responses={**COMMON_ERRORS, 409: {"model": ErrorResponse, "description": "A boundary with that id exists"}},
    summary="Create a boundary (requires MANAGE_GIS_DATA)",
    description=(
        "Geometry is validated before storage and rejected -- never repaired -- if invalid. "
        "Records default to demo data; `is_demo=false` is only for a genuinely official or "
        "licensed dataset."
    ),
)
def create_boundary(
    payload: BoundaryCreateRequest,
    principal: Annotated[Principal, Depends(require_permission(Permission.MANAGE_GIS_DATA))],
    boundary_service: Annotated[GISBoundaryService, Depends(get_boundary_service)],
) -> BoundaryDetailResponse:
    try:
        last_updated = date.fromisoformat(payload.last_updated)
    except ValueError as exc:
        raise HTTPException(
            status_code=HTTP_422,
            detail=f"last_updated must be an ISO date (YYYY-MM-DD), got {payload.last_updated!r}.",
        ) from exc

    metadata = {**payload.metadata, "created_by": principal.id}
    try:
        boundary = boundary_service.create_boundary(
            name=payload.name,
            category=payload.category,
            state=payload.state,
            district=payload.district,
            geometry=payload.geometry,
            source=payload.source,
            source_url=payload.source_url,
            last_updated=last_updated,
            metadata=metadata,
            is_demo=payload.is_demo,
            overwrite=payload.overwrite,
        )
    except DuplicateBoundaryError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (GeometryValidationError, ValidationError, ValueError) as exc:
        raise HTTPException(
            status_code=HTTP_422, detail=validation_detail(exc, prefix="Boundary rejected")
        ) from exc

    return BoundaryDetailResponse(
        boundary=boundary.to_summary(),
        geometry=boundary.geometry.as_mapping(),
        feature=boundary.to_feature(),
        metadata=boundary.metadata,
        is_demo=boundary.is_demo,
        notice=gis_config.DEMO_DATA_NOTICE if boundary.is_demo else None,
    )


@router.delete(
    "/boundaries/{boundary_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**COMMON_ERRORS, 404: {"model": ErrorResponse, "description": "No boundary with that id"}},
    summary="Delete a boundary (requires MANAGE_GIS_DATA)",
)
def delete_boundary(
    principal: Annotated[Principal, Depends(require_permission(Permission.MANAGE_GIS_DATA))],
    boundary_service: Annotated[GISBoundaryService, Depends(get_boundary_service)],
    boundary_id: Annotated[str, Path(min_length=1, max_length=128)],
) -> None:
    try:
        boundary_service.delete_boundary(boundary_id)
    except BoundaryNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No boundary with id {boundary_id!r}."
        ) from exc


# ---------------------------------------------------------------------------------
# API 5 -- assessments
# ---------------------------------------------------------------------------------


@router.post(
    "/assessments",
    response_model=AssessmentRecord,
    status_code=status.HTTP_201_CREATED,
    responses=COMMON_ERRORS,
    summary="Run an analysis and store it as a project assessment",
    description=(
        "Runs the spatial engine and records the outcome against a project: coordinates, buffer, "
        "timestamp, the authenticated user, the full engine result, intersecting boundaries, "
        "severity, and clearance flags. The stored result is a snapshot -- a later boundary "
        "dataset change never rewrites what a past assessment concluded."
    ),
)
def create_assessment(
    payload: AssessmentCreateRequest,
    principal: Annotated[Principal, Depends(require_permission(Permission.RUN_GIS_ANALYSIS))],
    spatial_service: Annotated[SpatialAnalysisService, Depends(get_spatial_service)],
    assessment_service: Annotated[AssessmentService, Depends(get_assessment_service)],
) -> AssessmentRecord:
    result = _run_analysis(
        spatial_service,
        latitude=payload.latitude,
        longitude=payload.longitude,
        buffer_meters=payload.buffer_meters,
        categories=payload.categories,
        include_demo=payload.include_demo,
    )
    return assessment_service.record(
        project_id=payload.project_id,
        result=result,
        principal_id=principal.id,
        principal_name=principal.display_name,
        notes=payload.notes,
    )


@router.get(
    "/assessments/{project_id}",
    response_model=AssessmentHistoryResponse,
    responses={**COMMON_ERRORS, 404: {"model": ErrorResponse, "description": "The project has no assessments"}},
    summary="Fetch a project's assessment history, newest first",
)
def get_assessment_history(
    principal: Annotated[Principal, Depends(require_permission(Permission.VIEW_GIS))],
    assessment_service: Annotated[AssessmentService, Depends(get_assessment_service)],
    project_id: Annotated[str, Path(min_length=1, max_length=128)],
    limit: Annotated[int | None, Query(ge=1, le=1_000)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AssessmentHistoryResponse:
    total = assessment_service.count(project_id)
    if total == 0:
        # 404 rather than an empty 200: the caller named a specific project, and "no such
        # project history" is a different situation from "this page happens to be empty".
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No assessments recorded for project {project_id!r}.",
        )

    records = assessment_service.history(project_id, limit=limit, offset=offset)
    return AssessmentHistoryResponse(
        project_id=project_id,
        count=len(records),
        total=total,
        limit=limit,
        offset=offset,
        assessments=records,
    )
