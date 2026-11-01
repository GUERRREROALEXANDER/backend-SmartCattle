-- 0016: comentarios de users.

COMMENT ON TABLE users IS
    'Personas que usan el sistema. Ningun servicio la consume todavia: el backend no tiene endpoints de autenticacion.';
COMMENT ON COLUMN users.email IS
    'Con CHECK (email = lower(email)). Ese CHECK es lo que hace valer al UNIQUE: sin el, Ana@finca.com y ana@finca.com son dos filas distintas y la misma persona entra dos veces.';
COMMENT ON COLUMN users.password_hash IS
    'Hash bcrypt, NUNCA la contrasena y NUNCA SHA. Una tabla filtrada no debe entregar las cuentas que describe, y SHA sin salt se revierte por volumen en una GPU.';
COMMENT ON COLUMN users.role IS
    'owner o worker. Registra el rol; lo que cada rol PUEDE hacer lo tiene que exigir el backend, no se controla aqui.';
COMMENT ON COLUMN users.is_active IS
    'Se baja para retirar el acceso. La fila se queda: una cuenta borrada no se distingue de una que nunca existio.';
COMMENT ON COLUMN users.updated_at IS
    'Lo refresca el trigger users_updated_at_trg, no la aplicacion.';
