# PAIMANA Project Intelligence API

A focused FastAPI backend for AI project delay-risk prediction with explainable feature contributions.

## Scope

This phase implements only:

- XGBoost binary delay-risk prediction
- SHAP-based feature explanations
- Pydantic request and response validation
- Health check and CORS for the Next.js frontend

Authentication, persistence, anomaly detection, RAG, LLM features, and other product capabilities are intentionally out of scope.

## Run locally

From the `backend` directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

The API is available at `http://127.0.0.1:8000` and interactive docs are at `/docs`.

## Endpoints

### `GET /health`

Returns the service status.

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

The model is loaded during FastAPI startup. API requests only perform preprocessing, prediction, and SHAP explanation; they do not retrain the model.

## Architecture

- `app/main.py`: application, CORS, lifecycle model loading, health endpoint
- `app/api/routes/prediction.py`: HTTP contract and error translation
- `app/schemas/project.py`: Pydantic API schemas
- `app/models/prediction_model.py`: training data validation and XGBoost model
- `app/services/prediction_service.py`: prediction orchestration and risk classification
- `app/services/explanation_service.py`: SHAP attribution and explanations
- `data/projects.csv`: reproducible synthetic training dataset with 5,000 projects
- `scripts/generate_projects.py`: seeded generator for realistic project features and delay labels
- `scripts/validate_projects.py`: shape, missing-value, balance, statistics, and directional risk checks

## Generate and validate data

From the `backend` directory:

```bash
python scripts/generate_projects.py
python scripts/validate_projects.py
```

The generator uses seed `20260903`. The `is_delayed` label is sampled from a noisy latent risk score influenced by milestone slippage, land acquisition, clearances, funding, contractor issues, schedule deviation, and progress gaps. This keeps the class boundary realistic while preserving reproducibility.

## Train and test the model

```bash
python scripts/train_delay_model.py
python scripts/test_saved_delay_model.py
```

Training uses a stratified 80/20 split and saves the complete preprocessing and model pipeline to `trained_models/delay_risk_pipeline.joblib`. This artifact contains feature engineering, median numeric imputation, unknown-safe categorical one-hot encoding, and the XGBoost classifier. Evaluation metrics are saved to `trained_models/delay_risk_metrics.json`.
