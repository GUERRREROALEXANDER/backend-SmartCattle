-- 0003: índices en las claves ajenas que quedan.
--
-- Mismo motivo que 0002, en el resto del esquema:
--
--   * zones.camera_id   -> "las zonas de esta cámara", y el CASCADE al borrar
--                          la cámara.
--   * animals.camera_id -> "los animales que ve esta cámara".
--   * animals.zone_id   -> "los animales de esta zona", y el SET NULL al
--                          borrar la zona.
--   * alerts.event_id   -> el ON DELETE CASCADE de alerts es el más caro de
--                          todos: borrar un evento tenía que recorrer alerts.
--
-- `zones` tiene UNIQUE (camera_id, name), y ese índice compuesto ya sirve
-- para buscar por camera_id, así que no se repite aquí.

CREATE INDEX IF NOT EXISTS animals_camera_id_idx ON animals (camera_id);
CREATE INDEX IF NOT EXISTS animals_zone_id_idx   ON animals (zone_id);
CREATE INDEX IF NOT EXISTS alerts_event_id_idx   ON alerts  (event_id);
