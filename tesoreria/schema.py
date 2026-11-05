"""Esquema de Base de Datos y DDL — GENARO TESORERÍA LABORATORIO.

Implementación de LAB-001 a LAB-012:
- Tablas aisladas para catálogo de fondos, auditoría, jornadas, movimientos, traslados, reservas e idempotencia.
- Sprint 3: Arqueos (LAB-009), Expedientes de diferencias (LAB-009), Cierres definitivos con snapshot canónico (LAB-010),
  Reversiones inmutables (LAB-011), Pista de auditoría append-only con cadena de hash (LAB-012) y Outbox transaccional (LAB-012).
- Restricciones CHECK para códigos canónicos, estados, naturaleza, titularidad y fórmulas de preservación.
- Triggers de inmutabilidad estricta (prohibido DELETE/UPDATE en movimientos confirmados, cierres y auditoría).
- Saldos enteros en centavos INTEGER sin punto flotante.
"""

import sqlite3

DDL_TESORERIA = """
-- 1. Catálogo Independiente de Fondos de Tesorería
CREATE TABLE IF NOT EXISTS tesoreria_fondos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo TEXT NOT NULL UNIQUE CHECK(codigo IN ('1110', '1120', '1130', '1140', '1150', '1160', '1210', '1220', '1230', '1310')),
    nombre TEXT NOT NULL CHECK(length(trim(nombre)) > 0),
    naturaleza TEXT NOT NULL CHECK(naturaleza IN ('EFECTIVO', 'FONDO_FIJO', 'ADICIONALES_PARTICULARES', 'RECAUDACION_TERCEROS', 'CUENTA_BANCARIA', 'BILLETERA_VIRTUAL', 'TRANSITO', 'TRANSITO_PREVENTISTAS')),
    titularidad TEXT NOT NULL CHECK(titularidad IN ('PROPIA', 'CUSTODIA', 'TRANSITO')),
    moneda TEXT NOT NULL DEFAULT 'ARS' CHECK(moneda = 'ARS'),
    estado TEXT NOT NULL DEFAULT 'ACTIVO' CHECK(estado IN ('ACTIVO', 'INACTIVO', 'BLOQUEADO')),
    saldo_centavos INTEGER NOT NULL DEFAULT 0 CHECK(typeof(saldo_centavos) = 'integer'),
    permite_sobregiro INTEGER NOT NULL DEFAULT 0 CHECK(permite_sobregiro IN (0, 1)),
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    creado_por TEXT NOT NULL DEFAULT 'SISTEMA_LAB',
    actualizado_por TEXT NOT NULL DEFAULT 'SISTEMA_LAB'
);

-- 2. Tabla de Auditoría Inmutable de Fondos
CREATE TABLE IF NOT EXISTS tesoreria_fondos_auditoria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fondo_id INTEGER NOT NULL,
    codigo TEXT NOT NULL,
    accion TEXT NOT NULL CHECK(accion IN ('INSERT', 'UPDATE', 'DELETE_ATTEMPT', 'BLOQUEO')),
    usuario TEXT NOT NULL,
    valores_previos TEXT, -- JSON con snapshot anterior
    valores_nuevos TEXT,   -- JSON con snapshot nuevo
    motivo TEXT,
    timestamp_utc TEXT NOT NULL,
    FOREIGN KEY (fondo_id) REFERENCES tesoreria_fondos(id) ON DELETE RESTRICT
);

-- 3. Tabla de Jornadas de Tesorería (LAB-004 & LAB-010)
CREATE TABLE IF NOT EXISTS tesoreria_jornadas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    numero_jornada INTEGER NOT NULL UNIQUE CHECK(numero_jornada > 0),
    fecha_operativa TEXT NOT NULL CHECK(length(fecha_operativa) = 10), -- YYYY-MM-DD
    estado TEXT NOT NULL DEFAULT 'ABIERTA' CHECK(estado IN ('ABIERTA', 'EN_ARQUEO', 'EN_CIERRE', 'CERRADA')),
    apertura_timestamp_utc TEXT NOT NULL,
    cierre_timestamp_utc TEXT,
    usuario_apertura TEXT NOT NULL CHECK(length(trim(usuario_apertura)) > 0),
    usuario_cierre TEXT,
    total_apertura_centavos INTEGER NOT NULL DEFAULT 0 CHECK(typeof(total_apertura_centavos) = 'integer'),
    total_cierre_centavos INTEGER CHECK(total_cierre_centavos IS NULL OR typeof(total_cierre_centavos) = 'integer'),
    observaciones TEXT,
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1)
);

-- 4. Detalle de Fondos por Jornada (LAB-004)
CREATE TABLE IF NOT EXISTS tesoreria_jornada_detalles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    jornada_id INTEGER NOT NULL,
    fondo_id INTEGER NOT NULL,
    codigo_fondo TEXT NOT NULL,
    saldo_apertura_centavos INTEGER NOT NULL DEFAULT 0 CHECK(typeof(saldo_apertura_centavos) = 'integer'),
    saldo_cierre_centavos INTEGER CHECK(saldo_cierre_centavos IS NULL OR typeof(saldo_cierre_centavos) = 'integer'),
    diferencia_centavos INTEGER CHECK(diferencia_centavos IS NULL OR typeof(diferencia_centavos) = 'integer'),
    FOREIGN KEY (jornada_id) REFERENCES tesoreria_jornadas(id) ON DELETE RESTRICT,
    FOREIGN KEY (fondo_id) REFERENCES tesoreria_fondos(id) ON DELETE RESTRICT
);

-- 5. Tabla de Idempotencia de Operaciones (Sprint 2 & 3)
CREATE TABLE IF NOT EXISTS tesoreria_idempotencia (
    idempotency_key TEXT PRIMARY KEY,
    operation_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- 6. Tabla de Movimientos de Tesorería (LAB-005 & LAB-011)
CREATE TABLE IF NOT EXISTS tesoreria_movimientos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    movement_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT UNIQUE,
    journal_id INTEGER NOT NULL,
    fund_id INTEGER NOT NULL,
    movement_type TEXT NOT NULL CHECK(movement_type IN ('ENTRY', 'EXIT', 'TRANSFER_OUT', 'TRANSFER_IN', 'REVERSAL', 'SWEEP_OUT', 'SWEEP_IN')),
    amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
    currency TEXT NOT NULL DEFAULT 'ARS' CHECK(currency = 'ARS'),
    counterpart TEXT NOT NULL CHECK(length(trim(counterpart)) > 0),
    reason TEXT NOT NULL CHECK(length(trim(reason)) > 0),
    evidence_reference TEXT,
    created_by TEXT NOT NULL CHECK(length(trim(created_by)) > 0),
    terminal_id TEXT NOT NULL DEFAULT 'TERM-01',
    status TEXT NOT NULL CHECK(status IN ('POSTED', 'PENDING_REVIEW', 'REJECTED', 'REVERSED')),
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    is_reversal INTEGER NOT NULL DEFAULT 0 CHECK(is_reversal IN (0, 1)),
    reversal_of_id TEXT,
    reversed_amount_cents INTEGER NOT NULL DEFAULT 0 CHECK(reversed_amount_cents >= 0),
    FOREIGN KEY (journal_id) REFERENCES tesoreria_jornadas(id) ON DELETE RESTRICT,
    FOREIGN KEY (fund_id) REFERENCES tesoreria_fondos(id) ON DELETE RESTRICT
);

-- 7. Tabla de Traslados de Fondos (LAB-006 & LAB-011)
CREATE TABLE IF NOT EXISTS tesoreria_traslados (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transfer_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT UNIQUE,
    journal_id INTEGER NOT NULL,
    origin_fund_id INTEGER NOT NULL,
    destination_fund_id INTEGER NOT NULL,
    amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
    currency TEXT NOT NULL DEFAULT 'ARS' CHECK(currency = 'ARS'),
    status TEXT NOT NULL CHECK(status IN ('PENDING_RECEIPT', 'CONFIRMED', 'CANCELLED', 'REVERSED')),
    dispatched_by TEXT NOT NULL,
    received_by TEXT,
    dispatched_at TEXT NOT NULL,
    received_at TEXT,
    reason TEXT NOT NULL CHECK(length(trim(reason)) > 0),
    evidence_reference TEXT,
    out_movement_id TEXT REFERENCES tesoreria_movimientos(movement_id),
    in_movement_id TEXT REFERENCES tesoreria_movimientos(movement_id),
    CHECK(origin_fund_id <> destination_fund_id),
    FOREIGN KEY (origin_fund_id) REFERENCES tesoreria_fondos(id) ON DELETE RESTRICT,
    FOREIGN KEY (destination_fund_id) REFERENCES tesoreria_fondos(id) ON DELETE RESTRICT,
    FOREIGN KEY (journal_id) REFERENCES tesoreria_jornadas(id) ON DELETE RESTRICT
);

-- 8. Tabla de Reservas de Fondos (LAB-007)
CREATE TABLE IF NOT EXISTS tesoreria_reservas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reservation_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT UNIQUE,
    journal_id INTEGER NOT NULL,
    fund_id INTEGER NOT NULL,
    original_amount_cents INTEGER NOT NULL CHECK(original_amount_cents > 0),
    applied_amount_cents INTEGER NOT NULL DEFAULT 0 CHECK(applied_amount_cents >= 0),
    released_amount_cents INTEGER NOT NULL DEFAULT 0 CHECK(released_amount_cents >= 0),
    pending_amount_cents INTEGER NOT NULL CHECK(pending_amount_cents >= 0),
    reason TEXT NOT NULL CHECK(length(trim(reason)) > 0),
    beneficiary TEXT NOT NULL CHECK(length(trim(beneficiary)) > 0),
    obligation_id TEXT,
    evidence_reference TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ACTIVE', 'PARTIALLY_APPLIED', 'FULLY_APPLIED', 'RELEASED', 'CANCELLED')),
    CHECK(original_amount_cents = applied_amount_cents + released_amount_cents + pending_amount_cents),
    FOREIGN KEY (fund_id) REFERENCES tesoreria_fondos(id) ON DELETE RESTRICT,
    FOREIGN KEY (journal_id) REFERENCES tesoreria_jornadas(id) ON DELETE RESTRICT
);

-- Índice único condicional: protección anti-doble reserva activa sobre una misma obligación
CREATE UNIQUE INDEX IF NOT EXISTS idx_tesoreria_reservas_active_obligation
ON tesoreria_reservas(obligation_id)
WHERE obligation_id IS NOT NULL AND status IN ('ACTIVE', 'PARTIALLY_APPLIED');

-- 9. Tabla de Movimientos sobre Reservas (Aplicaciones y Liberaciones)
CREATE TABLE IF NOT EXISTS tesoreria_reserva_movimientos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reservation_id TEXT NOT NULL,
    action_type TEXT NOT NULL CHECK(action_type IN ('APPLY', 'RELEASE')),
    amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
    performed_by TEXT NOT NULL,
    reason TEXT NOT NULL CHECK(length(trim(reason)) > 0),
    evidence_reference TEXT,
    occurred_at TEXT NOT NULL,
    idempotency_key TEXT UNIQUE,
    is_reversal INTEGER NOT NULL DEFAULT 0 CHECK(is_reversal IN (0, 1)),
    reversal_of_id INTEGER,
    FOREIGN KEY (reservation_id) REFERENCES tesoreria_reservas(reservation_id) ON DELETE RESTRICT
);

-- 10. Tabla de Auditoría General de Tesorería (Legacy Sprint 2)
CREATE TABLE IF NOT EXISTS tesoreria_auditoria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL CHECK(entity_type IN ('FONDO', 'MOVIMIENTO', 'TRASLADO', 'RESERVA', 'JORNADA', 'ARQUEO', 'CIERRE', 'REVERSION')),
    entity_id TEXT NOT NULL,
    action TEXT NOT NULL,
    user_id TEXT NOT NULL,
    details_json TEXT,
    timestamp_utc TEXT NOT NULL
);

-- 11. LAB-009: Tabla de Arqueos y Reconciliaciones
CREATE TABLE IF NOT EXISTS tesoreria_arqueos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reconciliation_id TEXT NOT NULL UNIQUE,
    journal_id INTEGER NOT NULL,
    fund_id INTEGER NOT NULL,
    reconciliation_type TEXT NOT NULL CHECK(reconciliation_type IN ('OPENING', 'PARTIAL', 'TOTAL', 'EXTRAORDINARY', 'CLOSING')),
    status TEXT NOT NULL DEFAULT 'RESOLVED' CHECK(status IN ('OPEN', 'UNDER_REVIEW', 'PENDING', 'RESOLVED')),
    saldo_registrado_cents INTEGER NOT NULL,
    saldo_verificado_cents INTEGER NOT NULL CHECK(saldo_verificado_cents >= 0),
    diferencia_cents INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'ARS' CHECK(currency = 'ARS'),
    performed_by TEXT NOT NULL,
    performed_at TEXT NOT NULL,
    notes TEXT,
    difference_case_id TEXT,
    authorized_by TEXT,
    authorized_at TEXT,
    authorization_notes TEXT,
    FOREIGN KEY (journal_id) REFERENCES tesoreria_jornadas(id) ON DELETE RESTRICT,
    FOREIGN KEY (fund_id) REFERENCES tesoreria_fondos(id) ON DELETE RESTRICT
);

-- 12. LAB-009: Tabla de Expedientes de Diferencias
CREATE TABLE IF NOT EXISTS tesoreria_expedientes_diferencias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL UNIQUE,
    reconciliation_id TEXT NOT NULL,
    journal_id INTEGER NOT NULL,
    fund_id INTEGER NOT NULL,
    difference_cents INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK(status IN ('OPEN', 'UNDER_REVIEW', 'PENDING', 'RESOLVED')),
    investigation_notes TEXT NOT NULL,
    resolution_notes TEXT,
    resolution_evidence TEXT,
    resolved_by TEXT,
    resolved_at TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
    authorized_by TEXT,
    authorized_at TEXT,
    authorization_notes TEXT,
    FOREIGN KEY (reconciliation_id) REFERENCES tesoreria_arqueos(reconciliation_id) ON DELETE RESTRICT,
    FOREIGN KEY (journal_id) REFERENCES tesoreria_jornadas(id) ON DELETE RESTRICT,
    FOREIGN KEY (fund_id) REFERENCES tesoreria_fondos(id) ON DELETE RESTRICT
);

-- 13. LAB-010: Tabla de Cierres Diarios y Snapshots Canónicos
CREATE TABLE IF NOT EXISTS tesoreria_cierres (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    closure_id TEXT NOT NULL UNIQUE,
    journal_id INTEGER NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'CONFIRMED' CHECK(status IN ('INITIATED', 'SWEEP_COMPLETED', 'CONFIRMED')),
    closed_by TEXT NOT NULL,
    closed_at TEXT NOT NULL,
    sweep_transfer_id TEXT,
    total_registrado_cents INTEGER NOT NULL,
    total_disponible_cents INTEGER NOT NULL,
    total_reservado_cents INTEGER NOT NULL,
    total_diferencias_cents INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL,
    canonical_hash TEXT NOT NULL,
    observations TEXT,
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
    FOREIGN KEY (journal_id) REFERENCES tesoreria_jornadas(id) ON DELETE RESTRICT
);

-- 14. LAB-011: Tabla de Reversiones
CREATE TABLE IF NOT EXISTS tesoreria_reversiones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reversal_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT UNIQUE,
    target_type TEXT NOT NULL CHECK(target_type IN ('MOVEMENT', 'TRANSFER', 'RESERVATION_OPERATION')),
    target_id TEXT NOT NULL,
    original_amount_cents INTEGER NOT NULL CHECK(original_amount_cents > 0),
    reversed_amount_cents INTEGER NOT NULL CHECK(reversed_amount_cents > 0),
    counter_movement_id TEXT,
    reason TEXT NOT NULL CHECK(length(trim(reason)) > 0),
    evidence_reference TEXT,
    authorized_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'COMPLETED' CHECK(status IN ('COMPLETED', 'FAILED')),
    FOREIGN KEY (counter_movement_id) REFERENCES tesoreria_movimientos(movement_id) ON DELETE RESTRICT
);

-- 15. LAB-012: Pista de Auditoría Append-Only con Cadena de Hash Verificable
CREATE TABLE IF NOT EXISTS tesoreria_auditoria_eventos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    sequence_number INTEGER NOT NULL UNIQUE CHECK(sequence_number > 0),
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    user_id TEXT NOT NULL,
    terminal_id TEXT NOT NULL DEFAULT 'TERM-01',
    payload_json TEXT NOT NULL,
    prev_canonical_hash TEXT NOT NULL,
    canonical_hash TEXT NOT NULL
);

-- 16. LAB-012: Outbox Transaccional
CREATE TABLE IF NOT EXISTS tesoreria_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING', 'PUBLISHED', 'FAILED', 'DEAD_LETTER')),
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count >= 0),
    max_retries INTEGER NOT NULL DEFAULT 3 CHECK(max_retries >= 1),
    last_error TEXT,
    created_at TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    published_at TEXT
);

-- 17. LAB-012: Eventos Consumidos (Deduplicación / Idempotencia de Consumo)
CREATE TABLE IF NOT EXISTS tesoreria_eventos_consumidos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    consumer_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    consumed_at TEXT NOT NULL,
    UNIQUE(consumer_id, event_id)
);

-- ===================================================================
-- TRIGGERS DE INTEGRIDAD E INMUTABILIDAD
-- ===================================================================

-- Trigger: PROHIBIDO eliminar fondos del catálogo raíz del sistema
CREATE TRIGGER IF NOT EXISTS trg_proteger_borrado_fondos
BEFORE DELETE ON tesoreria_fondos
BEGIN
    SELECT RAISE(ABORT, 'VIOLACIÓN DE INTEGRIDAD: No está permitido eliminar fondos del catálogo de tesorería.');
END;

-- Trigger de auditoría automática al insertar fondo
CREATE TRIGGER IF NOT EXISTS trg_audit_insert_fondo
AFTER INSERT ON tesoreria_fondos
BEGIN
    INSERT INTO tesoreria_fondos_auditoria (
        fondo_id, codigo, accion, usuario, valores_previos, valores_nuevos, motivo, timestamp_utc
    ) VALUES (
        NEW.id,
        NEW.codigo,
        'INSERT',
        NEW.creado_por,
        NULL,
        json_object(
            'codigo', NEW.codigo,
            'nombre', NEW.nombre,
            'naturaleza', NEW.naturaleza,
            'titularidad', NEW.titularidad,
            'estado', NEW.estado,
            'saldo_centavos', NEW.saldo_centavos
        ),
        'Alta inicial en catálogo',
        NEW.created_at
    );
END;

-- Trigger de auditoría automática al actualizar fondo
CREATE TRIGGER IF NOT EXISTS trg_audit_update_fondo
AFTER UPDATE ON tesoreria_fondos
BEGIN
    INSERT INTO tesoreria_fondos_auditoria (
        fondo_id, codigo, accion, usuario, valores_previos, valores_nuevos, motivo, timestamp_utc
    ) VALUES (
        NEW.id,
        NEW.codigo,
        'UPDATE',
        NEW.actualizado_por,
        json_object(
            'codigo', OLD.codigo,
            'nombre', OLD.nombre,
            'naturaleza', OLD.naturaleza,
            'titularidad', OLD.titularidad,
            'estado', OLD.estado,
            'saldo_centavos', OLD.saldo_centavos,
            'version', OLD.version
        ),
        json_object(
            'codigo', NEW.codigo,
            'nombre', NEW.nombre,
            'naturaleza', NEW.naturaleza,
            'titularidad', NEW.titularidad,
            'estado', NEW.estado,
            'saldo_centavos', NEW.saldo_centavos,
            'version', NEW.version
        ),
        'Modificación de atributos',
        NEW.updated_at
    );
END;

-- Trigger: Prohibir UPDATE en movimientos confirmados (POSTED)
CREATE TRIGGER IF NOT EXISTS trg_proteger_movimientos_update
BEFORE UPDATE ON tesoreria_movimientos
FOR EACH ROW
WHEN OLD.status = 'POSTED'
BEGIN
    SELECT CASE
        WHEN NEW.movement_id <> OLD.movement_id
          OR NEW.journal_id <> OLD.journal_id
          OR NEW.fund_id <> OLD.fund_id
          OR NEW.movement_type <> OLD.movement_type
          OR NEW.amount_cents <> OLD.amount_cents
          OR NEW.currency <> OLD.currency
          OR NEW.counterpart <> OLD.counterpart
          OR NEW.reason <> OLD.reason
          OR NEW.created_by <> OLD.created_by
          OR NEW.occurred_at <> OLD.occurred_at
        THEN RAISE(ABORT, 'RESTRICCIÓN DE INMUTABILIDAD: No está permitido modificar movimientos confirmados (POSTED).')
    END;
END;

-- Trigger: Prohibir DELETE en movimientos
CREATE TRIGGER IF NOT EXISTS trg_proteger_movimientos_delete
BEFORE DELETE ON tesoreria_movimientos
BEGIN
    SELECT RAISE(ABORT, 'RESTRICCIÓN DE INMUTABILIDAD: No está permitido eliminar movimientos de tesorería.');
END;

-- Trigger: Prohibir DELETE en traslados
CREATE TRIGGER IF NOT EXISTS trg_proteger_traslados_delete
BEFORE DELETE ON tesoreria_traslados
BEGIN
    SELECT RAISE(ABORT, 'RESTRICCIÓN DE INMUTABILIDAD: No está permitido eliminar registros de traslados.');
END;

-- Trigger: Prohibir UPDATE en traslados consolidados
CREATE TRIGGER IF NOT EXISTS trg_proteger_traslados_update
BEFORE UPDATE ON tesoreria_traslados
FOR EACH ROW
WHEN OLD.status IN ('CONFIRMED', 'CANCELLED')
BEGIN
    SELECT CASE
        WHEN NEW.amount_cents <> OLD.amount_cents
          OR NEW.origin_fund_id <> OLD.origin_fund_id
          OR NEW.destination_fund_id <> OLD.destination_fund_id
          OR NEW.transfer_id <> OLD.transfer_id
          OR NEW.dispatched_at <> OLD.dispatched_at
        THEN RAISE(ABORT, 'RESTRICCIÓN DE INMUTABILIDAD: No está permitido modificar traslados consolidados.')
    END;
END;

-- Trigger: Prohibir UPDATE en arqueos consolidados
CREATE TRIGGER IF NOT EXISTS trg_proteger_arqueos_update
BEFORE UPDATE ON tesoreria_arqueos
FOR EACH ROW
BEGIN
    SELECT CASE
        WHEN NEW.reconciliation_id <> OLD.reconciliation_id
          OR NEW.journal_id <> OLD.journal_id
          OR NEW.fund_id <> OLD.fund_id
          OR NEW.reconciliation_type <> OLD.reconciliation_type
          OR NEW.saldo_registrado_cents <> OLD.saldo_registrado_cents
          OR NEW.saldo_verificado_cents <> OLD.saldo_verificado_cents
          OR NEW.diferencia_cents <> OLD.diferencia_cents
        THEN RAISE(ABORT, 'RESTRICCIÓN DE INMUTABILIDAD: No está permitido modificar importes ni fondos de arqueos registrados.')
    END;
END;

-- Trigger: Prohibir UPDATE en reversiones consolidadas
CREATE TRIGGER IF NOT EXISTS trg_proteger_reversiones_update
BEFORE UPDATE ON tesoreria_reversiones
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'RESTRICCIÓN DE INMUTABILIDAD: No está permitido modificar registros de reversiones.');
END;

-- Trigger: garantizar que solo pueda existir una única jornada activa simultáneamente
CREATE TRIGGER IF NOT EXISTS trg_unica_jornada_abierta
BEFORE INSERT ON tesoreria_jornadas
WHEN NEW.estado IN ('ABIERTA', 'EN_ARQUEO', 'EN_CIERRE')
BEGIN
    SELECT CASE
        WHEN (SELECT COUNT(*) FROM tesoreria_jornadas WHERE estado IN ('ABIERTA', 'EN_ARQUEO', 'EN_CIERRE')) > 0
        THEN RAISE(ABORT, 'RESTRICCIÓN DE INTEGRIDAD: Ya existe una jornada de tesorería abierta. Debe cerrarse antes de iniciar una nueva.')
    END;
END;

-- Trigger: Prohibir UPDATE en cierres confirmados
CREATE TRIGGER IF NOT EXISTS trg_proteger_cierres_update
BEFORE UPDATE ON tesoreria_cierres
FOR EACH ROW
WHEN OLD.status = 'CONFIRMED'
BEGIN
    SELECT RAISE(ABORT, 'RESTRICCIÓN DE INMUTABILIDAD: No está permitido modificar un cierre de jornada confirmado.');
END;

-- Trigger: Prohibir DELETE en cierres
CREATE TRIGGER IF NOT EXISTS trg_proteger_cierres_delete
BEFORE DELETE ON tesoreria_cierres
BEGIN
    SELECT RAISE(ABORT, 'RESTRICCIÓN DE INMUTABILIDAD: No está permitido eliminar registros de cierres.');
END;

-- Trigger: Prohibir UPDATE en jornadas cerradas definitivamente
CREATE TRIGGER IF NOT EXISTS trg_proteger_jornadas_cerradas_update
BEFORE UPDATE ON tesoreria_jornadas
FOR EACH ROW
WHEN OLD.estado = 'CERRADA'
BEGIN
    SELECT RAISE(ABORT, 'RESTRICCIÓN DE INMUTABILIDAD: No está permitido modificar una jornada que ya ha sido cerrada.');
END;

-- Trigger: Prohibir DELETE en jornadas de tesorería
CREATE TRIGGER IF NOT EXISTS trg_proteger_jornadas_delete
BEFORE DELETE ON tesoreria_jornadas
BEGIN
    SELECT RAISE(ABORT, 'RESTRICCIÓN DE INMUTABILIDAD: No está permitido eliminar registros de jornadas de tesorería.');
END;

-- Trigger: Prohibir DELETE en arqueos
CREATE TRIGGER IF NOT EXISTS trg_proteger_arqueos_delete
BEFORE DELETE ON tesoreria_arqueos
BEGIN
    SELECT RAISE(ABORT, 'RESTRICCIÓN DE INMUTABILIDAD: No está permitido eliminar registros de arqueos.');
END;

-- Trigger: Prohibir DELETE en reversiones
CREATE TRIGGER IF NOT EXISTS trg_proteger_reversiones_delete
BEFORE DELETE ON tesoreria_reversiones
BEGIN
    SELECT RAISE(ABORT, 'RESTRICCIÓN DE INMUTABILIDAD: No está permitido eliminar registros de reversiones.');
END;

-- Trigger: Prohibir UPDATE en la pista de auditoría append-only
CREATE TRIGGER IF NOT EXISTS trg_proteger_auditoria_eventos_update
BEFORE UPDATE ON tesoreria_auditoria_eventos
BEGIN
    SELECT RAISE(ABORT, 'RESTRICCIÓN DE INMUTABILIDAD: La pista de auditoría es estrictamente inmutable.');
END;

-- Trigger: Prohibir DELETE en la pista de auditoría append-only
CREATE TRIGGER IF NOT EXISTS trg_proteger_auditoria_eventos_delete
BEFORE DELETE ON tesoreria_auditoria_eventos
BEGIN
    SELECT RAISE(ABORT, 'RESTRICCIÓN DE INMUTABILIDAD: Prohibido eliminar registros de la pista de auditoría.');
END;

-- 12. Tablas del Circuito PREPAY (Fase 2 / LAB PREPAY)
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

-- 13. Cuenta Corriente y Obligaciones PREPAY / David (Fase 2 / CC PREPAY)
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
"""


def inicializar_esquema_laboratorio(conn: sqlite3.Connection) -> None:
    """Ejecuta el script DDL de creación de tablas, índices y triggers en la conexión aislada."""
    conn.executescript(DDL_TESORERIA)
    # Migración/compatibilidad idempotente para columnas de autorización de diferencias
    for table in ("tesoreria_arqueos", "tesoreria_expedientes_diferencias"):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for col in ("authorized_by", "authorized_at", "authorization_notes"):
            if col not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT;")

    # Sincronizar control de versiones estructurales de Tesorería
    from tesoreria.catalog import sembrar_catalogo_canónico
    from tesoreria.migrations_runner import ejecutar_migraciones_tesoreria, migrar_tabla_fondos_si_requiere_1160
    migrar_tabla_fondos_si_requiere_1160(conn)
    sembrar_catalogo_canónico(conn)
    ejecutar_migraciones_tesoreria(conn)

    conn.commit()
