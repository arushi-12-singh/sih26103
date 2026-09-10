"""Seed / import GIS boundaries into the configured store.

By default this loads data/gis/demo_boundaries.geojson -- FICTIONAL test geometry that is
stamped as demo data on the way in and must never be presented as official government
boundaries. Point it at a real file to import a licensed dataset instead.

Usage:
    python scripts/seed_gis_boundaries.py                     # seed the demo dataset
    python scripts/seed_gis_boundaries.py --reset             # wipe the store first
    python scripts/seed_gis_boundaries.py --status            # report what is stored
    python scripts/seed_gis_boundaries.py --dry-run           # validate, write nothing

    # Import an official dataset (GeoJSON / Shapefile / GeoPackage):
    python scripts/seed_gis_boundaries.py \
        --file /path/to/protected_areas.shp \
        --source "State Forest Department" \
        --source-url https://example.gov.in/datasets/protected-areas \
        --last-updated 2026-04-01 \
        --default-state "Madhya Pradesh"

Runs against whichever backend is configured: PostGIS when DATABASE_URL is set, the
GeoJSON file store otherwise. Re-running is safe -- ids are deterministic, so an import
updates existing records rather than duplicating them.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import gis_config as config  # noqa: E402
from app.schemas.boundary import BoundaryCategory  # noqa: E402
from app.services.boundary_importer import BoundaryImporter, BoundaryImportError  # noqa: E402
from app.services.gis_boundary_service import build_gis_boundary_service  # noqa: E402


def _print_status(service) -> None:
    stats = service.stats()
    print(f"Backend        : {stats['backend']}  (CRS {stats['storage_crs']})")
    print(f"Boundaries     : {stats['total']}  ({stats['demo_records']} demo, {stats['official_records']} official)")
    if stats["by_category"]:
        print("By category    :")
        for category, count in stats["by_category"].items():
            print(f"  {BoundaryCategory(category).label:<22} {count}")
    if stats["by_state"]:
        print("By state       : " + ", ".join(f"{state} ({count})" for state, count in stats["by_state"].items()))
    if stats["notice"]:
        print(f"\n  *** {stats['notice']} ***")
        print("  Demo records are fictional test geometry and must not be used for any real")
        print("  clearance, siting, or compliance decision.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed or import GIS boundaries.")
    parser.add_argument("--file", default=None, help=f"Dataset to import (default: {config.DEMO_DATASET_PATH}).")
    parser.add_argument("--source", default=None, help="Attribution recorded on every imported record.")
    parser.add_argument("--source-url", default=None, help="URL the dataset was obtained from.")
    parser.add_argument("--last-updated", default=None, help="ISO date the source dataset was last updated.")
    parser.add_argument("--default-state", default=None, help="State to use when a feature has none.")
    parser.add_argument("--default-category", default=None, choices=[c.value for c in BoundaryCategory],
                        help="Category to use when a feature's category is missing.")
    parser.add_argument("--layer", default=None, help="Layer name (GeoPackage with multiple layers).")
    parser.add_argument("--official", action="store_true",
                        help="Mark records as official. Only use with a genuinely official/licensed dataset.")
    parser.add_argument("--reset", action="store_true", help="Delete all stored boundaries before importing.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and report without writing.")
    parser.add_argument("--status", action="store_true", help="Report what is stored, then exit.")
    args = parser.parse_args()

    service = build_gis_boundary_service()

    if args.status:
        _print_status(service)
        return 0

    is_demo_dataset = args.file is None
    path = Path(args.file) if args.file else config.DEMO_DATASET_PATH
    if is_demo_dataset and args.official:
        print("Refusing --official on the built-in demo dataset: it is fictional geometry.", file=sys.stderr)
        return 2

    source = args.source or (config.DEMO_SOURCE_NAME if is_demo_dataset else None)
    if not source:
        print("--source is required when importing your own dataset (attribution is mandatory).", file=sys.stderr)
        return 2

    last_updated = date.fromisoformat(args.last_updated) if args.last_updated else date.today()
    mark_demo = is_demo_dataset or not args.official

    if args.reset and not args.dry_run:
        removed = service.clear()
        print(f"Cleared {removed} existing boundaries.")

    if mark_demo:
        print(f"*** {config.DEMO_DATA_NOTICE} ***")
        if not is_demo_dataset:
            print("    (pass --official once you have verified this dataset is official/licensed)")

    importer = BoundaryImporter(service)
    try:
        report = importer.import_file(
            path,
            source=source,
            source_url=args.source_url,
            last_updated=last_updated,
            default_state=args.default_state,
            default_category=args.default_category,
            is_demo=mark_demo,
            overwrite=True,
            dry_run=args.dry_run,
            layer=args.layer,
        )
    except BoundaryImportError as exc:
        print(f"Import failed: {exc}", file=sys.stderr)
        return 1

    print(report.summary())
    for rejection in report.rejected:
        print(f"  REJECTED [{rejection.index}] {rejection.name or '<unnamed>'}: {rejection.reason}", file=sys.stderr)
    if args.dry_run:
        print("(dry run -- nothing was written)")

    print()
    _print_status(service)
    return 1 if report.rejected else 0


if __name__ == "__main__":
    raise SystemExit(main())
