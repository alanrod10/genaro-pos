-- Migración 012: Creación de infraestructura de integración comercial (Laboratorio).
-- Incorpora el buffer transaccional outbox (commercial_events_outbox) y
-- el registro informativo de arqueos de adicionales por turno (commercial_adicionales_arqueo_turno).
-- NOTA: Exclusivo de laboratorio aislado. No aplicar sobre bases comerciales remotas ni producción sin autorización.

BEGIN;

-- 1. Buffer transaccional outbox para eventos de integración comercial a tesorería
CREATE TABLE IF NOT EXISTS commercial_events_outbox (
    sequence_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    payload_canonical_hash TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'PROCESSING', 'PUBLISHED', 'FAILED', 'DEAD_LETTER')),
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    max_retries INTEGER NOT NULL DEFAULT 5 CHECK (max_retries >= 1),
    error_message TEXT,
    tesoreria_movement_id TEXT,
    reprocessed_by TEXT,
    reprocessed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    published_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_outbox_status_seq
    ON commercial_events_outbox (status, sequence_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_outbox_idempotency_unique
    ON commercial_events_outbox (idempotency_key);

CREATE INDEX IF NOT EXISTS idx_outbox_entity_lookup
    ON commercial_events_outbox (entity_type, entity_id);

-- 2. Tabla de arqueos informativos de adicionales en cierre de turno
-- Registro informativo para control de adicionales físicos en cajón.
-- NO genera movimientos financieros ni débitos automáticos.
CREATE TABLE IF NOT EXISTS commercial_adicionales_arqueo_turno (
    arqueo_id TEXT PRIMARY KEY,
    cierre_turno_id TEXT,
    fecha_operativa TEXT NOT NULL,
    turno TEXT NOT NULL,
    responsable_caja TEXT NOT NULL,
    total_adicionales_efectivo_calculado_cents INTEGER NOT NULL
        CHECK (total_adicionales_efectivo_calculado_cents >= 0),
    total_adicionales_efectivo_declarado_cents INTEGER
        CHECK (total_adicionales_efectivo_declarado_cents IS NULL OR total_adicionales_efectivo_declarado_cents >= 0),
    diferencia_cents INTEGER,
    observaciones TEXT,
    traslado_ejecutado INTEGER NOT NULL DEFAULT 0
        CHECK (traslado_ejecutado IN (0, 1)),
    traslado_movement_id TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_arqueo_adicionales_fecha
    ON commercial_adicionales_arqueo_turno (fecha_operativa, turno);

-- Registro en control de versiones
INSERT OR IGNORE INTO _schema_migrations (version, nombre, aplicado_en)
VALUES (12, '012_commercial_integration_outbox_lab', datetime('now'));

COMMIT;
