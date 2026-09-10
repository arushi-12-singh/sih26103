# PAIMANA Project Intelligence API

A focused FastAPI backend for AI project delay-risk prediction and historical project similarity search.

## Scope

This phase implements:

- XGBoost binary delay-risk prediction with SHAP-based feature explanations (Feature 1)
- Historical project similarity search via scikit-learn NearestNeighbors, with weighted risk features and deterministic aggregated evidence (Feature 2)
- A combined `/project-intelligence` endpoint that runs both against one validated input
- A deterministic Project Priority & Decision Engine that combines Feature 1 and Feature 2 evidence into a transparent, weighted 0-100 priority score, category, and recommended attention level -- no additional model, no LLM (Feature 3)
- Pydantic request and response validation
- Health check and CORS for the Next.js frontend

The SLA alert matrix is an additional capability and does not change the prediction model or project-intelligence contract.

## Project setup

From the `backend` directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

On macOS you also need the OpenMP runtime for XGBoost:

```bash
brew install libomp
```

## Generate and validate the datasets

Feature 1's synthetic project dataset:

```bash
python scripts/generate_projects.py
python scripts/validate_projects.py
```

Feature 2's historical project dataset (outcomes used as similarity-search ground truth):

```bash
python scripts/generate_historical_projects.py
python scripts/validate_historical_projects.py
```

`generate_historical_projects.py` (seed `20260904`) produces at least 1,000 historical infrastructure projects across 7 sectors (Roads, Railways, Power, Urban Development, Water Resources, Ports, Airports) with realistic, noisy — not deterministic — relationships: unresolved land acquisition and milestone slippage raise delay; funding/contractor issues raise cost overrun; larger schedule deviation raises delay; a modeled intervention (`intervention_taken` / `intervention_outcome`) can pull a project's delay and cost overrun back down. `validate_historical_projects.py` checks shape, missing values, duplicate IDs, sector/status distributions, delay and cost-overrun statistics, and asserts those causal relationships actually hold in the generated data.

## Train the models

Feature 1 (delay-risk classifier):

```bash
python scripts/train_delay_model.py
python scripts/test_saved_delay_model.py
```

Saves the complete preprocessing + XGBoost pipeline to `trained_models/delay_risk_pipeline.joblib`.

Feature 2 (similarity engine):

```bash
python scripts/train_similarity_model.py
python scripts/test_similarity_engine.py
```

`train_similarity_model.py` fits a pipeline (`ProjectFeatureEngineer` → `ColumnTransformer` → `FeatureWeighter`) on `data/historical_projects.csv` and a `NearestNeighbors` index on the resulting matrix, then saves the pipeline, the fitted index, and the historical data together to `trained_models/similarity_pipeline/historical_similarity.joblib`. `test_similarity_engine.py` is a standalone smoke test: it loads that artifact directly (no HTTP layer), sends a sample project, prints the top 5 matches, and asserts the similarity scores are sorted descending and within `[0, 100]`.

Both artifacts are loaded once at FastAPI startup (see `app/main.py`'s `lifespan`) and cached on `app.state`; no API request ever retrains or reloads a model.

## Run the backend

```bash
uvicorn app.main:app --reload
```

The API is available at `http://127.0.0.1:8000` and interactive docs are at `/docs`.

For local demos, copy the repository-root `.env.example` to a local `.env` and leave `MSG91_ENABLED=false`. SLA rules and explicitly configured milestone deadlines live in `data/sla_config.json`; they are kept separate from the project CSV.

## Endpoints

### `GET /health`

Returns service status, including whether each model loaded successfully (`model_status` / `similarity_status`) and, if not, why (`model_error` / `similarity_error`).

### `POST /api/v1/predict-risk`

Example request:

```json
{
  "sector": "Roads",
  "state": "Uttar Pradesh",
  "original_cost": 5000,
  "revised_cost": 5400,
  "planned_duration_months": 48,
  "project_age_months": 36,
  "physical_progress": 52,
  "financial_progress": 41,
  "milestones_total": 20,
  "milestones_delayed": 6,
  "land_acquisition_pending": true,
  "clearance_pending": false,
  "funding_issue": true,
  "contractor_issue": false,
  "previous_schedule_deviation": 5
}
```

The response includes a probability, 0-100 risk percentage, `LOW`/`MODERATE`/`HIGH`/`CRITICAL` level, transparent heuristic confidence, deterministic natural-language `summary`, and 3-5 SHAP-ranked `top_risk_factors`. Factors are mapped back from encoded and engineered model features and explicitly labeled as `increases_risk` or `reduces_risk`. The already-trained pipeline is loaded from `trained_models/delay_risk_pipeline.joblib`; requests never retrain the model. The SHAP explainer is created once when the service loads the model and reused across requests.

Example response:

```json
{
  "project_risk": {
    "delay_probability": 0.84243,
    "risk_percentage": 84,
    "risk_level": "CRITICAL",
    "model_confidence": "HIGH",
    "confidence_basis": "Heuristic confidence: probability is at least 30 points from the decision boundary."
  },
  "top_risk_factors": [
    {
      "factor": "financial_physical_gap",
      "impact": "increases_risk",
      "importance": 0.24,
      "description": "A gap between physical and financial progress is increasing project risk."
    },
    {
      "factor": "land_acquisition_pending",
      "impact": "increases_risk",
      "importance": 0.19,
      "description": "Pending land acquisition is a major contributor to the predicted delay risk."
    }
  ],
  "summary": "The project is primarily at risk due to financial-physical progress gap and land acquisition."
}
```

### `POST /api/v1/similar-projects?top_k=5`

Finds the `top_k` (optional query parameter, default `5`, must be between `1` and `10`) most similar historical projects to the given project, via the shared `ProjectFeatureEngineer` → `ColumnTransformer` → `FeatureWeighter` pipeline and a fitted `NearestNeighbors` index. Outcome columns (`actual_delay_months`, `final_status`, etc.) are never used as similarity inputs — only characteristics that would be known about a project in progress. `milestone_delay_ratio`, `land_acquisition_pending`, `funding_issue`, `previous_schedule_deviation`, and `financial_physical_gap` are weighted above the unweighted baseline (see `FEATURE_WEIGHTS` in `app/models/similarity_model.py`) since they're the characteristics most associated with real delay/cost-overrun outcomes in the historical data.

Example request:

```json
{
  "sector": "Railways",
  "state": "Bihar",
  "original_cost": 3200,
  "revised_cost": 4100,
  "planned_duration_months": 54,
  "project_age_months": 60,
  "physical_progress": 38,
  "financial_progress": 22,
  "milestones_total": 18,
  "milestones_delayed": 11,
  "land_acquisition_pending": true,
  "clearance_pending": true,
  "funding_issue": true,
  "contractor_issue": true,
  "previous_schedule_deviation": 14
}
```

Example response (verified live output, truncated to the top 2 matches for brevity):

```json
{
  "similar_projects": [
    {
      "project_id": "HIST-00907",
      "project_name": "Maharashtra Ring Road Development - Phase 2",
      "sector": "Roads",
      "state": "Maharashtra",
      "similarity_score": 23.0,
      "actual_delay_months": 12,
      "actual_cost_overrun_percentage": 18.0,
      "final_status": "Delayed",
      "primary_delay_cause": "Land acquisition",
      "intervention_taken": "Land Acquisition Task Force",
      "intervention_outcome": "No Significant Improvement"
    },
    {
      "project_id": "HIST-01386",
      "project_name": "Bihar Freight Corridor Development - Phase 2",
      "sector": "Railways",
      "state": "Bihar",
      "similarity_score": 22.1,
      "actual_delay_months": 8,
      "actual_cost_overrun_percentage": 11.8,
      "final_status": "Under Review",
      "primary_delay_cause": "Land acquisition",
      "intervention_taken": "Land Acquisition Task Force",
      "intervention_outcome": "Successful"
    }
  ],
  "historical_evidence": {
    "projects_analyzed": 5,
    "average_similarity": 21.9,
    "average_actual_delay_months": 8.8,
    "average_cost_overrun_percentage": 13.8,
    "projects_with_significant_delay": 3,
    "significant_delay_percentage": 60.0,
    "most_common_delay_cause": "Milestone slippage"
  },
  "historical_summary": "Among the 5 most similar historical projects, 3 experienced significant delays. Milestone slippage was the most common contributing factor, with an average delay of 8.8 months."
}
```

`historical_summary` is generated with a deterministic string template from the computed evidence — no LLM is involved anywhere in this API. Similarity scores in the 20-25% range here reflect that this sample project (deliberately extreme, to stress-test the model) doesn't closely match any single historical record; scores rise toward 100% for genuinely close matches, as covered by `tests/test_similarity_unit.py::test_identical_project_scores_near_100_percent`.

### `POST /api/v1/project-intelligence`

Runs risk prediction and historical similarity search for one project in a single call. It validates the input once and passes the same feature dict to both `SavedPredictionService` and `SimilarityService` directly (no internal HTTP requests to `/predict-risk` or `/similar-projects`), then returns a single flat response combining both. If one service is unavailable or fails, the endpoint returns a `503` naming which one and why, rather than a partial or silently-degraded response.

Example request: identical shape to `/predict-risk` and `/similar-projects` above (a single `ProjectRiskRequest` body) — using the same request as the `/similar-projects` example above:

```json
{
  "sector": "Railways",
  "state": "Bihar",
  "original_cost": 3200,
  "revised_cost": 4100,
  "planned_duration_months": 54,
  "project_age_months": 60,
  "physical_progress": 38,
  "financial_progress": 22,
  "milestones_total": 18,
  "milestones_delayed": 11,
  "land_acquisition_pending": true,
  "clearance_pending": true,
  "funding_issue": true,
  "contractor_issue": true,
  "previous_schedule_deviation": 14
}
```

Example response (verified live output, truncated to the top 2 matches for brevity):

```json
{
  "project_risk": {
    "delay_probability": 0.982807,
    "risk_percentage": 98,
    "risk_level": "CRITICAL",
    "model_confidence": "HIGH",
    "confidence_basis": "Heuristic confidence: probability is at least 30 points from the decision boundary."
  },
  "top_risk_factors": [
    {
      "factor": "cost_overrun_percentage",
      "impact": "increases_risk",
      "importance": 0.4637,
      "description": "Cost overrun is contributing to the predicted risk."
    },
    {
      "factor": "land_acquisition_pending",
      "impact": "increases_risk",
      "importance": 0.0566,
      "description": "Pending land acquisition is a major contributor to the predicted delay risk."
    }
  ],
  "risk_summary": "The project is primarily at risk due to cost overrun, milestone delay ratio, and delayed milestones.",
  "similar_projects": [
    {
      "project_id": "HIST-00907",
      "project_name": "Maharashtra Ring Road Development - Phase 2",
      "similarity_score": 23.0,
      "sector": "Roads",
      "state": "Maharashtra",
      "actual_delay_months": 12,
      "actual_cost_overrun_percentage": 18.0,
      "primary_delay_cause": "Land acquisition"
    },
    {
      "project_id": "HIST-01386",
      "project_name": "Bihar Freight Corridor Development - Phase 2",
      "similarity_score": 22.1,
      "sector": "Railways",
      "state": "Bihar",
      "actual_delay_months": 8,
      "actual_cost_overrun_percentage": 11.8,
      "primary_delay_cause": "Land acquisition"
    }
  ],
  "historical_evidence": {
    "projects_analyzed": 5,
    "significant_delay_percentage": 60.0,
    "average_actual_delay_months": 8.8,
    "most_common_delay_cause": "Milestone slippage"
  },
  "historical_summary": "Among the 5 most similar historical projects, 60% experienced significant delays, primarily associated with milestone slippage issues."
}
```

### `POST /api/v1/project-priority`

Feature 3 -- the Project Priority & Decision Engine. It answers "which projects need attention first?" rather than predicting another outcome. `PriorityService` is a **pure, deterministic** function: it never calls a model, never re-runs XGBoost or NearestNeighbors, and never uses an LLM -- the route calls `SavedPredictionService.predict()` and `SimilarityService.find_similar()` directly (in-process, no internal HTTP requests), and `PriorityService.assess()` only reads their already-computed, typed responses.

All weights and normalization thresholds live in one place, `app/config/priority_config.py`, so the methodology can be tuned without touching scoring logic. Every raw signal is normalized to a 0-100 scale *before* weighting (never multiplied raw) via five separate, independently testable functions in `app/services/priority_service.py`:

| Function | Weight | Formula |
|---|---|---|
| `calculate_risk_component` | 0.35 | XGBoost `delay_probability * 100` (e.g. 0.87 -> 87) |
| `calculate_delay_component` | 0.20 | `(delay_probability * avg_historical_delay_months) / MAX_EXPECTED_DELAY_MONTHS * 100` -- an expected-value estimate of months likely lost, normalized against a *configurable* anchor (`MAX_EXPECTED_DELAY_MONTHS`, default 24), not a hardcoded "12 months = 100" rule |
| `calculate_financial_component` | 0.20 | Potential exposure = `revised_cost * (1 + cost_overrun_pct/100)`, then **log-scale** min-max normalized between `FINANCIAL_EXPOSURE_MIN`/`MAX` -- log scaling is what stops one extremely large project from distorting every other project's score |
| `calculate_historical_component` | 0.15 | `significant_delay_percentage * 0.6 + normalized(avg_delay_months) * 0.4` -- blends how *often* comparable projects were delayed with how *severe* those delays were |
| `calculate_urgency_component` | 0.10 | Weighted blend of schedule position (`age/planned_duration`), schedule deviation, and milestone-delay ratio -- all from the project's own current state |

`calculate_priority_score()` then applies only the documented weights (`COMPONENT_WEIGHTS`) to these five already-normalized values -- it does no normalization itself. The final score maps to a `priority_category` (`LOW`/`MEDIUM`/`HIGH`/`CRITICAL`), a `recommended_attention_level`, and a `decision_explanation` built with a deterministic string template from the top 3 contributing signals -- calling the endpoint twice with the same input always returns the same explanation. The response's `score_breakdown` exposes every component's raw score, weight, weighted contribution, and a human-readable description, so the final number is never a black box.

Example request (same project shape used by `/predict-risk` and `/similar-projects` above):

```json
{
  "sector": "Railways",
  "state": "Bihar",
  "original_cost": 3200,
  "revised_cost": 4100,
  "planned_duration_months": 54,
  "project_age_months": 60,
  "physical_progress": 38,
  "financial_progress": 22,
  "milestones_total": 18,
  "milestones_delayed": 11,
  "land_acquisition_pending": true,
  "clearance_pending": true,
  "funding_issue": true,
  "contractor_issue": true,
  "previous_schedule_deviation": 14
}
```

Example response (verified live output):

```json
{
  "priority_score": 70.7,
  "priority_category": "HIGH",
  "recommended_attention_level": "Escalate to program leadership within two weeks.",
  "decision_explanation": "This project is rated HIGH priority (70.7/100). Leading contributors: XGBoost delay probability of 98% (CRITICAL); Revised cost 4,100 with 28.1% overrun over original cost 3,200 drives the estimated exposure; 60% of 5 similar historical projects had significant delays, averaging 8.8 months.",
  "score_breakdown": [
    {
      "name": "risk",
      "score": 98.3,
      "weight": 0.35,
      "weighted_contribution": 34.4,
      "description": "XGBoost delay probability of 98% (CRITICAL)."
    },
    {
      "name": "delay",
      "score": 36.0,
      "weight": 0.2,
      "weighted_contribution": 7.2,
      "description": "Expected delay of 8.6 months (probability 98% x historical average 8.8 months)."
    },
    {
      "name": "financial",
      "score": 70.4,
      "weight": 0.2,
      "weighted_contribution": 14.1,
      "description": "Revised cost 4,100 with 28.1% overrun over original cost 3,200 drives the estimated exposure."
    },
    {
      "name": "historical",
      "score": 50.7,
      "weight": 0.15,
      "weighted_contribution": 7.6,
      "description": "60% of 5 similar historical projects had significant delays, averaging 8.8 months."
    },
    {
      "name": "urgency",
      "score": 74.6,
      "weight": 0.1,
      "weighted_contribution": 7.5,
      "description": "Project is at 100% of its planned duration, with 11/18 milestones delayed and a schedule deviation of 14.0."
    }
  ]
}
```

### SLA alert endpoints

- `GET /api/v1/projects/{project_id}/sla` evaluates configured SLA status and applies duplicate-protected notification rules.
- `POST /api/v1/projects/{project_id}/sla/evaluate` evaluates the same workflow explicitly.
- `POST /api/v1/projects/{project_id}/sla/test-alert` is a controlled demo trigger; its recipient always comes from backend configuration.
- `GET /api/v1/projects/{project_id}/sla/audit` returns masked notification audit events.

`GREEN` is healthy, `AMBER` is approaching the configured deadline, `RED` is breached, and `CRITICAL` is a breach with a high saved-model risk score. With `MSG91_ENABLED=false`, breach alerts are recorded as `dry_run` and no provider request is made. MSG91 credentials are never returned by an API.

## Architecture

- `app/main.py`: application, CORS, lifecycle model loading (all services loaded once at startup), health endpoint
- `app/api/routes/prediction.py`, `similarity.py`, `intelligence.py`, `priority.py`: HTTP contracts and error translation
- `app/schemas/project.py`, `similarity.py`, `intelligence.py`, `priority.py`: Pydantic request/response schemas
- `app/services/priority_service.py`: `PriorityService`, the deterministic Feature 3 decision engine (documented weights, thresholds, and attention-level mapping)
- `app/models/preprocessing.py`: shared `ProjectFeatureEngineer` and feature-name constants used by both Feature 1 training and the Feature 2 similarity pipeline, so preprocessing logic is defined exactly once
- `app/models/similarity_model.py`: `HistoricalSimilarityModel` (NearestNeighbors + weighted preprocessing pipeline) and the shared `build_historical_evidence` aggregation used by both `/similar-projects` and `/project-intelligence`
- `app/services/prediction_service.py`, `similarity_service.py`: service-layer orchestration reused directly by `intelligence.py` (no internal HTTP calls)
- `app/services/explanation_service.py`: SHAP attribution and explanations
- `data/projects.csv`, `scripts/generate_projects.py`, `scripts/validate_projects.py`: Feature 1's synthetic training dataset
- `data/historical_projects.csv`, `scripts/generate_historical_projects.py`, `scripts/validate_historical_projects.py`: Feature 2's historical outcomes dataset
- `tests/test_integration.py`: end-to-end API tests (TC1-TC5) covering Features 1 and 2 and their combination
- `tests/test_similarity_unit.py`: unit tests for similarity-engine internals (leakage guards, empty-dataset handling, feature-weighting scope, JSON-safety of returned types)
- `tests/test_priority.py`: Feature 3 tests -- weight/threshold sanity, endpoint behavior, determinism, and invalid-input handling

## Testing

```bash
python -m pytest tests/ -v
```
