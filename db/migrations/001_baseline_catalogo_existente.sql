-- Baseline para el catálogo de productos (Fase 1).
-- Asegura la existencia del catálogo canónico de productos e índices
-- si la base es nueva y vacía, o preserva el catálogo existente si ya existía.
BEGIN;

CREATE TABLE IF NOT EXISTS _schema_migrations (
    version INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL,
    aplicado_en TEXT NOT NULL DEFAULT (datetime('now'))
);

-- CATÁLOGO DE PRODUCTOS CANÓNICO (db/schema_v1.sql)
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

INSERT OR IGNORE INTO _schema_migrations (version, nombre)
VALUES (1, 'baseline_catalogo_productos_existente');

COMMIT;
