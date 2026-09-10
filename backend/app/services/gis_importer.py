from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.schemas.gis_boundary import BoundaryCategory, GISBoundary, GISBoundaryImportResult
from app.services.gis_validator import validate_and_repair_geometry


class GISDataImporter:
    """Importer for loading, transforming, and validating GeoJSON/Shapefile/GeoPackage boundary layers."""

    @classmethod
    def import_file(
        self,
        file_path: Path | str,
        default_source: str = "DEMO DATA — NOT OFFICIAL BOUNDARIES",
        is_demo: bool = True,
        override_category: BoundaryCategory | None = None,
    ) -> tuple[list[GISBoundary], GISBoundaryImportResult]:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"GIS import file not found at {path}")

        ext = path.suffix.lower()
        if ext in [".geojson", ".json"]:
            return self._import_geojson(path, default_source, is_demo, override_category)
        elif ext in [".shp", ".gpkg"]:
            return self._import_geopandas(path, default_source, is_demo, override_category)
        else:
            raise ValueError(f"Unsupported file format '{ext}'. Supported formats: .geojson, .json, .shp, .gpkg")

    @classmethod
    def _import_geojson(
        cls,
        path: Path,
        default_source: str,
        is_demo: bool,
        override_category: BoundaryCategory | None,
    ) -> tuple[list[GISBoundary], GISBoundaryImportResult]:
        with open(path, "r", encoding="utf-8") as f:
            content = json.load(f)

        features = content.get("features", [])
        if not isinstance(features, list):
            raise ValueError("Invalid GeoJSON file structure: top-level object must contain a 'features' array")

        # Detect CRS from GeoJSON header if specified
        source_epsg = 4326
        crs_props = content.get("crs", {}).get("properties", {})
        crs_name = crs_props.get("name", "")
        if "3857" in crs_name:
            source_epsg = 3857
        elif "32643" in crs_name:
            source_epsg = 32643

        imported: list[GISBoundary] = []
        errors: list[str] = []
        repaired_count = 0

        for idx, feature in enumerate(features):
            props = feature.get("properties", {})
            geom_dict = feature.get("geometry")

            if not geom_dict:
                errors.append(f"Record #{idx + 1}: Missing geometry object")
                continue

            # Validate & repair geometry
            try:
                shapely_geom, valid_geom_dict, was_repaired = validate_and_repair_geometry(
                    geom_dict, source_epsg=source_epsg, target_epsg=4326,
                )
                if was_repaired:
                    repaired_count += 1
            except Exception as exc:
                errors.append(f"Record #{idx + 1} ({props.get('name', 'Unnamed')}): Geometry invalid - {exc}")
                continue

            # Extract fields with property name fallbacks
            boundary_id = str(props.get("id") or props.get("ID") or f"BOUND-{path.stem.upper()}-{idx + 1:04d}")
            name = str(props.get("name") or props.get("NAME") or props.get("zone_name") or f"Boundary #{idx + 1}")
            
            cat_raw = str(props.get("category") or props.get("CATEGORY") or props.get("designation") or "OTHER_RESTRICTED_ZONE")
            category = override_category if override_category else BoundaryCategory.parse_category(cat_raw)

            state = str(props.get("state") or props.get("STATE") or props.get("state_name") or "Unspecified")
            district = str(props.get("district") or props.get("DISTRICT") or props.get("dist_name") or "Unspecified")
            source = str(props.get("source") or default_source)
            source_url = props.get("source_url") or props.get("SOURCE_URL")
            last_updated = str(props.get("last_updated") or "2026-09-10T12:00:00Z")

            # Pack unmapped custom attributes into metadata
            metadata = {k: v for k, v in props.items() if k not in ["id", "name", "category", "state", "district", "source", "source_url", "last_updated"]}

            try:
                boundary = GISBoundary(
                    id=boundary_id,
                    name=name,
                    category=category,
                    state=state,
                    district=district,
                    geometry=valid_geom_dict,
                    source=source,
                    source_url=source_url,
                    last_updated=last_updated,
                    metadata=metadata,
                    is_demo=is_demo or "DEMO" in source.upper(),
                )
                imported.append(boundary)
            except Exception as exc:
                errors.append(f"Record #{idx + 1} ({name}): Validation error - {exc}")

        result = GISBoundaryImportResult(
            file_path=str(path),
            total_records=len(features),
            imported_count=len(imported),
            rejected_count=len(errors),
            repaired_count=repaired_count,
            source_attribution=default_source,
            errors=errors,
        )

        return imported, result

    @classmethod
    def _import_geopandas(
        cls,
        path: Path,
        default_source: str,
        is_demo: bool,
        override_category: BoundaryCategory | None,
    ) -> tuple[list[GISBoundary], GISBoundaryImportResult]:
        """Optional Shapefile / GeoPackage importer fallback using geopandas if installed."""
        try:
            import geopandas as gpd
        except ImportError:
            raise NotImplementedError(
                "Importing Shapefile (.shp) or GeoPackage (.gpkg) requires 'geopandas'. "
                "Please install geopandas or convert your file to .geojson"
            )

        gdf = gpd.read_file(path)
        if gdf.crs and gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs(epsg=4326)

        geojson_dict = json.loads(gdf.to_json())
        return cls._import_geojson_dict(geojson_dict, str(path), default_source, is_demo, override_category)
