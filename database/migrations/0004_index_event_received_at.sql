-- 0004: índice en events.received_at.
--
-- `events_detected_at_idx` ordena por hora de detección, que es lo que mira
-- la interfaz. Pero el feed del backend (GET /api/events) ordena por hora de
-- **recepción**, porque es la única que el backend controla y no depende del
-- reloj del servicio de IA:
--
--   ORDER BY received_at DESC, id DESC LIMIT 1000
--
-- Sin este índice esa consulta ordena la tabla completa en cada petición.
-- Incluye `id` para que el índice cubra el desempate del ORDER BY.
--
-- DESC NULLS LAST coincide con el orden pedido: received_at admite NULL,
-- porque otros servicios escriben aquí sin pasar por el backend.

CREATE INDEX IF NOT EXISTS events_received_at_idx
    ON events (received_at DESC NULLS LAST, id DESC);
