-- Rollback 012: Reversión simétrica de la migración de integración comercial (Laboratorio).
-- NOTA: Exclusivo de laboratorio aislado.

BEGIN;

DROP INDEX IF EXISTS idx_arqueo_adicionales_fecha;
DROP TABLE IF EXISTS commercial_adicionales_arqueo_turno;

DROP INDEX IF EXISTS idx_outbox_entity_lookup;
DROP INDEX IF EXISTS idx_outbox_idempotency_unique;
DROP INDEX IF EXISTS idx_outbox_status_seq;
DROP TABLE IF EXISTS commercial_events_outbox;

DELETE FROM _schema_migrations WHERE version = 12;

COMMIT;
