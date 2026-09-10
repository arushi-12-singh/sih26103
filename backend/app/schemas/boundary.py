"""Pydantic schemas for the GIS boundary data layer (Feature 4).

`GISBoundary` is the canonical record shape. Every storage backend -- the GeoJSON file
repository today, PostGIS tomorrow -- reads and writes exactly this model, so swapping
backends changes no caller code. Geometry is validated on construction by
app.models.boundary_geometry, which means an invalid boundary cannot be instantiated at
all, let alone persisted.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.config import gis_config as config
from app.models import boundary_geometry as geo

# Key used inside `metadata` to mark generated records. It lives in metadata rather than
# as a top-level column so the record shape stays exactly the ten specified fields.
DEMO_METADATA_KEY = "is_demo"
NOTICE_METADATA_KEY = "data_notice"


class BoundaryCategory(str, Enum):
    """The kinds of restricted/environmental area this system recognises.

    Single source of truth: gis_config.CATEGORY_LABELS is asserted to cover every member
    by tests/test_gis_boundaries.py, and the PostGIS enum in
    migrations/0001_create_gis_boundaries.sql mirrors it.
    """

    WILDLIFE_SANCTUARY = "WILDLIFE_SANCTUARY"
    NATIONAL_PARK = "NATIONAL_PARK"
    FOREST = "FOREST"
    ECO_SENSITIVE_ZONE = "ECO_SENSITIVE_ZONE"
    TIGER_RESERVE = "TIGER_RESERVE"
    RAMSAR_WETLAND = "RAMSAR_WETLAND"
    OTHER_RESTRICTED_ZONE = "OTHER_RESTRICTED_ZONE"

    @property
    def label(self) -> str:
        return config.CATEGORY_LABELS[self.value]


class BoundaryGeometry(BaseModel):
    """A GeoJSON Polygon or MultiPolygon in EPSG:4326, validated on construction."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["Polygon", "MultiPolygon"]
    coordinates: list[Any]

    @model_validator(mode="after")
    def _validate(self) -> "BoundaryGeometry":
        # validate_geometry raises GeometryValidationError (a ValueError), which Pydantic
        # surfaces as a normal validation error -- so invalid geometry fails the same way
        # a bad field type does, at every entry point.
        normalized = geo.validate_geometry({"type": self.type, "coordinates": self.coordinates})
        object.__setattr__(self, "coordinates", normalized["coordinates"])
        return self

    def as_mapping(self) -> dict[str, Any]:
        """The plain GeoJSON dict form the geometry helpers and repositories work on."""
        return {"type": self.type, "coordinates": self.coordinates}

    @property
    def bbox(self) -> geo.BBox:
        return geo.geometry_bbox(self.as_mapping())

    @property
    def area_sqkm(self) -> float:
        return geo.geometry_area_sqkm(self.as_mapping())


class GISBoundary(BaseModel):
    """One environmental or otherwise restricted geographic boundary.

    `district` is nullable because real protected areas routinely span several districts
    (or are published without one); it is always present as a key.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=300)
    category: BoundaryCategory
    state: str = Field(min_length=1, max_length=120)
    district: str | None = Field(default=None, max_length=120)
    geometry: BoundaryGeometry
    source: str = Field(min_length=1, max_length=300, description="Publisher or dataset the geometry came from.")
    source_url: str | None = Field(default=None, max_length=1000)
    last_updated: date = Field(description="Date the SOURCE dataset was last updated, not the row's write time.")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name", "state", "source", mode="before")
    @classmethod
    def _strip_required_text(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("district", "source_url", mode="before")
    @classmethod
    def _strip_optional_text(cls, value: Any) -> Any:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @property
    def is_demo(self) -> bool:
        """True for generated/test geometry that must never be shown as official."""
        return bool(self.metadata.get(DEMO_METADATA_KEY, False))

    @property
    def area_sqkm(self) -> float:
        return self.geometry.area_sqkm

    def to_feature(self) -> dict[str, Any]:
        """Render as a GeoJSON Feature, with the demo notice inlined when applicable."""
        properties: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "category": self.category.value,
            "category_label": self.category.label,
            "state": self.state,
            "district": self.district,
            "source": self.source,
            "source_url": self.source_url,
            "last_updated": self.last_updated.isoformat(),
            "area_sqkm": round(self.area_sqkm, 4),
            "is_demo": self.is_demo,
            "metadata": self.metadata,
        }
        if self.is_demo:
            properties["data_notice"] = config.DEMO_DATA_NOTICE
        return {
            "type": "Feature",
            "id": self.id,
            "geometry": self.geometry.as_mapping(),
            "properties": properties,
        }

    def to_summary(self) -> "BoundarySummary":
        """The lightweight, geometry-free view used for listings."""
        return BoundarySummary(
            id=self.id,
            name=self.name,
            category=self.category,
            state=self.state,
            district=self.district,
            source=self.source,
            source_url=self.source_url,
            last_updated=self.last_updated,
            area_sqkm=round(self.area_sqkm, 4),
            bbox=list(self.geometry.bbox),
            is_demo=self.is_demo,
        )


class BoundarySummary(BaseModel):
    """A boundary without its geometry -- for lists, counts, and admin views."""

    id: str
    name: str
    category: BoundaryCategory
    state: str
    district: str | None
    source: str
    source_url: str | None
    last_updated: date
    area_sqkm: float
    bbox: list[float] = Field(min_length=4, max_length=4)
    is_demo: bool


class BoundaryQuery(BaseModel):
    """Filters a repository can answer.

    Deliberately expressible in SQL as well as in Python: every field maps to a WHERE
    clause the PostGIS backend builds directly (see `PostGISBoundaryRepository`), so the
    two backends support an identical query surface.
    """

    model_config = ConfigDict(extra="forbid")

    categories: list[BoundaryCategory] | None = None
    state: str | None = None
    district: str | None = None
    name_contains: str | None = None
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    include_demo: bool = True
    limit: int | None = Field(default=None, ge=1, le=10_000)
    offset: int = Field(default=0, ge=0)

    @field_validator("bbox")
    @classmethod
    def _validate_bbox(cls, value: list[float] | None) -> list[float] | None:
        return None if value is None else list(geo.normalize_bbox(value))


class BoundaryFeatureCollection(BaseModel):
    """A GeoJSON FeatureCollection plus provenance the map layer can display.

    `notice` is populated whenever the collection contains at least one demo record, so
    a consumer cannot render this data without the disclaimer being available to it.
    """

    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[dict[str, Any]]
    count: int = Field(ge=0)
    contains_demo_data: bool
    notice: str | None = None


class RejectedFeature(BaseModel):
    """One feature an import refused, and why."""

    index: int
    name: str | None = None
    reason: str


class ImportReport(BaseModel):
    """The outcome of one import run -- never partial-silent, always accounted for."""

    source_path: str
    driver: str
    source_crs: str | None
    transformed: bool
    features_read: int = Field(ge=0)
    imported: int = Field(ge=0)
    skipped_duplicates: int = Field(ge=0)
    rejected: list[RejectedFeature] = Field(default_factory=list)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)

    def summary(self) -> str:
        return (
            f"{self.source_path} [{self.driver}] crs={self.source_crs or 'unknown'}"
            f"{' -> ' + config.STORAGE_CRS if self.transformed else ''}: "
            f"read {self.features_read}, imported {self.imported}, "
            f"duplicates {self.skipped_duplicates}, rejected {self.rejected_count}"
        )
