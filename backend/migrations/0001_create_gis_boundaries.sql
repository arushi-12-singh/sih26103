-- 0001_create_gis_boundaries.sql
-- GIS boundary data layer (Feature 4) -- PostgreSQL/PostGIS schema.
--
-- Apply with:  python scripts/migrate.py            (uses $DATABASE_URL)
--        or:   psql "$DATABASE_URL" -f migrations/0001_create_gis_boundaries.sql
--
-- This mirrors app/schemas/boundary.py exactly: same ten fields, same category
-- vocabulary, same EPSG:4326 storage CRS. app/repositories/gis_boundary_repository.py's
-- PostGISBoundaryRepository targets this table, so applying this migration and setting
-- DATABASE_URL is the entire migration path from the GeoJSON file backend -- no
-- application code changes.
--
-- Idempotent: safe to re-run.

BEGIN;

CREATE EXTENSION IF NOT EXISTS postgis;

-- Applied-migration ledger, so scripts/migrate.py can skip what already ran.
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     text        PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
);

-- Category vocabulary. Must stay in sync with BoundaryCategory in
-- app/schemas/boundary.py; tests/test_gis_boundaries.py asserts this file lists every
-- enum member, so adding a category in Python without a follow-up migration fails CI.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'boundary_category') THEN
        CREATE TYPE boundary_category AS ENUM (
            'WILDLIFE_SANCTUARY',
            'NATIONAL_PARK',
            'FOREST',
            'ECO_SENSITIVE_ZONE',
            'TIGER_RESERVE',
            'RAMSAR_WETLAND',
            'OTHER_RESTRICTED_ZONE'
        );
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS gis_boundaries (
    -- Deterministic UUIDv5 of (source, name, category); see make_boundary_id(). Held as
    -- text rather than uuid so non-UUID ids from an official dataset also fit.
    id            text                            PRIMARY KEY,
    name          text                            NOT NULL,
    category      boundary_category               NOT NULL,
    state         text                            NOT NULL,
    -- Nullable: real protected areas often span districts or are published without one.
    district      text,
    -- Areal geometry only, always WGS84 -- the CRS PostGIS geography casts and RFC 7946
    -- GeoJSON both require, so no reprojection is ever needed on read.
    geometry      geometry(Geometry, 4326)        NOT NULL,
    -- Attribution is NOT NULL by design: no boundary may exist without a traceable origin.
    source        text                            NOT NULL,
    source_url    text,
    -- Date the SOURCE dataset was last updated, not the row's write time.
    last_updated  date                            NOT NULL,
    metadata      jsonb                           NOT NULL DEFAULT '{}'::jsonb,
    created_at    timestamptz                     NOT NULL DEFAULT now(),
    updated_at    timestamptz                     NOT NULL DEFAULT now(),

    CONSTRAINT gis_boundaries_name_not_blank  CHECK (length(btrim(name)) > 0),
    CONSTRAINT gis_boundaries_state_not_blank CHECK (length(btrim(state)) > 0),
    -- Enforce the same rules app/models/boundary_geometry.py enforces in Python, so the
    -- database stays correct even if something writes to it outside the application.
    CONSTRAINT gis_boundaries_geometry_areal  CHECK (GeometryType(geometry) IN ('POLYGON', 'MULTIPOLYGON')),
    CONSTRAINT gis_boundaries_geometry_valid  CHECK (ST_IsValid(geometry)),
    CONSTRAINT gis_boundaries_geometry_srid   CHECK (ST_SRID(geometry) = 4326)
);

-- GiST over the geometry: what makes `geometry && ST_MakeEnvelope(...)` bbox filters --
-- and the intersection queries the collision feature will add later -- index-backed
-- rather than a sequential scan.
CREATE INDEX IF NOT EXISTS gis_boundaries_geometry_gist ON gis_boundaries USING GIST (geometry);

CREATE INDEX IF NOT EXISTS gis_boundaries_category_idx ON gis_boundaries (category);
CREATE INDEX IF NOT EXISTS gis_boundaries_state_idx    ON gis_boundaries (lower(state));
CREATE INDEX IF NOT EXISTS gis_boundaries_district_idx ON gis_boundaries (lower(district));
-- Serves the `metadata->>'is_demo'` filter used to exclude demo records from official views.
CREATE INDEX IF NOT EXISTS gis_boundaries_metadata_gin ON gis_boundaries USING GIN (metadata jsonb_path_ops);

CREATE OR REPLACE FUNCTION gis_boundaries_touch_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS gis_boundaries_set_updated_at ON gis_boundaries;
CREATE TRIGGER gis_boundaries_set_updated_at
    BEFORE UPDATE ON gis_boundaries
    FOR EACH ROW EXECUTE FUNCTION gis_boundaries_touch_updated_at();

COMMENT ON TABLE  gis_boundaries IS
    'Environmental and restricted-area boundaries in EPSG:4326. Rows whose metadata->>''is_demo'' is true are generated test data and MUST NOT be presented as official government boundaries.';
COMMENT ON COLUMN gis_boundaries.last_updated IS 'Date the source dataset was last updated (not the row write time).';
COMMENT ON COLUMN gis_boundaries.metadata IS 'Free-form source attributes; is_demo/data_notice mark generated records.';

INSERT INTO schema_migrations (version) VALUES ('0001_create_gis_boundaries')
ON CONFLICT (version) DO NOTHING;

COMMIT;
