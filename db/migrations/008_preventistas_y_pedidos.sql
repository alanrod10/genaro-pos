-- ==============================================================================
-- Migración 008: Módulo de Preventistas y Pedidos a Proveedores
-- Compatible con SQLite (local) y Turso (libSQL remoto)
-- ==============================================================================

BEGIN;

-- 1. Tabla principal de pedidos a proveedores / preventistas
CREATE TABLE IF NOT EXISTS pedidos_proveedor (
    id TEXT PRIMARY KEY,
    fecha TEXT NOT NULL,
    proveedor TEXT NOT NULL,
    estado TEXT NOT NULL CHECK (estado IN ('PENDIENTE', 'RECIBIDO', 'CANCELADO')) DEFAULT 'PENDIENTE',
    total REAL NOT NULL DEFAULT 0,
    observaciones TEXT,
    creado_en_utc TEXT NOT NULL DEFAULT (datetime('now')),
    actualizado_en_utc TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_pedidos_proveedor_fecha ON pedidos_proveedor (fecha);
CREATE INDEX IF NOT EXISTS idx_pedidos_proveedor_prov ON pedidos_proveedor (proveedor);
CREATE INDEX IF NOT EXISTS idx_pedidos_proveedor_estado ON pedidos_proveedor (estado);

-- 2. Ítems del pedido (congelan nombre y costo/precio al momento de registrar)
CREATE TABLE IF NOT EXISTS pedido_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pedido_id TEXT NOT NULL REFERENCES pedidos_proveedor(id) ON DELETE CASCADE,
    producto_id INTEGER,
    producto_nombre TEXT NOT NULL,
    cantidad REAL NOT NULL CHECK (cantidad > 0),
    costo_unitario REAL NOT NULL DEFAULT 0,
    subtotal REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_pedido_items_pedido ON pedido_items (pedido_id);

-- 3. Tabla de auditoría para pedidos
CREATE TABLE IF NOT EXISTS pedidos_auditoria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pedido_id TEXT NOT NULL,
    accion TEXT NOT NULL CHECK (accion IN ('CREAR_PEDIDO', 'CAMBIAR_ESTADO', 'CANCELAR_PEDIDO', 'EDITAR_PEDIDO')),
    valores_anteriores TEXT,
    valores_nuevos TEXT NOT NULL,
    fecha_utc TEXT NOT NULL DEFAULT (datetime('now')),
    actor TEXT NOT NULL DEFAULT 'sistema'
);

CREATE INDEX IF NOT EXISTS idx_pedidos_auditoria_pedido ON pedidos_auditoria (pedido_id);

INSERT OR IGNORE INTO _schema_migrations (version, nombre, aplicado_en)
VALUES (8, '008_preventistas_y_pedidos', datetime('now'));

COMMIT;
