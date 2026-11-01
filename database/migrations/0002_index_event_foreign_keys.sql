-- 0002: índices en las claves ajenas de events.
--
-- PostgreSQL indexa la clave primaria y los UNIQUE, pero **no** las claves
-- ajenas. Sin estos índices:
--
--   * "los eventos de esta cámara" recorre la tabla que más crece;
--   * borrar una cámara o una zona recorre `events` entero para aplicar el
--     ON DELETE SET NULL, con la fila de `cameras` bloqueada mientras tanto.
--
-- `events_animal_tag_idx` ya existía en schema.sql por el primer motivo;
-- faltaban camera_id y zone_id.

CREATE INDEX IF NOT EXISTS events_camera_id_idx ON events (camera_id);
CREATE INDEX IF NOT EXISTS events_zone_id_idx   ON events (zone_id);
