-- Migración 003: Ampliación de CHECK Constraint en tesoreria_fondos para incorporar fondo 1160 (Prepay Diario)
-- Ejecuta el procedimiento oficial de recreación de tabla en SQLite preservando datos, IDs y relaciones FK.

INSERT OR IGNORE INTO tesoreria_fondos (
    codigo, nombre, naturaleza, titularidad, moneda,
    estado, saldo_centavos, permite_sobregiro, version,
    created_at, updated_at, creado_por, actualizado_por
) VALUES (
    '1160', 'Prepay Diario Efectivo', 'EFECTIVO', 'PROPIA', 'ARS',
    'ACTIVO', 0, 0, 1,
    datetime('now'), datetime('now'), 'MIGRACION_1160', 'MIGRACION_1160'
);

-- Garantizar la existencia de la tabla de auditoría y los triggers de integridad e inmutabilidad de tesoreria_fondos
CREATE TABLE IF NOT EXISTS tesoreria_fondos_auditoria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fondo_id INTEGER NOT NULL,
    codigo TEXT NOT NULL,
    accion TEXT NOT NULL CHECK(accion IN ('INSERT', 'UPDATE', 'DELETE_ATTEMPT', 'BLOQUEO')),
    usuario TEXT NOT NULL,
    valores_previos TEXT,
    valores_nuevos TEXT,
    motivo TEXT,
    timestamp_utc TEXT NOT NULL,
    FOREIGN KEY (fondo_id) REFERENCES tesoreria_fondos(id) ON DELETE RESTRICT
);

CREATE TRIGGER IF NOT EXISTS trg_proteger_borrado_fondos
BEFORE DELETE ON tesoreria_fondos
BEGIN
    SELECT RAISE(ABORT, 'VIOLACIÓN DE INTEGRIDAD: No está permitido eliminar fondos del catálogo de tesorería.');
END;

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
