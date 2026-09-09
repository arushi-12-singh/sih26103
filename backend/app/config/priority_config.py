"""Central configuration for the Project Priority & Decision Engine (Feature 3).

Every weight and normalization threshold used by app/services/priority_service.py
lives here, in one place, so the methodology can be tuned without touching scoring
logic and without hunting for magic numbers scattered across the codebase.
"""

from __future__ import annotations

# --- Top-level component weights (must sum to 1.0) ---------------------------------
COMPONENT_WEIGHTS: dict[str, float] = {
    "risk": 0.35,
    "delay": 0.20,
    "financial": 0.20,
    "historical": 0.15,
    "urgency": 0.10,
}

# --- Delay component -----------------------------------------------------------
# "Predicted delay duration" = delay_probability (Feature 1) x average actual delay of
# similar historical projects (Feature 2): an expected-value estimate of months likely
# lost. MAX_EXPECTED_DELAY_MONTHS is the delay treated as maximally severe (score 100)
# -- a configurable anchor, not a hardcoded "12 months always equals 100" rule.
MAX_EXPECTED_DELAY_MONTHS: float = 24.0

# --- Financial component --------------------------------------------------------
# Potential financial exposure = revised_cost x (1 + cost_overrun_percentage / 100),
# i.e. the total money at risk if the project's own original-to-revised cost trend
# continues. Normalized on a LOG scale between these two reference bounds (the
# realistic project-cost range produced by scripts/generate_projects.py and
# scripts/generate_historical_projects.py) rather than linear min-max, specifically so
# one extremely large project's exposure cannot dominate or distort the 0-100 scale
# used for every other, typically smaller, project -- under log scaling a project 10x
# larger contributes a constant increase, not a 10x increase.
FINANCIAL_EXPOSURE_MIN: float = 25.0        # smallest realistic project cost in this system's data
FINANCIAL_EXPOSURE_MAX: float = 50_000.0    # largest realistic project cost (clip bound shared by both data generators)

# --- Historical component -------------------------------------------------------
# Blends two independent Feature 2 signals about comparable past projects into one
# 0-100 evidence score:
#   - significant_delay_percentage: already a 0-100 rate (share of similar projects
#     that slipped by more than 6 months).
#   - average_delay_severity: the typical delay magnitude among those matches,
#     normalized with the same MAX_EXPECTED_DELAY_MONTHS anchor used by the delay
#     component, since both describe "months of delay" on one shared scale.
# Sub-weights favor the rate over the magnitude because "how often" comparable
# projects failed is a stronger attention signal than "by how much" on average.
HISTORICAL_SUBWEIGHTS: dict[str, float] = {
    "significant_delay_percentage": 0.6,
    "average_delay_severity": 0.4,
}

# --- Urgency component -----------------------------------------------------------
# Three transparent, independently-normalized signals about the project's OWN current
# schedule position (as opposed to AI-predicted risk or historical precedent):
#   - schedule_position: project_age_months / planned_duration_months, i.e. how close
#     to (at 100) or past (clipped at 100) its planned completion date the project
#     already is -- this is what "approaching planned completion" means here.
#   - schedule_deviation: previous_schedule_deviation linearly rescaled between the
#     realistic min/max this system's data generators produce.
#   - milestone_delay_ratio: milestones_delayed / milestones_total.
URGENCY_SUBWEIGHTS: dict[str, float] = {
    "schedule_position": 0.4,
    "schedule_deviation": 0.3,
    "milestone_delay_ratio": 0.3,
}
SCHEDULE_DEVIATION_MIN: float = -12.0  # most "ahead of schedule" value this system's data produces
SCHEDULE_DEVIATION_MAX: float = 36.0   # most "behind schedule" value this system's data produces

# --- Priority categorization -------------------------------------------------------
# Ascending (threshold, category) pairs; a score above the last threshold is CRITICAL.
# Mirrors the LOW/MODERATE/HIGH/CRITICAL banding SavedPredictionService already uses.
PRIORITY_CATEGORY_THRESHOLDS: tuple[tuple[float, str], ...] = ((30.0, "LOW"), (55.0, "MEDIUM"), (75.0, "HIGH"))

ATTENTION_LEVELS: dict[str, str] = {
    "LOW": "Monitor routinely; no immediate action required.",
    "MEDIUM": "Schedule a review within the current reporting cycle.",
    "HIGH": "Escalate to program leadership within two weeks.",
    "CRITICAL": "Requires immediate executive attention and intervention.",
}


def _validate() -> None:
    for label, weights in (
        ("COMPONENT_WEIGHTS", COMPONENT_WEIGHTS),
        ("HISTORICAL_SUBWEIGHTS", HISTORICAL_SUBWEIGHTS),
        ("URGENCY_SUBWEIGHTS", URGENCY_SUBWEIGHTS),
    ):
        total = sum(weights.values())
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"{label} must sum to 1.0, got {total}")


_validate()
