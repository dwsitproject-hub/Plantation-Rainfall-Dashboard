-- Plantation Rainfall Dashboard - PostgreSQL 18 schema.
--
-- Idempotent: safe to run on every deployment (python -m backend.database.init_db).
-- All objects live in the "rainfall" schema owned by the application role.

CREATE SCHEMA IF NOT EXISTS rainfall;
SET search_path TO rainfall, public;

-- ---------------------------------------------------------------------------
-- Reference data
-- ---------------------------------------------------------------------------

-- Monitored All Regions locations (Open-Meteo / ECMWF IFS). Rainfall and soil
-- moisture use the rain coordinates; temperature uses separate lowland
-- reference coordinates so regional series are comparable.
CREATE TABLE IF NOT EXISTS weather_region (
    region_id            smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    country              text        NOT NULL,
    region_name          text        NOT NULL,
    rain_latitude        numeric(8,5) NOT NULL,
    rain_longitude       numeric(8,5) NOT NULL,
    temperature_latitude numeric(8,5) NOT NULL,
    temperature_longitude numeric(8,5) NOT NULL,
    is_major_plantation  boolean     NOT NULL DEFAULT false,
    display_order        smallint    NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_weather_region UNIQUE (country, region_name),
    CONSTRAINT ck_weather_region_country CHECK (country IN ('Indonesia', 'Malaysia', 'Thailand'))
);

-- In-house plantation estates, loaded from the estate coordinate workbook.
CREATE TABLE IF NOT EXISTS estate (
    estate_id        text PRIMARY KEY,               -- company + estate, e.g. TH3A
    company_code     char(2)      NOT NULL,
    estate_code      text         NOT NULL,
    estate_name      text         NOT NULL,
    province         text         NOT NULL,          -- geographic province
    business_region  text         NOT NULL,          -- internal operating region
    latitude         numeric(9,6) NOT NULL,
    longitude        numeric(9,6) NOT NULL,
    workbook_order   integer      NOT NULL,          -- row order in the source workbook
    is_active        boolean      NOT NULL DEFAULT true,  -- false once removed from the workbook
    created_at       timestamptz  NOT NULL DEFAULT now(),
    updated_at       timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT uq_estate_company_estate UNIQUE (company_code, estate_code),
    CONSTRAINT ck_estate_latitude  CHECK (latitude  BETWEEN -90  AND 90),
    CONSTRAINT ck_estate_longitude CHECK (longitude BETWEEN -180 AND 180)
);

-- ---------------------------------------------------------------------------
-- Audit of every data refresh
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS refresh_run (
    run_id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pipeline          text        NOT NULL,          -- all_regions | plantation
    step              text        NOT NULL,          -- e.g. historical_rainfall, sap_load
    status            text        NOT NULL DEFAULT 'running',
    started_at        timestamptz NOT NULL DEFAULT now(),
    finished_at       timestamptz,
    rows_written      integer,
    sap_actual_cutoff date,
    message           text,
    CONSTRAINT ck_refresh_run_pipeline CHECK (pipeline IN ('all_regions', 'plantation')),
    CONSTRAINT ck_refresh_run_status   CHECK (status IN ('running', 'succeeded', 'failed'))
);
CREATE INDEX IF NOT EXISTS ix_refresh_run_pipeline_started ON refresh_run (pipeline, started_at DESC);

-- ---------------------------------------------------------------------------
-- All Regions weather (Open-Meteo)
-- ---------------------------------------------------------------------------

-- One row per region, metric, day and data type. Historical rows are modelled
-- reanalysis ("historical_actual"), not rain-gauge observations. Forecast rows
-- are replaced on every forecast refresh.
CREATE TABLE IF NOT EXISTS weather_daily (
    region_id        smallint     NOT NULL REFERENCES weather_region (region_id),
    metric           text         NOT NULL,
    obs_date         date         NOT NULL,
    data_type        text         NOT NULL,
    value            double precision NOT NULL,
    latitude         numeric(8,5) NOT NULL,          -- coordinates actually queried
    longitude        numeric(8,5) NOT NULL,
    soil_depth_cm    text,                           -- soil_moisture only, e.g. 28-100
    method           text,                           -- soil_moisture aggregation method
    source           text         NOT NULL,
    retrieved_at     timestamptz  NOT NULL,
    CONSTRAINT pk_weather_daily PRIMARY KEY (region_id, metric, obs_date, data_type),
    CONSTRAINT ck_weather_daily_metric    CHECK (metric IN ('rainfall_mm', 'temperature_mean_c', 'soil_moisture_m3_m3')),
    CONSTRAINT ck_weather_daily_data_type CHECK (data_type IN ('historical_actual', 'forecast'))
);
CREATE INDEX IF NOT EXISTS ix_weather_daily_metric_type_date ON weather_daily (metric, data_type, obs_date);

-- Archive of every rainfall forecast run, used for run-to-run revisions and
-- last-week accuracy. Retention is FORECAST_SNAPSHOT_RETENTION_RUNS run dates.
CREATE TABLE IF NOT EXISTS forecast_snapshot (
    run_date      date         NOT NULL,             -- local date the forecast was retrieved
    region_id     smallint     NOT NULL REFERENCES weather_region (region_id),
    valid_date    date         NOT NULL,             -- date the forecast is for
    rainfall_mm   double precision NOT NULL,
    latitude      numeric(8,5) NOT NULL,
    longitude     numeric(8,5) NOT NULL,
    source        text         NOT NULL,
    retrieved_at  timestamptz  NOT NULL,
    CONSTRAINT pk_forecast_snapshot PRIMARY KEY (run_date, region_id, valid_date)
);
CREATE INDEX IF NOT EXISTS ix_forecast_snapshot_valid_date ON forecast_snapshot (valid_date);

-- ---------------------------------------------------------------------------
-- In-house plantation (SAP / Syncera)
-- ---------------------------------------------------------------------------

-- Raw SAP rainfall records exactly as returned by the Syncera API, with
-- translated column names. Each environment's dataset is replaced as a whole
-- on every successful refresh.
CREATE TABLE IF NOT EXISTS sap_rainfall_raw (
    sap_row_id                       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id                           bigint  NOT NULL REFERENCES refresh_run (run_id),
    environment                      text    NOT NULL,   -- dev = historical, prod = current
    company_code                     text    NOT NULL,
    estate_code                      text    NOT NULL,
    plant                            text,
    posting_date_raw                 text,
    measurement_tool                 text,
    administrator                    text,
    rainy_day                        text,
    number_of_rain_events            text,
    morning_rainfall_measurement     text,
    afternoon_rainfall_measurement   text,
    night_rainfall_measurement       text,
    total_rainfall_measurement       text,
    remarks                          text,
    morning_rain_duration_minutes    text,
    afternoon_rain_duration_minutes  text,
    night_rain_duration_minutes      text,
    extra_fields                     jsonb   NOT NULL DEFAULT '{}'::jsonb,  -- any undocumented API fields
    CONSTRAINT ck_sap_rainfall_raw_environment CHECK (environment IN ('dev', 'prod'))
);
CREATE INDEX IF NOT EXISTS ix_sap_rainfall_raw_env_estate ON sap_rainfall_raw (environment, company_code, estate_code);

-- Cleaned daily SAP rainfall used by the in-house dashboard: valid dates only,
-- portfolio-level all-zero / future placeholder dates removed, de-duplicated.
CREATE TABLE IF NOT EXISTS plantation_rainfall_daily (
    record_id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id            bigint  NOT NULL REFERENCES refresh_run (run_id),
    company_code      text    NOT NULL,
    estate_code       text    NOT NULL,
    plant             text    NOT NULL DEFAULT '',
    measurement_tool  text    NOT NULL DEFAULT 'Unspecified',
    obs_date          date    NOT NULL,
    rainfall_mm       double precision NOT NULL      -- preserved exactly as SAP reported it
);
CREATE INDEX IF NOT EXISTS ix_plantation_rainfall_daily_estate_date
    ON plantation_rainfall_daily (company_code, estate_code, obs_date);

-- Open-Meteo rainfall at each estate centroid: recent modelled actuals
-- (gap fill after the latest SAP date) and the forward forecast.
CREATE TABLE IF NOT EXISTS plantation_forecast_daily (
    estate_id     text    NOT NULL REFERENCES estate (estate_id),
    valid_date    date    NOT NULL,
    data_type     text    NOT NULL,                  -- recent_actual | forecast
    rainfall_mm   double precision,                  -- null when Open-Meteo has no value
    run_id        bigint  NOT NULL REFERENCES refresh_run (run_id),
    CONSTRAINT pk_plantation_forecast_daily PRIMARY KEY (estate_id, valid_date),
    CONSTRAINT ck_plantation_forecast_data_type CHECK (data_type IN ('recent_actual', 'forecast'))
);

-- Per-estate metadata of the latest plantation forecast pull.
CREATE TABLE IF NOT EXISTS plantation_forecast_run (
    run_id          bigint  PRIMARY KEY REFERENCES refresh_run (run_id),
    as_of_date      date    NOT NULL,
    generated_at    timestamptz NOT NULL,
    past_days       smallint NOT NULL,
    forecast_days   smallint NOT NULL,
    source          text    NOT NULL,
    source_url      text    NOT NULL,
    workbook_name   text    NOT NULL
);

CREATE TABLE IF NOT EXISTS plantation_forecast_location (
    run_id      bigint  NOT NULL REFERENCES plantation_forecast_run (run_id) ON DELETE CASCADE,
    estate_id   text    NOT NULL REFERENCES estate (estate_id),
    latitude    numeric(9,6) NOT NULL,               -- grid cell Open-Meteo actually used
    longitude   numeric(9,6) NOT NULL,
    timezone    text,
    CONSTRAINT pk_plantation_forecast_location PRIMARY KEY (run_id, estate_id)
);

-- ---------------------------------------------------------------------------
-- Serving cache
-- ---------------------------------------------------------------------------

-- Pre-built /api/data payload, keyed by a signature of the source data, so
-- every dashboard server instance shares one cache.
CREATE TABLE IF NOT EXISTS dashboard_cache (
    cache_key     text PRIMARY KEY,
    signature     text        NOT NULL,
    body          bytea       NOT NULL,
    body_gzip     bytea       NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Access control: users, roles, sessions, audit
-- ---------------------------------------------------------------------------

-- A role grants access to one or more dashboard sections.
CREATE TABLE IF NOT EXISTS app_role (
    role_code     text PRIMARY KEY,                  -- admin, plantation, regional
    role_name     text        NOT NULL,
    description   text        NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app_role_section (
    role_code     text NOT NULL REFERENCES app_role (role_code) ON DELETE CASCADE,
    section       text NOT NULL,
    CONSTRAINT pk_app_role_section PRIMARY KEY (role_code, section),
    CONSTRAINT ck_app_role_section CHECK (section IN ('all_regions', 'inhouse', 'user_admin'))
);

CREATE TABLE IF NOT EXISTS app_user (
    user_id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email                text        NOT NULL,       -- sign-in identifier, unique ignoring case
    full_name            text        NOT NULL,
    role_code            text        NOT NULL REFERENCES app_role (role_code),
    password_hash        text        NOT NULL,       -- scrypt$n$r$p$salt$hash
    is_active            boolean     NOT NULL DEFAULT true,
    failed_login_count   integer     NOT NULL DEFAULT 0,
    locked_until         timestamptz,                -- set after repeated failed logins
    last_login_at        timestamptz,
    password_changed_at  timestamptz NOT NULL DEFAULT now(),
    created_by           bigint REFERENCES app_user (user_id),
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_app_user_email CHECK (email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$' AND length(email) <= 200)
);

-- Upgrade from the first version, which signed in with a username.
ALTER TABLE app_user DROP CONSTRAINT IF EXISTS ck_app_user_username;
DROP INDEX IF EXISTS uq_app_user_username;
ALTER TABLE app_user DROP COLUMN IF EXISTS username;
ALTER TABLE app_user ALTER COLUMN email SET NOT NULL;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_app_user_email') THEN
        ALTER TABLE app_user ADD CONSTRAINT ck_app_user_email
            CHECK (email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$' AND length(email) <= 200);
    END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS uq_app_user_email ON app_user (lower(email));

-- Server-side sessions. Only a SHA-256 hash of the cookie token is stored.
CREATE TABLE IF NOT EXISTS app_session (
    token_hash    text PRIMARY KEY,
    user_id       bigint      NOT NULL REFERENCES app_user (user_id) ON DELETE CASCADE,
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_seen_at  timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    ip_address    text,
    user_agent    text
);
CREATE INDEX IF NOT EXISTS ix_app_session_user ON app_session (user_id);
CREATE INDEX IF NOT EXISTS ix_app_session_expires ON app_session (expires_at);

-- Who signed in, failed to sign in, or changed a user, and when.
CREATE TABLE IF NOT EXISTS auth_audit (
    audit_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at     timestamptz NOT NULL DEFAULT now(),
    action          text        NOT NULL,            -- login_succeeded, login_failed, user_created, ...
    actor_user_id   bigint REFERENCES app_user (user_id),
    target_user_id  bigint REFERENCES app_user (user_id),
    email           text,                            -- sign-in email as typed, for failed sign-ins
    ip_address      text,
    detail          jsonb       NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS ix_auth_audit_occurred ON auth_audit (occurred_at DESC);
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = current_schema() AND table_name = 'auth_audit' AND column_name = 'username') THEN
        ALTER TABLE auth_audit RENAME COLUMN username TO email;
    END IF;
END $$;

-- Default roles. Existing rows are left alone, so roles can be adjusted in the database.
INSERT INTO app_role (role_code, role_name, description) VALUES
    ('admin', 'Administrator', 'All dashboards and user management'),
    ('plantation', 'Plantation user', 'All Regions and In-house Plantation dashboards'),
    ('regional', 'Regional viewer', 'All Regions dashboards only')
ON CONFLICT (role_code) DO NOTHING;

INSERT INTO app_role_section (role_code, section)
SELECT r.role_code, s.section FROM (VALUES
    ('admin', 'all_regions'), ('admin', 'inhouse'), ('admin', 'user_admin'),
    ('plantation', 'all_regions'), ('plantation', 'inhouse'),
    ('regional', 'all_regions')
) AS s (role_code, section)
JOIN app_role r USING (role_code)
WHERE NOT EXISTS (SELECT 1 FROM app_role_section x WHERE x.role_code = r.role_code)
ON CONFLICT DO NOTHING;

-- ---------------------------------------------------------------------------
-- Single sign-on with DWS Hub (OIDC authorization code + PKCE)
-- ---------------------------------------------------------------------------

-- Stable Hub user id ("sub" claim), linked on first Hub sign-in.
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS sso_subject text;
CREATE UNIQUE INDEX IF NOT EXISTS uq_app_user_sso_subject ON app_user (sso_subject) WHERE sso_subject IS NOT NULL;

-- A sign-in started at the Hub and not yet completed. Only a hash of the state
-- value is stored; rows are single-use and expire after a few minutes.
CREATE TABLE IF NOT EXISTS sso_login_request (
    state_hash     text PRIMARY KEY,
    code_verifier  text        NOT NULL,
    nonce          text        NOT NULL,
    next_path      text        NOT NULL DEFAULT '/',
    created_at     timestamptz NOT NULL DEFAULT now(),
    expires_at     timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sso_login_request_expires ON sso_login_request (expires_at);
