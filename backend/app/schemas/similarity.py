from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.project import ProjectRiskRequest


class SimilarityRequest(ProjectRiskRequest):
    """The same validated raw project shape used by the prediction feature."""


class HistoricalProjectMatch(BaseModel):
    project_id: str
    project_name: str
    sector: str
    state: str
    similarity_score: float = Field(ge=0, le=100)
    actual_delay_months: int = Field(ge=0)
    actual_cost_overrun_percentage: float = Field(ge=0)
    final_status: str
    primary_delay_cause: str
    intervention_taken: str
    intervention_outcome: str


class HistoricalEvidence(BaseModel):
    projects_analyzed: int = Field(ge=0)
    average_similarity: float = Field(ge=0, le=100)
    average_actual_delay_months: float = Field(ge=0)
    average_cost_overrun_percentage: float = Field(ge=0)
    projects_with_significant_delay: int = Field(ge=0)
    significant_delay_percentage: float = Field(ge=0, le=100)
    most_common_delay_cause: str


class SimilarityResponse(BaseModel):
    similar_projects: list[HistoricalProjectMatch]
    historical_evidence: HistoricalEvidence
    historical_summary: str
