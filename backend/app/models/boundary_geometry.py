"""Geometry validation and normalization for the GIS boundary data layer.

One module owns every geometric rule in the system, so the repository, the service, the
importer, and the seed script all reject exactly the same things for exactly the same
reasons. Nothing here imports from app.schemas or app.repositories -- it operates on
plain GeoJSON-shaped mappings, which keeps it usable from scripts and tests with no
FastAPI or storage backend involved.

Validation policy: geometry is REJECTED, never silently repaired. The two exceptions are
deliberate, lossless normalizations that every GIS toolchain treats as equivalent input:

  1. Closing a ring whose final position merely repeats the first within
     RING_CLOSURE_TOLERANCE (many exporters drop the repeat).
  2. Re-winding rings to RFC 7946 orientation -- exterior counterclockwise, holes
     clockwise. Winding carries no information in GeoJSON, but PostGIS, Shapely, and
     several renderers behave better with consistent orientation.

Anything else -- a self-intersection, a hole outside its shell, a coordinate off the
globe, a degenerate ring -- is a GeometryValidationError.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Sequence

from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient
from shapely.validation import explain_validity

from app.config import gis_config as config

SUPPORTED_GEOMETRY_TYPES = ("Polygon", "MultiPolygon")

# (min_lon, min_lat, max_lon, max_lat)
BBox = tuple[float, float, float, float]


class GeometryValidationError(ValueError):
    """Raised when geometry cannot be accepted into the boundary store.

    Carries a human-readable reason so importers can report per-feature rejections
    without needing to re-derive why something failed.
    """


# ---------------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------------


def validate_geometry(geometry: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate a GeoJSON Polygon/MultiPolygon and return a normalized copy.

    Raises GeometryValidationError with a specific reason for anything invalid. The
    returned dict is plain JSON-safe Python (floats and lists), closed, and wound to
    RFC 7946 orientation.
    """
    if not isinstance(geometry, Mapping):
        raise GeometryValidationError(f"Geometry must be a GeoJSON object, got {type(geometry).__name__}.")

    geom_type = geometry.get("type")
    if geom_type not in SUPPORTED_GEOMETRY_TYPES:
        raise GeometryValidationError(
            f"Unsupported geometry type {geom_type!r}; boundaries must be one of {SUPPORTED_GEOMETRY_TYPES}."
        )

    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, Sequence) or isinstance(coordinates, (str, bytes)) or len(coordinates) == 0:
        raise GeometryValidationError(f"{geom_type} coordinates must be a non-empty array.")

    if geom_type == "Polygon":
        normalized_coords: Any = _normalize_polygon(coordinates, path="coordinates")
    else:
        normalized_coords = [
            _normalize_polygon(polygon, path=f"coordinates[{index}]") for index, polygon in enumerate(coordinates)
        ]

    normalized = {"type": geom_type, "coordinates": normalized_coords}

    # Structural checks passed; now let Shapely rule on topology (self-intersections,
    # holes outside their shell, overlapping parts of a MultiPolygon).
    geom = shape(normalized)
    if geom.is_empty:
        raise GeometryValidationError("Geometry is empty.")
    if not geom.is_valid:
        raise GeometryValidationError(f"Invalid geometry: {explain_validity(geom)}.")

    _validate_area(geom_type, normalized)

    # Re-wind through Shapely so stored orientation is always RFC 7946 compliant.
    return _to_geojson(_orient(geom))


def _normalize_polygon(polygon: Any, path: str) -> list[list[list[float]]]:
    """Validate one polygon's ring array (exterior first, then any holes)."""
    if not isinstance(polygon, Sequence) or isinstance(polygon, (str, bytes)) or len(polygon) == 0:
        raise GeometryValidationError(f"{path} must be a non-empty array of linear rings.")
    return [_normalize_ring(ring, path=f"{path}[{index}]") for index, ring in enumerate(polygon)]


def _normalize_ring(ring: Any, path: str) -> list[list[float]]:
    """Validate one linear ring and return it closed, as plain floats."""
    if not isinstance(ring, Sequence) or isinstance(ring, (str, bytes)):
        raise GeometryValidationError(f"{path} must be an array of positions.")

    positions = [_normalize_position(position, path=f"{path}[{index}]") for index, position in enumerate(ring)]
    positions = _close_ring(positions, path=path)

    if len(positions) < config.MIN_RING_POSITIONS:
        raise GeometryValidationError(
            f"{path} has {len(positions)} positions; a linear ring needs at least "
            f"{config.MIN_RING_POSITIONS} (3 distinct corners plus the repeated closing position)."
        )
    if len({(x, y) for x, y in positions[:-1]}) < 3:
        raise GeometryValidationError(f"{path} is degenerate: fewer than 3 distinct positions.")
    return positions


def _normalize_position(position: Any, path: str) -> list[float]:
    """Validate one [longitude, latitude] position; any elevation element is dropped."""
    if not isinstance(position, Sequence) or isinstance(position, (str, bytes)) or len(position) < 2:
        raise GeometryValidationError(f"{path} must be a [longitude, latitude] pair.")

    raw_lon, raw_lat = position[0], position[1]
    if isinstance(raw_lon, bool) or isinstance(raw_lat, bool) or not isinstance(raw_lon, (int, float)) or not isinstance(raw_lat, (int, float)):
        raise GeometryValidationError(f"{path} coordinates must be numbers, got {position[:2]!r}.")

    lon, lat = float(raw_lon), float(raw_lat)
    if not math.isfinite(lon) or not math.isfinite(lat):
        raise GeometryValidationError(f"{path} contains a non-finite coordinate.")
    if not -180.0 <= lon <= 180.0:
        raise GeometryValidationError(f"{path} longitude {lon} is outside [-180, 180]; is this data really in {config.STORAGE_CRS}?")
    if not -90.0 <= lat <= 90.0:
        raise GeometryValidationError(f"{path} latitude {lat} is outside [-90, 90]; is this data really in {config.STORAGE_CRS}?")
    return [lon, lat]


def _close_ring(positions: list[list[float]], path: str) -> list[list[float]]:
    """Append the closing position when an exporter dropped it."""
    if len(positions) < 3:
        raise GeometryValidationError(f"{path} has {len(positions)} positions; too few to form a ring.")

    first, last = positions[0], positions[-1]
    if first == last:
        return positions
    if math.isclose(first[0], last[0], abs_tol=config.RING_CLOSURE_TOLERANCE) and math.isclose(
        first[1], last[1], abs_tol=config.RING_CLOSURE_TOLERANCE
    ):
        return [*positions[:-1], list(first)]
    return [*positions, list(first)]


def _validate_area(geom_type: str, geometry: Mapping[str, Any]) -> None:
    """Reject globe-sized or vanishingly small polygons (see gis_config limits)."""
    if config.MIN_AREA_SQKM is None and config.MAX_AREA_SQKM is None:
        return
    area = geometry_area_sqkm(geometry)
    if config.MIN_AREA_SQKM is not None and area < config.MIN_AREA_SQKM:
        raise GeometryValidationError(
            f"{geom_type} area {area:.6f} sq km is below the minimum {config.MIN_AREA_SQKM} sq km."
        )
    if config.MAX_AREA_SQKM is not None and area > config.MAX_AREA_SQKM:
        raise GeometryValidationError(
            f"{geom_type} area {area:,.0f} sq km exceeds the maximum {config.MAX_AREA_SQKM:,.0f} sq km."
        )


# ---------------------------------------------------------------------------------
# Shapely interop -- the seam a PostGIS backend reuses (see geometry_to_ewkt)
# ---------------------------------------------------------------------------------


def to_shapely(geometry: Mapping[str, Any]) -> BaseGeometry:
    """Build a Shapely geometry from a GeoJSON mapping (no validation)."""
    return shape(dict(geometry))


def from_shapely(geom: BaseGeometry) -> dict[str, Any]:
    """Convert a Shapely Polygon/MultiPolygon back to a normalized GeoJSON mapping."""
    if not isinstance(geom, (Polygon, MultiPolygon)):
        raise GeometryValidationError(
            f"Expected Polygon or MultiPolygon, got {geom.geom_type}; boundaries must be areal."
        )
    return _to_geojson(_orient(geom))


def _orient(geom: BaseGeometry) -> BaseGeometry:
    """Apply RFC 7946 winding: exterior counterclockwise, interior rings clockwise."""
    if isinstance(geom, Polygon):
        return orient(geom, sign=1.0)
    if isinstance(geom, MultiPolygon):
        return MultiPolygon([orient(part, sign=1.0) for part in geom.geoms])
    return geom


def _to_geojson(geom: BaseGeometry) -> dict[str, Any]:
    """Shapely's mapping() returns nested tuples; JSON storage wants plain lists."""
    raw = mapping(geom)
    return {"type": raw["type"], "coordinates": _to_lists(raw["coordinates"])}


def _to_lists(value: Any) -> Any:
    if isinstance(value, (tuple, list)):
        return [_to_lists(item) for item in value]
    return float(value)


def geometry_to_ewkt(geometry: Mapping[str, Any], srid: int = 4326) -> str:
    """Render geometry as EWKT (`SRID=4326;POLYGON((...))`).

    This is the format a PostGIS backend hands to `ST_GeomFromEWKT`, and it is what
    makes the file backend and the PostGIS backend interchangeable: both accept and
    return the same validated GeoJSON mapping, and only this function sits between that
    mapping and the database.
    """
    return f"SRID={srid};{to_shapely(geometry).wkt}"


# ---------------------------------------------------------------------------------
# Derived measures -- used for indexing, filtering, and record summaries
# ---------------------------------------------------------------------------------


def geometry_bbox(geometry: Mapping[str, Any]) -> BBox:
    """Return (min_lon, min_lat, max_lon, max_lat).

    The file repository stores this per record and checks it before any Shapely call, so
    a bbox query stays cheap; PostGIS gets the same effect from a GiST index.
    """
    min_x, min_y, max_x, max_y = to_shapely(geometry).bounds
    return (float(min_x), float(min_y), float(max_x), float(max_y))


def geometry_centroid(geometry: Mapping[str, Any]) -> tuple[float, float]:
    """Return the geometry's (longitude, latitude) centroid."""
    centroid = to_shapely(geometry).centroid
    return (float(centroid.x), float(centroid.y))


def geometry_area_sqkm(geometry: Mapping[str, Any]) -> float:
    """Approximate a lon/lat polygon's area in square kilometres.

    Uses the spherical-excess approximation rather than Shapely's planar `.area`, which
    would return meaningless square-degrees for EPSG:4326 input. Accurate enough for
    sanity limits and record summaries; report official areas from an equal-area
    projection (or PostGIS `ST_Area(geography)`) instead.
    """
    geom_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    polygons = [coordinates] if geom_type == "Polygon" else list(coordinates)

    total = 0.0
    for polygon in polygons:
        if not polygon:
            continue
        total += _ring_area_sqkm(polygon[0])
        for hole in polygon[1:]:
            total -= _ring_area_sqkm(hole)
    return max(total, 0.0)


def _ring_area_sqkm(ring: Iterable[Sequence[float]]) -> float:
    positions = [(float(p[0]), float(p[1])) for p in ring]
    if len(positions) < 4:
        return 0.0

    total = 0.0
    for (lon1, lat1), (lon2, lat2) in zip(positions, positions[1:]):
        total += math.radians(lon2 - lon1) * (2 + math.sin(math.radians(lat1)) + math.sin(math.radians(lat2)))
    return abs(total * config.EARTH_RADIUS_KM * config.EARTH_RADIUS_KM / 2.0)


def bbox_intersects(a: BBox, b: BBox) -> bool:
    """True when two (min_lon, min_lat, max_lon, max_lat) boxes overlap or touch."""
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def normalize_bbox(bbox: Sequence[float]) -> BBox:
    """Validate and order a caller-supplied bounding box."""
    if len(bbox) != 4:
        raise GeometryValidationError("A bounding box must have 4 values: [min_lon, min_lat, max_lon, max_lat].")
    values = [float(v) for v in bbox]
    if any(not math.isfinite(v) for v in values):
        raise GeometryValidationError("Bounding box values must be finite numbers.")
    min_lon, max_lon = sorted((values[0], values[2]))
    min_lat, max_lat = sorted((values[1], values[3]))
    if not (-180.0 <= min_lon and max_lon <= 180.0 and -90.0 <= min_lat and max_lat <= 90.0):
        raise GeometryValidationError(f"Bounding box {values} is outside the valid {config.STORAGE_CRS} range.")
    return (min_lon, min_lat, max_lon, max_lat)
