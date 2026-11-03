-- Migración 014: Tablas de Coordinación PREPAY y Titularidad del Cargo Diario ($350)
-- Incorpora el registro canónico de pre-reserva síncrona y la gobernanza del cargo por jornada.
-- NOTA: Exclusivo de laboratorio aislado / desarrollo. No aplicar sobre bases remotas o producción sin autorización.

BEGIN;

-- 1. Tabla de propiedad y asignación única del cargo operativo diario ($350) por jornada
CREATE TABLE IF NOT EXISTS tesoreria_prepay_cargo_diario (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    jornada_id INTEGER NOT NULL,
    fecha_jornada TEXT NOT NULL,
    estado TEXT NOT NULL CHECK(estado IN ('PENDING_RESERVATION', 'RESERVED', 'CONFIRMED', 'APPLIED', 'RELEASED')),
    titular_reserva_id TEXT NOT NULL,
    titular_commercial_ticket_id TEXT,
    titular_idempotency_key TEXT NOT NULL,
    amount_cents INTEGER NOT NULL DEFAULT 35000 CHECK(amount_cents > 0),
    created_at TEXT NOT NULL,
    confirmed_at TEXT,
    applied_at TEXT,
    released_at TEXT,
    updated_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
    UNIQUE(jornada_id)
);

CREATE INDEX IF NOT EXISTS idx_prepay_cargo_jornada ON tesoreria_prepay_cargo_diario(jornada_id);
CREATE INDEX IF NOT EXISTS idx_prepay_cargo_estado ON tesoreria_prepay_cargo_diario(estado);

-- 2. Máquina de estados de 10 niveles y coordinación bidireccional PREPAY
CREATE TABLE IF NOT EXISTS tesoreria_prepay_coordinacion (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    coordination_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL,
    jornada_id INTEGER NOT NULL,
    servicio_destino TEXT NOT NULL,
    plataforma TEXT NOT NULL DEFAULT 'PREPAY',
    monto_capital_cents INTEGER NOT NULL CHECK(monto_capital_cents > 0),
    monto_adicional_cents INTEGER NOT NULL DEFAULT 0 CHECK(monto_adicional_cents >= 0),
    pago_efvo_cents INTEGER NOT NULL DEFAULT 0 CHECK(pago_efvo_cents >= 0),
    pago_transf_cents INTEGER NOT NULL DEFAULT 0 CHECK(pago_transf_cents >= 0),
    asume_cargo_diario INTEGER NOT NULL CHECK(asume_cargo_diario IN (0, 1)),
    monto_cargo_cents INTEGER NOT NULL DEFAULT 0 CHECK(monto_cargo_cents >= 0),
    capital_reservation_id TEXT NOT NULL,
    fee_reservation_id TEXT,
    estado TEXT NOT NULL CHECK(estado IN (
        'RESERVATION_REQUESTED', 'ACTIVE', 'COMMERCIAL_COMMITTED', 'COMMERCIAL_REJECTED',
        'RELEASE_PENDING', 'RELEASED', 'APPLICATION_PENDING', 'FULLY_APPLIED',
        'RECONCILIATION_REQUIRED', 'EXPIRED'
    )),
    commercial_ticket_id INTEGER,
    worker_movement_ids TEXT,
    error_detail TEXT,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (jornada_id) REFERENCES tesoreria_jornadas(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_prepay_coord_key ON tesoreria_prepay_coordinacion(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_prepay_coord_estado ON tesoreria_prepay_coordinacion(estado);
CREATE INDEX IF NOT EXISTS idx_prepay_coord_ticket ON tesoreria_prepay_coordinacion(commercial_ticket_id);

-- Registro en control de versiones
INSERT OR IGNORE INTO _schema_migrations (version, nombre, aplicado_en)
VALUES (14, '014_prepay_coordinacion_y_cargo_diario', datetime('now'));

COMMIT;
