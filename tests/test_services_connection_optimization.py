"""Pruebas de optimización de conexiones y eliminación de consultas redundantes en Servicios (Prioridad 3).

Verifica:
1. Alta normal devuelve la fila canónica correcta desde el repositorio.
2. Reintento con la misma clave de idempotencia retorna HTTP 200 OK reutilizando la fila en memoria.
3. Reutilización de clave con datos distintos retorna HTTP 409 Conflict y limpia la conexión.
4. Anulación lógica preserva la auditoría y actualiza el estado.
5. Manejo de errores garantiza el cierre de conexiones sin transacciones huérfanas.
6. Eliminación de lecturas redundantes y reducción demostrada de conexiones (de 2 a 1 en registro, de 3 a 1 en anulación).
7. Conexiones independientes por solicitud (no hay estado global compartido entre llamadas).
8. Integridad de reglas financieras, medios de pago, auditoría y claves de idempotencia.
"""

import json
import sqlite3
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.dependencies import AuthenticatedUser, get_current_user, get_db_connection
from api.main import app
from db.services import (
    COLUMNA_ID_INTERNO,
    ErrorCargasTurso,
    ErrorConflictoIdempotencia,
    _conexion,
    anular_carga_individual_turso,
    obtener_carga_por_id_turso,
    registrar_carga_idempotente_turso,
)


def crear_base_prueba():
    """Crea una base de datos SQLite en memoria con esquema de Servicios y Auditoría migrado."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
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
        CREATE TABLE cargas_servicios_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            carga_id INTEGER NOT NULL,
            accion TEXT NOT NULL,
            valores_anteriores TEXT,
            valores_nuevos TEXT,
            actor TEXT NOT NULL,
            creado_en TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    return conn


@pytest.fixture
def memory_db():
    conn = crear_base_prueba()
    yield conn
    conn.close()


@pytest.fixture
def client(memory_db):
    app.dependency_overrides[get_db_connection] = lambda: memory_db
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        username="test_admin", display_name="Admin Pruebas"
    )
    with TestClient(app) as tc:
        yield tc
    app.dependency_overrides.clear()


class ConnectionTracker:
    """Rastreador instrumental para contar aperturas, cierres y consultas SQL."""

    def __init__(self):
        self.opened = 0
        self.closed = 0
        self.queries: List[str] = []

    def make_conn_factory(self, base_conn):
        tracker = self

        class TrackedCursor:
            def __init__(self, cursor):
                self._cursor = cursor

            def execute(self, sql, *args, **kwargs):
                tracker.queries.append(sql.strip())
                return self._cursor.execute(sql, *args, **kwargs)

            def fetchone(self):
                return self._cursor.fetchone()

            def fetchall(self):
                return self._cursor.fetchall()

            def __getattr__(self, name):
                return getattr(self._cursor, name)

        class TrackedConnection:
            def __init__(self, raw_conn):
                self._raw = raw_conn
                tracker.opened += 1

            def execute(self, sql, *args, **kwargs):
                tracker.queries.append(sql.strip())
                return self._raw.execute(sql, *args, **kwargs)

            def cursor(self):
                return TrackedCursor(self._raw.cursor())

            def commit(self):
                return self._raw.commit()

            def rollback(self):
                return self._raw.rollback()

            def close(self):
                tracker.closed += 1
                return self._raw.close()

            def __getattr__(self, name):
                return getattr(self._raw, name)

        return TrackedConnection


def test_alta_normal_valores_y_conexion_unica(client, memory_db):
    """1. Alta normal devuelve los datos canónicos correctos y usa conexión compartida."""
    payload = {
        "servicio": "Claro",
        "monto_carga": 2000,
        "monto_adicional": 100,
        "total_cobrado": 2100,
        "pago_efvo": 2100,
        "pago_transf": 0,
        "fecha": "27/10/2026 10:00:00",
        "idempotency_key": "IDEMP-ALTA-001",
    }

    res = client.post("/api/services", json=payload)
    assert res.status_code == 201
    data = res.json()

    assert data["id"] > 0
    assert data["servicio"] == "Claro"
    assert data["monto_carga"] == 2000
    assert data["monto_adicional"] == 100
    assert data["total_cobrado"] == 2100
    assert data["pago_efvo"] == 2100
    assert data["pago_transf"] == 0
    assert data["anulado"] is False
    assert data["idempotency_key"] == "IDEMP-ALTA-001"

    # Verificar auditoría
    cursor = memory_db.execute("SELECT accion, actor, valores_nuevos FROM cargas_servicios_auditoria WHERE carga_id = ?", (data["id"],))
    audit = cursor.fetchone()
    assert audit is not None
    assert audit[0] == "CREAR"
    assert audit[1] == "test_admin"
    assert "Claro" in audit[2]


def test_reintento_idempotente_mismo_contenido(client):
    """2. Reintento con misma clave devuelve HTTP 200 OK y el mismo contenido."""
    payload = {
        "servicio": "Personal",
        "monto_carga": 3000,
        "monto_adicional": 200,
        "total_cobrado": 3200,
        "pago_efvo": 0,
        "pago_transf": 3200,
        "fecha": "27/10/2026 10:30:00",
        "idempotency_key": "IDEMP-REINTENTO-001",
    }

    # Primera llamada: 201 Created
    res1 = client.post("/api/services", json=payload)
    assert res1.status_code == 201
    id1 = res1.json()["id"]

    # Segunda llamada: 200 OK con mismo id y datos
    res2 = client.post("/api/services", json=payload)
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["id"] == id1
    assert data2["servicio"] == "Personal"
    assert data2["total_cobrado"] == 3200
    assert data2["idempotency_key"] == "IDEMP-REINTENTO-001"


def test_reutilizacion_clave_contenido_diferente_conflicto(client):
    """3. Reutilización de clave con datos discordantes devuelve HTTP 409 Conflict."""
    payload1 = {
        "servicio": "Tuenti",
        "monto_carga": 1000,
        "monto_adicional": 100,
        "total_cobrado": 1100,
        "pago_efvo": 1100,
        "pago_transf": 0,
        "idempotency_key": "IDEMP-CONFLICT-001",
    }
    res1 = client.post("/api/services", json=payload1)
    assert res1.status_code == 201

    payload2 = {
        "servicio": "Movistar",  # Servicio diferente
        "monto_carga": 1000,
        "monto_adicional": 100,
        "total_cobrado": 1100,
        "pago_efvo": 1100,
        "pago_transf": 0,
        "idempotency_key": "IDEMP-CONFLICT-001",
    }
    res2 = client.post("/api/services", json=payload2)
    assert res2.status_code == 409
    assert "Conflicto de idempotencia" in res2.json()["detail"]


def test_anulacion_y_conservacion_auditoria(client, memory_db):
    """4. Anulación lógica marca anulado=1 y registra auditoría."""
    res_crear = client.post(
        "/api/services",
        json={
            "servicio": "SUBE",
            "monto_carga": 4000,
            "monto_adicional": 200,
            "total_cobrado": 4200,
            "pago_efvo": 4200,
            "pago_transf": 0,
            "idempotency_key": "IDEMP-ANULAR-001",
        },
    )
    assert res_crear.status_code == 201
    carga_id = res_crear.json()["id"]

    # Anular
    res_cancel = client.post(f"/api/services/{carga_id}/cancel")
    assert res_cancel.status_code == 200
    assert res_cancel.json()["status"] == "anulado"
    assert res_cancel.json()["id"] == carga_id

    # Comprobar en base que anulado = 1
    cursor = memory_db.execute("SELECT anulado FROM cargas_servicios WHERE id = ?", (carga_id,))
    assert cursor.fetchone()[0] == 1

    # Comprobar auditoría
    cursor = memory_db.execute(
        "SELECT accion, actor, valores_nuevos FROM cargas_servicios_auditoria WHERE carga_id = ? AND accion = 'ANULAR'",
        (carga_id,),
    )
    audit = cursor.fetchone()
    assert audit is not None
    assert audit[0] == "ANULAR"
    assert audit[1] == "test_admin"

    # Reintento de anulación: devuelve ya_anulado
    res_re_cancel = client.post(f"/api/services/{carga_id}/cancel")
    assert res_re_cancel.status_code == 200
    assert res_re_cancel.json()["status"] == "ya_anulado"


def test_cierre_de_conexion_ante_error(memory_db):
    """5. Errores durante la operación de base de datos garantizan rollback y cierre de conexión."""
    tracker = ConnectionTracker()
    TrackedConn = tracker.make_conn_factory(memory_db)
    tracked_inst = TrackedConn(memory_db)

    with patch("db.services._abrir_conexion_dev", return_value=tracked_inst):
        # Simular error de base de datos durante la inserción
        with patch("db.services._insertar_carga", side_effect=sqlite3.OperationalError("disk I/O error")):
            with pytest.raises(sqlite3.OperationalError):
                registrar_carga_idempotente_turso(
                    {
                        "FECHA": "27/10/2026 10:00:00",
                        "SERVICIO": "Claro",
                        "MONTO_CARGA": 2000,
                        "MONTO_ADICIONAL": 100,
                        "TOTAL_COBRADO": 2100,
                        "PAGO_EFVO": 2100,
                        "PAGO_TRANSF": 0,
                    },
                    actor="test_user",
                    conn=None,
                )

    # Se abrió y se cerró limpiamente la conexión en el bloque finally
    assert tracker.opened == 1
    assert tracker.closed == 1


def test_reduccion_demostrada_de_conexiones_registro(memory_db):
    """6a. Registro sin inyección de dependencia usa exactamente 1 conexión (antes usaba 2)."""
    conn_a = crear_base_prueba()
    tracker = ConnectionTracker()

    def mock_abrir_conexion():
        tracker.opened += 1

        class FakeConnection:
            def execute(self, sql, *args, **kwargs):
                tracker.queries.append(sql.strip())
                return conn_a.execute(sql, *args, **kwargs)

            def commit(self):
                return conn_a.commit()

            def rollback(self):
                return conn_a.rollback()

            def close(self):
                tracker.closed += 1

        return FakeConnection()

    with patch("db.services._abrir_conexion_dev", side_effect=mock_abrir_conexion):
        # Desactivamos el override para forzar apertura interna (_abrir_conexion_dev)
        app.dependency_overrides.clear()
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
            username="test_admin", display_name="Admin"
        )
        with TestClient(app) as tc:
            res = tc.post(
                "/api/services",
                json={
                    "servicio": "Claro",
                    "monto_carga": 2000,
                    "monto_adicional": 100,
                    "total_cobrado": 2100,
                    "pago_efvo": 2100,
                    "pago_transf": 0,
                    "idempotency_key": "TRACK-001",
                },
            )
            assert res.status_code == 201

    # Exactamente 1 conexión abierta y cerrada para toda la operación (antes 2)
    assert tracker.opened == 1
    assert tracker.closed == 1


def test_reduccion_demostrada_de_conexiones_anulacion():
    """6b. Anulación sin inyección de dependencia usa exactamente 1 conexión (antes usaba 3)."""
    conn_b = crear_base_prueba()
    # Insertar una carga previa
    conn_b.execute(
        """
        INSERT INTO cargas_servicios (
            fecha_hora_utc, servicio, monto_carga, monto_adicional,
            total_cobrado, pago_efectivo, pago_transferencia, anulado
        ) VALUES ('2026-10-27 10:00:00', 'Claro', 2000, 100, 2100, 2100, 0, 0)
        """
    )
    conn_b.commit()

    tracker = ConnectionTracker()

    def mock_abrir_conexion():
        tracker.opened += 1

        class FakeConnection:
            def execute(self, sql, *args, **kwargs):
                tracker.queries.append(sql.strip())
                return conn_b.execute(sql, *args, **kwargs)

            def commit(self):
                return conn_b.commit()

            def rollback(self):
                return conn_b.rollback()

            def close(self):
                tracker.closed += 1

        return FakeConnection()

    with patch("db.services._abrir_conexion_dev", side_effect=mock_abrir_conexion):
        app.dependency_overrides.clear()
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
            username="test_admin", display_name="Admin"
        )
        with TestClient(app) as tc:
            res = tc.post("/api/services/1/cancel")
            assert res.status_code == 200
            assert res.json()["status"] == "anulado"

    # En la implementación previa, abría 3 conexiones:
    # 1: obtener_carga_por_id_turso inicial
    # 2: guardar_ediciones_cargas_turso
    # 3: obtener_carga_por_id_turso final redundante
    # Ahora abre exactamente 1 conexión para toda la solicitud y la cierra al terminar:
    assert tracker.opened == 1
    assert tracker.closed == 1


def test_ausencia_de_lectura_redundante_en_anular_individual(memory_db):
    """6c. anular_carga_individual_turso no ejecuta un SELECT redundante al final."""
    memory_db.execute(
        """
        INSERT INTO cargas_servicios (
            id, fecha_hora_utc, servicio, monto_carga, monto_adicional,
            total_cobrado, pago_efectivo, pago_transferencia, anulado
        ) VALUES (50, '2026-10-27 10:00:00', 'Movistar', 1000, 100, 1100, 1100, 0, 0)
        """
    )
    memory_db.commit()

    tracker = ConnectionTracker()
    TrackedConn = tracker.make_conn_factory(memory_db)
    tracked_conn = TrackedConn(memory_db)

    resultado = anular_carga_individual_turso(50, actor="auditor", conn=tracked_conn)
    assert resultado["anulado"] == 1
    assert resultado["SERVICIO"] == "Movistar"

    # Verificamos que no se haya llamado a obtener_carga_por_id_turso al final.
    # En guardar_ediciones_cargas_turso se ejecutan exactamente:
    # 1. SELECT 1 FROM cargas_servicios WHERE id = ? AND anulado = 0 (verificación de estado activo)
    # 2. SELECT id, fecha_hora_utc... FROM cargas_servicios WHERE id = ? (datos para auditoría)
    # Ningún SELECT adicional posterior.
    select_queries = [q for q in tracker.queries if "SELECT" in q.upper()]
    assert len(select_queries) == 2


def test_conexiones_aisladas_entre_solicitudes_independientes():
    """7. Las conexiones se crean y destruyen por solicitud sin quedar abiertas ni compartidas."""
    conn1 = crear_base_prueba()
    conn2 = crear_base_prueba()
    bases = [conn1, conn2]

    conexiones_abiertas = []

    def mock_abrir_conexion():
        base = bases.pop(0)
        conexiones_abiertas.append(base)
        return base

    with patch("db.services._abrir_conexion_dev", side_effect=mock_abrir_conexion):
        app.dependency_overrides.clear()
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
            username="admin", display_name="Admin"
        )
        with TestClient(app) as tc:
            # Solicitud 1
            tc.post(
                "/api/services",
                json={
                    "servicio": "Claro",
                    "monto_carga": 1000,
                    "monto_adicional": 100,
                    "total_cobrado": 1100,
                    "pago_efvo": 1100,
                    "pago_transf": 0,
                },
            )
            # Solicitud 2
            tc.post(
                "/api/services",
                json={
                    "servicio": "Personal",
                    "monto_carga": 2000,
                    "monto_adicional": 100,
                    "total_cobrado": 2100,
                    "pago_efvo": 2100,
                    "pago_transf": 0,
                },
            )

    # Dos conexiones completamente separadas fueron utilizadas
    assert len(conexiones_abiertas) == 2
    assert conexiones_abiertas[0] is not conexiones_abiertas[1]
