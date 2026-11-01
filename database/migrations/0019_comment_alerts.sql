-- 0019: comentarios de alerts.

COMMENT ON TABLE alerts IS
    'Lo que se le manda a la persona a cargo, derivado de un evento. Ningun servicio escribe aqui todavia.';
COMMENT ON COLUMN alerts.event_id IS
    'ON DELETE CASCADE, y es la excepcion deliberada del esquema: en todo lo demas se hace SET NULL porque el historico vale, pero una alerta es SOBRE un evento y sin el no se puede auditar ni reenviar.';
COMMENT ON COLUMN alerts.status IS
    'pending, sent, failed o handled. sent es que el mensaje salio; handled es que una PERSONA hizo algo. La diferencia entre avisamos y alguien fue a ver la vaca.';
COMMENT ON COLUMN alerts.detail IS
    'El error del proveedor cuando status = failed.';
COMMENT ON COLUMN alerts.sent_at IS
    'Cuando salio. NULL mientras siga pendiente.';
