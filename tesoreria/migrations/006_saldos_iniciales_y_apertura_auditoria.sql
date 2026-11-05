-- Migración 006: Configuración y Edición de Saldos Iniciales / Apertura de Tesorería
-- Incorpora columnas de saldo inicial en fondos, catálogo de apertura de obligaciones y
-- registro de auditoría append-only para trazabilidad estricta de aperturas y modificaciones.

-- 1. Agregar columnas de saldo inicial a tesoreria_fondos si no existen
ALTER TABLE tesoreria_fondos ADD COLUMN saldo_inicial_cents INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tesoreria_fondos ADD COLUMN saldo_inicial_configurado_por TEXT DEFAULT 'SISTEMA_INICIAL';
ALTER TABLE tesoreria_fondos ADD COLUMN saldo_inicial_configurado_en TEXT DEFAULT NULL;

-- 2. Tabla para seguimiento de apertura de obligaciones configurables
CREATE TABLE IF NOT EXISTS tesoreria_obligaciones_apertura (
    codigo TEXT PRIMARY KEY CHECK(codigo IN ('DAVID', 'CLARO', 'PREVENTISTAS')),
    nombre TEXT NOT NULL,
    categoria TEXT NOT NULL DEFAULT 'OBLIGACION',
    saldo_inicial_cents INTEGER NOT NULL DEFAULT 0 CHECK(saldo_inicial_cents >= 0),
    saldo_actual_cents INTEGER NOT NULL DEFAULT 0,
    configurado_por TEXT NOT NULL DEFAULT 'SISTEMA_INICIAL',
    configurado_en TEXT NOT NULL DEFAULT (datetime('now')),
    motivo TEXT NOT NULL DEFAULT 'Apertura oficial en $0 (Regla 2.3)',
    referencia_autorizacion TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Sembrar las 3 obligaciones oficiales en $0
INSERT OR IGNORE INTO tesoreria_obligaciones_apertura (
    codigo, nombre, categoria, saldo_inicial_cents, saldo_actual_cents,
    configurado_por, configurado_en, motivo, updated_at
) VALUES 
('DAVID', 'Deuda pendiente con David', 'OBLIGACION', 0, 0, 'SISTEMA_INICIAL', datetime('now'), 'Inicialización obligatoria en $0 (Regla 2.3)', datetime('now')),
('CLARO', 'Obligaciones circuito Claro / Ezequiel', 'OBLIGACION', 0, 0, 'SISTEMA_INICIAL', datetime('now'), 'Inicialización obligatoria en $0 (Regla 2.3)', datetime('now')),
('PREVENTISTAS', 'Obligaciones de reposición Preventistas (histórico)', 'OBLIGACION', 0, 0, 'SISTEMA_INICIAL', datetime('now'), 'Inicialización obligatoria en $0 (Regla 2.3)', datetime('now'));

-- 3. Tabla de auditoría append-only para trazabilidad de configuración y cambios en aperturas
CREATE TABLE IF NOT EXISTS tesoreria_saldos_iniciales_auditoria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_codigo TEXT NOT NULL,
    item_nombre TEXT NOT NULL,
    categoria TEXT NOT NULL CHECK(categoria IN ('FONDO_FISICO', 'CUENTA_DIGITAL', 'OBLIGACION')),
    saldo_anterior_cents INTEGER NOT NULL,
    saldo_nuevo_cents INTEGER NOT NULL,
    fecha_hora TEXT NOT NULL DEFAULT (datetime('now')),
    usuario_responsable TEXT NOT NULL,
    motivo TEXT NOT NULL CHECK(length(trim(motivo)) > 0),
    referencia_autorizacion TEXT,
    jornada_id INTEGER,
    es_apertura_inicial INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_saldos_audit_codigo ON tesoreria_saldos_iniciales_auditoria(item_codigo);
CREATE INDEX IF NOT EXISTS idx_saldos_audit_fecha ON tesoreria_saldos_iniciales_auditoria(fecha_hora);

-- 4. Registrar la apertura inicial en $0 de todos los fondos y obligaciones en la pista de auditoría
INSERT INTO tesoreria_saldos_iniciales_auditoria (
    item_codigo, item_nombre, categoria, saldo_anterior_cents, saldo_nuevo_cents,
    fecha_hora, usuario_responsable, motivo, es_apertura_inicial
)
SELECT 
    codigo,
    nombre,
    CASE 
        WHEN codigo IN ('1110', '1120', '1130', '1140', '1150', '1160') THEN 'FONDO_FISICO'
        ELSE 'CUENTA_DIGITAL'
    END as categoria,
    0,
    0,
    datetime('now'),
    'SISTEMA_INICIAL',
    'Inicialización oficial de saldos en $0 según directiva MEGA SYSTEM PROMPT',
    1
FROM tesoreria_fondos;

INSERT INTO tesoreria_saldos_iniciales_auditoria (
    item_codigo, item_nombre, categoria, saldo_anterior_cents, saldo_nuevo_cents,
    fecha_hora, usuario_responsable, motivo, es_apertura_inicial
)
SELECT 
    codigo,
    nombre,
    'OBLIGACION',
    0,
    0,
    datetime('now'),
    'SISTEMA_INICIAL',
    'Inicialización oficial de saldos en $0 según directiva MEGA SYSTEM PROMPT',
    1
FROM tesoreria_obligaciones_apertura;
