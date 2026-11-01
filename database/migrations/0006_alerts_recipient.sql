-- 0006: alerts.user_id.
--
-- `alerts` guardaba el canal y el estado, pero no a quien se le mando. Con un
-- solo destinatario eso se podia deducir; en cuanto hay dos personas en el
-- predio, "se envio por WhatsApp" no dice a quien.
--
-- SET NULL y no CASCADE: retirar una cuenta no debe borrar el registro de que
-- esa alerta se envio. La fila de `alerts` es justamente la prueba de que se
-- aviso, y vale aunque la persona ya no este.

ALTER TABLE alerts ADD COLUMN IF NOT EXISTS user_id BIGINT
    REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS alerts_user_id_idx ON alerts (user_id);

COMMENT ON COLUMN alerts.user_id IS
    'A quien se le mando. NULL = destinatario desconocido o cuenta retirada.';
