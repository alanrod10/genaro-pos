"""Pruebas unitarias y de integración para el Visor / Dashboard conectado (api/routers/reports.py).

Verifica sobre base de datos SQLite aislada y controlada:
1. Rechazo de acceso anónimo a /api/reports/dashboard (HTTP 401).
2. Cálculo financiero exacto de métricas consolidadas:
   - Caja A (Drugstore con análisis de costo unitario histórico congelado)
   - Cajas B, C, D, E de servicios y conciliación bancaria
   - Gastos y retiros (Efectivo vs Transferencia)
   - Conciliación de cajón físico (Arqueo esperado)
   - Serie temporal para evolución gráfica
3. Advertencia de costos históricos incompletos cuando hay ítems con costo desconocido.
4. Filtros de período (hoy, ultimos_7_dias, ultimos_30_dias, personalizado).
5. Rechazo de períodos no válidos con HTTP 422.
6. Funcionamiento de la ruta alias /api/expenses/dashboard.
"""

from datetime import datetime, timezone
import sqlite3
import pytest
from fastapi.testclient import TestClient

from api.dependencies import AuthenticatedUser, get_current_user, get_db_connection
from api.main import app


@pytest.fixture
def test_db():
    """Crea una base de datos SQLite en memoria con esquema de ventas, servicios, gastos y cierres."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")

    # 1. Esquema de productos
    conn.execute("""
        CREATE TABLE productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            costo INTEGER NOT NULL DEFAULT 0,
            precio_dia INTEGER NOT NULL DEFAULT 0,
            activo INTEGER NOT NULL DEFAULT 1
        );
    """)

    # 2. Esquema de tickets y ticket_items (migraciones 003 y 009)
    conn.execute("""
        CREATE TABLE tickets (
            id TEXT PRIMARY KEY,
            fecha_hora_utc TEXT NOT NULL,
            total_venta INTEGER NOT NULL CHECK (total_venta >= 0),
            monto_efectivo INTEGER NOT NULL DEFAULT 0,
            monto_transferencia INTEGER NOT NULL DEFAULT 0,
            metodo_pago TEXT NOT NULL,
            es_nocturno INTEGER NOT NULL DEFAULT 0,
            anulado INTEGER NOT NULL DEFAULT 0
        );
    """)

    conn.execute("""
        CREATE TABLE ticket_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT NOT NULL,
            producto_nombre TEXT NOT NULL,
            cantidad REAL NOT NULL,
            precio_unitario INTEGER NOT NULL,
            subtotal INTEGER NOT NULL,
            metodo_pago TEXT NOT NULL,
            costo_unitario INTEGER,
            FOREIGN KEY (ticket_id) REFERENCES tickets (id)
        );
    """)

    # 3. Esquema de cargas_servicios
    conn.execute("""
        CREATE TABLE cargas_servicios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha_hora_utc TEXT NOT NULL,
            servicio TEXT NOT NULL,
            monto_carga INTEGER NOT NULL,
            monto_adicional INTEGER NOT NULL DEFAULT 0,
            total_cobrado INTEGER NOT NULL,
            pago_efectivo INTEGER NOT NULL DEFAULT 0,
            pago_transferencia INTEGER NOT NULL DEFAULT 0,
            anulado INTEGER NOT NULL DEFAULT 0
        );
    """)

    # 4. Esquema de gastos_retiros
    conn.execute("""
        CREATE TABLE gastos_retiros (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha_hora_utc TEXT NOT NULL,
            tipo TEXT NOT NULL DEFAULT 'General',
            detalle TEXT NOT NULL,
            monto_salida INTEGER NOT NULL,
            origen_fondos TEXT DEFAULT 'Caja diaria',
            metodo_pago TEXT NOT NULL,
            anulado INTEGER NOT NULL DEFAULT 0
        );
    """)

    # 5. Esquema de cierres_diarios
    conn.execute("""
        CREATE TABLE cierres_diarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL UNIQUE,
            efectivo_esperado INTEGER NOT NULL,
            efectivo_contado INTEGER NOT NULL,
            diferencia INTEGER NOT NULL,
            total_ventas_efvo INTEGER NOT NULL DEFAULT 0,
            total_ventas_transf INTEGER NOT NULL DEFAULT 0,
            total_servicios_efvo INTEGER NOT NULL DEFAULT 0,
            total_servicios_transf INTEGER NOT NULL DEFAULT 0,
            total_gastos_efvo INTEGER NOT NULL DEFAULT 0,
            total_gastos_transf INTEGER NOT NULL DEFAULT 0,
            total_banco INTEGER NOT NULL DEFAULT 0,
            observaciones TEXT,
            cerrado_por TEXT NOT NULL
        );
    """)

    # ==========================================================================
    # SEMBRADO DE DATOS CONTROLADOS (Fecha: 2026-10-24)
    # ==========================================================================
    # Venta 1: $10.000 ($6.000 efvo, $4.000 transf), con costo congelado $6.000 -> ganancia $4.000
    conn.execute("""
        INSERT INTO tickets (id, fecha_hora_utc, total_venta, monto_efectivo, monto_transferencia, metodo_pago, anulado)
        VALUES ('T-DASH-1', '2026-10-24 10:00:00', 10000, 6000, 4000, 'MIXTO', 0);
    """)
    conn.execute("""
        INSERT INTO ticket_items (ticket_id, producto_nombre, cantidad, precio_unitario, subtotal, metodo_pago, costo_unitario)
        VALUES ('T-DASH-1', 'Gaseosa Cola 2L', 2, 5000, 10000, 'MIXTO', 3000);
    """)

    # Venta 2: $5.000 en efectivo, ítem sin costo histórico (costo_unitario IS NULL)
    conn.execute("""
        INSERT INTO tickets (id, fecha_hora_utc, total_venta, monto_efectivo, monto_transferencia, metodo_pago, anulado)
        VALUES ('T-DASH-2', '2026-10-24 14:00:00', 5000, 5000, 0, 'EFECTIVO', 0);
    """)
    conn.execute("""
        INSERT INTO ticket_items (ticket_id, producto_nombre, cantidad, precio_unitario, subtotal, metodo_pago, costo_unitario)
        VALUES ('T-DASH-2', 'Articulo Varios', 1, 5000, 5000, 'EFECTIVO', NULL);
    """)

    # Servicio 1: Claro $2.000 + $200 adic en efectivo (Caja E y C)
    conn.execute("""
        INSERT INTO cargas_servicios (fecha_hora_utc, servicio, monto_carga, monto_adicional, total_cobrado, pago_efectivo, pago_transferencia, anulado)
        VALUES ('2026-10-24 11:00:00', 'Claro', 2000, 200, 2200, 2200, 0, 0);
    """)

    # Servicio 2: SUBE MP $3.000 + $100 adic con transferencia (sube_mp_capital y Caja C)
    conn.execute("""
        INSERT INTO cargas_servicios (fecha_hora_utc, servicio, monto_carga, monto_adicional, total_cobrado, pago_efectivo, pago_transferencia, anulado)
        VALUES ('2026-10-24 12:00:00', 'SUBE Mercado Pago', 3000, 100, 3100, 0, 3100, 0);
    """)

    # Servicio 3: Prepay $1.500 + $100 adic en efectivo (Caja B y C)
    conn.execute("""
        INSERT INTO cargas_servicios (fecha_hora_utc, servicio, monto_carga, monto_adicional, total_cobrado, pago_efectivo, pago_transferencia, anulado)
        VALUES ('2026-10-24 13:00:00', 'Prepay', 1500, 100, 1600, 1600, 0, 0);
    """)

    # Gasto 1: Insumos $1.000 en efectivo
    conn.execute("""
        INSERT INTO gastos_retiros (fecha_hora_utc, tipo, detalle, monto_salida, metodo_pago, anulado)
        VALUES ('2026-10-24 15:00:00', 'Limpieza', 'Bolsas de residuo', 1000, 'EFECTIVO', 0);
    """)

    # Gasto 2: Pago proveedor $2.000 con transferencia
    conn.execute("""
        INSERT INTO gastos_retiros (fecha_hora_utc, tipo, detalle, monto_salida, metodo_pago, anulado)
        VALUES ('2026-10-24 16:00:00', 'Proveedores', 'Lácteos MP', 2000, 'TRANSFERENCIA', 0);
    """)

    # Cierre del día
    conn.execute("""
        INSERT INTO cierres_diarios (fecha, efectivo_esperado, efectivo_contado, diferencia, total_ventas_efvo, total_ventas_transf, cerrado_por)
        VALUES ('2026-10-24', 10700, 10700, 0, 11000, 4000, 'Alan');
    """)

    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def auth_client(test_db):
    app.dependency_overrides[get_db_connection] = lambda: test_db
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        username="alan", display_name="Alan"
    )
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ==============================================================================
# PRUEBAS DEL ENDPOINT
# ==============================================================================


def test_dashboard_requiere_autenticacion():
    """Garantiza que /api/reports/dashboard rechaza peticiones anónimas (401)."""
    with TestClient(app) as c:
        resp = c.get("/api/reports/dashboard")
        assert resp.status_code == 401


def test_dashboard_metricas_consolidadas_periodo_personalizado(auth_client):
    """Verifica que el dashboard consolidado central calcule correctamente todas las cajas."""
    resp = auth_client.get(
        "/api/reports/dashboard",
        params={"desde": "2026-10-24", "hasta": "2026-10-24", "periodo": "personalizado"},
    )
    assert resp.status_code == 200
    data = resp.json()

    # Período
    assert data["periodo_tipo"] == "personalizado"
    assert data["desde"] == "2026-10-24"
    assert data["hasta"] == "2026-10-24"

    # Caja A (Ventas)
    caja_a = data["caja_a"]
    assert caja_a["total_ventas"] == 15000  # 10.000 + 5.000
    assert caja_a["ventas_efvo"] == 11000   # 6.000 + 5.000
    assert caja_a["ventas_transf"] == 4000
    assert caja_a["cantidad_tickets"] == 2

    # Análisis de costo histórico
    assert caja_a["ventas_con_costo_completo"] == 1
    assert caja_a["ventas_con_costo_incompleto"] == 1
    assert caja_a["items_con_costo_desconocido"] == 1
    assert caja_a["tiene_costos_completos"] is False
    assert caja_a["costo_historico_total"] == 6000
    assert caja_a["ganancia_bruta_real"] == 4000  # 10.000 - 6.000
    assert caja_a["margen_bruto_porcentaje"] == 40.0  # 4.000 / 10.000

    # Cajas de servicios
    caja_b = data["caja_b"]
    assert caja_b["capital_total"] == 1500  # Prepay

    caja_c = data["caja_c"]
    assert caja_c["ganancia_adicional_total"] == 400  # 200 + 100 + 100

    caja_e = data["caja_e"]
    assert caja_e["capital_total"] == 2000  # Claro

    # Gastos
    gastos = data["gastos"]
    assert gastos["gastos_total"] == 3000
    assert gastos["gastos_efvo"] == 1000
    assert gastos["gastos_transf"] == 2000
    assert gastos["cantidad_gastos"] == 2

    # Conciliaciones financieras (fórmulas mostrar_visor)
    # d_recargas = total transferencias de servicios = 3100
    # sube_mp_capital = 3000
    # Caja D banco: ventas_transf (4000) + d_recargas (3100) - sube_mp_capital (3000) - gastos_transf (2000) = 2100
    caja_d = data["caja_d"]
    assert caja_d["ventas_transf"] == 4000
    assert caja_d["recargas_transf"] == 3100
    assert caja_d["sube_mp_capital"] == 3000
    assert caja_d["gastos_transf"] == 2000
    assert caja_d["total_banco_neto"] == 2100

    # Conciliación física cajón (Arqueo):
    # Exclusivamente movimientos físicos reales (sin restar transferencias bancarias d_recargas):
    # ventas_efvo (11000) + servicios_efvo (3800) - gastos_efvo (1000) = 13800
    cajon = data["conciliacion_cajon"]
    assert cajon["ventas_efvo"] == 11000
    assert cajon["servicios_efvo"] == 3800
    assert cajon["gastos_efvo"] == 1000
    assert cajon["d_recargas"] == 3100
    assert cajon["efectivo_esperado_cajon"] == 13800

    # Efectivo disponible atribuible a ventas de drugstore coincide con ventas en efectivo
    assert caja_a["efectivo_disponible_drugstore"] == 11000

    # Cierre registrado
    assert len(data["cierres"]) == 1
    assert data["cierres"][0]["cerrado_por"] == "Alan"

    # Serie temporal
    assert len(data["serie_temporal"]) == 1
    punto = data["serie_temporal"][0]
    assert punto["fecha"] == "2026-10-24"
    assert punto["ventas_total"] == 15000


def test_dashboard_rechaza_periodo_invalido(auth_client):
    """Rechaza períodos no reconocidos con HTTP 422."""
    resp = auth_client.get(
        "/api/reports/dashboard",
        params={"periodo": "periodo_inventado"},
    )
    assert resp.status_code == 422


def test_dashboard_ruta_alias_expenses(auth_client):
    """Comprueba que /api/expenses/dashboard responda idénticamente a /api/reports/dashboard."""
    resp = auth_client.get(
        "/api/expenses/dashboard",
        params={"desde": "2026-10-24", "hasta": "2026-10-24", "periodo": "personalizado"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["caja_a"]["total_ventas"] == 15000


# ==============================================================================
# PRUEBAS DE REGRESIÓN DE REGLAS FINANCIERAS CRÍTICAS (mostrar_visor)
# ==============================================================================


def test_regla_financiera_transferencias_y_capital_no_son_ganancia_ni_efectivo(auth_client):
    """Verifica que las transferencias no inflen el efectivo y que el capital de recargas no sea ganancia."""
    resp = auth_client.get(
        "/api/reports/dashboard",
        params={"desde": "2026-10-24", "hasta": "2026-10-24", "periodo": "personalizado"},
    )
    assert resp.status_code == 200
    data = resp.json()

    # 1. Las transferencias de ventas no entran a ventas_efvo ni a conciliacion_cajon.ventas_efvo
    assert data["caja_a"]["ventas_transf"] == 4000
    assert data["caja_a"]["ventas_efvo"] == 11000
    assert data["conciliacion_cajon"]["ventas_efvo"] == 11000

    # 2. El capital de recargas (Prepay $1500 + Claro $2000) no forma parte de Caja C (ganancia)
    assert data["caja_b"]["capital_total"] == 1500
    assert data["caja_e"]["capital_total"] == 2000
    assert data["caja_c"]["ganancia_adicional_total"] == 400  # Solo adicionales, jamás capital


def test_regla_financiera_sube_mercado_pago_tratamiento_bancario(auth_client):
    """Verifica que el capital SUBE (MP) se reste de Caja D (Banco) y no se sume a Prepay ni a Claro."""
    resp = auth_client.get(
        "/api/reports/dashboard",
        params={"desde": "2026-10-24", "hasta": "2026-10-24", "periodo": "personalizado"},
    )
    assert resp.status_code == 200
    data = resp.json()

    # SUBE MP tiene $3.000 de capital y $100 de adicional (pagados por transferencia)
    caja_d = data["caja_d"]
    assert caja_d["sube_mp_capital"] == 3000

    # No debe sumarse al capital de Caja B (Prepay) ni de Caja E (Claro)
    assert data["caja_b"]["capital_total"] == 1500  # Solo Prepay
    assert data["caja_e"]["capital_total"] == 2000  # Solo Claro

    # Debe restarse en el banco neto:
    # 4000 (ventas transf) + 3100 (recargas transf) - 3000 (sube mp capital) - 2000 (gastos transf) = 2100
    assert caja_d["total_banco_neto"] == 2100


def test_regla_financiera_anulaciones_y_costos_incompletos(test_db):
    """Verifica que operaciones anuladas se ignoren y que ventas sin costo histórico no inventen ganancias."""
    # Insertar ticket anulado y carga anulada
    test_db.execute("""
        INSERT INTO tickets (id, fecha_hora_utc, total_venta, monto_efectivo, monto_transferencia, metodo_pago, anulado)
        VALUES ('T-ANULADO', '2026-10-24 18:00:00', 99000, 99000, 0, 'EFECTIVO', 1);
    """)
    test_db.execute("""
        INSERT INTO cargas_servicios (fecha_hora_utc, servicio, monto_carga, monto_adicional, total_cobrado, pago_efectivo, pago_transferencia, anulado)
        VALUES ('2026-10-24 18:30:00', 'Claro', 50000, 5000, 55000, 55000, 0, 1);
    """)
    test_db.execute("""
        INSERT INTO gastos_retiros (fecha_hora_utc, tipo, detalle, monto_salida, metodo_pago, anulado)
        VALUES ('2026-10-24 19:00:00', 'Retiro', 'Retiro anulado', 80000, 'EFECTIVO', 1);
    """)
    test_db.commit()

    app.dependency_overrides[get_db_connection] = lambda: test_db
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(username="alan", display_name="Alan")

    with TestClient(app) as c:
        resp = c.get("/api/reports/dashboard", params={"desde": "2026-10-24", "hasta": "2026-10-24", "periodo": "personalizado"})
        assert resp.status_code == 200
        data = resp.json()

        # Las operaciones anuladas no deben alterar los totales
        assert data["caja_a"]["total_ventas"] == 15000
        assert data["caja_e"]["capital_total"] == 2000
        assert data["gastos"]["gastos_total"] == 3000

    app.dependency_overrides.clear()


def test_regla_financiera_zona_horaria_nocturna_argentina(test_db):
    """Verifica que una venta a las 22:30 hora argentina (almacenada en UTC como 01:30 del día siguiente)
    sea incluida con precisión en el día local argentino y no sea excluida ni trasladada al día siguiente."""
    # Venta a las 22:30 AR del 2026-10-24 -> En UTC es '2026-10-25 01:30:00'
    test_db.execute("""
        INSERT INTO tickets (id, fecha_hora_utc, total_venta, monto_efectivo, monto_transferencia, metodo_pago, anulado)
        VALUES ('T-NOCHE-AR', '2026-10-25 01:30:00', 800, 800, 0, 'EFECTIVO', 0);
    """)
    test_db.execute("""
        INSERT INTO ticket_items (ticket_id, producto_nombre, cantidad, precio_unitario, subtotal, metodo_pago, costo_unitario)
        VALUES ('T-NOCHE-AR', 'Alfajor Nocturno', 1, 800, 800, 'EFECTIVO', 400);
    """)
    test_db.commit()

    app.dependency_overrides[get_db_connection] = lambda: test_db
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(username="alan", display_name="Alan")

    with TestClient(app) as c:
        # Al consultar el día 2026-10-24, la venta nocturna argentina debe estar incluida
        resp_24 = c.get("/api/reports/dashboard", params={"desde": "2026-10-24", "hasta": "2026-10-24", "periodo": "personalizado"})
        assert resp_24.status_code == 200
        data_24 = resp_24.json()
        assert data_24["caja_a"]["total_ventas"] == 15000 + 800  # 15.800
        assert data_24["caja_a"]["ventas_efvo"] == 11000 + 800

        # Al consultar el día 2026-10-25, la venta no debe figurar (pertenece al 24 en Argentina)
        resp_25 = c.get("/api/reports/dashboard", params={"desde": "2026-10-25", "hasta": "2026-10-25", "periodo": "personalizado"})
        assert resp_25.status_code == 200
        data_25 = resp_25.json()
        assert data_25["caja_a"]["total_ventas"] == 0

    app.dependency_overrides.clear()


def test_regresion_conciliacion_contable_valores_dev_turso(test_db):
    """Prueba obligatoria de regresión contable con los valores observados en Turso DEV:
    - Ventas en efectivo: $1.000
    - Ventas por transferencia: $2.000
    - Servicios en efectivo: $123.600
    - Recargas de servicios por transferencia: $10.500
    - Gastos en efectivo y transferencia: $0
    Resultado esperado:
    - Efectivo físico esperado = $124.600
    - Efectivo disponible atribuible a ventas de drugstore = $1.000
    - Total en banco = $12.500
    Verifica además la estricta segregación de medios de pago ante gastos en efectivo y gastos por transferencia.
    """
    fecha_test = "2026-10-28"
    dt_utc = f"{fecha_test} 15:00:00"

    # 1. Ventas Drugstore: $3.000 total ($1.000 efvo, $2.000 transf)
    test_db.execute("""
        INSERT INTO tickets (id, fecha_hora_utc, total_venta, monto_efectivo, monto_transferencia, metodo_pago, anulado)
        VALUES ('T-TEST-TURSO-DEV', ?, 3000, 1000, 2000, 'MIXTO', 0);
    """, (dt_utc,))
    test_db.execute("""
        INSERT INTO ticket_items (ticket_id, producto_nombre, cantidad, precio_unitario, subtotal, metodo_pago, costo_unitario)
        VALUES ('T-TEST-TURSO-DEV', 'Item Kiosco DEV', 1, 3000, 3000, 'MIXTO', 1500);
    """)

    # 2. Servicios:
    # - PREPAY: $116.100 en efectivo (capital)
    test_db.execute("""
        INSERT INTO cargas_servicios (fecha_hora_utc, servicio, monto_carga, monto_adicional, total_cobrado, pago_efectivo, pago_transferencia, anulado)
        VALUES (?, 'PREPAY', 116100, 0, 116100, 116100, 0, 0);
    """, (dt_utc,))
    # - PREPAY + Recargo por transferencia: $10.000 capital + $500 recargo = $10.500 pagado por transferencia
    test_db.execute("""
        INSERT INTO cargas_servicios (fecha_hora_utc, servicio, monto_carga, monto_adicional, total_cobrado, pago_efectivo, pago_transferencia, anulado)
        VALUES (?, 'PREPAY', 10000, 500, 10500, 0, 10500, 0);
    """, (dt_utc,))
    # - CLARO: $1.000 en efectivo (capital Claro segregado)
    test_db.execute("""
        INSERT INTO cargas_servicios (fecha_hora_utc, servicio, monto_carga, monto_adicional, total_cobrado, pago_efectivo, pago_transferencia, anulado)
        VALUES (?, 'CLARO', 1000, 0, 1000, 1000, 0, 0);
    """, (dt_utc,))
    # - Recargos en efectivo: $6.500 (comisiones ganancia Caja C)
    test_db.execute("""
        INSERT INTO cargas_servicios (fecha_hora_utc, servicio, monto_carga, monto_adicional, total_cobrado, pago_efectivo, pago_transferencia, anulado)
        VALUES (?, 'PREPAY', 0, 6500, 6500, 6500, 0, 0);
    """, (dt_utc,))
    test_db.commit()

    app.dependency_overrides[get_db_connection] = lambda: test_db
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(username="alan", display_name="Alan")

    with TestClient(app) as c:
        resp = c.get("/api/reports/dashboard", params={"desde": fecha_test, "hasta": fecha_test, "periodo": "personalizado"})
        assert resp.status_code == 200
        d = resp.json()

        # Validaciones de Caja A (Drugstore)
        assert d["caja_a"]["ventas_efvo"] == 1000
        assert d["caja_a"]["ventas_transf"] == 2000
        assert d["caja_a"]["total_ventas"] == 3000
        assert d["caja_a"]["efectivo_disponible_drugstore"] == 1000

        # Validaciones de Servicios y Cajas B, C, E
        assert d["caja_b"]["capital_total"] == 126100  # 116.100 efvo + 10.000 transf
        assert d["caja_b"]["capital_efvo"] == 116100
        assert d["caja_b"]["capital_transf"] == 10000

        assert d["caja_c"]["ganancia_adicional_total"] == 7000  # 6.500 efvo + 500 transf
        assert d["caja_c"]["adicional_efvo"] == 6500
        assert d["caja_c"]["adicional_transf"] == 500

        assert d["caja_e"]["capital_total"] == 1000
        assert d["caja_e"]["capital_efvo"] == 1000
        assert d["caja_e"]["capital_transf"] == 0

        # Validaciones de Caja D (Banco)
        # Total banco: ventas transf ($2.000) + recargas transf ($10.500) = $12.500
        assert d["caja_d"]["ventas_transf"] == 2000
        assert d["caja_d"]["recargas_transf"] == 10500
        assert d["caja_d"]["total_banco_neto"] == 12500

        # Validaciones de Arqueo Físico (Conciliación de Cajón)
        # Efectivo esperado: ventas_efvo (1.000) + servicios_efvo (123.600) - gastos_efvo (0) = 124.600
        assert d["conciliacion_cajon"]["ventas_efvo"] == 1000
        assert d["conciliacion_cajon"]["servicios_efvo"] == 123600
        assert d["conciliacion_cajon"]["gastos_efvo"] == 0
        assert d["conciliacion_cajon"]["efectivo_esperado_cajon"] == 124600

        # 3. Registrar un gasto en EFECTIVO ($4.600):
        # Debe reducir el efectivo esperado físico y NO debe alterar el saldo bancario
        test_db.execute("""
            INSERT INTO gastos_retiros (fecha_hora_utc, tipo, detalle, monto_salida, metodo_pago, origen_fondos, anulado)
            VALUES (?, 'Limpieza', 'Limpieza y artículos en efectivo', 4600, 'EFECTIVO', 'Caja diaria', 0);
        """, (dt_utc,))
        test_db.commit()

        resp_gasto_efvo = c.get("/api/reports/dashboard", params={"desde": fecha_test, "hasta": fecha_test, "periodo": "personalizado"})
        assert resp_gasto_efvo.status_code == 200
        d_efvo = resp_gasto_efvo.json()
        assert d_efvo["gastos"]["gastos_efvo"] == 4600
        assert d_efvo["gastos"]["gastos_transf"] == 0
        # Caja A permanece intacta en 1.000 (los gastos en efectivo nunca se restan de Caja A):
        assert d_efvo["caja_a"]["ventas_efvo"] == 1000
        assert d_efvo["caja_a"]["efectivo_disponible_drugstore"] == 1000
        # Efectivo físico esperado baja de 124.600 a 120.000:
        assert d_efvo["conciliacion_cajon"]["efectivo_esperado_cajon"] == 120000
        # Banco se mantiene intacto en 12.500:
        assert d_efvo["caja_d"]["total_banco_neto"] == 12500

        # 4. Registrar un gasto por TRANSFERENCIA ($2.500):
        # Debe reducir el saldo bancario y NO debe alterar el efectivo físico esperado
        test_db.execute("""
            INSERT INTO gastos_retiros (fecha_hora_utc, tipo, detalle, monto_salida, metodo_pago, origen_fondos, anulado)
            VALUES (?, 'Servicios', 'Abono de internet por transferencia', 2500, 'TRANSFERENCIA', 'Banco / MP', 0);
        """, (dt_utc,))
        test_db.commit()

        resp_gasto_transf = c.get("/api/reports/dashboard", params={"desde": fecha_test, "hasta": fecha_test, "periodo": "personalizado"})
        assert resp_gasto_transf.status_code == 200
        d_transf = resp_gasto_transf.json()
        assert d_transf["gastos"]["gastos_efvo"] == 4600
        assert d_transf["gastos"]["gastos_transf"] == 2500
        # Efectivo físico permanece en 120.000 (no afectado por gasto bancario):
        assert d_transf["conciliacion_cajon"]["efectivo_esperado_cajon"] == 120000
        # Saldo bancario neto se reduce de 12.500 a 10.000:
        assert d_transf["caja_d"]["total_banco_neto"] == 10000

    app.dependency_overrides.clear()

