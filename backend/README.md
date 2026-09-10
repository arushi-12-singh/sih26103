# PAIMANA Project Intelligence API

A focused FastAPI backend for AI project delay-risk prediction and historical project similarity search.

## Scope

This phase implements:

- XGBoost binary delay-risk prediction with SHAP-based feature explanations (Feature 1)
- Historical project similarity search via scikit-learn NearestNeighbors, with weighted risk features and deterministic aggregated evidence (Feature 2)
- A combined `/project-intelligence` endpoint that runs both against one validated input
- A deterministic Project Priority & Decision Engine that combines Feature 1 and Feature 2 evidence into a transparent, weighted 0-100 priority score, category, and recommended attention level -- no additional model, no LLM (Feature 3)
- A GIS boundary data layer for environmental/restricted areas: GeoJSON-compatible Polygon/MultiPolygon storage, strict geometry validation, a swappable repository (GeoJSON file today, PostGIS ready), and a reusable GeoJSON/Shapefile/GeoPackage importer (Feature 4)
- A deterministic GIS Spatial Analysis Engine that computes real geometric collisions between a project location and those boundaries -- metre-accurate buffers in a projected CRS, spatial-index prefiltering, and configurable severity/clearance rules (Feature 5)
- A REST API over the GIS subsystem: collision check, nearby boundaries, boundary list/detail, boundary administration, and assessment history -- with bearer-token authentication and VIEW_GIS / RUN_GIS_ANALYSIS / MANAGE_GIS_DATA role-based access control (Feature 6)
- GIS integration into the intelligence pipeline: a structured (geometry-free) screening signal, a configurable GIS component in the priority engine, and rule-based intervention recommendations (Feature 7)
- Pydantic request and response validation
- Health check and CORS for the Next.js frontend

Anomaly detection, RAG, LLM features, and other product capabilities are intentionally out of scope. Authentication exists only for the GIS API (see below) and is a deliberately minimal seam, not a general auth system.

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

## Endpoints

### `GET /health`

Returns service status, including whether each model loaded successfully (`model_status` / `similarity_status`) and, if not, why (`model_error` / `similarity_error`). `gis_status` reports which GIS boundary storage backend is active (`geojson-file` or `postgis`), `spatial_engine_status` whether the spatial analysis engine loaded, and `gis_auth_status` whether the GIS API can authenticate callers (`configured` / `unconfigured` / `disabled`).

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

## GIS boundary data layer (Feature 4)

Stores environmental and otherwise restricted geographic boundaries -- wildlife sanctuaries, national parks, forests, eco-sensitive zones, tiger reserves, Ramsar wetlands, and other restricted zones -- so later features can ask spatial questions about project sites. This phase is the data layer only: model, storage, validation, import, and seed data. No API routes, no frontend, no collision/intersection calculation.

### Data model

Every boundary carries exactly ten fields (`GISBoundary` in `app/schemas/boundary.py`):

| Field | Type | Notes |
|---|---|---|
| `id` | `str` | Deterministic UUIDv5 of `(source, name, category)`, so re-importing a source file updates records instead of duplicating them |
| `name` | `str` | |
| `category` | `BoundaryCategory` | `WILDLIFE_SANCTUARY`, `NATIONAL_PARK`, `FOREST`, `ECO_SENSITIVE_ZONE`, `TIGER_RESERVE`, `RAMSAR_WETLAND`, `OTHER_RESTRICTED_ZONE` |
| `state` | `str` | |
| `district` | `str \| None` | Nullable: real protected areas routinely span districts or are published without one |
| `geometry` | `BoundaryGeometry` | GeoJSON `Polygon` or `MultiPolygon`, always EPSG:4326, validated on construction |
| `source` | `str` | Required -- no boundary may exist without a traceable origin |
| `source_url` | `str \| None` | |
| `last_updated` | `date` | Date the SOURCE dataset was last updated, not the row's write time |
| `metadata` | `dict` | Free-form source attributes; `is_demo` / `data_notice` mark generated records |

Everything is stored in **EPSG:4326** (WGS84 lon/lat) regardless of backend -- the CRS PostGIS geography casts require, RFC 7946 GeoJSON mandates, and Leaflet/MapLibre consume directly -- so no reprojection is needed on read or at migration time.

### Storage backends

`GISBoundaryRepository` (`app/repositories/gis_boundary_repository.py`) is the contract; two implementations satisfy it, and `build_repository()` picks one at startup:

- **`GeoJSONFileBoundaryRepository`** (default, what runs here). Persists one RFC 7946 FeatureCollection to `data/gis/boundaries.geojson`, written atomically via a temp file + `os.replace`. The on-disk artifact is itself a valid GIS file -- it opens in QGIS, `geopandas.read_file`, and `ogr2ogr` -- so the file backing the service is also the file you hand to PostGIS. Each record's bbox is cached in memory so bbox queries do a cheap rectangle test before touching Shapely.
- **`PostGISBoundaryRepository`**. The same contract over PostgreSQL + PostGIS, selected automatically when `DATABASE_URL` is set and `psycopg` is installed. Geometry crosses the boundary as GeoJSON both ways (`ST_GeomFromEWKT` on write, `ST_AsGeoJSON` on read), so callers hold identical `GISBoundary` objects either way. **This backend is SQL-complete but has not been exercised against a live PostGIS instance** -- no server is installed in this environment. Its query translation (`build_where_clause`, `row_to_boundary`) is unit-tested; its execution paths are not.

Migrating to PostGIS is: apply the migration, set `DATABASE_URL`, re-run the seed script. No service or route code changes.

### Geometry validation

`app/models/boundary_geometry.py` owns every geometric rule, so the repository, service, importer, and seed script all reject the same things for the same reasons. Policy: **invalid geometry is rejected, never silently repaired.** Rejected: non-areal types (Point/LineString/GeometryCollection), self-intersections, holes outside their shell, coordinates off the globe, degenerate rings, and areas outside the `MIN_AREA_SQKM`/`MAX_AREA_SQKM` guards. The only two normalizations applied are lossless ones every GIS toolchain treats as equivalent input: closing a ring whose repeat was dropped by an exporter, and re-winding rings to RFC 7946 orientation (exterior counterclockwise, holes clockwise).

Because `BoundaryGeometry` runs the validator in its own Pydantic model validator, an invalid boundary cannot be *instantiated* -- let alone persisted -- at any entry point.

### Demo dataset

`data/gis/demo_boundaries.geojson` contains six **fictional** boundaries in a made-up "DEMO STATE": Demo National Park, Demo Eco-Sensitive Zone (a buffer ring with a hole where the park sits), Demo Wildlife Sanctuary, Demo Protected Forest, Demo Tiger Reserve (a two-part MultiPolygon), and Demo Ramsar Wetland.

> **DEMO DATA - NOT OFFICIAL BOUNDARIES**
>
> These geometries are invented, correspond to no real protected area, and must never be presented as official government boundaries or used for any real clearance, siting, or compliance decision.

That is enforced, not just documented: seeded records are stamped `metadata.is_demo = true`, every rendered Feature carries a `data_notice` property, any `BoundaryFeatureCollection` containing one sets a top-level `notice`, `stats()` reports the demo/official split, `BoundaryQuery(include_demo=False)` excludes them, and `seed_gis_boundaries.py` refuses `--official` on the built-in demo dataset. `tests/test_gis_boundaries.py::TestDemoDataIsNeverPresentedAsOfficial` covers all of it.

### Seeding

```bash
python scripts/migrate.py                    # initialize the store (or apply SQL against PostGIS)
python scripts/seed_gis_boundaries.py        # load the demo dataset
python scripts/seed_gis_boundaries.py --status
```

Other flags: `--reset` (wipe first), `--dry-run` (validate and report, write nothing). Re-running is safe -- deterministic ids mean an import updates rather than duplicates.

To import a real dataset instead:

```bash
python scripts/seed_gis_boundaries.py \
    --file /path/to/protected_areas.shp \
    --source "State Forest Department" \
    --source-url https://example.gov.in/datasets/protected-areas \
    --last-updated 2026-04-01 \
    --default-state "Madhya Pradesh" \
    --official
```

Records are marked as demo data unless `--official` is passed, so an unverified import cannot quietly enter the store looking authoritative.

### Importer

`BoundaryImporter` (`app/services/boundary_importer.py`) reads **GeoJSON, ESRI Shapefile, and GeoPackage**. It:

- **detects the source CRS** -- the pre-RFC-7946 `crs` member for GeoJSON (including OGC URN forms like `urn:ogc:def:crs:EPSG::32644`), the `.prj` sidecar for shapefiles, `gpkg_spatial_ref_sys` for GeoPackages -- and assumes WGS84 when nothing is declared, per RFC 7946;
- **transforms to EPSG:4326** when they differ (pyproj for GeoJSON, geopandas `to_crs` for the GDAL formats), and reports whether it did;
- **validates every geometry** and **rejects invalid features individually** rather than failing the whole file -- one bad record does not lose the other 999;
- **preserves source attribution**: `source` is a required argument, and source attribute columns are carried into `metadata` (coerced JSON-safe rather than dropped, so a GeoPackage `DATE` column or a numpy scalar survives as provenance);
- returns an `ImportReport` accounting for every feature read as imported, duplicate, or rejected-with-reason. Nothing is dropped silently.

Column names are configurable per dataset via `FieldMapping` (candidate column names tried in order, case-insensitively) rather than hardcoded, and category vocabulary is resolved through `CATEGORY_ALIASES` in `app/config/gis_config.py` ("WLS", "Ramsar Site", "Reserved Forest", ...). An unrecognised but present category becomes `OTHER_RESTRICTED_ZONE` instead of dropping a real boundary over a vocabulary mismatch.

### Dependencies

The core layer needs only `shapely` (in `requirements.txt`): GeoJSON import, geometry validation, and the file backend. Shapefile/GeoPackage import, CRS transformation, and the PostGIS backend need the optional extras, imported lazily so a machine that never uses them does not have to install the GDAL stack:

```bash
pip install -r requirements-gis.txt    # geopandas, pyproj, psycopg
```

Without them, those paths fail with a message naming exactly what to install -- never with a wrong-CRS silent success.


## GIS Spatial Analysis Engine (Feature 5)

Given a project's latitude, longitude, and a buffer radius in metres, computes the actual geometric relationship between that project and every boundary in the Feature 4 store. Backend only -- no API routes and no UI in this phase.

```python
from app.services.spatial_analysis_service import build_spatial_analysis_service

engine = build_spatial_analysis_service()
result = engine.analyze(latitude=21.20, longitude=78.20, buffer_meters=1000)
```

### Pipeline

```
lat / lon
   -> validate coordinates              app/models/projection.py
   -> local metre-based CRS             AEQD centred on the point (or UTM)
   -> project the point                 exact origin under AEQD
   -> buffer IN METRES                  never in degrees
   -> project the search envelope back to degrees
   -> spatial index lookup              STRtree, or PostGIS GiST via `&&`
   -> exact geometry intersection       per candidate, in metres
   -> distance / intersection area / overlap %
   -> classify collision type, severity, clearance
```

This is pure computational geometry. There is no model, no LLM, no text or name matching, no hardcoded coordinates, and no hardcoded verdicts anywhere in the path -- every number comes from the input coordinates and the stored geometry, and identical inputs always produce identical output (`test_repeated_analysis_is_byte_identical`).

### Never buffering in degrees

A degree of longitude is ~111 km at the equator and ~0 km at the poles, so `point.buffer(1000)` on lon/lat data is not a 1 km buffer -- it is a 1000-degree shape, and every distance, area, and overlap derived from it is meaningless. The engine therefore projects first and measures second:

- **`aeqd`** (default) -- azimuthal equidistant centred on the project point. Distance *from that point* is exact by construction and the metre buffer is a true circle, which is what every primary measurement here is relative to.
- **`utm`** -- the point's UTM zone, for workflows already anchored to UTM. `test_utm_strategy_agrees_with_aeqd` asserts the two never disagree by more than 0.5%.

Degrees appear exactly once, in the bounding box handed to the spatial index -- and that box is obtained by projecting the real metre buffer *back* to WGS84, never by converting metres to degrees by hand. Distances are cross-checked in the tests against pyproj's geodesic solver (`Geod.inv`), which shares no code with the engine's projection path.

### Collision types

Evaluated most-severe-first, so a boundary is classified exactly once:

| Type | Meaning |
|---|---|
| `DIRECT_COLLISION` | The project point lies inside the boundary polygon |
| `BUFFER_COLLISION` | The point is outside, but the metre buffer intersects the boundary |
| `NEARBY` | No intersection, but the boundary is within the proximity threshold |
| `CLEAR` | Nothing relevant intersected or nearby (an overall verdict only) |

The proximity threshold is `max(NEARBY_THRESHOLD_METERS, buffer_meters * NEARBY_BUFFER_MULTIPLIER)`, so a large buffer widens the advisory band instead of being capped by a fixed number.

### Per-boundary output

Every relevant boundary returns `boundary_name`, `category` + `category_label`, `state`/`district`, `distance_meters` (0 when inside), `intersection_area_sqm`, `buffer_overlap_percentage`, `boundary_area_sqm`, `collision_type`, `severity`, `severity_rank`, `clearance_required`, and `is_demo`. Results are sorted most-severe, then nearest, then by id -- a total order, so output is reproducible.

### Severity and clearance rules

All thresholds live in `app/config/spatial_config.py`; nothing is scattered through the engine. Severity is an integer rank built from three additive terms, which keeps it auditable -- given a result you can reconstruct exactly which term produced the band:

```
rank = COLLISION_TYPE_BASE_RANK[type]     # DIRECT 3, BUFFER 2, NEARBY 1
     + CATEGORY_SENSITIVITY[category]     # +1 for statutorily strict designations
     + overlap escalation                 # +1 at 25% of buffer, +2 at 60%
```

clamped into `SEVERITY_BY_RANK` (1 `LOW` -> 4 `CRITICAL`). `calculate_severity()` returns a `SeverityRuleExplanation` carrying all three terms, so a severity is never a black box. Clearance is flagged when the collision type is in `CLEARANCE_REQUIRED_COLLISION_TYPES`, or when a `NEARBY` boundary's category is in `CLEARANCE_ON_PROXIMITY_CATEGORIES`.

`tests/test_spatial_analysis.py::TestSeverityRules::test_no_severity_threshold_is_hardcoded_in_the_service` scans the engine source and fails if any numeric literal other than 0, 1, or 100 appears in it.

### Performance

The engine never compares the project against every boundary. It asks the repository for a bbox query, and both backends answer through a real spatial index:

- **File backend** -- a Shapely `STRtree` built lazily over stored geometries, invalidated on any mutation and rebuilt on next use, so it can never answer from stale geometry.
- **PostGIS backend** -- `geometry && ST_MakeEnvelope(...)`, served by the GiST index from `migrations/0001_create_gis_boundaries.sql`.

Both are bounding-box prefilters and therefore over-inclusive by design; every candidate is then tested against real geometry. `analyze(..., use_index=False)` is an audit mode that skips the prefilter and scans exhaustively -- `test_prefiltering_never_drops_a_real_collision` asserts the two paths return identical verdicts.

### Configuration

`app/config/spatial_config.py` holds buffer bounds and circle resolution, the proximity threshold and multiplier, the projection strategy, the edge tolerance, the severity ranks/sensitivities/escalation rules, the clearance rules, and output rounding. It self-validates on import: inconsistent severity ordering or unsorted escalation thresholds raise at startup rather than producing quietly wrong severities.

### Tests

`tests/test_spatial_analysis.py` -- 91 tests covering all eleven required cases (CLEAR, DIRECT_COLLISION, BUFFER_COLLISION, NEARBY, multiple intersections, varying buffer sizes, Polygon, MultiPolygon, invalid coordinates, invalid geometry, CRS transformation) plus severity rules, clearance rules, spatial indexing, and demo-data provenance. Test geometry is *constructed*, never hardcoded: `square_boundary()` builds a boundary at an exact metre offset by working in the projected CRS and converting back, so expected distances are derived from the same geometry the engine sees.


## GIS REST API (Feature 6)

Exposes Features 4 and 5 over HTTP. The routes are a thin translation layer: they call `SpatialAnalysisService` and `GISBoundaryService` directly in-process -- never via internal HTTP requests -- exactly as `intelligence.py` and `priority.py` do for Features 1 and 2. No response value is fabricated in the route layer; every number comes from the engine or the store.

| Method | Path | Permission |
|---|---|---|
| `POST` | `/api/v1/gis/check-collision` | `RUN_GIS_ANALYSIS` |
| `GET` | `/api/v1/gis/nearby-boundaries` | `VIEW_GIS` |
| `GET` | `/api/v1/gis/boundaries` | `VIEW_GIS` |
| `GET` | `/api/v1/gis/boundaries/{id}` | `VIEW_GIS` |
| `POST` | `/api/v1/gis/boundaries` | `MANAGE_GIS_DATA` |
| `DELETE` | `/api/v1/gis/boundaries/{id}` | `MANAGE_GIS_DATA` |
| `POST` | `/api/v1/gis/assessments` | `RUN_GIS_ANALYSIS` |
| `GET` | `/api/v1/gis/assessments/{project_id}` | `VIEW_GIS` |

### Authentication and RBAC -- read this first

**This project had no authentication layer before Feature 6.** The README listed auth as out of scope and no module referenced tokens, users, roles, or permissions, so there was nothing to reuse. `app/api/security.py` and `app/config/auth_config.py` implement a deliberately small bearer-token + role layer whose purpose is to be a clean **seam**: routes depend on `require_permission(...)` and never on how a caller was identified, so replacing this with real OIDC/JWT or an API gateway means rewriting `resolve_principal` and nothing else.

It provides opaque bearer tokens stored as SHA-256 hashes, mapped to roles, mapped to permissions. It does **not** provide token expiry, refresh, revocation lists, rate limiting, audit trails, or credential handling. Do not extend it into a full auth system -- replace it.

| Role | Permissions |
|---|---|
| `GIS_VIEWER` | `VIEW_GIS` |
| `GIS_ANALYST` | `VIEW_GIS`, `RUN_GIS_ANALYSIS` |
| `GIS_ADMIN` | `VIEW_GIS`, `RUN_GIS_ANALYSIS`, `MANAGE_GIS_DATA` |

Issue tokens (raw values are printed once and are not recoverable; the registry stores only hashes and is gitignored):

```bash
python scripts/generate_api_tokens.py                          # one token per role
python scripts/generate_api_tokens.py --role GIS_ANALYST --id alice
python scripts/generate_api_tokens.py --show                   # ids and roles only
```

```bash
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/v1/gis/boundaries
```

The default posture is **fail-closed**: with no registry configured, protected endpoints return `503` naming the fix rather than admitting everyone. `GIS_AUTH_MODE=disabled` grants every request admin rights and exists for local development only -- it must be an explicit opt-in, never something a missing file falls back to. `GET /health` reports `gis_auth_status` as `configured`, `unconfigured`, or `disabled`.

### `POST /api/v1/gis/check-collision`

```json
{ "project_id": "PRJ-DEMO-1", "latitude": 21.20, "longitude": 78.20, "buffer_meters": 25000 }
```

`project_id` is optional and echoed back. Optional `categories`, `include_demo`, and `include_geometry` narrow the scan or trim the payload.

Response (verified live output against the demo dataset, collisions abbreviated):

```json
{
  "project_id": "PRJ-DEMO-1",
  "project_location": { "latitude": 21.2, "longitude": 78.2 },
  "buffer_meters": 25000.0,
  "proximity_threshold_meters": 50000.0,
  "overall_status": "DIRECT_COLLISION",
  "overall_severity": "CRITICAL",
  "clearance_required": true,
  "boundaries_checked": 6,
  "candidates_examined": 4,
  "collision_count": 4,
  "collisions": [
    {
      "boundary_name": "Demo National Park",
      "category": "NATIONAL_PARK",
      "collision_type": "DIRECT_COLLISION",
      "severity": "CRITICAL",
      "distance_meters": 0.0,
      "intersection_area_sqm": 459846036.83,
      "buffer_overlap_percentage": 23.4243,
      "clearance_required": true,
      "geometry": { "type": "Polygon", "coordinates": [] }
    }
  ],
  "clearance_flags": [
    {
      "boundary_name": "Demo National Park",
      "severity": "CRITICAL",
      "reason": "The project location lies inside Demo National Park (National Park); 23.4% of the analysis buffer falls within it."
    }
  ],
  "geojson": { "type": "FeatureCollection", "features": [] },
  "projected_crs": "+proj=aeqd +lat_0=21.2 +lon_0=78.2 ...",
  "contains_demo_data": true,
  "notice": "DEMO DATA - NOT OFFICIAL BOUNDARIES"
}
```

**GeoJSON for the frontend.** `geojson` is a FeatureCollection carrying the project point, the *tested buffer* (the true metre circle projected back to WGS84 -- the exact shape the engine used), and every colliding boundary. Each feature's `role` property is `project_location`, `analysis_buffer`, or `boundary`, so a map can style them without guessing, and boundary features carry their collision type, severity, distance, and overlap in their properties. Nothing needs transformation before it reaches Leaflet/MapLibre.

`clearance_flags` reasons are built from a deterministic template over the computed values -- the same result always produces the same sentence, and no language model is involved.

### `GET /api/v1/gis/nearby-boundaries`

`latitude`, `longitude`, `radius_meters` (default `NEARBY_THRESHOLD_METERS`), optional repeatable `category`, plus `include_demo` and `include_geometry`.

Distances are **measured, not bounding-box filtered**: the endpoint runs the engine with a zero buffer and the requested radius as the proximity threshold, so a boundary containing the point reports `distance_meters: 0` and `contains_point: true`, and a boundary whose bbox overlaps the radius but whose geometry does not is correctly excluded.

### `GET /api/v1/gis/boundaries`

Filters: `category` (repeatable), `state`, `district`, `search` (case-insensitive substring of the name), plus `include_demo`, `include_geometry`, `limit`, `offset`. `total` reflects the filters before the paging window; `filters` echoes what was applied.

### `GET /api/v1/gis/boundaries/{id}`

Returns the summary, raw geometry, the complete GeoJSON Feature, source metadata, and the demo notice where applicable. `404` for an unknown id.

### `POST` / `DELETE /api/v1/gis/boundaries` -- `MANAGE_GIS_DATA`

These exist so "only authorized users can modify GIS boundary data" is actually enforceable rather than a decorative permission. Geometry is validated and **rejected, never repaired**; `409` on a duplicate id unless `overwrite: true`. Records default to `is_demo: true` -- marking something official is an explicit act.

### `POST /api/v1/gis/assessments` and `GET /api/v1/gis/assessments/{project_id}`

`POST` runs the engine and stores the outcome against a project, capturing everything the spec requires: project, coordinates, buffer, timestamp, the authenticated user, the full engine result, intersecting boundaries, severity, and clearance flags.

The stored result is a **snapshot**, not a live view. Deleting or editing a boundary afterwards never rewrites what a past assessment concluded -- that is what makes the log an audit artifact rather than a cache. For the same reason the repository has `add`/`get`/`for_project` but no `update`: correcting an assessment means recording a new one.

`GET` returns history newest-first with `limit`/`offset`, and `404` when a project has no assessments (distinct from an empty page).

### Status codes

| Code | Meaning |
|---|---|
| `200` / `201` / `204` | Success |
| `401` | Missing or invalid bearer token (with `WWW-Authenticate: Bearer`) |
| `403` | Authenticated but lacking the required permission -- the message names what was needed and what is missing |
| `404` | Unknown boundary id, or a project with no assessment history |
| `409` | Boundary id already exists |
| `422` | Malformed input: coordinates, buffer, category, geometry, or date |
| `503` | A required service, or the token registry, is not configured |

Validation errors name the field and the reason (`"Boundary rejected: geometry: Invalid geometry: Self-intersection[1 1]"`), and unknown request fields are rejected rather than silently ignored -- a misspelled `buffer_metres` fails loudly instead of quietly falling back to the default.

### Storage

Assessments live in `data/gis/assessments.json` (gitignored runtime state), written atomically through `JSONFileAssessmentRepository`. `migrations/0002_create_gis_assessments.sql` defines the equivalent PostgreSQL table -- append-only by design, with no `updated_at` and no update trigger. A PostgreSQL-backed assessment repository is deliberately **not** implemented: the contract and schema are in place, but writing a second SQL backend that no test in this environment can exercise would mean shipping unverified code.


## GIS integration with Project Intelligence and Priority (Feature 7)

The pipeline now runs end to end:

```
Project Data -> XGBoost Risk -> SHAP Explanation -> Historical Similarity
             -> GIS Boundary Screening -> Priority Engine -> Intervention Recommendation
```

Every stage after the first two reads evidence the previous ones already computed. Nothing re-runs a model, and no stage recomputes another stage's verdict.

### The GIS intelligence signal -- structured, never geometry

`app/services/gis_intelligence_service.py` reduces a `SpatialAnalysisResult` to `GISIntelligenceSignal`: `gis_status`, `gis_severity`, `collision_count`, `boundaries_checked`, `highest_risk_category`, `nearest_boundary`, `clearance_required` / `clearance_flags`, `max_buffer_overlap_percentage`, and `total_intersection_area_sqm`.

**No geometry crosses this boundary.** A polygon ring is not a feature: nothing downstream -- the weighted priority score, XGBoost, the SHAP explainer -- can consume coordinates as evidence, and passing them through would invite exactly that mistake. The spatial verdict is the evidence; the geometry that produced it stays in the GIS module and is served by the map endpoints. `test_signal_exposes_no_geometry` fails the build if a geometry-shaped field is ever added.

The service is a pure projection of values the engine already computed. It re-derives no verdict and re-classifies no boundary -- if it and the map ever disagreed, the map would be right.

### Priority engine -- GIS as a configurable component

Two weight profiles live in `app/config/priority_config.py`, selected only by whether a GIS signal was supplied:

| Component | Standard | With GIS |
|---|---|---|
| Risk | 0.35 | 0.30 |
| Predicted delay | 0.20 | 0.20 |
| Financial exposure | 0.20 | 0.20 |
| Historical evidence | 0.15 | 0.15 |
| Current urgency | 0.10 | 0.05 |
| **GIS environmental** | -- | **0.10** |

**The old scoring system is untouched.** A project assessed without coordinates scores byte-identically to what it scored before the GIS module existed -- `test_score_without_gis_matches_the_original_formula` pins the exact legacy expression, and `test_base_weight_profile_is_unchanged` pins the weights. Every `PriorityResponse` now reports `weight_profile` (`standard` / `with_gis`) so a score is never ambiguous about its methodology. Both profiles are asserted to sum to 1.0 at import time, and the GIS profile must add exactly one component -- renaming or dropping a base component raises at startup.

`calculate_gis_component()` blends three already-normalized sub-scores (status, severity, buffer overlap) by `GIS_SUBWEIGHTS`, then applies `GIS_CLEARANCE_FLOOR`: a project that clips the edge of a sanctuary overlaps almost none of its buffer but faces the same statutory process as one that overlaps a great deal, so a clearance obligation cannot score near zero.

### Intervention recommendations

`app/services/intervention_service.py`, with all rules, thresholds, and templates in `app/config/intervention_config.py`. Rule-based and deterministic -- no model, no LLM -- because a recommendation that cannot be traced to a threshold cannot be defended in a review meeting. Ranked by configured weight, which puts statutory obligations above managerial actions: a clearance gates the others, so recovering milestones on a project that cannot yet lawfully proceed is wasted effort. Each recommendation cites the pipeline stage that triggered it, and the list is never empty -- "nothing crossed a threshold" is itself a finding.

### Statutory language

This system performs **screening, not adjudication**, and must never imply otherwise. The approved wording is fixed in `priority_config.py`:

- `"Potential spatial conflict detected."`
- `"Final clearance requirements must be verified by the competent authority and applicable regulations."`

`tests/test_gis_integration.py` asserts both appear, and scans every response-producing module (and a live API response) for prohibited determinative wording -- "legally rejected", "not permitted", "prohibited", "cannot proceed", and others. Docstrings and comments are stripped before the scan, so prose *about* the prohibited wording is not mistaken for the wording itself.

### API

`POST /api/v1/project-intelligence` and `POST /api/v1/project-priority` both accept **optional** `latitude`, `longitude`, and `buffer_meters`. Supplying them adds the GIS stage and switches the priority profile; omitting them reproduces the previous behaviour exactly. Latitude and longitude must be supplied together (422 otherwise). The intelligence response gains three optional sections -- `gis_screening`, `priority`, `interventions` -- so clients reading only the original six fields are unaffected.

Example `gis_screening` (verified live output):

```json
{
  "gis_status": "DIRECT_COLLISION",
  "gis_severity": "CRITICAL",
  "buffer_meters": 2000.0,
  "collision_count": 1,
  "boundaries_checked": 6,
  "highest_risk_category": "NATIONAL_PARK",
  "highest_risk_category_label": "National Park",
  "nearest_boundary": {
    "name": "Demo National Park",
    "category_label": "National Park",
    "distance_meters": 0.0,
    "collision_type": "DIRECT_COLLISION",
    "severity": "CRITICAL"
  },
  "clearance_required": true,
  "clearance_flag_count": 1,
  "clearance_flags": ["Environmental review required: Demo National Park (National Park, direct collision)"],
  "max_buffer_overlap_percentage": 100.0,
  "advisory": "Potential spatial conflict detected.",
  "disclaimer": "Final clearance requirements must be verified by the competent authority and applicable regulations."
}
```


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
- `app/config/gis_config.py`: every path, CRS assumption, geometry limit, and category label for Feature 4
- `app/schemas/boundary.py`: `BoundaryCategory`, `BoundaryGeometry` (self-validating), `GISBoundary`, query/response/import-report models
- `app/models/boundary_geometry.py`: the single owner of geometry validation, normalization, bbox/area/centroid, and EWKT rendering for PostGIS
- `app/repositories/gis_boundary_repository.py`: the storage contract plus the GeoJSON-file and PostGIS backends
- `app/services/gis_boundary_service.py`: `GISBoundaryService` -- validation, demo-data marking, deterministic ids, FeatureCollection assembly
- `app/services/boundary_importer.py`: the reusable GeoJSON/Shapefile/GeoPackage importer
- `data/gis/demo_boundaries.geojson`, `scripts/seed_gis_boundaries.py`, `scripts/migrate.py`, `migrations/`: Feature 4's demo dataset, seed/import CLI, and PostGIS schema
- `app/config/spatial_config.py`: every threshold, severity rank, and clearance rule for Feature 5
- `app/models/projection.py`: coordinate validation and the local metre-based CRS (`LocalProjection`) -- the module that guarantees metres are never applied to degrees
- `app/schemas/spatial.py`: `CollisionType`, `Severity`, request/result models
- `app/services/spatial_analysis_service.py`: `SpatialAnalysisService` -- the deterministic collision engine, plus its pure classification functions
- `app/config/auth_config.py`: permissions, roles, the role-to-permission map, and the auth posture for Feature 6
- `app/api/security.py`: `Principal`, `TokenRegistry`, and `require_permission` -- the only module that knows how callers are authenticated
- `app/api/routes/gis.py`: the eight GIS endpoints and their HTTP/GeoJSON translation
- `app/schemas/gis_api.py`, `app/schemas/assessment.py`: API contracts and the stored assessment record
- `app/repositories/assessment_repository.py`, `app/services/assessment_service.py`: assessment history storage and clearance-flag derivation
- `scripts/generate_api_tokens.py`: issues bearer tokens; stores only hashes
- `tests/test_integration.py`: end-to-end API tests (TC1-TC5) covering Features 1 and 2 and their combination
- `tests/test_similarity_unit.py`: unit tests for similarity-engine internals (leakage guards, empty-dataset handling, feature-weighting scope, JSON-safety of returned types)
- `tests/test_priority.py`: Feature 3 tests -- weight/threshold sanity, endpoint behavior, determinism, and invalid-input handling
- `tests/test_gis_boundaries.py`: Feature 4 tests -- geometry validation (accept and reject), repository CRUD/filtering/persistence, importer CRS detection and per-feature rejection, PostGIS query translation, and the demo-data guarantees
- `app/schemas/gis_signal.py`, `app/services/gis_intelligence_service.py`: the geometry-free screening signal (Feature 7)
- `app/config/intervention_config.py`, `app/services/intervention_service.py`: the rule-based intervention playbook
- `tests/test_gis_integration.py`: Feature 7 tests -- backward compatibility of the original score, GIS component maths, the no-geometry rule, statutory language, and intervention ranking
- `tests/test_gis_api.py`: Feature 6 tests -- valid collision, clear result, nearby result, invalid coordinates/buffer/category/geometry, multiple collisions, assessment storage and retrieval, and authentication/authorization across all eight endpoints and three roles
- `tests/test_spatial_analysis.py`: Feature 5 tests -- the four collision types, multiple intersections, buffer sizing, Polygon/MultiPolygon, invalid coordinates and geometry, CRS transformation cross-checked against a geodesic solver, severity/clearance rules, and spatial-index correctness

## Environment Variables

| Variable | Scope | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | Backend | _None_ | PostgreSQL / PostGIS connection string (e.g. `postgresql://user:pass@localhost:5432/gis_db`). When set and `psycopg` is installed, switches GIS boundary storage from the GeoJSON file backend to PostGIS. |
| `GIS_AUTH_MODE` | Backend | `token` | Authentication mode for GIS API (`token` or `disabled`). In `token` mode (fail-closed), a valid bearer token is required. `disabled` grants `GIS_ADMIN` permissions to all requests and is strictly for local dev. |
| `GIS_API_TOKENS_FILE` | Backend | `data/auth/api_tokens.json` | Path to the hashed token registry file generated by `scripts/generate_api_tokens.py`. |
| `GIS_API_TOKENS` | Backend | _None_ | Inline JSON string of the token registry for container/secret-manager deployments where mounting files is undesirable. |
| `GIS_API_URL` | Frontend | `http://127.0.0.1:8000` | Base URL of the FastAPI backend used by the Next.js API proxy (`src/app/api/gis/[...path]/route.ts`). |
| `GIS_API_TOKEN` | Frontend | _Configured in `.env.local`_ | Bearer token used by Next.js server-side proxy to authenticate with the GIS API. |

## GIS Subsystem Limitations

1. **Screening vs. Statutory Adjudication**: This system performs preliminary automated spatial screening and generates advisory risk flags. It does **not** grant or deny legal clearances, nor does it replace formal environmental impact assessments (EIA) or approvals from statutory authorities (such as the Ministry of Environment, Forest and Climate Change or State Forest Departments).
2. **2D Projected Metric Calculations**: Spatial calculations (buffers, distances, intersection polygons) are computed on a 2D projected plane (azimuthal equidistant `aeqd` centered on the project location or UTM). Topographic relief, slope, and elevation are not modeled.
3. **Point-Based Project Locations**: Projects are evaluated based on their survey center coordinates and radial buffer envelopes. Linear corridor modeling (e.g., polyline right-of-way centerlines for highways or railway tracks) is currently approximated via bounding coordinates and buffer radii.
4. **Data Currency and Boundary Precision**: Spatial screening accuracy depends on the fidelity and currency of the underlying boundary datasets. Real-world legal boundaries require official cadastral surveys.

## Demo-Data Disclaimer

> **⚠️ CRITICAL NOTICE: DEMO DATA - NOT OFFICIAL BOUNDARIES**
>
> The boundaries included in `data/gis/demo_boundaries.geojson` are **synthetic, fictional test geometries** located in a fictional "DEMO STATE". They correspond to no real protected areas, sanctuaries, or reserves.
> - They are strictly intended for automated integration testing, interface demonstrations, and engine verification.
> - They must **never** be cited, presented, or relied upon as official government boundaries, legal clearances, or compliance determinations.
> - All demo records are marked with `is_demo: true` and carry mandatory provenance notices across all API responses and UI displays.

## How to Run the Complete Stack

### 1. Backend (FastAPI)

From the `backend` directory:

```bash
# Activate virtual environment
source .venv/bin/activate

# Initialize GIS store and seed demo boundaries
python scripts/migrate.py
python scripts/seed_gis_boundaries.py

# Ensure API tokens are generated (already configured in data/auth/api_tokens.json)
python scripts/generate_api_tokens.py --show

# Start FastAPI server
uvicorn app.main:app --reload --port 8000
```

The API will be available at `http://127.0.0.1:8000` (docs at `http://127.0.0.1:8000/docs`).

### 2. Frontend (Next.js)

From the project root directory:

```bash
# Install dependencies if not already done
npm install

# Start Next.js development server
npm run dev
```

Visit:
- Main Project Dashboard: `http://localhost:3000/`
- GIS Environmental Boundary Check: `http://localhost:3000/gis-check`

## How to Test

### 1. Run Complete Pytest Backend Suite (389 tests)

```bash
cd backend
.venv/bin/pytest -v
```

Covers:
- `tests/test_gis_api.py`: All 8 GIS REST endpoints, RBAC permissions, and HTTP status codes.
- `tests/test_gis_boundaries.py`: Boundary models, geometry validation, repository CRUD, and file importer.
- `tests/test_spatial_analysis.py`: Metre-accurate projections, buffer intersections, and classification rules.
- `tests/test_gis_integration.py`: Signal extraction, priority engine integration, and non-determinism prevention.
- `tests/test_integration.py`: XGBoost risk prediction (TC1-TC5), SHAP explanations, and model health.
- `tests/test_priority.py`: Deterministic 5-component and 6-component scoring profiles.
- `tests/test_similarity_unit.py`: NearestNeighbors pipeline, feature weighting, and historical matching.

### 2. Run End-to-End GIS Flow Verification Script

```bash
cd backend
.venv/bin/python scripts/verify_gis_flow.py
```

Validates all 15 key functional requirements:
- `CLEAR`, `NEARBY`, `BUFFER_COLLISION`, `DIRECT_COLLISION`
- Multi-boundary intersections & ordering
- Buffer sensitivity and dynamic overlap calculations
- Polygon and MultiPolygon containment
- Strict input validation (coordinates, buffer bounds, IDs)
- GIS accuracy vs. pyproj geodesic solver
- Rejection of invalid geometries (self-intersecting bowties, degenerate rings)
- Security enforcement (fail-closed RBAC, 401 on unauthorized calls)
- Assessment storage and historical retrieval
- Downstream integration: GIS Screening -> Project Intelligence -> Priority Score & Interventions

### 3. Frontend Build & Lint Verification

From project root:

```bash
npm run lint
npm run build
```
