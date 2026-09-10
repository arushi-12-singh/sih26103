"""Central configuration for the Project Priority & Decision Engine (Feature 3).

Every weight and normalization threshold used by app/services/priority_service.py
lives here, in one place, so the methodology can be tuned without touching scoring
logic and without hunting for magic numbers scattered across the codebase.
"""

from __future__ import annotations

# --- Top-level component weights (each profile must sum to 1.0) --------------------
# TWO profiles, selected by whether GIS screening evidence is available for a project.
# The GIS-free profile is the ORIGINAL scoring system, unchanged: a project assessed
# without coordinates scores exactly what it scored before the GIS module existed, so
# adding GIS never silently re-scores historical assessments or existing callers.
COMPONENT_WEIGHTS: dict[str, float] = {
    "risk": 0.35,
    "delay": 0.20,
    "financial": 0.20,
    "historical": 0.15,
    "urgency": 0.10,
}

# Used only when a GIS screening signal is supplied. GIS's 10% is funded by trimming
# risk (0.35 -> 0.30) and urgency (0.10 -> 0.05): risk still dominates, and urgency --
# the project's own schedule position -- is the weakest evidence of the five, so it
# gives way first. Retune here; nothing in priority_service.py hardcodes a weight.
COMPONENT_WEIGHTS_WITH_GIS: dict[str, float] = {
    "risk": 0.30,
    "delay": 0.20,
    "financial": 0.20,
    "historical": 0.15,
    "urgency": 0.05,
    "gis": 0.10,
}


def weights_for(include_gis: bool) -> dict[str, float]:
    """The weight profile to score with. The only place the choice is made."""
    return COMPONENT_WEIGHTS_WITH_GIS if include_gis else COMPONENT_WEIGHTS

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

# --- GIS environmental component (weight 0.10, only in the GIS profile) -------------
# Scores the project's spatial relationship to protected/restricted areas. It consumes
# only the STRUCTURED signal produced by app/services/gis_intelligence_service.py --
# status, severity, overlap, category, clearance flags -- and never geometry. Polygons
# are not features: an ML model or a weighted score cannot meaningfully consume a ring
# of coordinates, and feeding one in would be numerology rather than evidence.
#
# Three normalized sub-scores, blended by GIS_SUBWEIGHTS, then a clearance floor.
GIS_STATUS_SCORES: dict[str, float] = {
    "DIRECT_COLLISION": 100.0,
    "BUFFER_COLLISION": 70.0,
    "NEARBY": 35.0,
    "CLEAR": 0.0,
}

GIS_SEVERITY_SCORES: dict[str, float] = {
    "CRITICAL": 100.0,
    "HIGH": 75.0,
    "MEDIUM": 50.0,
    "LOW": 25.0,
}

# Sub-weights favour status because "is the site inside a protected area?" is a
# categorically different question from "how much of the buffer overlaps?".
GIS_SUBWEIGHTS: dict[str, float] = {
    "status": 0.55,
    "severity": 0.25,
    "overlap": 0.20,
}

# A project needing environmental clearance cannot score low on this component however
# small the geometric overlap: the obligation itself is the schedule risk.
GIS_CLEARANCE_FLOOR: float = 50.0

# --- Statutory language -------------------------------------------------------------
# This system performs SCREENING, not adjudication. It has no authority to decide
# whether a project is permissible, and must never imply that it does. These strings are
# the only approved phrasing for a spatial conflict, and are asserted by
# tests/test_gis_integration.py -- which also fails the build if prohibited
# determinative wording ("legally rejected", "prohibited", "not permitted", ...) appears
# anywhere in the response-producing code.
SPATIAL_CONFLICT_HEADLINE: str = "Potential spatial conflict detected."
NO_CONFLICT_HEADLINE: str = "No spatial conflict detected within the screened buffer."
CLEARANCE_DISCLAIMER: str = (
    "Final clearance requirements must be verified by the competent authority and "
    "applicable regulations."
)

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
        ("COMPONENT_WEIGHTS_WITH_GIS", COMPONENT_WEIGHTS_WITH_GIS),
        ("HISTORICAL_SUBWEIGHTS", HISTORICAL_SUBWEIGHTS),
        ("URGENCY_SUBWEIGHTS", URGENCY_SUBWEIGHTS),
        ("GIS_SUBWEIGHTS", GIS_SUBWEIGHTS),
    ):
        total = sum(weights.values())
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"{label} must sum to 1.0, got {total}")

    # The GIS profile must extend the base one, not quietly rename or drop a component.
    missing = set(COMPONENT_WEIGHTS) - set(COMPONENT_WEIGHTS_WITH_GIS)
    if missing:
        raise ValueError(f"COMPONENT_WEIGHTS_WITH_GIS is missing base components: {sorted(missing)}")
    if set(COMPONENT_WEIGHTS_WITH_GIS) - set(COMPONENT_WEIGHTS) != {"gis"}:
        raise ValueError("COMPONENT_WEIGHTS_WITH_GIS must add exactly one component: 'gis'.")


_validate()
