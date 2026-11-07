-- Migración 008: Estados de Apertura Económica Diferida, Protección de Disponibilidad y Ajustes Compensatorios
-- GenaroPOS V4: Permite desplegar y operar distinguiendo explícitamente entre saldo cero y saldo desconocido.
-- Estados canónicos de apertura: NOT_CONFIGURED, PENDING_OPENING, OPENED, RECONCILIATION_REQUIRED, BLOCKED.

-- 1. Ampliar tesoreria_fondos con columnas de apertura diferida
ALTER TABLE tesoreria_fondos ADD COLUMN estado_apertura TEXT NOT NULL DEFAULT 'PENDING_OPENING';
ALTER TABLE tesoreria_fondos ADD COLUMN fecha_corte TEXT DEFAULT NULL;
ALTER TABLE tesoreria_fondos ADD COLUMN naturaleza_saldo TEXT DEFAULT NULL;
ALTER TABLE tesoreria_fondos ADD COLUMN observaciones_apertura TEXT DEFAULT NULL;
ALTER TABLE tesoreria_fondos ADD COLUMN comprobante_apertura TEXT DEFAULT NULL;
ALTER TABLE tesoreria_fondos ADD COLUMN idempotency_key TEXT DEFAULT NULL;

-- 2. Ampliar tesoreria_obligaciones_apertura
ALTER TABLE tesoreria_obligaciones_apertura ADD COLUMN estado_apertura TEXT NOT NULL DEFAULT 'PENDING_OPENING';
ALTER TABLE tesoreria_obligaciones_apertura ADD COLUMN fecha_corte TEXT DEFAULT NULL;
ALTER TABLE tesoreria_obligaciones_apertura ADD COLUMN observaciones_apertura TEXT DEFAULT NULL;
ALTER TABLE tesoreria_obligaciones_apertura ADD COLUMN comprobante_apertura TEXT DEFAULT NULL;
ALTER TABLE tesoreria_obligaciones_apertura ADD COLUMN idempotency_key TEXT DEFAULT NULL;

-- 3. Ampliar tesoreria_saldos_iniciales_auditoria con campos de trazabilidad avanzada
ALTER TABLE tesoreria_saldos_iniciales_auditoria ADD COLUMN fecha_corte TEXT DEFAULT NULL;
ALTER TABLE tesoreria_saldos_iniciales_auditoria ADD COLUMN naturaleza_saldo TEXT DEFAULT NULL;
ALTER TABLE tesoreria_saldos_iniciales_auditoria ADD COLUMN idempotency_key TEXT DEFAULT NULL;
ALTER TABLE tesoreria_saldos_iniciales_auditoria ADD COLUMN tipo_operacion TEXT NOT NULL DEFAULT 'APERTURA_INICIAL';
ALTER TABLE tesoreria_saldos_iniciales_auditoria ADD COLUMN estado_apertura TEXT NOT NULL DEFAULT 'OPENED';

-- Índices para búsqueda rápida e idempotencia
CREATE INDEX IF NOT EXISTS idx_saldos_audit_idem ON tesoreria_saldos_iniciales_auditoria(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_fondos_estado_apertura ON tesoreria_fondos(estado_apertura);
CREATE INDEX IF NOT EXISTS idx_obligaciones_estado_apertura ON tesoreria_obligaciones_apertura(estado_apertura);

-- 4. Garantizar que todos los fondos sin saldo inicial confirmado por el propietario permanezcan en 'PENDING_OPENING'
UPDATE tesoreria_fondos
SET estado_apertura = 'PENDING_OPENING'
WHERE saldo_inicial_configurado_en IS NULL OR saldo_inicial_configurado_por = 'SISTEMA_INICIAL';

UPDATE tesoreria_obligaciones_apertura
SET estado_apertura = 'PENDING_OPENING'
WHERE configurado_por = 'SISTEMA_INICIAL';
