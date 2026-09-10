from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import spatial_config
from app.schemas.project import ProjectRiskRequest


class PriorityRequest(ProjectRiskRequest):
    """The same validated project shape used by prediction/similarity.

    OPTIONAL coordinates enable the GIS environmental component. Without them the engine
    scores exactly as it did before the GIS module existed, using the original five-
    component weight profile -- so no existing caller is silently re-scored.
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
    def _validate_location(self) -> "PriorityRequest":
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be supplied together, or both omitted")
        return self

    @property
    def has_location(self) -> bool:
        return self.latitude is not None and self.longitude is not None


class PriorityComponent(BaseModel):
    """One named, weighted signal that fed into the final priority score."""

    name: str
    score: float = Field(ge=0, le=100, description="Normalised 0-100 score for this signal, before weighting.")
    weight: float = Field(ge=0, le=1)
    weighted_contribution: float = Field(ge=0, le=100)
    description: str


class PriorityResponse(BaseModel):
    priority_score: float = Field(ge=0, le=100)
    weight_profile: Literal["standard", "with_gis"] = Field(
        default="standard",
        description=(
            "Which weight profile produced this score. 'standard' is the original five-component "
            "system; 'with_gis' adds the GIS environmental component. Reported so a score is never "
            "ambiguous about the methodology behind it."
        ),
    )
    priority_category: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    recommended_attention_level: str
    decision_explanation: str
    score_breakdown: list[PriorityComponent]
