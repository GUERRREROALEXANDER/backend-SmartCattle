-- 0025: medidas de los lotes (largo y ancho).
--
-- Un potrero o establo rectangular se describe por sus medidas, y el area sale
-- de ellas. Las dos o ninguna: un solo lado no describe ninguna forma. Quien no
-- sabe las medidas no manda nada; el area sigue siendo opcional e independiente
-- para los lotes que no son rectangulos.
--
-- La tabla lots la crea la migracion 0007 de SmartCattle-Backend, asi que esta
-- migracion se salta si todavia no existe. Es la misma que la 0014 del backend:
-- da igual cual corra primero, la segunda no hace nada.

DO $$
BEGIN
    IF to_regclass('lots') IS NULL THEN
        RAISE NOTICE '0025 omitida: falta la tabla lots (migracion del backend)';
        RETURN;
    END IF;

    ALTER TABLE lots ADD COLUMN IF NOT EXISTS length_meters double precision;
    ALTER TABLE lots ADD COLUMN IF NOT EXISTS width_meters  double precision;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'lots_length_meters_check') THEN
        ALTER TABLE lots ADD CONSTRAINT lots_length_meters_check CHECK (length_meters > 0);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'lots_width_meters_check') THEN
        ALTER TABLE lots ADD CONSTRAINT lots_width_meters_check CHECK (width_meters > 0);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'lots_dimensions_pair_check') THEN
        ALTER TABLE lots ADD CONSTRAINT lots_dimensions_pair_check
            CHECK ((length_meters IS NULL) = (width_meters IS NULL));
    END IF;

    COMMENT ON COLUMN lots.length_meters IS
        'Largo en metros de un lote rectangular. Va con width_meters; el area sale de los dos.';
    COMMENT ON COLUMN lots.width_meters IS
        'Ancho en metros. NULL junto con length_meters cuando no se conocen las medidas.';
END $$;
