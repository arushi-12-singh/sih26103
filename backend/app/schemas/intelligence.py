from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.project import ProjectRiskSummary, RiskFactor


class IntelligenceSimilarProject(BaseModel):
    """A single historical project match in the intelligence response."""

    project_id: str
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
    """Flat combined risk prediction and historical similarity response."""

    project_risk: ProjectRiskSummary
    top_risk_factors: list[RiskFactor]
    risk_summary: str
    similar_projects: list[IntelligenceSimilarProject]
    historical_evidence: IntelligenceHistoricalEvidence
    historical_summary: str
