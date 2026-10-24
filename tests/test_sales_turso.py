import sqlite3
from pathlib import Path

import pytest

from db.sales import (
    ErrorVentasTurso,
    cargar_items_turso,
    cargar_movimientos_turso,
    registrar_venta_turso,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_003 = ROOT / "db/migrations/003_ventas_tickets_outbox.sql"


@pytest.fixture
def conn():
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript("""
        CREATE TABLE _schema_migrations (
            version INTEGER PRIMARY KEY,
            nombre TEXT NOT NULL,
            aplicado_en TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL COLLATE NOCASE,
            activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1))
        );
        INSERT INTO _schema_migrations (version, nombre)
        VALUES
            (1, 'baseline_catalogo_productos_existente'),
            (2, 'crear_cargas_servicios');
        INSERT INTO productos (id, nombre, activo)
        VALUES (10, 'Producto A', 1), (11, 'Producto Inactivo', 0);
    """)
    db.executescript(MIGRATION_003.read_text(encoding="utf-8"))
    yield db
    db.close()


def ticket(**cambios):
    base = {
        "TICKET_ID": "T-TEST-100",
        "FECHA": "24/10/2026 10:00:00",
        "TOTAL_VENTA": 1500,
        "MONTO_EFECTIVO": 1000,
        "MONTO_TRANSF": 500,
        "METODO_PAGO": "MIXTO",
    }
    base.update(cambios)
    return base


def items():
    return [
        {
            "TICKET_ID": "T-TEST-100",
            "FECHA": "24/10/2026 10:00:00",
            "PRODUCTO": "Producto A",
            "CANTIDAD": 1,
            "UNIDAD": "Unidad",
            "PRECIO_UNIT": 1000,
            "SUBTOTAL": 1000,
            "METODO_PAGO": "MIXTO",
        },
        {
            "TICKET_ID": "T-TEST-100",
            "FECHA": "24/10/2026 10:00:00",
            "PRODUCTO": "Producto sin coincidencia",
            "CANTIDAD": 1,
            "UNIDAD": "Unidad",
            "PRECIO_UNIT": 500,
            "SUBTOTAL": 500,
            "METODO_PAGO": "MIXTO",
        },
    ]


def test_ticket_e_items_se_registran_atomicos(conn):
    assert registrar_venta_turso(ticket(), items(), conn=conn) == "T-TEST-100"

    cabecera = conn.execute(
        """
        SELECT total_venta, monto_efectivo, monto_transferencia,
               metodo_pago
        FROM tickets WHERE id='T-TEST-100'
        """
    ).fetchone()
    detalle = conn.execute(
        """
        SELECT ticket_id, producto_id, producto_nombre, subtotal
        FROM ticket_items ORDER BY id
        """
    ).fetchall()

    assert cabecera == (1500, 1000, 500, "MIXTO")
    assert detalle == [
        ("T-TEST-100", 10, "Producto A", 1000),
        ("T-TEST-100", None, "Producto sin coincidencia", 500),
    ]


def test_lecturas_compatibles_con_columnas_de_la_app(conn):
    registrar_venta_turso(ticket(), items(), conn=conn)

    movimientos = cargar_movimientos_turso(conn=conn)
    detalle = cargar_items_turso(conn=conn)

    assert movimientos.iloc[0]["TICKET_ID"] == "T-TEST-100"
    assert int(movimientos.iloc[0]["TOTAL_VENTA"]) == 1500
    assert list(detalle.columns)[1:] == [
        "TICKET_ID", "FECHA", "PRODUCTO", "CANTIDAD", "UNIDAD",
        "PRECIO_UNIT", "SUBTOTAL", "METODO_PAGO",
    ]
    assert len(detalle) == 2
    assert detalle["FECHA"].notna().all()


def test_indicador_nocturno_legacy_se_ignora(conn):
    registrar_venta_turso(ticket(ES_NOCTURNO=True), items(), conn=conn)

    almacenado = conn.execute(
        "SELECT es_nocturno FROM tickets WHERE id = ?",
        ("T-TEST-100",),
    ).fetchone()[0]
    movimientos = cargar_movimientos_turso(conn=conn)

    assert almacenado == 0
    assert not bool(movimientos.iloc[0]["ES_NOCTURNO"])


def test_balance_invalido_no_guarda_ticket_ni_items(conn):
    with pytest.raises(ErrorVentasTurso, match="Efectivo más transferencia"):
        registrar_venta_turso(
            ticket(MONTO_TRANSF=400),
            items(),
            conn=conn,
        )

    assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM ticket_items").fetchone()[0] == 0


def test_subtotales_de_items_deben_coincidir_con_el_total(conn):
    items_malos = items()
    items_malos[1]["SUBTOTAL"] = 300

    with pytest.raises(ErrorVentasTurso, match="subtotales"):
        registrar_venta_turso(ticket(), items_malos, conn=conn)

    assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 0


def test_ticket_duplicado_no_agrega_items_parciales(conn):
    registrar_venta_turso(ticket(), items(), conn=conn)

    with pytest.raises(sqlite3.IntegrityError):
        registrar_venta_turso(ticket(), items(), conn=conn)

    assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM ticket_items").fetchone()[0] == 2


def test_fallo_de_item_hace_rollback_de_cabecera(conn):
    # Activamos una regla temporal para forzar fallo en el segundo INSERT.
    conn.execute("""
        CREATE TRIGGER rechazar_item_prueba
        BEFORE INSERT ON ticket_items
        WHEN NEW.producto_nombre = 'Producto sin coincidencia'
        BEGIN
            SELECT RAISE(ABORT, 'fallo sintético de ítem');
        END
    """)
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="fallo sintético"):
        registrar_venta_turso(ticket(), items(), conn=conn)

    assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM ticket_items").fetchone()[0] == 0
