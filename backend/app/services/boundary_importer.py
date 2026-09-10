"""Reusable importer for third-party boundary datasets (Feature 4).

Reads GeoJSON, ESRI Shapefile, and GeoPackage; detects the source CRS; transforms to the
storage CRS when they differ; validates every geometry; rejects (never repairs) invalid
records; and preserves source attribution on each one.

Dependency policy
-----------------
GeoJSON import needs only shapely, which is a hard requirement of this backend.
Shapefile and GeoPackage need the optional GIS extras (geopandas + pyproj, see
requirements-gis.txt) and CRS transformation needs pyproj. Those are imported lazily, at
the moment they are actually needed, so a machine that only ever imports WGS84 GeoJSON
does not have to install the heavy GDAL stack -- and one that tries to import a shapefile
without them gets a precise message naming what to install, rather than an ImportError
at startup.

Every run returns an `ImportReport` accounting for all features read: imported,
duplicate, or rejected-with-reason. Nothing is dropped silently.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from shapely.geometry import mapping as shapely_mapping

from app.config import gis_config as config
from app.models.boundary_geometry import GeometryValidationError, validate_geometry
from app.schemas.boundary import BoundaryCategory, GISBoundary, ImportReport, RejectedFeature
from app.services.gis_boundary_service import GISBoundaryService, make_boundary_id, mark_as_demo


class BoundaryImportError(RuntimeError):
    """Raised when a whole file cannot be imported (bad format, missing driver, unknown CRS)."""


@dataclass(frozen=True)
class FieldMapping:
    """How to find each boundary field in a source dataset's attribute table.

    Each entry is a list of candidate column names tried in order, case-insensitively --
    published datasets are wildly inconsistent about naming, and this keeps that mess in
    configuration instead of in branching code. Pass a custom mapping for any dataset the
    defaults do not fit.
    """

    name: Sequence[str] = ("name", "boundary_name", "site_name", "pa_name", "np_name", "wls_name", "title")
    category: Sequence[str] = ("category", "type", "pa_type", "designation", "desig", "class")
    state: Sequence[str] = ("state", "state_name", "st_name", "province")
    district: Sequence[str] = ("district", "district_name", "dist_name", "dt_name")
    last_updated: Sequence[str] = ("last_updated", "updated", "update_date", "notif_date", "date")
    default_category: BoundaryCategory | None = None
    #: Attribute columns copied verbatim into `metadata`; empty means copy them all.
    metadata_fields: Sequence[str] = field(default_factory=tuple)


DEFAULT_FIELD_MAPPING = FieldMapping()


@dataclass
class _ReadResult:
    features: list[dict[str, Any]]
    source_crs: str | None
    driver: str


class BoundaryImporter:
    """Loads boundary files into a `GISBoundaryService`."""

    def __init__(self, service: GISBoundaryService, *, mapping: FieldMapping = DEFAULT_FIELD_MAPPING) -> None:
        self.service = service
        self.mapping = mapping

    # --- public API -----------------------------------------------------------------

    def import_file(
        self,
        path: Path | str,
        *,
        source: str,
        source_url: str | None = None,
        last_updated: date | None = None,
        default_state: str | None = None,
        default_category: BoundaryCategory | str | None = None,
        is_demo: bool = False,
        overwrite: bool = True,
        dry_run: bool = False,
        layer: str | None = None,
    ) -> ImportReport:
        """Import one file and return a full account of what happened.

        `source`, `source_url`, and `last_updated` are the attribution stamped onto every
        imported record; `source` is required precisely so no boundary can enter the
        store without a traceable origin. Per-feature values found via the field mapping
        override `last_updated`, `default_state`, and `default_category`.

        `dry_run=True` validates everything and reports, without writing.
        """
        path = Path(path)
        if not path.exists():
            raise BoundaryImportError(f"No such file: {path}")

        read = self._read(path, layer=layer)
        transformed = False
        features = read.features
        if read.source_crs and not _is_storage_crs(read.source_crs):
            features = [_transform_feature(feature, read.source_crs) for feature in features]
            transformed = True

        boundaries, rejected, duplicates = self._build_boundaries(
            features,
            source=source,
            source_url=source_url,
            last_updated=last_updated or date.today(),
            default_state=default_state,
            default_category=default_category,
            is_demo=is_demo,
        )

        if boundaries and not dry_run:
            self.service.add_boundaries(boundaries, overwrite=overwrite)

        return ImportReport(
            source_path=str(path),
            driver=read.driver,
            source_crs=read.source_crs,
            transformed=transformed,
            features_read=len(read.features),
            imported=0 if dry_run else len(boundaries),
            skipped_duplicates=duplicates,
            rejected=rejected,
        )

    # --- reading --------------------------------------------------------------------

    def _read(self, path: Path, *, layer: str | None) -> _ReadResult:
        driver = config.SUPPORTED_IMPORT_SUFFIXES.get(path.suffix.lower())
        if driver is None:
            raise BoundaryImportError(
                f"Unsupported file type {path.suffix!r}. Supported: "
                f"{', '.join(sorted(config.SUPPORTED_IMPORT_SUFFIXES))}"
            )
        if driver == "geojson":
            return self._read_geojson(path)
        return self._read_with_geopandas(path, driver=driver, layer=layer)

    def _read_geojson(self, path: Path) -> _ReadResult:
        """Read a GeoJSON file with the standard library.

        RFC 7946 fixed GeoJSON at WGS84 and removed the `crs` member, but files predating
        that revision still carry one, so it is honoured when present.
        """
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise BoundaryImportError(f"{path} is not valid JSON: {exc}") from exc

        payload_type = payload.get("type")
        if payload_type == "FeatureCollection":
            features = list(payload.get("features") or [])
        elif payload_type == "Feature":
            features = [payload]
        else:
            raise BoundaryImportError(
                f"{path} must contain a GeoJSON Feature or FeatureCollection, got {payload_type!r}."
            )
        return _ReadResult(features=features, source_crs=_geojson_crs(payload), driver="geojson")

    def _read_with_geopandas(self, path: Path, *, driver: str, layer: str | None) -> _ReadResult:
        """Read a Shapefile or GeoPackage via geopandas, detecting the CRS from the file.

        geopandas reads the `.prj` sidecar (shapefile) or the `gpkg_spatial_ref_sys` table
        (GeoPackage), so CRS detection is the driver's job here, not ours. Reprojection is
        done by geopandas' own `to_crs` for these formats, which is why the returned CRS
        is already the storage CRS.
        """
        try:
            import geopandas  # noqa: PLC0415  (optional extra, imported on demand)
        except ModuleNotFoundError as exc:
            raise BoundaryImportError(
                f"Reading {driver} files needs the optional GIS extras. "
                "Install them with: pip install -r requirements-gis.txt"
            ) from exc

        frame = geopandas.read_file(path, layer=layer) if layer else geopandas.read_file(path)
        source_crs = str(frame.crs) if frame.crs is not None else None
        if frame.crs is not None and not _is_storage_crs(source_crs):
            frame = frame.to_crs(config.STORAGE_CRS)
            # Report the ORIGINAL crs but return already-projected geometry; import_file's
            # `transformed` flag is set from this comparison, so keep the original string.

        # Built row by row rather than via `frame.to_json()`: GDAL hands back real date
        # and numpy scalar columns (a GeoPackage DATE becomes a pandas Timestamp), which
        # the stdlib JSON encoder refuses. `_json_safe` coerces them instead of losing
        # the attribute.
        geometry_column = frame.geometry.name
        attribute_columns = [column for column in frame.columns if column != geometry_column]
        features = [
            {
                "type": "Feature",
                "geometry": None if row[geometry_column] is None or row[geometry_column].is_empty
                else shapely_mapping(row[geometry_column]),
                "properties": {column: _json_safe(row[column]) for column in attribute_columns},
            }
            for _, row in frame.iterrows()
        ]
        return _ReadResult(features=features, source_crs=source_crs, driver=driver)

    # --- feature mapping ------------------------------------------------------------

    def _build_boundaries(
        self,
        features: Iterable[dict[str, Any]],
        *,
        source: str,
        source_url: str | None,
        last_updated: date,
        default_state: str | None,
        default_category: BoundaryCategory | str | None,
        is_demo: bool,
    ) -> tuple[list[GISBoundary], list[RejectedFeature], int]:
        fallback_category = (
            BoundaryCategory(default_category) if default_category else self.mapping.default_category
        )
        boundaries: dict[str, GISBoundary] = {}
        rejected: list[RejectedFeature] = []
        duplicates = 0

        for index, feature in enumerate(features):
            properties = {str(k): v for k, v in (feature.get("properties") or {}).items()}
            name = _first_value(properties, self.mapping.name)
            try:
                boundary = self._build_one(
                    feature,
                    properties=properties,
                    index=index,
                    source=source,
                    source_url=source_url,
                    last_updated=last_updated,
                    default_state=default_state,
                    fallback_category=fallback_category,
                    is_demo=is_demo,
                )
            except (GeometryValidationError, ValueError, TypeError) as exc:
                rejected.append(RejectedFeature(index=index, name=_as_text(name), reason=_reason(exc)))
                continue

            if boundary.id in boundaries:
                duplicates += 1
                continue
            boundaries[boundary.id] = boundary

        return list(boundaries.values()), rejected, duplicates

    def _build_one(
        self,
        feature: dict[str, Any],
        *,
        properties: dict[str, Any],
        index: int,
        source: str,
        source_url: str | None,
        last_updated: date,
        default_state: str | None,
        fallback_category: BoundaryCategory | None,
        is_demo: bool,
    ) -> GISBoundary:
        geometry = feature.get("geometry")
        if geometry is None:
            raise GeometryValidationError("Feature has no geometry.")
        geometry = validate_geometry(geometry)

        name = _as_text(_first_value(properties, self.mapping.name))
        if not name:
            raise ValueError(f"No name found; tried columns {list(self.mapping.name)}.")

        category = _resolve_category(_first_value(properties, self.mapping.category), fallback_category)
        state = _as_text(_first_value(properties, self.mapping.state)) or default_state
        if not state:
            raise ValueError(f"No state found; tried columns {list(self.mapping.state)} and no default was given.")

        district = _as_text(_first_value(properties, self.mapping.district))
        record_updated = _coerce_date(_first_value(properties, self.mapping.last_updated)) or last_updated

        metadata = self._collect_metadata(properties)
        metadata["source_feature_index"] = index
        if is_demo:
            metadata = mark_as_demo(metadata)

        return GISBoundary(
            id=make_boundary_id(source, name, category),
            name=name,
            category=category,
            state=state,
            district=district,
            geometry=geometry,
            source=source,
            source_url=source_url,
            last_updated=record_updated,
            metadata=metadata,
        )

    def _collect_metadata(self, properties: dict[str, Any]) -> dict[str, Any]:
        """Carry source attributes through, so provenance detail is never lost on import."""
        wanted = self.mapping.metadata_fields
        selected = properties if not wanted else {k: v for k, v in properties.items() if k in wanted}
        return {key: _json_safe(value) for key, value in selected.items()}


# ---------------------------------------------------------------------------------
# CRS handling
# ---------------------------------------------------------------------------------


def _geojson_crs(payload: dict[str, Any]) -> str | None:
    """Read a pre-RFC-7946 `crs` member, defaulting to the spec's WGS84."""
    crs = payload.get("crs")
    if isinstance(crs, dict):
        name = (crs.get("properties") or {}).get("name")
        if isinstance(name, str) and name:
            return _normalize_crs_name(name)
    return config.DEFAULT_GEOJSON_CRS


def _normalize_crs_name(name: str) -> str:
    """Turn OGC URN forms (`urn:ogc:def:crs:EPSG::32643`) into `EPSG:32643`."""
    text = name.strip()
    if text.upper().startswith("URN:OGC:DEF:CRS:"):
        parts = [part for part in text.split(":") if part]
        if len(parts) >= 2 and parts[-1].isdigit():
            return f"{parts[-2].upper()}:{parts[-1]}"
    if text.upper() in {"CRS84", "OGC:CRS84", "URN:OGC:DEF:CRS:OGC:1.3:CRS84"}:
        return config.STORAGE_CRS
    return text


def _is_storage_crs(crs: str | None) -> bool:
    """True when `crs` is the storage CRS, using pyproj for equivalence when available."""
    if crs is None:
        return True  # Nothing declared: RFC 7946 says assume WGS84.
    normalized = _normalize_crs_name(crs)
    if normalized.upper() in {config.STORAGE_CRS.upper(), "EPSG:4326", "WGS84", "CRS84", "OGC:CRS84"}:
        return True
    try:
        from pyproj import CRS  # noqa: PLC0415  (optional extra)
    except ModuleNotFoundError:
        # Cannot prove equivalence without pyproj; treat as different so the caller gets
        # an explicit "install pyproj to transform" error instead of silently storing
        # coordinates in the wrong CRS.
        return False
    return CRS.from_user_input(normalized) == CRS.from_user_input(config.STORAGE_CRS)


def _transform_feature(feature: dict[str, Any], source_crs: str) -> dict[str, Any]:
    """Reproject one feature's geometry into the storage CRS."""
    geometry = feature.get("geometry")
    if geometry is None:
        return feature
    return {**feature, "geometry": transform_geometry(geometry, source_crs)}


def transform_geometry(geometry: dict[str, Any], source_crs: str, target_crs: str | None = None) -> dict[str, Any]:
    """Reproject a GeoJSON Polygon/MultiPolygon between CRSs.

    Public because the seed script and tests reproject without going through a file.
    Requires pyproj; raises `BoundaryImportError` naming the fix when it is absent.
    """
    target_crs = target_crs or config.STORAGE_CRS
    try:
        from pyproj import Transformer  # noqa: PLC0415  (optional extra)
    except ModuleNotFoundError as exc:
        raise BoundaryImportError(
            f"Source data is in {source_crs} and must be transformed to {target_crs}, "
            "which needs pyproj. Install the GIS extras: pip install -r requirements-gis.txt"
        ) from exc

    transformer = Transformer.from_crs(
        _normalize_crs_name(source_crs), target_crs, always_xy=True
    )

    def project(coordinates: Any) -> Any:
        if coordinates and isinstance(coordinates[0], (int, float)):
            x, y = transformer.transform(coordinates[0], coordinates[1])
            return [x, y]
        return [project(item) for item in coordinates]

    return {"type": geometry["type"], "coordinates": project(geometry.get("coordinates") or [])}


# ---------------------------------------------------------------------------------
# Attribute helpers
# ---------------------------------------------------------------------------------


def _first_value(properties: dict[str, Any], candidates: Sequence[str]) -> Any:
    """Return the first candidate column present and non-empty, matched case-insensitively."""
    lowered = {key.casefold(): value for key, value in properties.items()}
    for candidate in candidates:
        value = lowered.get(candidate.casefold())
        if value not in (None, "", []):
            return value
    return None


def _resolve_category(raw: Any, fallback: BoundaryCategory | None) -> BoundaryCategory:
    """Map a source value onto a BoundaryCategory via the enum then the alias table."""
    text = _as_text(raw)
    if text:
        try:
            return BoundaryCategory(text.upper().replace(" ", "_").replace("-", "_"))
        except ValueError:
            pass
        alias = config.CATEGORY_ALIASES.get(text.casefold().strip())
        if alias:
            return BoundaryCategory(alias)
    if fallback is not None:
        return fallback
    if text:
        # Unrecognised but present: keep the record and preserve the original wording in
        # metadata rather than dropping a real boundary over a vocabulary mismatch.
        return BoundaryCategory.OTHER_RESTRICTED_ZONE
    raise ValueError("No category found and no default category was given.")


def _coerce_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    text = _as_text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _json_safe(value: Any) -> Any:
    """Coerce a driver-supplied attribute value into something JSON (and JSONB) accepts.

    GDAL and pandas return numpy scalars, pandas Timestamps, and NaN for perfectly
    ordinary columns. Coercing beats filtering here: a dropped attribute is provenance
    permanently lost at import time, while a stringified one is still traceable.
    """
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, (int, float)):
        return None if isinstance(value, float) and not math.isfinite(value) else value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    # numpy scalars and pandas Timestamps expose these; check them before falling back.
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except (ValueError, TypeError):  # pragma: no cover - defensive
            pass
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _reason(exc: Exception) -> str:
    """Flatten an exception (including Pydantic's multi-error form) into one line."""
    text = str(exc).replace("\n", " ")
    return text[:400] if text else exc.__class__.__name__
