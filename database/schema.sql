-- SmartCattle: PostgreSQL schema (safe to run several times).
--
-- Table and column names are English, matching the REST API, so no service has
-- to translate between the two. Every timestamp is stored in UTC (timestamptz).

CREATE TABLE IF NOT EXISTS cameras (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        TEXT        NOT NULL UNIQUE,
    source      TEXT        NOT NULL DEFAULT '0',  -- USB index, video path or RTSP URL
    location    TEXT,
    active      BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Rectangular safe zone, coordinates normalised between 0 and 1 (as SAFE_ZONE was).
CREATE TABLE IF NOT EXISTS zones (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    camera_id   BIGINT      NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
    name        TEXT        NOT NULL,
    x_min       REAL        NOT NULL CHECK (x_min BETWEEN 0 AND 1),
    y_min       REAL        NOT NULL CHECK (y_min BETWEEN 0 AND 1),
    x_max       REAL        NOT NULL CHECK (x_max BETWEEN 0 AND 1),
    y_max       REAL        NOT NULL CHECK (y_max BETWEEN 0 AND 1),
    active      BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (camera_id, name),
    CHECK (x_min < x_max AND y_min < y_max)
);

-- People who use the system. Registration stores who they are and whether they
-- own the farm or work on it.
CREATE TABLE IF NOT EXISTS users (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Lower-cased before it is stored, so one address cannot register twice
    -- under different capitalisation.
    email          TEXT        NOT NULL UNIQUE CHECK (email = lower(email)),
    -- NEVER the password itself: a bcrypt hash, which cannot be reversed. A
    -- leaked table must not hand over the accounts it describes.
    password_hash  TEXT        NOT NULL,
    full_name      TEXT        NOT NULL,
    -- 'owner' owns the farm, 'worker' works on it. The column records the role;
    -- what each role is allowed to do is not enforced here yet.
    role           TEXT        NOT NULL DEFAULT 'worker'
                   CHECK (role IN ('owner', 'worker')),
    -- Access is withdrawn by clearing this flag, not by deleting the row: an
    -- account that is gone cannot be told apart from one that never existed.
    is_active      BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS users_role_idx ON users (role);

-- Animal registry. Individual identification by the AI is a future phase.
--
-- The primary key is the ear tag, not a database number: the tag is the real
-- identity of the animal, the one physically on its ear and the one the farm
-- staff use. A second numeric identifier would mean translating between the two
-- on every query.
--
-- There is no breed, sex or birth date: SmartCattle tracks cattle for security
-- -- where an animal is and whether it left its zone -- and does not manage the
-- herd commercially. Those are zootechnical and valuation data, and they do not
-- help locate an animal.
CREATE TABLE IF NOT EXISTS animals (
    tag             TEXT        PRIMARY KEY,       -- ear tag, name or code
    status          TEXT        NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'inactive', 'lost')),
    camera_id       BIGINT      REFERENCES cameras(id) ON DELETE SET NULL,
    zone_id         BIGINT      REFERENCES zones(id)   ON DELETE SET NULL,
    name            TEXT,       -- so staff recognise which animal an alert is about
    last_detection  TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Animals are deactivated, never deleted, so "the active herd" is the common
-- query. Without this index it scans the table.
CREATE INDEX IF NOT EXISTS animals_status_idx ON animals (status);

-- Events produced by the rules (today: cattle_out_of_zone).
CREATE TABLE IF NOT EXISTS events (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    type        TEXT        NOT NULL,              -- e.g. 'cattle_out_of_zone'
    level       TEXT        NOT NULL DEFAULT 'medium'
                CHECK (level IN ('low', 'medium', 'high')),
    source      TEXT        NOT NULL DEFAULT 'camera'
                CHECK (source IN ('camera', 'image')),
    camera_id   BIGINT      REFERENCES cameras(id) ON DELETE SET NULL,
    zone_id     BIGINT      REFERENCES zones(id)   ON DELETE SET NULL,
    -- Holds the ear tag, because that is the primary key of `animals`.
    -- ON UPDATE CASCADE: with a natural primary key, replacing a lost tag would
    -- otherwise leave these events pointing at one that no longer exists.
    -- ON DELETE SET NULL: removing an animal must not delete its history.
    animal_tag  TEXT        REFERENCES animals(tag)
                            ON DELETE SET NULL ON UPDATE CASCADE,
    detected_object TEXT,                          -- 'cow', etc.
    confidence  REAL        CHECK (confidence BETWEEN 0 AND 1),
    box         JSONB,                             -- [x1, y1, x2, y2] in pixels
    width       INTEGER,
    height      INTEGER,
    -- `detected_at` is when the AI detected it. `received_at` is when the
    -- backend received it. They differ under network delay, and the difference
    -- reveals a skewed clock in the AI service.
    detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    received_at TIMESTAMPTZ,
    -- Identifier the AI service generates once per detection and reuses on
    -- every retry. The UNIQUE is what makes ingestion idempotent: the database
    -- refuses the second insert, so two simultaneous retries cannot create two
    -- rows. It allows NULL (and PostgreSQL permits several NULLs under a
    -- UNIQUE) so other services writing here are not forced to supply it.
    ai_event_id UUID        UNIQUE
);
CREATE INDEX IF NOT EXISTS events_detected_at_idx   ON events (detected_at DESC);
CREATE INDEX IF NOT EXISTS events_type_detected_idx ON events (type, detected_at DESC);
-- "The events of this animal" scans the table that grows fastest.
CREATE INDEX IF NOT EXISTS events_animal_tag_idx    ON events (animal_tag);

-- Alerts sent (or to be sent) to the person in charge, derived from an event.
CREATE TABLE IF NOT EXISTS alerts (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id    BIGINT      NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    channel     TEXT        NOT NULL DEFAULT 'email'
                CHECK (channel IN ('email', 'whatsapp', 'telegram', 'sms', 'n8n')),
    status      TEXT        NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'sent', 'failed', 'handled')),
    detail      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS alerts_status_idx ON alerts (status);

-- `updated_at` is maintained by the database, not by the application: several
-- services write to these tables (the AI service updates `last_detection`), and
-- a timestamp only one writer refreshes starts lying as soon as another touches
-- the row.
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS animals_updated_at_trg ON animals;
CREATE TRIGGER animals_updated_at_trg
    BEFORE UPDATE ON animals
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS users_updated_at_trg ON users;
CREATE TRIGGER users_updated_at_trg
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Seed data: one camera and the backend's default safe zone.
INSERT INTO cameras (name, source, location)
VALUES ('Main camera', '0', 'To be defined')
ON CONFLICT (name) DO NOTHING;

INSERT INTO zones (camera_id, name, x_min, y_min, x_max, y_max)
SELECT id, 'Safe zone', 0.1, 0.1, 0.9, 0.9
FROM cameras WHERE name = 'Main camera'
ON CONFLICT (camera_id, name) DO NOTHING;
