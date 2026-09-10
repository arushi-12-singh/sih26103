"""Apply the GIS data-layer migrations.

Two backends, one command:

  * DATABASE_URL set + psycopg installed -> applies every migrations/*.sql not already
    recorded in `schema_migrations`, in filename order, each in its own transaction.
  * Otherwise -> initializes the GeoJSON file store (creates data/gis/ and an empty
    FeatureCollection), which is the file backend's equivalent of a schema migration.

Usage:
    python scripts/migrate.py                 # apply pending migrations
    python scripts/migrate.py --status        # report without changing anything
    python scripts/migrate.py --database-url postgresql://user@host/db
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import gis_config as config  # noqa: E402


def _migration_files() -> list[Path]:
    return sorted(config.MIGRATIONS_DIR.glob("*.sql"))


def _init_file_store() -> int:
    """Create the file backend's store if it does not exist. Returns 1 if created."""
    config.GIS_DATA_DIR.mkdir(parents=True, exist_ok=True)
    if config.BOUNDARY_STORE_PATH.exists():
        return 0
    config.BOUNDARY_STORE_PATH.write_text(
        json.dumps({"type": "FeatureCollection", "features": []}, indent=2) + "\n", encoding="utf-8"
    )
    return 1


def _applied_versions(cur) -> set[str]:
    cur.execute(
        "SELECT to_regclass('public.schema_migrations') IS NOT NULL AS present"
    )
    row = cur.fetchone()
    if not row or not row[0]:
        return set()
    cur.execute("SELECT version FROM schema_migrations")
    return {r[0] for r in cur.fetchall()}


def _apply_postgres(dsn: str, *, status_only: bool) -> int:
    try:
        import psycopg
    except ModuleNotFoundError:
        print(
            "DATABASE_URL is set but psycopg is not installed.\n"
            "  Install the GIS extras:  pip install -r requirements-gis.txt\n"
            "  Or apply the SQL by hand: psql \"$DATABASE_URL\" -f migrations/0001_create_gis_boundaries.sql",
            file=sys.stderr,
        )
        return 1

    files = _migration_files()
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        applied = _applied_versions(cur)
        pending = [path for path in files if path.stem not in applied]

        print(f"Backend : postgis ({conn.info.host or 'local'}/{conn.info.dbname})")
        print(f"Applied : {len(applied)} | Pending: {len(pending)}")
        if status_only:
            for path in pending:
                print(f"  pending  {path.name}")
            return 0

        for path in pending:
            print(f"  applying {path.name} ...", end=" ", flush=True)
            # Each file wraps itself in BEGIN/COMMIT and records its own version row.
            cur.execute(path.read_text(encoding="utf-8"))
            print("ok")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply GIS boundary data-layer migrations.")
    parser.add_argument("--database-url", default=None, help="PostGIS DSN (defaults to $DATABASE_URL).")
    parser.add_argument("--status", action="store_true", help="Report pending work without applying it.")
    args = parser.parse_args()

    dsn = args.database_url or os.environ.get("DATABASE_URL")
    if dsn:
        return _apply_postgres(dsn, status_only=args.status)

    print("Backend : geojson-file (no DATABASE_URL configured)")
    print(f"Store   : {config.BOUNDARY_STORE_PATH}")
    if args.status:
        print("  exists" if config.BOUNDARY_STORE_PATH.exists() else "  pending: store not created")
        print(f"  {len(_migration_files())} SQL migration(s) available for a future PostGIS backend.")
        return 0
    created = _init_file_store()
    print("  created empty store" if created else "  already initialized")
    print("\nTo migrate to PostGIS later: set DATABASE_URL, re-run this script, then re-run")
    print("scripts/seed_gis_boundaries.py. No application code changes are needed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
