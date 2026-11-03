"""Pruebas rigurosas de validación de PATCH para Servicios (AUD-002).

Verifica exhaustivamente:
- PATCH solamente de plataforma (válida e incompatible).
- PATCH solamente de destino (válido e incompatible).
- PATCH de ambos campos (combinación válida e inválida).
- PATCH sin cambios en plataforma/destino (ajustes financieros).
- Preservación de registros históricos con ambos campos NULL.
- Ausencia de mutaciones parciales o registros espurios de auditoría ante 422.
"""
import sqlite3
import pytest
from fastapi.testclient import TestClient

from api.dependencies import AuthenticatedUser, get_current_user, get_db_connection
from api.main import app
from db.services import COLUMNA_ID_INTERNO


@pytest.fixture
def test_db():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
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
            plataforma_origen TEXT DEFAULT NULL,
            servicio_destino TEXT DEFAULT NULL,
            CHECK (total_cobrado = monto_carga + monto_adicional),
            CHECK (total_cobrado = pago_efectivo + pago_transferencia)
        );
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
            actor TEXT NOT NULL DEFAULT 'sistema'
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS commercial_events_outbox (
            sequence_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            action_type TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            payload_canonical_hash TEXT NOT NULL,
            payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            retry_count INTEGER NOT NULL DEFAULT 0,
            max_retries INTEGER NOT NULL DEFAULT 5,
            error_message TEXT,
            tesoreria_movement_id TEXT,
            reprocessed_by TEXT,
            reprocessed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            published_at TEXT
        );
        """
    )
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def client(test_db):
    app.dependency_overrides[get_db_connection] = lambda: test_db
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        username="tester_aud002",
        role="ADMIN",
        display_name="Tester AUD-002",
    )
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_patch_solamente_plataforma_valida(client):
    """PATCH modificando solo plataforma cuando el destino es compatible con la nueva plataforma."""
    # Registro inicial: PREPAY -> Personal ($1.000 + $100)
    crear = client.post(
        "/api/services",
        json={
            "plataforma_origen": "PREPAY",
            "servicio_destino": "Personal",
            "monto_carga": 1000,
            "monto_adicional": 100,
            "pago_efvo": 1100,
            "pago_transf": 0,
        },
    )
    assert crear.status_code == 201
    cid = crear.json()["id"]

    # Cambiar solo plataforma a MERCADO PAGO (Personal es válido para MERCADO PAGO)
    edit = client.patch(
        f"/api/services/{cid}",
        json={"plataforma_origen": "MERCADO PAGO"},
    )
    assert edit.status_code == 200
    data = edit.json()
    assert data["plataforma_origen"] == "MERCADO PAGO"
    assert data["servicio_destino"] == "Personal"
    assert data["servicio"] == "Personal"


def test_patch_solamente_plataforma_invalida_combinacion(client):
    """PATCH modificando solo plataforma a una incompatible con el destino existente -> HTTP 422."""
    # Registro inicial: PREPAY -> Personal ($1.000 + $100)
    crear = client.post(
        "/api/services",
        json={
            "plataforma_origen": "PREPAY",
            "servicio_destino": "Personal",
            "monto_carga": 1000,
            "monto_adicional": 100,
            "pago_efvo": 1100,
            "pago_transf": 0,
        },
    )
    cid = crear.json()["id"]

    # Cambiar plataforma a CLARO COMERCIOS (solo admite Claro, Personal es inválido)
    edit = client.patch(
        f"/api/services/{cid}",
        json={"plataforma_origen": "CLARO COMERCIOS"},
    )
    assert edit.status_code == 422
    assert "Combinación inválida" in edit.json()["detail"]


def test_patch_solamente_destino_valido(client):
    """PATCH modificando solo el destino cuando es compatible con la plataforma existente."""
    # Registro inicial: PREPAY -> Personal
    crear = client.post(
        "/api/services",
        json={
            "plataforma_origen": "PREPAY",
            "servicio_destino": "Personal",
            "monto_carga": 1000,
            "monto_adicional": 100,
            "pago_efvo": 1100,
            "pago_transf": 0,
        },
    )
    cid = crear.json()["id"]

    # Cambiar destino a SUBE (válido para PREPAY)
    edit = client.patch(
        f"/api/services/{cid}",
        json={"servicio_destino": "SUBE"},
    )
    assert edit.status_code == 200
    data = edit.json()
    assert data["plataforma_origen"] == "PREPAY"
    assert data["servicio_destino"] == "SUBE"


def test_patch_solamente_destino_invalido(client):
    """PATCH modificando solo el destino a uno no admitido por la plataforma existente -> HTTP 422."""
    # Registro inicial: CLARO COMERCIOS -> Claro
    crear = client.post(
        "/api/services",
        json={
            "plataforma_origen": "CLARO COMERCIOS",
            "servicio_destino": "Claro",
            "monto_carga": 2000,
            "monto_adicional": 150,
            "pago_efvo": 2150,
            "pago_transf": 0,
        },
    )
    cid = crear.json()["id"]

    # Intentar cambiar destino a SUBE en CLARO COMERCIOS
    edit = client.patch(
        f"/api/services/{cid}",
        json={"servicio_destino": "SUBE"},
    )
    assert edit.status_code == 422
    assert "Combinación inválida" in edit.json()["detail"]


def test_patch_ambos_campos_valido(client):
    """PATCH modificando ambos campos a una combinación completamente válida."""
    crear = client.post(
        "/api/services",
        json={
            "plataforma_origen": "PREPAY",
            "servicio_destino": "SUBE",
            "monto_carga": 1000,
            "monto_adicional": 100,
            "pago_efvo": 1100,
            "pago_transf": 0,
        },
    )
    cid = crear.json()["id"]

    # Cambiar ambos campos a CLARO COMERCIOS -> Claro
    edit = client.patch(
        f"/api/services/{cid}",
        json={
            "plataforma_origen": "CLARO COMERCIOS",
            "servicio_destino": "Claro",
        },
    )
    assert edit.status_code == 200
    data = edit.json()
    assert data["plataforma_origen"] == "CLARO COMERCIOS"
    assert data["servicio_destino"] == "Claro"


def test_patch_ambos_campos_invalido(client):
    """PATCH modificando ambos campos a una combinación incompatible entre sí -> HTTP 422."""
    crear = client.post(
        "/api/services",
        json={
            "plataforma_origen": "PREPAY",
            "servicio_destino": "SUBE",
            "monto_carga": 1000,
            "monto_adicional": 100,
            "pago_efvo": 1100,
            "pago_transf": 0,
        },
    )
    cid = crear.json()["id"]

    edit = client.patch(
        f"/api/services/{cid}",
        json={
            "plataforma_origen": "CLARO COMERCIOS",
            "servicio_destino": "Movistar",
        },
    )
    assert edit.status_code == 422


def test_patch_sin_cambios_en_plataforma_ni_destino(client):
    """PATCH que solo actualiza importes financieros conservando intactos plataforma y destino."""
    crear = client.post(
        "/api/services",
        json={
            "plataforma_origen": "MERCADO PAGO",
            "servicio_destino": "SUBE",
            "monto_carga": 2000,
            "monto_adicional": 200,
            "pago_efvo": 2200,
            "pago_transf": 0,
        },
    )
    cid = crear.json()["id"]

    edit = client.patch(
        f"/api/services/{cid}",
        json={
            "monto_carga": 3000,
            "monto_adicional": 300,
            "pago_efvo": 3300,
            "pago_transf": 0,
        },
    )
    assert edit.status_code == 200
    data = edit.json()
    assert data["plataforma_origen"] == "MERCADO PAGO"
    assert data["servicio_destino"] == "SUBE"
    assert data["monto_carga"] == 3000
    assert data["total_cobrado"] == 3300


def test_patch_registro_historico_con_ambos_campos_null(client, test_db):
    """Un registro histórico con PLATAFORMA_ORIGEN=NULL y SERVICIO_DESTINO=NULL se actualiza sin forzar campos espurios."""
    # Insertar directamente una fila histórica pre-migración 013
    cur = test_db.execute(
        """
        INSERT INTO cargas_servicios (
            fecha_hora_utc, servicio, monto_carga, monto_adicional,
            total_cobrado, pago_efectivo, pago_transferencia, anulado,
            plataforma_origen, servicio_destino
        ) VALUES (
            '2026-10-01T12:00:00Z', 'DIRECTV_PREVIO', 2000, 100,
            2100, 2100, 0, 0,
            NULL, NULL
        );
        """
    )
    test_db.commit()
    hid = cur.lastrowid

    # Actualizar solo el importe adicional
    edit = client.patch(
        f"/api/services/{hid}",
        json={
            "monto_adicional": 200,
            "pago_efvo": 2200,
            "pago_transf": 0,
        },
    )
    assert edit.status_code == 200
    data = edit.json()
    assert data["plataforma_origen"] is None
    assert data["servicio_destino"] is None
    assert data["servicio"] == "DIRECTV_PREVIO"
    assert data["total_cobrado"] == 2200


def test_error_validacion_no_modifica_base_ni_genera_auditoria(client, test_db):
    """Si la validación falla (422), la base queda 100% inalterada y no se escribe fila de auditoría."""
    crear = client.post(
        "/api/services",
        json={
            "plataforma_origen": "CLARO COMERCIOS",
            "servicio_destino": "Claro",
            "monto_carga": 1500,
            "monto_adicional": 100,
            "pago_efvo": 1600,
            "pago_transf": 0,
        },
    )
    cid = crear.json()["id"]

    # Contar auditorías previas
    aud_antes = test_db.execute("SELECT count(*) FROM cargas_servicios_auditoria WHERE carga_id = ?", (cid,)).fetchone()[0]

    # Intentar PATCH incompatible
    edit = client.patch(
        f"/api/services/{cid}",
        json={"servicio_destino": "Personal"},
    )
    assert edit.status_code == 422

    # Verificar que el registro en la base de datos no cambió
    row = test_db.execute("SELECT plataforma_origen, servicio_destino, servicio FROM cargas_servicios WHERE id = ?", (cid,)).fetchone()
    assert row["plataforma_origen"] == "CLARO COMERCIOS"
    assert row["servicio_destino"] == "Claro"
    assert row["servicio"] == "Claro"

    # Verificar que no se insertó ninguna auditoría espuria por el intento fallido
    aud_despues = test_db.execute("SELECT count(*) FROM cargas_servicios_auditoria WHERE carga_id = ?", (cid,)).fetchone()[0]
    assert aud_despues == aud_antes
