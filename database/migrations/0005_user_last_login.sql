-- 0005: users.last_login_at.
--
-- La autenticacion real la va a necesitar para dos cosas distintas: mostrar
-- "ultimo acceso" en el perfil, y detectar cuentas que nadie usa (candidatas
-- a is_active = false).
--
-- Admite NULL: una cuenta recien creada no ha entrado nunca, y eso no es lo
-- mismo que haber entrado en la epoca Unix.

ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ;

COMMENT ON COLUMN users.last_login_at IS
    'Ultimo inicio de sesion correcto. NULL = nunca ha entrado.';
