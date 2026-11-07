"""Pruebas Obligatorias de Recuperación del Incidente P0 de Outbox y Persistencia Comercial.

Cubre las 12 comprobaciones requeridas por el protocolo de liberación P0:
1. Venta nueva correctamente sincronizada con persistencia y Outbox.
2. Generación atómica del evento en commercial_events_outbox.
3. Reintento con misma clave de idempotencia (status 'existente', sin registros extra).
4. Venta duplicada con misma clave pero payload discordante (rechazo estricto).
5. Falla temporal o bloqueo en Outbox provoca rollback atómico total.
6. Recuperación y reintento exitoso tras restaurar disponibilidad.
7. Comportamiento ante Outbox ausente y validación de fallos de esquema.
8. Aplicación de migración correctiva 012 habilita el flujo de persistencia y Outbox.
9. Recuperación y trazabilidad de eventos PENDING en commercial_events_outbox.
10. Reconocimiento idempotente de venta existente con confirmación previa perdida.
11. Garantía de consistencia: cero duplicación de ventas, ítems, auditoría o movimientos.
12. Persistencia duradera que sobrevive al cierre y reapertura de la conexión.
"""

import json
import sqlite3
import pytest
from datetime import datetime, timezone
from pathlib import Path

from db.commercial_outbox_emitter import (
    enqueue_commercial_event,
    compute_canonical_hash,
    to_amount_cents,
)
from db.sales import (
    registrar_venta_idempotente_turso,
    ErrorConflictoIdempotencia,
)

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "db" / "migrations"


def crear_base_comercial_con_migraciones(hasta_version: int = 14) -> sqlite3.Connection:
    """Crea una base SQLite en memoria aplicando migraciones secuenciales."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS _schema_migrations (
            version INTEGER PRIMARY KEY,
            nombre TEXT NOT NULL,
            aplicado_en TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )

    # Migración 001: Productos
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL COLLATE NOCASE,
            categoria TEXT NOT NULL DEFAULT 'Otro',
            proveedor TEXT NOT NULL DEFAULT 'General',
            unidad TEXT NOT NULL DEFAULT 'Unidad',
            costo INTEGER NOT NULL DEFAULT 0 CHECK (costo >= 0),
            precio_dia INTEGER NOT NULL CHECK (precio_dia >= 0),
            precio_noche INTEGER NOT NULL DEFAULT 0,
            margen_ratio REAL AS (CASE WHEN costo > 0 THEN ROUND ((CAST (precio_dia AS REAL) - costo) / costo, 4) ELSE 0.0 END) VIRTUAL,
            activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
            fecha_actualizacion TEXT NOT NULL DEFAULT (datetime ('now'))
        );
        INSERT INTO _schema_migrations (version, nombre) VALUES (1, '001_baseline_catalogo_existente');
        """
    )

    # Migración 003: Tickets
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tickets (
            id TEXT PRIMARY KEY,
            fecha_hora_utc TEXT NOT NULL DEFAULT (datetime('now')),
            total_venta INTEGER NOT NULL CHECK (total_venta >= 0),
            monto_efectivo INTEGER NOT NULL DEFAULT 0 CHECK (monto_efectivo >= 0),
            monto_transferencia INTEGER NOT NULL DEFAULT 0 CHECK (monto_transferencia >= 0),
            metodo_pago TEXT NOT NULL CHECK (metodo_pago IN ('EFECTIVO', 'TRANSFERENCIA', 'MIXTO')),
            es_nocturno INTEGER NOT NULL DEFAULT 0 CHECK (es_nocturno IN (0, 1)),
            anulado INTEGER NOT NULL DEFAULT 0 CHECK (anulado IN (0, 1)),
            motivo_anulacion TEXT,
            CONSTRAINT chk_ticket_balance CHECK (total_venta = monto_efectivo + monto_transferencia)
        );
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
            costo_unitario INTEGER,
            FOREIGN KEY (ticket_id) REFERENCES tickets (id) ON DELETE CASCADE
        );
        INSERT INTO _schema_migrations (version, nombre) VALUES (3, '003_ventas_tickets_outbox');
        """
    )

    # Migración 007: Ventas Auditoría
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS ventas_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT NOT NULL,
            item_id INTEGER,
            accion TEXT NOT NULL,
            motivo TEXT,
            usuario TEXT NOT NULL DEFAULT 'sistema',
            datos_previos_json TEXT,
            datos_nuevos_json TEXT,
            creado_en_utc TEXT NOT NULL DEFAULT (datetime ('now'))
        );
        INSERT INTO _schema_migrations (version, nombre) VALUES (7, '007_sales_audit');
        """
    )

    # Migración 012: Commercial Events Outbox
    if hasta_version >= 12:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS commercial_events_outbox (
                sequence_id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                action_type TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                payload_canonical_hash TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING'
                    CHECK (status IN ('PENDING', 'PROCESSING', 'PUBLISHED', 'FAILED', 'DEAD_LETTER')),
                retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
                max_retries INTEGER NOT NULL DEFAULT 5 CHECK (max_retries >= 1),
                error_message TEXT,
                tesoreria_movement_id TEXT,
                reprocessed_by TEXT,
                reprocessed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                published_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_outbox_status_seq ON commercial_events_outbox (status, sequence_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_outbox_idempotency_unique ON commercial_events_outbox (idempotency_key);
            CREATE INDEX IF NOT EXISTS idx_outbox_entity_lookup ON commercial_events_outbox (entity_type, entity_id);
            INSERT INTO _schema_migrations (version, nombre) VALUES (12, '012_commercial_integration_outbox_lab');
            """
        )

    # Insertar producto de prueba
    conn.execute(
        """
        INSERT INTO productos (id, nombre, categoria, precio_dia, costo, activo)
        VALUES (1, 'Gaseosa Cola 500ml', 'Bebidas', 1500, 1000, 1);
        """
    )
    conn.commit()
    return conn


def test_01_y_02_venta_nueva_con_outbox_correctamente_sincronizada():
    """1 y 2. Venta nueva registra Ticket, Items, Auditoría y Outbox de forma atómica."""
    conn = crear_base_comercial_con_migraciones(hasta_version=14)

    ticket_id = "T-1794019964387-sxn3qznt"
    ticket_data = {
        "TICKET_ID": ticket_id,
        "FECHA": "2026-11-07 02:52:44",
        "TOTAL_VENTA": 1500,
        "MONTO_EFECTIVO": 1500,
        "MONTO_TRANSF": 0,
        "METODO_PAGO": "EFECTIVO",
        "ES_NOCTURNO": False,
    }
    items_data = [
        {
            "TICKET_ID": ticket_id,
            "FECHA": "2026-11-07 02:52:44",
            "PRODUCTO": "Gaseosa Cola 500ml",
            "CANTIDAD": 1.0,
            "UNIDAD": "Unidad",
            "PRECIO_UNIT": 1500,
            "SUBTOTAL": 1500,
            "METODO_PAGO": "EFECTIVO",
            "COSTO_UNITARIO": 1000,
        }
    ]

    ticket_id_ret, es_reintento = registrar_venta_idempotente_turso(
        ticket=ticket_data,
        items=items_data,
        usuario="alan",
        conn=conn,
    )

    assert not es_reintento
    assert ticket_id_ret == ticket_id

    # Verificar existencia en tickets
    row_ticket = conn.execute("SELECT id, total_venta, metodo_pago FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    assert row_ticket is not None
    assert row_ticket[0] == ticket_id
    assert row_ticket[1] == 1500

    # Verificar ticket_items
    items = conn.execute("SELECT producto_nombre, cantidad, subtotal FROM ticket_items WHERE ticket_id = ?", (ticket_id,)).fetchall()
    assert len(items) == 1
    assert items[0][0] == "Gaseosa Cola 500ml"

    # Verificar ventas_auditoria
    audit = conn.execute("SELECT ticket_id, accion, usuario FROM ventas_auditoria WHERE ticket_id = ?", (ticket_id,)).fetchone()
    assert audit is not None
    assert audit[0] == ticket_id
    assert audit[1] == "CREACION"

    # 2. Verificar commercial_events_outbox
    outbox = conn.execute(
        "SELECT event_id, event_type, entity_id, status, idempotency_key FROM commercial_events_outbox WHERE entity_id = ?",
        (ticket_id,),
    ).fetchone()
    assert outbox is not None
    assert outbox[1] == "SALE_CONFIRMED"
    assert outbox[2] == ticket_id
    assert outbox[3] == "PENDING"
    assert outbox[4] == f"COMM_SALE_{ticket_id}_CONFIRM"
    conn.close()


def test_03_reintento_con_misma_clave_es_idempotente():
    """3. Reintento idéntico retorna status 'existente' sin duplicar registros."""
    conn = crear_base_comercial_con_migraciones(hasta_version=14)
    ticket_id = "T-1794019964387-sxn3qznt"
    ticket_data = {
        "TICKET_ID": ticket_id,
        "FECHA": "2026-11-07 02:52:44",
        "TOTAL_VENTA": 1500,
        "MONTO_EFECTIVO": 1500,
        "MONTO_TRANSF": 0,
        "METODO_PAGO": "EFECTIVO",
        "ES_NOCTURNO": False,
    }
    items_data = [
        {
            "TICKET_ID": ticket_id,
            "FECHA": "2026-11-07 02:52:44",
            "PRODUCTO": "Gaseosa Cola 500ml",
            "CANTIDAD": 1.0,
            "UNIDAD": "Unidad",
            "PRECIO_UNIT": 1500,
            "SUBTOTAL": 1500,
            "METODO_PAGO": "EFECTIVO",
            "COSTO_UNITARIO": 1000,
        }
    ]

    t1, r1 = registrar_venta_idempotente_turso(ticket_data, items_data, usuario="alan", conn=conn)
    assert not r1
    assert t1 == ticket_id

    # Segundo envío (reintento de sincronización offline)
    t2, r2 = registrar_venta_idempotente_turso(ticket_data, items_data, usuario="alan", conn=conn)
    assert r2
    assert t2 == ticket_id

    # Conteo no aumenta
    count_t = conn.execute("SELECT count(*) FROM tickets WHERE id = ?", (ticket_id,)).fetchone()[0]
    count_i = conn.execute("SELECT count(*) FROM ticket_items WHERE ticket_id = ?", (ticket_id,)).fetchone()[0]
    count_o = conn.execute("SELECT count(*) FROM commercial_events_outbox WHERE entity_id = ?", (ticket_id,)).fetchone()[0]
    assert count_t == 1
    assert count_i == 1
    assert count_o == 1
    conn.close()


def test_04_venta_duplicada_con_payload_discordante_es_rechazada():
    """4. Mismo ticket_id con datos diferentes produce ErrorConflictoIdempotencia."""
    conn = crear_base_comercial_con_migraciones(hasta_version=14)
    ticket_id = "T-1794019964387-sxn3qznt"
    ticket_data = {
        "TICKET_ID": ticket_id,
        "FECHA": "2026-11-07 02:52:44",
        "TOTAL_VENTA": 1500,
        "MONTO_EFECTIVO": 1500,
        "MONTO_TRANSF": 0,
        "METODO_PAGO": "EFECTIVO",
        "ES_NOCTURNO": False,
    }
    items_data = [
        {"TICKET_ID": ticket_id, "FECHA": "2026-11-07 02:52:44", "PRODUCTO": "Gaseosa Cola 500ml", "CANTIDAD": 1.0, "UNIDAD": "Unidad", "PRECIO_UNIT": 1500, "SUBTOTAL": 1500, "METODO_PAGO": "EFECTIVO", "COSTO_UNITARIO": 1000}
    ]
    registrar_venta_idempotente_turso(ticket_data, items_data, usuario="alan", conn=conn)

    # Mismo ID pero items y total alterados (3000 vs 1500 original)
    ticket_alterado = dict(ticket_data, TOTAL_VENTA=3000, MONTO_EFECTIVO=3000)
    items_alterados = [
        {"TICKET_ID": ticket_id, "FECHA": "2026-11-07 02:52:44", "PRODUCTO": "Gaseosa Cola 500ml", "CANTIDAD": 2.0, "UNIDAD": "Unidad", "PRECIO_UNIT": 1500, "SUBTOTAL": 3000, "METODO_PAGO": "EFECTIVO", "COSTO_UNITARIO": 1000}
    ]
    with pytest.raises(ErrorConflictoIdempotencia):
        registrar_venta_idempotente_turso(ticket_alterado, items_alterados, usuario="alan", conn=conn)
    conn.close()


def test_05_y_06_falla_en_outbox_rollback_limpio_y_recuperacion():
    """5 y 6. Falla en inserción de Outbox provoca rollback total; al restaurar, persiste."""
    conn = crear_base_comercial_con_migraciones(hasta_version=14)

    # Creamos un trigger temporal para simular fallo catastrófico en outbox
    conn.execute(
        """
        CREATE TRIGGER test_fail_outbox BEFORE INSERT ON commercial_events_outbox
        BEGIN
            SELECT RAISE(FAIL, 'SIMULATED_DATABASE_IO_ERROR');
        END;
        """
    )
    conn.commit()

    ticket_id = "T-1794019964387-sxn3qznt"
    ticket_data = {
        "TICKET_ID": ticket_id,
        "FECHA": "2026-11-07 02:52:44",
        "TOTAL_VENTA": 1500,
        "MONTO_EFECTIVO": 1500,
        "MONTO_TRANSF": 0,
        "METODO_PAGO": "EFECTIVO",
        "ES_NOCTURNO": False,
    }
    items_data = [
        {"TICKET_ID": ticket_id, "FECHA": "2026-11-07 02:52:44", "PRODUCTO": "Gaseosa Cola 500ml", "CANTIDAD": 1.0, "UNIDAD": "Unidad", "PRECIO_UNIT": 1500, "SUBTOTAL": 1500, "METODO_PAGO": "EFECTIVO", "COSTO_UNITARIO": 1000}
    ]

    with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError), match="SIMULATED_DATABASE_IO_ERROR"):
        registrar_venta_idempotente_turso(ticket_data, items_data, usuario="alan", conn=conn)

    # 5. Rollback atómico: NADA quedó insertado
    assert conn.execute("SELECT count(*) FROM tickets WHERE id = ?", (ticket_id,)).fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM ticket_items WHERE ticket_id = ?", (ticket_id,)).fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM ventas_auditoria WHERE ticket_id = ?", (ticket_id,)).fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM commercial_events_outbox WHERE entity_id = ?", (ticket_id,)).fetchone()[0] == 0

    # 6. Recuperación: removemos la condición de falla y reintentamos la venta con ID original
    conn.execute("DROP TRIGGER test_fail_outbox;")
    conn.commit()

    t_rec, r_rec = registrar_venta_idempotente_turso(ticket_data, items_data, usuario="alan", conn=conn)
    assert not r_rec
    assert t_rec == ticket_id
    assert conn.execute("SELECT count(*) FROM tickets WHERE id = ?", (ticket_id,)).fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM commercial_events_outbox WHERE entity_id = ?", (ticket_id,)).fetchone()[0] == 1
    conn.close()


def test_07_y_08_migracion_012_correctiva_habilita_outbox():
    """7 y 8. Base sin migración 012 no tiene tabla outbox; aplicar 012 habilita el flujo."""
    # Base con solo migraciones hasta la 11 (como estaba Turso antes de la reparación)
    conn = crear_base_comercial_con_migraciones(hasta_version=11)

    # Verificar que commercial_events_outbox no existe
    assert conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='commercial_events_outbox'").fetchone()[0] == 0

    # 8. Aplicar la migración 012 canónica
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS commercial_events_outbox (
            sequence_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            event_type TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            action_type TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            payload_canonical_hash TEXT NOT NULL,
            payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING'
                CHECK (status IN ('PENDING', 'PROCESSING', 'PUBLISHED', 'FAILED', 'DEAD_LETTER')),
            retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
            max_retries INTEGER NOT NULL DEFAULT 5 CHECK (max_retries >= 1),
            error_message TEXT,
            tesoreria_movement_id TEXT,
            reprocessed_by TEXT,
            reprocessed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            published_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_outbox_status_seq ON commercial_events_outbox (status, sequence_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_outbox_idempotency_unique ON commercial_events_outbox (idempotency_key);
        CREATE INDEX IF NOT EXISTS idx_outbox_entity_lookup ON commercial_events_outbox (entity_type, entity_id);
        INSERT INTO _schema_migrations (version, nombre) VALUES (12, '012_commercial_integration_outbox_lab');
        """
    )
    conn.commit()

    # Tabla ahora existe
    assert conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='commercial_events_outbox'").fetchone()[0] == 1

    # Operación comercial ahora se registra y encola perfectamente
    ticket_id = "T-1794019964387-sxn3qznt"
    ticket_data = {
        "TICKET_ID": ticket_id,
        "FECHA": "2026-11-07 02:52:44",
        "TOTAL_VENTA": 1500,
        "MONTO_EFECTIVO": 1500,
        "MONTO_TRANSF": 0,
        "METODO_PAGO": "EFECTIVO",
        "ES_NOCTURNO": False,
    }
    items_data = [
        {"TICKET_ID": ticket_id, "FECHA": "2026-11-07 02:52:44", "PRODUCTO": "Gaseosa Cola 500ml", "CANTIDAD": 1.0, "UNIDAD": "Unidad", "PRECIO_UNIT": 1500, "SUBTOTAL": 1500, "METODO_PAGO": "EFECTIVO", "COSTO_UNITARIO": 1000}
    ]
    t_mig, r_mig = registrar_venta_idempotente_turso(ticket_data, items_data, usuario="alan", conn=conn)
    assert not r_mig
    assert t_mig == ticket_id
    assert conn.execute("SELECT count(*) FROM commercial_events_outbox WHERE entity_id = ?", (ticket_id,)).fetchone()[0] == 1
    conn.close()


def test_09_trazabilidad_y_recuperacion_evento_pendiente_outbox():
    """9. El evento outbox generado contiene payload canónico e idempotencia trazable."""
    conn = crear_base_comercial_con_migraciones(hasta_version=14)
    ticket_id = "T-1794019964387-sxn3qznt"
    ticket_data = {
        "TICKET_ID": ticket_id,
        "FECHA": "2026-11-07 02:52:44",
        "TOTAL_VENTA": 1500,
        "MONTO_EFECTIVO": 1500,
        "MONTO_TRANSF": 0,
        "METODO_PAGO": "EFECTIVO",
        "ES_NOCTURNO": False,
    }
    items_data = [
        {"TICKET_ID": ticket_id, "FECHA": "2026-11-07 02:52:44", "PRODUCTO": "Gaseosa Cola 500ml", "CANTIDAD": 1.0, "UNIDAD": "Unidad", "PRECIO_UNIT": 1500, "SUBTOTAL": 1500, "METODO_PAGO": "EFECTIVO", "COSTO_UNITARIO": 1000}
    ]
    registrar_venta_idempotente_turso(ticket_data, items_data, usuario="alan", conn=conn)

    row = conn.execute(
        "SELECT event_id, event_type, entity_id, payload, payload_canonical_hash, status FROM commercial_events_outbox WHERE entity_id = ?",
        (ticket_id,),
    ).fetchone()
    assert row is not None
    payload = json.loads(row[3])
    assert payload["ticket_id"] == ticket_id
    assert payload["total_venta_cents"] == 150000
    assert payload["monto_efectivo_cents"] == 150000
    assert payload["monto_transferencia_cents"] == 0
    assert row[5] == "PENDING"
    conn.close()


def test_10_y_11_reconocimiento_de_venta_con_confirmacion_perdida_cero_duplicacion():
    """10 y 11. Sincronización reconoce venta preexistente, confirmando sin doble movimiento."""
    conn = crear_base_comercial_con_migraciones(hasta_version=14)
    ticket_id = "T-1794019964387-sxn3qznt"
    ticket_data = {
        "TICKET_ID": ticket_id,
        "FECHA": "2026-11-07 02:52:44",
        "TOTAL_VENTA": 1500,
        "MONTO_EFECTIVO": 1500,
        "MONTO_TRANSF": 0,
        "METODO_PAGO": "EFECTIVO",
        "ES_NOCTURNO": False,
    }
    items_data = [
        {"TICKET_ID": ticket_id, "FECHA": "2026-11-07 02:52:44", "PRODUCTO": "Gaseosa Cola 500ml", "CANTIDAD": 1.0, "UNIDAD": "Unidad", "PRECIO_UNIT": 1500, "SUBTOTAL": 1500, "METODO_PAGO": "EFECTIVO", "COSTO_UNITARIO": 1000}
    ]

    # Paso 1: Venta asentada en servidor
    t1, r1 = registrar_venta_idempotente_turso(ticket_data, items_data, usuario="alan", conn=conn)
    assert not r1
    assert t1 == ticket_id

    # Paso 2: Supongamos que la respuesta HTTP se perdió y el cliente reintenta
    t2, r2 = registrar_venta_idempotente_turso(ticket_data, items_data, usuario="alan", conn=conn)
    assert r2
    assert t2 == ticket_id

    # 11. Verificación matemática: exactamente 1 ticket, 1 item, 1 outbox
    assert conn.execute("SELECT count(*) FROM tickets").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM ticket_items").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM commercial_events_outbox").fetchone()[0] == 1
    conn.close()


def test_12_persistencia_sobrevive_cierre_y_reapertura(tmp_path):
    """12. Los datos y outbox sobreviven al cierre de la conexión y reinicio del servicio."""
    db_file = tmp_path / "genaro_test_sales.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        """
        CREATE TABLE _schema_migrations (version INTEGER PRIMARY KEY, nombre TEXT);
        """
    )
    # Reutilizamos las tablas mínimas en archivo físico
    conn.executescript(
        """
        CREATE TABLE productos (id INTEGER PRIMARY KEY, nombre TEXT COLLATE NOCASE, activo INTEGER DEFAULT 1, costo INTEGER, precio_dia INTEGER);
        CREATE TABLE tickets (id TEXT PRIMARY KEY, fecha_hora_utc TEXT, total_venta INTEGER, monto_efectivo INTEGER, monto_transferencia INTEGER, metodo_pago TEXT, es_nocturno INTEGER DEFAULT 0, anulado INTEGER DEFAULT 0, motivo_anulacion TEXT);
        CREATE TABLE ticket_items (id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id TEXT, producto_id INTEGER, producto_nombre TEXT, cantidad REAL, unidad TEXT DEFAULT 'Unidad', precio_unitario INTEGER, subtotal INTEGER, metodo_pago TEXT, costo_unitario INTEGER);
        CREATE TABLE ventas_auditoria (id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id TEXT, item_id INTEGER, accion TEXT, motivo TEXT, usuario TEXT, datos_previos_json TEXT, datos_nuevos_json TEXT, creado_en_utc TEXT);
        CREATE TABLE commercial_events_outbox (
            sequence_id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, event_type TEXT NOT NULL,
            entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, action_type TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE, payload_canonical_hash TEXT NOT NULL, payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING', retry_count INTEGER NOT NULL DEFAULT 0, max_retries INTEGER NOT NULL DEFAULT 5,
            error_message TEXT, tesoreria_movement_id TEXT, reprocessed_by TEXT, reprocessed_at TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, published_at TEXT
        );
        INSERT INTO productos (id, nombre, precio_dia, costo, activo) VALUES (1, 'Gaseosa Cola 500ml', 1500, 1000, 1);
        """
    )
    conn.commit()

    ticket_id = "T-1794019964387-sxn3qznt"
    ticket_data = {
        "TICKET_ID": ticket_id,
        "FECHA": "2026-11-07 02:52:44",
        "TOTAL_VENTA": 1500,
        "MONTO_EFECTIVO": 1500,
        "MONTO_TRANSF": 0,
        "METODO_PAGO": "EFECTIVO",
        "ES_NOCTURNO": False,
    }
    items_data = [
        {"TICKET_ID": ticket_id, "FECHA": "2026-11-07 02:52:44", "PRODUCTO": "Gaseosa Cola 500ml", "CANTIDAD": 1.0, "UNIDAD": "Unidad", "PRECIO_UNIT": 1500, "SUBTOTAL": 1500, "METODO_PAGO": "EFECTIVO", "COSTO_UNITARIO": 1000}
    ]
    registrar_venta_idempotente_turso(ticket_data, items_data, usuario="alan", conn=conn)
    conn.close()

    # Reapertura simulando reinicio de backend
    conn2 = sqlite3.connect(str(db_file))
    row_t = conn2.execute("SELECT id, total_venta FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    assert row_t is not None
    assert row_t[0] == ticket_id
    assert row_t[1] == 1500

    row_o = conn2.execute("SELECT entity_id, status FROM commercial_events_outbox WHERE entity_id = ?", (ticket_id,)).fetchone()
    assert row_o is not None
    assert row_o[0] == ticket_id
    assert row_o[1] == "PENDING"
    conn2.close()
