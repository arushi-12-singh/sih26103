from __future__ import annotations

from typing import Any
from fastapi import APIRouter, HTTPException, Query, Request, status

from app.schemas.gis import GISBufferRequest, GISCollisionResponse
from app.services.gis_service import GISService

router = APIRouter(prefix="/gis", tags=["GIS & Environmental Boundaries"])


def get_gis_service(request: Request) -> GISService:
    service: GISService | None = getattr(request.app.state, "gis_service", None)
    if service is None:
        error = getattr(request.app.state, "gis_error", "GIS service unavailable")
        raise HTTPException(
            status_code=status.HTTP_533_SERVICE_UNAVAILABLE,
            detail=f"GIS service is not loaded: {error}",
        )
    return service


@router.post(
    "/check-collision",
    response_model=GISCollisionResponse,
    summary="Check GIS buffer collision against environmental boundaries",
    description="Calculates a metric buffer around project coordinates and tests for intersections with Indian protected areas.",
)
def check_collision(payload: GISBufferRequest, request: Request) -> GISCollisionResponse:
    service = get_gis_service(request)
    try:
        return service.check_buffer_collision(payload)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Spatial calculation error: {exc}",
        ) from exc


@router.get(
    "/protected-zones",
    summary="Get all protected environmental zones (GeoJSON)",
    description="Returns GeoJSON FeatureCollection of protected boundaries for map layer overlays.",
)
def get_protected_zones(
    request: Request,
    category: list[str] | None = Query(default=None, description="Optional category filters"),
) -> dict[str, Any]:
    service = get_gis_service(request)
    return service.get_all_protected_zones_geojson(category_filter=category)
