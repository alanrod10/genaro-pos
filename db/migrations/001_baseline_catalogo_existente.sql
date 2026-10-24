-- Baseline para el catálogo ya existente en genaro-pos-dev.
-- No representa la aplicación completa de schema_v1.sql.
BEGIN;

CREATE TABLE IF NOT EXISTS _schema_migrations (
    version INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL,
    aplicado_en TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO _schema_migrations (version, nombre)
VALUES (1, 'baseline_catalogo_productos_existente');

COMMIT;
