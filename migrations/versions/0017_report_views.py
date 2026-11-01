"""Report views for querying the database directly

Ready-made queries for anyone with psql or a BI tool: the API serves its own
reports (GET /api/reports/*), and these give the same answers to someone who
only has the database.

Days are cut in the session time zone: a timestamptz cast to date follows
the TimeZone setting. Run SET TIME ZONE 'America/Bogota' first to get
Colombian days; the default on Neon is UTC.

PostgreSQL only. SQLite is the test and local fallback, and nobody points a
reporting tool at it.

The database repository has the same views as its migration 0028, which 0012
applies first. CREATE OR REPLACE with identical definitions makes running
both harmless; tests/test_database.py fails if the two drift apart.

Revision ID: 0017_report_views
Revises: 0016_history
Create Date: 2026-10-31
"""
from alembic import op

revision = "0017_report_views"
down_revision = "0016_history"
branch_labels = None
depends_on = None

VIEWS: dict[str, str] = {
    # Alerts raised per day, by severity, and how many of them are still pending.
    "v_alerts_daily": """
        SELECT created_at::date AS day,
               severity,
               count(*) AS total,
               count(*) FILTER (WHERE status = 'open') AS open,
               count(*) FILTER (WHERE status = 'acknowledged') AS acknowledged,
               count(*) FILTER (WHERE status = 'resolved') AS resolved
          FROM security_alerts
         GROUP BY 1, 2
    """,
    # How long each alert waited, and who handled it.
    "v_alert_response_times": """
        SELECT a.id,
               c.name AS camera,
               a.severity,
               a.status,
               a.created_at,
               round((extract(epoch FROM a.acknowledged_at - a.created_at) / 60)::numeric, 1)
                   AS minutes_to_acknowledge,
               round((extract(epoch FROM a.resolved_at - a.created_at) / 60)::numeric, 1)
                   AS minutes_to_resolve,
               ack.email AS acknowledged_by,
               res.email AS resolved_by
          FROM security_alerts a
          LEFT JOIN cameras c ON c.id = a.camera_id
          LEFT JOIN users ack ON ack.id = a.acknowledged_by
          LEFT JOIN users res ON res.id = a.resolved_by
    """,
    # Detections per day and camera, by detection time. Rows without one come
    # from other services writing partial events and cannot be placed on a day.
    "v_events_daily": """
        SELECT e.detected_at::date AS day,
               c.name AS camera,
               e.type AS event_type,
               e.level,
               count(*) AS total,
               round(avg(e.confidence)::numeric, 3) AS avg_confidence
          FROM events e
          JOIN cameras c ON c.id = e.camera_id
         WHERE e.detected_at IS NOT NULL
         GROUP BY 1, 2, 3, 4
    """,
    # The per-minute samples rolled up to the hour.
    "v_occupancy_hourly": """
        SELECT c.name AS camera,
               date_trunc('hour', s.minute) AS hour,
               round(avg(s.cattle_inside)::numeric, 1) AS avg_inside,
               max(s.cattle_inside) AS max_inside,
               round(avg(s.cattle_outside)::numeric, 1) AS avg_outside,
               max(s.cattle_outside) AS max_outside,
               count(*) AS samples
          FROM occupancy_samples s
          JOIN cameras c ON c.id = s.camera_id
         GROUP BY 1, 2
    """,
    # Each status a camera was in, and for how long. ended_at is NULL for the current one.
    "v_camera_status_periods": """
        SELECT c.name AS camera,
               s.status,
               s.error,
               s.changed_at AS started_at,
               lead(s.changed_at) OVER w AS ended_at,
               round((extract(epoch FROM coalesce(lead(s.changed_at) OVER w, now()) - s.changed_at) / 60)::numeric, 1)
                   AS minutes
          FROM camera_status_changes s
          JOIN cameras c ON c.id = s.camera_id
        WINDOW w AS (PARTITION BY s.camera_id ORDER BY s.changed_at, s.id)
    """,
    # One row per farm: its owner, its lots and what they add up to.
    "v_farm_overview": """
        SELECT f.id AS farm_id,
               f.name AS farm,
               u.email AS owner,
               f.area_hectares,
               count(l.id) AS lots,
               count(l.id) FILTER (WHERE l.active) AS active_lots,
               sum(l.area_square_meters) AS lots_area_square_meters,
               sum(l.capacity) AS total_capacity,
               count(l.camera_id) AS lots_with_camera
          FROM farms f
          JOIN users u ON u.id = f.owner_id
          LEFT JOIN lots l ON l.farm_id = f.id
         GROUP BY f.id, u.email
    """,
    # Every uploaded video with who sent it and what the analysis found.
    "v_video_processing": """
        SELECT v.id,
               v.file_name,
               v.status,
               u.email AS uploaded_by,
               l.name AS lot,
               v.size_bytes,
               v.attempts,
               v.created_at,
               v.finished_at,
               round((extract(epoch FROM v.finished_at - v.started_at))::numeric, 1) AS processing_seconds,
               a.max_cattle,
               a.max_people,
               v.error
          FROM video_uploads v
          JOIN users u ON u.id = v.uploaded_by
          LEFT JOIN lots l ON l.id = v.lot_id
          LEFT JOIN video_analyses a ON a.id = v.analysis_id
    """,
}


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for name, query in VIEWS.items():
        op.execute(f"CREATE OR REPLACE VIEW {name} AS {query}")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for name in reversed(VIEWS):
        op.execute(f"DROP VIEW IF EXISTS {name}")
