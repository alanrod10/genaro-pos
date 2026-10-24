import sqlite3
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/003_ventas_tickets_outbox.sql"


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")

    conn.executescript("""
        CREATE TABLE _schema_migrations (
            version INTEGER PRIMARY KEY,
            nombre TEXT NOT NULL,
            aplicado_en TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL
        );

        INSERT INTO _schema_migrations (version, nombre)
        VALUES
            (1, 'baseline_catalogo_productos_existente'),
            (2, 'crear_cargas_servicios');

        INSERT INTO productos (id, nombre)
        VALUES (10, 'Producto de prueba');
    """)

    conn.executescript(MIGRATION.read_text(encoding="utf-8"))
    yield conn
    conn.close()


def test_migracion_crea_tablas_indices_y_version(db):
    tablas = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"tickets", "ticket_items", "outbox_events"} <= tablas

    migraciones = db.execute(
        "SELECT version, nombre FROM _schema_migrations ORDER BY version"
    ).fetchall()
    assert migraciones[-1] == (3, "crear_ventas_tickets_outbox")

    indices = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
    }
    assert {
        "idx_tickets_fecha",
        "idx_tickets_anulado",
        "idx_items_ticket",
        "idx_items_producto_nombre",
        "idx_outbox_estado",
    } <= indices


def test_migracion_es_reejecutable(db):
    db.executescript(MIGRATION.read_text(encoding="utf-8"))

    assert db.execute(
        "SELECT COUNT(*) FROM _schema_migrations WHERE version=3"
    ).fetchone()[0] == 1


def test_ticket_e_items_se_guardan_con_integridad_referencial(db):
    db.execute("""
        INSERT INTO tickets (
            id, total_venta, monto_efectivo, monto_transferencia,
            metodo_pago
        )
        VALUES ('T-TEST-001', 1500, 1000, 500, 'MIXTO')
    """)

    db.execute("""
        INSERT INTO ticket_items (
            ticket_id, producto_id, producto_nombre, cantidad, unidad,
            precio_unitario, subtotal, metodo_pago
        )
        VALUES (
            'T-TEST-001', 10, 'Producto de prueba', 1, 'Unidad',
            1500, 1500, 'MIXTO'
        )
    """)
    db.commit()

    ticket = db.execute(
        "SELECT total_venta, monto_efectivo, monto_transferencia "
        "FROM tickets WHERE id='T-TEST-001'"
    ).fetchone()
    item = db.execute(
        "SELECT ticket_id, producto_id, subtotal FROM ticket_items"
    ).fetchone()

    assert ticket == (1500, 1000, 500)
    assert item == ("T-TEST-001", 10, 1500)


def test_ticket_rechaza_balance_de_pago_incorrecto(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("""
            INSERT INTO tickets (
                id, total_venta, monto_efectivo, monto_transferencia,
                metodo_pago
            )
            VALUES ('T-INVALIDO', 1500, 1000, 400, 'MIXTO')
        """)


def test_ticket_item_requiere_ticket_existente(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("""
            INSERT INTO ticket_items (
                ticket_id, producto_nombre, cantidad, precio_unitario,
                subtotal, metodo_pago
            )
            VALUES ('T-NO-EXISTE', 'Producto', 1, 100, 100, 'EFECTIVO')
        """)


def test_eliminar_producto_preserva_el_item_historico(db):
    db.execute("""
        INSERT INTO tickets (
            id, total_venta, monto_efectivo, monto_transferencia,
            metodo_pago
        )
        VALUES ('T-TEST-002', 100, 100, 0, 'EFECTIVO')
    """)
    db.execute("""
        INSERT INTO ticket_items (
            ticket_id, producto_id, producto_nombre, cantidad,
            precio_unitario, subtotal, metodo_pago
        )
        VALUES ('T-TEST-002', 10, 'Nombre histórico', 1, 100, 100, 'EFECTIVO')
    """)

    db.execute("DELETE FROM productos WHERE id=10")

    fila = db.execute(
        "SELECT producto_id, producto_nombre FROM ticket_items "
        "WHERE ticket_id='T-TEST-002'"
    ).fetchone()
    assert fila == (None, "Nombre histórico")


def test_outbox_rechaza_operacion_no_permitida(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("""
            INSERT INTO outbox_events (
                aggregate_type, aggregate_id, operation, payload_json
            )
            VALUES ('ticket', 'T-1', 'UPSERT', '{}')
        """)
