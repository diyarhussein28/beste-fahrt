-- Fleet Dispatch Monitor — schema init. راجع القسم 5.2 و8.7 من الوثيقة التقنية.

CREATE EXTENSION IF NOT EXISTS postgis;

DO $$ BEGIN
    CREATE TYPE driver_status AS ENUM ('available', 'on_job', 'off_duty');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE job_status AS ENUM ('open', 'dispatched', 'taken_by_us', 'gone');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE watch_status AS ENUM ('open', 'matched', 'closed', 'expired');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS drivers (
    id                SERIAL PRIMARY KEY,
    name              TEXT NOT NULL,
    telegram_chat_id  BIGINT UNIQUE,
    phone             TEXT,
    status            driver_status NOT NULL DEFAULT 'off_duty',
    location_consent  BOOLEAN NOT NULL DEFAULT FALSE,
    active            BOOLEAN NOT NULL DEFAULT TRUE,
    license_classes   TEXT[] NOT NULL DEFAULT '{}',
    jobs_today        INT NOT NULL DEFAULT 0,
    home_geom         GEOGRAPHY(Point, 4326),
    home_city         TEXT,
    allow_overnight   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS driver_locations (
    id          BIGSERIAL PRIMARY KEY,
    driver_id   INT NOT NULL REFERENCES drivers(id) ON DELETE CASCADE,
    geom        GEOGRAPHY(Point, 4326) NOT NULL,
    source      TEXT NOT NULL, -- live_share | last_dropoff | manual
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_driver_locations_driver_recorded
    ON driver_locations (driver_id, recorded_at DESC);

CREATE TABLE IF NOT EXISTS jobs (
    fp            CHAR(64) PRIMARY KEY,
    platform_id   TEXT,
    pickup_addr   TEXT,
    dropoff_addr  TEXT,
    pickup_geom   GEOGRAPHY(Point, 4326),
    dropoff_geom  GEOGRAPHY(Point, 4326),
    price_eur     NUMERIC(8, 2),
    route_km      NUMERIC(7, 1),
    pickup_date   TIMESTAMPTZ,
    url           TEXT,
    status        job_status NOT NULL DEFAULT 'open',
    first_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
    gone_at       TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_jobs_pickup_geom ON jobs USING GIST (pickup_geom);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status);

CREATE TABLE IF NOT EXISTS dispatches (
    id           BIGSERIAL PRIMARY KEY,
    job_fp       CHAR(64) NOT NULL REFERENCES jobs(fp) ON DELETE CASCADE,
    driver_id    INT NOT NULL REFERENCES drivers(id) ON DELETE CASCADE,
    rank         SMALLINT,
    sent_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    response     TEXT, -- accept | decline | timeout
    responded_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_dispatches_job_fp ON dispatches (job_fp);

-- Circular service areas: the base Leverkusen area plus temporary areas
-- opened around a driver's drop-off point while a return watch is active
-- (القسم 8.3). A polygon variant can be added later without breaking this
-- table by adding a nullable `polygon` column.
CREATE TABLE IF NOT EXISTS service_areas (
    id         SERIAL PRIMARY KEY,
    name       TEXT NOT NULL,
    geom       GEOGRAPHY(Point, 4326) NOT NULL,
    radius_km  NUMERIC(6, 1) NOT NULL,
    active     BOOLEAN NOT NULL DEFAULT TRUE,
    expires_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_service_areas_active ON service_areas (active);

CREATE TABLE IF NOT EXISTS geocode_cache (
    address_hash CHAR(64) PRIMARY KEY,
    address      TEXT NOT NULL,
    lat          DOUBLE PRECISION NOT NULL,
    lon          DOUBLE PRECISION NOT NULL,
    provider     TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS return_watches (
    id           BIGSERIAL PRIMARY KEY,
    driver_id    INT NOT NULL REFERENCES drivers(id) ON DELETE CASCADE,
    outbound_fp  CHAR(64) NOT NULL REFERENCES jobs(fp) ON DELETE CASCADE,
    from_geom    GEOGRAPHY(Point, 4326) NOT NULL, -- B
    home_geom    GEOGRAPHY(Point, 4326) NOT NULL, -- H
    available_at TIMESTAMPTZ NOT NULL,            -- ETA_B
    expires_at   TIMESTAMPTZ NOT NULL,
    status       watch_status NOT NULL DEFAULT 'open',
    matched_fp   CHAR(64) REFERENCES jobs(fp),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_return_watches_open_from_geom
    ON return_watches USING GIST (from_geom) WHERE status = 'open';

CREATE TABLE IF NOT EXISTS heartbeats (
    component  TEXT PRIMARY KEY,
    last_beat  TIMESTAMPTZ NOT NULL DEFAULT now()
);
