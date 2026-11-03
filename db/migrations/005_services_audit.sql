-- Migración aditiva 005: Auditoría y trazabilidad para operaciones de Servicios y Cargas.
-- Registra eventos de creación, edición y anulación de cargas
-- para auditoría contable, conciliación y control de saldos prepagos.

BEGIN;

CREATE TABLE IF NOT EXISTS cargas_servicios_auditoria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    carga_id INTEGER NOT NULL,
    accion TEXT NOT NULL CHECK (accion IN ('CREAR', 'EDITAR', 'ANULAR')),
    valores_anteriores TEXT, -- JSON serializado con estado previo o NULL en 'CREAR'
    valores_nuevos TEXT NOT NULL, -- JSON serializado con nuevo estado
    fecha_utc TEXT NOT NULL DEFAULT (datetime('now')),
    actor TEXT NOT NULL DEFAULT 'sistema',
    FOREIGN KEY (carga_id) REFERENCES cargas_servicios (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_cargas_auditoria_carga
    ON cargas_servicios_auditoria (carga_id);

CREATE INDEX IF NOT EXISTS idx_cargas_auditoria_fecha
    ON cargas_servicios_auditoria (fecha_utc);

INSERT OR IGNORE INTO _schema_migrations (version, nombre, aplicado_en)
VALUES (5, '005_services_audit', datetime('now'));

COMMIT;
