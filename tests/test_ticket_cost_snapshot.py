"""Pruebas de integridad financiera para el snapshot de costo unitario histórico (Migración 009).

Verifica:
1. Una venta nueva guarda el costo unitario correcto del producto desde el catálogo.
2. El costo histórico permanece congelado cuando cambia el costo actual en productos.
3. El subtotal, total y distribución efvo/transf no cambian por almacenar el costo.
4. Un artículo sin producto inequívoco queda con costo NULL (desconocido, no 0 inventado).
5. Una solicitud idempotente repetida no duplica ni altera el costo congelado.
6. La edición de cantidades conserva el costo unitario histórico.
7. Las anulaciones lógicas mantienen su comportamiento y auditoría.
8. Los tickets antiguos sin costo permanecen identificados con costo_unitario IS NULL.
9. La migración 009 funciona sobre esquema limpio aplicando migraciones en orden.
"""
import sqlite3
from pathlib import Path
import pytest
from db.sales import (
    registrar_venta_turso,
    registrar_venta_idempotente_turso,
    obtener_detalle_ticket_turso,
    editar_item_venta_turso,
    anular_venta_turso,
    ErrorConflictoIdempotencia,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_003 = ROOT / "db/migrations/003_ventas_tickets_outbox.sql"
MIGRATION_007 = ROOT / "db/migrations/007_sales_audit.sql"
MIGRATION_009 = ROOT / "db/migrations/009_ticket_item_cost_snapshot.sql"


@pytest.fixture
def db_conn():
    """Base de datos SQLite en memoria con esquema de catálogo y migraciones 003, 007 y 009."""
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
            nombre TEXT NOT NULL COLLATE NOCASE,
            categoria TEXT DEFAULT 'Bebidas',
            proveedor TEXT DEFAULT 'Distribuidora Central',
            unidad TEXT DEFAULT 'Unidad',
            costo INTEGER DEFAULT 600,
            precio_dia INTEGER DEFAULT 1000,
            precio_noche INTEGER DEFAULT 1200,
            margen_ratio REAL DEFAULT 0.6667,
            fecha_actualizacion TEXT DEFAULT '2026-10-24',
            activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1))
        );

        INSERT INTO _schema_migrations (version, nombre)
        VALUES
            (1, 'baseline_catalogo_productos_existente'),
            (2, 'crear_cargas_servicios');

        INSERT INTO productos (id, nombre, costo, precio_dia, activo)
        VALUES
            (10, 'Gaseosa Cola 500ml', 650, 1200, 1),
            (20, 'Alfajor Chocolate', 300, 600, 1),
            (30, 'Producto Ambiguo', 100, 200, 1),
            (31, 'Producto Ambiguo', 150, 250, 1),
            (40, 'Producto Inactivo', 50, 100, 0);
    """)

    conn.executescript(MIGRATION_003.read_text(encoding="utf-8"))
    conn.executescript(MIGRATION_007.read_text(encoding="utf-8"))
    conn.executescript(MIGRATION_009.read_text(encoding="utf-8"))

    yield conn
    conn.close()


def test_migracion_009_secuencial_agrega_columna_y_registra_version():
    """Verifica que la migración 009 sea puramente aditiva y se aplique secuencialmente."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
        CREATE TABLE _schema_migrations (
            version INTEGER PRIMARY KEY,
            nombre TEXT NOT NULL,
            aplicado_en TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE productos (
            id INTEGER PRIMARY KEY,
            nombre TEXT NOT NULL
        );
    """)

    # Aplicar 003 y verificar que ticket_items NO tiene costo_unitario inicialmente
    conn.executescript(MIGRATION_003.read_text(encoding="utf-8"))
    cols_pre = {r[1] for r in conn.execute("PRAGMA table_info(ticket_items)").fetchall()}
    assert "costo_unitario" not in cols_pre

    # Aplicar 009
    conn.executescript(MIGRATION_009.read_text(encoding="utf-8"))
    cols_post = {r[1] for r in conn.execute("PRAGMA table_info(ticket_items)").fetchall()}
    assert "costo_unitario" in cols_post

    # Verificar registro en _schema_migrations
    v = conn.execute("SELECT version, nombre FROM _schema_migrations WHERE version = 9").fetchone()
    assert v == (9, "ticket_item_cost_snapshot")
    conn.close()


def test_venta_nueva_congela_costo_unitario_correcto(db_conn):
    """1. Una venta nueva guarda el costo unitario correcto del producto."""
    ticket = {
        "TICKET_ID": "T-TEST-001",
        "FECHA": "24/10/2026 12:00:00",
        "TOTAL_VENTA": 1800,
        "MONTO_EFECTIVO": 1800,
        "MONTO_TRANSF": 0,
        "METODO_PAGO": "EFECTIVO",
    }
    items = [
        {
            "PRODUCTO": "Gaseosa Cola 500ml",
            "CANTIDAD": 1,
            "UNIDAD": "Unidad",
            "PRECIO_UNIT": 1200,
            "SUBTOTAL": 1200,
        },
        {
            "PRODUCTO": "Alfajor Chocolate",
            "CANTIDAD": 1,
            "UNIDAD": "Unidad",
            "PRECIO_UNIT": 600,
            "SUBTOTAL": 600,
        },
    ]

    t_id = registrar_venta_turso(ticket, items, conn=db_conn)
    assert t_id == "T-TEST-001"

    detalle = obtener_detalle_ticket_turso("T-TEST-001", conn=db_conn)
    assert detalle is not None
    assert len(detalle["items"]) == 2

    it_gaseosa = next(i for i in detalle["items"] if i["producto_nombre"] == "Gaseosa Cola 500ml")
    it_alfajor = next(i for i in detalle["items"] if i["producto_nombre"] == "Alfajor Chocolate")

    assert it_gaseosa["costo_unitario"] == 650
    assert it_alfajor["costo_unitario"] == 300


def test_costo_historico_permanece_inmutable_tras_cambio_en_catalogo(db_conn):
    """2. Modificar posteriormente el costo en catálogo no altera el costo histórico ya vendido."""
    ticket = {
        "TICKET_ID": "T-TEST-002",
        "FECHA": "24/10/2026 12:30:00",
        "TOTAL_VENTA": 1200,
        "MONTO_EFECTIVO": 1200,
        "MONTO_TRANSF": 0,
        "METODO_PAGO": "EFECTIVO",
    }
    items = [
        {
            "PRODUCTO": "Gaseosa Cola 500ml",
            "CANTIDAD": 1,
            "UNIDAD": "Unidad",
            "PRECIO_UNIT": 1200,
            "SUBTOTAL": 1200,
        }
    ]

    registrar_venta_turso(ticket, items, conn=db_conn)

    # Modificar el costo actual del producto en catálogo de $650 a $900
    db_conn.execute("UPDATE productos SET costo = 900 WHERE id = 10")
    db_conn.commit()

    # El ticket histórico debe conservar exactamente $650
    detalle = obtener_detalle_ticket_turso("T-TEST-002", conn=db_conn)
    assert detalle["items"][0]["costo_unitario"] == 650


def test_subtotal_total_y_metodos_pago_no_se_alteran(db_conn):
    """3. El subtotal, el total del ticket y la distribución efvo/transf no cambian por guardar costo."""
    ticket = {
        "TICKET_ID": "T-TEST-003",
        "FECHA": "24/10/2026 13:00:00",
        "TOTAL_VENTA": 2400,
        "MONTO_EFECTIVO": 1000,
        "MONTO_TRANSF": 1400,
        "METODO_PAGO": "MIXTO",
    }
    items = [
        {
            "PRODUCTO": "Gaseosa Cola 500ml",
            "CANTIDAD": 2,
            "UNIDAD": "Unidad",
            "PRECIO_UNIT": 1200,
            "SUBTOTAL": 2400,
        }
    ]

    registrar_venta_turso(ticket, items, conn=db_conn)

    detalle = obtener_detalle_ticket_turso("T-TEST-003", conn=db_conn)
    assert detalle["total_venta"] == 2400
    assert detalle["monto_efectivo"] == 1000
    assert detalle["monto_transferencia"] == 1400
    assert detalle["metodo_pago"] == "MIXTO"
    assert detalle["items"][0]["subtotal"] == 2400
    assert detalle["items"][0]["costo_unitario"] == 650


def test_articulo_sin_producto_inequivoco_queda_con_costo_null(db_conn):
    """4. Un artículo sin producto inequívoco queda con costo NULL (no con cero inventado)."""
    ticket = {
        "TICKET_ID": "T-TEST-004",
        "FECHA": "24/10/2026 13:30:00",
        "TOTAL_VENTA": 850,
        "MONTO_EFECTIVO": 850,
        "MONTO_TRANSF": 0,
        "METODO_PAGO": "EFECTIVO",
    }
    items = [
        # Inexistente
        {
            "PRODUCTO": "Producto Inexistente",
            "CANTIDAD": 1,
            "UNIDAD": "Unidad",
            "PRECIO_UNIT": 500,
            "SUBTOTAL": 500,
        },
        # Ambiguo (hay dos activos con el mismo nombre)
        {
            "PRODUCTO": "Producto Ambiguo",
            "CANTIDAD": 1,
            "UNIDAD": "Unidad",
            "PRECIO_UNIT": 250,
            "SUBTOTAL": 250,
        },
        # Inactivo
        {
            "PRODUCTO": "Producto Inactivo",
            "CANTIDAD": 1,
            "UNIDAD": "Unidad",
            "PRECIO_UNIT": 100,
            "SUBTOTAL": 100,
        },
    ]

    registrar_venta_turso(ticket, items, conn=db_conn)

    detalle = obtener_detalle_ticket_turso("T-TEST-004", conn=db_conn)
    for it in detalle["items"]:
        # Debe ser estrictamente None / NULL
        assert it["costo_unitario"] is None


def test_idempotencia_no_duplica_ni_altera_costo_congelado(db_conn):
    """5. Una solicitud repetida idéntica no duplica el ticket ni altera el costo congelado."""
    ticket = {
        "TICKET_ID": "T-TEST-005",
        "FECHA": "24/10/2026 14:00:00",
        "TOTAL_VENTA": 1200,
        "MONTO_EFECTIVO": 1200,
        "MONTO_TRANSF": 0,
        "METODO_PAGO": "EFECTIVO",
    }
    items = [
        {
            "PRODUCTO": "Gaseosa Cola 500ml",
            "CANTIDAD": 1,
            "UNIDAD": "Unidad",
            "PRECIO_UNIT": 1200,
            "SUBTOTAL": 1200,
        }
    ]

    t_id1, es_reintento1 = registrar_venta_idempotente_turso(ticket, items, conn=db_conn)
    assert not es_reintento1
    assert t_id1 == "T-TEST-005"

    # Cambiar costo en catálogo
    db_conn.execute("UPDATE productos SET costo = 999 WHERE id = 10")
    db_conn.commit()

    # Repetir exactamente la misma solicitud
    t_id2, es_reintento2 = registrar_venta_idempotente_turso(ticket, items, conn=db_conn)
    assert es_reintento2
    assert t_id2 == "T-TEST-005"

    # Verificar que solo hay 1 fila en ticket_items y conserva 650
    rows = db_conn.execute(
        "SELECT id, costo_unitario FROM ticket_items WHERE ticket_id = 'T-TEST-005'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][1] == 650


def test_edicion_de_cantidad_conserva_costo_unitario_historico(db_conn):
    """6. La edición de cantidades en el historial conserva el costo unitario congelado."""
    ticket = {
        "TICKET_ID": "T-TEST-006",
        "FECHA": "24/10/2026 14:30:00",
        "TOTAL_VENTA": 1200,
        "MONTO_EFECTIVO": 1200,
        "MONTO_TRANSF": 0,
        "METODO_PAGO": "EFECTIVO",
    }
    items = [
        {
            "PRODUCTO": "Gaseosa Cola 500ml",
            "CANTIDAD": 1,
            "UNIDAD": "Unidad",
            "PRECIO_UNIT": 1200,
            "SUBTOTAL": 1200,
        }
    ]

    registrar_venta_turso(ticket, items, conn=db_conn)
    det1 = obtener_detalle_ticket_turso("T-TEST-006", conn=db_conn)
    item_id = det1["items"][0]["id"]
    assert det1["items"][0]["costo_unitario"] == 650

    # Cambiar costo en catálogo antes de editar ítem
    db_conn.execute("UPDATE productos SET costo = 1100 WHERE id = 10")
    db_conn.commit()

    # Editar cantidad de 1 a 2
    det2 = editar_item_venta_turso(
        "T-TEST-006", item_id, nueva_cantidad=2, usuario="alan", conn=db_conn
    )

    assert det2["total_venta"] == 2400
    assert det2["items"][0]["cantidad"] == 2.0
    assert det2["items"][0]["subtotal"] == 2400
    # El costo unitario DEBE seguir siendo 650
    assert det2["items"][0]["costo_unitario"] == 650


def test_anulacion_logica_mantiene_costo_y_auditoria(db_conn):
    """7. La anulación lógica marca el ticket sin borrar ítems ni alterar el costo congelado."""
    ticket = {
        "TICKET_ID": "T-TEST-007",
        "FECHA": "24/10/2026 15:00:00",
        "TOTAL_VENTA": 600,
        "MONTO_EFECTIVO": 600,
        "MONTO_TRANSF": 0,
        "METODO_PAGO": "EFECTIVO",
    }
    items = [
        {
            "PRODUCTO": "Alfajor Chocolate",
            "CANTIDAD": 1,
            "UNIDAD": "Unidad",
            "PRECIO_UNIT": 600,
            "SUBTOTAL": 600,
        }
    ]

    registrar_venta_turso(ticket, items, conn=db_conn)
    anular_venta_turso("T-TEST-007", motivo="Error de cobro", usuario="alan", conn=db_conn)

    det = obtener_detalle_ticket_turso("T-TEST-007", conn=db_conn)
    assert det["anulado"] is True
    assert det["motivo_anulacion"] == "Error de cobro"
    assert det["items"][0]["costo_unitario"] == 300
    assert any(a["accion"] == "ANULACION" for a in det["auditoria"])


def test_tickets_antiguos_sin_costo_quedan_con_costo_null(db_conn):
    """8. Registros insertados previamente a la migración conservan costo NULL."""
    # Simular una venta insertada como en el pasado directo a ticket_items sin costo_unitario
    db_conn.execute(
        """
        INSERT INTO tickets (id, fecha_hora_utc, total_venta, monto_efectivo, monto_transferencia, metodo_pago)
        VALUES ('T-HISTORICO-OLD', '2026-10-20 10:00:00', 1000, 1000, 0, 'EFECTIVO')
        """
    )
    db_conn.execute(
        """
        INSERT INTO ticket_items (ticket_id, producto_id, producto_nombre, cantidad, unidad, precio_unitario, subtotal, metodo_pago, costo_unitario)
        VALUES ('T-HISTORICO-OLD', 10, 'Gaseosa Cola 500ml', 1, 'Unidad', 1000, 1000, 'EFECTIVO', NULL)
        """
    )
    db_conn.commit()

    det = obtener_detalle_ticket_turso("T-HISTORICO-OLD", conn=db_conn)
    assert det["items"][0]["costo_unitario"] is None


def test_snapshot_costo_unitario_cliente_se_conserva_al_sincronizar_offline(db_conn):
    """9. Si el producto cambió de costo entre que se agregó al carrito offline y se sincronizó,
    se preserva el snapshot de costo_unitario capturado en la venta."""
    # El producto tenía costo 650 al agregarse al carrito offline, pero antes de sincronizar subió a 920
    db_conn.execute("UPDATE productos SET costo = 920 WHERE id = 10")
    db_conn.commit()

    ticket = {
        "TICKET_ID": "T-OFFLINE-SNAP-1",
        "FECHA": "24/10/2026 16:00:00",
        "TOTAL_VENTA": 1200,
        "MONTO_EFECTIVO": 1200,
        "MONTO_TRANSF": 0,
        "METODO_PAGO": "EFECTIVO",
    }
    items = [
        {
            "PRODUCTO": "Gaseosa Cola 500ml",
            "CANTIDAD": 1,
            "UNIDAD": "Unidad",
            "PRECIO_UNIT": 1200,
            "SUBTOTAL": 1200,
            "COSTO_UNITARIO": 650,
        }
    ]

    t_id, es_reintento = registrar_venta_idempotente_turso(ticket, items, conn=db_conn)
    assert t_id == "T-OFFLINE-SNAP-1"
    assert es_reintento is False

    det = obtener_detalle_ticket_turso("T-OFFLINE-SNAP-1", conn=db_conn)
    assert det["items"][0]["costo_unitario"] == 650
    assert det["total_venta"] == 1200
