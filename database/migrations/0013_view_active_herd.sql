-- 0013: vista v_active_herd.
--
-- "El hato activo, con donde se vio cada animal por ultima vez." Resuelve los
-- dos LEFT JOIN que hay que acertar cada vez: son LEFT y no INNER porque
-- camera_id y zone_id admiten NULL, y un INNER JOIN esconde en silencio a los
-- animales que todavia no se han visto.

CREATE OR REPLACE VIEW v_active_herd AS
SELECT a.tag,
       a.name,
       a.status,
       a.last_detection,
       c.name AS camera_name,
       z.name AS zone_name,
       now() - a.last_detection AS unseen_for
FROM animals a
LEFT JOIN cameras c ON c.id = a.camera_id
LEFT JOIN zones   z ON z.id = a.zone_id
WHERE a.status = 'active';

COMMENT ON VIEW v_active_herd IS
    'Hato activo con camara y zona resueltas. LEFT JOIN: un animal sin ubicacion sigue estando en el hato.';
