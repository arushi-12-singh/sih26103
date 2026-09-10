-- 0002_create_gis_assessments.sql
-- GIS assessment history (Feature 6, API 5) -- PostgreSQL schema.
--
-- Apply with:  python scripts/migrate.py            (uses $DATABASE_URL)
--
-- Mirrors app/schemas/assessment.py and the contract in
-- app/repositories/assessment_repository.py exactly. The JSON file backend is what runs
-- today; this table is what a PostgreSQL-backed AssessmentRepository writes to, so
-- adding that backend needs no schema design work.
--
-- Assessments are an APPEND-MOSTLY AUDIT LOG. There is deliberately no updated_at and no
-- update trigger: correcting an assessment means recording a new one, never editing the
-- record of what was concluded at the time.
--
-- Idempotent: safe to re-run.

BEGIN;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     text        PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS gis_assessments (
    id                      text        PRIMARY KEY,
    project_id              text        NOT NULL,

    latitude                double precision NOT NULL,
    longitude               double precision NOT NULL,
    buffer_meters           double precision NOT NULL,

    -- When the assessment was run, and by whom. NOT NULL on both: an audit record with no
    -- actor or no timestamp is not an audit record.
    created_at              timestamptz NOT NULL DEFAULT now(),
    created_by              text        NOT NULL,
    created_by_name         text        NOT NULL DEFAULT '',

    overall_status          text        NOT NULL,
    overall_severity        text,
    clearance_required      boolean     NOT NULL,
    boundaries_checked      integer     NOT NULL,

    -- Snapshots, not foreign keys: a boundary being edited or deleted later must not
    -- change what a past assessment concluded.
    intersecting_boundaries jsonb       NOT NULL DEFAULT '[]'::jsonb,
    clearance_flags         jsonb       NOT NULL DEFAULT '[]'::jsonb,
    result                  jsonb       NOT NULL,

    notes                   text,
    metadata                jsonb       NOT NULL DEFAULT '{}'::jsonb,

    CONSTRAINT gis_assessments_project_not_blank CHECK (length(btrim(project_id)) > 0),
    CONSTRAINT gis_assessments_actor_not_blank   CHECK (length(btrim(created_by)) > 0),
    CONSTRAINT gis_assessments_latitude_range    CHECK (latitude BETWEEN -90 AND 90),
    CONSTRAINT gis_assessments_longitude_range   CHECK (longitude BETWEEN -180 AND 180),
    CONSTRAINT gis_assessments_buffer_positive   CHECK (buffer_meters >= 0),
    CONSTRAINT gis_assessments_status_known      CHECK (
        overall_status IN ('DIRECT_COLLISION', 'BUFFER_COLLISION', 'NEARBY', 'CLEAR')
    ),
    CONSTRAINT gis_assessments_severity_known    CHECK (
        overall_severity IS NULL OR overall_severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')
    ),
    -- CLEAR carries no severity; anything else must have one.
    CONSTRAINT gis_assessments_severity_matches_status CHECK (
        (overall_status = 'CLEAR') = (overall_severity IS NULL)
    )
);

-- Serves the history endpoint's "newest first for one project" query directly.
CREATE INDEX IF NOT EXISTS gis_assessments_project_created_idx
    ON gis_assessments (project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS gis_assessments_created_by_idx ON gis_assessments (created_by);
CREATE INDEX IF NOT EXISTS gis_assessments_status_idx     ON gis_assessments (overall_status);

COMMENT ON TABLE  gis_assessments IS
    'Append-only log of GIS spatial assessments. Rows are snapshots: boundary edits never rewrite past conclusions.';
COMMENT ON COLUMN gis_assessments.result IS 'The complete spatial engine output as computed at created_at.';

INSERT INTO schema_migrations (version) VALUES ('0002_create_gis_assessments')
ON CONFLICT (version) DO NOTHING;

COMMIT;
