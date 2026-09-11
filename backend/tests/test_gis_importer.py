from __future__ import annotations

import json
from pathlib import Path
import pytest
import shapely.geometry as sg

from app.schemas.gis_boundary import BoundaryCategory, GISBoundary
from app.services.gis_importer import GISDataImporter
from app.services.gis_validator import validate_and_repair_geometry

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "gis"


def test_boundary_category_parsing() -> None:
    assert BoundaryCategory.parse_category("WILDLIFE_SANCTUARY") == BoundaryCategory.WILDLIFE_SANCTUARY
    assert BoundaryCategory.parse_category("Jim Corbett National Park") == BoundaryCategory.NATIONAL_PARK
    assert BoundaryCategory.parse_category("Chilika Ramsar Wetland") == BoundaryCategory.RAMSAR_WETLAND
    assert BoundaryCategory.parse_category("Western Ghats ESZ Corridor") == BoundaryCategory.ECO_SENSITIVE_ZONE
    assert BoundaryCategory.parse_category("Dudhwa Tiger Reserve") == BoundaryCategory.TIGER_RESERVE
    assert BoundaryCategory.parse_category("Random Restricted Zone") == BoundaryCategory.OTHER_RESTRICTED_ZONE


def test_geometry_validation_and_repair() -> None:
    # Valid polygon
    poly = sg.Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
    geom, geojson_dict, was_repaired = validate_and_repair_geometry(poly)
    assert geom.is_valid
    assert geojson_dict["type"] == "Polygon"
    assert was_repaired is False

    # Self-intersecting bowtie polygon (invalid)
    invalid_poly = sg.Polygon([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)])
    assert not invalid_poly.is_valid
    geom, geojson_dict, was_repaired = validate_and_repair_geometry(invalid_poly)
    assert geom.is_valid
    assert was_repaired is True


def test_geometry_validation_rejects_point() -> None:
    point = sg.Point(77.0, 28.0)
    with pytest.raises(ValueError, match="Only Polygon and MultiPolygon are allowed"):
        validate_and_repair_geometry(sg.mapping(point))


def test_import_demo_boundaries_file() -> None:
    demo_file = DATA_DIR / "demo_boundaries.geojson"
    assert demo_file.exists()

    boundaries, result = GISDataImporter.import_file(demo_file)
    assert result.total_records == 7
    assert result.imported_count == 7
    assert result.rejected_count == 0
    assert len(boundaries) == 7

    for b in boundaries:
        assert isinstance(b, GISBoundary)
        assert b.is_demo is True
        assert b.source == "DEMO DATA — NOT OFFICIAL BOUNDARIES"
        assert b.category in list(BoundaryCategory)
