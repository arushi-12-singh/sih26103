"""Quality checks for the generated historical project dataset (data/historical_projects.csv)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "historical_projects.csv"

INPUT_FEATURE_COLUMNS = [
    "project_id", "project_name", "sector", "state", "original_cost", "revised_cost",
    "planned_duration_months", "project_age_months", "physical_progress", "financial_progress",
    "milestones_total", "milestones_delayed", "land_acquisition_pending", "clearance_pending",
    "funding_issue", "contractor_issue", "previous_schedule_deviation",
]
OUTCOME_COLUMNS = [
    "actual_delay_months", "actual_cost_overrun_percentage", "final_status",
    "primary_delay_cause", "intervention_taken", "intervention_outcome",
]
EXPECTED_COLUMNS = INPUT_FEATURE_COLUMNS + OUTCOME_COLUMNS

EXPECTED_SECTORS = {"Roads", "Railways", "Power", "Urban Development", "Water Resources", "Ports", "Airports"}
EXPECTED_STATUSES = {"Completed", "Delayed", "Completed with Cost Overrun", "Under Review"}
EXPECTED_INTERVENTIONS = {
    "Land Acquisition Task Force", "Additional Funding Approval", "Milestone Restructuring",
    "Contractor Replacement", "Inter-Department Coordination", "No Major Intervention",
}
EXPECTED_OUTCOMES = {"Successful", "Partially Successful", "No Significant Improvement", "Not Applicable"}


def main() -> None:
    if not DATA_PATH.exists():
        raise FileNotFoundError(f"Dataset not found: {DATA_PATH}. Run generate_historical_projects.py first.")

    data = pd.read_csv(DATA_PATH)

    print("Dataset shape:", data.shape)
    if len(data) < 1000:
        raise ValueError(f"Expected at least 1000 historical projects, found {len(data)}")

    missing_columns = sorted(set(EXPECTED_COLUMNS) - set(data.columns))
    if missing_columns:
        raise ValueError(f"Missing expected columns: {missing_columns}")

    print("\nMissing values:")
    print(data[EXPECTED_COLUMNS].isna().sum().to_string())
    if data[EXPECTED_COLUMNS].isna().any().any():
        raise ValueError("Dataset contains missing values in expected columns")

    duplicate_ids = int(data["project_id"].duplicated().sum())
    print("\nDuplicate project IDs:", duplicate_ids)
    if duplicate_ids:
        raise ValueError(f"Found {duplicate_ids} duplicate project_id values")

    print("\nSector distribution:")
    sector_share = data["sector"].value_counts(normalize=True).round(4)
    print(sector_share.to_string())
    unexpected_sectors = set(data["sector"].unique()) - EXPECTED_SECTORS
    if unexpected_sectors:
        raise ValueError(f"Unexpected sector values: {sorted(unexpected_sectors)}")
    if sector_share.min() < 0.02:
        raise ValueError(f"A sector is under-represented (<2%): {sector_share.idxmin()}")

    print("\nfinal_status distribution:")
    status_share = data["final_status"].value_counts(normalize=True).round(4)
    print(status_share.to_string())
    if set(data["final_status"].unique()) != EXPECTED_STATUSES:
        raise ValueError(f"final_status must contain exactly {EXPECTED_STATUSES}, found {set(data['final_status'].unique())}")
    if status_share.min() < 0.02:
        raise ValueError(f"A final_status category is under-represented (<2%): {status_share.idxmin()}")

    if not set(data["intervention_taken"].unique()).issubset(EXPECTED_INTERVENTIONS):
        raise ValueError("intervention_taken contains unexpected values")
    if not set(data["intervention_outcome"].unique()).issubset(EXPECTED_OUTCOMES):
        raise ValueError("intervention_outcome contains unexpected values")
    no_intervention_mask = data["intervention_taken"] == "No Major Intervention"
    if not (data.loc[no_intervention_mask, "intervention_outcome"] == "Not Applicable").all():
        raise ValueError("Rows with 'No Major Intervention' must have intervention_outcome == 'Not Applicable'")
    if (data.loc[~no_intervention_mask, "intervention_outcome"] == "Not Applicable").any():
        raise ValueError("Rows with an intervention must not have intervention_outcome == 'Not Applicable'")

    print("\nDelay statistics (actual_delay_months):")
    print(data["actual_delay_months"].describe().round(2).to_string())
    if (data["actual_delay_months"] < 0).any():
        raise ValueError("actual_delay_months must be non-negative")
    delay_rate = (data["actual_delay_months"] > 0).mean()
    print(f"Delay rate (>0 months): {delay_rate:.1%}")
    if not .10 <= delay_rate <= .90:
        raise ValueError(f"Delay rate is outside a realistic range: {delay_rate:.1%}")

    print("\nCost overrun statistics (actual_cost_overrun_percentage):")
    print(data["actual_cost_overrun_percentage"].describe().round(2).to_string())
    if (data["actual_cost_overrun_percentage"] < 0).any():
        raise ValueError("actual_cost_overrun_percentage must be non-negative")

    if (data["milestones_delayed"] > data["milestones_total"]).any():
        raise ValueError("milestones_delayed cannot exceed milestones_total")
    for column in ("physical_progress", "financial_progress"):
        if not data[column].between(0, 100).all():
            raise ValueError(f"{column} must be within [0, 100]")
    if (data["original_cost"] <= 0).any() or (data["revised_cost"] <= 0).any():
        raise ValueError("original_cost and revised_cost must be positive")

    print("\nRelationship sanity checks:")
    by_land = data.groupby("land_acquisition_pending")["actual_delay_months"].mean().rename("mean_delay_months")
    print(by_land.to_string())
    if by_land.loc[1] <= by_land.loc[0]:
        raise ValueError("Unresolved land acquisition should be associated with higher average delay")

    by_funding = data.groupby("funding_issue")["actual_cost_overrun_percentage"].mean().rename("mean_cost_overrun_pct")
    print(by_funding.to_string())
    if by_funding.loc[1] <= by_funding.loc[0]:
        raise ValueError("Funding issues should be associated with higher average cost overrun")

    high_deviation = data["previous_schedule_deviation"] > data["previous_schedule_deviation"].median()
    deviation_delay = data.groupby(high_deviation)["actual_delay_months"].mean()
    print(deviation_delay.rename("mean_delay_months (by above-median schedule deviation)").to_string())
    if deviation_delay.loc[True] <= deviation_delay.loc[False]:
        raise ValueError("Larger previous schedule deviation should be associated with higher average delay")

    successful = data.loc[data["intervention_outcome"] == "Successful", "actual_delay_months"]
    no_improvement = data.loc[data["intervention_outcome"] == "No Significant Improvement", "actual_delay_months"]
    print(f"Mean delay after successful intervention: {successful.mean():.2f}")
    print(f"Mean delay with no significant improvement: {no_improvement.mean():.2f}")
    if successful.mean() >= no_improvement.mean():
        raise ValueError("Successful interventions should be associated with lower average delay than ineffective ones")

    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
