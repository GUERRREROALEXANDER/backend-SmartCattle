-- 0011: vista v_api_events.
--
-- El filtro que separa "evento de la API" de "fila que escribio otro servicio"
-- estaba escrito tres veces: en GET /api/events, en /api/metrics/summary y en
-- cualquier consulta manual que quisiera lo mismo. Las tres pueden divergir, y
-- de hecho divergieron: la version del feed filtraba dos columnas y la de
-- metricas las mismas dos, cuando el esquema Event exige cuatro.
--
-- La vista pone la regla en un solo lugar y trae ya resuelto el nombre de la
-- camara, que es lo que la API publica como camera_id.
--
-- CREATE OR REPLACE y no DROP + CREATE: un DROP se lleva los permisos
-- concedidos sobre la vista.

CREATE OR REPLACE VIEW v_api_events AS
SELECT e.id,
       e.ai_event_id,
       e.type,
       e.level,
       c.name AS camera_name,
       e.camera_id,
       e.zone_id,
       e.animal_tag,
       e.detected_object,
       e.confidence,
       e.box,
       e.width,
       e.height,
       e.detected_at,
       e.received_at
FROM events e
JOIN cameras c ON c.id = e.camera_id
WHERE e.ai_event_id     IS NOT NULL
  AND e.received_at     IS NOT NULL
  AND e.detected_object IS NOT NULL
  AND e.confidence      IS NOT NULL;

COMMENT ON VIEW v_api_events IS
    'Eventos que el esquema Event de la API puede representar, con el nombre de la camara resuelto.';
