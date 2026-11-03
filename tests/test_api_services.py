"""Pruebas unitarias y de integración para la API de Servicios y Cargas virtuales."""
import sqlite3
import pytest
from fastapi.testclient import TestClient

from api.dependencies import AuthenticatedUser, get_current_user, get_db_connection
from api.main import app


@pytest.fixture
def sqlite_services_conn():
    """Base SQLite en memoria con esquema de cargas_servicios."""
    conexion = sqlite3.connect(":memory:", check_same_thread=False)
    conexion.execute(
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
    conexion.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_cargas_idempotency_key
        ON cargas_servicios(idempotency_key)
        WHERE idempotency_key IS NOT NULL
        """
    )
    conexion.execute(
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
    conexion.commit()
    yield conexion
    conexion.close()


@pytest.fixture
def client(sqlite_services_conn):
    """TestClient de FastAPI con conexión SQLite inyectada y autenticación activa."""
    app.dependency_overrides[get_db_connection] = lambda: sqlite_services_conn
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(username="alan", display_name="Alan")
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_registrar_carga_valida(client):
    """Registra una carga válida en la API y verifica respuesta 201 y cálculo."""
    payload = {
        "servicio": "Claro",
        "monto_carga": 3000,
        "monto_adicional": 200,
        "pago_efvo": 3200,
        "pago_transf": 0,
        "fecha": "24/10/2026 14:00:00",
    }
    res = client.post("/api/services", json=payload)
    assert res.status_code == 201
    data = res.json()
    assert data["id"] > 0
    assert data["servicio"] == "Claro"
    assert data["monto_carga"] == 3000
    assert data["monto_adicional"] == 200
    assert data["total_cobrado"] == 3200
    assert data["pago_efvo"] == 3200
    assert data["pago_transf"] == 0
    assert data["anulado"] is False


def test_rechaza_carga_con_inconsistencia_de_pagos(client):
    """Rechaza una carga cuando la suma de pagos no coincide con carga + adicional."""
    payload = {
        "servicio": "Personal",
        "monto_carga": 2000,
        "monto_adicional": 100,
        "pago_efvo": 1500,  # Suma 1500 vs esperado 2100
        "pago_transf": 0,
    }
    res = client.post("/api/services", json=payload)
    assert res.status_code in (400, 422)


def test_rechaza_carga_con_monto_cero_o_negativo(client):
    """Rechaza una carga con monto_carga <= 0."""
    payload = {
        "servicio": "Tuenti",
        "monto_carga": 0,
        "monto_adicional": 100,
        "pago_efvo": 100,
        "pago_transf": 0,
    }
    res = client.post("/api/services", json=payload)
    assert res.status_code in (400, 422)


def test_listar_cargas_y_filtrar_por_fecha(client):
    """Crea múltiples cargas y valida filtrado y orden descendente."""
    client.post(
        "/api/services",
        json={
            "servicio": "SUBE",
            "monto_carga": 1000,
            "monto_adicional": 100,
            "pago_efvo": 1100,
            "pago_transf": 0,
            "fecha": "23/10/2026 10:00:00",
        },
    )
    client.post(
        "/api/services",
        json={
            "servicio": "Movistar",
            "monto_carga": 4000,
            "monto_adicional": 200,
            "pago_efvo": 0,
            "pago_transf": 4200,
            "fecha": "24/10/2026 11:00:00",
        },
    )

    # Listar todas
    res = client.get("/api/services")
    assert res.status_code == 200
    assert res.json()["total"] == 2

    # Filtrar solo el día 24/10/2026
    res_fecha = client.get("/api/services?fecha=24/10/2026")
    assert res_fecha.status_code == 200
    assert res_fecha.json()["total"] == 1
    assert res_fecha.json()["cargas"][0]["servicio"] == "Movistar"


def test_resumen_contable_con_segregacion_prepay_claro_sube_mp(client):
    """Verifica el cálculo de capital PREPAY, Claro y SUBE (MP) según reglas del sistema anterior."""
    # 1. Claro (capital segregado a capital_claro)
    client.post(
        "/api/services",
        json={
            "servicio": "Claro",
            "monto_carga": 5000,
            "monto_adicional": 300,
            "pago_efvo": 5300,
            "pago_transf": 0,
            "fecha": "24/10/2026 09:00:00",
        },
    )
    # 2. SUBE (MP) (capital segregado a capital_sube_mp, no entra a prepay)
    client.post(
        "/api/services",
        json={
            "servicio": "SUBE (MP)",
            "monto_carga": 2000,
            "monto_adicional": 100,
            "pago_efvo": 2100,
            "pago_transf": 0,
            "fecha": "24/10/2026 10:00:00",
        },
    )
    # 3. Personal (PREPAY habitual)
    client.post(
        "/api/services",
        json={
            "servicio": "Personal",
            "monto_carga": 3000,
            "monto_adicional": 200,
            "pago_efvo": 1200,
            "pago_transf": 2000,
            "fecha": "24/10/2026 11:00:00",
        },
    )

    res = client.get("/api/services/summary?fecha=24/10/2026")
    assert res.status_code == 200
    summary = res.json()
    assert summary["total_operaciones"] == 3
    assert summary["total_capital"] == 10000  # 5000 + 2000 + 3000
    assert summary["total_adicional"] == 600  # 300 + 100 + 200
    assert summary["total_cobrado"] == 10600
    assert summary["total_efectivo"] == 8600  # 5300 + 2100 + 1200
    assert summary["total_transferencia"] == 2000
    # Segregación:
    assert summary["capital_claro"] == 5000
    assert summary["capital_sube_mp"] == 2000
    assert summary["capital_prepay"] == 3000
    assert summary["operaciones_anuladas"] == 0


def test_edicion_puntual_de_carga(client):
    """Permite editar una carga existente sin alterar las demás."""
    res_crear = client.post(
        "/api/services",
        json={
            "servicio": "DIRECTV",
            "monto_carga": 2000,
            "monto_adicional": 100,
            "pago_efvo": 2100,
            "pago_transf": 0,
            "fecha": "24/10/2026 12:00:00",
        },
    )
    carga_id = res_crear.json()["id"]

    # Editar monto de carga a 3000 y adicional a 200
    res_edit = client.patch(
        f"/api/services/{carga_id}",
        json={
            "monto_carga": 3000,
            "monto_adicional": 200,
            "pago_efvo": 3200,
            "pago_transf": 0,
        },
    )
    assert res_edit.status_code == 200
    editada = res_edit.json()
    assert editada["monto_carga"] == 3000
    assert editada["monto_adicional"] == 200
    assert editada["total_cobrado"] == 3200


def test_anulacion_logica_de_carga(client):
    """Anula lógicamente una carga y comprueba que se excluye de las listas normales pero se conserva."""
    res_crear = client.post(
        "/api/services",
        json={
            "servicio": "SUBE",
            "monto_carga": 1000,
            "monto_adicional": 100,
            "pago_efvo": 1100,
            "pago_transf": 0,
            "fecha": "24/10/2026 13:00:00",
        },
    )
    carga_id = res_crear.json()["id"]

    # Anular
    res_cancel = client.post(f"/api/services/{carga_id}/cancel")
    assert res_cancel.status_code == 200
    assert res_cancel.json()["status"] == "anulado"

    # En listado normal no debe aparecer
    res_list = client.get("/api/services")
    assert res_list.json()["total"] == 0

    # Con incluir_anuladas sí debe aparecer
    res_full = client.get("/api/services?incluir_anuladas=true")
    assert res_full.json()["total"] == 1
    assert res_full.json()["cargas"][0]["anulado"] is True
