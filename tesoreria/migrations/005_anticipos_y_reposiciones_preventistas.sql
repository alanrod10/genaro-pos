-- ==============================================================================
-- Migración 005: Circuito de Anticipos y Reposición de Preventistas
-- GenaroPOS Tesorería — Staging
-- ==============================================================================
-- Permite el uso temporal de efectivo del fondo 1130 (Prepay Efectivo) para pagos
-- urgentes a preventistas y su posterior reposición jerárquica (1° 1120, 2° 1110).
-- Invariantes económicas:
-- 1. La salida de 1130 debita efectivo real y crea un expediente de anticipo auditable.
-- 2. La reposición restituye el fondo 1130 debitando primero 1120 y luego 1110 si es necesario.
-- 3. La reposición es una transferencia interna, NO un segundo gasto comercial.
-- 4. No se almacena saldo mutable: el importe repuesto y el saldo pendiente se derivan
--    de los movimientos reales registrados en tesoreria_reposiciones_preventistas.
-- ==============================================================================

CREATE TABLE IF NOT EXISTS tesoreria_anticipos_preventistas (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    proveedor TEXT NOT NULL CHECK(length(trim(proveedor)) > 0),
    monto_original_cents INTEGER NOT NULL CHECK(monto_original_cents > 0),
    motivo TEXT NOT NULL CHECK(length(trim(motivo)) > 0),
    referencia_documento TEXT,
    pedido_id TEXT,
    salida_movement_id TEXT NOT NULL UNIQUE,
    creado_por TEXT NOT NULL CHECK(length(trim(creado_por)) > 0),
    creado_en_utc TEXT NOT NULL,
    jornada_id INTEGER,
    FOREIGN KEY (salida_movement_id) REFERENCES tesoreria_movimientos(movement_id) ON DELETE RESTRICT,
    FOREIGN KEY (jornada_id) REFERENCES tesoreria_jornadas(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_anticipos_prev_proveedor ON tesoreria_anticipos_preventistas(proveedor);
CREATE INDEX IF NOT EXISTS idx_anticipos_prev_pedido ON tesoreria_anticipos_preventistas(pedido_id);
CREATE INDEX IF NOT EXISTS idx_anticipos_prev_salida ON tesoreria_anticipos_preventistas(salida_movement_id);
CREATE INDEX IF NOT EXISTS idx_anticipos_prev_fecha ON tesoreria_anticipos_preventistas(creado_en_utc);
CREATE INDEX IF NOT EXISTS idx_anticipos_prev_idem ON tesoreria_anticipos_preventistas(idempotency_key);

CREATE TABLE IF NOT EXISTS tesoreria_reposiciones_preventistas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reposicion_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    anticipo_id TEXT NOT NULL,
    fondo_origen TEXT NOT NULL CHECK(fondo_origen IN ('1120', '1110')),
    fondo_destino TEXT NOT NULL CHECK(fondo_destino = '1130'),
    monto_cents INTEGER NOT NULL CHECK(monto_cents > 0),
    movement_id_egreso TEXT NOT NULL UNIQUE,
    movement_id_ingreso TEXT NOT NULL UNIQUE,
    motivo TEXT NOT NULL,
    actor TEXT NOT NULL CHECK(length(trim(actor)) > 0),
    creado_en_utc TEXT NOT NULL,
    jornada_id INTEGER,
    FOREIGN KEY (anticipo_id) REFERENCES tesoreria_anticipos_preventistas(id) ON DELETE RESTRICT,
    FOREIGN KEY (movement_id_egreso) REFERENCES tesoreria_movimientos(movement_id) ON DELETE RESTRICT,
    FOREIGN KEY (movement_id_ingreso) REFERENCES tesoreria_movimientos(movement_id) ON DELETE RESTRICT,
    FOREIGN KEY (jornada_id) REFERENCES tesoreria_jornadas(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_reposiciones_prev_anticipo ON tesoreria_reposiciones_preventistas(anticipo_id);
CREATE INDEX IF NOT EXISTS idx_reposiciones_prev_origen ON tesoreria_reposiciones_preventistas(fondo_origen);
CREATE INDEX IF NOT EXISTS idx_reposiciones_prev_fecha ON tesoreria_reposiciones_preventistas(creado_en_utc);
CREATE INDEX IF NOT EXISTS idx_reposiciones_prev_idem ON tesoreria_reposiciones_preventistas(idempotency_key);
