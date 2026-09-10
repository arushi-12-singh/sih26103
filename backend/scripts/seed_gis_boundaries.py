#!/usr/bin/env python3
"""Seed script for importing and validating GIS environmental/restricted boundary layers."""

from __future__ import annotations

import sys
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parents[1]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.repositories.gis_boundary_repository import GISBoundaryRepository
from app.services.gis_importer import GISDataImporter


def main() -> None:
    data_dir = backend_dir / "data" / "gis"
    demo_file = data_dir / "demo_boundaries.geojson"
    official_file = data_dir / "protected_areas_india.geojson"

    repo = GISBoundaryRepository(persistence_file=data_dir / "boundary_store.json")
    repo.clear()

    total_imported = 0

    if demo_file.exists():
        print(f"Loading DEMO boundary dataset from: {demo_file}")
        boundaries, result = GISDataImporter.import_file(
            demo_file,
            default_source="DEMO DATA — NOT OFFICIAL BOUNDARIES",
            is_demo=True,
        )
        count = repo.add_many(boundaries)
        total_imported += count
        print(f"  -> Imported {result.imported_count}/{result.total_records} demo records ({result.repaired_count} repaired, {result.rejected_count} rejected)")
        if result.errors:
            for err in result.errors:
                print(f"     [Warning] {err}")

    if official_file.exists():
        print(f"\nLoading protected areas dataset from: {official_file}")
        boundaries, result = GISDataImporter.import_file(
            official_file,
            default_source="MoEFCC / WII Protected Areas Database",
            is_demo=False,
        )
        count = repo.add_many(boundaries)
        total_imported += count
        print(f"  -> Imported {result.imported_count}/{result.total_records} protected area records ({result.repaired_count} repaired, {result.rejected_count} rejected)")

    saved_path = repo.save_to_json()
    print(f"\nSuccessfully seeded {repo.count()} GIS boundaries into repository storage:")
    print(f"Saved store to: {saved_path}")

    # Summary by Category
    print("\n--- Summary by Boundary Category ---")
    all_b = repo.get_all()
    categories = sorted(set(b.category.value for b in all_b))
    for cat in categories:
        cat_items = repo.filter_by_category(cat)
        print(f"  • {cat:22s}: {len(cat_items)} boundaries")


if __name__ == "__main__":
    main()
