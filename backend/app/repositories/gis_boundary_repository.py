"""Storage backends for GIS boundaries.

`GISBoundaryRepository` is the contract; two implementations satisfy it:

  * `GeoJSONFileBoundaryRepository` -- the default. Persists one RFC 7946
    FeatureCollection to disk and answers queries in memory with Shapely. No database
    required, which is why it is what runs in this project today.
  * `PostGISBoundaryRepository` -- the same contract against PostgreSQL/PostGIS, used
    automatically when DATABASE_URL is configured and psycopg is installed.

The two are interchangeable because the contract is written in terms of the domain model
(`GISBoundary`, `BoundaryQuery`) and never leaks storage details. Migrating to PostGIS
is therefore: run migrations/0001_create_gis_boundaries.sql, set DATABASE_URL, re-run the
seed script. No service or route code changes.

Every filter in `BoundaryQuery` was chosen because it has a direct, index-friendly SQL
equivalent -- bbox becomes `geometry && ST_MakeEnvelope(...)` against a GiST index, and
the rest become plain WHERE clauses.

Both backends answer a bbox query through a real spatial index, so a caller never
compares its point against every boundary: the file backend keeps a Shapely `STRtree`,
the PostGIS backend uses the GiST index via the `&&` operator. That is what makes
`app/services/spatial_analysis_service.py` scale past a handful of records.
"""

from __future__ import annotations

import json
import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Iterable, Sequence

from shapely import STRtree
from shapely.geometry import box

from app.config import gis_config as config
from app.models import boundary_geometry as geo
from app.schemas.boundary import BoundaryQuery, GISBoundary


class BoundaryNotFoundError(LookupError):
    """Raised when a boundary id does not exist in the store."""


class DuplicateBoundaryError(ValueError):
    """Raised when adding a boundary whose id is already stored (without overwrite)."""


class GISBoundaryRepository(ABC):
    """The storage contract every backend implements."""

    #: Human-readable backend name, surfaced in health/status output.
    backend: str = "abstract"

    @abstractmethod
    def add(self, boundary: GISBoundary, *, overwrite: bool = False) -> GISBoundary:
        """Persist one boundary. Raises DuplicateBoundaryError unless `overwrite`."""

    @abstractmethod
    def add_many(self, boundaries: Iterable[GISBoundary], *, overwrite: bool = False) -> int:
        """Persist several boundaries in one write; returns how many were stored."""

    @abstractmethod
    def get(self, boundary_id: str) -> GISBoundary:
        """Fetch one boundary by id. Raises BoundaryNotFoundError."""

    @abstractmethod
    def find(self, query: BoundaryQuery | None = None) -> list[GISBoundary]:
        """Fetch boundaries matching `query`, ordered by name for stable output."""

    @abstractmethod
    def count(self, query: BoundaryQuery | None = None) -> int:
        """Count boundaries matching `query` without materializing geometry."""

    @abstractmethod
    def delete(self, boundary_id: str) -> None:
        """Remove one boundary. Raises BoundaryNotFoundError."""

    @abstractmethod
    def clear(self) -> int:
        """Remove every boundary; returns how many were removed."""

    def exists(self, boundary_id: str) -> bool:
        try:
            self.get(boundary_id)
        except BoundaryNotFoundError:
            return False
        return True


# ---------------------------------------------------------------------------------
# File backend (default)
# ---------------------------------------------------------------------------------


class GeoJSONFileBoundaryRepository(GISBoundaryRepository):
    """Stores boundaries as a single GeoJSON FeatureCollection on disk.

    Chosen over a bare SQLite/CSV store because the on-disk artifact is itself a valid
    GIS file: it opens directly in QGIS, `geopandas.read_file`, and `ogr2ogr`, so the
    same file that backs the API is also the file you hand to PostGIS at migration time.

    Records are held in memory (`_records`, insertion-ordered by id) alongside a Shapely
    `STRtree` built lazily over their geometries. A bbox query hits that R-tree instead of
    scanning every record -- the in-process equivalent of the GiST index the PostGIS
    backend relies on. The tree is invalidated on any mutation and rebuilt on next use,
    so it can never answer from stale geometry.
    """

    backend = "geojson-file"

    def __init__(self, path: Path | str = config.BOUNDARY_STORE_PATH, *, autoload: bool = True) -> None:
        self.path = Path(path)
        self._records: dict[str, GISBoundary] = {}
        self._bboxes: dict[str, geo.BBox] = {}
        # STRtree over every stored geometry, plus the id list aligned to its input
        # order. None means "needs rebuilding"; see _invalidate_index / _spatial_index.
        self._tree: STRtree | None = None
        self._tree_ids: list[str] = []
        if autoload:
            self.load()

    # --- persistence ---------------------------------------------------------------

    def load(self) -> int:
        """(Re)read the store from disk. A missing file is an empty store, not an error."""
        self._records.clear()
        self._bboxes.clear()
        self._invalidate_index()
        if not self.path.exists():
            return 0

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        for feature in payload.get("features", []):
            boundary = feature_to_boundary(feature)
            self._index(boundary)
        return len(self._records)

    def save(self) -> None:
        """Write the store atomically, so an interrupted write cannot truncate the file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_feature_collection()
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp", delete=False
        )
        try:
            with handle as tmp:
                json.dump(payload, tmp, ensure_ascii=False, indent=2)
                tmp.write("\n")
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(handle.name, self.path)
        except BaseException:
            Path(handle.name).unlink(missing_ok=True)
            raise

    def to_feature_collection(self) -> dict[str, Any]:
        """The exact JSON written to disk, including the demo notice when relevant."""
        boundaries = list(self._records.values())
        contains_demo = any(boundary.is_demo for boundary in boundaries)
        payload: dict[str, Any] = {"type": "FeatureCollection"}
        if contains_demo:
            payload["notice"] = config.DEMO_DATA_NOTICE
        payload["features"] = [boundary.to_feature() for boundary in boundaries]
        return payload

    # --- contract ------------------------------------------------------------------

    def add(self, boundary: GISBoundary, *, overwrite: bool = False) -> GISBoundary:
        if not overwrite and boundary.id in self._records:
            raise DuplicateBoundaryError(f"Boundary id {boundary.id!r} already exists.")
        self._index(boundary)
        self.save()
        return boundary

    def add_many(self, boundaries: Iterable[GISBoundary], *, overwrite: bool = False) -> int:
        staged = list(boundaries)
        if not overwrite:
            clashes = [b.id for b in staged if b.id in self._records]
            if clashes:
                raise DuplicateBoundaryError(f"Boundary ids already exist: {', '.join(sorted(clashes)[:5])}")
        for boundary in staged:
            self._index(boundary)
        self.save()
        return len(staged)

    def get(self, boundary_id: str) -> GISBoundary:
        try:
            return self._records[boundary_id]
        except KeyError as exc:
            raise BoundaryNotFoundError(f"No boundary with id {boundary_id!r}.") from exc

    def find(self, query: BoundaryQuery | None = None) -> list[GISBoundary]:
        query = query or BoundaryQuery()
        matches = sorted(self._matching(query), key=lambda b: (b.name.casefold(), b.id))
        window = matches[query.offset :]
        return window[: query.limit] if query.limit is not None else window

    def count(self, query: BoundaryQuery | None = None) -> int:
        return sum(1 for _ in self._matching(query or BoundaryQuery()))

    def delete(self, boundary_id: str) -> None:
        if boundary_id not in self._records:
            raise BoundaryNotFoundError(f"No boundary with id {boundary_id!r}.")
        del self._records[boundary_id]
        self._bboxes.pop(boundary_id, None)
        self._invalidate_index()
        self.save()

    def clear(self) -> int:
        removed = len(self._records)
        self._records.clear()
        self._bboxes.clear()
        self._invalidate_index()
        self.save()
        return removed

    # --- internals -----------------------------------------------------------------

    def _index(self, boundary: GISBoundary) -> None:
        self._records[boundary.id] = boundary
        self._bboxes[boundary.id] = boundary.geometry.bbox
        self._invalidate_index()

    def _invalidate_index(self) -> None:
        """Drop the spatial index. Rebuilt lazily, so a bulk import pays for one rebuild."""
        self._tree = None
        self._tree_ids = []

    def _spatial_index(self) -> tuple[STRtree, list[str]]:
        """Return the STRtree over stored geometries, building it if needed."""
        if self._tree is None:
            self._tree_ids = list(self._records)
            self._tree = STRtree([geo.to_shapely(self._records[i].geometry.as_mapping()) for i in self._tree_ids])
        return self._tree, self._tree_ids

    def bbox_candidates(self, bbox: geo.BBox) -> set[str]:
        """Ids whose geometry bounding box overlaps `bbox`, via the R-tree.

        This is a prefilter, not an answer: STRtree compares bounding boxes, so callers
        needing true intersection must still test the geometry itself. Being
        over-inclusive here is correct -- it can only add candidates, never drop a real
        one.
        """
        if not self._records:
            return set()
        tree, ids = self._spatial_index()
        envelope = box(*geo.normalize_bbox(bbox))
        return {ids[position] for position in tree.query(envelope)}

    def _matching(self, query: BoundaryQuery) -> Iterable[GISBoundary]:
        wanted = {c.value for c in query.categories} if query.categories else None
        state = query.state.casefold() if query.state else None
        district = query.district.casefold() if query.district else None
        needle = query.name_contains.casefold() if query.name_contains else None
        bbox = tuple(query.bbox) if query.bbox else None
        # One R-tree lookup up front, instead of a rectangle test per record.
        candidates = self.bbox_candidates(bbox) if bbox is not None else None

        for boundary in self._records.values():
            if candidates is not None and boundary.id not in candidates:
                continue
            if wanted is not None and boundary.category.value not in wanted:
                continue
            if state is not None and boundary.state.casefold() != state:
                continue
            if district is not None and (boundary.district or "").casefold() != district:
                continue
            if needle is not None and needle not in boundary.name.casefold():
                continue
            if not query.include_demo and boundary.is_demo:
                continue
            yield boundary


def feature_to_boundary(feature: dict[str, Any]) -> GISBoundary:
    """Rebuild a `GISBoundary` from the GeoJSON Feature form written by `to_feature`.

    Derived properties (`category_label`, `area_sqkm`, `is_demo`, `data_notice`) are
    recomputed rather than trusted, so hand-editing the store cannot desynchronise them
    from the geometry.
    """
    properties = feature.get("properties") or {}
    return GISBoundary(
        id=str(properties.get("id") or feature.get("id") or ""),
        name=properties.get("name", ""),
        category=properties.get("category"),
        state=properties.get("state", ""),
        district=properties.get("district"),
        geometry=feature.get("geometry"),
        source=properties.get("source", ""),
        source_url=properties.get("source_url"),
        last_updated=properties.get("last_updated"),
        metadata=properties.get("metadata") or {},
    )


# ---------------------------------------------------------------------------------
# PostGIS backend
# ---------------------------------------------------------------------------------

SELECT_COLUMNS = (
    "id, name, category, state, district, ST_AsGeoJSON(geometry) AS geometry, "
    "source, source_url, last_updated, metadata"
)


class PostGISBoundaryRepository(GISBoundaryRepository):
    """The same contract backed by PostgreSQL + PostGIS.

    Requires the schema in migrations/0001_create_gis_boundaries.sql and the `psycopg`
    (v3) driver. Geometry crosses the boundary as GeoJSON in both directions --
    `ST_GeomFromEWKT` on write, `ST_AsGeoJSON` on read -- so callers hold the identical
    `GISBoundary` objects the file backend produces.

    NOTE: this backend is wired and SQL-complete but has not been exercised against a
    live PostGIS instance in this environment (no server is installed here); the file
    backend is what the test suite covers.
    """

    backend = "postgis"

    def __init__(self, dsn: str, *, table: str = "gis_boundaries") -> None:
        try:
            import psycopg  # noqa: PLC0415  (optional dependency, imported on demand)
            from psycopg.rows import dict_row  # noqa: PLC0415
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "PostGISBoundaryRepository needs the 'psycopg[binary]' driver. "
                "Install the GIS extras: pip install -r requirements-gis.txt"
            ) from exc

        self._psycopg = psycopg
        self._row_factory = dict_row
        self._dsn = dsn
        # Table name is interpolated, never parameterized (SQL identifiers cannot be
        # bound), so it is restricted to a safe identifier shape.
        if not table.replace("_", "").isalnum():
            raise ValueError(f"Unsafe table name {table!r}.")
        self.table = table

    def _connect(self):  # pragma: no cover - requires a live database
        return self._psycopg.connect(self._dsn, row_factory=self._row_factory)

    def add(self, boundary: GISBoundary, *, overwrite: bool = False) -> GISBoundary:  # pragma: no cover
        self.add_many([boundary], overwrite=overwrite)
        return boundary

    def add_many(self, boundaries: Iterable[GISBoundary], *, overwrite: bool = False) -> int:  # pragma: no cover
        staged = list(boundaries)
        if not staged:
            return 0
        conflict = (
            """ON CONFLICT (id) DO UPDATE SET
                   name = EXCLUDED.name, category = EXCLUDED.category, state = EXCLUDED.state,
                   district = EXCLUDED.district, geometry = EXCLUDED.geometry, source = EXCLUDED.source,
                   source_url = EXCLUDED.source_url, last_updated = EXCLUDED.last_updated,
                   metadata = EXCLUDED.metadata, updated_at = now()"""
            if overwrite
            else "ON CONFLICT (id) DO NOTHING"
        )
        sql = f"""
            INSERT INTO {self.table}
                (id, name, category, state, district, geometry, source, source_url, last_updated, metadata)
            VALUES (%s, %s, %s, %s, %s, ST_GeomFromEWKT(%s), %s, %s, %s, %s)
            {conflict}
        """
        rows = [
            (
                b.id,
                b.name,
                b.category.value,
                b.state,
                b.district,
                geo.geometry_to_ewkt(b.geometry.as_mapping()),
                b.source,
                b.source_url,
                b.last_updated,
                json.dumps(b.metadata),
            )
            for b in staged
        ]
        with self._connect() as conn, conn.cursor() as cur:
            cur.executemany(sql, rows)
            conn.commit()
        return len(staged)

    def get(self, boundary_id: str) -> GISBoundary:  # pragma: no cover
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT {SELECT_COLUMNS} FROM {self.table} WHERE id = %s", (boundary_id,))
            row = cur.fetchone()
        if row is None:
            raise BoundaryNotFoundError(f"No boundary with id {boundary_id!r}.")
        return row_to_boundary(row)

    def find(self, query: BoundaryQuery | None = None) -> list[GISBoundary]:  # pragma: no cover
        query = query or BoundaryQuery()
        where, params = build_where_clause(query)
        sql = f"SELECT {SELECT_COLUMNS} FROM {self.table} {where} ORDER BY lower(name), id"
        if query.limit is not None:
            sql += " LIMIT %s"
            params.append(query.limit)
        if query.offset:
            sql += " OFFSET %s"
            params.append(query.offset)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [row_to_boundary(row) for row in rows]

    def count(self, query: BoundaryQuery | None = None) -> int:  # pragma: no cover
        where, params = build_where_clause(query or BoundaryQuery())
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT count(*) AS n FROM {self.table} {where}", params)
            row = cur.fetchone()
        return int(row["n"]) if row else 0

    def delete(self, boundary_id: str) -> None:  # pragma: no cover
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f"DELETE FROM {self.table} WHERE id = %s", (boundary_id,))
            deleted = cur.rowcount
            conn.commit()
        if not deleted:
            raise BoundaryNotFoundError(f"No boundary with id {boundary_id!r}.")

    def clear(self) -> int:  # pragma: no cover
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f"DELETE FROM {self.table}")
            removed = cur.rowcount
            conn.commit()
        return int(removed)


def build_where_clause(query: BoundaryQuery) -> tuple[str, list[Any]]:
    """Translate a `BoundaryQuery` into a parameterized SQL WHERE clause.

    Split out from the repository so it is unit-testable without a database -- the SQL
    shape is verified in tests/test_gis_boundaries.py even though execution is not.
    """
    clauses: list[str] = []
    params: list[Any] = []

    if query.categories:
        clauses.append("category = ANY(%s)")
        params.append([c.value for c in query.categories])
    if query.state:
        clauses.append("lower(state) = lower(%s)")
        params.append(query.state)
    if query.district:
        clauses.append("lower(district) = lower(%s)")
        params.append(query.district)
    if query.name_contains:
        clauses.append("name ILIKE %s")
        params.append(f"%{query.name_contains}%")
    if not query.include_demo:
        # Records carry their demo flag inside the metadata JSONB column.
        clauses.append("COALESCE((metadata->>'is_demo')::boolean, false) = false")
    if query.bbox:
        # `&&` is the bbox-overlap operator the GiST index on geometry serves directly.
        clauses.append("geometry && ST_MakeEnvelope(%s, %s, %s, %s, 4326)")
        params.extend(query.bbox)

    return ("WHERE " + " AND ".join(clauses) if clauses else "", params)


def row_to_boundary(row: dict[str, Any]) -> GISBoundary:
    """Build a `GISBoundary` from a PostGIS result row (geometry as ST_AsGeoJSON text)."""
    metadata = row.get("metadata")
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    geometry = row["geometry"]
    if isinstance(geometry, str):
        geometry = json.loads(geometry)
    return GISBoundary(
        id=row["id"],
        name=row["name"],
        category=row["category"],
        state=row["state"],
        district=row.get("district"),
        geometry=geometry,
        source=row["source"],
        source_url=row.get("source_url"),
        last_updated=row["last_updated"],
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------------


def build_repository(
    *, dsn: str | None = None, path: Path | str | None = None
) -> GISBoundaryRepository:
    """Return the best available backend.

    PostGIS when a DSN is configured (argument or DATABASE_URL) and psycopg is importable;
    the GeoJSON file store otherwise. The fallback is logged through the returned object's
    `backend` attribute rather than by failing, so the data layer works out of the box on
    a machine with no database.
    """
    dsn = dsn or os.environ.get("DATABASE_URL") or None
    if dsn:
        try:
            return PostGISBoundaryRepository(dsn)
        except RuntimeError:
            # psycopg missing: fall through to the file store rather than crash startup.
            pass
    return GeoJSONFileBoundaryRepository(path or config.BOUNDARY_STORE_PATH)
