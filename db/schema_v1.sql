-- ==========================================================
-- GENARO POS — ESQUEMA RELACIONAL VALIDADO V1 (SQLite / libSQL)
-- ==========================================================

PRAGMA foreign_keys = ON;

-- TABLA DE MIGRACIONES
CREATE TABLE IF NOT EXISTS _schema_migrations (
    version INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL,
    aplicado_en TEXT NOT NULL DEFAULT (datetime('now'))
);

-- CATÁLOGO DE PRODUCTOS
CREATE TABLE IF NOT EXISTS productos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL COLLATE NOCASE,
    categoria TEXT NOT NULL DEFAULT 'Otro',
    proveedor TEXT NOT NULL DEFAULT 'General',
    unidad TEXT NOT NULL DEFAULT 'Unidad',
    costo INTEGER NOT NULL DEFAULT 0 CHECK (costo >= 0),
    precio_dia INTEGER NOT NULL CHECK (precio_dia >= 0),
    precio_noche INTEGER NOT NULL DEFAULT 0,
    margen_ratio REAL GENERATED ALWAYS AS (
        CASE WHEN costo > 0 THEN ROUND((CAST(precio_dia AS REAL) - costo) / costo, 4) ELSE 0.0 END
    ) VIRTUAL,
    activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
    fecha_actualizacion TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_productos_nombre ON productos (nombre);
CREATE INDEX IF NOT EXISTS idx_productos_proveedor ON productos (proveedor);
CREATE INDEX IF NOT EXISTS idx_productos_activo ON productos (activo);

-- TICKETS DE VENTA
CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY,
    fecha_hora_utc TEXT NOT NULL DEFAULT (datetime('now')),
    total_venta INTEGER NOT NULL CHECK (total_venta >= 0),
    monto_efectivo INTEGER NOT NULL DEFAULT 0 CHECK (monto_efectivo >= 0),
    monto_transferencia INTEGER NOT NULL DEFAULT 0 CHECK (monto_transferencia >= 0),
    metodo_pago TEXT NOT NULL CHECK (metodo_pago IN ('EFECTIVO', 'TRANSFERENCIA', 'MIXTO')),
    es_nocturno INTEGER NOT NULL DEFAULT 0 CHECK (es_nocturno IN (0, 1)),
    anulado INTEGER NOT NULL DEFAULT 0 CHECK (anulado IN (0, 1)),
    motivo_anulacion TEXT,
    CONSTRAINT chk_ticket_balance CHECK (total_venta = (monto_efectivo + monto_transferencia))
);

CREATE INDEX IF NOT EXISTS idx_tickets_fecha ON tickets (fecha_hora_utc);
CREATE INDEX IF NOT EXISTS idx_tickets_anulado ON tickets (anulado);

-- ÍTEMS DE VENTA
CREATE TABLE IF NOT EXISTS ticket_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id TEXT NOT NULL,
    producto_id INTEGER,
    producto_nombre TEXT NOT NULL,
    cantidad REAL NOT NULL CHECK (cantidad > 0),
    unidad TEXT NOT NULL DEFAULT 'Unidad',
    precio_unitario INTEGER NOT NULL CHECK (precio_unitario >= 0),
    subtotal INTEGER NOT NULL CHECK (subtotal >= 0),
    metodo_pago TEXT NOT NULL,
    FOREIGN KEY (ticket_id) REFERENCES tickets (id) ON DELETE CASCADE,
    FOREIGN KEY (producto_id) REFERENCES productos (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_items_ticket ON ticket_items (ticket_id);
CREATE INDEX IF NOT EXISTS idx_items_producto_nombre ON ticket_items (producto_nombre);

-- CARGAS Y SERVICIOS
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
    CONSTRAINT chk_carga_balance CHECK ((monto_carga + monto_adicional) = (pago_efectivo + pago_transferencia))
);

CREATE INDEX IF NOT EXISTS idx_cargas_fecha ON cargas_servicios (fecha_hora_utc);
CREATE INDEX IF NOT EXISTS idx_cargas_servicio ON cargas_servicios (servicio);
CREATE UNIQUE INDEX IF NOT EXISTS uq_cargas_legacy_row_id
    ON cargas_servicios (legacy_row_id);

CREATE TABLE IF NOT EXISTS cargas_import_batch (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    source_sha256 TEXT NOT NULL,
    filas_origen INTEGER NOT NULL CHECK (filas_origen >= 0),
    filas_importadas INTEGER NOT NULL CHECK (filas_importadas >= 0),
    filas_cuarentena INTEGER NOT NULL CHECK (filas_cuarentena >= 0),
    grupos_duplicados INTEGER NOT NULL CHECK (grupos_duplicados >= 0),
    importado_en_utc TEXT NOT NULL DEFAULT (datetime('now'))
);

-- GASTOS Y RETIROS
CREATE TABLE IF NOT EXISTS gastos_retiros (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    legacy_row_id TEXT,
    fecha_hora_utc TEXT NOT NULL DEFAULT (datetime('now')),
    tipo TEXT NOT NULL DEFAULT 'General',
    detalle TEXT NOT NULL,
    monto_salida INTEGER NOT NULL CHECK (monto_salida > 0),
    origen_fondos TEXT DEFAULT 'Caja diaria',
    metodo_pago TEXT NOT NULL CHECK (metodo_pago IN ('EFECTIVO', 'TRANSFERENCIA'))
);

CREATE INDEX IF NOT EXISTS idx_gastos_fecha ON gastos_retiros (fecha_hora_utc);

-- COLA OUTBOX TRANSACCIONAL
CREATE TABLE IF NOT EXISTS outbox_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN ('INSERT', 'UPDATE', 'DELETE')),
    payload_json TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'PENDIENTE' CHECK (estado IN ('PENDIENTE', 'EN_PROCESO', 'SINCRONIZADO', 'ERROR')),
    intentos INTEGER NOT NULL DEFAULT 0,
    ultimo_error TEXT,
    creado_en_utc TEXT NOT NULL DEFAULT (datetime('now')),
    procesado_en_utc TEXT
);

CREATE INDEX IF NOT EXISTS idx_outbox_estado ON outbox_events (estado, intentos);
