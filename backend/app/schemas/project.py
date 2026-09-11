from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProjectRiskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str | None = Field(default=None, min_length=1, max_length=100)
    sector: str = Field(min_length=1, max_length=100)
    state: str = Field(min_length=1, max_length=100)
    original_cost: float = Field(gt=0, le=1_000_000_000)
    revised_cost: float = Field(gt=0, le=1_000_000_000)
    planned_duration_months: int = Field(ge=1, le=240)
    project_age_months: int = Field(ge=0, le=240)
    physical_progress: float = Field(ge=0, le=100)
    financial_progress: float = Field(ge=0, le=100)
    milestones_total: int = Field(ge=1, le=500)
    milestones_delayed: int = Field(ge=0, le=500)
    land_acquisition_pending: bool
    clearance_pending: bool
    funding_issue: bool
    contractor_issue: bool
    previous_schedule_deviation: float = Field(ge=-100, le=240)

    @model_validator(mode="after")
    def validate_milestones(self) -> "ProjectRiskRequest":
        if self.milestones_delayed > self.milestones_total:
            raise ValueError("milestones_delayed cannot exceed milestones_total")
        return self


class ProjectRecord(ProjectRiskRequest):
    """A stored project record suitable for selecting a prediction input."""

    project_id: str = Field(min_length=1, max_length=100)


class ProjectRiskResponse(BaseModel):
    project_risk: "ProjectRiskSummary"
    top_risk_factors: list["RiskFactor"]
    summary: str


class ProjectRiskSummary(BaseModel):
    delay_probability: float = Field(ge=0, le=1)
    risk_percentage: int = Field(ge=0, le=100)
    risk_level: str
    model_confidence: str
    confidence_basis: str


class RiskFactor(BaseModel):
    factor: str
    impact: str
    importance: float = Field(ge=0, le=1)
    description: str
