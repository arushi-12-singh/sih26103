"""The structured GIS signal consumed by the intelligence and priority pipeline.

This is the seam between the GIS module and everything downstream. The spatial engine
works in geometry; the priority engine and the intelligence response work in features.
`GISIntelligenceSignal` is the deliberately narrow, geometry-free summary that crosses
between them.

Why no geometry crosses this line: a polygon ring is not a feature. Nothing downstream --
the weighted priority score, the XGBoost model, the SHAP explainer -- can consume
coordinates as evidence, and passing them through would invite exactly that mistake. The
already-computed spatial verdict (status, severity, overlap, category, clearance) is the
evidence; the geometry that produced it stays in the GIS module and is served by the map
endpoints. `tests/test_gis_integration.py` asserts this model exposes no geometry field.

Legal posture: this is SCREENING output. Nothing here asserts that a project is
permitted or prohibited -- see `advisory` and `disclaimer`, whose wording is fixed in
app/config/priority_config.py.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.boundary import BoundaryCategory
from app.schemas.spatial import CollisionType, Severity


class NearestBoundary(BaseModel):
    """The closest screened boundary, whether or not it was actually intersected."""

    model_config = ConfigDict(extra="forbid")

    boundary_id: str
    name: str
    category: BoundaryCategory
    category_label: str
    distance_meters: float = Field(ge=0, description="0 when the project point lies inside it.")
    collision_type: CollisionType
    severity: Severity


class GISIntelligenceSignal(BaseModel):
    """Structured spatial evidence for one project location. No geometry, by design."""

    model_config = ConfigDict(extra="forbid")

    gis_status: CollisionType
    gis_severity: Severity | None = Field(default=None, description="Null when the screening is CLEAR.")
    buffer_meters: float = Field(ge=0)
    collision_count: int = Field(ge=0)
    boundaries_checked: int = Field(ge=0)

    highest_risk_category: BoundaryCategory | None = Field(
        default=None, description="Category of the most severe boundary found; null when CLEAR."
    )
    highest_risk_category_label: str | None = None
    nearest_boundary: NearestBoundary | None = None

    clearance_required: bool
    clearance_flag_count: int = Field(ge=0)
    clearance_flags: list[str] = Field(
        default_factory=list, description="Short, human-readable labels for each boundary requiring clearance."
    )

    max_buffer_overlap_percentage: float = Field(
        ge=0, le=100, description="Largest single-boundary share of the analysis buffer that was overlapped."
    )
    total_intersection_area_sqm: float = Field(ge=0)

    advisory: str = Field(description="Approved screening wording; never a permitting determination.")
    disclaimer: str = Field(description="Competent-authority verification notice.")
    contains_demo_data: bool = False
    notice: str | None = None
