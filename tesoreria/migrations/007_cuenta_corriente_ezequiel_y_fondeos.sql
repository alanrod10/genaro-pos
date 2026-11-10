-- Migración 007: Cuenta Corriente Ezequiel (Claro) y Fondeos Físicos de Custodia
-- Incorpora el libro mayor de cuenta corriente con Ezequiel (proveedor de saldo Claro Comercios)
-- y el subregistro de fondeos físicos pendientes de custodia (Claro y Prepay).

CREATE TABLE IF NOT EXISTS tesoreria_cuenta_corriente_ezequiel (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    tipo_movimiento TEXT NOT NULL CHECK(tipo_movimiento IN ('ACREDITACION_SALDO', 'PAGO_LIQUIDACION', 'RECONOCIMIENTO_DEUDA_HISTORICA')),
    monto_cents INTEGER NOT NULL CHECK(monto_cents > 0),
    fecha_referencia TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    movement_id TEXT,
    fondo_afectado TEXT,
    referencia_externa TEXT,
    motivo TEXT NOT NULL CHECK(length(trim(motivo)) > 0),
    actor TEXT NOT NULL CHECK(length(trim(actor)) > 0),
    jornada_id INTEGER,
    detalle_liquidacion TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (movement_id) REFERENCES tesoreria_movimientos(movement_id) ON DELETE RESTRICT,
    FOREIGN KEY (jornada_id) REFERENCES tesoreria_jornadas(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_cc_ezequiel_tipo ON tesoreria_cuenta_corriente_ezequiel(tipo_movimiento);
CREATE INDEX IF NOT EXISTS idx_cc_ezequiel_movement_id ON tesoreria_cuenta_corriente_ezequiel(movement_id);
CREATE INDEX IF NOT EXISTS idx_cc_ezequiel_idempotency ON tesoreria_cuenta_corriente_ezequiel(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_cc_ezequiel_fecha_ref ON tesoreria_cuenta_corriente_ezequiel(fecha_referencia);

CREATE TABLE IF NOT EXISTS tesoreria_fondeos_pendientes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo_circuito TEXT NOT NULL CHECK(tipo_circuito IN ('CLARO', 'PREPAY')),
    servicio_id INTEGER NOT NULL,
    idempotency_key TEXT UNIQUE NOT NULL,
    monto_capital_cents INTEGER NOT NULL CHECK(monto_capital_cents > 0),
    fondo_origen_esperado TEXT NOT NULL CHECK(fondo_origen_esperado IN ('1110', '1120')),
    fondo_destino_custodia TEXT NOT NULL CHECK(fondo_destino_custodia IN ('1150', '1160', '1130')),
    estado TEXT NOT NULL DEFAULT 'PENDIENTE' CHECK(estado IN ('PENDIENTE', 'FONDEADO', 'CANCELADO')),
    movement_id_traslado TEXT,
    actor TEXT NOT NULL,
    created_at_utc TEXT NOT NULL DEFAULT (datetime('now')),
    funded_at_utc TEXT,
    observaciones TEXT
);

CREATE INDEX IF NOT EXISTS idx_fondeos_pend_circuito ON tesoreria_fondeos_pendientes(tipo_circuito, estado);
CREATE INDEX IF NOT EXISTS idx_fondeos_pend_servicio ON tesoreria_fondeos_pendientes(servicio_id);
