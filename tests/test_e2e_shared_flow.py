"""Prueba end-to-end de flujo operativo compartido entre Alan y Yilda.

Verifica el ciclo completo sobre base central SQLite controlada:
1. Alan inicia sesión y obtiene token JWT.
2. Alan crea un producto en el catálogo central.
3. Yilda inicia sesión con sus credenciales y consulta el catálogo, viendo el producto creado por Alan.
4. Yilda registra una venta con snapshot de costo histórico.
5. Se verifica la idempotencia: un reintento idéntico retorna 200 sin duplicar registros.
6. La venta registrada por Yilda se lee inmediatamente desde el endpoint central de historial.
7. Alan registra una carga de servicio y un gasto en efectivo.
8. Alan registra el cierre diario de caja.
9. Yilda consulta el Dashboard Financiero central y observa todas las métricas consolidadas
   procedentes de la base central compartida.
"""

from datetime import datetime
import sqlite3
import pytest
from fastapi.testclient import TestClient

from api.auth import generate_password_hash
from api.dependencies import get_db_connection
from api.main import app

TEST_SECRET = "secreto_e2e_super_seguro_minimo_32_caracteres_long"
ALAN_PASS = "clave-alan-e2e-123"
YILDA_PASS = "clave-yilda-e2e-456"


@pytest.fixture(autouse=True)
def setup_auth_env(monkeypatch):
    monkeypatch.setenv("GENARO_AUTH_SECRET_KEY", TEST_SECRET)
    monkeypatch.setenv("GENARO_USER_ALAN_HASH", generate_password_hash(ALAN_PASS, iterations=1_000))
    monkeypatch.setenv("GENARO_USER_YILDA_HASH", generate_password_hash(YILDA_PASS, iterations=1_000))


@pytest.fixture
def shared_db():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")

    # Catálogo
    conn.execute("""
        CREATE TABLE IF NOT EXISTS productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL COLLATE NOCASE,
            categoria TEXT NOT NULL DEFAULT 'Otro',
            proveedor TEXT NOT NULL DEFAULT 'General',
            unidad TEXT NOT NULL DEFAULT 'Unidad',
            costo INTEGER NOT NULL DEFAULT 0 CHECK (costo >= 0),
            precio_dia INTEGER NOT NULL CHECK (precio_dia >= 0),
            precio_noche INTEGER NOT NULL DEFAULT 0,
            margen_ratio REAL GENERATED ALWAYS AS (
                CASE WHEN costo > 0 THEN ROUND((CAST(precio_dia AS REAL) - costo) / costo, 4) ELSE 0.0 END
            ) VIRTUAL,
            activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
            fecha_actualizacion TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS productos_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            producto_id INTEGER NOT NULL,
            accion TEXT NOT NULL CHECK (accion IN ('CREAR', 'EDITAR', 'ACTIVAR', 'DESACTIVAR')),
            valores_anteriores TEXT,
            valores_nuevos TEXT NOT NULL,
            fecha_utc TEXT NOT NULL DEFAULT (datetime('now')),
            actor TEXT NOT NULL DEFAULT 'sistema',
            FOREIGN KEY (producto_id) REFERENCES productos (id) ON DELETE CASCADE
        );
    """)

    # Tickets y ticket_items
    conn.execute("""
        CREATE TABLE tickets (
            id TEXT PRIMARY KEY,
            fecha_hora_utc TEXT NOT NULL,
            total_venta INTEGER NOT NULL,
            monto_efectivo INTEGER NOT NULL,
            monto_transferencia INTEGER NOT NULL,
            metodo_pago TEXT NOT NULL,
            es_nocturno INTEGER NOT NULL DEFAULT 0,
            anulado INTEGER NOT NULL DEFAULT 0,
            motivo_anulacion TEXT,
            usuario_creacion TEXT DEFAULT 'sistema',
            usuario_anulacion TEXT
        );
    """)
    conn.execute("""
        CREATE TABLE ticket_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT NOT NULL,
            producto_id INTEGER,
            producto_nombre TEXT NOT NULL,
            cantidad REAL NOT NULL,
            unidad TEXT NOT NULL DEFAULT 'Unidad',
            precio_unitario INTEGER NOT NULL,
            subtotal INTEGER NOT NULL,
            metodo_pago TEXT NOT NULL,
            costo_unitario INTEGER,
            FOREIGN KEY (ticket_id) REFERENCES tickets (id)
        );
    """)

    # Cargas de servicios
    conn.execute("""
        CREATE TABLE cargas_servicios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            legacy_row_id TEXT,
            fecha_hora_utc TEXT NOT NULL,
            servicio TEXT NOT NULL,
            monto_carga INTEGER NOT NULL,
            monto_adicional INTEGER NOT NULL DEFAULT 0,
            total_cobrado INTEGER NOT NULL,
            pago_efectivo INTEGER NOT NULL DEFAULT 0,
            pago_transferencia INTEGER NOT NULL DEFAULT 0,
            anulado INTEGER NOT NULL DEFAULT 0,
            CHECK (total_cobrado = monto_carga + monto_adicional),
            CHECK (total_cobrado = pago_efectivo + pago_transferencia)
        );
    """)

    # Gastos y retiros
    conn.execute("""
        CREATE TABLE gastos_retiros (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            legacy_row_id TEXT,
            fecha_hora_utc TEXT NOT NULL,
            tipo TEXT NOT NULL DEFAULT 'General',
            detalle TEXT NOT NULL,
            monto_salida INTEGER NOT NULL,
            origen_fondos TEXT DEFAULT 'Caja diaria',
            metodo_pago TEXT NOT NULL,
            anulado INTEGER NOT NULL DEFAULT 0
        );
    """)

    # Cierres diarios
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
            cerrado_por TEXT NOT NULL,
            cerrado_en_utc TEXT DEFAULT (datetime('now'))
        );
    """)

    # Tablas de auditoría
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ventas_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT NOT NULL,
            item_id INTEGER,
            accion TEXT NOT NULL CHECK (accion IN ('CREACION', 'ANULACION', 'EDICION_ITEM', 'ELIMINACION_ITEM')),
            motivo TEXT,
            usuario TEXT NOT NULL DEFAULT 'sistema',
            datos_previos_json TEXT,
            datos_nuevos_json TEXT,
            creado_en_utc TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (ticket_id) REFERENCES tickets (id) ON DELETE CASCADE
        );
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cargas_servicios_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            carga_id INTEGER NOT NULL,
            accion TEXT NOT NULL CHECK (accion IN ('CREAR', 'EDITAR', 'ANULAR')),
            valores_anteriores TEXT,
            valores_nuevos TEXT NOT NULL,
            fecha_utc TEXT NOT NULL DEFAULT (datetime('now')),
            actor TEXT NOT NULL DEFAULT 'sistema',
            FOREIGN KEY (carga_id) REFERENCES cargas_servicios (id) ON DELETE CASCADE
        );
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gastos_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gasto_id INTEGER NOT NULL,
            accion TEXT NOT NULL CHECK (accion IN ('CREAR', 'EDITAR', 'ANULAR')),
            valores_anteriores TEXT,
            valores_nuevos TEXT NOT NULL,
            fecha_utc TEXT NOT NULL DEFAULT (datetime('now')),
            actor TEXT NOT NULL DEFAULT 'sistema',
            FOREIGN KEY (gasto_id) REFERENCES gastos_retiros (id) ON DELETE CASCADE
        );
    """)

    conn.commit()
    yield conn
    conn.close()


def test_flujo_compartido_alan_y_yilda(shared_db):
    """Prueba que los cambios realizados por Alan son leídos y continuados por Yilda sobre la misma base central."""
    app.dependency_overrides[get_db_connection] = lambda: shared_db

    with TestClient(app) as client:
        # 1. Login de Alan
        resp_login_alan = client.post("/api/auth/login", json={"username": "alan", "password": ALAN_PASS})
        assert resp_login_alan.status_code == 200
        token_alan = resp_login_alan.json()["access_token"]
        headers_alan = {"Authorization": f"Bearer {token_alan}"}

        # 2. Alan da de alta un producto en el catálogo central
        prod_payload = {
            "nombre": "Alfajor Triple Chocolate",
            "categoria": "Golosinas",
            "proveedor": "Distribuidora Dulce",
            "unidad": "Unidad",
            "costo": 500,
            "precio_venta": 900,
            "activo": True,
        }
        resp_prod = client.post("/api/catalog", json=prod_payload, headers=headers_alan)
        assert resp_prod.status_code == 201
        prod_id = resp_prod.json()["id_producto"]
        assert prod_id > 0

        # 3. Login de Yilda
        resp_login_yilda = client.post("/api/auth/login", json={"username": "yilda", "password": YILDA_PASS})
        assert resp_login_yilda.status_code == 200
        token_yilda = resp_login_yilda.json()["access_token"]
        headers_yilda = {"Authorization": f"Bearer {token_yilda}"}

        # 4. Yilda consulta el catálogo y ve el producto creado por Alan
        resp_cat = client.get("/api/catalog", headers=headers_yilda)
        assert resp_cat.status_code == 200
        nombres_productos = [p["nombre"] for p in resp_cat.json()["productos"]]
        assert "Alfajor Triple Chocolate" in nombres_productos

        # 5. Yilda registra una venta con ese producto (2 unidades = $1.800)
        hoy_str = "24/10/2026"
        hoy_iso = "2026-10-24"
        ticket_id = "T-E2E-SHARED-001"
        venta_payload = {
            "ticket": {
                "ticket_id": ticket_id,
                "fecha": f"{hoy_str} 14:30:00",
                "total_venta": 1800,
                "monto_efectivo": 1800,
                "monto_transf": 0,
                "metodo_pago": "EFECTIVO",
            },
            "items": [
                {
                    "ticket_id": ticket_id,
                    "producto": "Alfajor Triple Chocolate",
                    "cantidad": 2.0,
                    "unidad": "Unidad",
                    "precio_unit": 900,
                    "subtotal": 1800,
                    "costo_unitario": 500,
                }
            ],
            "usuario": "Yilda",
        }
        resp_venta = client.post("/api/sales", json=venta_payload, headers=headers_yilda)
        assert resp_venta.status_code == 201
        assert resp_venta.json()["ticket_id"] == ticket_id

        # 6. Idempotencia: Yilda reintenta la misma venta (ej. caída de red momentánea)
        resp_retry = client.post("/api/sales", json=venta_payload, headers=headers_yilda)
        assert resp_retry.status_code == 200  # Idempotente OK
        assert resp_retry.json()["ticket_id"] == ticket_id

        # 7. Alan consulta el historial de ventas y observa la venta de Yilda
        resp_hist = client.get("/api/sales", headers=headers_alan)
        assert resp_hist.status_code == 200
        tickets_hist = [t["ticket_id"] for t in resp_hist.json()["ventas"]]
        assert ticket_id in tickets_hist

        # 8. Alan registra una carga de servicio Claro y un gasto
        carga_payload = {
            "servicio": "Claro",
            "monto_carga": 3000,
            "monto_adicional": 200,
            "total_cobrado": 3200,
            "pago_efvo": 3200,
            "pago_transf": 0,
            "fecha": f"{hoy_str} 15:00:00",
        }
        resp_carga = client.post("/api/services", json=carga_payload, headers=headers_alan)
        assert resp_carga.status_code == 201

        gasto_payload = {
            "detalle": "Compra de café y azúcar",
            "monto_salida": 1000,
            "tipo": "Insumos",
            "metodo_pago": "EFECTIVO",
            "fecha": f"{hoy_str} 15:30:00",
        }
        resp_gasto = client.post("/api/expenses", json=gasto_payload, headers=headers_alan)
        assert resp_gasto.status_code == 201

        # 9. Alan realiza el arqueo y cierre diario
        # Ventas efvo: 1800. Servicios efvo: 3200. Gastos efvo: 1000. d_recargas: 0.
        # Efectivo esperado: 1800 + 3200 - 1000 = 4000.
        cierre_payload = {
            "fecha": hoy_iso,
            "efectivo_esperado": 4000,
            "efectivo_contado": 4000,
            "diferencia": 0,
            "total_ventas_efvo": 1800,
            "total_ventas_transf": 0,
            "total_servicios_efvo": 3200,
            "total_servicios_transf": 0,
            "total_gastos_efvo": 1000,
            "total_gastos_transf": 0,
            "total_banco": 0,
            "observaciones": "Cierre compartido cuadrado",
            "cerrado_por": "Alan",
        }
        resp_cierre = client.post("/api/expenses/cierres", json=cierre_payload, headers=headers_alan)
        assert resp_cierre.status_code == 201

        # 10. Yilda consulta el Dashboard Financiero central y ve todas las operaciones consolidadas
        resp_dash = client.get(
            "/api/reports/dashboard",
            params={"desde": hoy_iso, "hasta": hoy_iso, "periodo": "personalizado"},
            headers=headers_yilda,
        )
        assert resp_dash.status_code == 200
        metricas = resp_dash.json()

        # Caja A
        assert metricas["caja_a"]["total_ventas"] == 1800
        assert metricas["caja_a"]["ventas_con_costo_completo"] == 1
        assert metricas["caja_a"]["ganancia_bruta_real"] == 800  # 1800 - 1000 de costo
        assert metricas["caja_a"]["margen_bruto_porcentaje"] == 44.4  # (800 / 1800) * 100

        # Cajas de servicios
        assert metricas["caja_e"]["capital_total"] == 3000  # Claro
        assert metricas["caja_c"]["ganancia_adicional_total"] == 200

        # Gastos
        assert metricas["gastos"]["gastos_total"] == 1000

        # Arqueo de cajón
        assert metricas["conciliacion_cajon"]["efectivo_esperado_cajon"] == 4000

        # Cierre registrado
        assert len(metricas["cierres"]) == 1
        assert metricas["cierres"][0]["cerrado_por"] == "alan"

    app.dependency_overrides.clear()
