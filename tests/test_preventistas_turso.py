"""Pruebas unitarias para el adaptador de persistencia db/preventistas.py.

Verifica la equivalencia con la lógica de app.py (mostrar_preventistas),
la actualización transaccional de lotes de precios con auditoría, el alta rápida
y el ciclo completo de pedidos a proveedores con cálculo y auditoría.
"""

from pathlib import Path
import sqlite3
import pytest

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_004 = ROOT / "db/migrations/004_catalog_audit.sql"
MIGRATION_008 = ROOT / "db/migrations/008_preventistas_y_pedidos.sql"
from db.preventistas import (
    ErrorValidacionPreventistas,
    PedidoNoEncontrado,
    actualizar_precios_lote_preventista_turso,
    alta_rapida_producto_preventista_turso,
    cambiar_estado_pedido_turso,
    crear_pedido_proveedor_turso,
    listar_pedidos_proveedor_turso,
    listar_productos_por_proveedor_turso,
    listar_proveedores_turso,
    obtener_detalle_pedido_proveedor_turso,
)


@pytest.fixture
def db_conn():
    """Conexión SQLite en memoria con esquema de catálogo, auditoría y pedidos."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")

    # Tabla productos baseline
    conn.execute(
        """
        CREATE TABLE productos (
            id INTEGER PRIMARY KEY,
            nombre TEXT NOT NULL,
            categoria TEXT NOT NULL DEFAULT 'Otro',
            proveedor TEXT NOT NULL DEFAULT 'General',
            unidad TEXT NOT NULL DEFAULT 'Unidad',
            costo REAL NOT NULL DEFAULT 0,
            precio_dia REAL NOT NULL DEFAULT 0,
            precio_noche REAL NOT NULL DEFAULT 0,
            margen_ratio REAL NOT NULL DEFAULT 0,
            activo INTEGER NOT NULL DEFAULT 1,
            fecha_actualizacion TEXT
        );
        """
    )

    # Insertar datos de prueba para dos proveedores
    conn.execute(
        """
        INSERT INTO productos (id, nombre, categoria, proveedor, unidad, costo, precio_dia, precio_noche, margen_ratio, activo)
        VALUES
            (1, 'Coca Cola 2.25L', 'Bebidas', 'Coca Cola', 'Unidad', 2000, 3000, 3000, 0.5, 1),
            (2, 'Sprite 2.25L', 'Bebidas', 'Coca Cola', 'Unidad', 1800, 2700, 2700, 0.5, 1),
            (3, 'Quilmes Clásica 1L', 'Cervezas', 'Cervecería Quilmes', 'Unidad', 1500, 2200, 2200, 0.4667, 1),
            (4, 'Stella Artois 1L', 'Cervezas', 'Cervecería Quilmes', 'Unidad', 2200, 3200, 3200, 0.4545, 1);
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS _schema_migrations (
            version INTEGER PRIMARY KEY,
            nombre TEXT NOT NULL,
            aplicado_en TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.executescript(MIGRATION_004.read_text(encoding="utf-8"))
    conn.executescript(MIGRATION_008.read_text(encoding="utf-8"))
    conn.commit()
    yield conn
    conn.close()


def test_listar_proveedores_turso(db_conn):
    """Debe retornar la lista única y ordenada de proveedores."""
    proveedores = listar_proveedores_turso(conn=db_conn)
    assert proveedores == ["Cervecería Quilmes", "Coca Cola"]


def test_listar_productos_por_proveedor_turso(db_conn):
    """Debe listar únicamente los productos del proveedor solicitado con cálculo de margen."""
    prods = listar_productos_por_proveedor_turso("Coca Cola", conn=db_conn)
    assert len(prods) == 2
    assert prods[0]["nombre"] == "Coca Cola 2.25L"
    assert prods[0]["costo"] == 2000.0
    assert prods[0]["precio_dia"] == 3000.0
    assert prods[0]["margen_porcentaje"] == 50.0

    prods_quilmes = listar_productos_por_proveedor_turso("Cervecería Quilmes", conn=db_conn)
    assert len(prods_quilmes) == 2


def test_actualizar_precios_lote_preventista(db_conn):
    """Actualiza selectivamente sólo los productos modificados y genera auditoría."""
    cambios = [
        {"id_producto": 1, "costo": 2200, "precio_venta": 3500},  # Modificado
        {"id_producto": 2, "costo": 1800, "precio_venta": 2700},  # Sin cambios
    ]
    res = actualizar_precios_lote_preventista_turso(
        proveedor="Coca Cola",
        cambios=cambios,
        actor="alan",
        conn=db_conn,
    )
    assert res["actualizados"] == 1

    # Verificar producto 1 actualizado
    p1 = listar_productos_por_proveedor_turso("Coca Cola", conn=db_conn)[0]
    assert p1["costo"] == 2200.0
    assert p1["precio_venta"] == 3500.0
    # Margen: (3500 - 2200) / 2200 = 1300 / 2200 = 59.1%
    assert round(p1["margen_porcentaje"], 1) == 59.1

    # Verificar que el producto de otro proveedor no se alteró
    p3 = listar_productos_por_proveedor_turso("Cervecería Quilmes", conn=db_conn)[0]
    assert p3["costo"] == 1500.0

    # Verificar auditoría
    cursor = db_conn.execute("SELECT producto_id, accion, actor FROM productos_auditoria;")
    auditorias = cursor.fetchall()
    assert len(auditorias) == 1
    assert auditorias[0][0] == 1
    assert auditorias[0][1] == "EDITAR"
    assert auditorias[0][2] == "alan"


def test_alta_rapida_producto_preventista(db_conn):
    """Crea un nuevo producto vinculado al proveedor especificado."""
    datos = {
        "nombre": "Fanta Naranja 2.25L",
        "categoria": "Bebidas",
        "unidad": "Unidad",
        "costo": 1900,
        "precio_venta": 2800,
    }
    nuevo = alta_rapida_producto_preventista_turso(
        proveedor="Coca Cola",
        datos=datos,
        actor="alan",
        conn=db_conn,
    )
    assert nuevo["nombre"] == "Fanta Naranja 2.25L"
    assert nuevo["proveedor"] == "Coca Cola"
    assert nuevo["costo"] == 1900
    assert nuevo["precio_dia"] == 2800

    prods = listar_productos_por_proveedor_turso("Coca Cola", conn=db_conn)
    assert len(prods) == 3


def test_ciclo_completo_pedido_proveedor(db_conn):
    """Crea un pedido con ítems, congela importes y transiciona su estado con auditoría."""
    items = [
        {"producto_id": 1, "producto_nombre": "Coca Cola 2.25L", "cantidad": 10, "costo_unitario": 2000},
        {"producto_id": 2, "producto_nombre": "Sprite 2.25L", "cantidad": 5, "costo_unitario": 1800},
    ]
    # Total esperado: 10 * 2000 + 5 * 1800 = 20000 + 9000 = 29000
    pedido = crear_pedido_proveedor_turso(
        pedido_data={"proveedor": "Coca Cola", "fecha": "2026-10-24", "observaciones": "Entrega jueves mañana"},
        items=items,
        actor="alan",
        conn=db_conn,
    )
    assert pedido["proveedor"] == "Coca Cola"
    assert pedido["total"] == 29000.0
    assert pedido["estado"] == "PENDIENTE"
    assert len(pedido["items"]) == 2

    # Consultar detalle
    detalle = obtener_detalle_pedido_proveedor_turso(pedido["id"], conn=db_conn)
    assert detalle is not None
    assert detalle["id"] == pedido["id"]
    assert len(detalle["items"]) == 2
    assert detalle["items"][0]["subtotal"] == 20000.0
    assert detalle["items"][1]["subtotal"] == 9000.0

    # Listar pedidos con filtro
    lista = listar_pedidos_proveedor_turso(proveedor="Coca Cola", estado="PENDIENTE", conn=db_conn)
    assert len(lista) == 1
    assert lista[0]["id"] == pedido["id"]
    assert lista[0]["items_count"] == 2

    # Cambiar estado a RECIBIDO
    res_estado = cambiar_estado_pedido_turso(
        pedido["id"],
        nuevo_estado="RECIBIDO",
        observaciones="Mercadería controlada y descargada",
        actor="alan",
        conn=db_conn,
    )
    assert res_estado["estado"] == "RECIBIDO"

    # Verificar auditoría del pedido
    cursor = db_conn.execute("SELECT accion, actor FROM pedidos_auditoria WHERE pedido_id = ? ORDER BY id ASC;", (pedido["id"],))
    filas_aud = cursor.fetchall()
    assert len(filas_aud) == 2
    assert filas_aud[0][0] == "CREAR_PEDIDO"
    assert filas_aud[1][0] == "CAMBIAR_ESTADO"


def test_cancelar_pedido_proveedor(db_conn):
    """Permite cancelar un pedido registrando el motivo de cancelación."""
    items = [{"producto_nombre": "Barril Quilmes", "cantidad": 1, "costo_unitario": 50000}]
    pedido = crear_pedido_proveedor_turso(
        pedido_data={"proveedor": "Cervecería Quilmes", "fecha": "2026-10-24"},
        items=items,
        actor="alan",
        conn=db_conn,
    )
    res = cambiar_estado_pedido_turso(
        pedido["id"],
        nuevo_estado="CANCELADO",
        observaciones="Falta de stock en fábrica",
        actor="alan",
        conn=db_conn,
    )
    assert res["estado"] == "CANCELADO"
    assert "Falta de stock en fábrica" in res["observaciones"]


def test_validacion_pedido_sin_items(db_conn):
    """Un pedido sin ítems o con cantidad <= 0 debe ser rechazado."""
    with pytest.raises(ErrorValidacionPreventistas):
        crear_pedido_proveedor_turso(
            pedido_data={"proveedor": "Coca Cola"},
            items=[],
            conn=db_conn,
        )

    with pytest.raises(ErrorValidacionPreventistas):
        crear_pedido_proveedor_turso(
            pedido_data={"proveedor": "Coca Cola"},
            items=[{"producto_nombre": "Item", "cantidad": -1, "costo_unitario": 100}],
            conn=db_conn,
        )
