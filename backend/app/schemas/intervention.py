"""Schemas for the Intervention Recommendation stage."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class InterventionRecommendation(BaseModel):
    """One recommended action, with the evidence that triggered it."""

    model_config = ConfigDict(extra="forbid")

    rank: int = Field(ge=1)
    id: str
    title: str
    category: str = Field(description="Statutory, Execution, Schedule, Financial, or Governance.")
    urgency: str
    rationale: str = Field(description="Why this fired, quoting the evidence values that triggered it.")
    expected_impact: str
    evidence_source: str = Field(description="Which pipeline stage produced the triggering evidence.")
