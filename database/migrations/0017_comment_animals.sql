-- 0017: comentarios de animals.

COMMENT ON TABLE animals IS
    'Registro de animales. Hoy la llena el personal, no la vision: la identificacion individual por IA es una fase futura.';
COMMENT ON COLUMN animals.tag IS
    'El arete, y la clave primaria. Es la identidad real del animal: la que esta en su oreja y la que usa el personal. El precio es que una clave natural cambia, y de ahi el ON UPDATE CASCADE en events.animal_tag.';
COMMENT ON COLUMN animals.status IS
    'active, inactive o lost. lost NO es inactive: inactive es una baja administrativa, lost es un animal que deberia estar y no aparece, que es lo que el sistema existe para detectar.';
COMMENT ON COLUMN animals.name IS
    'Para que el personal reconozca de que animal habla una alerta.';
COMMENT ON COLUMN animals.last_detection IS
    'Lo escribe el servicio de IA.';
