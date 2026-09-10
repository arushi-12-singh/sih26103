"""Unit tests for the GIS boundary data layer (Feature 4).

Covers the four things that must not silently break:
  1. geometry validation accepts what it should and REJECTS what it should;
  2. the repository contract (CRUD, filtering, persistence round-trip);
  3. the importer (CRS detection/transformation, attribution, per-feature rejection);
  4. the demo-data guarantee -- fictional geometry always carries its notice.

Every test uses a temporary store, so nothing here touches data/gis/boundaries.geojson.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app.config import gis_config as config
from app.models import boundary_geometry as geo
from app.models.boundary_geometry import GeometryValidationError
from app.repositories.gis_boundary_repository import (
    BoundaryNotFoundError,
    DuplicateBoundaryError,
    GeoJSONFileBoundaryRepository,
    build_repository,
    build_where_clause,
    feature_to_boundary,
    row_to_boundary,
)
from app.schemas.boundary import (
    BoundaryCategory,
    BoundaryQuery,
    GISBoundary,
)
from app.services.boundary_importer import (
    BoundaryImporter,
    BoundaryImportError,
    FieldMapping,
    transform_geometry,
)
from app.services.gis_boundary_service import (
    GISBoundaryService,
    make_boundary_id,
    mark_as_demo,
)

DEMO_DATASET = config.DEMO_DATASET_PATH


def square(west: float, south: float, size: float = 0.1) -> dict:
    east, north = west + size, south + size
    return {
        "type": "Polygon",
        "coordinates": [[[west, south], [east, south], [east, north], [west, north], [west, south]]],
    }


@pytest.fixture
def service(tmp_path: Path) -> GISBoundaryService:
    """A service backed by an isolated, empty file store."""
    return GISBoundaryService(GeoJSONFileBoundaryRepository(tmp_path / "boundaries.geojson"))


@pytest.fixture
def seeded(service: GISBoundaryService) -> GISBoundaryService:
    """The service loaded with the demo dataset, exactly as the seed script does."""
    BoundaryImporter(service).import_file(
        DEMO_DATASET, source=config.DEMO_SOURCE_NAME, last_updated=date(2026, 9, 10), is_demo=True
    )
    return service


# ---------------------------------------------------------------------------------
# Geometry validation
# ---------------------------------------------------------------------------------


class TestGeometryValidation:
    def test_accepts_polygon_and_multipolygon(self):
        for geometry in (square(78.0, 21.0), {"type": "MultiPolygon", "coordinates": [square(78.0, 21.0)["coordinates"], square(79.0, 21.0)["coordinates"]]}):
            assert geo.validate_geometry(geometry)["type"] == geometry["type"]

    def test_unclosed_ring_is_closed_not_rejected(self):
        """Exporters routinely drop the repeated closing position; that is recoverable."""
        unclosed = {"type": "Polygon", "coordinates": [[[78.0, 21.0], [78.1, 21.0], [78.1, 21.1], [78.0, 21.1]]]}
        ring = geo.validate_geometry(unclosed)["coordinates"][0]
        assert ring[0] == ring[-1]
        assert len(ring) == 5

    def test_rings_are_rewound_to_rfc7946_orientation(self):
        """Exterior rings come back counterclockwise regardless of input winding."""
        clockwise = {
            "type": "Polygon",
            "coordinates": [[[78.0, 21.0], [78.0, 21.1], [78.1, 21.1], [78.1, 21.0], [78.0, 21.0]]],
        }
        ring = geo.validate_geometry(clockwise)["coordinates"][0]
        signed_area = sum(
            (ring[i + 1][0] - ring[i][0]) * (ring[i + 1][1] + ring[i][1]) for i in range(len(ring) - 1)
        )
        assert signed_area < 0, "shoelace sign indicates the exterior ring is not counterclockwise"

    def test_elevation_is_dropped(self):
        geometry = {"type": "Polygon", "coordinates": [[[78.0, 21.0, 400], [78.1, 21.0, 410], [78.1, 21.1, 420], [78.0, 21.0, 400]]]}
        assert all(len(position) == 2 for position in geo.validate_geometry(geometry)["coordinates"][0])

    @pytest.mark.parametrize(
        "geometry, expected",
        [
            ({"type": "Point", "coordinates": [78.0, 21.0]}, "Unsupported geometry type"),
            ({"type": "LineString", "coordinates": [[78.0, 21.0], [78.1, 21.1]]}, "Unsupported geometry type"),
            ({"type": "Polygon", "coordinates": []}, "non-empty array"),
            ({"type": "Polygon", "coordinates": [[[78.0, 21.0], [78.1, 21.0], [78.0, 21.0]]]}, "needs at least 4"),
            ({"type": "Polygon", "coordinates": [[[78.0, 21.0], [78.1, 21.0], [78.0, 21.0], [78.0, 21.0]]]}, "degenerate"),
            ({"type": "Polygon", "coordinates": [[[0, 0], [2, 2], [2, 0], [0, 2], [0, 0]]]}, "Invalid geometry"),
            ({"type": "Polygon", "coordinates": [[[200.0, 21.0], [201.0, 21.0], [201.0, 22.0], [200.0, 21.0]]]}, "longitude"),
            ({"type": "Polygon", "coordinates": [[[78.0, 95.0], [78.1, 95.0], [78.1, 96.0], [78.0, 95.0]]]}, "latitude"),
            ({"type": "Polygon", "coordinates": [[["a", "b"], [78.1, 21.0], [78.1, 21.1], ["a", "b"]]]}, "must be numbers"),
            (None, "must be a GeoJSON object"),
        ],
    )
    def test_rejects_invalid_geometry(self, geometry, expected):
        with pytest.raises(GeometryValidationError, match=expected):
            geo.validate_geometry(geometry)

    def test_rejects_geometry_larger_than_the_configured_maximum(self):
        """A whole-globe polygon is a units/CRS mistake, not a protected area."""
        with pytest.raises(GeometryValidationError, match="exceeds the maximum"):
            geo.validate_geometry({
                "type": "Polygon",
                "coordinates": [[[-170.0, -80.0], [170.0, -80.0], [170.0, 80.0], [-170.0, 80.0], [-170.0, -80.0]]],
            })

    def test_self_intersecting_polygon_is_rejected_not_repaired(self):
        """Policy: reject invalid geometry. Never silently make_valid() it."""
        bowtie = {"type": "Polygon", "coordinates": [[[0.0, 0.0], [1.0, 1.0], [1.0, 0.0], [0.0, 1.0], [0.0, 0.0]]]}
        with pytest.raises(GeometryValidationError, match="Self-intersection"):
            geo.validate_geometry(bowtie)

    def test_hole_area_is_subtracted(self):
        outer = square(78.0, 21.0, size=0.4)["coordinates"][0]
        inner = list(reversed(square(78.1, 21.1, size=0.2)["coordinates"][0]))
        with_hole = geo.geometry_area_sqkm({"type": "Polygon", "coordinates": [outer, inner]})
        without_hole = geo.geometry_area_sqkm({"type": "Polygon", "coordinates": [outer]})
        assert with_hole < without_hole

    def test_bbox_and_centroid(self):
        geometry = square(78.0, 21.0, size=0.2)
        assert geo.geometry_bbox(geometry) == pytest.approx((78.0, 21.0, 78.2, 21.2))
        assert geo.geometry_centroid(geometry) == pytest.approx((78.1, 21.1))

    def test_ewkt_carries_the_srid_postgis_expects(self):
        assert geo.geometry_to_ewkt(square(78.0, 21.0)).startswith("SRID=4326;POLYGON")

    def test_normalize_bbox_orders_and_validates(self):
        assert geo.normalize_bbox([78.2, 21.2, 78.0, 21.0]) == (78.0, 21.0, 78.2, 21.2)
        with pytest.raises(GeometryValidationError):
            geo.normalize_bbox([0.0, 0.0, 500.0, 0.0])


# ---------------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------------


class TestBoundarySchema:
    def test_every_category_has_a_label_and_is_in_the_migration(self):
        """Python enum, config labels, and the PostGIS enum must not drift apart."""
        sql = (config.MIGRATIONS_DIR / "0001_create_gis_boundaries.sql").read_text(encoding="utf-8")
        for category in BoundaryCategory:
            assert category.value in config.CATEGORY_LABELS
            assert f"'{category.value}'" in sql, f"{category.value} missing from the PostGIS enum"

    def test_boundary_exposes_the_ten_required_fields(self):
        required = {
            "id", "name", "category", "state", "district",
            "geometry", "source", "source_url", "last_updated", "metadata",
        }
        assert set(GISBoundary.model_fields) == required

    def test_invalid_geometry_cannot_be_instantiated(self):
        with pytest.raises(ValueError):
            GISBoundary(
                id="x", name="n", category=BoundaryCategory.FOREST, state="s",
                geometry={"type": "Polygon", "coordinates": [[[0, 0], [2, 2], [2, 0], [0, 2], [0, 0]]]},
                source="src", last_updated=date(2026, 1, 1),
            )

    def test_demo_flag_and_notice_ride_along_with_the_feature(self):
        boundary = GISBoundary(
            id="x", name="Demo", category=BoundaryCategory.FOREST, state="DEMO STATE",
            geometry=square(78.0, 21.0), source="demo", last_updated=date(2026, 1, 1),
            metadata=mark_as_demo(),
        )
        assert boundary.is_demo
        assert boundary.to_feature()["properties"]["data_notice"] == config.DEMO_DATA_NOTICE

    def test_ids_are_deterministic_so_reimport_updates_rather_than_duplicates(self):
        first = make_boundary_id("Source A", "Demo National Park", BoundaryCategory.NATIONAL_PARK)
        assert first == make_boundary_id(" source a ", "demo national park", "NATIONAL_PARK")
        assert first != make_boundary_id("Source B", "Demo National Park", BoundaryCategory.NATIONAL_PARK)


# ---------------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------------


class TestRepository:
    def test_crud_round_trip(self, service: GISBoundaryService):
        created = service.create_boundary(
            name="Demo Forest", category="FOREST", state="DEMO STATE", district="Demo North District",
            geometry=square(78.0, 21.0), source="demo", last_updated=date(2026, 1, 1), is_demo=True,
        )
        assert service.get_boundary(created.id).name == "Demo Forest"
        service.delete_boundary(created.id)
        with pytest.raises(BoundaryNotFoundError):
            service.get_boundary(created.id)

    def test_duplicate_id_is_rejected_unless_overwrite(self, service: GISBoundaryService):
        kwargs = dict(
            name="Demo Forest", category="FOREST", state="DEMO STATE",
            geometry=square(78.0, 21.0), source="demo", last_updated=date(2026, 1, 1), is_demo=True,
        )
        service.create_boundary(**kwargs)
        with pytest.raises(DuplicateBoundaryError):
            service.create_boundary(**kwargs)
        assert service.create_boundary(**kwargs, overwrite=True)
        assert service.count() == 1

    def test_store_survives_a_reload_from_disk(self, tmp_path: Path):
        path = tmp_path / "boundaries.geojson"
        first = GISBoundaryService(GeoJSONFileBoundaryRepository(path))
        first.create_boundary(
            name="Demo Wetland", category="RAMSAR_WETLAND", state="DEMO STATE",
            geometry=square(78.9, 20.6, size=0.08), source="demo",
            last_updated=date(2026, 1, 1), metadata={"note": "kept"}, is_demo=True,
        )
        reloaded = GISBoundaryService(GeoJSONFileBoundaryRepository(path)).find()
        assert len(reloaded) == 1
        assert reloaded[0].metadata["note"] == "kept"
        assert reloaded[0].geometry.type == "Polygon"

    def test_written_file_is_valid_geojson_with_the_demo_notice(self, seeded: GISBoundaryService):
        payload = json.loads(Path(seeded.repository.path).read_text(encoding="utf-8"))
        assert payload["type"] == "FeatureCollection"
        assert payload["notice"] == config.DEMO_DATA_NOTICE
        assert all(feature["type"] == "Feature" for feature in payload["features"])

    def test_atomic_save_leaves_no_temp_files(self, seeded: GISBoundaryService):
        directory = Path(seeded.repository.path).parent
        assert not list(directory.glob("*.tmp"))

    @pytest.mark.parametrize(
        "query, expected",
        [
            (BoundaryQuery(), 6),
            (BoundaryQuery(categories=[BoundaryCategory.NATIONAL_PARK]), 1),
            (BoundaryQuery(categories=[BoundaryCategory.FOREST, BoundaryCategory.TIGER_RESERVE]), 2),
            (BoundaryQuery(state="demo state"), 6),          # case-insensitive
            (BoundaryQuery(state="Nowhere"), 0),
            (BoundaryQuery(district="Demo South District"), 2),
            (BoundaryQuery(name_contains="tiger"), 1),
            (BoundaryQuery(include_demo=False), 0),          # the dataset is entirely demo data
            (BoundaryQuery(bbox=[79.0, 21.0, 79.5, 21.4]), 1),
            (BoundaryQuery(bbox=[0.0, 0.0, 1.0, 1.0]), 0),
            (BoundaryQuery(limit=2), 2),
        ],
    )
    def test_filters(self, seeded: GISBoundaryService, query, expected):
        assert len(seeded.find(query)) == expected
        assert seeded.count(query) == (expected if query.limit is None else 6)

    def test_results_are_ordered_by_name_for_stable_output(self, seeded: GISBoundaryService):
        names = [boundary.name for boundary in seeded.find()]
        assert names == sorted(names, key=str.casefold)

    def test_offset_pages_without_overlap(self, seeded: GISBoundaryService):
        first = seeded.find(BoundaryQuery(limit=3))
        second = seeded.find(BoundaryQuery(limit=3, offset=3))
        assert {b.id for b in first}.isdisjoint({b.id for b in second})
        assert len(first) == len(second) == 3

    def test_clear_empties_the_store(self, seeded: GISBoundaryService):
        assert seeded.clear() == 6
        assert seeded.count() == 0

    def test_build_repository_falls_back_to_the_file_backend(self, tmp_path: Path):
        assert build_repository(path=tmp_path / "b.geojson").backend == "geojson-file"

    def test_feature_round_trip_recomputes_derived_properties(self, seeded: GISBoundaryService):
        """Hand-editing area/label in the file must not desync them from the geometry."""
        feature = seeded.find()[0].to_feature()
        feature["properties"]["area_sqkm"] = 999_999.0
        feature["properties"]["category_label"] = "Tampered"
        rebuilt = feature_to_boundary(feature)
        assert rebuilt.area_sqkm != pytest.approx(999_999.0)
        assert rebuilt.category.label != "Tampered"


class TestPostGISTranslation:
    """The PostGIS backend needs a live database, but its SQL translation does not."""

    def test_where_clause_is_parameterized_and_bbox_uses_the_indexable_operator(self):
        where, params = build_where_clause(
            BoundaryQuery(
                categories=[BoundaryCategory.FOREST], state="Bihar", district="Patna",
                name_contains="Kanha", include_demo=False, bbox=[78.0, 21.0, 78.5, 21.5],
            )
        )
        assert "category = ANY(%s)" in where
        assert "geometry && ST_MakeEnvelope(%s, %s, %s, %s, 4326)" in where
        assert "metadata->>'is_demo'" in where
        assert params[:1] == [["FOREST"]] and params[-4:] == [78.0, 21.0, 78.5, 21.5]
        assert "Bihar" in params and "%Kanha%" in params
        # No caller value is ever inlined into the SQL text.
        for literal in ("Bihar", "Patna", "Kanha"):
            assert literal not in where

    def test_empty_query_produces_no_where_clause(self):
        assert build_where_clause(BoundaryQuery()) == ("", [])

    def test_row_to_boundary_parses_st_asgeojson_output(self):
        boundary = row_to_boundary(
            {
                "id": "abc", "name": "Demo Forest", "category": "FOREST", "state": "DEMO STATE",
                "district": None, "geometry": json.dumps(square(78.0, 21.0)), "source": "demo",
                "source_url": None, "last_updated": date(2026, 1, 1), "metadata": '{"is_demo": true}',
            }
        )
        assert boundary.is_demo and boundary.geometry.type == "Polygon"


# ---------------------------------------------------------------------------------
# Importer
# ---------------------------------------------------------------------------------


class TestImporter:
    def test_imports_the_demo_dataset_with_attribution(self, service: GISBoundaryService):
        report = BoundaryImporter(service).import_file(
            DEMO_DATASET, source="Test Source", source_url="https://example.test/demo",
            last_updated=date(2026, 9, 10), is_demo=True,
        )
        assert (report.features_read, report.imported, report.rejected_count) == (6, 6, 0)
        assert report.source_crs == "EPSG:4326" and report.transformed is False
        assert all(b.source == "Test Source" and b.source_url == "https://example.test/demo" for b in service.find())

    def test_reimport_updates_instead_of_duplicating(self, seeded: GISBoundaryService):
        BoundaryImporter(seeded).import_file(
            DEMO_DATASET, source=config.DEMO_SOURCE_NAME, last_updated=date(2026, 9, 10), is_demo=True
        )
        assert seeded.count() == 6

    def test_dry_run_writes_nothing(self, service: GISBoundaryService):
        report = BoundaryImporter(service).import_file(
            DEMO_DATASET, source="Test Source", last_updated=date(2026, 9, 10), is_demo=True, dry_run=True
        )
        assert report.features_read == 6 and report.imported == 0
        assert service.count() == 0

    def test_invalid_features_are_rejected_individually_not_fatally(self, service: GISBoundaryService, tmp_path: Path):
        path = tmp_path / "mixed.geojson"
        path.write_text(json.dumps({
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": square(78.0, 21.0),
                 "properties": {"name": "Good One", "category": "FOREST", "state": "DEMO STATE"}},
                {"type": "Feature",
                 "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [2, 2], [2, 0], [0, 2], [0, 0]]]},
                 "properties": {"name": "Bowtie", "category": "FOREST", "state": "DEMO STATE"}},
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [78.0, 21.0]},
                 "properties": {"name": "A Point", "category": "FOREST", "state": "DEMO STATE"}},
                {"type": "Feature", "geometry": square(78.5, 21.5),
                 "properties": {"category": "FOREST", "state": "DEMO STATE"}},  # no name
                {"type": "Feature", "geometry": square(78.7, 21.7),
                 "properties": {"name": "No State", "category": "FOREST"}},
            ],
        }), encoding="utf-8")

        report = BoundaryImporter(service).import_file(path, source="Test", last_updated=date(2026, 1, 1), is_demo=True)
        assert (report.features_read, report.imported, report.rejected_count) == (5, 1, 4)
        assert service.find()[0].name == "Good One"
        reasons = " | ".join(rejection.reason for rejection in report.rejected)
        assert "Self-intersection" in reasons and "Unsupported geometry type" in reasons
        assert "No name found" in reasons and "No state found" in reasons

    def test_defaults_fill_missing_state_and_category(self, service: GISBoundaryService, tmp_path: Path):
        path = tmp_path / "sparse.geojson"
        path.write_text(json.dumps({
            "type": "Feature", "geometry": square(78.0, 21.0), "properties": {"name": "Unlabelled Block"},
        }), encoding="utf-8")
        BoundaryImporter(service).import_file(
            path, source="Test", last_updated=date(2026, 1, 1),
            default_state="DEMO STATE", default_category="FOREST", is_demo=True,
        )
        stored = service.find()[0]
        assert stored.state == "DEMO STATE" and stored.category is BoundaryCategory.FOREST

    def test_category_aliases_are_resolved(self, service: GISBoundaryService, tmp_path: Path):
        path = tmp_path / "aliases.geojson"
        path.write_text(json.dumps({
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": square(78.0 + i * 0.3, 21.0),
                 "properties": {"name": f"Area {i}", "state": "DEMO STATE", "pa_type": raw}}
                for i, raw in enumerate(["Wildlife Sanctuary", "WLS", "national park", "Ramsar Site", "Something Else"])
            ],
        }), encoding="utf-8")
        BoundaryImporter(service).import_file(path, source="Test", last_updated=date(2026, 1, 1), is_demo=True)
        categories = {b.name: b.category for b in service.find()}
        assert categories["Area 0"] is BoundaryCategory.WILDLIFE_SANCTUARY
        assert categories["Area 1"] is BoundaryCategory.WILDLIFE_SANCTUARY
        assert categories["Area 2"] is BoundaryCategory.NATIONAL_PARK
        assert categories["Area 3"] is BoundaryCategory.RAMSAR_WETLAND
        # Unrecognised vocabulary is kept as OTHER_RESTRICTED_ZONE, not dropped.
        assert categories["Area 4"] is BoundaryCategory.OTHER_RESTRICTED_ZONE

    def test_custom_field_mapping(self, service: GISBoundaryService, tmp_path: Path):
        path = tmp_path / "custom.geojson"
        path.write_text(json.dumps({
            "type": "Feature", "geometry": square(78.0, 21.0),
            "properties": {"SANCTUARY_NM": "Custom Sanctuary", "ST_NM": "DEMO STATE"},
        }), encoding="utf-8")
        mapping = FieldMapping(
            name=("SANCTUARY_NM",), state=("ST_NM",), default_category=BoundaryCategory.WILDLIFE_SANCTUARY
        )
        BoundaryImporter(service, mapping=mapping).import_file(
            path, source="Test", last_updated=date(2026, 1, 1), is_demo=True
        )
        assert service.find()[0].name == "Custom Sanctuary"

    def test_source_attributes_are_preserved_in_metadata(self, seeded: GISBoundaryService):
        stored = seeded.find(BoundaryQuery(name_contains="National Park"))[0]
        assert stored.metadata["fictional"] is True
        assert "description" in stored.metadata

    def test_unsupported_extension_is_reported_clearly(self, service: GISBoundaryService, tmp_path: Path):
        path = tmp_path / "boundaries.csv"
        path.write_text("id,name\n", encoding="utf-8")
        with pytest.raises(BoundaryImportError, match="Unsupported file type"):
            BoundaryImporter(service).import_file(path, source="Test", last_updated=date(2026, 1, 1))

    def test_missing_file_and_malformed_json_are_reported_clearly(self, service: GISBoundaryService, tmp_path: Path):
        with pytest.raises(BoundaryImportError, match="No such file"):
            BoundaryImporter(service).import_file(tmp_path / "nope.geojson", source="T", last_updated=date(2026, 1, 1))
        broken = tmp_path / "broken.geojson"
        broken.write_text("{not json", encoding="utf-8")
        with pytest.raises(BoundaryImportError, match="not valid JSON"):
            BoundaryImporter(service).import_file(broken, source="T", last_updated=date(2026, 1, 1))


class TestCRSHandling:
    """CRS detection and transformation. Skipped wholesale when pyproj is absent."""

    def test_detects_and_transforms_a_projected_source_crs(self, service: GISBoundaryService, tmp_path: Path):
        pytest.importorskip("pyproj", reason="CRS transformation needs the optional GIS extras")
        # UTM zone 44N metres, roughly central India -- values that would be nonsense as lon/lat.
        path = tmp_path / "utm.geojson"
        path.write_text(json.dumps({
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::32644"}},
            "features": [{
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [[
                    [400000.0, 2300000.0], [420000.0, 2300000.0],
                    [420000.0, 2320000.0], [400000.0, 2320000.0], [400000.0, 2300000.0],
                ]]},
                "properties": {"name": "Projected Block", "category": "FOREST", "state": "DEMO STATE"},
            }],
        }), encoding="utf-8")

        report = BoundaryImporter(service).import_file(path, source="Test", last_updated=date(2026, 1, 1), is_demo=True)
        assert report.source_crs == "EPSG:32644" and report.transformed is True
        min_lon, min_lat, max_lon, max_lat = service.find()[0].geometry.bbox
        assert 78.0 < min_lon < 82.0 and 18.0 < min_lat < 23.0
        assert max_lon <= 180.0 and max_lat <= 90.0

    def test_transform_geometry_is_a_round_trip(self):
        pytest.importorskip("pyproj", reason="CRS transformation needs the optional GIS extras")
        original = square(78.0, 21.0)
        projected = transform_geometry(original, "EPSG:4326", "EPSG:32644")
        back = transform_geometry(projected, "EPSG:32644", "EPSG:4326")
        assert back["coordinates"][0][0] == pytest.approx(original["coordinates"][0][0], abs=1e-6)

    def test_geojson_without_a_crs_member_is_assumed_wgs84(self, service: GISBoundaryService):
        """RFC 7946 removed the crs member and fixed GeoJSON at WGS84."""
        report = BoundaryImporter(service).import_file(
            DEMO_DATASET, source="Test", last_updated=date(2026, 1, 1), is_demo=True
        )
        assert report.source_crs == config.DEFAULT_GEOJSON_CRS and report.transformed is False

    def test_shapefile_and_geopackage_round_trip(self, service: GISBoundaryService, tmp_path: Path):
        geopandas = pytest.importorskip("geopandas", reason="Shapefile/GeoPackage import needs the optional GIS extras")
        frame = geopandas.read_file(DEMO_DATASET)

        for path in (tmp_path / "demo.shp", tmp_path / "demo.gpkg"):
            store = GISBoundaryService(GeoJSONFileBoundaryRepository(tmp_path / f"{path.suffix[1:]}.geojson"))
            frame.to_file(path)
            report = BoundaryImporter(store).import_file(
                path, source="Test", last_updated=date(2026, 1, 1), is_demo=True
            )
            assert report.imported == 6, f"{path.suffix}: {report.summary()}"
            assert report.driver in {"shapefile", "geopackage"}
            assert store.count(BoundaryQuery(categories=[BoundaryCategory.TIGER_RESERVE])) == 1


# ---------------------------------------------------------------------------------
# Demo-data safety
# ---------------------------------------------------------------------------------


class TestDemoDataIsNeverPresentedAsOfficial:
    def test_the_shipped_dataset_declares_itself_as_demo_data(self):
        payload = json.loads(DEMO_DATASET.read_text(encoding="utf-8"))
        assert payload["notice"] == config.DEMO_DATA_NOTICE
        for feature in payload["features"]:
            properties = feature["properties"]
            assert properties["is_demo"] is True
            assert properties["data_notice"] == config.DEMO_DATA_NOTICE
            assert properties["name"].startswith("Demo ")
            assert properties["state"] == "DEMO STATE"

    def test_seeded_records_are_flagged_and_every_feature_carries_the_notice(self, seeded: GISBoundaryService):
        assert all(boundary.is_demo for boundary in seeded.find())
        collection = seeded.feature_collection()
        assert collection.contains_demo_data and collection.notice == config.DEMO_DATA_NOTICE
        assert all(f["properties"]["data_notice"] == config.DEMO_DATA_NOTICE for f in collection.features)

    def test_stats_surface_the_notice_and_the_demo_official_split(self, seeded: GISBoundaryService):
        stats = seeded.stats()
        assert stats["demo_records"] == 6 and stats["official_records"] == 0
        assert stats["notice"] == config.DEMO_DATA_NOTICE

    def test_a_collection_of_official_records_carries_no_notice(self, service: GISBoundaryService):
        service.create_boundary(
            name="Licensed Area", category="FOREST", state="DEMO STATE", geometry=square(78.0, 21.0),
            source="Some official publisher", last_updated=date(2026, 1, 1), is_demo=False,
        )
        collection = service.feature_collection()
        assert collection.contains_demo_data is False and collection.notice is None

    def test_include_demo_false_hides_generated_records(self, seeded: GISBoundaryService):
        seeded.create_boundary(
            name="Licensed Area", category="FOREST", state="DEMO STATE", geometry=square(70.0, 15.0),
            source="Some official publisher", last_updated=date(2026, 1, 1), is_demo=False,
        )
        official = seeded.find(BoundaryQuery(include_demo=False))
        assert [boundary.name for boundary in official] == ["Licensed Area"]
