from __future__ import annotations

from typing import Any
from pyproj import Transformer
import shapely.geometry as sg
import shapely.ops as so
import shapely.validation as sv


def validate_and_repair_geometry(
    raw_geometry: dict[str, Any] | sg.base.BaseGeometry,
    source_epsg: int = 4326,
    target_epsg: int = 4326,
) -> tuple[sg.base.BaseGeometry, dict[str, Any], bool]:
    """Validate, repair self-intersections, and transform CRS to WGS84 (EPSG:4326).

    Returns:
        (shapely_geometry, geojson_dict, was_repaired)

    Raises:
        ValueError: If geometry is unrepairable, empty, or not a Polygon/MultiPolygon.
    """
    if isinstance(raw_geometry, dict):
        geom_type = raw_geometry.get("type")
        if geom_type not in ["Polygon", "MultiPolygon"]:
            raise ValueError(f"Geometry type '{geom_type}' is invalid. Only Polygon and MultiPolygon are allowed.")
        try:
            geom = sg.shape(raw_geometry)
        except Exception as exc:
            raise ValueError(f"Failed to parse GeoJSON geometry: {exc}") from exc
    elif isinstance(raw_geometry, sg.base.BaseGeometry):
        geom = raw_geometry
    else:
        raise ValueError(f"Unsupported geometry object type: {type(raw_geometry)}")

    if geom.is_empty:
        raise ValueError("Geometry is empty")

    was_repaired = False

    # Perform CRS reprojection if source CRS is not EPSG:4326 WGS84
    if source_epsg != target_epsg:
        try:
            transformer = Transformer.from_crs(f"EPSG:{source_epsg}", f"EPSG:{target_epsg}", always_xy=True)
            geom = so.transform(transformer.transform, geom)
            was_repaired = True
        except Exception as exc:
            raise ValueError(f"CRS transformation from EPSG:{source_epsg} to EPSG:{target_epsg} failed: {exc}") from exc

    # Validate geometry integrity & repair self-intersections
    if not geom.is_valid:
        was_repaired = True
        try:
            # First repair attempt: make_valid
            repaired = sv.make_valid(geom)
            # If make_valid created a GeometryCollection, extract only Polygons
            if isinstance(repaired, sg.GeometryCollection):
                polys = [g for g in repaired.geoms if isinstance(g, (sg.Polygon, sg.MultiPolygon))]
                if not polys:
                    raise ValueError("make_valid did not produce any valid polygon components")
                repaired = so.unary_union(polys)
            geom = repaired
        except Exception:
            # Fallback repair attempt: zero-distance buffer
            geom = geom.buffer(0)

    if geom.is_empty or not geom.is_valid:
        raise ValueError("Geometry is invalid and could not be automatically repaired")

    if not isinstance(geom, (sg.Polygon, sg.MultiPolygon)):
        # Extract polygon components if wrapped in another geometry type
        if hasattr(geom, "geoms"):
            polys = [g for g in geom.geoms if isinstance(g, (sg.Polygon, sg.MultiPolygon))]
            if polys:
                geom = so.unary_union(polys)
            else:
                raise ValueError(f"Repaired geometry result '{geom.geom_type}' is not a Polygon/MultiPolygon")
        else:
            raise ValueError(f"Repaired geometry result '{geom.geom_type}' is not a Polygon/MultiPolygon")

    geojson_dict = sg.mapping(geom)
    return geom, geojson_dict, was_repaired
