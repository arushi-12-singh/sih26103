"""Coordinate validation and metre-accurate projection for the spatial engine.

The one rule this module exists to enforce: **a distance in metres is never applied to
coordinates in degrees.** A degree of longitude is ~111 km at the equator and ~0 km at
the poles, so `point.buffer(1000)` on lon/lat data is not a 1 km buffer -- it is a
1000-degree shape, and any distance, area, or overlap derived from it is meaningless.

Everything metric therefore happens in a projected CRS chosen for the specific point
being analysed:

  * "aeqd" -- azimuthal equidistant centred on the project point. Distance from that
    point is exact by construction, and a metre buffer around it is a true circle. This
    is the default because every primary measurement the engine reports (distance to
    boundary, buffer overlap) is relative to that point.
  * "utm" -- the point's UTM zone, for workflows that already use UTM.

Both are built with pyproj, which is a hard requirement of the spatial engine (unlike
the optional geopandas/psycopg extras used elsewhere in the GIS layer).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping

from pyproj import CRS, Transformer
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform

from app.config import spatial_config as config


class InvalidCoordinateError(ValueError):
    """Raised when a latitude/longitude pair cannot be used for spatial analysis."""


def validate_coordinates(latitude: Any, longitude: Any) -> tuple[float, float]:
    """Validate a WGS84 latitude/longitude pair and return it as floats.

    Rejects non-numeric input, booleans (which are `int` subclasses and almost always a
    bug here), NaN/infinity, and out-of-range values. This is the engine's first step:
    nothing downstream has to re-check the coordinates.
    """
    values: dict[str, float] = {}
    for label, raw, limit in (("latitude", latitude, 90.0), ("longitude", longitude, 180.0)):
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise InvalidCoordinateError(f"{label} must be a number, got {type(raw).__name__}.")
        value = float(raw)
        if not math.isfinite(value):
            raise InvalidCoordinateError(f"{label} must be a finite number, got {raw!r}.")
        if not -limit <= value <= limit:
            raise InvalidCoordinateError(f"{label} {value} is outside [{-limit}, {limit}].")
        values[label] = value
    return values["latitude"], values["longitude"]


def validate_buffer_meters(buffer_meters: Any) -> float:
    """Validate a buffer radius in metres against the configured bounds."""
    if isinstance(buffer_meters, bool) or not isinstance(buffer_meters, (int, float)):
        raise InvalidCoordinateError(f"buffer_meters must be a number, got {type(buffer_meters).__name__}.")
    value = float(buffer_meters)
    if not math.isfinite(value):
        raise InvalidCoordinateError(f"buffer_meters must be a finite number, got {buffer_meters!r}.")
    if not config.MIN_BUFFER_METERS <= value <= config.MAX_BUFFER_METERS:
        raise InvalidCoordinateError(
            f"buffer_meters {value} is outside "
            f"[{config.MIN_BUFFER_METERS}, {config.MAX_BUFFER_METERS}]."
        )
    return value


def utm_epsg(latitude: float, longitude: float) -> str:
    """Return the EPSG code of the UTM zone containing a point."""
    zone = int(math.floor((longitude + 180.0) / 6.0) % 60) + 1
    return f"EPSG:{326 if latitude >= 0 else 327}{zone:02d}"


def local_projected_crs(latitude: float, longitude: float, strategy: str | None = None) -> str:
    """Build the projected CRS used for all metric work at this location.

    Returned as a string (an EPSG code, or a PROJ definition for AEQD) so it can be
    reported verbatim in the analysis result -- a caller can reproduce every number the
    engine returns from the coordinates plus this string.
    """
    strategy = strategy or config.PROJECTION_STRATEGY
    if strategy == "utm":
        return utm_epsg(latitude, longitude)
    if strategy == "aeqd":
        # Centred on the point, so distances measured from it are exact.
        return (
            f"+proj=aeqd +lat_0={latitude!r} +lon_0={longitude!r} +x_0=0 +y_0=0 "
            "+datum=WGS84 +units=m +no_defs"
        )
    raise ValueError(f"Unknown projection strategy {strategy!r}; expected 'aeqd' or 'utm'.")


@lru_cache(maxsize=256)
def _transformer(source_crs: str, target_crs: str) -> Transformer:
    """Cached transformer. Building one costs milliseconds; reusing it costs nothing."""
    return Transformer.from_crs(CRS.from_user_input(source_crs), CRS.from_user_input(target_crs), always_xy=True)


@dataclass(frozen=True)
class LocalProjection:
    """A two-way bridge between WGS84 degrees and a local metre-based CRS.

    Holding both directions in one object is what keeps the engine honest: geometry is
    projected once on the way in, every metric operation happens on the projected side,
    and only the final envelope is sent back to degrees for the spatial-index lookup.
    """

    latitude: float
    longitude: float
    crs: str
    geographic_crs: str = config.GEOGRAPHIC_CRS

    @classmethod
    def for_point(cls, latitude: float, longitude: float, strategy: str | None = None) -> "LocalProjection":
        return cls(latitude=latitude, longitude=longitude, crs=local_projected_crs(latitude, longitude, strategy))

    def to_projected(self, geometry: BaseGeometry) -> BaseGeometry:
        """Degrees -> metres."""
        return shapely_transform(_transformer(self.geographic_crs, self.crs).transform, geometry)

    def to_geographic(self, geometry: BaseGeometry) -> BaseGeometry:
        """Metres -> degrees."""
        return shapely_transform(_transformer(self.crs, self.geographic_crs).transform, geometry)

    def project_mapping(self, geometry: Mapping[str, Any]) -> BaseGeometry:
        """Project a GeoJSON mapping straight into the metric CRS."""
        from app.models.boundary_geometry import to_shapely  # local import avoids a cycle

        return self.to_projected(to_shapely(geometry))
