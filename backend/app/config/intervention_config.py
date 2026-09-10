"""Configuration for the Intervention Recommendation stage.

The final step of the intelligence pipeline: given evidence already produced by
Features 1-5, which actions are worth taking first. Every trigger threshold, title,
template, and ranking weight lives here, so the playbook can be revised without touching
the evaluation logic in app/services/intervention_service.py.

Recommendations are RULE-BASED and deterministic -- no model, no LLM. The same evidence
always yields the same ordered list, which is what makes them auditable.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- Trigger thresholds --------------------------------------------------------------
MILESTONE_DELAY_RATIO_TRIGGER: float = 0.30
COST_OVERRUN_PERCENTAGE_TRIGGER: float = 15.0
PROGRESS_GAP_TRIGGER: float = 10.0        # physical minus financial progress, percentage points
HIGH_RISK_PROBABILITY_TRIGGER: float = 0.70
SCHEDULE_POSITION_TRIGGER: float = 0.85   # project age as a share of planned duration

#: Most recommendations shown at once. Beyond a handful, a "priority" list stops being one.
MAX_RECOMMENDATIONS: int = 5


@dataclass(frozen=True)
class InterventionRule:
    """One playbook entry.

    `base_weight` orders recommendations against each other when several fire. Statutory
    obligations outrank managerial actions because they gate the others: no amount of
    milestone recovery helps a project that cannot lawfully proceed until a clearance is
    resolved.
    """

    id: str
    title: str
    category: str
    base_weight: int
    rationale_template: str
    impact_template: str


# Ordered by base_weight descending for readability; the service sorts explicitly.
INTERVENTION_RULES: dict[str, InterventionRule] = {
    "spatial_clearance": InterventionRule(
        id="spatial_clearance",
        title="Initiate environmental clearance review",
        category="Statutory",
        base_weight=100,
        rationale_template=(
            "GIS screening returned {gis_status} with {clearance_flag_count} clearance "
            "flag(s); the nearest screened boundary is {nearest_boundary}."
        ),
        impact_template=(
            "Starting the statutory review now avoids it becoming the binding constraint later. "
            "Requirements must be confirmed with the competent authority."
        ),
    ),
    "site_review": InterventionRule(
        id="site_review",
        title="Commission a siting and alignment review",
        category="Statutory",
        base_weight=95,
        rationale_template=(
            "The project location falls inside {nearest_boundary}, so the current siting is the "
            "primary spatial constraint."
        ),
        impact_template=(
            "An alignment study establishes whether the conflict can be designed out before "
            "further commitments are made."
        ),
    ),
    "land_acquisition": InterventionRule(
        id="land_acquisition",
        title="Accelerate land acquisition resolution",
        category="Execution",
        base_weight=90,
        rationale_template="Land acquisition is still pending and is a leading delay driver in comparable projects.",
        impact_template=(
            "Comparable historical projects averaged {average_delay_months:.1f} months of delay; "
            "land acquisition was the most frequently recorded cause."
        ),
    ),
    "regulatory_clearance": InterventionRule(
        id="regulatory_clearance",
        title="Escalate pending regulatory clearances",
        category="Statutory",
        base_weight=85,
        rationale_template="A regulatory clearance is recorded as pending on this project.",
        impact_template="Clearance dependencies typically block downstream milestones rather than run alongside them.",
    ),
    "milestone_recovery": InterventionRule(
        id="milestone_recovery",
        title="Prepare a milestone recovery plan",
        category="Schedule",
        base_weight=70,
        rationale_template=(
            "{milestones_delayed} of {milestones_total} milestones are delayed "
            "({milestone_delay_ratio:.0%} of the schedule)."
        ),
        impact_template="Re-sequencing the critical path is the main lever available once slippage is established.",
    ),
    "funding_release": InterventionRule(
        id="funding_release",
        title="Escalate funding release with the sponsoring authority",
        category="Financial",
        base_weight=65,
        rationale_template="A funding issue is recorded, and financial progress trails physical progress.",
        impact_template="Unblocking disbursement protects the work already in place from stalling.",
    ),
    "cost_baseline": InterventionRule(
        id="cost_baseline",
        title="Revalidate the cost baseline",
        category="Financial",
        base_weight=60,
        rationale_template=(
            "Revised cost is {cost_overrun_percentage:.1f}% above the original sanction."
        ),
        impact_template="A revalidated baseline prevents further approvals being made against a stale estimate.",
    ),
    "contractor_review": InterventionRule(
        id="contractor_review",
        title="Conduct a contractor performance review",
        category="Execution",
        base_weight=55,
        rationale_template="A contractor issue is recorded against this project.",
        impact_template="Performance remedies take effect slowly, so they are worth starting early.",
    ),
    "progress_reconciliation": InterventionRule(
        id="progress_reconciliation",
        title="Reconcile physical and financial progress reporting",
        category="Governance",
        base_weight=45,
        rationale_template=(
            "Physical progress ({physical_progress:.0f}%) and financial progress "
            "({financial_progress:.0f}%) differ by {progress_gap:.0f} percentage points."
        ),
        impact_template="A reporting gap this wide usually indicates either unbilled work or unrecorded slippage.",
    ),
    "schedule_review": InterventionRule(
        id="schedule_review",
        title="Convene a schedule review with programme leadership",
        category="Schedule",
        base_weight=40,
        rationale_template=(
            "The project is at {schedule_position:.0%} of its planned duration with "
            "{physical_progress:.0f}% physical progress."
        ),
        impact_template="Time remaining is the constraint least able to be recovered later.",
    ),
    "monitoring": InterventionRule(
        id="monitoring",
        title="Maintain routine monitoring",
        category="Governance",
        base_weight=10,
        rationale_template="No individual risk driver crossed its intervention threshold.",
        impact_template="Continued monitoring is sufficient while the current evidence holds.",
    ),
}

#: Urgency label per priority category, so recommendations inherit the engine's banding
#: rather than inventing a second, conflicting scale.
URGENCY_BY_PRIORITY: dict[str, str] = {
    "CRITICAL": "Immediate",
    "HIGH": "Within two weeks",
    "MEDIUM": "Current reporting cycle",
    "LOW": "Routine",
}


def _validate() -> None:
    for key, rule in INTERVENTION_RULES.items():
        if key != rule.id:
            raise ValueError(f"INTERVENTION_RULES key {key!r} does not match rule id {rule.id!r}.")
    if MAX_RECOMMENDATIONS < 1:
        raise ValueError("MAX_RECOMMENDATIONS must be at least 1.")


_validate()
