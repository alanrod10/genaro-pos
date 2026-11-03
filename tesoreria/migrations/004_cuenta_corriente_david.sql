-- Migración 004: Cuenta Corriente y Obligaciones PREPAY / David
-- Incorpora el libro mayor de cuenta corriente vinculado a movimientos reales de Tesorería.
-- Invariantes económicas:
-- 1. Acreditación de saldo digital incrementa fondo 1220 y la obligación con David.
-- 2. Pago efectivo a David debita exclusivamente fondo físico 1130 y reduce la obligación.
-- 3. Reconocimiento de deuda histórica es append-only, auditable y NO afecta fondos ni genera movimientos comerciales.
-- 4. La obligación pendiente no se almacena como saldo mutable: se deriva de los movimientos registrados.

CREATE TABLE IF NOT EXISTS tesoreria_cuenta_corriente_david (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    tipo_movimiento TEXT NOT NULL CHECK(tipo_movimiento IN ('ACREDITACION_SALDO', 'PAGO_EFECTIVO', 'RECONOCIMIENTO_DEUDA_HISTORICA')),
    monto_cents INTEGER NOT NULL CHECK(monto_cents > 0),
    fecha_referencia TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    movement_id TEXT,
    fondo_afectado TEXT CHECK(fondo_afectado IN ('1220', '1130') OR fondo_afectado IS NULL),
    referencia_externa TEXT,
    motivo TEXT NOT NULL CHECK(length(trim(motivo)) > 0),
    actor TEXT NOT NULL CHECK(length(trim(actor)) > 0),
    jornada_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (
        (tipo_movimiento = 'ACREDITACION_SALDO' AND movement_id IS NOT NULL AND fondo_afectado = '1220') OR
        (tipo_movimiento = 'PAGO_EFECTIVO' AND movement_id IS NOT NULL AND fondo_afectado = '1130') OR
        (tipo_movimiento = 'RECONOCIMIENTO_DEUDA_HISTORICA' AND movement_id IS NULL AND fondo_afectado IS NULL)
    ),
    FOREIGN KEY (movement_id) REFERENCES tesoreria_movimientos(movement_id) ON DELETE RESTRICT,
    FOREIGN KEY (jornada_id) REFERENCES tesoreria_jornadas(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_cc_david_tipo ON tesoreria_cuenta_corriente_david(tipo_movimiento);
CREATE INDEX IF NOT EXISTS idx_cc_david_movement_id ON tesoreria_cuenta_corriente_david(movement_id);
CREATE INDEX IF NOT EXISTS idx_cc_david_idempotency ON tesoreria_cuenta_corriente_david(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_cc_david_fecha_ref ON tesoreria_cuenta_corriente_david(fecha_referencia);
