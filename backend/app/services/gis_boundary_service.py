"""Service layer for the GIS boundary data layer (Feature 4).

Sits between callers (seed scripts, importers, and -- later -- API routes) and whichever
`GISBoundaryRepository` is configured. Its job is the policy the storage backends must
not own:

  * every write goes through full geometry validation (`GISBoundary` construction), so
    invalid geometry cannot reach any backend;
  * generated records are marked as demo data and any collection containing one carries
    the DEMO DATA notice, so fabricated geometry can never be served as an official
    government boundary;
  * ids are deterministic, so re-running an import updates records instead of
    duplicating them.

Deliberately excluded: spatial collision/intersection analysis between project sites and
boundaries. That is the next feature and will consume this service, not live inside it.
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Sequence

from app.config import gis_config as config
from app.models import boundary_geometry as geo
from app.repositories.gis_boundary_repository import (
    BoundaryNotFoundError,
    GISBoundaryRepository,
    build_repository,
)
from app.schemas.boundary import (
    DEMO_METADATA_KEY,
    NOTICE_METADATA_KEY,
    BoundaryCategory,
    BoundaryFeatureCollection,
    BoundaryQuery,
    BoundarySummary,
    GISBoundary,
)

_ID_NAMESPACE = uuid.UUID(config.BOUNDARY_ID_NAMESPACE)


def make_boundary_id(source: str, name: str, category: BoundaryCategory | str) -> str:
    """Derive a stable id from (source, name, category).

    UUIDv5 rather than a counter or uuid4: importing the same source file twice must
    produce the same ids, so a re-import is an idempotent update rather than a silent
    duplication of every record.
    """
    category_value = category.value if isinstance(category, BoundaryCategory) else str(category)
    key = f"{source.strip().casefold()}|{name.strip().casefold()}|{category_value}"
    return str(uuid.uuid5(_ID_NAMESPACE, key))


def mark_as_demo(metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Stamp a metadata dict as generated/test data carrying the mandatory notice."""
    stamped = dict(metadata or {})
    stamped[DEMO_METADATA_KEY] = True
    stamped[NOTICE_METADATA_KEY] = config.DEMO_DATA_NOTICE
    return stamped


class GISBoundaryService:
    """Read/write access to environmental and restricted-area boundaries."""

    def __init__(self, repository: GISBoundaryRepository) -> None:
        self.repository = repository

    # --- reads ----------------------------------------------------------------------

    def get_boundary(self, boundary_id: str) -> GISBoundary:
        """Fetch one boundary. Raises BoundaryNotFoundError if it does not exist."""
        return self.repository.get(boundary_id)

    def find(self, query: BoundaryQuery | None = None) -> list[GISBoundary]:
        """Fetch matching boundary records."""
        return self.repository.find(query)

    def list_summaries(self, query: BoundaryQuery | None = None) -> list[BoundarySummary]:
        """Fetch matching boundaries without their geometry -- cheap for listings."""
        return [boundary.to_summary() for boundary in self.repository.find(query)]

    def feature_collection(self, query: BoundaryQuery | None = None) -> BoundaryFeatureCollection:
        """Fetch matches as a GeoJSON FeatureCollection ready for a map layer.

        When any included record is demo data, `notice` is set. A consumer therefore
        always receives the disclaimer alongside the geometry -- it is not something the
        frontend has to remember to add.
        """
        boundaries = self.repository.find(query)
        contains_demo = any(boundary.is_demo for boundary in boundaries)
        return BoundaryFeatureCollection(
            features=[boundary.to_feature() for boundary in boundaries],
            count=len(boundaries),
            contains_demo_data=contains_demo,
            notice=config.DEMO_DATA_NOTICE if contains_demo else None,
        )

    def count(self, query: BoundaryQuery | None = None) -> int:
        return self.repository.count(query)

    def stats(self) -> dict[str, Any]:
        """Backend, totals, and per-category/per-state counts -- for status output."""
        boundaries = self.repository.find()
        by_category: dict[str, int] = {}
        by_state: dict[str, int] = {}
        for boundary in boundaries:
            by_category[boundary.category.value] = by_category.get(boundary.category.value, 0) + 1
            by_state[boundary.state] = by_state.get(boundary.state, 0) + 1
        demo_count = sum(1 for boundary in boundaries if boundary.is_demo)
        return {
            "backend": self.repository.backend,
            "storage_crs": config.STORAGE_CRS,
            "total": len(boundaries),
            "demo_records": demo_count,
            "official_records": len(boundaries) - demo_count,
            "notice": config.DEMO_DATA_NOTICE if demo_count else None,
            "by_category": dict(sorted(by_category.items())),
            "by_state": dict(sorted(by_state.items())),
        }

    # --- writes ---------------------------------------------------------------------

    def create_boundary(
        self,
        *,
        name: str,
        category: BoundaryCategory | str,
        state: str,
        geometry: dict[str, Any],
        source: str,
        last_updated: date,
        district: str | None = None,
        source_url: str | None = None,
        metadata: dict[str, Any] | None = None,
        boundary_id: str | None = None,
        is_demo: bool = False,
        overwrite: bool = False,
    ) -> GISBoundary:
        """Validate and persist one boundary.

        Geometry is validated by `GISBoundary`'s own validator; anything invalid raises
        before the repository is touched, so a rejected record never reaches storage.
        """
        category = BoundaryCategory(category)
        boundary = GISBoundary(
            id=boundary_id or make_boundary_id(source, name, category),
            name=name,
            category=category,
            state=state,
            district=district,
            geometry=geometry,
            source=source,
            source_url=source_url,
            last_updated=last_updated,
            metadata=mark_as_demo(metadata) if is_demo else dict(metadata or {}),
        )
        return self.repository.add(boundary, overwrite=overwrite)

    def add_boundaries(self, boundaries: Iterable[GISBoundary], *, overwrite: bool = False) -> int:
        """Persist already-validated boundaries in one write."""
        return self.repository.add_many(boundaries, overwrite=overwrite)

    def delete_boundary(self, boundary_id: str) -> None:
        self.repository.delete(boundary_id)

    def clear(self) -> int:
        """Remove every boundary. Used by the seed script's --reset flag."""
        return self.repository.clear()

    # --- geometry helpers -----------------------------------------------------------

    @staticmethod
    def validate_geometry(geometry: dict[str, Any]) -> dict[str, Any]:
        """Validate arbitrary geometry without storing it (raises GeometryValidationError)."""
        return geo.validate_geometry(geometry)

    @staticmethod
    def bbox_of(geometry: dict[str, Any]) -> Sequence[float]:
        return geo.geometry_bbox(geometry)


def build_gis_boundary_service(
    *, dsn: str | None = None, path: Path | str | None = None
) -> GISBoundaryService:
    """Construct the service with whichever backend this environment supports.

    Mirrors `build_prediction_service` / `build_priority_service`: one factory, called
    once at startup, so nothing constructs storage per request.
    """
    return GISBoundaryService(build_repository(dsn=dsn, path=path))


__all__ = [
    "BoundaryNotFoundError",
    "GISBoundaryService",
    "build_gis_boundary_service",
    "make_boundary_id",
    "mark_as_demo",
]
