-- Migración aditiva 006: Gastos, Retiros, Arqueo y Cierre Diario de Caja.
-- Garantiza la trazabilidad de egresos físicos y digitales, conciliación y control de caja.

BEGIN;

-- 1. Tabla de gastos y retiros de caja
CREATE TABLE IF NOT EXISTS gastos_retiros (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    legacy_row_id TEXT,
    fecha_hora_utc TEXT NOT NULL DEFAULT (datetime('now')),
    tipo TEXT NOT NULL DEFAULT 'General',
    detalle TEXT NOT NULL,
    monto_salida INTEGER NOT NULL CHECK (monto_salida > 0),
    origen_fondos TEXT DEFAULT 'Caja diaria',
    metodo_pago TEXT NOT NULL CHECK (metodo_pago IN ('EFECTIVO', 'TRANSFERENCIA')),
    anulado INTEGER NOT NULL DEFAULT 0 CHECK (anulado IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_gastos_fecha ON gastos_retiros (fecha_hora_utc);
CREATE INDEX IF NOT EXISTS idx_gastos_tipo ON gastos_retiros (tipo);
CREATE INDEX IF NOT EXISTS idx_gastos_anulado ON gastos_retiros (anulado);

-- 2. Tabla de cierres diarios de caja para arqueo y auditoría
CREATE TABLE IF NOT EXISTS cierres_diarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT NOT NULL UNIQUE, -- Formato YYYY-MM-DD
    efectivo_esperado INTEGER NOT NULL,
    efectivo_contado INTEGER NOT NULL,
    diferencia INTEGER NOT NULL,
    total_ventas_efvo INTEGER NOT NULL DEFAULT 0,
    total_ventas_transf INTEGER NOT NULL DEFAULT 0,
    total_servicios_efvo INTEGER NOT NULL DEFAULT 0,
    total_servicios_transf INTEGER NOT NULL DEFAULT 0,
    total_gastos_efvo INTEGER NOT NULL DEFAULT 0,
    total_gastos_transf INTEGER NOT NULL DEFAULT 0,
    total_banco INTEGER NOT NULL DEFAULT 0,
    observaciones TEXT,
    cerrado_por TEXT NOT NULL DEFAULT 'sistema',
    cerrado_en_utc TEXT NOT NULL DEFAULT (datetime('now')),
    actualizado_en_utc TEXT
);

CREATE INDEX IF NOT EXISTS idx_cierres_fecha ON cierres_diarios (fecha);

-- 3. Tabla de auditoría para trazabilidad de gastos
CREATE TABLE IF NOT EXISTS gastos_auditoria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gasto_id INTEGER NOT NULL,
    accion TEXT NOT NULL CHECK (accion IN ('CREAR', 'EDITAR', 'ANULAR')),
    valores_anteriores TEXT,
    valores_nuevos TEXT NOT NULL,
    fecha_utc TEXT NOT NULL DEFAULT (datetime('now')),
    actor TEXT NOT NULL DEFAULT 'sistema',
    FOREIGN KEY (gasto_id) REFERENCES gastos_retiros (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_gastos_auditoria_gasto ON gastos_auditoria (gasto_id);

INSERT OR IGNORE INTO _schema_migrations (version, nombre, aplicado_en)
VALUES (6, '006_gastos_y_cierres', datetime('now'));

COMMIT;
