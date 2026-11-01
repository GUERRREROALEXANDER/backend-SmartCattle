-- 0014: funcion de retencion para events.
--
-- `events` crece sin techo y Neon da 1 GB en el plan gratuito. Hasta ahora no
-- habia forma acordada de recortar, asi que el recorte iba a ser un DELETE
-- escrito a mano en la consola, de noche, por alguien con prisa.
--
-- La funcion deja la decision explicita y acotada:
--
--   SELECT prune_events(interval '90 days');
--
-- Borra por hora de deteccion, devuelve cuantas filas borro, y las alertas se
-- van con sus eventos por el CASCADE que ya existe.
--
-- No se programa aqui: PostgreSQL no tiene cron y Neon no instala pg_cron en
-- el plan gratuito. La llama quien decida la politica.

CREATE OR REPLACE FUNCTION prune_events(older_than interval)
RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    removed bigint;
BEGIN
    IF older_than IS NULL OR older_than <= interval '0' THEN
        RAISE EXCEPTION 'prune_events necesita un intervalo positivo, recibio %', older_than;
    END IF;

    DELETE FROM events WHERE detected_at < now() - older_than;
    GET DIAGNOSTICS removed = ROW_COUNT;
    RETURN removed;
END $$;

COMMENT ON FUNCTION prune_events(interval) IS
    'Borra eventos anteriores al intervalo dado y devuelve cuantos. Las alertas se van en cascada.';
