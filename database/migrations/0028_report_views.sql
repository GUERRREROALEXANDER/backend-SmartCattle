-- 0028: vistas de reportes.
--
-- Consultas listas para quien tenga psql o una herramienta de BI. Los dias se
-- cortan en la zona horaria de la sesion; Neon usa UTC, asi que primero:
--
--     SET TIME ZONE 'America/Bogota';
--
-- Las definiciones son las mismas de la migracion 0017 del backend. Las vistas
-- que leen tablas del backend (security_alerts, farms, lots, video_uploads,
-- video_analyses) se saltan si esas tablas todavia no existen, como 0012.

DO $$
BEGIN
    IF to_regclass('security_alerts') IS NULL THEN
        RAISE NOTICE 'v_alerts_daily omitida: faltan tablas del backend (security_alerts)';
        RETURN;
    END IF;

    CREATE OR REPLACE VIEW v_alerts_daily AS
    SELECT created_at::date AS day,
               severity,
               count(*) AS total,
               count(*) FILTER (WHERE status = 'open') AS open,
               count(*) FILTER (WHERE status = 'acknowledged') AS acknowledged,
               count(*) FILTER (WHERE status = 'resolved') AS resolved
          FROM security_alerts
         GROUP BY 1, 2;
END $$;

DO $$
BEGIN
    IF to_regclass('security_alerts') IS NULL THEN
        RAISE NOTICE 'v_alert_response_times omitida: faltan tablas del backend (security_alerts)';
        RETURN;
    END IF;

    CREATE OR REPLACE VIEW v_alert_response_times AS
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
          LEFT JOIN users res ON res.id = a.resolved_by;
END $$;

CREATE OR REPLACE VIEW v_events_daily AS
SELECT e.detected_at::date AS day,
           c.name AS camera,
           e.type AS event_type,
           e.level,
           count(*) AS total,
           round(avg(e.confidence)::numeric, 3) AS avg_confidence
      FROM events e
      JOIN cameras c ON c.id = e.camera_id
     WHERE e.detected_at IS NOT NULL
     GROUP BY 1, 2, 3, 4;

CREATE OR REPLACE VIEW v_occupancy_hourly AS
SELECT c.name AS camera,
           date_trunc('hour', s.minute) AS hour,
           round(avg(s.cattle_inside)::numeric, 1) AS avg_inside,
           max(s.cattle_inside) AS max_inside,
           round(avg(s.cattle_outside)::numeric, 1) AS avg_outside,
           max(s.cattle_outside) AS max_outside,
           count(*) AS samples
      FROM occupancy_samples s
      JOIN cameras c ON c.id = s.camera_id
     GROUP BY 1, 2;

CREATE OR REPLACE VIEW v_camera_status_periods AS
SELECT c.name AS camera,
           s.status,
           s.error,
           s.changed_at AS started_at,
           lead(s.changed_at) OVER w AS ended_at,
           round((extract(epoch FROM coalesce(lead(s.changed_at) OVER w, now()) - s.changed_at) / 60)::numeric, 1)
               AS minutes
      FROM camera_status_changes s
      JOIN cameras c ON c.id = s.camera_id
    WINDOW w AS (PARTITION BY s.camera_id ORDER BY s.changed_at, s.id);

DO $$
BEGIN
    IF to_regclass('farms') IS NULL OR to_regclass('lots') IS NULL THEN
        RAISE NOTICE 'v_farm_overview omitida: faltan tablas del backend (farms, lots)';
        RETURN;
    END IF;

    CREATE OR REPLACE VIEW v_farm_overview AS
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
         GROUP BY f.id, u.email;
END $$;

DO $$
BEGIN
    IF to_regclass('video_uploads') IS NULL OR to_regclass('lots') IS NULL OR to_regclass('video_analyses') IS NULL THEN
        RAISE NOTICE 'v_video_processing omitida: faltan tablas del backend (video_uploads, lots, video_analyses)';
        RETURN;
    END IF;

    CREATE OR REPLACE VIEW v_video_processing AS
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
          LEFT JOIN video_analyses a ON a.id = v.analysis_id;
END $$;
