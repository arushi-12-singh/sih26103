"""Intervention Recommendation -- the final stage of the intelligence pipeline.

Turns evidence already produced upstream (XGBoost risk, SHAP factors, historical
similarity, GIS screening, and the priority score) into a ranked list of actions.

Rule-based and deterministic: each rule is a small predicate over evidence, every
threshold and template lives in app/config/intervention_config.py, and identical
evidence always produces an identical ordered list. No model and no LLM is involved --
a recommendation that cannot be traced to a threshold is not auditable, and this output
is meant to be defended in a review meeting.

Ordering is by the rules' configured `base_weight`, which places statutory obligations
above managerial actions: a clearance gates the others, so recovering milestones on a
project that cannot lawfully proceed is wasted effort.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.config import intervention_config as config
from app.config import priority_config as priority_config
from app.schemas.intervention import InterventionRecommendation
from app.schemas.priority import PriorityResponse
from app.schemas.project import ProjectRiskRequest
from app.schemas.similarity import SimilarityResponse

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.schemas.gis_signal import GISIntelligenceSignal


def _facts(
    payload: ProjectRiskRequest,
    similarity: SimilarityResponse,
    gis_signal: "GISIntelligenceSignal | None",
) -> dict[str, object]:
    """Flatten the pipeline's evidence into the values rule templates interpolate."""
    milestone_ratio = (
        payload.milestones_delayed / payload.milestones_total if payload.milestones_total else 0.0
    )
    cost_overrun = (
        (payload.revised_cost - payload.original_cost) / payload.original_cost * 100
        if payload.original_cost > 0
        else 0.0
    )
    schedule_position = (
        payload.project_age_months / payload.planned_duration_months
        if payload.planned_duration_months > 0
        else 0.0
    )
    nearest = gis_signal.nearest_boundary if gis_signal else None
    return {
        "milestones_delayed": payload.milestones_delayed,
        "milestones_total": payload.milestones_total,
        "milestone_delay_ratio": milestone_ratio,
        "cost_overrun_percentage": cost_overrun,
        "physical_progress": payload.physical_progress,
        "financial_progress": payload.financial_progress,
        "progress_gap": payload.physical_progress - payload.financial_progress,
        "schedule_position": schedule_position,
        "average_delay_months": similarity.historical_evidence.average_actual_delay_months,
        "gis_status": gis_signal.gis_status.value.replace("_", " ").lower() if gis_signal else "not screened",
        "clearance_flag_count": gis_signal.clearance_flag_count if gis_signal else 0,
        "nearest_boundary": (
            f"{nearest.name} ({nearest.category_label}) at {nearest.distance_meters:,.0f} m"
            if nearest
            else "no screened boundary"
        ),
    }


def _triggered(
    payload: ProjectRiskRequest,
    facts: dict[str, object],
    delay_probability: float,
    gis_signal: "GISIntelligenceSignal | None",
) -> list[tuple[str, str]]:
    """Evaluate every rule. Returns (rule_id, evidence_source) for those that fire."""
    fired: list[tuple[str, str]] = []

    if gis_signal is not None and gis_signal.clearance_required:
        fired.append(("spatial_clearance", "GIS boundary screening"))
    if gis_signal is not None and gis_signal.gis_status.value == "DIRECT_COLLISION":
        fired.append(("site_review", "GIS boundary screening"))

    if payload.land_acquisition_pending:
        fired.append(("land_acquisition", "Project data + historical similarity"))
    if payload.clearance_pending:
        fired.append(("regulatory_clearance", "Project data"))
    if float(facts["milestone_delay_ratio"]) >= config.MILESTONE_DELAY_RATIO_TRIGGER:
        fired.append(("milestone_recovery", "Project data"))
    if payload.funding_issue:
        fired.append(("funding_release", "Project data"))
    if float(facts["cost_overrun_percentage"]) >= config.COST_OVERRUN_PERCENTAGE_TRIGGER:
        fired.append(("cost_baseline", "Project data"))
    if payload.contractor_issue:
        fired.append(("contractor_review", "Project data"))
    if float(facts["progress_gap"]) >= config.PROGRESS_GAP_TRIGGER:
        fired.append(("progress_reconciliation", "Project data"))
    if (
        float(facts["schedule_position"]) >= config.SCHEDULE_POSITION_TRIGGER
        and delay_probability >= config.HIGH_RISK_PROBABILITY_TRIGGER
    ):
        fired.append(("schedule_review", "XGBoost risk prediction + project data"))

    # Never return an empty list: "nothing crossed a threshold" is itself a finding, and
    # a blank recommendations panel reads as a failure rather than as good news.
    if not fired:
        fired.append(("monitoring", "All pipeline stages"))
    return fired


class InterventionService:
    """Builds ranked, evidence-linked intervention recommendations."""

    def recommend(
        self,
        payload: ProjectRiskRequest,
        similarity: SimilarityResponse,
        priority: PriorityResponse,
        delay_probability: float,
        gis_signal: "GISIntelligenceSignal | None" = None,
    ) -> list[InterventionRecommendation]:
        facts = _facts(payload, similarity, gis_signal)
        fired = _triggered(payload, facts, delay_probability, gis_signal)
        urgency = config.URGENCY_BY_PRIORITY.get(priority.priority_category, "Routine")

        ranked = sorted(
            fired,
            # Sorted by configured weight, then by rule id, so equal-weight rules cannot
            # swap places between two runs on identical evidence.
            key=lambda item: (-config.INTERVENTION_RULES[item[0]].base_weight, item[0]),
        )[: config.MAX_RECOMMENDATIONS]

        return [
            InterventionRecommendation(
                rank=index,
                id=rule.id,
                title=rule.title,
                category=rule.category,
                urgency=urgency,
                rationale=rule.rationale_template.format(**facts),
                expected_impact=rule.impact_template.format(**facts),
                evidence_source=source,
            )
            for index, (rule_id, source) in enumerate(ranked, start=1)
            for rule in (config.INTERVENTION_RULES[rule_id],)
        ]


def build_intervention_service() -> InterventionService:
    """Constructed once at startup, mirroring the other build_* factories."""
    return InterventionService()


__all__ = ["InterventionRecommendation", "InterventionService", "build_intervention_service"]
