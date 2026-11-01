-- 0018: comentarios de events.

COMMENT ON TABLE events IS
    'Lo que detectan las reglas. La tabla que mas crece, y la que manda en el diseno del esquema.';
COMMENT ON COLUMN events.type IS
    'Hoy solo cattle_out_of_zone. Sin CHECK a proposito: los tipos los define el servicio de IA, que evoluciona aparte. El costo es que quien lee tiene que filtrar los que no conoce.';
COMMENT ON COLUMN events.level IS
    'low, medium o high. NINGUN servicio la escribe todavia: el frontend deduce la severidad del tipo de evento.';
COMMENT ON COLUMN events.animal_tag IS
    'El arete. Sin usar aun: la IA no identifica animales todavia. ON UPDATE CASCADE porque apunta a una clave primaria natural.';
COMMENT ON COLUMN events.box IS
    'Caja [x1, y1, x2, y2] en PIXELES, no normalizada como zones. Normalizarla es dividir por width y height.';
COMMENT ON COLUMN events.detected_at IS
    'Reloj del servicio de IA, en el PC del predio. Comparado con received_at revela un reloj desfasado.';
COMMENT ON COLUMN events.received_at IS
    'Reloj del backend. Admite NULL: otros servicios escriben aqui sin pasar por el.';
COMMENT ON COLUMN events.ai_event_id IS
    'UUID que la IA genera una vez por deteccion y reutiliza en cada reintento. El UNIQUE es lo que hace idempotente la ingesta: la base rechaza el segundo INSERT, asi que dos reintentos simultaneos no crean dos filas. Admite NULL para no obligar a los demas servicios.';
