-- Adds ai_event_id to an events table created before idempotent ingestion.
--
-- Fresh installs do not need this: db/schema.sql already includes the column.
-- Run only on a database whose events table lacks it:
--   mysql -u root -p smartcattle < db/migrations/0001_add_ai_event_id.sql
--
-- Existing rows have no AI identifier, so each gets its own UUID: that keeps
-- them distinct under the UNIQUE constraint without inventing a shared value.

ALTER TABLE events
  ADD COLUMN ai_event_id CHAR(36) NULL AFTER id;

UPDATE events SET ai_event_id = UUID() WHERE ai_event_id IS NULL;

ALTER TABLE events
  MODIFY COLUMN ai_event_id CHAR(36) NOT NULL,
  ADD UNIQUE KEY uq_events_ai_event_id (ai_event_id);
