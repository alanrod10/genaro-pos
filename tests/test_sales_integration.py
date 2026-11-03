"""Pruebas de integración del flujo de ventas (Caja) para el laboratorio Genaro POS.

Verifica de forma estrictamente local y sin red:
1. Selección entre SHEETS y TURSO mediante la variable VENTAS_BACKEND.
2. Invocación única del adaptador Turso con ticket e ítems normalizados.
3. Preservación íntegra del flujo original cuando se selecciona SHEETS.
4. Manejo de errores de validación, fallos de persistencia y no eliminación del carrito ante error.
5. Atomicidad y ausencia de inserciones parciales ante fallos.
6. Lecturas de movimientos e ítems compatibles con el resumen de Caja y cálculo de impresiones.
"""

import os
import sqlite3
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import streamlit as st

from app import (
    PRODUCTOS_IMPRESIONES_RESUMEN,
    calcular_resumen_impresiones,
    cargar_items_impresiones_resumen,
    cargar_movimientos_resumen,
    procesar_venta,
)
from db.sales import (
    BACKEND_SHEETS,
    BACKEND_TURSO,
    COLUMNAS_ITEMS,
    COLUMNAS_MOVIMIENTOS,
    ErrorVentasTurso,
    cargar_items_turso,
    cargar_movimientos_turso,
    obtener_backend_ventas,
    registrar_venta_turso,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_003 = ROOT / "db/migrations/003_ventas_tickets_outbox.sql"
MIGRATION_007 = ROOT / "db/migrations/007_sales_audit.sql"
MIGRATION_009 = ROOT / "db/migrations/009_ticket_item_cost_snapshot.sql"


@pytest.fixture
def conn_memoria():
    """Base de datos SQLite en memoria con esquema de productos y migración 003."""
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
        VALUES
            (10, 'Impresion a color', 1),
            (20, 'Gaseosa Cola', 1),
            (30, 'Producto Inactivo', 0);
    """)
    db.executescript(MIGRATION_003.read_text(encoding="utf-8"))
    db.executescript(MIGRATION_007.read_text(encoding="utf-8"))
    db.executescript(MIGRATION_009.read_text(encoding="utf-8"))
    yield db
    db.close()


@pytest.fixture(autouse=True)
def limpiar_estado_caja():
    """Garantiza estado limpio en st.session_state y cachés de resumen antes de cada test."""
    st.session_state.carrito = []
    st.session_state.search_key = 0
    cargar_movimientos_resumen.clear()
    cargar_items_impresiones_resumen.clear()
    yield
    st.session_state.carrito = []
    cargar_movimientos_resumen.clear()
    cargar_items_impresiones_resumen.clear()


# ==============================================================================
# 1. SELECCIÓN DE BACKEND (SHEETS vs TURSO)
# ==============================================================================

def test_backend_ventas_por_defecto_es_sheets(monkeypatch):
    monkeypatch.delenv("VENTAS_BACKEND", raising=False)
    assert obtener_backend_ventas() == BACKEND_SHEETS


def test_backend_ventas_valores_validos(monkeypatch):
    monkeypatch.setenv("VENTAS_BACKEND", "SHEETS")
    assert obtener_backend_ventas() == BACKEND_SHEETS

    monkeypatch.setenv("VENTAS_BACKEND", "sheets")
    assert obtener_backend_ventas() == BACKEND_SHEETS

    monkeypatch.setenv("VENTAS_BACKEND", "TURSO")
    assert obtener_backend_ventas() == BACKEND_TURSO

    monkeypatch.setenv("VENTAS_BACKEND", "turso")
    assert obtener_backend_ventas() == BACKEND_TURSO


def test_backend_ventas_valor_invalido_lanza_error(monkeypatch):
    monkeypatch.setenv("VENTAS_BACKEND", "POSTGRES")
    with pytest.raises(ErrorVentasTurso, match="VENTAS_BACKEND debe ser SHEETS o TURSO"):
        obtener_backend_ventas()


# ==============================================================================
# 2. PROCESAR VENTA CON BACKEND TURSO
# ==============================================================================

def test_procesar_venta_turso_efectivo_invoca_adaptador_correctamente(monkeypatch):
    monkeypatch.setenv("VENTAS_BACKEND", "TURSO")

    st.session_state.carrito = [
        {"nombre": "Gaseosa Cola", "precio": 1200, "cantidad": 2, "subtotal": 2400},
        {"nombre": "Impresion a color", "precio": 500, "cantidad": 1, "subtotal": 500},
    ]

    with patch("db.sales.registrar_venta_turso") as mock_turso, \
         patch("app.obtener_conexion") as mock_conn:
        mock_turso.return_value = "T-TEST-001"

        resultado = procesar_venta("EFECTIVO")

        assert resultado is True
        assert mock_turso.call_count == 1
        assert mock_conn.call_count == 0

        kwargs = mock_turso.call_args.kwargs
        ticket_arg = kwargs["ticket"]
        items_arg = kwargs["items"]

        # Validaciones de normalización de la cabecera
        assert ticket_arg["TICKET_ID"].startswith("T-")
        assert ticket_arg["TOTAL_VENTA"] == 2900
        assert ticket_arg["MONTO_EFECTIVO"] == 2900
        assert ticket_arg["MONTO_TRANSF"] == 0
        assert ticket_arg["METODO_PAGO"] == "EFECTIVO"
        assert ticket_arg["ES_NOCTURNO"] is False
        assert "FECHA" in ticket_arg

        # Validaciones del detalle de ítems
        assert len(items_arg) == 2
        assert items_arg[0]["PRODUCTO"] == "Gaseosa Cola"
        assert items_arg[0]["CANTIDAD"] == 2
        assert items_arg[0]["PRECIO_UNIT"] == 1200
        assert items_arg[0]["SUBTOTAL"] == 2400
        assert items_arg[0]["METODO_PAGO"] == "EFECTIVO"
        assert items_arg[0]["TICKET_ID"] == ticket_arg["TICKET_ID"]

        assert items_arg[1]["PRODUCTO"] == "Impresion a color"
        assert items_arg[1]["CANTIDAD"] == 1
        assert items_arg[1]["SUBTOTAL"] == 500

        # Carrito vaciado tras éxito
        assert st.session_state.carrito == []


def test_procesar_venta_turso_transferencia_invoca_adaptador_correctamente(monkeypatch):
    monkeypatch.setenv("VENTAS_BACKEND", "TURSO")

    st.session_state.carrito = [
        {"nombre": "Gaseosa Cola", "precio": 1500, "cantidad": 2, "subtotal": 3000},
    ]

    with patch("db.sales.registrar_venta_turso") as mock_turso:
        mock_turso.return_value = "T-TRANSF-1"

        resultado = procesar_venta("TRANSFERENCIA")

        assert resultado is True
        assert mock_turso.call_count == 1
        ticket_arg = mock_turso.call_args.kwargs["ticket"]
        assert ticket_arg["TOTAL_VENTA"] == 3000
        assert ticket_arg["MONTO_EFECTIVO"] == 0
        assert ticket_arg["MONTO_TRANSF"] == 3000
        assert ticket_arg["METODO_PAGO"] == "TRANSFERENCIA"
        assert st.session_state.carrito == []


def test_procesar_venta_turso_mixto_invoca_adaptador_correctamente(monkeypatch):
    monkeypatch.setenv("VENTAS_BACKEND", "TURSO")

    st.session_state.carrito = [
        {"nombre": "Impresion a color", "precio": 1000, "cantidad": 5, "subtotal": 5000},
    ]

    with patch("db.sales.registrar_venta_turso") as mock_turso:
        mock_turso.return_value = "T-MIXTO-1"

        resultado = procesar_venta("MIXTO", monto_efvo=2000, monto_transf=3000)

        assert resultado is True
        assert mock_turso.call_count == 1
        ticket_arg = mock_turso.call_args.kwargs["ticket"]
        assert ticket_arg["TOTAL_VENTA"] == 5000
        assert ticket_arg["MONTO_EFECTIVO"] == 2000
        assert ticket_arg["MONTO_TRANSF"] == 3000
        assert ticket_arg["METODO_PAGO"] == "MIXTO"
        assert st.session_state.carrito == []


# ==============================================================================
# 3. PROCESAR VENTA CON BACKEND SHEETS (FLUJO ORIGINAL PRESERVADO)
# ==============================================================================

def test_procesar_venta_sheets_preserva_flujo_original(monkeypatch):
    monkeypatch.setenv("VENTAS_BACKEND", "SHEETS")

    st.session_state.carrito = [
        {"nombre": "Gaseosa Cola", "precio": 1000, "cantidad": 1, "subtotal": 1000},
    ]

    mock_conn = MagicMock()
    mock_conn.read.side_effect = [
        pd.DataFrame(columns=["TICKET_ID", "TOTAL_VENTA"]),
        pd.DataFrame(columns=["TICKET_ID", "PRODUCTO"]),
    ]

    with patch("app.obtener_conexion", return_value=mock_conn), \
         patch("db.sales.registrar_venta_turso") as mock_turso:

        resultado = procesar_venta("EFECTIVO")

        assert resultado is True
        assert mock_turso.call_count == 0
        assert mock_conn.read.call_count == 2
        assert mock_conn.update.call_count == 2

        # Primera actualización: DB_MOVIMIENTOS_CAJA
        args_mov, kwargs_mov = mock_conn.update.call_args_list[0]
        assert kwargs_mov["worksheet"] == "DB_MOVIMIENTOS_CAJA"
        df_caja_guardado = kwargs_mov["data"]
        assert len(df_caja_guardado) == 1
        assert int(df_caja_guardado.iloc[0]["TOTAL_VENTA"]) == 1000
        assert int(df_caja_guardado.iloc[0]["MONTO_EFECTIVO"]) == 1000

        # Segunda actualización: DB_HISTORIAL_ITEMS
        args_items, kwargs_items = mock_conn.update.call_args_list[1]
        assert kwargs_items["worksheet"] == "DB_HISTORIAL_ITEMS"
        df_items_guardado = kwargs_items["data"]
        assert len(df_items_guardado) == 1
        assert df_items_guardado.iloc[0]["PRODUCTO"] == "Gaseosa Cola"

        assert st.session_state.carrito == []


# ==============================================================================
# 4. VALIDACIONES Y MANEJO DE ERRORES (SIN PÉRDIDA DE DATOS)
# ==============================================================================

def test_procesar_venta_carrito_vacio_retorna_false_sin_escribir(monkeypatch):
    monkeypatch.setenv("VENTAS_BACKEND", "TURSO")
    st.session_state.carrito = []

    with patch("db.sales.registrar_venta_turso") as mock_turso, \
         patch("streamlit.warning") as mock_warn:

        resultado = procesar_venta("EFECTIVO")

        assert resultado is False
        assert mock_turso.call_count == 0
        assert mock_warn.call_count == 1


def test_fallo_en_adaptador_turso_no_vacia_el_carrito(monkeypatch):
    monkeypatch.setenv("VENTAS_BACKEND", "TURSO")

    st.session_state.carrito = [
        {"nombre": "Gaseosa Cola", "precio": 1000, "cantidad": 2, "subtotal": 2000},
    ]

    with patch("db.sales.registrar_venta_turso", side_effect=Exception("Fallo de red Turso")), \
         patch("streamlit.error") as mock_error:

        resultado = procesar_venta("EFECTIVO")

        assert resultado is False
        assert mock_error.call_count == 1
        # El carrito NO debe perderse si el guardado falla
        assert len(st.session_state.carrito) == 1
        assert st.session_state.carrito[0]["subtotal"] == 2000


def test_fallo_en_sheets_no_vacia_el_carrito(monkeypatch):
    monkeypatch.setenv("VENTAS_BACKEND", "SHEETS")

    st.session_state.carrito = [
        {"nombre": "Gaseosa Cola", "precio": 1000, "cantidad": 2, "subtotal": 2000},
    ]

    mock_conn = MagicMock()
    mock_conn.read.side_effect = Exception("Fallo de API Google Sheets")

    with patch("app.obtener_conexion", return_value=mock_conn), \
         patch("streamlit.error") as mock_error:

        resultado = procesar_venta("EFECTIVO")

        assert resultado is False
        assert mock_error.call_count == 1
        assert len(st.session_state.carrito) == 1


def test_registrar_venta_turso_validaciones_de_dominio(conn_memoria):
    ticket_valido = {
        "TICKET_ID": "T-VAL-1",
        "FECHA": "24/10/2026 12:00:00",
        "TOTAL_VENTA": 1000,
        "MONTO_EFECTIVO": 1000,
        "MONTO_TRANSF": 0,
        "METODO_PAGO": "EFECTIVO",
    }
    items_validos = [
        {
            "PRODUCTO": "Gaseosa Cola",
            "CANTIDAD": 1,
            "UNIDAD": "Unidad",
            "PRECIO_UNIT": 1000,
            "SUBTOTAL": 1000,
            "METODO_PAGO": "EFECTIVO",
        }
    ]

    # 1. TICKET_ID faltante
    with pytest.raises(ErrorVentasTurso, match="TICKET_ID es obligatorio"):
        registrar_venta_turso({**ticket_valido, "TICKET_ID": ""}, items_validos, conn=conn_memoria)

    # 2. Método de pago inválido
    with pytest.raises(ErrorVentasTurso, match="Método de pago no permitido"):
        registrar_venta_turso({**ticket_valido, "METODO_PAGO": "CRIPTOMONEDA"}, items_validos, conn=conn_memoria)

    # 3. Discrepancia en total del ticket
    with pytest.raises(ErrorVentasTurso, match="Efectivo más transferencia"):
        registrar_venta_turso({**ticket_valido, "MONTO_EFECTIVO": 800}, items_validos, conn=conn_memoria)

    # 4. Ítems vacíos
    with pytest.raises(ErrorVentasTurso, match="al menos un ítem"):
        registrar_venta_turso(ticket_valido, [], conn=conn_memoria)

    # 5. Ítem sin nombre de producto
    items_sin_nombre = [{**items_validos[0], "PRODUCTO": "   "}]
    with pytest.raises(ErrorVentasTurso, match="nombre de producto"):
        registrar_venta_turso(ticket_valido, items_sin_nombre, conn=conn_memoria)

    # 6. Ítem con cantidad no positiva
    items_cant_invalida = [{**items_validos[0], "CANTIDAD": 0}]
    with pytest.raises(ErrorVentasTurso, match="mayor que cero"):
        registrar_venta_turso(ticket_valido, items_cant_invalida, conn=conn_memoria)

    # 7. Descuadre entre suma de ítems y total de ticket
    items_descuadrados = [{**items_validos[0], "SUBTOTAL": 900}]
    with pytest.raises(ErrorVentasTurso, match="no coinciden con el ticket"):
        registrar_venta_turso(ticket_valido, items_descuadrados, conn=conn_memoria)


# ==============================================================================
# 5. ATOMICIDAD DE PERSISTENCIA Y SIN INSERCIONES PARCIALES
# ==============================================================================

def test_persistencia_turso_atomica_e2e_en_sqlite(conn_memoria):
    """Flujo completo con base local: procesar_venta -> registrar_venta_turso -> SQLite."""
    ticket = {
        "TICKET_ID": "T-E2E-100",
        "FECHA": "24/10/2026 12:30:00",
        "TOTAL_VENTA": 1700,
        "MONTO_EFECTIVO": 1000,
        "MONTO_TRANSF": 700,
        "METODO_PAGO": "MIXTO",
    }
    items = [
        {
            "TICKET_ID": "T-E2E-100",
            "FECHA": "24/10/2026 12:30:00",
            "PRODUCTO": "Impresion a color",
            "CANTIDAD": 1,
            "UNIDAD": "Unidad",
            "PRECIO_UNIT": 1000,
            "SUBTOTAL": 1000,
            "METODO_PAGO": "MIXTO",
        },
        {
            "TICKET_ID": "T-E2E-100",
            "FECHA": "24/10/2026 12:30:00",
            "PRODUCTO": "Gaseosa Cola",
            "CANTIDAD": 1,
            "UNIDAD": "Unidad",
            "PRECIO_UNIT": 700,
            "SUBTOTAL": 700,
            "METODO_PAGO": "MIXTO",
        },
    ]

    id_generado = registrar_venta_turso(ticket, items, conn=conn_memoria)
    assert id_generado == "T-E2E-100"

    # Verificar en tablas SQLite
    fila_ticket = conn_memoria.execute(
        "SELECT id, total_venta, monto_efectivo, monto_transferencia, metodo_pago FROM tickets WHERE id=?",
        ("T-E2E-100",),
    ).fetchone()
    assert fila_ticket == ("T-E2E-100", 1700, 1000, 700, "MIXTO")

    filas_items = conn_memoria.execute(
        "SELECT producto_id, producto_nombre, cantidad, subtotal FROM ticket_items WHERE ticket_id=? ORDER BY id",
        ("T-E2E-100",),
    ).fetchall()
    assert filas_items == [
        (10, "Impresion a color", 1.0, 1000),
        (20, "Gaseosa Cola", 1.0, 700),
    ]


def test_fallo_en_segundo_item_hace_rollback_completo_sin_ticket_huerfano(conn_memoria):
    """Verifica que si un ítem falla al insertarse, se descarta el ticket completo."""
    ticket = {
        "TICKET_ID": "T-ROLLBACK-1",
        "FECHA": "24/10/2026 12:45:00",
        "TOTAL_VENTA": 2000,
        "MONTO_EFECTIVO": 2000,
        "MONTO_TRANSF": 0,
        "METODO_PAGO": "EFECTIVO",
    }
    items = [
        {
            "PRODUCTO": "Gaseosa Cola",
            "CANTIDAD": 1,
            "UNIDAD": "Unidad",
            "PRECIO_UNIT": 1000,
            "SUBTOTAL": 1000,
            "METODO_PAGO": "EFECTIVO",
        },
        {
            "PRODUCTO": "Producto Que Falla",
            "CANTIDAD": 1,
            "UNIDAD": "Unidad",
            "PRECIO_UNIT": 1000,
            "SUBTOTAL": 1000,
            "METODO_PAGO": "EFECTIVO",
        },
    ]

    # Forzar fallo antes de insertar el segundo ítem mediante un trigger local
    conn_memoria.execute("""
        CREATE TRIGGER trigger_falla_sintetica
        BEFORE INSERT ON ticket_items
        WHEN NEW.producto_nombre = 'Producto Que Falla'
        BEGIN
            SELECT RAISE(ABORT, 'Fallo sintético en segundo ítem');
        END;
    """)

    with pytest.raises(sqlite3.IntegrityError, match="Fallo sintético"):
        registrar_venta_turso(ticket, items, conn=conn_memoria)

    # Ninguna fila en tickets ni en ticket_items debe persistir
    total_tickets = conn_memoria.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    total_items = conn_memoria.execute("SELECT COUNT(*) FROM ticket_items").fetchone()[0]
    assert total_tickets == 0
    assert total_items == 0


# ==============================================================================
# 6. FUNCIONES DE LECTURA DE CAJA Y RESÚMENES
# ==============================================================================

def test_cargar_movimientos_turso_formato_y_exclusiones(conn_memoria):
    # Insertar ticket activo y ticket anulado
    conn_memoria.execute("""
        INSERT INTO tickets (id, fecha_hora_utc, total_venta, monto_efectivo, monto_transferencia, metodo_pago, anulado)
        VALUES
            ('T-ACTIVO', '2026-10-24 13:00:00', 3000, 2000, 1000, 'MIXTO', 0),
            ('T-ANULADO', '2026-10-24 13:05:00', 1500, 1500, 0, 'EFECTIVO', 1);
    """)
    conn_memoria.commit()

    df = cargar_movimientos_turso(conn=conn_memoria)

    # Solo el ticket activo debe figurar
    assert len(df) == 1
    assert df.iloc[0]["TICKET_ID"] == "T-ACTIVO"
    assert int(df.iloc[0]["TOTAL_VENTA"]) == 3000
    assert int(df.iloc[0]["MONTO_EFECTIVO"]) == 2000
    assert int(df.iloc[0]["MONTO_TRANSF"]) == 1000
    assert not bool(df.iloc[0]["ES_NOCTURNO"])
    assert list(df.columns) == list(COLUMNAS_MOVIMIENTOS)


def test_cargar_items_turso_formato_y_exclusiones(conn_memoria):
    conn_memoria.execute("""
        INSERT INTO tickets (id, fecha_hora_utc, total_venta, monto_efectivo, monto_transferencia, metodo_pago, anulado)
        VALUES
            ('T-1', '2026-10-24 13:00:00', 1000, 1000, 0, 'EFECTIVO', 0),
            ('T-2', '2026-10-24 13:05:00', 500, 500, 0, 'EFECTIVO', 1);
    """)
    conn_memoria.execute("""
        INSERT INTO ticket_items (ticket_id, producto_id, producto_nombre, cantidad, unidad, precio_unitario, subtotal, metodo_pago)
        VALUES
            ('T-1', 10, 'Impresion a color', 1, 'Unidad', 1000, 1000, 'EFECTIVO'),
            ('T-2', 10, 'Impresion a color', 1, 'Unidad', 500, 500, 'EFECTIVO');
    """)
    conn_memoria.commit()

    df = cargar_items_turso(conn=conn_memoria)

    # Solo el ítem del ticket activo
    assert len(df) == 1
    assert df.iloc[0]["TICKET_ID"] == "T-1"
    assert df.iloc[0]["PRODUCTO"] == "Impresion a color"
    assert int(df.iloc[0]["SUBTOTAL"]) == 1000
    assert list(df.columns)[1:] == list(COLUMNAS_ITEMS)


def test_cargar_movimientos_resumen_switch(monkeypatch):
    df_turso = pd.DataFrame([
        {"TICKET_ID": "T-1", "FECHA": "24/10/2026 10:00:00", "TOTAL_VENTA": 1000, "MONTO_EFECTIVO": 1000, "MONTO_TRANSF": 0, "ES_NOCTURNO": False}
    ])
    df_sheets = pd.DataFrame([
        {"TICKET_ID": "S-1", "FECHA": "24/10/2026 11:00:00", "TOTAL_VENTA": 2000, "MONTO_EFECTIVO": 2000, "MONTO_TRANSF": 0, "ES_NOCTURNO": False}
    ])

    # 1. En TURSO
    monkeypatch.setenv("VENTAS_BACKEND", "TURSO")
    with patch("db.sales.cargar_movimientos_turso", return_value=df_turso) as mock_turso_read, \
         patch("app.obtener_conexion") as mock_conn:
        res_turso = cargar_movimientos_resumen()
        assert mock_turso_read.call_count == 1
        assert mock_conn.call_count == 0
        assert res_turso.iloc[0]["TICKET_ID"] == "T-1"
        assert "FECHA_REAL" in res_turso.columns
        assert isinstance(res_turso.iloc[0]["FECHA_REAL"], pd.Timestamp)

    cargar_movimientos_resumen.clear()

    # 2. En SHEETS
    monkeypatch.setenv("VENTAS_BACKEND", "SHEETS")
    mock_conn = MagicMock()
    mock_conn.read.return_value = df_sheets
    with patch("db.sales.cargar_movimientos_turso") as mock_turso_read, \
         patch("app.obtener_conexion", return_value=mock_conn):
        res_sheets = cargar_movimientos_resumen()
        assert mock_turso_read.call_count == 0
        assert mock_conn.read.call_count == 1
        assert res_sheets.iloc[0]["TICKET_ID"] == "S-1"
        assert "FECHA_REAL" in res_sheets.columns


def test_cargar_items_impresiones_resumen_precalcula_columnas(monkeypatch):
    monkeypatch.setenv("VENTAS_BACKEND", "TURSO")

    df_items = pd.DataFrame([
        {
            "_TURSO_ITEM_ID": 1,
            "TICKET_ID": "T-1",
            "FECHA": "24/10/2026 10:00:00",
            "PRODUCTO": "Impresion a Color",
            "CANTIDAD": 1,
            "UNIDAD": "Unidad",
            "PRECIO_UNIT": 500,
            "SUBTOTAL": 500,
            "METODO_PAGO": "EFECTIVO",
        }
    ])

    with patch("db.sales.cargar_items_turso", return_value=df_items):
        df_res = cargar_items_impresiones_resumen()

        assert "FECHA_REAL" in df_res.columns
        assert "__PRODUCTO_NORMALIZADO" in df_res.columns
        assert "__SUBTOTAL_NUM" in df_res.columns
        assert df_res.iloc[0]["__SUBTOTAL_NUM"] == 500.0


def test_calcular_resumen_impresiones_mixto_y_fechas():
    hoy = date(2026, 10, 24)

    df_movimientos_hoy = pd.DataFrame([
        {
            "TICKET_ID": "T-MIXTO",
            "FECHA": "24/10/2026 10:00:00",
            "TOTAL_VENTA": 4000,
            "MONTO_EFECTIVO": 3000,
            "MONTO_TRANSF": 1000,
            "ES_NOCTURNO": False,
        },
        {
            "TICKET_ID": "T-EFVO",
            "FECHA": "24/10/2026 11:00:00",
            "TOTAL_VENTA": 1000,
            "MONTO_EFECTIVO": 1000,
            "MONTO_TRANSF": 0,
            "ES_NOCTURNO": False,
        },
    ])

    df_items = pd.DataFrame([
        # Ítem de impresión en ticket mixto de hoy (3/4 efvo, 1/4 transf)
        {
            "TICKET_ID": "T-MIXTO",
            "FECHA": "24/10/2026 10:00:00",
            "PRODUCTO": "Impresion a color",
            "SUBTOTAL": 2000,
            "METODO_PAGO": "MIXTO",
        },
        # Ítem de impresión en ticket puramente efectivo de hoy
        {
            "TICKET_ID": "T-EFVO",
            "FECHA": "24/10/2026 11:00:00",
            "PRODUCTO": "Fotocopia comun",
            "SUBTOTAL": 500,
            "METODO_PAGO": "EFECTIVO",
        },
        # Ítem de producto común que NO es impresión (no debe sumarse)
        {
            "TICKET_ID": "T-EFVO",
            "FECHA": "24/10/2026 11:00:00",
            "PRODUCTO": "Gaseosa Cola",
            "SUBTOTAL": 500,
            "METODO_PAGO": "EFECTIVO",
        },
        # Ítem de impresión de AYER (no debe sumarse al resumen de hoy)
        {
            "TICKET_ID": "T-AYER",
            "FECHA": "23/10/2026 10:00:00",
            "PRODUCTO": "Impresion a color",
            "SUBTOTAL": 1000,
            "METODO_PAGO": "EFECTIVO",
        },
    ])

    total, efvo, transf = calcular_resumen_impresiones(
        df_items=df_items,
        df_movimientos_hoy=df_movimientos_hoy,
        fecha_hoy=hoy,
    )

    # Impresión mixta: 2000 * (3000/4000) = 1500 efvo, 500 transf
    # Fotocopia efvo: 500 efvo, 0 transf
    # Total esperado = 2500, efvo = 2000, transf = 500
    assert total == 2500.0
    assert efvo == 2000.0
    assert transf == 500.0
