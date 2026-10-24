-- Tabla de cargas históricas y servicios.
-- Esta migración es aditiva: no modifica productos ni tablas de ventas.
BEGIN;

CREATE TABLE IF NOT EXISTS cargas_servicios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    legacy_row_id TEXT,
    fecha_hora_utc TEXT NOT NULL DEFAULT (datetime('now')),
    servicio TEXT NOT NULL COLLATE NOCASE,
    monto_carga INTEGER NOT NULL CHECK (monto_carga > 0),
    monto_adicional INTEGER NOT NULL DEFAULT 0 CHECK (monto_adicional >= 0),
    total_cobrado INTEGER NOT NULL CHECK (total_cobrado >= 0),
    pago_efectivo INTEGER NOT NULL DEFAULT 0 CHECK (pago_efectivo >= 0),
    pago_transferencia INTEGER NOT NULL DEFAULT 0 CHECK (pago_transferencia >= 0),
    anulado INTEGER NOT NULL DEFAULT 0 CHECK (anulado IN (0, 1)),
    CONSTRAINT chk_carga_total CHECK (
        total_cobrado = (monto_carga + monto_adicional)
    ),
    CONSTRAINT chk_carga_balance CHECK (
        (monto_carga + monto_adicional) =
        (pago_efectivo + pago_transferencia)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_cargas_legacy_row_id
    ON cargas_servicios (legacy_row_id);

CREATE INDEX IF NOT EXISTS idx_cargas_fecha
    ON cargas_servicios (fecha_hora_utc);

CREATE INDEX IF NOT EXISTS idx_cargas_servicio
    ON cargas_servicios (servicio);

CREATE TABLE IF NOT EXISTS cargas_import_batch (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    source_sha256 TEXT NOT NULL,
    filas_origen INTEGER NOT NULL CHECK (filas_origen >= 0),
    filas_importadas INTEGER NOT NULL CHECK (filas_importadas >= 0),
    filas_cuarentena INTEGER NOT NULL CHECK (filas_cuarentena >= 0),
    grupos_duplicados INTEGER NOT NULL CHECK (grupos_duplicados >= 0),
    importado_en_utc TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO _schema_migrations (version, nombre)
VALUES (2, 'crear_cargas_servicios');

COMMIT;
