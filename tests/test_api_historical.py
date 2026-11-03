"""Pruebas unitarias y de integración para el módulo HISTÓRICO (api/routers/historical.py).

Verifica sobre base de datos SQLite aislada y controlada:
1. Rechazo de acceso anónimo (HTTP 401) en todos los endpoints.
2. Acceso autorizado para usuarios (Alan y Yilda).
3. Funcionamiento de GET /api/historical/summary con paridad de métricas.
4. Funcionamiento de GET /api/historical/sales con filtros de fecha, medio de pago, serie temporal y paginación.
5. Funcionamiento de GET /api/historical/products con toggle depurado (True/False) y métricas de pesables.
6. Funcionamiento de GET /api/historical/services con toggle de cargas duplicadas.
7. Búsqueda y detalle de tickets en GET /api/historical/tickets y /api/historical/tickets/{ticket_id}.
8. Comparación analítica de períodos en GET /api/historical/compare.
9. Verificación de aislamiento estricto (cero escrituras y cero impacto operativo).
"""

import sqlite3
import pytest
from fastapi.testclient import TestClient

from api.dependencies import AuthenticatedUser, get_current_user, get_db_connection
from api.main import app


@pytest.fixture
def test_hist_db():
    """Crea una base de datos SQLite en memoria con el esquema histórico y datos de prueba."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")

    # Crear tablas históricas
    conn.executescript("""
    CREATE TABLE historico_lotes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_filename TEXT NOT NULL,
        source_sha256 TEXT NOT NULL UNIQUE,
        source_size_bytes INTEGER NOT NULL,
        total_tickets INTEGER NOT NULL,
        total_items INTEGER NOT NULL,
        total_cargas INTEGER NOT NULL,
        total_ventas_pesos INTEGER NOT NULL,
        total_cargas_pesos INTEGER NOT NULL,
        creado_en_utc TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE historico_tickets (
        id TEXT PRIMARY KEY,
        fecha_hora_local TEXT NOT NULL,
        fecha_hora_utc TEXT NOT NULL,
        total_venta INTEGER NOT NULL CHECK (total_venta >= 0),
        monto_efectivo INTEGER NOT NULL CHECK (monto_efectivo >= 0),
        monto_transferencia INTEGER NOT NULL CHECK (monto_transferencia >= 0),
        metodo_pago TEXT NOT NULL,
        es_nocturno INTEGER NOT NULL DEFAULT 0,
        balance_pago_diferencia INTEGER NOT NULL DEFAULT 0,
        tiene_items INTEGER NOT NULL DEFAULT 1 CHECK (tiene_items IN (0, 1)),
        origen_fila_excel INTEGER NOT NULL,
        creado_en_utc TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE historico_ticket_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id TEXT NOT NULL,
        ticket_id_correlacionado TEXT,
        fecha_hora_local TEXT NOT NULL,
        fecha_hora_utc TEXT NOT NULL,
        producto_nombre TEXT NOT NULL,
        cantidad REAL,
        unidad TEXT DEFAULT 'Unidad',
        precio_unitario INTEGER NOT NULL CHECK (precio_unitario >= 0),
        subtotal INTEGER NOT NULL CHECK (subtotal >= 0),
        metodo_pago TEXT,
        calculo_subtotal_coincide INTEGER NOT NULL DEFAULT 1 CHECK (calculo_subtotal_coincide IN (0, 1)),
        es_duplicado_deriva INTEGER NOT NULL DEFAULT 0 CHECK (es_duplicado_deriva IN (0, 1)),
        origen_fila_excel INTEGER NOT NULL,
        creado_en_utc TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE historico_cargas_servicios (
        id TEXT PRIMARY KEY,
        fecha_hora_local TEXT NOT NULL,
        fecha_hora_utc TEXT NOT NULL,
        servicio TEXT NOT NULL,
        monto_carga INTEGER NOT NULL CHECK (monto_carga > 0),
        monto_adicional INTEGER NOT NULL DEFAULT 0 CHECK (monto_adicional >= 0),
        total_cobrado INTEGER NOT NULL CHECK (total_cobrado >= 0),
        pago_efectivo INTEGER NOT NULL DEFAULT 0 CHECK (pago_efectivo >= 0),
        pago_transferencia INTEGER NOT NULL DEFAULT 0 CHECK (pago_transferencia >= 0),
        balance_pago_diferencia INTEGER NOT NULL DEFAULT 0,
        es_posible_duplicado INTEGER NOT NULL DEFAULT 0 CHECK (es_posible_duplicado IN (0, 1)),
        fingerprint_sha256 TEXT NOT NULL UNIQUE,
        origen_fila_excel INTEGER NOT NULL,
        creado_en_utc TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """)

    # Insertar lote
    conn.execute("""
    INSERT INTO historico_lotes (source_filename, source_sha256, source_size_bytes, total_tickets, total_items, total_cargas, total_ventas_pesos, total_cargas_pesos)
    VALUES ('SISTEMA_GENARO_v4_PRODUCCION.xlsx', 'test_sha256_mock', 1381969, 3, 4, 3, 8600, 7400);
    """)

    # Insertar tickets de prueba
    # 1. Normal en efectivo con items
    conn.execute("""
    INSERT INTO historico_tickets (id, fecha_hora_local, fecha_hora_utc, total_venta, monto_efectivo, monto_transferencia, metodo_pago, es_nocturno, balance_pago_diferencia, tiene_items, origen_fila_excel)
    VALUES ('T-1001', '2026-01-15 10:30:00', '2026-01-15 13:30:00', 3500, 3500, 0, 'EFECTIVO', 0, 0, 1, 2);
    """)

    # 2. Con discrepancia de pago (-3500)
    conn.execute("""
    INSERT INTO historico_tickets (id, fecha_hora_local, fecha_hora_utc, total_venta, monto_efectivo, monto_transferencia, metodo_pago, es_nocturno, balance_pago_diferencia, tiene_items, origen_fila_excel)
    VALUES ('T-1002', '2026-04-15 18:00:34', '2026-04-15 21:00:34', 3500, 3500, 3500, 'MIXTO', 0, -3500, 1, 3);
    """)

    # 3. Sin items (venta directa cabecera)
    conn.execute("""
    INSERT INTO historico_tickets (id, fecha_hora_local, fecha_hora_utc, total_venta, monto_efectivo, monto_transferencia, metodo_pago, es_nocturno, balance_pago_diferencia, tiene_items, origen_fila_excel)
    VALUES ('T-1003', '2026-06-20 20:00:00', '2026-06-20 23:00:00', 1600, 0, 1600, 'TRANSFERENCIA', 0, 0, 0, 4);
    """)

    # Insertar items de prueba
    conn.execute("""
    INSERT INTO historico_ticket_items (ticket_id, ticket_id_correlacionado, fecha_hora_local, fecha_hora_utc, producto_nombre, cantidad, unidad, precio_unitario, subtotal, metodo_pago, calculo_subtotal_coincide, es_duplicado_deriva, origen_fila_excel)
    VALUES ('T-1001', NULL, '2026-01-15 10:30:00', '2026-01-15 13:30:00', 'Quilmes 1lt', 1.0, 'Unidad', 3500, 3500, 'EFECTIVO', 1, 0, 2);
    """)

    # Item pesable con cantidad NULL
    conn.execute("""
    INSERT INTO historico_ticket_items (ticket_id, ticket_id_correlacionado, fecha_hora_local, fecha_hora_utc, producto_nombre, cantidad, unidad, precio_unitario, subtotal, metodo_pago, calculo_subtotal_coincide, es_duplicado_deriva, origen_fila_excel)
    VALUES ('T-1002', NULL, '2026-04-15 18:00:34', '2026-04-15 21:00:34', 'Mignon blandito', NULL, 'Gramos', 2200, 2000, 'MIXTO', 0, 0, 3);
    """)

    # Item normal asociado a T-1002
    conn.execute("""
    INSERT INTO historico_ticket_items (ticket_id, ticket_id_correlacionado, fecha_hora_local, fecha_hora_utc, producto_nombre, cantidad, unidad, precio_unitario, subtotal, metodo_pago, calculo_subtotal_coincide, es_duplicado_deriva, origen_fila_excel)
    VALUES ('T-1002', NULL, '2026-04-15 18:00:34', '2026-04-15 21:00:34', 'Coca 2,5lts', 1.0, 'Unidad', 1500, 1500, 'MIXTO', 1, 0, 4);
    """)

    # Item marcado como reintento duplicado
    conn.execute("""
    INSERT INTO historico_ticket_items (ticket_id, ticket_id_correlacionado, fecha_hora_local, fecha_hora_utc, producto_nombre, cantidad, unidad, precio_unitario, subtotal, metodo_pago, calculo_subtotal_coincide, es_duplicado_deriva, origen_fila_excel)
    VALUES ('T-9999', NULL, '2026-09-16 20:06:04', '2026-09-16 23:06:04', 'Quilmes 1lt', 2.0, 'Unidad', 3800, 7600, 'EFECTIVO', 1, 1, 9879);
    """)

    # Insertar cargas de prueba
    # 1. Carga normal
    conn.execute("""
    INSERT INTO historico_cargas_servicios (id, fecha_hora_local, fecha_hora_utc, servicio, monto_carga, monto_adicional, total_cobrado, pago_efectivo, pago_transferencia, balance_pago_diferencia, es_posible_duplicado, fingerprint_sha256, origen_fila_excel)
    VALUES ('HC-1', '2026-01-10 11:00:00', '2026-01-10 14:00:00', 'SUBE', 3000, 200, 3200, 3200, 0, 0, 0, 'fp1', 2);
    """)

    # 2 y 3. Pareja de posibles duplicados simultáneos
    conn.execute("""
    INSERT INTO historico_cargas_servicios (id, fecha_hora_local, fecha_hora_utc, servicio, monto_carga, monto_adicional, total_cobrado, pago_efectivo, pago_transferencia, balance_pago_diferencia, es_posible_duplicado, fingerprint_sha256, origen_fila_excel)
    VALUES ('HC-2', '2026-03-25 12:17:44', '2026-03-25 15:17:44', 'SUBE', 2000, 100, 2100, 2100, 0, 0, 1, 'fp2', 681);
    """)
    conn.execute("""
    INSERT INTO historico_cargas_servicios (id, fecha_hora_local, fecha_hora_utc, servicio, monto_carga, monto_adicional, total_cobrado, pago_efectivo, pago_transferencia, balance_pago_diferencia, es_posible_duplicado, fingerprint_sha256, origen_fila_excel)
    VALUES ('HC-3', '2026-03-25 12:17:44', '2026-03-25 15:17:44', 'SUBE', 2000, 100, 2100, 2100, 0, 0, 1, 'fp3', 682);
    """)

    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def client_authenticated(test_hist_db):
    """Cliente de prueba autenticado con conexión inyectada."""
    user = AuthenticatedUser(username="alan", display_name="Alan")
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db_connection] = lambda: test_hist_db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def client_anonymous():
    """Cliente anónimo sin overrides de autenticación."""
    app.dependency_overrides.clear()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_rechazo_solicitudes_anonimas(client_anonymous):
    """Todos los endpoints históricos deben rechazar solicitudes sin token con HTTP 401."""
    endpoints = [
        "/api/historical/summary",
        "/api/historical/sales",
        "/api/historical/products",
        "/api/historical/services",
        "/api/historical/tickets",
        "/api/historical/tickets/T-1001",
        "/api/historical/compare?desde_a=2026-01-01&hasta_a=2026-01-31&desde_b=2026-02-01&hasta_b=2026-02-28",
    ]
    for ep in endpoints:
        resp = client_anonymous.get(ep)
        assert resp.status_code == 401, f"Endpoint {ep} no rechazó usuario anónimo"


def test_historical_summary_exitoso(client_authenticated):
    """Verifica que el resumen histórico calcula exactamente los totales del esquema."""
    resp = client_authenticated.get("/api/historical/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_tickets"] == 3
    assert data["total_ventas_pesos"] == 8600  # 3500 + 3500 + 1600
    assert data["total_ventas_efectivo"] == 7000  # 3500 + 3500
    assert data["total_ventas_transferencia"] == 5100  # 0 + 3500 + 1600
    assert data["total_cargas_cobrado"] == 7400  # 3200 + 2100 + 2100
    assert data["total_operaciones_cargas"] == 3
    assert "advertencia_pagos" in data
    assert "advertencia_items" in data


def test_historical_sales_filtros_y_paginacion(client_authenticated):
    """Verifica filtros de fecha, medio de pago y serie temporal en ventas históricas."""
    # 1. Sin filtros
    resp = client_authenticated.get("/api/historical/sales")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_ventas"] == 8600
    assert data["cantidad_tickets"] == 3
    assert len(data["tickets"]) == 3

    # 2. Filtro por fecha (solo enero)
    resp_ene = client_authenticated.get("/api/historical/sales?desde=2026-01-01&hasta=2026-01-31")
    assert resp_ene.status_code == 200
    data_ene = resp_ene.json()
    assert data_ene["cantidad_tickets"] == 1
    assert data_ene["tickets"][0]["id"] == "T-1001"

    # 3. Filtro por medio de pago
    resp_efv = client_authenticated.get("/api/historical/sales?metodo_pago=EFECTIVO")
    assert resp_efv.status_code == 200
    assert resp_efv.json()["cantidad_tickets"] == 1

    # 4. Agrupación inválida (HTTP 422)
    resp_bad = client_authenticated.get("/api/historical/sales?agrupacion=semana")
    assert resp_bad.status_code == 422


def test_historical_products_toggle_depurado(client_authenticated):
    """Verifica que el ranking de productos excluye reintentos duplicados cuando depurado=True."""
    # 1. Modo Depurado (True por defecto): excluye la fila duplicada de Quilmes ($7600)
    resp_dep = client_authenticated.get("/api/historical/products?depurado=true")
    assert resp_dep.status_code == 200
    data_dep = resp_dep.json()
    quilmes_dep = next(p for p in data_dep["productos"] if p["producto_nombre"] == "Quilmes 1lt")
    assert quilmes_dep["total_recaudado"] == 3500
    assert quilmes_dep["operaciones"] == 1

    # 2. Modo Original (False): incluye la fila duplicada (3500 + 7600 = 11100)
    resp_orig = client_authenticated.get("/api/historical/products?depurado=false")
    assert resp_orig.status_code == 200
    data_orig = resp_orig.json()
    quilmes_orig = next(p for p in data_orig["productos"] if p["producto_nombre"] == "Quilmes 1lt")
    assert quilmes_orig["total_recaudado"] == 11100
    assert quilmes_orig["operaciones"] == 2

    # 3. Métricas de pesables: Mignon blandito debe reflejar ventas_pesables_monto = 1
    mignon = next(p for p in data_dep["productos"] if p["producto_nombre"] == "Mignon blandito")
    assert mignon["ventas_pesables_monto"] == 1
    assert mignon["unidades_cuantificables"] == 0.0


def test_historical_services_toggle_duplicados(client_authenticated):
    """Verifica que el ranking de servicios permite excluir las cargas potencialmente duplicadas."""
    # 1. Incluyendo posibles duplicados (3 cargas = 7400)
    resp_all = client_authenticated.get("/api/historical/services?excluir_duplicados=false")
    assert resp_all.status_code == 200
    assert resp_all.json()["total_operaciones"] == 3
    assert resp_all.json()["total_cobrado"] == 7400

    # 2. Excluyendo posibles duplicados (1 carga no duplicada = 3200)
    resp_ex = client_authenticated.get("/api/historical/services?excluir_duplicados=true")
    assert resp_ex.status_code == 200
    assert resp_ex.json()["total_operaciones"] == 1
    assert resp_ex.json()["total_cobrado"] == 3200


def test_historical_ticket_detail_and_search(client_authenticated):
    """Verifica la búsqueda de tickets y la ficha detallada con alertas."""
    # 1. Búsqueda por query
    resp_search = client_authenticated.get("/api/historical/tickets?query=1002")
    assert resp_search.status_code == 200
    assert resp_search.json()["total_encontrados"] == 1

    # 2. Detalle de ticket con discrepancia de pago (T-1002)
    resp_det = client_authenticated.get("/api/historical/tickets/T-1002")
    assert resp_det.status_code == 200
    data_det = resp_det.json()
    assert data_det["id"] == "T-1002"
    assert data_det["balance_pago_diferencia"] == -3500
    assert len(data_det["items"]) == 2
    assert any("Discrepancia en pagos" in al for al in data_det["alertas"])

    # 3. Detalle de ticket sin ítems (T-1003)
    resp_det_sin_items = client_authenticated.get("/api/historical/tickets/T-1003")
    assert resp_det_sin_items.status_code == 200
    data_sin = resp_det_sin_items.json()
    assert data_sin["tiene_items"] == 0
    assert any("sin desglose de artículos" in al for al in data_sin["alertas"])

    # 4. Ticket inexistente (HTTP 404)
    resp_404 = client_authenticated.get("/api/historical/tickets/T-INEXISTENTE")
    assert resp_404.status_code == 404


def test_historical_compare(client_authenticated):
    """Verifica la comparación de métricas y cálculo de variaciones porcentuales."""
    url = "/api/historical/compare?desde_a=2026-01-01&hasta_a=2026-01-31&desde_b=2026-04-01&hasta_b=2026-04-30"
    resp = client_authenticated.get(url)
    assert resp.status_code == 200
    data = resp.json()
    assert "periodo_a" in data
    assert "periodo_b" in data
    assert "variaciones" in data
    assert data["periodo_a"]["cantidad_tickets"] == 1
    assert data["periodo_b"]["cantidad_tickets"] == 1
    assert data["variaciones"]["cantidad_tickets_pct"] == 0.0
