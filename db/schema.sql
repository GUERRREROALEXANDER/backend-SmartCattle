-- SmartCattle Backend - MySQL schema
--
-- Run from the repository root:
--   mysql -u root -p < db/schema.sql
--
-- Safe to run more than once: every statement is guarded by IF NOT EXISTS.

CREATE DATABASE IF NOT EXISTS smartcattle
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE smartcattle;

CREATE TABLE IF NOT EXISTS events (
  -- UUID4 assigned by the backend, stored as its 36-character text form.
  id              CHAR(36)     NOT NULL,
  -- Identifier the AI service generates once per detection and reuses on every
  -- retry. UNIQUE is what makes ingestion idempotent: the database rejects the
  -- second insert, so two simultaneous retries cannot both succeed.
  ai_event_id     CHAR(36)     NOT NULL,
  event_type      VARCHAR(32)  NOT NULL,
  -- 64 mirrors the max_length validated in app/schemas/event.py.
  camera_id       VARCHAR(64)  NOT NULL,
  detected_object VARCHAR(64)  NOT NULL,
  -- DOUBLE, not DECIMAL: the API already treats confidence as a float, so no
  -- conversion is needed on the way in or out.
  confidence      DOUBLE       NOT NULL,
  -- DATETIME carries no timezone. Both columns store UTC, and the store
  -- converts on the way in and re-attaches UTC on the way out.
  -- (6) keeps microseconds, so events within the same second stay ordered.
  `timestamp`     DATETIME(6)  NOT NULL,
  received_at     DATETIME(6)  NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_events_ai_event_id (ai_event_id),
  -- GET /api/events orders by received_at DESC.
  INDEX idx_events_received_at (received_at),
  -- Supports future per-camera queries.
  INDEX idx_events_camera_id (camera_id)
) ENGINE = InnoDB;
