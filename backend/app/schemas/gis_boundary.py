from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, field_validator


class BoundaryCategory(str, Enum):
    WILDLIFE_SANCTUARY = "WILDLIFE_SANCTUARY"
    NATIONAL_PARK = "NATIONAL_PARK"
    FOREST = "FOREST"
    ECO_SENSITIVE_ZONE = "ECO_SENSITIVE_ZONE"
    TIGER_RESERVE = "TIGER_RESERVE"
    RAMSAR_WETLAND = "RAMSAR_WETLAND"
    OTHER_RESTRICTED_ZONE = "OTHER_RESTRICTED_ZONE"

    @classmethod
    def parse_category(cls, value: str) -> BoundaryCategory:
        """Flexible parser mapping user/raw category strings to standard Enum values."""
        normalized = value.strip().upper().replace(" ", "_").replace("-", "_")
        for member in cls:
            if member.value == normalized:
                return member
        # Mapping aliases
        if "TIGER" in normalized or "RESERVE" in normalized and "TIGER" in normalized:
            return cls.TIGER_RESERVE
        if "PARK" in normalized:
            return cls.NATIONAL_PARK
        if "WETLAND" in normalized or "RAMSAR" in normalized:
            return cls.RAMSAR_WETLAND
        if "SANCTUARY" in normalized or "WILDLIFE" in normalized:
            return cls.WILDLIFE_SANCTUARY
        if "ECO" in normalized or "ESZ" in normalized:
            return cls.ECO_SENSITIVE_ZONE
        if "FOREST" in normalized:
            return cls.FOREST
        return cls.OTHER_RESTRICTED_ZONE


class GISBoundary(BaseModel):
    """Domain schema for environmental & restricted geographic boundaries."""
    model_config = ConfigDict(extra="allow")

    id: str = Field(description="Unique boundary identifier (e.g. DEMO-PA-001)")
    name: str = Field(min_length=1, max_length=255, description="Boundary name")
    category: BoundaryCategory = Field(description="Boundary category")
    state: str = Field(min_length=1, max_length=100, description="Indian state name")
    district: str = Field(default="Unspecified", max_length=100, description="District name")
    geometry: dict[str, Any] = Field(description="GeoJSON geometry object (Polygon or MultiPolygon)")
    source: str = Field(default="DEMO DATA — NOT OFFICIAL BOUNDARIES", description="Source attribution")
    source_url: str | None = Field(default=None, description="Source reference URL")
    last_updated: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO timestamp of last update",
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional properties and metadata")
    is_demo: bool = Field(default=True, description="Flag indicating if dataset is demo/test data")

    @field_validator("geometry")
    @classmethod
    def validate_geometry_dict(cls, val: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(val, dict) or "type" not in val or "coordinates" not in val:
            raise ValueError("Invalid GeoJSON geometry structure: must contain 'type' and 'coordinates'")
        if val["type"] not in ["Polygon", "MultiPolygon"]:
            raise ValueError(f"Unsupported geometry type '{val['type']}'. Only Polygon and MultiPolygon are supported.")
        return val


class GISBoundaryImportResult(BaseModel):
    """Summary statistics for data import operations."""
    file_path: str
    total_records: int
    imported_count: int
    rejected_count: int
    repaired_count: int
    source_attribution: str
    errors: list[str] = Field(default_factory=list)
