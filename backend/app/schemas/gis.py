from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class GISBufferRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str | None = Field(default=None, min_length=1, max_length=100)
    latitude: float = Field(ge=6.0, le=37.5, description="Latitude in WGS84 decimal degrees (India range: ~6.0 N to 37.5 N)")
    longitude: float = Field(ge=68.0, le=97.5, description="Longitude in WGS84 decimal degrees (India range: ~68.0 E to 97.5 E)")
    buffer_distance_km: float = Field(default=5.0, ge=0.1, le=50.0, description="Buffer radius in kilometers (0.1 km to 50.0 km)")
    zone_categories: list[str] | None = Field(
        default=None,
        description="Filter boundary categories: Wildlife Sanctuary, National Park, Tiger Reserve, Ramsar Wetland, Eco-Sensitive Zone, Reserved Forest",
    )


class ZoneCollision(BaseModel):
    zone_id: str
    zone_name: str
    zone_category: str
    state: str
    designation: str
    clearance_type_required: str
    distance_to_boundary_km: float
    is_direct_intersection: bool
    intersection_area_sq_km: float
    severity: str  # CRITICAL, HIGH, WARNING


class GISCollisionResponse(BaseModel):
    has_collision: bool
    total_collisions: int
    highest_severity: str  # NONE, WARNING, HIGH, CRITICAL
    clearance_required: bool
    buffer_distance_km: float
    project_coordinates: dict[str, float]
    collisions: list[ZoneCollision]
    geojson_layers: dict[str, Any]
    summary: str
