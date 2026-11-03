-- Migración 001: Baseline Tesorería Laboratorio (LAB-001 a LAB-012)
-- Marca el punto de partida estructural de las tablas de tesorería.

-- Nota: Si las tablas base no existen, se crean. Si ya existen, la migración es no-op.
CREATE TABLE IF NOT EXISTS _tesoreria_schema_migrations (
    version INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL,
    aplicado_en TEXT NOT NULL DEFAULT (datetime('now'))
);
