-- 0012: vista v_camera_health.
--
-- Responde "que camaras estan de verdad en linea", que no es lo mismo que
-- leer reported_status: esa columna dice lo ultimo que dijo la IA, y una IA
-- que se cayo hace una hora sigue diciendo 'online'.
--
-- La vista aplica la misma regla que el backend (CAMERA_OFFLINE_AFTER_SECONDS,
-- 60 s por defecto) para que una consulta manual no contradiga a la API.
--
-- Las columnas reported_status y last_report_at las crea la migracion
-- 0002_camera_status de SmartCattle-Backend, asi que la vista se salta si
-- todavia no estan.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'cameras' AND column_name = 'last_report_at'
    ) THEN
        RAISE NOTICE 'v_camera_health omitida: falta cameras.last_report_at (migracion del backend)';
        RETURN;
    END IF;

    CREATE OR REPLACE VIEW v_camera_health AS
    SELECT c.id,
           c.name,
           c.location,
           c.active,
           c.reported_status,
           c.last_report_at,
           c.last_online_at,
           c.last_error,
           now() - c.last_report_at AS silent_for,
           CASE
               WHEN c.last_report_at IS NULL OR c.reported_status IS NULL THEN 'unknown'
               WHEN now() - c.last_report_at > interval '60 seconds'      THEN 'offline'
               ELSE c.reported_status
           END AS effective_status
    FROM cameras c;

    COMMENT ON VIEW v_camera_health IS
        'Estado efectivo de cada camara: offline cuando la IA dejo de reportar, aunque la columna diga online.';
END $$;
