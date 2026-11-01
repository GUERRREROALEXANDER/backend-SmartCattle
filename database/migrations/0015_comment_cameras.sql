-- 0015: comentarios de cameras y zones.
--
-- COMMENT ON vive en la base, asi que lo ve quien abra la tabla en la consola
-- de Neon, en DBeaver o con \d+ en psql. El diccionario de docs/ es mas
-- extenso, pero no esta ahi cuando alguien mira la tabla a las dos de la
-- manana.
--
-- Solo lo que no se deduce del nombre y el tipo. "id = identificador" no
-- aporta nada; "las coordenadas van de 0 a 1 y no en pixeles" si.

COMMENT ON TABLE cameras IS 'Camaras del predio. Una fila por camara fisica.';
COMMENT ON COLUMN cameras.name IS
    'Identificador que usan la API y el servicio de IA (camera-01). El UNIQUE permite el ON CONFLICT con el que el backend crea la fila sin riesgo de carrera.';
COMMENT ON COLUMN cameras.source IS
    'Indice USB, ruta de video o URL RTSP. Configuracion de vision, no de la API: texto libre a proposito.';
COMMENT ON COLUMN cameras.active IS
    'Se baja para retirar una camara sin borrar sus eventos.';

COMMENT ON TABLE zones IS 'Zona segura de una camara: el rectangulo del que el ganado no deberia salir.';
COMMENT ON COLUMN zones.x_min IS
    'Coordenada normalizada de 0 a 1, NO pixeles: una zona en pixeles se mueve sola al cambiar la resolucion.';
COMMENT ON COLUMN zones.camera_id IS
    'ON DELETE CASCADE: una zona sin camara no significa nada.';
