"""Pruebas del Catálogo de Fondos — LAB-003.

Verificación del catálogo independiente de 9 fondos:
- 1110, 1120, 1130, 1140, 1150, 1210, 1220, 1230 y 1310.
- Integridad de atributos: código, nombre, naturaleza, titularidad, estado.
- Inexistencia de saldos ficticios o iniciales inventados (saldo inicial = 0).
- Restricciones de integridad y protección contra borrado accidental.
- Pista de auditoría inmutable ante cualquier modificación.
"""

import sqlite3
import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.dependencies import get_current_user
from api.auth import AuthenticatedUser
from tesoreria.isolation import conectar_db_laboratorio
from tesoreria.schema import inicializar_esquema_laboratorio
from tesoreria.catalog import (
    CODIGOS_CANONICOS,
    ErrorCatalogoFondos,
    actualizar_estado_fondo,
    obtener_auditoria_fondo,
    obtener_catalogo_fondos,
    obtener_fondo_por_codigo,
    sembrar_catalogo_canónico,
    verificar_integridad_catalogo,
)


@pytest.fixture
def conn_aislada(tmp_path):
    """Crea una base SQLite de laboratorio limpia e inicializada."""
    db_file = tmp_path / "tesoreria_lab_fixture.db"
    conn = conectar_db_laboratorio(str(db_file))
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)
    yield conn
    conn.close()


def test_catalogo_001_diez_fondos_exactos(conn_aislada):
    """Verifica que se creen exactamente los 10 fondos requeridos con saldo 0."""
    fondos = obtener_catalogo_fondos(conn_aislada)
    assert len(fondos) == 10

    codigos = {f["codigo"] for f in fondos}
    esperados = {"1110", "1120", "1130", "1140", "1150", "1160", "1210", "1220", "1230", "1310"}
    assert codigos == esperados

    for f in fondos:
        assert f["saldo_centavos"] == 0, f"El fondo {f['codigo']} no debe tener saldo inventado"
        assert f["estado"] == "ACTIVO"
        assert f["moneda"] == "ARS"
        assert f["version"] == 1


def test_catalogo_002_atributos_y_naturaleza(conn_aislada):
    """Verifica la asignación correcta de naturaleza y titularidad para cada fondo según el catálogo aprobado."""
    fondos_map = {f["codigo"]: f for f in obtener_catalogo_fondos(conn_aislada)}

    # 1110: Drugstore Diario
    assert fondos_map["1110"]["nombre"] == "Drugstore Diario"
    assert fondos_map["1110"]["naturaleza"] == "EFECTIVO"
    assert fondos_map["1110"]["titularidad"] == "PROPIA"
    assert fondos_map["1110"]["disponibilidad_inmediata"] is True

    # 1160: Prepay Diario Efectivo
    assert fondos_map["1160"]["nombre"] == "Prepay Diario Efectivo"
    assert fondos_map["1160"]["naturaleza"] == "EFECTIVO"
    assert fondos_map["1160"]["titularidad"] == "PROPIA"
    assert fondos_map["1160"]["disponibilidad_inmediata"] is True

    # 1120: Drugstore Acumulado
    assert fondos_map["1120"]["nombre"] == "Drugstore Acumulado"
    assert fondos_map["1120"]["naturaleza"] == "EFECTIVO"
    assert fondos_map["1120"]["titularidad"] == "PROPIA"

    # 1130: Prepay Efectivo
    assert fondos_map["1130"]["nombre"] == "Prepay Efectivo"
    assert fondos_map["1130"]["naturaleza"] == "EFECTIVO"
    assert fondos_map["1130"]["titularidad"] == "PROPIA"

    # 1140: Adicionales Efectivo (retiros particulares autorizados)
    assert fondos_map["1140"]["nombre"] == "Adicionales Efectivo"
    assert fondos_map["1140"]["naturaleza"] == "ADICIONALES_PARTICULARES"
    assert fondos_map["1140"]["titularidad"] == "PROPIA"

    # 1150: Claro Efectivo (Custodia / Recaudación Terceros)
    assert fondos_map["1150"]["nombre"] == "Claro Efectivo"
    assert fondos_map["1150"]["naturaleza"] == "RECAUDACION_TERCEROS"
    assert fondos_map["1150"]["titularidad"] == "CUSTODIA"
    assert fondos_map["1150"]["disponibilidad_inmediata"] is False

    # 1210: Mercado Pago Propio
    assert fondos_map["1210"]["nombre"] == "Mercado Pago Propio"
    assert fondos_map["1210"]["naturaleza"] == "BILLETERA_VIRTUAL"
    assert fondos_map["1210"]["titularidad"] == "PROPIA"

    # 1220: Prepay Digital Saldo
    assert fondos_map["1220"]["nombre"] == "Prepay Digital Saldo"
    assert fondos_map["1220"]["naturaleza"] == "BILLETERA_VIRTUAL"
    assert fondos_map["1220"]["titularidad"] == "PROPIA"

    # 1230: Claro Comercios Digital Saldo
    assert fondos_map["1230"]["nombre"] == "Claro Comercios Digital Saldo"
    assert fondos_map["1230"]["naturaleza"] == "BILLETERA_VIRTUAL"
    assert fondos_map["1230"]["titularidad"] == "PROPIA"

    # 1310: Cobranzas en Tránsito Preventistas (Ezequiel)
    assert fondos_map["1310"]["nombre"] == "Cobranzas en Tránsito Preventistas"
    assert fondos_map["1310"]["naturaleza"] == "TRANSITO_PREVENTISTAS"
    assert fondos_map["1310"]["titularidad"] == "TRANSITO"
    assert fondos_map["1310"]["disponibilidad_inmediata"] is False


def test_catalogo_003_proteccion_contra_borrado(conn_aislada):
    """Verifica que el trigger trg_proteger_borrado_fondos impida eliminar fondos."""
    cursor = conn_aislada.cursor()
    with pytest.raises(sqlite3.IntegrityError, match="No está permitido eliminar fondos del catálogo"):
        cursor.execute("DELETE FROM tesoreria_fondos WHERE codigo = '1110';")
    cursor.close()


def test_catalogo_004_restriccion_codigo_invalido(conn_aislada):
    """Verifica que la restricción CHECK impida insertar códigos de fondo no canónicos."""
    cursor = conn_aislada.cursor()
    with pytest.raises(sqlite3.IntegrityError):
        cursor.execute(
            """
            INSERT INTO tesoreria_fondos (
                codigo, nombre, naturaleza, titularidad, moneda, estado, saldo_centavos,
                permite_sobregiro, version, created_at, updated_at
            ) VALUES ('9999', 'Fondo Pirata', 'EFECTIVO', 'PROPIA', 'ARS', 'ACTIVO', 0, 0, 1, '2026-10-31', '2026-10-31')
            """
        )
    cursor.close()


def test_catalogo_005_auditoria_automatica_y_actualizacion_estado(conn_aislada):
    """Verifica que la modificación de estado actualice la versión y genere registro de auditoría inmutable."""
    # Auditoría inicial generada por el INSERT
    auditoria_inicial = obtener_auditoria_fondo(conn_aislada, "1120")
    assert len(auditoria_inicial) >= 1
    assert auditoria_inicial[-1]["accion"] == "INSERT"

    # Actualizar estado a BLOQUEADO
    actualizado = actualizar_estado_fondo(
        conn_aislada,
        codigo="1120",
        nuevo_estado="BLOQUEADO",
        usuario="alan",
        motivo="Mantenimiento preventivo",
    )
    assert actualizado["estado"] == "BLOQUEADO"
    assert actualizado["version"] == 2

    # Verificar nueva entrada de auditoría
    auditoria_posterior = obtener_auditoria_fondo(conn_aislada, "1120")
    assert len(auditoria_posterior) == len(auditoria_inicial) + 1
    ultima_auditoria = auditoria_posterior[0]
    assert ultima_auditoria["accion"] == "UPDATE"
    assert ultima_auditoria["usuario"] == "alan"
    assert ultima_auditoria["valores_previos"]["estado"] == "ACTIVO"
    assert ultima_auditoria["valores_nuevos"]["estado"] == "BLOQUEADO"


def test_catalogo_006_verificar_integridad_completa(conn_aislada):
    """Verifica la función verificar_integridad_catalogo."""
    res = verificar_integridad_catalogo(conn_aislada)
    assert res["valido"] is True
    assert res["total_fondos"] == len(CODIGOS_CANONICOS)
    assert set(res["codigos"]) == CODIGOS_CANONICOS


def test_catalogo_007_endpoints_api(tmp_path, monkeypatch):
    """Verifica los endpoints REST de catálogo y detalle bajo /api/tesoreria/fondos."""
    db_file = tmp_path / "api_test_catalogo.db"
    monkeypatch.setenv("GENARO_TESORERIA_DB_PATH", str(db_file))

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        username="alan", display_name="Alan"
    )

    client = TestClient(app)
    try:
        # GET /api/tesoreria/fondos
        resp = client.get("/api/tesoreria/fondos")
        assert resp.status_code == 200
        fondos = resp.json()
        assert len(fondos) == len(CODIGOS_CANONICOS)

        # GET /api/tesoreria/fondos/1110
        resp_detalle = client.get("/api/tesoreria/fondos/1110")
        assert resp_detalle.status_code == 200
        f1110 = resp_detalle.json()
        assert f1110["codigo"] == "1110"
        assert f1110["saldo_centavos"] == 0
        assert f1110["saldo_formateado"] == "$0,00"

        # PATCH /api/tesoreria/fondos/1120/estado
        resp_patch = client.patch(
            "/api/tesoreria/fondos/1120/estado",
            json={"nuevo_estado": "BLOQUEADO", "motivo": "Auditoría de arqueo"},
        )
        assert resp_patch.status_code == 200
        f1120 = resp_patch.json()
        assert f1120["estado"] == "BLOQUEADO"

        # GET /api/tesoreria/fondos/1120/auditoria
        resp_audit = client.get("/api/tesoreria/fondos/1120/auditoria")
        assert resp_audit.status_code == 200
        audit_records = resp_audit.json()
        assert len(audit_records) >= 2
    finally:
        app.dependency_overrides.clear()
