-- Migración aditiva 010: Idempotencia para Cargas de Servicios y Gastos/Retiros.
-- Agrega columnas idempotency_key e índices únicos en cargas_servicios y gastos_retiros.

BEGIN;

ALTER TABLE cargas_servicios ADD COLUMN idempotency_key TEXT DEFAULT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_cargas_idempotency_key ON cargas_servicios (idempotency_key) WHERE idempotency_key IS NOT NULL;

ALTER TABLE gastos_retiros ADD COLUMN idempotency_key TEXT DEFAULT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_gastos_idempotency_key ON gastos_retiros (idempotency_key) WHERE idempotency_key IS NOT NULL;

INSERT OR IGNORE INTO _schema_migrations (version, nombre, aplicado_en)
VALUES (10, '010_idempotencia_servicios_gastos', datetime('now'));

COMMIT;
