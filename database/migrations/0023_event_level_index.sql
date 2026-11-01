-- 0023: indice por nivel de severidad.
--
-- `events_type_detected_idx` cubre (type, detected_at). La consulta que falta
-- es por severidad: "las alertas altas de las ultimas 24 horas", que es la
-- unica lista que alguien revisa cuando no tiene tiempo de revisarlas todas.
--
-- Parcial sobre `level = 'high'`: las de nivel medio son la mayoria por
-- defecto, y un indice sobre todos los niveles seria casi la tabla entera.
--
-- Hoy ningun servicio escribe `level`, asi que el indice esta vacio. Se agrega
-- con la columna para que el dia que el backend empiece a clasificar no haga
-- falta crear un indice sobre una tabla ya grande, que es cuando crearlo
-- duele.

CREATE INDEX IF NOT EXISTS events_high_level_idx
    ON events (detected_at DESC)
    WHERE level = 'high';
