-- 0001: events.confidence pasa de REAL a DOUBLE PRECISION.
--
-- REAL es float4: 24 bits de mantisa, ~7 dígitos decimales. Guardar 0.95 y
-- leerlo devuelve 0.949999988079071. El backend lo venía tapando al leer,
-- redondeando a 6 cifras significativas en cada fila, y cualquier otro
-- servicio que consulte la tabla directamente ve el valor sucio.
--
-- DOUBLE PRECISION es float8 y representa 0.95 con holgura. El CHECK
-- (confidence BETWEEN 0 AND 1) sigue valiendo sin tocarlo.
--
-- Idempotente: comprueba el catálogo porque ALTER COLUMN no admite
-- IF NOT EXISTS.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'events' AND column_name = 'confidence' AND data_type = 'real'
    ) THEN
        ALTER TABLE events ALTER COLUMN confidence TYPE DOUBLE PRECISION;

        -- Las filas que ya existían arrastran el error de float4: al ampliar,
        -- 0.949999988 se convierte en 0.949999988079071, no en 0.95. Esto las
        -- limpia de una vez; a partir de aquí las escrituras entran exactas.
        UPDATE events
        SET confidence = round(confidence::numeric, 6)::double precision
        WHERE confidence IS NOT NULL;
    END IF;
END $$;
