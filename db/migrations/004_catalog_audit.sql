-- Migración aditiva 004: Auditoría de cambios en el catálogo de productos.
-- Registra eventos de creación, edición y activación/desactivación de productos
-- para trazabilidad, reproducibilidad histórica y métricas de margen.

BEGIN;

CREATE TABLE IF NOT EXISTS productos_auditoria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    producto_id INTEGER NOT NULL,
    accion TEXT NOT NULL CHECK (accion IN ('CREAR', 'EDITAR', 'ACTIVAR', 'DESACTIVAR')),
    valores_anteriores TEXT, -- JSON serializado con estado previo o NULL en 'CREAR'
    valores_nuevos TEXT NOT NULL, -- JSON serializado con nuevo estado
    fecha_utc TEXT NOT NULL DEFAULT (datetime('now')),
    actor TEXT NOT NULL DEFAULT 'sistema',
    FOREIGN KEY (producto_id) REFERENCES productos (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_prod_auditoria_prod
    ON productos_auditoria (producto_id);

CREATE INDEX IF NOT EXISTS idx_prod_auditoria_fecha
    ON productos_auditoria (fecha_utc);

INSERT OR IGNORE INTO _schema_migrations (version, nombre, aplicado_en)
VALUES (4, '004_catalog_audit', datetime('now'));

COMMIT;
