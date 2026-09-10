from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pyproj import Transformer
import shapely.geometry as sg
import shapely.ops as so
from shapely.strtree import STRtree

from app.schemas.gis import GISBufferRequest, GISCollisionResponse, ZoneCollision


# Coordinate Transformer: WGS84 (lat/lon) <-> World Mercator EPSG:3857 (meters)
WGS84_TO_METRIC = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
METRIC_TO_WGS84 = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)

DEFAULT_GEOJSON_PATH = Path(__file__).resolve().parents[2] / "data" / "gis" / "protected_areas_india.geojson"


class GISService:
    def __init__(self, geojson_path: Path = DEFAULT_GEOJSON_PATH) -> None:
        if not geojson_path.exists():
            raise FileNotFoundError(f"GeoJSON protected areas dataset not found at {geojson_path}")

        with open(geojson_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.features_raw = data.get("features", [])
        self.geometries_wgs84: list[sg.base.BaseGeometry] = []
        self.geometries_metric: list[sg.base.BaseGeometry] = []
        self.properties_list: list[dict[str, Any]] = []

        for feature in self.features_raw:
            props = feature.get("properties", {})
            geom_dict = feature.get("geometry")
            if not geom_dict:
                continue

            try:
                geom_wgs84 = sg.shape(geom_dict)
                if not geom_wgs84.is_valid:
                    geom_wgs84 = geom_wgs84.buffer(0)
                
                # Transform to metric for accurate spatial operations
                geom_metric = so.transform(WGS84_TO_METRIC.transform, geom_wgs84)

                self.geometries_wgs84.append(geom_wgs84)
                self.geometries_metric.append(geom_metric)
                self.properties_list.append(props)
            except Exception as exc:
                continue

        if not self.geometries_metric:
            raise ValueError("No valid geometry features found in protected areas GeoJSON")

        # Build spatial R-Tree index for O(log N) bounding box queries
        self.spatial_tree = STRtree(self.geometries_metric)

    def get_all_protected_zones_geojson(self, category_filter: list[str] | None = None) -> dict[str, Any]:
        """Return raw GeoJSON FeatureCollection filtered by category if specified."""
        filtered_features = []
        for feature in self.features_raw:
            cat = feature.get("properties", {}).get("category")
            if category_filter and cat not in category_filter:
                continue
            filtered_features.append(feature)

        return {
            "type": "FeatureCollection",
            "features": filtered_features,
        }

    def check_buffer_collision(self, request: GISBufferRequest) -> GISCollisionResponse:
        """Perform metric buffer calculation and protected boundary intersection check."""
        proj_point_wgs84 = sg.Point(request.longitude, request.latitude)
        proj_point_metric = so.transform(WGS84_TO_METRIC.transform, proj_point_wgs84)

        buffer_radius_meters = request.buffer_distance_km * 1000.0
        buffer_poly_metric = proj_point_metric.buffer(buffer_radius_meters)

        # Convert metric buffer polygon back to WGS84 for GeoJSON display
        buffer_poly_wgs84 = so.transform(METRIC_TO_WGS84.transform, buffer_poly_metric)

        # Spatial index candidate lookup
        candidate_indices = self.spatial_tree.query(buffer_poly_metric)

        collisions: list[ZoneCollision] = []
        active_geojson_features = []

        highest_severity_rank = 0  # 0: NONE, 1: WARNING, 2: HIGH, 3: CRITICAL
        severity_map = {0: "NONE", 1: "WARNING", 2: "HIGH", 3: "CRITICAL"}

        for idx in candidate_indices:
            props = self.properties_list[idx]
            zone_category = props.get("category", "Restricted Zone")

            if request.zone_categories and zone_category not in request.zone_categories:
                continue

            zone_geom_metric = self.geometries_metric[idx]
            zone_geom_wgs84 = self.geometries_wgs84[idx]

            # Metric distance from project point to zone boundary (in km)
            dist_meters = proj_point_metric.distance(zone_geom_metric)
            dist_km = round(dist_meters / 1000.0, 2)

            # Check direct project point intersection vs buffer intersection
            is_direct = proj_point_metric.intersects(zone_geom_metric)
            intersects_buffer = buffer_poly_metric.intersects(zone_geom_metric)

            if not (is_direct or intersects_buffer):
                continue

            # Calculate intersection overlap area (sq km)
            intersection_area_sq_km = 0.0
            if intersects_buffer:
                overlap_geom = buffer_poly_metric.intersection(zone_geom_metric)
                intersection_area_sq_km = round(overlap_geom.area / 1e6, 3)

            # Determine severity
            if is_direct or (zone_category in ["Tiger Reserve", "National Park", "Ramsar Wetland"] and dist_km <= 1.0):
                severity = "CRITICAL"
                sev_rank = 3
            elif intersects_buffer and zone_category in ["Tiger Reserve", "National Park", "Ramsar Wetland", "Wildlife Sanctuary"]:
                severity = "HIGH"
                sev_rank = 2
            else:
                severity = "WARNING"
                sev_rank = 1

            highest_severity_rank = max(highest_severity_rank, sev_rank)

            collisions.append(ZoneCollision(
                zone_id=str(props.get("id", "PA-UNKNOWN")),
                zone_name=str(props.get("name", "Unknown Boundary")),
                zone_category=str(zone_category),
                state=str(props.get("state", "Unknown")),
                designation=str(props.get("designation", "Protected Area")),
                clearance_type_required=str(props.get("clearance_type_required", "Environmental / Forest Clearance Required")),
                distance_to_boundary_km=dist_km,
                is_direct_intersection=is_direct,
                intersection_area_sq_km=intersection_area_sq_km,
                severity=severity,
            ))

            # Include feature in GeoJSON output for client map overlay
            active_geojson_features.append({
                "type": "Feature",
                "properties": {
                    **props,
                    "collision_severity": severity,
                    "distance_km": dist_km,
                    "is_direct": is_direct,
                },
                "geometry": sg.mapping(zone_geom_wgs84),
            })

        # Sort collisions by severity and distance
        collisions.sort(key=lambda c: (0 if c.severity == "CRITICAL" else 1 if c.severity == "HIGH" else 2, c.distance_to_boundary_km))

        # Add project point & buffer circle as GeoJSON features
        map_features = [
            {
                "type": "Feature",
                "properties": {
                    "feature_type": "project_point",
                    "project_id": request.project_id or "CURRENT",
                },
                "geometry": sg.mapping(proj_point_wgs84),
            },
            {
                "type": "Feature",
                "properties": {
                    "feature_type": "buffer_radius",
                    "buffer_km": request.buffer_distance_km,
                    "has_collision": len(collisions) > 0,
                },
                "geometry": sg.mapping(buffer_poly_wgs84),
            },
            *active_geojson_features
        ]

        geojson_layers = {
            "type": "FeatureCollection",
            "features": map_features,
        }

        has_col = len(collisions) > 0
        overall_severity = severity_map[highest_severity_rank]
        clearance = highest_severity_rank >= 2 or any(c.is_direct_intersection for c in collisions)

        if has_col:
            summary = (
                f"Environmental collision warning: Project buffer ({request.buffer_distance_km} km) "
                f"intersects {len(collisions)} protected zone(s). Highest severity: {overall_severity}. "
                f"Mandatory clearance required."
            )
        else:
            summary = f"No environmental boundary collisions detected within the {request.buffer_distance_km} km buffer zone."

        return GISCollisionResponse(
            has_collision=has_col,
            total_collisions=len(collisions),
            highest_severity=overall_severity,
            clearance_required=clearance,
            buffer_distance_km=request.buffer_distance_km,
            project_coordinates={"latitude": request.latitude, "longitude": request.longitude},
            collisions=collisions,
            geojson_layers=geojson_layers,
            summary=summary,
        )


def build_gis_service(geojson_path: Path = DEFAULT_GEOJSON_PATH) -> GISService:
    return GISService(geojson_path)
