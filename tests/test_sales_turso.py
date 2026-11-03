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
MIGRATION_007 = ROOT / "db/migrations/007_sales_audit.sql"
MIGRATION_009 = ROOT / "db/migrations/009_ticket_item_cost_snapshot.sql"


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
            costo INTEGER DEFAULT 0,
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
    db.executescript(MIGRATION_007.read_text(encoding="utf-8"))
    db.executescript(MIGRATION_009.read_text(encoding="utf-8"))
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


def test_ticket_con_un_solo_item(conn):
    t = ticket(TICKET_ID="T-SOLO-1", TOTAL_VENTA=1000, MONTO_EFECTIVO=1000, MONTO_TRANSF=0, METODO_PAGO="EFECTIVO")
    it = [{
        "TICKET_ID": "T-SOLO-1",
        "FECHA": "24/10/2026 10:00:00",
        "PRODUCTO": "Producto A",
        "CANTIDAD": 1,
        "UNIDAD": "Unidad",
        "PRECIO_UNIT": 1000,
        "SUBTOTAL": 1000,
        "METODO_PAGO": "EFECTIVO",
    }]
    assert registrar_venta_turso(t, it, conn=conn) == "T-SOLO-1"

    cabecera = conn.execute("SELECT total_venta, metodo_pago FROM tickets WHERE id='T-SOLO-1'").fetchone()
    items_bd = conn.execute("SELECT producto_id, producto_nombre, subtotal FROM ticket_items WHERE ticket_id='T-SOLO-1'").fetchall()

    assert cabecera == (1000, "EFECTIVO")
    assert items_bd == [(10, "Producto A", 1000)]


def test_ticket_con_varios_items_batch(conn):
    # Agregar productos para probar duplicados/ambigüedad
    conn.execute("INSERT INTO productos (id, nombre, activo) VALUES (20, 'Producto Ambiguo', 1), (21, 'Producto Ambiguo', 1)")
    conn.commit()

    t = ticket(TICKET_ID="T-MULTI-1", TOTAL_VENTA=3500, MONTO_EFECTIVO=1500, MONTO_TRANSF=2000, METODO_PAGO="MIXTO")
    items_multi = [
        {"TICKET_ID": "T-MULTI-1", "FECHA": "24/10/2026 10:00:00", "PRODUCTO": "Producto A", "CANTIDAD": 1, "UNIDAD": "Unidad", "PRECIO_UNIT": 1000, "SUBTOTAL": 1000, "METODO_PAGO": "MIXTO"},
        {"TICKET_ID": "T-MULTI-1", "FECHA": "24/10/2026 10:00:00", "PRODUCTO": "Producto Inactivo", "CANTIDAD": 1, "UNIDAD": "Unidad", "PRECIO_UNIT": 500, "SUBTOTAL": 500, "METODO_PAGO": "MIXTO"},
        {"TICKET_ID": "T-MULTI-1", "FECHA": "24/10/2026 10:00:00", "PRODUCTO": "Producto Ambiguo", "CANTIDAD": 1, "UNIDAD": "Unidad", "PRECIO_UNIT": 800, "SUBTOTAL": 800, "METODO_PAGO": "MIXTO"},
        {"TICKET_ID": "T-MULTI-1", "FECHA": "24/10/2026 10:00:00", "PRODUCTO": "Producto Inexistente", "CANTIDAD": 2, "UNIDAD": "Unidad", "PRECIO_UNIT": 600, "SUBTOTAL": 1200, "METODO_PAGO": "MIXTO"},
    ]

    assert registrar_venta_turso(t, items_multi, conn=conn) == "T-MULTI-1"

    items_bd = conn.execute(
        "SELECT producto_id, producto_nombre, subtotal FROM ticket_items WHERE ticket_id='T-MULTI-1' ORDER BY id"
    ).fetchall()

    assert items_bd == [
        (10, "Producto A", 1000),       # Unico activo -> asigna 10
        (None, "Producto Inactivo", 500), # Inactivo -> None
        (None, "Producto Ambiguo", 800),  # Duplicado activo -> None
        (None, "Producto Inexistente", 1200), # Inexistente -> None
    ]


def test_lote_mantiene_numero_de_operaciones_execute_constante(conn):
    """Verifica que para N items, el número de sentencias execute sobre la conexión es O(1)."""
    class ConnSpy:
        def __init__(self, real_conn):
            self._conn = real_conn
            self.statements = []

        def execute(self, sql, *args, **kwargs):
            self.statements.append(sql.strip())
            return self._conn.execute(sql, *args, **kwargs)

        def commit(self):
            return self._conn.commit()

        def rollback(self):
            return self._conn.rollback()

        def close(self):
            return self._conn.close()

        def __getattr__(self, name):
            return getattr(self._conn, name)

    spy = ConnSpy(conn)

    t = ticket(TICKET_ID="T-SPY-1", TOTAL_VENTA=5000, MONTO_EFECTIVO=5000, MONTO_TRANSF=0, METODO_PAGO="EFECTIVO")
    items_5 = [
        {"TICKET_ID": "T-SPY-1", "FECHA": "24/10/2026 10:00:00", "PRODUCTO": "Producto A", "CANTIDAD": 1, "UNIDAD": "Unidad", "PRECIO_UNIT": 1000, "SUBTOTAL": 1000, "METODO_PAGO": "EFECTIVO"}
        for _ in range(5)
    ]

    registrar_venta_turso(t, items_5, conn=spy)

    # Debe ejecutar exactamente 5 sentencias execute (O(1) independiente de la cantidad de items):
    # 1: BEGIN IMMEDIATE
    # 2: INSERT INTO tickets
    # 3: SELECT LOWER(nombre), COUNT(*)... (resolución en lote)
    # 4: INSERT INTO ticket_items ... VALUES (...), (...), ... (inserción en lote)
    # 5: INSERT INTO ventas_auditoria ... (auditoría de creación)
    assert len(spy.statements) == 5
    assert spy.statements[0].startswith("BEGIN IMMEDIATE")
    assert spy.statements[1].startswith("INSERT INTO tickets")
    assert "FROM productos" in spy.statements[2]
    assert spy.statements[3].startswith("INSERT INTO ticket_items")
