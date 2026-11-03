"""Pruebas exhaustivas del modo de solo lectura del servidor (Backend Read-Only Mode).

Verifica:
1. Precedencia y comportamiento de GENARO_API_READ_ONLY y VITE_READ_ONLY_CONNECTED_SMOKE.
2. Bloqueo estricto con HTTP 423 Locked de todas las operaciones mutadoras comerciales
   (POST /api/sales, POST /api/services, POST /api/expenses, DELETE, PATCH, etc.)
   incluso cuando se invoca con token JWT válido de Alan o Yilda.
3. Que las peticiones bloqueadas no produzcan mutaciones ni eventos de auditoría en la BD.
4. Que POST /api/auth/login permanezca permitido para autenticación de inspección.
5. Que los métodos GET, HEAD y OPTIONS continúen operando normalmente.
6. Que cuando el modo esté explícitamente desactivado, la API retorne al funcionamiento normal.
"""

import os
from pathlib import Path
import sqlite3
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.auth import (
    AuthenticatedUser,
    create_access_token,
    generate_password_hash,
)
from api.dependencies import get_current_user, get_db_connection
from api.main import app, is_api_read_only

ROOT = Path(__file__).resolve().parents[1]

TEST_SECRET = "clave_secreta_super_segura_de_mas_de_32_caracteres_read_only"
TEST_ALAN_PASS = "AlanPassSecure123!"


@pytest.fixture
def memory_db():
    """Base de datos SQLite en memoria con esquema completo para comprobar la ausencia de escrituras."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript("""
        CREATE TABLE _schema_migrations (
            version INTEGER PRIMARY KEY,
            nombre TEXT NOT NULL,
            aplicado_en TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO _schema_migrations (version, nombre) VALUES
            (1, '001'), (2, '002'), (3, '003'), (4, '004'),
            (5, '005'), (6, '006'), (7, '007'), (8, '008'), (9, '009'), (10, '010');

        CREATE TABLE productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL COLLATE NOCASE,
            categoria TEXT DEFAULT 'Bebidas',
            proveedor TEXT DEFAULT 'Distribuidora Central',
            unidad TEXT DEFAULT 'Unidad',
            costo INTEGER NOT NULL DEFAULT 0,
            precio_dia INTEGER NOT NULL DEFAULT 0,
            precio_noche INTEGER NOT NULL DEFAULT 0,
            margen_ratio REAL DEFAULT 0,
            activo INTEGER NOT NULL DEFAULT 1,
            fecha_actualizacion TEXT DEFAULT (datetime('now'))
        );
        INSERT INTO productos (id, nombre, categoria, proveedor, unidad, costo, precio_dia, precio_noche, margen_ratio, activo) VALUES
            (1, '361 1,5lts', 'Bebidas', 'Distribuidora Central', 'Unidad', 1900, 2700, 2700, 0.42, 1);

        CREATE TABLE tickets (
            id TEXT PRIMARY KEY,
            fecha_hora_utc TEXT NOT NULL,
            total_venta INTEGER NOT NULL,
            monto_efectivo INTEGER NOT NULL,
            monto_transferencia INTEGER NOT NULL,
            metodo_pago TEXT NOT NULL,
            anulado INTEGER NOT NULL DEFAULT 0,
            motivo_anulacion TEXT
        );

        CREATE TABLE ticket_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT NOT NULL,
            producto_id INTEGER NOT NULL,
            cantidad REAL NOT NULL,
            precio_unitario INTEGER NOT NULL,
            subtotal INTEGER NOT NULL,
            costo_unitario INTEGER,
            FOREIGN KEY (ticket_id) REFERENCES tickets(id)
        );

        CREATE TABLE ventas_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT NOT NULL,
            item_id INTEGER,
            accion TEXT NOT NULL,
            motivo TEXT,
            usuario TEXT NOT NULL,
            datos_previos_json TEXT,
            datos_nuevos_json TEXT,
            creado_en_utc TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE cargas_servicios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            legacy_row_id INTEGER,
            fecha_hora_utc TEXT NOT NULL,
            servicio TEXT NOT NULL,
            monto_carga INTEGER NOT NULL,
            monto_adicional INTEGER NOT NULL DEFAULT 0,
            total_cobrado INTEGER NOT NULL,
            pago_efectivo INTEGER NOT NULL DEFAULT 0,
            pago_transferencia INTEGER NOT NULL DEFAULT 0,
            anulado INTEGER NOT NULL DEFAULT 0,
            motivo_anulacion TEXT,
            idempotency_key TEXT UNIQUE
        );

        CREATE TABLE cargas_servicios_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            carga_id INTEGER NOT NULL,
            accion TEXT NOT NULL,
            valores_anteriores TEXT,
            valores_nuevos TEXT,
            fecha_utc TEXT NOT NULL DEFAULT (datetime('now')),
            actor TEXT NOT NULL
        );

        CREATE TABLE gastos_retiros (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha_hora_utc TEXT NOT NULL,
            detalle TEXT NOT NULL,
            monto_salida INTEGER NOT NULL,
            tipo TEXT NOT NULL DEFAULT 'General',
            origen_fondos TEXT NOT NULL DEFAULT 'Caja diaria',
            metodo_pago TEXT NOT NULL DEFAULT 'EFECTIVO',
            anulado INTEGER NOT NULL DEFAULT 0,
            motivo_anulacion TEXT,
            idempotency_key TEXT UNIQUE
        );

        CREATE TABLE gastos_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gasto_id INTEGER NOT NULL,
            accion TEXT NOT NULL,
            valores_anteriores TEXT,
            valores_nuevos TEXT,
            fecha_utc TEXT NOT NULL DEFAULT (datetime('now')),
            actor TEXT NOT NULL
        );

        CREATE TABLE cierres_diarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL UNIQUE,
            fecha_hora_utc TEXT NOT NULL,
            cerrado_por TEXT NOT NULL DEFAULT 'sistema',
            total_ventas INTEGER NOT NULL DEFAULT 0,
            total_gastos INTEGER NOT NULL DEFAULT 0,
            saldo_caja INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE outbox_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            estado TEXT NOT NULL DEFAULT 'PENDING'
        );
    """)
    yield conn
    conn.close()


def test_is_api_read_only_precedencia():
    """Valida la lectura y precedencia de las variables de entorno de solo lectura."""
    # 1. Variable propia GENARO_API_READ_ONLY activada
    with patch.dict(os.environ, {"GENARO_API_READ_ONLY": "true"}, clear=False):
        assert is_api_read_only() is True
    with patch.dict(os.environ, {"GENARO_API_READ_ONLY": "1"}, clear=False):
        assert is_api_read_only() is True
    with patch.dict(os.environ, {"GENARO_API_READ_ONLY": "yes"}, clear=False):
        assert is_api_read_only() is True

    # 2. Variable propia desactivada explícitamente
    with patch.dict(os.environ, {"GENARO_API_READ_ONLY": "false"}, clear=False):
        assert is_api_read_only() is False
    with patch.dict(os.environ, {"GENARO_API_READ_ONLY": "0"}, clear=False):
        assert is_api_read_only() is False

    # 3. Respaldo por compatibilidad con VITE_READ_ONLY_CONNECTED_SMOKE
    env_clean = {k: v for k, v in os.environ.items() if k != "GENARO_API_READ_ONLY"}
    with patch.dict(os.environ, {**env_clean, "VITE_READ_ONLY_CONNECTED_SMOKE": "true"}, clear=True):
        assert is_api_read_only() is True
    with patch.dict(os.environ, {**env_clean, "VITE_READ_ONLY_CONNECTED_SMOKE": "false"}, clear=True):
        assert is_api_read_only() is False

    # 4. Precedencia: GENARO_API_READ_ONLY='false' domina sobre VITE_READ_ONLY_CONNECTED_SMOKE='true'
    with patch.dict(
        os.environ,
        {"GENARO_API_READ_ONLY": "false", "VITE_READ_ONLY_CONNECTED_SMOKE": "true"},
        clear=False,
    ):
        assert is_api_read_only() is False

    # 5. Sin ninguna variable configurada: False por defecto
    with patch.dict(os.environ, {k: v for k, v in os.environ.items() if k not in ("GENARO_API_READ_ONLY", "VITE_READ_ONLY_CONNECTED_SMOKE")}, clear=True):
        assert is_api_read_only() is False


@pytest.fixture
def auth_setup(memory_db):
    """Configura cliente de prueba con autenticación y base de datos sobreescrita."""
    hash_alan = generate_password_hash(TEST_ALAN_PASS, iterations=1000)
    with patch.dict(
        os.environ,
        {
            "GENARO_AUTH_SECRET_KEY": TEST_SECRET,
            "GENARO_USER_ALAN_HASH": hash_alan,
        },
        clear=False,
    ):
        app.dependency_overrides[get_db_connection] = lambda: memory_db
        user_alan = AuthenticatedUser(username="alan", display_name="Alan", role="admin")
        app.dependency_overrides[get_current_user] = lambda: user_alan
        client = TestClient(app)
        yield client, memory_db, user_alan
        app.dependency_overrides.clear()


def test_bloqueo_escrituras_ventas_con_423(auth_setup):
    """Verifica que POST /api/sales sea rechazado con HTTP 423 Locked y sin mutaciones en BD."""
    client, conn, user = auth_setup
    token = create_access_token(user, secret_key=TEST_SECRET)
    headers = {"Authorization": f"Bearer {token}"}

    payload = {
        "ticket": {
            "ticket_id": "T-TEST-BLOCKED-001",
            "fecha": "25/10/2026 20:00:00",
            "total_venta": 2700,
            "monto_efectivo": 2700,
            "monto_transf": 0,
            "metodo_pago": "EFECTIVO",
        },
        "items": [
            {
                "producto": "361 1,5lts",
                "cantidad": 1.0,
                "precio_unit": 2700,
                "subtotal": 2700,
            }
        ],
    }

    with patch.dict(os.environ, {"GENARO_API_READ_ONLY": "true"}, clear=False):
        res = client.post("/api/sales", json=payload, headers=headers)
        assert res.status_code == 423, f"Se esperaba 423, obtenido: {res.status_code}"
        data = res.json()
        assert data.get("error_code") == "API_READ_ONLY_LOCKED"
        assert "modo de solo lectura" in data.get("detail", "")

        # Verificar que NO se escribió en tickets ni en ventas_auditoria
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM tickets WHERE id = 'T-TEST-BLOCKED-001'")
        assert c.fetchone()[0] == 0
        c.execute("SELECT COUNT(*) FROM ventas_auditoria")
        assert c.fetchone()[0] == 0


def test_bloqueo_escrituras_servicios_y_gastos_con_423(auth_setup):
    """Verifica que POST y DELETE en servicios y gastos retornen HTTP 423 Locked."""
    client, conn, user = auth_setup
    token = create_access_token(user, secret_key=TEST_SECRET)
    headers = {"Authorization": f"Bearer {token}"}

    with patch.dict(os.environ, {"GENARO_API_READ_ONLY": "true"}, clear=False):
        # 1. Crear servicio
        res_serv = client.post(
            "/api/services",
            json={"servicio": "TEST_SERV", "monto_carga": 100, "idempotency_key": "IDEMP-TEST-1"},
            headers=headers,
        )
        assert res_serv.status_code == 423
        assert res_serv.json()["error_code"] == "API_READ_ONLY_LOCKED"

        # 2. Anular servicio
        res_anul_serv = client.delete("/api/services/1", headers=headers)
        assert res_anul_serv.status_code == 423

        # 3. Crear gasto
        res_gasto = client.post(
            "/api/expenses",
            json={"detalle": "TEST_GASTO", "monto_salida": 50, "idempotency_key": "IDEMP-GASTO-1"},
            headers=headers,
        )
        assert res_gasto.status_code == 423

        # 4. Anular gasto
        res_anul_gasto = client.delete("/api/expenses/1", headers=headers)
        assert res_anul_gasto.status_code == 423

        # 5. Cierre diario
        res_cierre = client.post(
            "/api/expenses/cierre",
            json={"fecha": "2026-10-25", "total_ventas": 0, "total_gastos": 0, "saldo_caja": 0},
            headers=headers,
        )
        assert res_cierre.status_code == 423

        # Comprobar que ninguna tabla fue alterada
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM cargas_servicios")
        assert c.fetchone()[0] == 0
        c.execute("SELECT COUNT(*) FROM cargas_servicios_auditoria")
        assert c.fetchone()[0] == 0
        c.execute("SELECT COUNT(*) FROM gastos_retiros")
        assert c.fetchone()[0] == 0
        c.execute("SELECT COUNT(*) FROM gastos_auditoria")
        assert c.fetchone()[0] == 0
        c.execute("SELECT COUNT(*) FROM cierres_diarios")
        assert c.fetchone()[0] == 0


def test_post_login_permanece_permitido_en_read_only(auth_setup):
    """Verifica que POST /api/auth/login continúe funcionando para autenticar sesiones."""
    client, _, _ = auth_setup

    with patch.dict(os.environ, {"GENARO_API_READ_ONLY": "true"}, clear=False):
        res = client.post("/api/auth/login", json={"username": "alan", "password": TEST_ALAN_PASS})
        assert res.status_code == 200, f"Login falló con: {res.status_code} {res.text}"
        data = res.json()
        assert "access_token" in data
        assert data.get("token_type") == "bearer"
        assert data.get("user", {}).get("username") == "alan"


def test_consultas_get_y_options_permitidas_en_read_only(auth_setup):
    """Verifica que peticiones GET y OPTIONS operen con normalidad en modo de solo lectura."""
    client, _, user = auth_setup
    token = create_access_token(user, secret_key=TEST_SECRET)
    headers = {"Authorization": f"Bearer {token}"}

    with patch.dict(os.environ, {"GENARO_API_READ_ONLY": "true"}, clear=False):
        # 1. Health check público
        res_health = client.get("/health")
        assert res_health.status_code == 200

        # 2. Catálogo (GET /api/catalog)
        res_cat = client.get("/api/catalog", headers=headers)
        assert res_cat.status_code == 200

        # 3. Ventas
        res_ventas = client.get("/api/sales", headers=headers)
        assert res_ventas.status_code == 200

        # 4. Servicios
        res_serv = client.get("/api/services", headers=headers)
        assert res_serv.status_code == 200

        # 5. Gastos
        res_gastos = client.get("/api/expenses", headers=headers)
        assert res_gastos.status_code == 200

        # 6. Readiness
        res_ready = client.get("/api/system/readiness", headers=headers)
        assert res_ready.status_code == 200

        # 7. OPTIONS CORS preflight
        res_opt = client.options(
            "/api/sales",
            headers={"Origin": "http://127.0.0.1:5173", "Access-Control-Request-Method": "POST"},
        )
        assert res_opt.status_code == 200


def test_modo_desactivado_permite_operaciones_normales(auth_setup):
    """Verifica que cuando GENARO_API_READ_ONLY='false', las escrituras normales estén habilitadas."""
    client, conn, user = auth_setup
    token = create_access_token(user, secret_key=TEST_SECRET)
    headers = {"Authorization": f"Bearer {token}"}

    payload_serv = {
        "servicio": "TEST_OPERATIVO_NORMAL",
        "monto_carga": 500,
        "monto_adicional": 0,
        "total_cobrado": 500,
        "pago_efvo": 500,
        "pago_transf": 0,
        "idempotency_key": "IDEMP-NORMAL-001",
    }

    with patch.dict(os.environ, {"GENARO_API_READ_ONLY": "false"}, clear=False):
        res = client.post("/api/services", json=payload_serv, headers=headers)
        assert res.status_code == 201, f"Se esperaba 201 en modo normal, obtenido: {res.status_code}"
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM cargas_servicios WHERE idempotency_key = 'IDEMP-NORMAL-001'")
        assert c.fetchone()[0] == 1
