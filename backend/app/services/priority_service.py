"""Deterministic Project Priority & Decision Engine (Feature 3).

Answers "which projects need attention first?" by combining evidence ALREADY produced
by Feature 1 (PredictionService) and Feature 2 (SimilarityService) into one transparent,
weighted score. This module never calls a model, never re-runs XGBoost or
NearestNeighbors, and never uses an LLM -- it only reads the typed responses those two
services already computed, so there is no duplicated ML logic here.

Every weight and normalization threshold lives in app.config.priority_config; nothing
here is an arbitrary or hidden constant.
"""

from __future__ import annotations

import numpy as np

from app.config import priority_config as config
from app.schemas.priority import PriorityComponent, PriorityRequest, PriorityResponse
from app.schemas.project import ProjectRiskResponse
from app.schemas.similarity import SimilarityResponse

# ---------------------------------------------------------------------------------
# Component calculators -- pure functions, each independently testable, each
# normalizing its inputs to a 0-100 "needs attention" scale BEFORE any weighting is
# applied (per the methodology: normalize first, then weight -- never multiply raw
# values together).
# ---------------------------------------------------------------------------------


def calculate_risk_component(delay_probability: float) -> float:
    """AI Delay Risk component (weight 0.35).

    Formula: risk_component = delay_probability * 100

    Directly rescales the XGBoost classifier's calibrated delay probability (already a
    well-behaved value in [0, 1]) onto a 0-100 scale. No further transformation is
    needed or applied. Example: 0.87 -> 87.0.
    """
    return float(np.clip(delay_probability * 100, 0, 100))


def calculate_delay_component(
    delay_probability: float,
    average_actual_delay_months: float,
    max_expected_delay_months: float = config.MAX_EXPECTED_DELAY_MONTHS,
) -> float:
    """Predicted Delay Severity component (weight 0.20).

    Formula: expected_delay_months = delay_probability * average_actual_delay_months
             delay_component = min(100, expected_delay_months / max_expected_delay_months * 100)

    "Predicted delay duration" is modeled as an expected value: this project's own
    XGBoost delay probability (Feature 1) multiplied by the average delay actually
    observed among its closest historical matches (Feature 2). This differs from the
    Historical component below, which reflects precedent alone, independent of this
    project's individual risk score.

    Normalized against `max_expected_delay_months`, a configurable anchor from
    app/config/priority_config.py -- not a hardcoded "12 months always equals 100" rule.
    """
    expected_delay_months = delay_probability * average_actual_delay_months
    return float(np.clip((expected_delay_months / max_expected_delay_months) * 100, 0, 100))


def calculate_financial_component(
    original_cost: float,
    revised_cost: float,
    exposure_min: float = config.FINANCIAL_EXPOSURE_MIN,
    exposure_max: float = config.FINANCIAL_EXPOSURE_MAX,
) -> float:
    """Financial Exposure component (weight 0.20).

    Formula: cost_overrun_percentage = (revised_cost - original_cost) / original_cost * 100
             potential_exposure = revised_cost * (1 + max(cost_overrun_percentage, 0) / 100)
             financial_component = 100 * (log(potential_exposure) - log(exposure_min))
                                        / (log(exposure_max)   - log(exposure_min))

    `potential_exposure` combines project scale (revised_cost) with the project's own
    cost overrun percentage (how much it has already grown from its original sanctioned
    cost), projecting that trend forward by one more increment -- a forward-looking
    "money at risk" figure, not just the currently booked revised cost.

    Normalized with LOG-scale min-max scaling (never linear min-max) between
    `exposure_min`/`exposure_max`, real project-cost bounds shared by this system's data
    generators. Log scaling is the specific mechanism that prevents one extremely large
    project from distorting every other project's score: under a linear scale a
    10,000 Cr project would force nearly all normal projects toward 0, whereas under a
    log scale a 10x larger exposure only ever contributes a constant-sized increase.
    """
    cost_overrun_percentage = ((revised_cost - original_cost) / original_cost * 100) if original_cost > 0 else 0.0
    potential_exposure = revised_cost * (1 + max(cost_overrun_percentage, 0.0) / 100)
    bounded_exposure = max(potential_exposure, exposure_min)  # keep the log argument within the reference domain
    log_position = (np.log(bounded_exposure) - np.log(exposure_min)) / (np.log(exposure_max) - np.log(exposure_min))
    return float(np.clip(log_position * 100, 0, 100))


def calculate_historical_component(
    significant_delay_percentage: float,
    average_actual_delay_months: float,
    max_expected_delay_months: float = config.MAX_EXPECTED_DELAY_MONTHS,
    subweights: dict[str, float] = config.HISTORICAL_SUBWEIGHTS,
) -> float:
    """Historical Delay Evidence component (weight 0.15).

    Formula: rate_score = significant_delay_percentage                       (already 0-100)
             severity_score = min(100, average_actual_delay_months / max_expected_delay_months * 100)
             historical_component = rate_score * 0.6 + severity_score * 0.4

    Blends two Feature 2 signals about comparable past projects: how OFTEN they were
    significantly delayed (rate) and how SEVERE those delays typically were
    (magnitude, normalized with the same anchor the delay component uses, since both
    describe "months of delay"). Sub-weights are documented in
    app/config/priority_config.py, not an arbitrary/hidden blend.
    """
    rate_score = float(np.clip(significant_delay_percentage, 0, 100))
    severity_score = float(np.clip((average_actual_delay_months / max_expected_delay_months) * 100, 0, 100))
    combined = rate_score * subweights["significant_delay_percentage"] + severity_score * subweights["average_delay_severity"]
    return float(np.clip(combined, 0, 100))


def calculate_urgency_component(
    project_age_months: float,
    planned_duration_months: float,
    previous_schedule_deviation: float,
    milestones_delayed: int,
    milestones_total: int,
    subweights: dict[str, float] = config.URGENCY_SUBWEIGHTS,
    deviation_min: float = config.SCHEDULE_DEVIATION_MIN,
    deviation_max: float = config.SCHEDULE_DEVIATION_MAX,
) -> float:
    """Current Project Urgency component (weight 0.10).

    Formula (three independently-normalized signals about the project's OWN current
    schedule state, combined with documented sub-weights):
      schedule_position_score = min(100, project_age_months / planned_duration_months * 100)
      deviation_score = 100 * (previous_schedule_deviation - deviation_min) / (deviation_max - deviation_min)
      milestone_score = milestones_delayed / milestones_total * 100
      urgency_component = schedule_position_score * 0.4
                         + deviation_score * 0.3
                         + milestone_score * 0.3

    `schedule_position_score` is what captures "approaching (or past) planned
    completion": it rises toward 100 as the project's age nears its planned duration,
    and clips at 100 once the project is overdue.
    """
    schedule_position_score = (
        float(np.clip((project_age_months / planned_duration_months) * 100, 0, 100)) if planned_duration_months > 0 else 0.0
    )
    deviation_score = float(np.clip(
        (previous_schedule_deviation - deviation_min) / (deviation_max - deviation_min) * 100, 0, 100,
    ))
    milestone_score = float(np.clip((milestones_delayed / milestones_total) * 100, 0, 100)) if milestones_total > 0 else 0.0
    combined = (
        schedule_position_score * subweights["schedule_position"]
        + deviation_score * subweights["schedule_deviation"]
        + milestone_score * subweights["milestone_delay_ratio"]
    )
    return float(np.clip(combined, 0, 100))


def calculate_priority_score(
    risk_component: float,
    delay_component: float,
    financial_component: float,
    historical_component: float,
    urgency_component: float,
    weights: dict[str, float] = config.COMPONENT_WEIGHTS,
) -> float:
    """Combine the five normalized (0-100) components into the final 0-100 priority score.

    Priority Score = risk_component      * 0.35
                    + delay_component      * 0.20
                    + financial_component  * 0.20
                    + historical_component * 0.15
                    + urgency_component    * 0.10

    Every argument must already be normalized to [0, 100] by its own calculate_*
    function above -- this function applies only the documented weights
    (app/config/priority_config.COMPONENT_WEIGHTS); it performs no normalization and no
    raw-value multiplication of its own.
    """
    score = (
        risk_component * weights["risk"]
        + delay_component * weights["delay"]
        + financial_component * weights["financial"]
        + historical_component * weights["historical"]
        + urgency_component * weights["urgency"]
    )
    return float(np.clip(round(score, 1), 0, 100))


class PriorityService:
    """Orchestrates the calculate_* functions above into a full PriorityResponse.

    Pure with respect to Feature 1/2: it never calls PredictionService or
    SimilarityService itself, it only reads their already-computed, typed outputs.
    """

    def assess(
        self,
        payload: PriorityRequest,
        project_risk: ProjectRiskResponse,
        similarity: SimilarityResponse,
    ) -> PriorityResponse:
        delay_probability = project_risk.project_risk.delay_probability
        evidence = similarity.historical_evidence

        risk_score = calculate_risk_component(delay_probability)
        delay_score = calculate_delay_component(delay_probability, evidence.average_actual_delay_months)
        financial_score = calculate_financial_component(payload.original_cost, payload.revised_cost)
        historical_score = calculate_historical_component(
            evidence.significant_delay_percentage, evidence.average_actual_delay_months,
        )
        urgency_score = calculate_urgency_component(
            payload.project_age_months, payload.planned_duration_months, payload.previous_schedule_deviation,
            payload.milestones_delayed, payload.milestones_total,
        )

        components = [
            self._component(
                "risk", risk_score,
                f"XGBoost delay probability of {delay_probability:.0%} ({project_risk.project_risk.risk_level}).",
            ),
            self._component(
                "delay", delay_score,
                f"Expected delay of {delay_probability * evidence.average_actual_delay_months:.1f} months "
                f"(probability {delay_probability:.0%} x historical average {evidence.average_actual_delay_months:.1f} months).",
            ),
            self._component(
                "financial", financial_score,
                f"Revised cost {payload.revised_cost:,.0f} with "
                f"{self._cost_overrun_percentage(payload):.1f}% overrun over original cost "
                f"{payload.original_cost:,.0f} drives the estimated exposure.",
            ),
            self._component(
                "historical", historical_score,
                f"{evidence.significant_delay_percentage:.0f}% of {evidence.projects_analyzed} similar historical "
                f"projects had significant delays, averaging {evidence.average_actual_delay_months:.1f} months.",
            ),
            self._component(
                "urgency", urgency_score,
                f"Project is at {self._schedule_position_pct(payload):.0f}% of its planned duration, with "
                f"{payload.milestones_delayed}/{payload.milestones_total} milestones delayed and a schedule "
                f"deviation of {payload.previous_schedule_deviation:.1f}.",
            ),
        ]

        priority_score = calculate_priority_score(risk_score, delay_score, financial_score, historical_score, urgency_score)
        category = self._categorize(priority_score)
        return PriorityResponse(
            priority_score=priority_score,
            priority_category=category,
            recommended_attention_level=config.ATTENTION_LEVELS[category],
            decision_explanation=self._build_explanation(priority_score, category, components),
            score_breakdown=components,
        )

    @staticmethod
    def _cost_overrun_percentage(payload: PriorityRequest) -> float:
        if payload.original_cost <= 0:
            return 0.0
        return (payload.revised_cost - payload.original_cost) / payload.original_cost * 100

    @staticmethod
    def _schedule_position_pct(payload: PriorityRequest) -> float:
        if payload.planned_duration_months <= 0:
            return 0.0
        return min(100.0, (payload.project_age_months / payload.planned_duration_months) * 100)

    @staticmethod
    def _component(name: str, score: float, description: str) -> PriorityComponent:
        weight = config.COMPONENT_WEIGHTS[name]
        rounded_score = round(score, 1)
        return PriorityComponent(
            name=name,
            score=rounded_score,
            weight=weight,
            weighted_contribution=round(rounded_score * weight, 1),
            description=description,
        )

    @staticmethod
    def _categorize(score: float) -> str:
        for threshold, label in config.PRIORITY_CATEGORY_THRESHOLDS:
            if score <= threshold:
                return label
        return "CRITICAL"

    @staticmethod
    def _build_explanation(score: float, category: str, components: list[PriorityComponent]) -> str:
        top_contributors = sorted(components, key=lambda c: c.weighted_contribution, reverse=True)[:3]
        reasons = "; ".join(c.description.rstrip(".") for c in top_contributors)
        return f"This project is rated {category} priority ({score:.1f}/100). Leading contributors: {reasons}."


def build_priority_service() -> PriorityService:
    return PriorityService()
