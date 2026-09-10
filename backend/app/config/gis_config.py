"""Central configuration for the GIS boundary data layer (Feature 4).

Every path, CRS assumption, geometry limit, and category label used by
app/models/boundary_geometry.py, app/repositories/, and app/services/gis_boundary_service.py
lives here, so storage location and validation strictness can be tuned in one place.

Storage model
-------------
Boundaries are stored in EPSG:4326 (WGS84 lon/lat) regardless of backend. That is the
CRS PostGIS geography columns require, the CRS RFC 7946 GeoJSON mandates, and the CRS
Leaflet/MapLibre consume directly -- so the file backend and a future PostGIS backend
hold byte-identical coordinates and no reprojection is needed when migrating.
"""

from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]

# --- Storage -----------------------------------------------------------------------
GIS_DATA_DIR = BACKEND_ROOT / "data" / "gis"
DEMO_DATASET_PATH = GIS_DATA_DIR / "demo_boundaries.geojson"
BOUNDARY_STORE_PATH = GIS_DATA_DIR / "boundaries.geojson"
MIGRATIONS_DIR = BACKEND_ROOT / "migrations"

# --- Coordinate reference systems --------------------------------------------------
# The one CRS everything is stored in. Imports in any other CRS are transformed to this
# before they reach a repository; nothing is ever persisted in a source CRS.
STORAGE_CRS = "EPSG:4326"

# RFC 7946 (the current GeoJSON spec) fixes GeoJSON to WGS84 and removed the `crs`
# member. Files written before that revision may still carry one, so the importer reads
# it when present and otherwise assumes this.
DEFAULT_GEOJSON_CRS = "EPSG:4326"

# --- Geometry validation limits ----------------------------------------------------
# A linear ring needs 4 positions minimum: 3 distinct corners plus the repeated closing
# position (RFC 7946 section 3.1.6).
MIN_RING_POSITIONS = 4

# Rings whose first and last positions differ by less than this (in degrees) are closed
# automatically rather than rejected; real-world exports frequently drop the repeat.
RING_CLOSURE_TOLERANCE = 1e-9

# Guardrails against obviously-wrong input (a whole-globe polygon, or a "boundary" too
# small to be a real restricted area). Both are in square kilometres; set either to None
# to disable that check.
MIN_AREA_SQKM: float | None = 1e-4
MAX_AREA_SQKM: float | None = 1_000_000.0

# Mean Earth radius (km), used by the spherical-excess area approximation in
# app/models/boundary_geometry.py. Adequate for sanity checks; not a substitute for a
# proper equal-area projection when reporting official areas.
EARTH_RADIUS_KM = 6371.0088

# --- Categories --------------------------------------------------------------------
# Human-readable labels for the BoundaryCategory enum in app/schemas/boundary.py. The
# enum is the single source of truth for which categories exist; this maps them to
# display text and is asserted complete by tests/test_gis_boundaries.py.
CATEGORY_LABELS: dict[str, str] = {
    "WILDLIFE_SANCTUARY": "Wildlife Sanctuary",
    "NATIONAL_PARK": "National Park",
    "FOREST": "Forest",
    "ECO_SENSITIVE_ZONE": "Eco-Sensitive Zone",
    "TIGER_RESERVE": "Tiger Reserve",
    "RAMSAR_WETLAND": "Ramsar Wetland",
    "OTHER_RESTRICTED_ZONE": "Other Restricted Zone",
}

# Lowercase source-field values an importer should map onto a category. Extend this
# rather than hardcoding aliases inside the importer.
CATEGORY_ALIASES: dict[str, str] = {
    "wls": "WILDLIFE_SANCTUARY",
    "wildlife sanctuary": "WILDLIFE_SANCTUARY",
    "sanctuary": "WILDLIFE_SANCTUARY",
    "np": "NATIONAL_PARK",
    "national park": "NATIONAL_PARK",
    "forest": "FOREST",
    "reserved forest": "FOREST",
    "protected forest": "FOREST",
    "esz": "ECO_SENSITIVE_ZONE",
    "eco sensitive zone": "ECO_SENSITIVE_ZONE",
    "eco-sensitive zone": "ECO_SENSITIVE_ZONE",
    "tiger reserve": "TIGER_RESERVE",
    "ramsar": "RAMSAR_WETLAND",
    "ramsar site": "RAMSAR_WETLAND",
    "ramsar wetland": "RAMSAR_WETLAND",
    "wetland": "RAMSAR_WETLAND",
}

# --- Provenance --------------------------------------------------------------------
# Attached to every record that is not sourced from an official/licensed dataset, and
# echoed at the top of any FeatureCollection containing at least one such record.
# Nothing in this system may present generated geometry as an official boundary.
DEMO_DATA_NOTICE = "DEMO DATA - NOT OFFICIAL BOUNDARIES"
DEMO_SOURCE_NAME = "PAIMANA synthetic demo dataset"

# --- Import ------------------------------------------------------------------------
# Extensions the importer dispatches on. Shapefile and GeoPackage need the optional GIS
# extras (see requirements-gis.txt); GeoJSON needs nothing beyond shapely.
SUPPORTED_IMPORT_SUFFIXES: dict[str, str] = {
    ".geojson": "geojson",
    ".json": "geojson",
    ".shp": "shapefile",
    ".gpkg": "geopackage",
}

# Namespace for deterministic UUIDv5 boundary ids, so re-importing the same source file
# produces the same ids instead of duplicating records.
BOUNDARY_ID_NAMESPACE = "1b2f8c60-0f1a-5a7e-9c3d-6f0a1e2b3c4d"
