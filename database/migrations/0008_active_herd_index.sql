-- 0008: indice parcial del hato activo.
--
-- `animals_status_idx` indexa el status de todos los animales. Pero los
-- animales se desactivan y no se borran, asi que con el tiempo la tabla
-- acumula filas inactive y lost que ninguna consulta de operacion mira.
--
-- "El hato activo de esta camara" es la consulta del tablero. El indice
-- parcial la sirve y solo contiene las filas activas.

CREATE INDEX IF NOT EXISTS animals_active_by_camera_idx
    ON animals (camera_id)
    WHERE status = 'active';
