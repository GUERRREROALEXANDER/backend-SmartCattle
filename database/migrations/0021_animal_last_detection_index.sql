-- 0021: indice en animals.last_detection.
--
-- "Que animales no se ven desde hace mucho" es la consulta de seguridad del
-- sistema: un animal que deberia aparecer y no aparece es exactamente lo que
-- SmartCattle existe para detectar.
--
-- NULLS FIRST coincide con el orden que pide esa consulta, y no es un detalle:
-- un animal que nunca se detecto tiene NULL ahi, y es el caso mas urgente de
-- todos. Un indice con el orden por defecto (NULLS LAST) obliga a recorrerlo
-- al reves para esas filas.
--
-- Parcial sobre el hato activo: los animales inactivos y perdidos no se
-- revisan por esta via, y la tabla los conserva para siempre.

CREATE INDEX IF NOT EXISTS animals_last_detection_idx
    ON animals (last_detection NULLS FIRST)
    WHERE status = 'active';
