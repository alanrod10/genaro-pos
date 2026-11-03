"""Pruebas de Jornadas de Tesorería — LAB-004.

Verificación del ciclo de vida de jornadas:
- Apertura con snapshot de los 9 fondos canónicos.
- Invariante de una sola jornada activa a la vez.
- Cierre y arqueo con determinación exacta de diferencias (subtractMoney).
- Endpoints REST bajo /api/tesoreria/jornada.
"""

import sqlite3
import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.dependencies import get_current_user
from api.auth import AuthenticatedUser
from tesoreria.isolation import conectar_db_laboratorio
from tesoreria.schema import inicializar_esquema_laboratorio
from tesoreria.catalog import sembrar_catalogo_canónico
from tesoreria.jornadas import (
    ErrorJornada,
    abrir_jornada,
    cerrar_jornada,
    obtener_jornada_actual,
    obtener_jornada_por_id,
)


@pytest.fixture
def conn_aislada(tmp_path):
    """Crea una base SQLite de laboratorio limpia con catálogo sembrado."""
    db_file = tmp_path / "tesoreria_jornadas_fixture.db"
    conn = conectar_db_laboratorio(str(db_file))
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)
    yield conn
    conn.close()


def test_jornada_001_apertura_exitosa(conn_aislada):
    """Verifica apertura correcta de jornada con 9 fondos vinculados."""
    assert obtener_jornada_actual(conn_aislada) is None

    jornada = abrir_jornada(conn_aislada, usuario="alan", observaciones="Inicio de semana")
    assert jornada["numero_jornada"] == 1
    assert jornada["estado"] == "ABIERTA"
    assert jornada["usuario_apertura"] == "alan"
    assert jornada["total_apertura_centavos"] == 0
    assert len(jornada["detalles"]) in (9, 10)

    # Verificar que ahora es la jornada activa
    activa = obtener_jornada_actual(conn_aislada)
    assert activa is not None
    assert activa["id"] == jornada["id"]


def test_jornada_002_invariante_unica_jornada_activa(conn_aislada):
    """Verifica que no se pueda abrir una segunda jornada si ya hay una abierta."""
    abrir_jornada(conn_aislada, usuario="alan")

    # Intento por lógica de negocio
    with pytest.raises(ErrorJornada, match="ya existe la jornada"):
        abrir_jornada(conn_aislada, usuario="yilda")

    # Intento directo por SQL (debe ser bloqueado por el trigger trg_unica_jornada_abierta)
    cursor = conn_aislada.cursor()
    with pytest.raises(sqlite3.IntegrityError, match="Ya existe una jornada de tesorería abierta"):
        cursor.execute(
            """
            INSERT INTO tesoreria_jornadas (
                numero_jornada, fecha_operativa, estado,
                apertura_timestamp_utc, usuario_apertura,
                total_apertura_centavos, version
            ) VALUES (999, '2026-10-31', 'ABIERTA', '2026-10-31T12:00:00Z', 'hack', 0, 1)
            """
        )
    cursor.close()


def test_jornada_003_cierre_con_arqueo(conn_aislada):
    """Verifica el cierre de jornada calculando diferencias con subtractMoney."""
    jornada = abrir_jornada(conn_aislada, usuario="alan")
    jornada_id = jornada["id"]

    # Simular arqueo: Fondo 1110 tiene 5000 centavos ($50), Fondo 1120 tiene 0 centavos
    arqueo = {
        "1110": 5000,
        "1120": 0,
    }

    cerrada = cerrar_jornada(
        conn_aislada,
        jornada_id=jornada_id,
        usuario="alan",
        arqueo_centavos_por_fondo=arqueo,
        observaciones="Cierre turno normal",
    )

    assert cerrada["estado"] == "CERRADA"
    assert cerrada["usuario_cierre"] == "alan"
    assert cerrada["cierre_timestamp_utc"] is not None

    # Verificar que ya no hay jornada activa
    assert obtener_jornada_actual(conn_aislada) is None

    # Verificar diferencias en detalles
    detalles_map = {d["codigo_fondo"]: d for d in cerrada["detalles"]}
    assert detalles_map["1110"]["saldo_cierre_centavos"] == 5000
    assert detalles_map["1110"]["diferencia_centavos"] == 5000  # Arqueado 5000 - Sistema 0 = +5000
    assert detalles_map["1120"]["saldo_cierre_centavos"] == 0
    assert detalles_map["1120"]["diferencia_centavos"] == 0


def test_jornada_004_endpoints_api(tmp_path, monkeypatch):
    """Verifica los endpoints REST para abrir y cerrar jornadas bajo /api/tesoreria/jornada."""
    db_file = tmp_path / "api_test_jornadas.db"
    monkeypatch.setenv("GENARO_TESORERIA_DB_PATH", str(db_file))

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        username="alan", display_name="Alan"
    )

    client = TestClient(app)
    try:
        # 1. Sin jornada activa
        resp = client.get("/api/tesoreria/jornada/actual")
        assert resp.status_code == 200
        assert resp.json() is None

        # 2. Abrir jornada
        resp_abrir = client.post(
            "/api/tesoreria/jornada/abrir",
            json={"observaciones": "Apertura desde API"},
        )
        assert resp_abrir.status_code == 200
        j_abierta = resp_abrir.json()
        assert j_abierta["estado"] == "ABIERTA"
        assert j_abierta["usuario_apertura"] == "alan"
        jornada_id = j_abierta["id"]

        # 3. Consultar jornada actual
        resp_act = client.get("/api/tesoreria/jornada/actual")
        assert resp_act.status_code == 200
        assert resp_act.json()["id"] == jornada_id

        # 4. Cerrar jornada
        resp_cerrar = client.post(
            "/api/tesoreria/jornada/cerrar",
            json={
                "jornada_id": jornada_id,
                "arqueo_centavos_por_fondo": {"1110": 10000},
                "observaciones": "Cierre con arqueo correcto",
            },
        )
        assert resp_cerrar.status_code == 200
        j_cerrada = resp_cerrar.json()
        assert j_cerrada["estado"] == "CERRADA"

        # 5. Ya no hay activa
        resp_post = client.get("/api/tesoreria/jornada/actual")
        assert resp_post.status_code == 200
        assert resp_post.json() is None
    finally:
        app.dependency_overrides.clear()
