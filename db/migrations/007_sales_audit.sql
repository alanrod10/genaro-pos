-- Migración aditiva 007: Auditoría del historial de ventas e ítems
-- No modifica productos, tickets ni cargas_servicios existentes.

BEGIN;

CREATE TABLE IF NOT EXISTS ventas_auditoria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id TEXT NOT NULL,
    item_id INTEGER,
    accion TEXT NOT NULL CHECK (accion IN ('CREACION', 'ANULACION', 'EDICION_ITEM', 'ELIMINACION_ITEM')),
    motivo TEXT,
    usuario TEXT NOT NULL DEFAULT 'sistema',
    datos_previos_json TEXT,
    datos_nuevos_json TEXT,
    creado_en_utc TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (ticket_id) REFERENCES tickets (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ventas_auditoria_ticket
    ON ventas_auditoria (ticket_id);

CREATE INDEX IF NOT EXISTS idx_ventas_auditoria_fecha
    ON ventas_auditoria (creado_en_utc);

INSERT OR IGNORE INTO _schema_migrations (version, nombre)
VALUES (7, 'ventas_auditoria');

COMMIT;
