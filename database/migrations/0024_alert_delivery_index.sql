-- 0024: indice de alertas enviadas por fecha de envio.
--
-- `alerts_pending_queue_idx` (0007) sirve a la cola. La otra pregunta es
-- historica: "que se envio anoche", y esa ordena por `sent_at`, que es NULL
-- mientras la alerta siga pendiente.
--
-- Parcial sobre `sent_at IS NOT NULL`: las pendientes no tienen fecha de envio
-- y no pertenecen a esta consulta. Asi el indice tampoco guarda una entrada
-- por cada NULL.

CREATE INDEX IF NOT EXISTS alerts_sent_at_idx
    ON alerts (sent_at DESC)
    WHERE sent_at IS NOT NULL;
