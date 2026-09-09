"""Generate a reproducible historical infrastructure project corpus with observed outcomes.

Outcome columns (actual_delay_months, actual_cost_overrun_percentage, final_status,
primary_delay_cause, intervention_taken, intervention_outcome) describe what happened
to a project AFTER the fact. They must never be fed back in as similarity-matching
input features — see app.models.similarity_model.RAW_FEATURES, which only reads the
input characteristic columns below.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

SEED = 20260904
PROJECT_COUNT = 1500
OUTPUT_PATH = Path(__file__).resolve().parents[1] / "data" / "historical_projects.csv"

SECTORS = ["Roads", "Railways", "Power", "Urban Development", "Water Resources", "Ports", "Airports"]
SECTOR_WEIGHTS = [0.22, 0.18, 0.14, 0.16, 0.12, 0.09, 0.09]
STATES = [
    "Uttar Pradesh", "Maharashtra", "Rajasthan", "Karnataka", "Tamil Nadu",
    "Gujarat", "Madhya Pradesh", "West Bengal", "Odisha", "Bihar",
    "Andhra Pradesh", "Telangana", "Kerala", "Haryana", "Jharkhand",
]
COMPLEX_SECTORS = ["Roads", "Railways", "Water Resources", "Ports"]

NAME_TEMPLATES: dict[str, list[str]] = {
    "Roads": ["{state} National Highway Widening", "{state} Expressway Corridor", "{state} Ring Road Development", "{state} State Highway Upgradation"],
    "Railways": ["{state} Rail Line Doubling", "{state} Railway Electrification Project", "{state} Metro Rail Extension", "{state} Freight Corridor Development"],
    "Power": ["{state} Thermal Power Plant Modernisation", "{state} Transmission Line Augmentation", "{state} Solar Power Park", "{state} Substation Capacity Expansion"],
    "Urban Development": ["{state} Smart City Development", "{state} Urban Renewal Mission", "{state} Affordable Housing Scheme", "{state} Sewerage Network Upgrade"],
    "Water Resources": ["{state} Irrigation Canal Modernisation", "{state} Dam Rehabilitation Project", "{state} River Interlinking Scheme", "{state} Water Supply Augmentation"],
    "Ports": ["{state} Port Terminal Expansion", "{state} Port Connectivity Corridor", "{state} Container Yard Development"],
    "Airports": ["{state} Airport Terminal Development", "{state} Runway Expansion Project", "{state} Airport Cargo Complex"],
}

INTERVENTIONS = [
    "Land Acquisition Task Force",
    "Additional Funding Approval",
    "Milestone Restructuring",
    "Contractor Replacement",
    "Inter-Department Coordination",
    "No Major Intervention",
]
OUTCOME_OPTIONS = ["Successful", "Partially Successful", "No Significant Improvement"]
OUTCOME_PROBS = [0.38, 0.34, 0.28]


def sigmoid(values: np.ndarray) -> np.ndarray:
    return 1 / (1 + np.exp(-values))


def generate_project_names(sector: np.ndarray, state: np.ndarray, rng: np.random.Generator) -> list[str]:
    phases = rng.integers(1, 4, size=len(sector))
    names = []
    for sec, st, phase in zip(sector, state, phases):
        template = rng.choice(NAME_TEMPLATES[sec])
        names.append(f"{template.format(state=st)} - Phase {phase}")
    return names


def generate_historical_projects(count: int = PROJECT_COUNT, seed: int = SEED) -> pd.DataFrame:
    rng = np.random.default_rng(seed)

    sector = rng.choice(SECTORS, size=count, p=SECTOR_WEIGHTS)
    state = rng.choice(STATES, size=count)
    project_name = generate_project_names(sector, state, rng)

    age = rng.integers(6, 90, size=count)
    planned_duration = np.clip(rng.normal(42, 16, size=count), 12, 120).round().astype(int)
    expected_progress = np.clip(age / planned_duration * 100, 4, 99)
    physical_progress = np.clip(expected_progress + rng.normal(0, 8, count), 1, 100).round(1)
    financial_progress = np.clip(physical_progress + rng.normal(0, 9, count), 0, 100).round(1)

    milestones_total = np.clip(np.round(planned_duration / 4 + rng.normal(0, 2, count)), 4, 32).astype(int)
    schedule_pressure = np.clip((expected_progress - physical_progress) / 15, 0, 4)
    milestones_delayed = np.clip(rng.poisson(0.6 + schedule_pressure * 1.3), 0, milestones_total - 1).astype(int)

    complex_sector = np.isin(sector, COMPLEX_SECTORS)
    age_pressure = np.clip((age - 24) / 60, 0, 1)
    land_acquisition_pending = rng.binomial(1, np.clip(.12 + .28 * complex_sector + .18 * age_pressure, .05, .75))
    clearance_pending = rng.binomial(1, np.clip(.10 + .11 * complex_sector + .12 * age_pressure, .04, .55))
    funding_issue = rng.binomial(1, np.clip(.09 + .15 * (financial_progress < physical_progress - 10) + .10 * age_pressure, .03, .48))
    contractor_issue = rng.binomial(1, np.clip(.08 + .13 * (milestones_delayed >= 2) + .07 * complex_sector, .03, .45))

    previous_schedule_deviation = np.clip(
        rng.normal(-1.0 + milestones_delayed * 2.6 + age_pressure * 2.3, 4.5, count), -12, 36
    ).round(1)

    original_cost = np.clip(np.exp(rng.normal(np.log(260), .85, count)), 25, 50000).round(1)

    # Latent risk drives the *baseline* (pre-intervention) outcome; interventions can pull it back.
    latent_risk = (
        -2.1
        + milestones_delayed * .40
        + land_acquisition_pending * 1.35
        + clearance_pending * .70
        + funding_issue * .85
        + contractor_issue * .60
        + np.clip(previous_schedule_deviation, 0, None) * .075
        + np.maximum(financial_progress - physical_progress, 0) * .015
        + age_pressure * .28
        + rng.normal(0, .45, count)
    )
    delay_probability = np.clip(sigmoid(latent_risk), .03, .97)
    baseline_delayed = rng.binomial(1, delay_probability)

    baseline_delay_months = np.where(
        baseline_delayed == 1,
        np.clip(
            2
            + milestones_delayed * 1.3
            + land_acquisition_pending * 4.2
            + clearance_pending * 1.8
            + np.maximum(previous_schedule_deviation, 0) * 0.15
            + rng.normal(0, 2.0, count),
            1, 30,
        ),
        np.clip(rng.normal(0, 0.7, count), 0, 2),
    )
    cost_overrun_rate_baseline = np.clip(
        .02 + np.clip(delay_probability - .30, 0, .65) * .24 + funding_issue * .03 + contractor_issue * .02 + rng.normal(0, .02, count),
        0, .45,
    )
    revised_cost = (original_cost * (1 + cost_overrun_rate_baseline)).round(1)

    # --- Interventions: correlated with the dominant risk driver, with noise so outcomes vary. ---
    need_intervention = (baseline_delayed == 1) | (land_acquisition_pending == 1) | (funding_issue == 1) | (contractor_issue == 1) | (milestones_delayed >= 3)
    base_intervention = np.select(
        [
            ~need_intervention,
            land_acquisition_pending.astype(bool) & (baseline_delayed == 1),
            funding_issue.astype(bool),
            contractor_issue.astype(bool),
            milestones_delayed >= 3,
            clearance_pending.astype(bool),
        ],
        [
            "No Major Intervention",
            "Land Acquisition Task Force",
            "Additional Funding Approval",
            "Contractor Replacement",
            "Milestone Restructuring",
            "Inter-Department Coordination",
        ],
        default="No Major Intervention",
    )
    noise_override = rng.random(count) < 0.10
    random_choice = rng.choice(INTERVENTIONS, size=count)
    intervention_taken = np.where(noise_override, random_choice, base_intervention)

    raw_outcome = rng.choice(OUTCOME_OPTIONS, size=count, p=OUTCOME_PROBS)
    intervention_outcome = np.where(intervention_taken == "No Major Intervention", "Not Applicable", raw_outcome)

    reduction_factor = np.select(
        [intervention_outcome == "Successful", intervention_outcome == "Partially Successful"],
        [0.55, 0.22],
        default=0.0,
    )
    reduction_factor = np.clip(reduction_factor + rng.normal(0, 0.05, count), 0, 0.85)

    actual_delay_months = np.clip(
        np.round(baseline_delay_months * (1 - reduction_factor) + rng.normal(0, 0.4, count)), 0, 30
    ).astype(int)
    cost_overrun_rate_final = np.clip(
        cost_overrun_rate_baseline * (1 - reduction_factor * 0.8) + rng.normal(0, 0.01, count), 0, .5
    )
    actual_cost_overrun_percentage = (cost_overrun_rate_final * 100).round(1)

    delayed = actual_delay_months > 1
    primary_delay_cause = np.select(
        [
            delayed & (land_acquisition_pending == 1),
            delayed & (milestones_delayed >= 2),
            delayed & (contractor_issue == 1),
            delayed & (funding_issue == 1),
            delayed & (clearance_pending == 1),
            delayed,
        ],
        [
            "Land acquisition",
            "Milestone slippage",
            "Contractor performance",
            "Funding release",
            "Pending clearance",
            "Other execution delay",
        ],
        default="No material delay",
    )

    # Closure is a noisy function of how far past its planned duration a project is,
    # not a hard progress cutoff -- older, well-progressed projects are more likely closed out.
    closure_probability = np.clip(sigmoid((age - planned_duration * 0.75) / 10), .03, .97)
    is_closed = rng.binomial(1, closure_probability)
    completed = is_closed == 1
    final_status = np.select(
        [
            ~completed,
            completed & (actual_delay_months <= 1) & (actual_cost_overrun_percentage <= 5),
            completed & (actual_delay_months <= 1) & (actual_cost_overrun_percentage > 5),
            completed & (actual_delay_months > 1),
        ],
        ["Under Review", "Completed", "Completed with Cost Overrun", "Delayed"],
        default="Delayed",
    )

    return pd.DataFrame({
        "project_id": [f"HIST-{index:05d}" for index in range(1, count + 1)],
        "project_name": project_name,
        "sector": sector,
        "state": state,
        "original_cost": original_cost,
        "revised_cost": revised_cost,
        "planned_duration_months": planned_duration,
        "project_age_months": age,
        "physical_progress": physical_progress,
        "financial_progress": financial_progress,
        "milestones_total": milestones_total,
        "milestones_delayed": milestones_delayed,
        "land_acquisition_pending": land_acquisition_pending,
        "clearance_pending": clearance_pending,
        "funding_issue": funding_issue,
        "contractor_issue": contractor_issue,
        "previous_schedule_deviation": previous_schedule_deviation,
        "actual_delay_months": actual_delay_months,
        "actual_cost_overrun_percentage": actual_cost_overrun_percentage,
        "final_status": final_status,
        "primary_delay_cause": primary_delay_cause,
        "intervention_taken": intervention_taken,
        "intervention_outcome": intervention_outcome,
    })


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = generate_historical_projects()
    data.to_csv(OUTPUT_PATH, index=False)
    print(f"Generated {len(data):,} historical projects at {OUTPUT_PATH}")
    print(f"Delay rate: {(data['actual_delay_months'] > 0).mean():.1%}")
    print(f"final_status distribution:\n{data['final_status'].value_counts(normalize=True).round(3).to_string()}")

q
if __name__ == "__main__":
    main()
