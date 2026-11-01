-- 0010: width y height de events tienen que ser positivos.
--
-- Son la resolucion del cuadro, y son lo que le da sentido a `box`: las cajas
-- estan en pixeles, asi que normalizarlas es dividir por estos dos numeros.
-- Un 0 ahi es una division por cero en quien consuma la caja, y un negativo
-- no significa nada.
--
-- Siguen admitiendo NULL, porque no todos los servicios los reportan. La regla
-- es "si viene, es positivo", no "tiene que venir".

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'events_frame_size_check'
    ) THEN
        -- Limpia primero lo que ya hubiera, o el ALTER falla al validar.
        UPDATE events SET width = NULL WHERE width IS NOT NULL AND width <= 0;
        UPDATE events SET height = NULL WHERE height IS NOT NULL AND height <= 0;

        ALTER TABLE events ADD CONSTRAINT events_frame_size_check
            CHECK ((width IS NULL OR width > 0) AND (height IS NULL OR height > 0));
    END IF;
END $$;
