-- Migración aditiva 009: Snapshot de costo unitario histórico en ticket_items.
-- Agrega costo_unitario numérico y nullable para congelar el costo de compra
-- al momento exacto de la venta sin alterar registros previos ni esquemas existentes.

BEGIN;

ALTER TABLE ticket_items ADD COLUMN costo_unitario INTEGER;

INSERT OR IGNORE INTO _schema_migrations (version, nombre)
VALUES (9, 'ticket_item_cost_snapshot');

COMMIT;
