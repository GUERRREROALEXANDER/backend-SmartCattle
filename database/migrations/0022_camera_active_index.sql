-- 0022: indice parcial de camaras activas.
--
-- Las camaras se retiran bajando `active`, no borrando la fila, asi que la
-- tabla acumula camaras que ya no estan. "Las camaras del predio" quiere decir
-- las activas.
--
-- La tabla es pequena -- una fila por camara fisica -- y en una tabla pequena
-- PostgreSQL recorre en vez de usar un indice, asi que esto no es por
-- velocidad. Es por el JOIN: `events` y `animals` se unen a `cameras` en cada
-- consulta del tablero, y ahi si cambia el plan.

CREATE INDEX IF NOT EXISTS cameras_active_idx
    ON cameras (name)
    WHERE active;
