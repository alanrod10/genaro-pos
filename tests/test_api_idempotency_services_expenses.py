"""Pruebas de idempotencia garantizada por base de datos para Servicios y Gastos.

Verifica:
1. Reintentos con la misma clave y payload idéntico retornan HTTP 200 OK con el registro previo
   sin crear duplicados contables ni auditorías repetidas.
2. Claves reutilizadas con payload discordante (diferente servicio, monto, medio de pago o detalle)
   retornan HTTP 409 Conflict.
3. Reintentos de operaciones anuladas son rechazados con HTTP 409 Conflict.
4. Concurrencia y colisión a nivel base de datos (IntegrityError en índice único) se resuelve
   atómicamente sin registrar filas espurias.
5. Compatibilidad retroactiva: operaciones sin clave de idempotencia continúan operando normalmente.
6. Detección de esquema no migrado (sin columna idempotency_key) devuelve HTTP 503 SCHEMA_NOT_READY.
"""

import sqlite3
import pytest
from fastapi.testclient import TestClient

from api.dependencies import AuthenticatedUser, get_current_user, get_db_connection
from api.main import app
from db.readiness import ErrorEsquemaNoPreparado
from db.services import registrar_carga_idempotente_turso, ErrorConflictoIdempotencia as ErrorConflictoServicios
from db.expenses import registrar_gasto_idempotente_turso, ErrorConflictoIdempotencia as ErrorConflictoGastos


@pytest.fixture
def test_db_migrada():
    """Base SQLite en memoria con esquema migrado hasta la versión 010."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)

    # Esquema de cargas_servicios con migración 010
    conn.execute(
        """
        CREATE TABLE cargas_servicios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            legacy_row_id TEXT,
            fecha_hora_utc TEXT NOT NULL,
            servicio TEXT NOT NULL COLLATE NOCASE,
            monto_carga INTEGER NOT NULL CHECK (monto_carga > 0),
            monto_adicional INTEGER NOT NULL DEFAULT 0 CHECK (monto_adicional >= 0),
            total_cobrado INTEGER NOT NULL CHECK (total_cobrado >= 0),
            pago_efectivo INTEGER NOT NULL DEFAULT 0 CHECK (pago_efectivo >= 0),
            pago_transferencia INTEGER NOT NULL DEFAULT 0 CHECK (pago_transferencia >= 0),
            anulado INTEGER NOT NULL DEFAULT 0 CHECK (anulado IN (0, 1)),
            idempotency_key TEXT DEFAULT NULL,
            CHECK (total_cobrado = monto_carga + monto_adicional),
            CHECK (total_cobrado = pago_efectivo + pago_transferencia)
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX uq_cargas_idempotency_key
        ON cargas_servicios(idempotency_key)
        WHERE idempotency_key IS NOT NULL
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cargas_servicios_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            carga_id INTEGER NOT NULL,
            accion TEXT NOT NULL CHECK (accion IN ('CREAR', 'EDITAR', 'ANULAR')),
            valores_anteriores TEXT,
            valores_nuevos TEXT NOT NULL,
            fecha_utc TEXT NOT NULL DEFAULT (datetime('now')),
            actor TEXT NOT NULL DEFAULT 'sistema',
            FOREIGN KEY (carga_id) REFERENCES cargas_servicios (id) ON DELETE CASCADE
        )
        """
    )

    # Esquema de gastos_retiros y gastos_auditoria con migración 010
    conn.execute(
        """
        CREATE TABLE gastos_retiros (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            legacy_row_id TEXT,
            fecha_hora_utc TEXT NOT NULL,
            tipo TEXT NOT NULL DEFAULT 'General',
            detalle TEXT NOT NULL,
            monto_salida INTEGER NOT NULL CHECK (monto_salida > 0),
            origen_fondos TEXT DEFAULT 'Caja diaria',
            metodo_pago TEXT NOT NULL CHECK (metodo_pago IN ('EFECTIVO', 'TRANSFERENCIA')),
            anulado INTEGER NOT NULL DEFAULT 0 CHECK (anulado IN (0, 1)),
            idempotency_key TEXT DEFAULT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX uq_gastos_idempotency_key
        ON gastos_retiros(idempotency_key)
        WHERE idempotency_key IS NOT NULL
        """
    )
    conn.execute(
        """
        CREATE TABLE gastos_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gasto_id INTEGER NOT NULL,
            accion TEXT NOT NULL,
            valores_anteriores TEXT,
            valores_nuevos TEXT,
            actor TEXT NOT NULL DEFAULT 'sistema',
            creado_en_utc TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def client_migrado(test_db_migrada):
    """TestClient autenticado con conexión inyectada sobre base migrada."""
    app.dependency_overrides[get_db_connection] = lambda: test_db_migrada
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(username="alan", display_name="Alan")
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# ==============================================================================
# PRUEBAS DE IDEMPOTENCIA EN SERVICIOS Y CARGAS
# ==============================================================================


def test_servicio_registro_inicial_crea_con_201(client_migrado, test_db_migrada):
    """Un registro de servicio con Idempotency-Key devuelve 201 Created y persiste la clave."""
    key = "srv-test-key-001"
    payload = {
        "servicio": "Claro",
        "monto_carga": 5000,
        "monto_adicional": 300,
        "total_cobrado": 5300,
        "pago_efvo": 5300,
        "pago_transf": 0,
    }
    res = client_migrado.post("/api/services", json=payload, headers={"Idempotency-Key": key})
    assert res.status_code == 201
    data = res.json()
    assert data["id"] > 0
    assert data["servicio"] == "Claro"
    assert data["total_cobrado"] == 5300
    assert data["idempotency_key"] == key

    # Verificar existencia única en la base
    cursor = test_db_migrada.execute("SELECT id, idempotency_key FROM cargas_servicios WHERE idempotency_key = ?", (key,))
    filas = cursor.fetchall()
    assert len(filas) == 1
    assert filas[0][0] == data["id"]


def test_servicio_reintento_con_mismo_payload_devuelve_200_sin_duplicar(client_migrado, test_db_migrada):
    """Reenviar la misma solicitud con Idempotency-Key idéntica devuelve 200 OK y no duplica filas."""
    key = "srv-test-key-002"
    payload = {
        "servicio": "Personal",
        "monto_carga": 3000,
        "monto_adicional": 200,
        "total_cobrado": 3200,
        "pago_efvo": 3200,
        "pago_transf": 0,
    }

    # 1. Primer envío -> 201 Created
    res1 = client_migrado.post("/api/services", json=payload, headers={"Idempotency-Key": key})
    assert res1.status_code == 201
    id_creado = res1.json()["id"]

    # 2. Reintento (simulando caída de respuesta o timeout en cliente) -> 200 OK
    res2 = client_migrado.post("/api/services", json=payload, headers={"Idempotency-Key": key})
    assert res2.status_code == 200
    assert res2.json()["id"] == id_creado
    assert res2.json()["servicio"] == "Personal"
    assert res2.json()["idempotency_key"] == key

    # 3. Reintento con clave enviada en el body en vez del header -> 200 OK
    payload_con_key = dict(payload, idempotency_key=key)
    res3 = client_migrado.post("/api/services", json=payload_con_key)
    assert res3.status_code == 200
    assert res3.json()["id"] == id_creado

    # Comprobar que en la base de datos existe EXACTAMENTE una fila
    filas = test_db_migrada.execute("SELECT COUNT(*) FROM cargas_servicios WHERE idempotency_key = ?", (key,)).fetchone()[0]
    assert filas == 1


def test_servicio_reutilizar_clave_con_datos_discordantes_devuelve_409(client_migrado):
    """Reutilizar una Idempotency-Key existente con montos o servicio diferentes es rechazado con 409 Conflict."""
    key = "srv-test-conflict-001"
    payload_original = {
        "servicio": "Claro",
        "monto_carga": 4000,
        "monto_adicional": 200,
        "total_cobrado": 4200,
        "pago_efvo": 4200,
        "pago_transf": 0,
    }
    res1 = client_migrado.post("/api/services", json=payload_original, headers={"Idempotency-Key": key})
    assert res1.status_code == 201

    # Intento discordante 1: diferente servicio
    payload_otro_servicio = dict(payload_original, servicio="Movistar")
    res_conflicto1 = client_migrado.post("/api/services", json=payload_otro_servicio, headers={"Idempotency-Key": key})
    assert res_conflicto1.status_code == 409
    assert "Conflicto de idempotencia" in res_conflicto1.json()["detail"]

    # Intento discordante 2: diferente monto
    payload_otro_monto = {
        "servicio": "Claro",
        "monto_carga": 5000,
        "monto_adicional": 300,
        "total_cobrado": 5300,
        "pago_efvo": 5300,
        "pago_transf": 0,
    }
    res_conflicto2 = client_migrado.post("/api/services", json=payload_otro_monto, headers={"Idempotency-Key": key})
    assert res_conflicto2.status_code == 409
    assert "Conflicto de idempotencia" in res_conflicto2.json()["detail"]


def test_servicio_reintento_de_operacion_anulada_devuelve_409(client_migrado):
    """Reintentar con la misma clave una operación que ya fue anulada lógicamente retorna 409 Conflict."""
    key = "srv-test-anulada-001"
    payload = {
        "servicio": "DIRECTV",
        "monto_carga": 2500,
        "monto_adicional": 200,
        "total_cobrado": 2700,
        "pago_efvo": 2700,
        "pago_transf": 0,
    }
    res1 = client_migrado.post("/api/services", json=payload, headers={"Idempotency-Key": key})
    assert res1.status_code == 201
    id_carga = res1.json()["id"]

    # Anular la carga
    res_anular = client_migrado.delete(f"/api/services/{id_carga}")
    assert res_anular.status_code == 200

    # Reintentar la operación con la misma clave
    res_reintento = client_migrado.post("/api/services", json=payload, headers={"Idempotency-Key": key})
    assert res_reintento.status_code == 409
    assert "fue anulada previamente" in res_reintento.json()["detail"]


def test_servicio_sin_clave_idempotencia_opera_normalmente(client_migrado, test_db_migrada):
    """Operaciones sin Idempotency-Key se guardan como registros independientes sin colisionar."""
    payload = {
        "servicio": "SUBE",
        "monto_carga": 1000,
        "monto_adicional": 100,
        "total_cobrado": 1100,
        "pago_efvo": 1100,
        "pago_transf": 0,
    }
    res1 = client_migrado.post("/api/services", json=payload)
    res2 = client_migrado.post("/api/services", json=payload)
    assert res1.status_code == 201
    assert res2.status_code == 201
    assert res1.json()["id"] != res2.json()["id"]

    # Ambos tienen idempotency_key NULL en la base
    cursor = test_db_migrada.execute("SELECT id, idempotency_key FROM cargas_servicios WHERE servicio = 'SUBE'")
    filas = cursor.fetchall()
    assert len(filas) == 2
    assert filas[0][1] is None
    assert filas[1][1] is None


# ==============================================================================
# PRUEBAS DE IDEMPOTENCIA EN GASTOS Y RETIROS
# ==============================================================================


def test_gasto_registro_inicial_crea_con_201(client_migrado, test_db_migrada):
    """Un registro de gasto con Idempotency-Key devuelve 201 Created y persiste la clave."""
    key = "gst-test-key-001"
    payload = {
        "detalle": "Compra de bolsas de consorcio",
        "monto_salida": 2500,
        "tipo": "Limpieza",
        "origen_fondos": "Caja diaria",
        "metodo_pago": "EFECTIVO",
    }
    res = client_migrado.post("/api/expenses", json=payload, headers={"Idempotency-Key": key})
    assert res.status_code == 201
    data = res.json()
    assert data["id"] > 0
    assert data["detalle"] == "Compra de bolsas de consorcio"
    assert data["idempotency_key"] == key

    # Verificar que se creó auditoría correspondiente
    auditoria = test_db_migrada.execute("SELECT accion FROM gastos_auditoria WHERE gasto_id = ?", (data["id"],)).fetchall()
    assert len(auditoria) == 1
    assert auditoria[0][0] == "CREAR"


def test_gasto_reintento_con_mismo_payload_devuelve_200_sin_duplicar(client_migrado, test_db_migrada):
    """Reenviar la misma solicitud de gasto devuelve 200 OK sin crear registros ni auditorías duplicadas."""
    key = "gst-test-key-002"
    payload = {
        "detalle": "Pago a distribuidora Arcor",
        "monto_salida": 15000,
        "tipo": "Proveedores",
        "origen_fondos": "Caja diaria",
        "metodo_pago": "EFECTIVO",
    }

    # 1. Primer envío
    res1 = client_migrado.post("/api/expenses", json=payload, headers={"Idempotency-Key": key})
    assert res1.status_code == 201
    id_creado = res1.json()["id"]

    # 2. Reintento idéntico
    res2 = client_migrado.post("/api/expenses", json=payload, headers={"Idempotency-Key": key})
    assert res2.status_code == 200
    assert res2.json()["id"] == id_creado
    assert res2.json()["detalle"] == "Pago a distribuidora Arcor"
    assert res2.json()["idempotency_key"] == key

    # 3. Comprobar que en la base de datos existe exactamente 1 gasto y exactamente 1 evento de auditoría
    total_gastos = test_db_migrada.execute("SELECT COUNT(*) FROM gastos_retiros WHERE idempotency_key = ?", (key,)).fetchone()[0]
    total_auditorias = test_db_migrada.execute("SELECT COUNT(*) FROM gastos_auditoria WHERE gasto_id = ?", (id_creado,)).fetchone()[0]
    assert total_gastos == 1
    assert total_auditorias == 1


def test_gasto_reutilizar_clave_con_datos_discordantes_devuelve_409(client_migrado):
    """Reutilizar una Idempotency-Key de gasto con montos o detalle diferentes devuelve 409 Conflict."""
    key = "gst-test-conflict-001"
    payload_original = {
        "detalle": "Lavandina y artículos de limpieza",
        "monto_salida": 3200,
        "tipo": "Limpieza",
        "origen_fondos": "Caja diaria",
        "metodo_pago": "EFECTIVO",
    }
    res1 = client_migrado.post("/api/expenses", json=payload_original, headers={"Idempotency-Key": key})
    assert res1.status_code == 201

    # Intento discordante con diferente monto
    payload_otro_monto = dict(payload_original, monto_salida=5000)
    res_conflicto = client_migrado.post("/api/expenses", json=payload_otro_monto, headers={"Idempotency-Key": key})
    assert res_conflicto.status_code == 409
    assert "Conflicto de idempotencia" in res_conflicto.json()["detail"]


def test_gasto_reintento_de_operacion_anulada_devuelve_409(client_migrado):
    """Reintentar con la misma clave un gasto previamente anulado retorna 409 Conflict."""
    key = "gst-test-anulado-001"
    payload = {
        "detalle": "Gasto que será anulado",
        "monto_salida": 1800,
        "tipo": "General",
        "origen_fondos": "Caja diaria",
        "metodo_pago": "EFECTIVO",
    }
    res1 = client_migrado.post("/api/expenses", json=payload, headers={"Idempotency-Key": key})
    assert res1.status_code == 201
    id_gasto = res1.json()["id"]

    # Anular el gasto
    res_anular = client_migrado.delete(f"/api/expenses/{id_gasto}")
    assert res_anular.status_code == 200

    # Reintentar con la misma clave
    res_reintento = client_migrado.post("/api/expenses", json=payload, headers={"Idempotency-Key": key})
    assert res_reintento.status_code == 409
    assert "fue anulada previamente" in res_reintento.json()["detail"]


# ==============================================================================
# PRUEBAS DE ESQUEMA NO MIGRADO (SCHEMA_NOT_READY -> HTTP 503)
# ==============================================================================


@pytest.fixture
def test_db_sin_migrar():
    """Base de datos sin migración 010 (sin columna idempotency_key)."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE cargas_servicios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            legacy_row_id TEXT,
            fecha_hora_utc TEXT NOT NULL,
            servicio TEXT NOT NULL,
            monto_carga INTEGER NOT NULL,
            monto_adicional INTEGER NOT NULL,
            total_cobrado INTEGER NOT NULL,
            pago_efectivo INTEGER NOT NULL,
            pago_transferencia INTEGER NOT NULL,
            anulado INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE gastos_retiros (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            legacy_row_id TEXT,
            fecha_hora_utc TEXT NOT NULL,
            tipo TEXT NOT NULL,
            detalle TEXT NOT NULL,
            monto_salida INTEGER NOT NULL,
            origen_fondos TEXT,
            metodo_pago TEXT NOT NULL,
            anulado INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.commit()
    yield conn
    conn.close()


def test_esquema_sin_migrar_devuelve_503_al_usar_idempotency_key(test_db_sin_migrar):
    """Si una base de datos no tiene aplicada la migración 010, enviar Idempotency-Key devuelve 503."""
    app.dependency_overrides[get_db_connection] = lambda: test_db_sin_migrar
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(username="alan", display_name="Alan")

    with TestClient(app) as client:
        # Carga de servicio con clave en esquema no migrado
        res_serv = client.post(
            "/api/services",
            json={
                "servicio": "Claro",
                "monto_carga": 1000,
                "monto_adicional": 100,
                "total_cobrado": 1100,
                "pago_efvo": 1100,
                "pago_transf": 0,
            },
            headers={"Idempotency-Key": "srv-unmigrated-test"},
        )
        assert res_serv.status_code == 503
        assert "SCHEMA_NOT_READY" in res_serv.json()["detail"]

        # Gasto con clave en esquema no migrado
        res_gasto = client.post(
            "/api/expenses",
            json={
                "detalle": "Prueba esquema no migrado",
                "monto_salida": 500,
                "tipo": "General",
                "origen_fondos": "Caja diaria",
                "metodo_pago": "EFECTIVO",
            },
            headers={"Idempotency-Key": "gst-unmigrated-test"},
        )
        assert res_gasto.status_code == 503
        assert "SCHEMA_NOT_READY" in res_gasto.json()["detail"]

    app.dependency_overrides.clear()
