"""Pruebas Unitarias y de Integración — Configuración de Saldos Iniciales y Apertura de Tesorería.

Verifica el cumplimiento de las directivas 2 y 3 del MEGA SYSTEM PROMPT:
- Inicialización estricta en $0 de todos los fondos físicos, digitales y obligaciones.
- Modificación auditada append-only de aperturas con motivo obligatorio.
- Restricción de permisos para Alan y Yilda.
- Inmutabilidad histórica: Bloqueo de alteración silenciosa de apertura si ya existen movimientos.
- Trazabilidad y consulta de historial de cambios.
"""

import sqlite3
import pytest
from fastapi.testclient import TestClient

from api.auth import AuthenticatedUser
from api.dependencies import get_current_user, require_admin_or_socio
from api.main import app
from tesoreria.aperturas import (
    ErrorAperturaTesoreria,
    ErrorFondoConMovimientos,
    modificar_saldo_inicial,
    obtener_historial_aperturas,
    obtener_saldos_iniciales_completos,
)
from tesoreria.isolation import conectar_db_laboratorio
from tesoreria.catalog import sembrar_catalogo_canónico
from tesoreria.schema import inicializar_esquema_laboratorio
from tesoreria.migrations_runner import ejecutar_migraciones_tesoreria
from tesoreria.movements import registerEntry


@pytest.fixture
def db_conn():
    """Crea una base de datos en memoria con el esquema y migraciones completas de tesorería."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    inicializar_esquema_laboratorio(conn)
    yield conn
    conn.close()


def test_saldos_iniciales_todos_en_cero(db_conn):
    """Verifica que todos los fondos y obligaciones abren estrictamente en $0."""
    res = obtener_saldos_iniciales_completos(db_conn)

    assert len(res["fondos_fisicos"]) == 6
    assert len(res["cuentas_digitales"]) == 4
    assert len(res["obligaciones"]) == 3

    assert res["total_fondos_fisicos_cents"] == 0
    assert res["total_cuentas_digitales_cents"] == 0
    assert res["total_obligaciones_cents"] == 0

    # Todos los fondos físicos en 0
    codigos_ff = [f["codigo"] for f in res["fondos_fisicos"]]
    assert set(codigos_ff) == {"1110", "1120", "1130", "1140", "1150", "1160"}
    for f in res["fondos_fisicos"]:
        assert f["saldo_inicial_cents"] == 0
        assert f["saldo_actual_cents"] == 0
        assert f["estado_configuracion"] == "EN_CERO"
        assert f["puede_editar_apertura"] is True

    # Cuentas digitales en 0
    codigos_cd = [c["codigo"] for c in res["cuentas_digitales"]]
    assert set(codigos_cd) == {"1210", "1220", "1230", "1310"}
    for c in res["cuentas_digitales"]:
        assert c["saldo_inicial_cents"] == 0
        assert c["saldo_actual_cents"] == 0
        assert c["estado_configuracion"] == "EN_CERO"

    # Obligaciones en 0 (David, Claro, Preventistas)
    codigos_ob = [o["codigo"] for o in res["obligaciones"]]
    assert set(codigos_ob) == {"DAVID", "CLARO", "PREVENTISTAS"}
    for o in res["obligaciones"]:
        assert o["saldo_inicial_cents"] == 0
        assert o["saldo_actual_cents"] == 0
        assert o["estado_configuracion"] == "EN_CERO"


def test_modificar_saldo_inicial_fondo_sin_movimientos(db_conn):
    """Permite configurar apertura de un fondo antes de iniciar operaciones, con auditoría."""
    res = modificar_saldo_inicial(
        db_conn,
        codigo="1110",
        saldo_nuevo_cents=5000000,  # $50.000
        usuario="alan",
        motivo="Apertura de caja de cambio para inicio de jornada",
        referencia_autorizacion="Acta 001",
    )

    assert res["item_codigo"] == "1110"
    assert res["saldo_anterior_cents"] == 0
    assert res["saldo_nuevo_cents"] == 5000000
    assert res["usuario_responsable"] == "alan"

    # Verificar que el fondo se actualizó
    estado = obtener_saldos_iniciales_completos(db_conn)
    f1110 = next(f for f in estado["fondos_fisicos"] if f["codigo"] == "1110")
    assert f1110["saldo_inicial_cents"] == 5000000
    assert f1110["saldo_actual_cents"] == 5000000
    assert f1110["estado_configuracion"] == "CONFIGURADO"
    assert f1110["configurado_por"] == "alan"

    # Verificar historial append-only
    historial = obtener_historial_aperturas(db_conn)
    assert len(historial) >= 14  # 13 iniciales + 1 modificación
    ultimo = historial[0]
    assert ultimo["item_codigo"] == "1110"
    assert ultimo["saldo_nuevo_cents"] == 5000000
    assert ultimo["motivo"] == "Apertura de caja de cambio para inicio de jornada"
    assert ultimo["usuario_responsable"] == "alan"


def test_modificar_saldo_inicial_obligacion(db_conn):
    """Permite configurar la apertura de obligaciones (David, Claro, Preventistas)."""
    res = modificar_saldo_inicial(
        db_conn,
        codigo="DAVID",
        saldo_nuevo_cents=1200000,  # $12.000
        usuario="yilda",
        motivo="Ajuste inicial pactado",
        referencia_autorizacion="Convenio firmado",
    )

    assert res["item_codigo"] == "DAVID"
    assert res["saldo_anterior_cents"] == 0
    assert res["saldo_nuevo_cents"] == 1200000
    assert res["usuario_responsable"] == "yilda"

    estado = obtener_saldos_iniciales_completos(db_conn)
    david = next(o for o in estado["obligaciones"] if o["codigo"] == "DAVID")
    assert david["saldo_inicial_cents"] == 1200000
    assert david["saldo_actual_cents"] == 1200000
    assert david["estado_configuracion"] == "CONFIGURADO"


def test_bloqueo_modificacion_con_movimientos_existentes(db_conn):
    """Impide modificar retroactivamente la apertura si el fondo ya tiene movimientos."""
    # 1. Abrir jornada y registrar un movimiento en 1110
    from tesoreria.jornadas import abrir_jornada
    jornada = abrir_jornada(db_conn, usuario="alan", observaciones="Jornada de prueba")

    cur = db_conn.cursor()
    cur.execute("SELECT id FROM tesoreria_fondos WHERE codigo = '1110';")
    f_id = cur.fetchone()[0]

    registerEntry(
        conn=db_conn,
        fund_code="1110",
        amount_cents=10000,
        counterpart="VENTA",
        reason="Venta de prueba",
        created_by="alan",
        idempotency_key="IDEM_TEST_001",
    )

    # 2. Intentar alterar la apertura de 1110 debe arrojar ErrorFondoConMovimientos
    with pytest.raises(ErrorFondoConMovimientos) as exc_info:
        modificar_saldo_inicial(
            db_conn,
            codigo="1110",
            saldo_nuevo_cents=2000000,
            usuario="alan",
            motivo="Intento de reescritura retroactiva",
        )
    assert "no está permitido alterar retroactivamente" in str(exc_info.value).lower()


def test_api_apertura_endpoints(tmp_path):
    """Verifica los endpoints REST de saldos iniciales con autorización y validación."""
    from api.routers import tesoreria
    db_file = str(tmp_path / "test_api_apertura.db")
    init_conn = sqlite3.connect(db_file)
    init_conn.row_factory = sqlite3.Row
    init_conn.execute("PRAGMA foreign_keys = ON;")
    inicializar_esquema_laboratorio(init_conn)
    init_conn.close()

    original_get_db = tesoreria._get_lab_db
    def _override_db():
        c = sqlite3.connect(db_file, timeout=30.0)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON;")
        return c

    tesoreria._get_lab_db = _override_db

    try:
        client = TestClient(app)

        # Simular usuario Alan (socio)
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
            username="alan", display_name="Alan", role="socio"
        )
        app.dependency_overrides[require_admin_or_socio] = lambda: AuthenticatedUser(
            username="alan", display_name="Alan", role="socio"
        )

        # 1. GET /api/tesoreria/apertura/saldos-iniciales
        resp = client.get("/api/tesoreria/apertura/saldos-iniciales")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["fondos_fisicos"]) == 6
        assert data["total_fondos_fisicos_cents"] == 0

        # 2. POST /api/tesoreria/apertura/saldos-iniciales (Éxito)
        payload = {
            "codigo": "1120",
            "saldo_nuevo_cents": 1500000,  # $15.000
            "motivo": "Fondo acumulado de reserva inicial verificado en caja fuerte",
            "referencia_autorizacion": "Arqueo Inicial Conjunto",
        }
        resp_post = client.post("/api/tesoreria/apertura/saldos-iniciales", json=payload)
        assert resp_post.status_code == 200
        res_data = resp_post.json()
        assert res_data["saldo_nuevo_cents"] == 1500000
        assert res_data["usuario_responsable"] == "alan"

        # 3. GET /api/tesoreria/apertura/historial
        resp_hist = client.get("/api/tesoreria/apertura/historial")
        assert resp_hist.status_code == 200
        hist = resp_hist.json()
        assert len(hist) >= 14
        assert hist[0]["item_codigo"] == "1120"
        assert hist[0]["saldo_nuevo_cents"] == 1500000

        # 4. Validación: Motivo vacío debe fallar con 422 o 400
        resp_err = client.post("/api/tesoreria/apertura/saldos-iniciales", json={
            "codigo": "1120",
            "saldo_nuevo_cents": 1000,
            "motivo": "  ",
        })
        assert resp_err.status_code in (400, 422)

        # 5. Validación: Saldo negativo debe ser rechazado
        resp_neg = client.post("/api/tesoreria/apertura/saldos-iniciales", json={
            "codigo": "1120",
            "saldo_nuevo_cents": -500,
            "motivo": "Ajuste negativo no permitido",
        })
        assert resp_neg.status_code == 422
    finally:
        tesoreria._get_lab_db = original_get_db
        app.dependency_overrides.clear()
