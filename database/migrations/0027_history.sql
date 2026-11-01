-- 0027: tablas de historial.
--
-- Hasta aqui la base guardaba solo el presente: el ultimo conteo por camara y
-- el estado actual, sobrescritos. Estas guardan el pasado a un ritmo que cabe
-- en el plan gratuito de Neon:
--
-- - occupancy_samples: una fila por camara y minuto, no por reporte.
-- - camera_status_changes: una fila por cambio de estado, no por reporte.
-- - audit_log: una fila por accion de una persona.
--
-- Las escribe solo el backend. Son las mismas que crea su migracion 0016; da
-- igual cual corra primero.

CREATE TABLE IF NOT EXISTS occupancy_samples (
    camera_id      bigint      NOT NULL REFERENCES cameras (id) ON DELETE CASCADE,
    minute         timestamptz NOT NULL,
    cattle_inside  integer     NOT NULL,
    cattle_outside integer     NOT NULL,
    observed_at    timestamptz NOT NULL,
    -- Empieza por camera_id, asi que sirve tambien de indice de la clave ajena.
    CONSTRAINT occupancy_samples_pkey PRIMARY KEY (camera_id, minute),
    CONSTRAINT occupancy_samples_inside_check  CHECK (cattle_inside >= 0),
    CONSTRAINT occupancy_samples_outside_check CHECK (cattle_outside >= 0)
);
CREATE INDEX IF NOT EXISTS occupancy_samples_minute_idx ON occupancy_samples (minute);

COMMENT ON TABLE occupancy_samples IS
    'Conteo de ganado por camara, uno por minuto: el ultimo observado en ese minuto. Crece ~1 440 filas por camara al dia.';
COMMENT ON COLUMN occupancy_samples.minute IS 'El minuto, con los segundos en cero (UTC).';

CREATE TABLE IF NOT EXISTS camera_status_changes (
    id         bigint       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    camera_id  bigint       NOT NULL REFERENCES cameras (id) ON DELETE CASCADE,
    status     text         NOT NULL,
    error      varchar(300),
    changed_at timestamptz  NOT NULL,
    CONSTRAINT camera_status_changes_status_check CHECK (status IN ('online', 'offline', 'error'))
);
CREATE INDEX IF NOT EXISTS camera_status_changes_camera_id_changed_at_idx
    ON camera_status_changes (camera_id, changed_at);

COMMENT ON TABLE camera_status_changes IS
    'Cada cambio del estado que reporta la IA. Repetir el mismo estado no es un cambio; un silencio tampoco se registra.';

CREATE TABLE IF NOT EXISTS audit_log (
    id          bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_id    bigint      REFERENCES users (id) ON DELETE SET NULL,
    actor_email text,
    action      text        NOT NULL,
    target_type text,
    target_id   text,
    detail      jsonb,
    ip_address  varchar(45),
    created_at  timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_log_created_at_idx        ON audit_log (created_at);
CREATE INDEX IF NOT EXISTS audit_log_actor_id_idx          ON audit_log (actor_id);
CREATE INDEX IF NOT EXISTS audit_log_action_created_at_idx ON audit_log (action, created_at);

COMMENT ON TABLE audit_log IS
    'Quien hizo que: inicios de sesion, cambios de cuentas, alertas atendidas y cambios de configuracion. Nunca contrasenas.';
COMMENT ON COLUMN audit_log.actor_email IS
    'Copia, no join: un inicio fallido trae un correo sin cuenta, y el registro debe leerse igual si el correo cambia.';
