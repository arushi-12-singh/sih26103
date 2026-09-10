from __future__ import annotations

from pathlib import Path
import pytest
import shapely.geometry as sg

from app.repositories.gis_boundary_repository import GISBoundaryRepository
from app.schemas.gis_boundary import BoundaryCategory, GISBoundary
from app.services.gis_importer import GISDataImporter

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "gis"


@pytest.fixture
def sample_repository() -> GISBoundaryRepository:
    repo = GISBoundaryRepository()
    repo.clear()

    b1 = GISBoundary(
        id="TEST-01",
        name="Test Sanctuary",
        category=BoundaryCategory.WILDLIFE_SANCTUARY,
        state="Uttar Pradesh",
        district="Test District",
        geometry=sg.mapping(sg.Polygon([(80.0, 28.0), (81.0, 28.0), (81.0, 29.0), (80.0, 29.0), (80.0, 28.0)])),
        source="DEMO DATA — NOT OFFICIAL BOUNDARIES",
        is_demo=True,
    )
    b2 = GISBoundary(
        id="TEST-02",
        name="Test Wetland",
        category=BoundaryCategory.RAMSAR_WETLAND,
        state="Odisha",
        district="Puri",
        geometry=sg.mapping(sg.Polygon([(85.0, 19.0), (86.0, 19.0), (86.0, 20.0), (85.0, 20.0), (85.0, 19.0)])),
        source="DEMO DATA — NOT OFFICIAL BOUNDARIES",
        is_demo=True,
    )
    repo.add_many([b1, b2])
    return repo


def test_repository_add_and_get(sample_repository: GISBoundaryRepository) -> None:
    assert sample_repository.count() == 2
    b1 = sample_repository.get_by_id("TEST-01")
    assert b1 is not None
    assert b1.name == "Test Sanctuary"
    assert b1.category == BoundaryCategory.WILDLIFE_SANCTUARY


def test_repository_filter_by_category(sample_repository: GISBoundaryRepository) -> None:
    sanctuaries = sample_repository.filter_by_category(BoundaryCategory.WILDLIFE_SANCTUARY)
    assert len(sanctuaries) == 1
    assert sanctuaries[0].id == "TEST-01"

    wetlands = sample_repository.filter_by_category(BoundaryCategory.RAMSAR_WETLAND)
    assert len(wetlands) == 1
    assert wetlands[0].id == "TEST-02"


def test_repository_filter_by_state(sample_repository: GISBoundaryRepository) -> None:
    up_items = sample_repository.filter_by_state("Uttar Pradesh")
    assert len(up_items) == 1
    assert up_items[0].id == "TEST-01"


def test_repository_spatial_query(sample_repository: GISBoundaryRepository) -> None:
    # Query point inside b1 bounds (80.5, 28.5)
    query_point = sg.Point(80.5, 28.5)
    candidates = sample_repository.query_spatial_candidates(query_point)
    assert len(candidates) >= 1
    assert any(c.id == "TEST-01" for c in candidates)


def test_repository_json_persistence(tmp_path: Path) -> None:
    store_file = tmp_path / "test_boundary_store.json"
    repo = GISBoundaryRepository(persistence_file=store_file)

    demo_file = DATA_DIR / "demo_boundaries.geojson"
    boundaries, _ = GISDataImporter.import_file(demo_file)
    repo.add_many(boundaries)

    saved_path = repo.save_to_json()
    assert saved_path.exists()

    new_repo = GISBoundaryRepository(persistence_file=store_file)
    count = new_repo.load_from_json()
    assert count == 7
    assert new_repo.get_by_id("DEMO-PA-001") is not None
