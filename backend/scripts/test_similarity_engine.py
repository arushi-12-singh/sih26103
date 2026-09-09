"""Standalone smoke test for the historical similarity engine (no HTTP layer).

Loads the trained artifact directly, sends a sample project, and checks that the
returned matches are sorted by similarity and fall within a valid [0, 100] range.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.models.similarity_model import HistoricalSimilarityModel

MODEL_PATH = ROOT / "trained_models" / "similarity_pipeline" / "historical_similarity.joblib"

SAMPLE_PROJECT = {
    "sector": "Roads",
    "state": "Bihar",
    "original_cost": 850.0,
    "revised_cost": 1020.0,
    "planned_duration_months": 48,
    "project_age_months": 40,
    "physical_progress": 55.0,
    "financial_progress": 70.0,
    "milestones_total": 12,
    "milestones_delayed": 5,
    "land_acquisition_pending": True,
    "clearance_pending": True,
    "funding_issue": True,
    "contractor_issue": False,
    "previous_schedule_deviation": 9.0,
}


def main() -> None:
    model = HistoricalSimilarityModel(MODEL_PATH)
    matches, evidence = model.find_matches(SAMPLE_PROJECT, top_k=5)

    print(f"Loaded similarity engine from {MODEL_PATH}")
    print(f"Sample project: {SAMPLE_PROJECT['sector']} in {SAMPLE_PROJECT['state']}\n")
    print(f"Top {len(matches)} similar historical projects:")
    for rank, match in enumerate(matches, start=1):
        print(
            f"  {rank}. {match['project_id']} ({match['project_name']}) - "
            f"{match['similarity_score']:.1f}% similar | status={match['final_status']} | "
            f"delay={match['actual_delay_months']}mo | cost_overrun={match['actual_cost_overrun_percentage']:.1f}% | "
            f"cause={match['primary_delay_cause']} | intervention={match['intervention_taken']} -> {match['intervention_outcome']}"
        )

    scores = [m["similarity_score"] for m in matches]
    is_sorted_desc = all(scores[i] >= scores[i + 1] for i in range(len(scores) - 1))
    print(f"\nSimilarity scores: {scores}")
    print(f"Sorted descending: {is_sorted_desc}")
    if not is_sorted_desc:
        raise AssertionError(f"Similarity scores are not sorted descending: {scores}")
    if not all(0 <= s <= 100 for s in scores):
        raise AssertionError(f"Similarity scores out of [0, 100] range: {scores}")

    print(f"\nAggregated evidence: {evidence}")
    print("All checks passed.")


if __name__ == "__main__":
    main()
