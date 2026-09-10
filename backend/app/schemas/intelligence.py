from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import spatial_config
from app.models.projection import validate_buffer_meters, validate_coordinates
from app.schemas.gis_signal import GISIntelligenceSignal
from app.schemas.intervention import InterventionRecommendation
from app.schemas.priority import PriorityResponse
from app.schemas.project import ProjectRiskRequest, ProjectRiskSummary, RiskFactor


class ProjectIntelligenceRequest(ProjectRiskRequest):
    """The existing project shape, plus OPTIONAL coordinates for GIS screening.

    Optional by design: every field the endpoint accepted before is unchanged and still
    sufficient. Supplying coordinates adds the GIS screening stage and switches the
    priority engine to its GIS weight profile; omitting them reproduces the previous
    behaviour exactly.
    """

    model_config = ConfigDict(extra="forbid")

    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    buffer_meters: float = Field(
        default=spatial_config.DEFAULT_BUFFER_METERS,
        ge=spatial_config.MIN_BUFFER_METERS,
        le=spatial_config.MAX_BUFFER_METERS,
    )

    @model_validator(mode="after")
    def _validate_location(self) -> "ProjectIntelligenceRequest":
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be supplied together, or both omitted")
        if self.latitude is not None and self.longitude is not None:
            # Routed through the engine's own checks so HTTP and in-process callers can
            # never disagree about what a valid coordinate is.
            validate_coordinates(self.latitude, self.longitude)
            validate_buffer_meters(self.buffer_meters)
        return self

    @property
    def has_location(self) -> bool:
        return self.latitude is not None and self.longitude is not None


class IntelligenceSimilarProject(BaseModel):
    """A single historical project match in the intelligence response."""

    project_id: str
    project_name: str
    similarity_score: float = Field(ge=0, le=100)
    sector: str
    state: str
    actual_delay_months: int = Field(ge=0)
    actual_cost_overrun_percentage: float
    primary_delay_cause: str


class IntelligenceHistoricalEvidence(BaseModel):
    """Aggregated evidence from historically similar projects."""

    projects_analyzed: int = Field(ge=0)
    significant_delay_percentage: float = Field(ge=0, le=100)
    average_actual_delay_months: float = Field(ge=0)
    most_common_delay_cause: str


class ProjectIntelligenceResponse(BaseModel):
    """The full intelligence pipeline for one project.

    Risk prediction -> SHAP explanation -> historical similarity -> GIS boundary
    screening -> priority -> intervention recommendations. The three trailing sections
    are optional: `gis_screening` is present only when coordinates were supplied, and
    `priority`/`interventions` only when both upstream models were available. Existing
    clients that read only the first six fields are unaffected.
    """

    project_risk: ProjectRiskSummary
    top_risk_factors: list[RiskFactor]
    risk_summary: str
    similar_projects: list[IntelligenceSimilarProject]
    historical_evidence: IntelligenceHistoricalEvidence
    historical_summary: str
    gis_screening: GISIntelligenceSignal | None = Field(
        default=None, description="Present only when latitude/longitude were supplied."
    )
    priority: PriorityResponse | None = None
    interventions: list[InterventionRecommendation] = Field(default_factory=list)
