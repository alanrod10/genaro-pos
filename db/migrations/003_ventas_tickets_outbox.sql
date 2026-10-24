-- Migración aditiva 003: ventas, tickets, ítems y outbox.
-- No modifica productos ni cargas_servicios.

BEGIN;

CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY,
    fecha_hora_utc TEXT NOT NULL DEFAULT (datetime('now')),
    total_venta INTEGER NOT NULL CHECK (total_venta >= 0),
    monto_efectivo INTEGER NOT NULL DEFAULT 0
        CHECK (monto_efectivo >= 0),
    monto_transferencia INTEGER NOT NULL DEFAULT 0
        CHECK (monto_transferencia >= 0),
    metodo_pago TEXT NOT NULL
        CHECK (metodo_pago IN ('EFECTIVO', 'TRANSFERENCIA', 'MIXTO')),
    es_nocturno INTEGER NOT NULL DEFAULT 0
        CHECK (es_nocturno IN (0, 1)),
    anulado INTEGER NOT NULL DEFAULT 0
        CHECK (anulado IN (0, 1)),
    motivo_anulacion TEXT,
    CONSTRAINT chk_ticket_balance CHECK (
        total_venta = monto_efectivo + monto_transferencia
    )
);

CREATE INDEX IF NOT EXISTS idx_tickets_fecha
    ON tickets (fecha_hora_utc);

CREATE INDEX IF NOT EXISTS idx_tickets_anulado
    ON tickets (anulado);

CREATE TABLE IF NOT EXISTS ticket_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id TEXT NOT NULL,
    producto_id INTEGER,
    producto_nombre TEXT NOT NULL,
    cantidad REAL NOT NULL CHECK (cantidad > 0),
    unidad TEXT NOT NULL DEFAULT 'Unidad',
    precio_unitario INTEGER NOT NULL CHECK (precio_unitario >= 0),
    subtotal INTEGER NOT NULL CHECK (subtotal >= 0),
    metodo_pago TEXT NOT NULL,
    FOREIGN KEY (ticket_id)
        REFERENCES tickets (id) ON DELETE CASCADE,
    FOREIGN KEY (producto_id)
        REFERENCES productos (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_items_ticket
    ON ticket_items (ticket_id);

CREATE INDEX IF NOT EXISTS idx_items_producto_nombre
    ON ticket_items (producto_nombre);

CREATE TABLE IF NOT EXISTS outbox_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    operation TEXT NOT NULL
        CHECK (operation IN ('INSERT', 'UPDATE', 'DELETE')),
    payload_json TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'PENDIENTE'
        CHECK (estado IN (
            'PENDIENTE', 'EN_PROCESO', 'SINCRONIZADO', 'ERROR'
        )),
    intentos INTEGER NOT NULL DEFAULT 0,
    ultimo_error TEXT,
    creado_en_utc TEXT NOT NULL DEFAULT (datetime('now')),
    procesado_en_utc TEXT
);

CREATE INDEX IF NOT EXISTS idx_outbox_estado
    ON outbox_events (estado, intentos);

INSERT OR IGNORE INTO _schema_migrations (version, nombre)
VALUES (3, 'crear_ventas_tickets_outbox');

COMMIT;
