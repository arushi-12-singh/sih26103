"""Create a reproducible historical project corpus with observed outcomes."""

from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.generate_projects import generate_projects

OUTPUT_PATH = ROOT / "data" / "historical_projects.csv"


def main() -> None:
    data = generate_projects(count=1500, seed=20260904)
    rng = np.random.default_rng(20260904)
    data = data.rename(columns={"is_delayed": "historically_delayed"})
    delay_months = np.where(
        data["historically_delayed"].to_numpy() == 1,
        np.clip(2 + data["milestones_delayed"].to_numpy() * 1.2 + data["land_acquisition_pending"].to_numpy() * 4 + rng.normal(0, 1.5, len(data)), 1, 24),
        np.clip(rng.normal(0, 0.8, len(data)), 0, 2),
    ).round().astype(int)
    delayed = data["historically_delayed"].eq(1)
    causes = np.select(
        [delayed & data["land_acquisition_pending"].eq(1), delayed & data["milestones_delayed"].ge(2), delayed & data["funding_issue"].eq(1), delayed & data["clearance_pending"].eq(1)],
        ["Land acquisition", "Milestone slippage", "Funding release", "Pending clearance"],
        default="No material delay",
    )
    data["project_id"] = [f"HIST-{index:05d}" for index in range(1, len(data) + 1)]
    data["actual_delay_months"] = delay_months
    data["actual_outcome"] = np.where(delay_months > 0, data["actual_delay_months"].astype(str) + "-month delay", "Delivered within plan")
    data["primary_delay_cause"] = causes
    data.drop(columns=["historically_delayed"], inplace=True)
    data.to_csv(OUTPUT_PATH, index=False)
    print(f"Generated {len(data):,} historical projects at {OUTPUT_PATH}")

q
if __name__ == "__main__":
    main()
