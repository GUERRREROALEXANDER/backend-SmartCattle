-- 0007: indice parcial para la cola de envio.
--
-- La consulta del despachador es siempre la misma:
--
--   SELECT * FROM alerts WHERE status = 'pending' ORDER BY created_at
--
-- `alerts_status_idx` cubre el WHERE, pero indexa las cuatro cadenas de
-- status. En regimen, casi todas las filas son 'sent': el indice crece con el
-- historico completo mientras la cola util son unas pocas filas.
--
-- Un indice parcial solo contiene las pendientes, asi que su tamano depende
-- del atraso y no del historico, y ademas trae el created_at ya ordenado.

CREATE INDEX IF NOT EXISTS alerts_pending_queue_idx
    ON alerts (created_at)
    WHERE status = 'pending';
