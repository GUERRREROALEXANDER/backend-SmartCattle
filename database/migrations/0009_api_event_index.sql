-- 0009: indice parcial de los eventos que la API puede representar.
--
-- GET /api/events y GET /api/metrics/summary filtran lo mismo, porque el
-- esquema `Event` de la API exige columnas que aqui admiten NULL:
--
--   WHERE ai_event_id IS NOT NULL AND received_at IS NOT NULL
--     AND detected_object IS NOT NULL AND confidence IS NOT NULL
--
-- Los indices existentes ordenan por fecha sobre la tabla **entera**, incluidas
-- las filas que esas consultas van a descartar. Este solo contiene las filas
-- que la API lista, ordenadas por hora de deteccion, que es lo que usan las
-- metricas.
--
-- No se incluye el filtro por `type`: los tipos los define el servicio de IA y
-- un indice que los enumere habria que rehacerlo cada vez que agregue uno.

CREATE INDEX IF NOT EXISTS events_api_listable_idx
    ON events (detected_at DESC)
    WHERE ai_event_id IS NOT NULL
      AND received_at IS NOT NULL
      AND detected_object IS NOT NULL
      AND confidence IS NOT NULL;
