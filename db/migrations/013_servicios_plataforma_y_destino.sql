-- Migración aditiva 013: Plataforma de origen y servicio de destino independientes
-- Compatible con registros históricos existentes (plataforma_origen queda NULL)
BEGIN;

ALTER TABLE cargas_servicios ADD COLUMN plataforma_origen TEXT DEFAULT NULL;
ALTER TABLE cargas_servicios ADD COLUMN servicio_destino TEXT DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_cargas_plataforma_origen
    ON cargas_servicios (plataforma_origen);

CREATE INDEX IF NOT EXISTS idx_cargas_servicio_destino
    ON cargas_servicios (servicio_destino);

INSERT OR IGNORE INTO _schema_migrations (version, nombre)
VALUES (13, '013_servicios_plataforma_y_destino');

COMMIT;
