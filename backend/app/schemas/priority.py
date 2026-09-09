from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.project import ProjectRiskRequest


class PriorityRequest(ProjectRiskRequest):
    """The same validated project shape used by prediction/similarity -- the priority
    engine derives every scoring component from Feature 1, Feature 2, and this input,
    with no additional business-context fields required.
    """


class PriorityComponent(BaseModel):
    """One named, weighted signal that fed into the final priority score."""

    name: str
    score: float = Field(ge=0, le=100, description="Normalised 0-100 score for this signal, before weighting.")
    weight: float = Field(ge=0, le=1)
    weighted_contribution: float = Field(ge=0, le=100)
    description: str


class PriorityResponse(BaseModel):
    priority_score: float = Field(ge=0, le=100)
    priority_category: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    recommended_attention_level: str
    decision_explanation: str
    score_breakdown: list[PriorityComponent]
