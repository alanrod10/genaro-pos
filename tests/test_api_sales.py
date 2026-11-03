"""Pruebas sin red para la API local de GenaroPOS (FastAPI) con idempotencia real.

Verifica de forma estrictamente local y sin red:
1. GET /health y diagnóstico sin exposición de credenciales.
2. GET /api/catalog respetando el contrato del adaptador y sin llamadas a Sheets.
3. POST /api/sales registrando transaccionalmente tickets e ítems (HTTP 201).
4. Idempotencia real: reintento idéntico retorna HTTP 200 sin registros adicionales.
5. Detección de colisiones: mismo ID con datos discordantes retorna HTTP 409 Conflict.
6. Recuperación tras timeout de respuesta y ausencia de falso éxito ante fallos reales.
7. Concurrencia simulada con múltiples solicitudes simultáneas para el mismo ticket.
8. Rechazo de payloads inválidos antes de persistir (HTTP 422).
9. Manejo seguro de excepciones sin filtración de credenciales (HTTP 400 y 500).
10. Ausencia total de conexiones remotas o llamadas a Google Sheets.
"""

from concurrent.futures import ThreadPoolExecutor
import http.client
import sqlite3
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.dependencies import AuthenticatedUser, get_current_user, get_db_connection
from api.main import app
from db.catalog import ErrorAccesoCatalogoTurso
from db.sales import ErrorVentasTurso


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_003 = ROOT / "db/migrations/003_ventas_tickets_outbox.sql"
MIGRATION_007 = ROOT / "db/migrations/007_sales_audit.sql"
MIGRATION_009 = ROOT / "db/migrations/009_ticket_item_cost_snapshot.sql"


@pytest.fixture
def sqlite_conn():
    """Base de datos SQLite en memoria con esquema de productos y migración 003."""
    db = sqlite3.connect(":memory:", check_same_thread=False)
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
            categoria TEXT DEFAULT 'Bebidas',
            proveedor TEXT DEFAULT 'Distribuidora Central',
            unidad TEXT DEFAULT 'Unidad',
            costo INTEGER DEFAULT 500,
            precio_dia INTEGER DEFAULT 1000,
            precio_noche INTEGER DEFAULT 1200,
            margen_ratio REAL DEFAULT 1.0,
            fecha_actualizacion TEXT DEFAULT '2026-10-24',
            activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1))
        );
        INSERT INTO _schema_migrations (version, nombre)
        VALUES
            (1, 'baseline_catalogo_productos_existente'),
            (2, 'crear_cargas_servicios');
        INSERT INTO productos (id, nombre, activo)
        VALUES
            (10, 'Gaseosa Cola', 1),
            (20, 'Alfajor Triple', 1),
            (30, 'Producto Desactivado', 0);
    """)
    db.executescript(MIGRATION_003.read_text(encoding="utf-8"))
    db.executescript(MIGRATION_007.read_text(encoding="utf-8"))
    db.executescript(MIGRATION_009.read_text(encoding="utf-8"))
    yield db
    db.close()


@pytest.fixture
def client(sqlite_conn):
    """Cliente de pruebas de FastAPI con inyección de conexión SQLite local."""
    app.dependency_overrides[get_db_connection] = lambda: sqlite_conn
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(username="alan", display_name="Alan")
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def block_network():
    """Garantiza que ningún test intente abrir sockets de red remotos."""
    def _bloquear(*args, **kwargs):
        raise RuntimeError("Intento de conexión de red remoto bloqueado en test de API.")

    with patch.object(http.client.HTTPConnection, "connect", side_effect=_bloquear):
        with patch.object(urllib.request, "urlopen", side_effect=_bloquear):
            yield


# ==============================================================================
# 1. GET /health
# ==============================================================================

def test_health_endpoint_basico(client):
    """Verifica el estado del proceso sin consultar base de datos."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "genaro-pos-api"
    assert data["version"] == "0.1.0"
    assert data["db_connectivity"] == "not_checked"


def test_health_endpoint_con_db_check(client):
    """Verifica la conectividad de la base inyectada cuando se solicita explícitamente."""
    resp = client.get("/health?check_db=true")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["db_connectivity"] == "connected_local"


def test_health_no_expone_credenciales_ni_rutas(client, monkeypatch):
    """Verifica que secretos en variables de entorno no se filtren en /health."""
    monkeypatch.setenv("TURSO_DEV_DATABASE_URL", "turso://secret-host.turso.io")
    monkeypatch.setenv("TURSO_DEV_AUTH_TOKEN", "super-secret-jwt-token")

    resp = client.get("/health?check_db=true")
    assert resp.status_code == 200
    body = resp.text
    assert "super-secret-jwt-token" not in body
    assert "secret-host.turso.io" not in body


# ==============================================================================
# 2. GET /api/catalog
# ==============================================================================

def test_catalog_endpoint_retorna_productos_activos(client):
    """Verifica que el catálogo devuelva solo productos activos con contrato estable."""
    resp = client.get("/api/catalog")
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "productos" in data
    assert data["total"] == 2  # Gaseosa Cola y Alfajor Triple (excluye Producto Desactivado)

    nombres = [p["nombre"] for p in data["productos"]]
    assert "Gaseosa Cola" in nombres
    assert "Alfajor Triple" in nombres
    assert "Producto Desactivado" not in nombres

    primer_item = data["productos"][0]
    for campo in ("id_producto", "nombre", "categoria", "costo", "precio_venta", "precio_dia", "precio_noche"):
        assert campo in primer_item
    assert primer_item["precio_venta"] == primer_item["precio_dia"]


def test_catalog_error_adaptador_retorna_503_seguro(client):
    """Verifica respuesta segura 503 ante caída del adaptador Turso."""
    with patch("api.routers.catalog.cargar_catalogo_turso_filas", side_effect=ErrorAccesoCatalogoTurso("Host inalcanzable")):
        resp = client.get("/api/catalog")
        assert resp.status_code == 503
        data = resp.json()
        assert "Host inalcanzable" not in data.get("detail", "")
        assert "no disponible" in data.get("detail", "").lower()


def test_catalog_error_inesperado_retorna_500_seguro(client):
    """Verifica que excepciones genéricas no filtren trazas internas."""
    with patch("api.routers.catalog.cargar_catalogo_turso_filas", side_effect=RuntimeError("disco dañado /var/lib/secret")):
        resp = client.get("/api/catalog")
        assert resp.status_code == 500
        data = resp.json()
        assert "secret" not in data.get("detail", "")
        assert "Error interno" in data.get("detail", "")


# ==============================================================================
# 3. POST /api/sales (Persistencia exitosa e integridad)
# ==============================================================================

def _payload_valido(**cambios_ticket):
    ticket = {
        "ticket_id": "T-API-001",
        "fecha": "24/10/2026 14:00:00",
        "total_venta": 1500,
        "monto_efectivo": 1000,
        "monto_transf": 500,
        "metodo_pago": "MIXTO",
    }
    ticket.update(cambios_ticket)
    items = [
        {
            "ticket_id": ticket["ticket_id"],
            "producto": "Gaseosa Cola",
            "cantidad": 1.0,
            "unidad": "Unidad",
            "precio_unit": 1000,
            "subtotal": 1000,
        },
        {
            "ticket_id": ticket["ticket_id"],
            "producto": "Alfajor Triple",
            "cantidad": 1.0,
            "unidad": "Unidad",
            "precio_unit": 500,
            "subtotal": 500,
        },
    ]
    return {"ticket": ticket, "items": items}


def test_sales_registro_exitoso_primera_solicitud(client, sqlite_conn):
    """Verifica el registro exitoso (HTTP 201) de una venta nueva."""
    payload = _payload_valido()
    resp = client.post("/api/sales", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["ticket_id"] == "T-API-001"
    assert data["status"] == "registrado"

    # Verificación en la base de datos SQLite
    cur = sqlite_conn.execute("SELECT id, total_venta, metodo_pago FROM tickets WHERE id = ?", ("T-API-001",))
    ticket_db = cur.fetchone()
    assert ticket_db is not None
    assert ticket_db[0] == "T-API-001"
    assert ticket_db[1] == 1500
    assert ticket_db[2] == "MIXTO"

    cur_items = sqlite_conn.execute(
        "SELECT producto_nombre, subtotal FROM ticket_items WHERE ticket_id = ? ORDER BY id",
        ("T-API-001",),
    )
    items_db = cur_items.fetchall()
    assert len(items_db) == 2
    assert items_db[0] == ("Gaseosa Cola", 1000)
    assert items_db[1] == ("Alfajor Triple", 500)


def test_sales_compatibilidad_con_claves_mayusculas(client, sqlite_conn):
    """Verifica que la API acepte también las claves en mayúsculas de los adaptadores existentes."""
    payload = {
        "TICKET": {
            "TICKET_ID": "T-API-UPPER-001",
            "FECHA": "24/10/2026 15:30:00",
            "TOTAL_VENTA": 1000,
            "MONTO_EFECTIVO": 1000,
            "MONTO_TRANSF": 0,
            "METODO_PAGO": "EFECTIVO",
        },
        "ITEMS": [
            {
                "TICKET_ID": "T-API-UPPER-001",
                "PRODUCTO": "Gaseosa Cola",
                "CANTIDAD": 1,
                "PRECIO_UNIT": 1000,
                "SUBTOTAL": 1000,
            }
        ],
    }
    resp = client.post("/api/sales", json=payload)
    assert resp.status_code == 201
    assert resp.json()["ticket_id"] == "T-API-UPPER-001"


def test_sales_cantidad_decimal_valida(client, sqlite_conn):
    """Verifica venta válida con cantidad fraccionaria (ej. peso o granel)."""
    payload = {
        "ticket": {
            "ticket_id": "T-API-DECIMAL",
            "fecha": "24/10/2026 16:00:00",
            "total_venta": 1500,
            "monto_efectivo": 1500,
            "monto_transf": 0,
            "metodo_pago": "EFECTIVO",
        },
        "items": [
            {
                "ticket_id": "T-API-DECIMAL",
                "producto": "Gaseosa Cola",
                "cantidad": 1.5,
                "unidad": "Kg",
                "precio_unit": 1000,
                "subtotal": 1500,
            }
        ],
    }
    resp = client.post("/api/sales", json=payload)
    assert resp.status_code == 201
    assert resp.json()["ticket_id"] == "T-API-DECIMAL"


# ==============================================================================
# 4. IDEMPOTENCIA REAL: REINTENTOS Y CONFLICTOS
# ==============================================================================

def test_sales_reintento_identico_retorna_200_sin_duplicados(client, sqlite_conn):
    """Verifica que un reintento del mismo payload retorne HTTP 200 sin insertar duplicados."""
    payload = _payload_valido(ticket_id="T-API-IDEM-001")

    # Primera llamada: 201 Created
    resp1 = client.post("/api/sales", json=payload)
    assert resp1.status_code == 201
    assert resp1.json()["status"] == "registrado"

    # Segunda llamada idéntica: 200 OK con status existente
    resp2 = client.post("/api/sales", json=payload)
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["ticket_id"] == "T-API-IDEM-001"
    assert data2["status"] == "existente"

    # Verificación en BD: exactamente 1 cabecera y 2 ítems en total
    cur_t = sqlite_conn.execute("SELECT COUNT(*) FROM tickets WHERE id = 'T-API-IDEM-001'")
    assert cur_t.fetchone()[0] == 1
    cur_i = sqlite_conn.execute("SELECT COUNT(*) FROM ticket_items WHERE ticket_id = 'T-API-IDEM-001'")
    assert cur_i.fetchone()[0] == 2


def test_sales_mismo_id_total_diferente_retorna_409(client, sqlite_conn):
    """Verifica que mismo ID con total diferente retorne 409 Conflict sin alterar la BD."""
    payload_original = _payload_valido(ticket_id="T-API-CONF-001", total_venta=1500, monto_efectivo=1500, monto_transf=0, metodo_pago="EFECTIVO")
    payload_original["items"] = [
        {"ticket_id": "T-API-CONF-001", "producto": "Gaseosa Cola", "cantidad": 1.0, "unidad": "Unidad", "precio_unit": 1500, "subtotal": 1500}
    ]

    resp1 = client.post("/api/sales", json=payload_original)
    assert resp1.status_code == 201

    # Intento con total discordante (total=2000)
    payload_modificado = _payload_valido(ticket_id="T-API-CONF-001", total_venta=2000, monto_efectivo=2000, monto_transf=0, metodo_pago="EFECTIVO")
    payload_modificado["items"] = [
        {"ticket_id": "T-API-CONF-001", "producto": "Gaseosa Cola", "cantidad": 1.0, "unidad": "Unidad", "precio_unit": 2000, "subtotal": 2000}
    ]

    resp2 = client.post("/api/sales", json=payload_modificado)
    assert resp2.status_code == 409
    assert "contenido diferente" in resp2.json().get("detail", "").lower()

    # Base de datos intacta con valor original (1500)
    cur = sqlite_conn.execute("SELECT total_venta FROM tickets WHERE id = 'T-API-CONF-001'")
    assert cur.fetchone()[0] == 1500


def test_sales_mismo_id_item_producto_diferente_retorna_409(client, sqlite_conn):
    """Verifica que mismo ID y total pero con producto diferente retorne 409 Conflict."""
    payload1 = _payload_valido(ticket_id="T-API-CONF-PROD", total_venta=1000, monto_efectivo=1000, monto_transf=0, metodo_pago="EFECTIVO")
    payload1["items"] = [
        {"ticket_id": "T-API-CONF-PROD", "producto": "Gaseosa Cola", "cantidad": 1.0, "unidad": "Unidad", "precio_unit": 1000, "subtotal": 1000}
    ]
    resp1 = client.post("/api/sales", json=payload1)
    assert resp1.status_code == 201

    # Mismo ID y total, pero producto "Alfajor Triple"
    payload2 = _payload_valido(ticket_id="T-API-CONF-PROD", total_venta=1000, monto_efectivo=1000, monto_transf=0, metodo_pago="EFECTIVO")
    payload2["items"] = [
        {"ticket_id": "T-API-CONF-PROD", "producto": "Alfajor Triple", "cantidad": 2.0, "unidad": "Unidad", "precio_unit": 500, "subtotal": 1000}
    ]
    resp2 = client.post("/api/sales", json=payload2)
    assert resp2.status_code == 409
    assert "contenido diferente" in resp2.json().get("detail", "").lower()


def test_sales_mismo_id_metodo_pago_diferente_retorna_409(client, sqlite_conn):
    """Verifica que mismo ID pero con distinto método de pago retorne 409 Conflict."""
    payload1 = _payload_valido(ticket_id="T-API-CONF-PAGO", total_venta=1000, monto_efectivo=1000, monto_transf=0, metodo_pago="EFECTIVO")
    payload1["items"] = [
        {"ticket_id": "T-API-CONF-PAGO", "producto": "Gaseosa Cola", "cantidad": 1.0, "unidad": "Unidad", "precio_unit": 1000, "subtotal": 1000}
    ]
    resp1 = client.post("/api/sales", json=payload1)
    assert resp1.status_code == 201

    payload2 = _payload_valido(ticket_id="T-API-CONF-PAGO", total_venta=1000, monto_efectivo=0, monto_transf=1000, metodo_pago="TRANSFERENCIA")
    payload2["items"] = [
        {"ticket_id": "T-API-CONF-PAGO", "producto": "Gaseosa Cola", "cantidad": 1.0, "unidad": "Unidad", "precio_unit": 1000, "subtotal": 1000}
    ]
    resp2 = client.post("/api/sales", json=payload2)
    assert resp2.status_code == 409


def test_sales_mismo_id_cantidad_item_diferente_retorna_409(client, sqlite_conn):
    """Verifica que mismo ID pero con distinta cantidad y precio de ítem retorne 409 Conflict."""
    payload1 = _payload_valido(ticket_id="T-API-CONF-CANT", total_venta=1000, monto_efectivo=1000, monto_transf=0, metodo_pago="EFECTIVO")
    payload1["items"] = [
        {"ticket_id": "T-API-CONF-CANT", "producto": "Gaseosa Cola", "cantidad": 1.0, "unidad": "Unidad", "precio_unit": 1000, "subtotal": 1000}
    ]
    resp1 = client.post("/api/sales", json=payload1)
    assert resp1.status_code == 201

    # Mismo total (1000) pero cantidad 2 y precio 500
    payload2 = _payload_valido(ticket_id="T-API-CONF-CANT", total_venta=1000, monto_efectivo=1000, monto_transf=0, metodo_pago="EFECTIVO")
    payload2["items"] = [
        {"ticket_id": "T-API-CONF-CANT", "producto": "Gaseosa Cola", "cantidad": 2.0, "unidad": "Unidad", "precio_unit": 500, "subtotal": 1000}
    ]
    resp2 = client.post("/api/sales", json=payload2)
    assert resp2.status_code == 409
    assert "contenido diferente" in resp2.json().get("detail", "").lower()


def test_sales_mismo_id_fecha_diferente_retorna_409(client, sqlite_conn):
    """Verifica que mismo ID pero con fecha relevante distinta retorne 409 Conflict."""
    payload1 = _payload_valido(ticket_id="T-API-CONF-FECHA", fecha="24/10/2026 10:00:00")
    resp1 = client.post("/api/sales", json=payload1)
    assert resp1.status_code == 201

    payload2 = _payload_valido(ticket_id="T-API-CONF-FECHA", fecha="24/10/2026 12:00:00")
    resp2 = client.post("/api/sales", json=payload2)
    assert resp2.status_code == 409
    assert "contenido diferente" in resp2.json().get("detail", "").lower()



def test_sales_recuperacion_tras_timeout_red(client, sqlite_conn):
    """Simula pérdida de respuesta en primer intento: el reintento recupera el ticket con 200 OK."""
    payload = _payload_valido(ticket_id="T-API-TIMEOUT-001")

    # Inserción inicial exitosa
    resp1 = client.post("/api/sales", json=payload)
    assert resp1.status_code == 201

    # Reintento del cliente tras timeout:
    resp2 = client.post("/api/sales", json=payload)
    assert resp2.status_code == 200
    assert resp2.json()["ticket_id"] == "T-API-TIMEOUT-001"
    assert resp2.json()["status"] == "existente"

    # Conteos en base de datos sin duplicados
    cur_t = sqlite_conn.execute("SELECT COUNT(*) FROM tickets WHERE id = 'T-API-TIMEOUT-001'")
    assert cur_t.fetchone()[0] == 1


def test_sales_error_bd_sin_registro_retorna_500_sin_falso_exito(client):
    """Verifica que un fallo de base de datos sin registro guardado devuelva HTTP 500 sin simular éxito."""
    with patch("api.routers.sales.registrar_venta_idempotente_turso", side_effect=RuntimeError("Fallo catastrófico de disco")):
        payload = _payload_valido(ticket_id="T-API-ERR-DISCO")
        resp = client.post("/api/sales", json=payload)
        assert resp.status_code == 500
        assert "Error interno" in resp.json().get("detail", "")


def test_sales_solicitudes_concurrentes_mismo_id(tmp_path):
    """Simula dos solicitudes concurrentes para el mismo ticket ejecutadas en paralelo."""
    db_file = tmp_path / "concurrent_sales.db"
    init_conn = sqlite3.connect(db_file)
    init_conn.execute("PRAGMA journal_mode = WAL")
    init_conn.execute("CREATE TABLE _schema_migrations (version INT, nombre TEXT)")
    init_conn.execute("CREATE TABLE productos (id INTEGER PRIMARY KEY, nombre TEXT, activo INT)")
    init_conn.executescript(MIGRATION_003.read_text(encoding="utf-8"))
    init_conn.executescript(MIGRATION_007.read_text(encoding="utf-8"))
    init_conn.executescript(MIGRATION_009.read_text(encoding="utf-8"))
    init_conn.commit()
    init_conn.close()

    app.dependency_overrides[get_db_connection] = lambda: sqlite3.connect(db_file, timeout=10.0)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(username="alan", display_name="Alan")
    payload = _payload_valido(ticket_id="T-API-CONCURRENT")

    def _enviar():
        with TestClient(app) as local_client:
            return local_client.post("/api/sales", json=payload)

    with ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(_enviar)
        f2 = executor.submit(_enviar)
        r1 = f1.result()
        r2 = f2.result()

    app.dependency_overrides.clear()

    # Uno debe responder 201 Created y el concurrente 200 OK (idempotente)
    statuses = sorted([r1.status_code, r2.status_code])
    assert statuses == [200, 201], f"Se esperaba [200, 201], se obtuvo {statuses}"

    check_conn = sqlite3.connect(db_file)
    cur_t = check_conn.execute("SELECT COUNT(*) FROM tickets WHERE id = 'T-API-CONCURRENT'")
    assert cur_t.fetchone()[0] == 1
    cur_i = check_conn.execute("SELECT COUNT(*) FROM ticket_items WHERE ticket_id = 'T-API-CONCURRENT'")
    assert cur_i.fetchone()[0] == 2
    check_conn.close()


# ==============================================================================
# 5. VALIDACIONES PREVIAS A LA PERSISTENCIA (HTTP 422)
# ==============================================================================

def test_sales_rechaza_lista_items_vacia(client):
    payload = _payload_valido()
    payload["items"] = []
    resp = client.post("/api/sales", json=payload)
    assert resp.status_code == 422


def test_sales_rechaza_subtotal_incoherente(client):
    payload = _payload_valido()
    # 1 * 1000 debería ser 1000, pero se envía 999
    payload["items"][0]["subtotal"] = 999
    resp = client.post("/api/sales", json=payload)
    assert resp.status_code == 422
    assert "subtotal" in resp.text.lower()


def test_sales_rechaza_suma_subtotales_distinta_del_total(client):
    payload = _payload_valido(total_venta=2000, monto_efectivo=2000, monto_transf=0, metodo_pago="EFECTIVO")
    # Subtotales suman 1500, pero el total del ticket dice 2000
    resp = client.post("/api/sales", json=payload)
    assert resp.status_code == 422
    assert "no coincide con el total" in resp.text.lower()


def test_sales_rechaza_balance_pagos_invalido(client):
    payload = _payload_valido(total_venta=1500, monto_efectivo=1000, monto_transf=1000)
    # 1000 + 1000 != 1500
    resp = client.post("/api/sales", json=payload)
    assert resp.status_code == 422
    assert "coincidir" in resp.text.lower()


def test_sales_rechaza_metodo_efectivo_con_transferencia(client):
    payload = _payload_valido(
        total_venta=1500,
        monto_efectivo=1000,
        monto_transf=500,
        metodo_pago="EFECTIVO",
    )
    resp = client.post("/api/sales", json=payload)
    assert resp.status_code == 422
    assert "efectivo" in resp.text.lower()


def test_sales_rechaza_metodo_transferencia_con_efectivo(client):
    payload = _payload_valido(
        total_venta=1500,
        monto_efectivo=500,
        monto_transf=1000,
        metodo_pago="TRANSFERENCIA",
    )
    resp = client.post("/api/sales", json=payload)
    assert resp.status_code == 422
    assert "transferencia" in resp.text.lower()


def test_sales_rechaza_metodo_pago_desconocido(client):
    payload = _payload_valido(metodo_pago="CRIPTOMONEDA")
    resp = client.post("/api/sales", json=payload)
    assert resp.status_code == 422
    assert "no permitido" in resp.text.lower()


def test_sales_rechaza_item_con_ticket_id_discordante(client):
    payload = _payload_valido(ticket_id="T-API-ORIGINAL")
    payload["items"][0]["ticket_id"] = "T-API-OTRO-TICKET"
    resp = client.post("/api/sales", json=payload)
    assert resp.status_code == 422
    assert "no coincide" in resp.text.lower()


def test_sales_rechaza_ticket_id_vacio(client):
    payload = _payload_valido(ticket_id="   ")
    resp = client.post("/api/sales", json=payload)
    assert resp.status_code == 422


def test_sales_rechaza_producto_vacio(client):
    payload = _payload_valido()
    payload["items"][0]["producto"] = "   "
    resp = client.post("/api/sales", json=payload)
    assert resp.status_code == 422


def test_sales_rechaza_cantidad_cero_o_negativa(client):
    payload = _payload_valido()
    payload["items"][0]["cantidad"] = 0
    resp = client.post("/api/sales", json=payload)
    assert resp.status_code == 422


# ==============================================================================
# 6. EXCEPCIONES DEL ADAPTADOR Y RESPUESTAS HTTP SEGURAS
# ==============================================================================

def test_sales_error_adaptador_negocio_retorna_400(client):
    """Verifica que ErrorVentasTurso emitido por el adaptador se mapee a HTTP 400."""
    with patch("api.routers.sales.registrar_venta_idempotente_turso", side_effect=ErrorVentasTurso("Fallo de negocio en adaptador")):
        payload = _payload_valido()
        resp = client.post("/api/sales", json=payload)
        assert resp.status_code == 400
        data = resp.json()
        assert "Fallo de negocio en adaptador" in data.get("detail", "")


def test_sales_error_inesperado_retorna_500_sin_fugas(client):
    """Verifica que errores no controlados no revelen credenciales ni trazas."""
    with patch("api.routers.sales.registrar_venta_idempotente_turso", side_effect=RuntimeError("Token secreto JWT: abc.123.xyz")):
        payload = _payload_valido()
        resp = client.post("/api/sales", json=payload)
        assert resp.status_code == 500
        data = resp.json()
        assert "abc.123.xyz" not in resp.text
        assert "Error interno" in data.get("detail", "")


# ==============================================================================
# 7. AUSENCIA DE LLAMADAS A GOOGLE SHEETS Y VERIFICACIÓN OFFLINE
# ==============================================================================

def test_sin_acceso_a_google_sheets(client):
    """Verifica que los endpoints de la API no intenten interactuar con Google Sheets."""
    with patch("app.st.connection", side_effect=RuntimeError("Llamada no autorizada a st.connection en API")):
        # Health
        resp_h = client.get("/health")
        assert resp_h.status_code == 200

        # Catalog
        resp_c = client.get("/api/catalog")
        assert resp_c.status_code == 200

        # Sales
        resp_s = client.post("/api/sales", json=_payload_valido(ticket_id="T-API-NO-SHEETS"))
        assert resp_s.status_code == 201


# ==============================================================================
# 8. HISTORIAL, DETALLE, ANULACIÓN Y EDICIÓN DE ÍTEMS DE VENTA
# ==============================================================================

def test_historial_listar_ventas(client):
    """Verifica listar ventas registradas con conteo de ítems."""
    client.post("/api/sales", json=_payload_valido(ticket_id="T-HIST-001"))
    client.post("/api/sales", json=_payload_valido(ticket_id="T-HIST-002"))

    resp = client.get("/api/sales")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 2
    ids = [v["ticket_id"] for v in data["ventas"]]
    assert "T-HIST-001" in ids
    assert "T-HIST-002" in ids


def test_historial_busqueda_y_filtros(client):
    """Verifica filtrado por ID de ticket, nombre de producto y medio de pago."""
    # Venta 1: Efectivo con 'Gaseosa Cola'
    client.post(
        "/api/sales",
        json={
            "ticket": {
                "ticket_id": "T-FILT-EFVO",
                "fecha": "2026-10-24 10:00:00",
                "total_venta": 3000,
                "monto_efectivo": 3000,
                "monto_transf": 0,
                "metodo_pago": "EFECTIVO",
            },
            "items": [
                {
                    "producto": "Gaseosa Cola",
                    "cantidad": 3,
                    "unidad": "Unidad",
                    "precio_unit": 1000,
                    "subtotal": 3000,
                }
            ],
        },
    )
    # Venta 2: Transferencia con 'Alfajor Triple'
    client.post(
        "/api/sales",
        json={
            "ticket": {
                "ticket_id": "T-FILT-TRANSF",
                "fecha": "2026-10-24 11:00:00",
                "total_venta": 2000,
                "monto_efectivo": 0,
                "monto_transf": 2000,
                "metodo_pago": "TRANSFERENCIA",
            },
            "items": [
                {
                    "producto": "Alfajor Triple",
                    "cantidad": 2,
                    "unidad": "Unidad",
                    "precio_unit": 1000,
                    "subtotal": 2000,
                }
            ],
        },
    )

    # Filtro por método de pago
    resp_efvo = client.get("/api/sales?metodo_pago=EFECTIVO")
    assert resp_efvo.status_code == 200
    assert any(v["ticket_id"] == "T-FILT-EFVO" for v in resp_efvo.json()["ventas"])
    assert not any(v["ticket_id"] == "T-FILT-TRANSF" for v in resp_efvo.json()["ventas"])

    # Búsqueda por nombre de producto
    resp_prod = client.get("/api/sales?busqueda=Alfajor")
    assert resp_prod.status_code == 200
    assert any(v["ticket_id"] == "T-FILT-TRANSF" for v in resp_prod.json()["ventas"])
    assert not any(v["ticket_id"] == "T-FILT-EFVO" for v in resp_prod.json()["ventas"])

    # Búsqueda por ID de ticket
    resp_tid = client.get("/api/sales?busqueda=FILT-EFVO")
    assert resp_tid.status_code == 200
    assert len(resp_tid.json()["ventas"]) == 1
    assert resp_tid.json()["ventas"][0]["ticket_id"] == "T-FILT-EFVO"


def test_obtener_detalle_venta(client):
    """Verifica consultar detalle exhaustivo de ticket con ítems."""
    client.post("/api/sales", json=_payload_valido(ticket_id="T-DET-001"))

    resp = client.get("/api/sales/T-DET-001")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ticket_id"] == "T-DET-001"
    assert len(data["items"]) == 2
    assert data["items"][0]["producto_nombre"] in {"Gaseosa Cola", "Alfajor Triple"}

    # Inexistente retorna 404
    resp_404 = client.get("/api/sales/T-NO-EXISTE")
    assert resp_404.status_code == 404


def test_anular_venta_con_auditoria(client):
    """Verifica anulación lógica de venta con motivo y registro inmutable en auditoría."""
    client.post("/api/sales", json=_payload_valido(ticket_id="T-ANUL-001"))

    resp_anular = client.post(
        "/api/sales/T-ANUL-001/anular",
        json={"motivo": "Cliente devolvió la mercadería", "usuario": "Alan"},
    )
    assert resp_anular.status_code == 200
    data = resp_anular.json()
    assert data["anulado"] is True
    assert data["motivo_anulacion"] == "Cliente devolvió la mercadería"
    assert len(data["auditoria"]) == 2
    acciones = [a["accion"] for a in data["auditoria"]]
    assert "CREACION" in acciones
    assert "ANULACION" in acciones
    anul_ev = [a for a in data["auditoria"] if a["accion"] == "ANULACION"][0]
    assert anul_ev["usuario"] == "alan"

    # Verificar que al listar con incluir_anuladas=False queda excluida
    resp_list = client.get("/api/sales?incluir_anuladas=false")
    assert not any(v["ticket_id"] == "T-ANUL-001" for v in resp_list.json()["ventas"])


def test_editar_item_venta_recalcula_ticket_con_regla_legacy(client):
    """Verifica que modificar un ítem recalcule la cabecera preservando las reglas contables."""
    # Venta inicial total 1500: efvo 1000, transf 500 (Gaseosa $1000 + Alfajor $500)
    client.post("/api/sales", json=_payload_valido(ticket_id="T-EDIT-001"))

    # Obtener ID del primer ítem (Gaseosa Cola)
    detalle = client.get("/api/sales/T-EDIT-001").json()
    item_target = detalle["items"][0]
    item_id = item_target["id"]

    # Incrementar cantidad a 2 y subtotal a 2000 (diff = -1000)
    resp_edit = client.put(
        f"/api/sales/T-EDIT-001/items/{item_id}",
        json={"nueva_cantidad": 2.0, "nuevo_subtotal": 2000, "usuario": "Alan"},
    )
    assert resp_edit.status_code == 200
    nuevo_detalle = resp_edit.json()
    # Total nuevo debe ser 2500 (2000 + 500)
    assert nuevo_detalle["total_venta"] == 2500
    assert len(nuevo_detalle["auditoria"]) == 2
    acciones_edit = [a["accion"] for a in nuevo_detalle["auditoria"]]
    assert "CREACION" in acciones_edit
    assert "EDICION_ITEM" in acciones_edit
    edit_ev = [a for a in nuevo_detalle["auditoria"] if a["accion"] == "EDICION_ITEM"][0]
    assert edit_ev["usuario"] == "alan"


def test_listar_ventas_filtro_fecha_respeta_zona_horaria_argentina(client):
    """P1-02: Verifica que GET /api/sales?fecha=... filtre según la fecha local de Argentina
    (America/Argentina/Jujuy, UTC-3), incluyendo ventas realizadas a las 22:30 AR
    (01:30 UTC del día siguiente) y respetando el borde exacto 00:00:00 AR."""
    # Venta 1: 25/10/2026 00:00:00 AR -> 2026-10-25 03:00:00 UTC
    client.post(
        "/api/sales",
        json=_payload_valido(ticket_id="T-AR-0000", fecha="25/10/2026 00:00:00"),
    )
    # Venta 2: 25/10/2026 22:30:00 AR -> 2026-10-26 01:30:00 UTC
    client.post(
        "/api/sales",
        json=_payload_valido(ticket_id="T-AR-2230", fecha="25/10/2026 22:30:00"),
    )
    # Venta 3: 25/10/2026 23:59:59 AR -> 2026-10-26 02:59:59 UTC
    client.post(
        "/api/sales",
        json=_payload_valido(ticket_id="T-AR-2359", fecha="25/10/2026 23:59:59"),
    )
    # Venta 4: 26/10/2026 00:00:00 AR -> 2026-10-26 03:00:00 UTC
    client.post(
        "/api/sales",
        json=_payload_valido(ticket_id="T-AR-NEXT-0000", fecha="26/10/2026 00:00:00"),
    )

    # Consulta del 2026-10-25 (formato ISO YYYY-MM-DD y formato DD/MM/YYYY)
    for filtro in ("2026-10-25", "25/10/2026"):
        resp_25 = client.get(f"/api/sales?fecha={filtro}")
        assert resp_25.status_code == 200
        ids_25 = {v["ticket_id"] for v in resp_25.json()["ventas"]}
        assert ids_25 == {"T-AR-0000", "T-AR-2230", "T-AR-2359"}

    # Consulta del 2026-10-26: solo debe incluir T-AR-NEXT-0000
    resp_26 = client.get("/api/sales?fecha=2026-10-26")
    assert resp_26.status_code == 200
    ids_26 = {v["ticket_id"] for v in resp_26.json()["ventas"]}
    assert ids_26 == {"T-AR-NEXT-0000"}
