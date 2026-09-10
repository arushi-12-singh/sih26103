from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import shapely.geometry as sg
from shapely.strtree import STRtree

from app.schemas.gis_boundary import BoundaryCategory, GISBoundary


class GISBoundaryRepository:
    """In-memory spatial repository for GISBoundary records with Shapely STRtree indexing and JSON persistence.

    Designed following the Repository pattern to enable seamless future migration to PostGIS / SQLAlchemy.
    """

    def __init__(self, persistence_file: Path | None = None) -> None:
        self._boundaries: dict[str, GISBoundary] = {}
        self._shapely_geoms: list[sg.base.BaseGeometry] = []
        self._id_index_map: list[str] = []
        self._tree: STRtree | None = None
        self.persistence_file = persistence_file

    def _rebuild_spatial_index(self) -> None:
        """Rebuild the Shapely STRtree index over boundary geometries."""
        self._shapely_geoms = []
        self._id_index_map = []
        for boundary_id, boundary in self._boundaries.items():
            try:
                geom = sg.shape(boundary.geometry)
                self._shapely_geoms.append(geom)
                self._id_index_map.append(boundary_id)
            except Exception:
                continue

        if self._shapely_geoms:
            self._tree = STRtree(self._shapely_geoms)
        else:
            self._tree = None

    def add(self, boundary: GISBoundary) -> None:
        self._boundaries[boundary.id] = boundary
        self._rebuild_spatial_index()

    def add_many(self, boundaries: list[GISBoundary]) -> int:
        added_count = 0
        for b in boundaries:
            self._boundaries[b.id] = b
            added_count += 1
        self._rebuild_spatial_index()
        return added_count

    def get_by_id(self, boundary_id: str) -> GISBoundary | None:
        return self._boundaries.get(boundary_id)

    def get_all(self) -> list[GISBoundary]:
        return list(self._boundaries.values())

    def filter_by_category(self, category: BoundaryCategory | str) -> list[GISBoundary]:
        target_cat = category.value if isinstance(category, BoundaryCategory) else str(category).upper()
        return [b for b in self._boundaries.values() if b.category.value == target_cat]

    def filter_by_state(self, state: str) -> list[GISBoundary]:
        target_state = state.strip().lower()
        return [b for b in self._boundaries.values() if b.state.strip().lower() == target_state]

    def query_spatial_candidates(self, query_geometry: sg.base.BaseGeometry) -> list[GISBoundary]:
        """Query spatial R-tree index for candidate intersecting boundaries."""
        if not self._tree or not self._id_index_map:
            return []

        candidate_indices = self._tree.query(query_geometry)
        results: list[GISBoundary] = []
        for idx in candidate_indices:
            boundary_id = self._id_index_map[idx]
            boundary = self._boundaries.get(boundary_id)
            if boundary:
                results.append(boundary)
        return results

    def count(self) -> int:
        return len(self._boundaries)

    def clear(self) -> None:
        self._boundaries.clear()
        self._shapely_geoms.clear()
        self._id_index_map.clear()
        self._tree = None

    def save_to_json(self, file_path: Path | str | None = None) -> Path:
        target_path = Path(file_path or self.persistence_file or "backend/data/gis/boundary_store.json")
        target_path.parent.mkdir(parents=True, exist_ok=True)

        features = []
        for boundary in self._boundaries.values():
            features.append({
                "type": "Feature",
                "properties": {
                    "id": boundary.id,
                    "name": boundary.name,
                    "category": boundary.category.value,
                    "state": boundary.state,
                    "district": boundary.district,
                    "source": boundary.source,
                    "source_url": boundary.source_url,
                    "last_updated": boundary.last_updated,
                    "is_demo": boundary.is_demo,
                    "metadata": boundary.metadata,
                },
                "geometry": boundary.geometry,
            })

        geojson_data = {
            "type": "FeatureCollection",
            "name": "gis_boundary_repository_store",
            "features": features,
        }

        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(geojson_data, f, indent=2)

        return target_path

    def load_from_json(self, file_path: Path | str | None = None) -> int:
        target_path = Path(file_path or self.persistence_file or "backend/data/gis/boundary_store.json")
        if not target_path.exists():
            return 0

        with open(target_path, "r", encoding="utf-8") as f:
            geojson_data = json.load(f)

        features = geojson_data.get("features", [])
        loaded_boundaries: list[GISBoundary] = []

        for f_item in features:
            props = f_item.get("properties", {})
            geom = f_item.get("geometry")
            if not geom:
                continue

            try:
                cat_enum = BoundaryCategory.parse_category(props.get("category", "OTHER_RESTRICTED_ZONE"))
                boundary = GISBoundary(
                    id=str(props.get("id")),
                    name=str(props.get("name")),
                    category=cat_enum,
                    state=str(props.get("state", "Unspecified")),
                    district=str(props.get("district", "Unspecified")),
                    geometry=geom,
                    source=str(props.get("source", "DEMO DATA — NOT OFFICIAL BOUNDARIES")),
                    source_url=props.get("source_url"),
                    last_updated=str(props.get("last_updated")),
                    metadata=props.get("metadata", {}),
                    is_demo=bool(props.get("is_demo", True)),
                )
                loaded_boundaries.append(boundary)
            except Exception:
                continue

        return self.add_many(loaded_boundaries)
