-- Migración 011: Almacén distribuido para prevención anti-replay en autenticación SCRAM-SHA-256.
-- Garantiza consistencia atómica y consumo de un solo uso entre múltiples instancias de Cloudflare Workers.
-- NOTA DE SEGURIDAD: Archivo local preparado para despliegue controlado. No aplicar automáticamente a entornos remotos sin autorización.

BEGIN;

CREATE TABLE IF NOT EXISTS auth_scram_challenges (
    jti TEXT PRIMARY KEY,
    exp INTEGER NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    creado_en_utc TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_scram_challenges_exp
    ON auth_scram_challenges (exp);

CREATE INDEX IF NOT EXISTS idx_scram_challenges_used
    ON auth_scram_challenges (used);

INSERT OR IGNORE INTO _schema_migrations (version, nombre, aplicado_en)
VALUES (11, '011_auth_scram_challenges', datetime('now'));

COMMIT;
