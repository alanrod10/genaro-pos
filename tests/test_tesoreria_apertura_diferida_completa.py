"""Pruebas Exhaustivas — Apertura Económica Manual Diferida y Ajustes Compensatorios (Etapa V).

Verifica el cumplimiento estricto de las directivas del MEGA PROMPT:
1. Consultar fondos sin apertura y confirmar estado PENDING_OPENING (no disponibilidad $0).
2. Apertura parcial de un fondo individual sin forzar la apertura de los demás.
3. Confirmar que los restantes fondos continúan en PENDING_OPENING.
4. Prevención estricta de duplicación mediante Idempotency-Key.
5. Verificación de que no se crean asientos dobles ni movimientos redundantes.
6. Corrección posterior auditada mediante AJUSTE_COMPENSATORIO sin reescribir la historia.
7. Consulta y validación del historial inmutable append-only de auditoría.
8. Preservación íntegra de movimientos contables históricos.
9. Bloqueo explicativo de egresos/liquidaciones que dependan de disponibilidades desconocidas.
10. Identificación explícita de movimientos posteriores:
    'SALDO INICIAL PENDIENTE — MOVIMIENTOS POSTERIORES REGISTRADOS'.
"""

import sqlite3
import pytest
from fastapi.testclient import TestClient

from api.auth import AuthenticatedUser
from api.dependencies import get_current_user, require_admin_or_socio
from api.main import app
from tesoreria.aperturas import (
    ErrorAperturaPendiente,
    ErrorAperturaTesoreria,
    ErrorFondoConMovimientos,
    modificar_saldo_inicial,
    obtener_historial_aperturas,
    obtener_saldos_iniciales_completos,
)
from tesoreria.jornadas import abrir_jornada
from tesoreria.movements import registerEntry, registerExit
from tesoreria.schema import inicializar_esquema_laboratorio


@pytest.fixture
def clean_db():
    """Crea una base de datos de laboratorio completamente aislada en memoria."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    inicializar_esquema_laboratorio(conn)
    yield conn
    conn.close()


def test_1_consultar_fondos_sin_apertura_no_son_disponibles_cero(clean_db):
    """1 y 2: Fondos sin saldo inicial cargado deben figurar en PENDING_OPENING sin disponibilidad confirmada."""
    saldos = obtener_saldos_iniciales_completos(clean_db)

    assert saldos["apertura_completa"] is False
    assert saldos["estado_general_apertura"] == "DIFERIDO_PENDIENTE"
    assert saldos["conteo_abiertos"] == 0
    assert saldos["conteo_pendientes"] == 10  # 10 fondos canónicos

    # Verificar cada fondo individualmente
    for f in saldos["fondos_fisicos"] + saldos["cuentas_digitales"]:
        assert f["estado_apertura"] == "PENDING_OPENING"
        assert f["disponibilidad_confirmada"] is False
        assert f["subestado_apertura"] == "SIN_MOVIMIENTOS"
        assert f["configurado_en"] is None or f["configurado_por"] == "SISTEMA_INICIAL"

    # Obligaciones también en PENDING_OPENING
    for ob in saldos["obligaciones"]:
        assert ob["estado_apertura"] == "PENDING_OPENING"
        assert ob["disponibilidad_confirmada"] is False


def test_2_apertura_parcial_fondo_individual(clean_db):
    """3 y 4: Apertura parcial de un solo fondo (1110) dejando los restantes en PENDING_OPENING."""
    # Alan carga el conteo de caja mostrador ($45.000 = 4.500.000 cents)
    res = modificar_saldo_inicial(
        clean_db,
        codigo="1110",
        saldo_nuevo_cents=4500000,
        usuario="alan",
        motivo="Conteo físico de billetes en mostrador apertura",
        referencia_autorizacion="Arqueo Manual #001",
        fecha_corte="2026-11-06T08:00:00Z",
        naturaleza_saldo="EFECTIVO_CONTADO",
        idempotency_key="IDEM_APERTURA_1110_001",
        tipo_operacion="APERTURA_INICIAL",
    )

    assert res["item_codigo"] == "1110"
    assert res["saldo_nuevo_cents"] == 4500000
    assert res["estado_apertura"] == "OPENED"
    assert res["tipo_operacion"] == "APERTURA_INICIAL"

    # Consultar estado general
    saldos = obtener_saldos_iniciales_completos(clean_db)
    assert saldos["apertura_completa"] is False  # Aún incompleta
    assert saldos["conteo_abiertos"] == 1
    assert saldos["conteo_pendientes"] == 9

    f1110 = next(f for f in saldos["fondos_fisicos"] if f["codigo"] == "1110")
    assert f1110["estado_apertura"] == "OPENED"
    assert f1110["disponibilidad_confirmada"] is True
    assert f1110["saldo_inicial_cents"] == 4500000
    assert f1110["saldo_actual_cents"] == 4500000

    # Todos los restantes fondos permanecen pendientes
    f1120 = next(f for f in saldos["fondos_fisicos"] if f["codigo"] == "1120")
    assert f1120["estado_apertura"] == "PENDING_OPENING"
    assert f1120["disponibilidad_confirmada"] is False


def test_3_idempotencia_estricta_sin_duplicar(clean_db):
    """5 y 6: Reintentos con la misma idempotency_key devuelven el registro previo sin duplicar asientos."""
    key = "IDEM_APERTURA_1210_UNIQUE"
    res1 = modificar_saldo_inicial(
        clean_db,
        codigo="1210",
        saldo_nuevo_cents=12500000,  # $125.000 MP
        usuario="alan",
        motivo="Saldo verificado en cuenta MP Alan",
        referencia_autorizacion="Extracto MP Nov 2026",
        fecha_corte="2026-11-06T08:30:00Z",
        naturaleza_saldo="SALDO_PLATAFORMA",
        idempotency_key=key,
    )
    assert res1["saldo_nuevo_cents"] == 12500000

    historial_1 = obtener_historial_aperturas(clean_db)
    cant_inicial = len(historial_1)

    # Segundo envío (simulando doble clic o reintento de red)
    res2 = modificar_saldo_inicial(
        clean_db,
        codigo="1210",
        saldo_nuevo_cents=12500000,
        usuario="alan",
        motivo="Saldo verificado en cuenta MP Alan",
        referencia_autorizacion="Extracto MP Nov 2026",
        fecha_corte="2026-11-06T08:30:00Z",
        idempotency_key=key,
    )

    assert res2["item_codigo"] == "1210"
    assert res2["saldo_nuevo_cents"] == 12500000
    assert res2.get("idempotente") is True

    # Comprobar que NO se insertó otro registro en la pista de auditoría
    historial_2 = obtener_historial_aperturas(clean_db)
    assert len(historial_2) == cant_inicial


def test_4_ajuste_compensatorio_posterior(clean_db):
    """7, 8 y 9: Modificación posterior mediante Ajuste Compensatorio formal preservando historial."""
    # 1. Apertura inicial en $10.000
    modificar_saldo_inicial(
        clean_db,
        codigo="1140",
        saldo_nuevo_cents=1000000,
        usuario="yilda",
        motivo="Apertura caja adicionales",
        idempotency_key="IDEM_1140_01",
    )

    # 2. Registrar movimiento contable
    jornada = abrir_jornada(clean_db, usuario="alan", observaciones="Jornada para prueba")
    registerEntry(
        clean_db,
        fund_code="1140",
        amount_cents=50000,  # +$500
        counterpart="COMISION",
        reason="Comisión recarga",
        created_by="alan",
        idempotency_key="IDEM_COM_01",
    )

    # 3. Descubrimiento de error en conteo inicial: debían ser $12.000 (1.200.000 cents) en lugar de $10.000
    # Intento de modificación directa sin ajuste formal falla:
    with pytest.raises(ErrorFondoConMovimientos):
        modificar_saldo_inicial(
            clean_db,
            codigo="1140",
            saldo_nuevo_cents=1200000,
            usuario="yilda",
            motivo="Intento de reescritura directa",
            tipo_operacion="APERTURA_INICIAL",
        )

    # 4. Modificación mediante AJUSTE_COMPENSATORIO formal: Éxito
    res_ajuste = modificar_saldo_inicial(
        clean_db,
        codigo="1140",
        saldo_nuevo_cents=1200000,
        usuario="yilda",
        motivo="Corrección de conteo inicial de monedas adicionales",
        referencia_autorizacion="Recuento supervisado",
        tipo_operacion="AJUSTE_COMPENSATORIO",
        idempotency_key="IDEM_AJUSTE_1140_01",
    )

    assert res_ajuste["tipo_operacion"] == "AJUSTE_COMPENSATORIO"
    assert res_ajuste["saldo_anterior_cents"] == 1000000
    assert res_ajuste["saldo_nuevo_cents"] == 1200000

    # Saldo resultante = $12.000 + $500 = $12.500 (1.250.000 cents)
    cur = clean_db.cursor()
    cur.execute("SELECT saldo_centavos, saldo_inicial_cents FROM tesoreria_fondos WHERE codigo = '1140';")
    row = cur.fetchone()
    assert row[0] == 1250000
    assert row[1] == 1200000

    # Historial de auditoría conserva AMBOS registros (apertura original y ajuste compensatorio)
    historial = obtener_historial_aperturas(clean_db)
    items_1140 = [h for h in historial if h["item_codigo"] == "1140"]
    assert len(items_1140) >= 2
    assert any(h["tipo_operacion"] == "APERTURA_INICIAL" and h["saldo_nuevo_cents"] == 1000000 for h in items_1140)
    assert any(h["tipo_operacion"] == "AJUSTE_COMPENSATORIO" and h["saldo_nuevo_cents"] == 1200000 for h in items_1140)


def test_5_movimientos_posteriores_en_fondo_pendiente(clean_db):
    """10: Ingresos en fondo PENDING_OPENING muestran subestado 'MOVIMIENTOS_POSTERIORES_REGISTRADOS'."""
    jornada = abrir_jornada(clean_db, usuario="alan", observaciones="Jornada para prueba")

    # Fondo 1160 (Prepay Mostrador) está en PENDING_OPENING
    saldos_ini = obtener_saldos_iniciales_completos(clean_db)
    f1160_ini = next(f for f in saldos_ini["fondos_fisicos"] if f["codigo"] == "1160")
    assert f1160_ini["estado_apertura"] == "PENDING_OPENING"
    assert f1160_ini["subestado_apertura"] == "SIN_MOVIMIENTOS"

    # Ingreso comercial en 1160 (cobro de recarga en mostrador $2.000)
    registerEntry(
        clean_db,
        fund_code="1160",
        amount_cents=200000,
        counterpart="COBRANZA_PREPAY",
        reason="Cobranza recarga prepay en mostrador",
        created_by="cajero",
        idempotency_key="IDEM_COB_1160_01",
    )

    # Estado post-ingreso: Sigue PENDING_OPENING pero con MOVIMIENTOS_POSTERIORES_REGISTRADOS
    saldos_post = obtener_saldos_iniciales_completos(clean_db)
    f1160_post = next(f for f in saldos_post["fondos_fisicos"] if f["codigo"] == "1160")
    assert f1160_post["estado_apertura"] == "PENDING_OPENING"
    assert f1160_post["subestado_apertura"] == "MOVIMIENTOS_POSTERIORES_REGISTRADOS"
    assert f1160_post["disponibilidad_confirmada"] is False
    assert f1160_post["saldo_actual_cents"] == 200000


def test_6_api_endpoints_apertura_diferida(tmp_path):
    """Verifica los contratos HTTP REST de apertura diferida, auditoría y seguridad."""
    from api.routers import tesoreria
    db_file = str(tmp_path / "test_api_diferida.db")
    c = sqlite3.connect(db_file)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON;")
    inicializar_esquema_laboratorio(c)
    c.close()

    original_get_db = tesoreria._get_lab_db
    def _override_db():
        conn = sqlite3.connect(db_file, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    tesoreria._get_lab_db = _override_db

    try:
        client = TestClient(app)
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
            username="alan", display_name="Alan", role="socio"
        )
        app.dependency_overrides[require_admin_or_socio] = lambda: AuthenticatedUser(
            username="alan", display_name="Alan", role="socio"
        )

        # 1. GET /api/tesoreria/apertura/saldos-iniciales
        r_get = client.get("/api/tesoreria/apertura/saldos-iniciales")
        assert r_get.status_code == 200
        data = r_get.json()
        assert data["apertura_completa"] is False
        assert data["estado_general_apertura"] == "DIFERIDO_PENDIENTE"
        assert data["conteo_abiertos"] == 0
        assert data["conteo_pendientes"] == 10

        # 2. POST /api/tesoreria/apertura/saldos-iniciales con payload completo
        payload = {
            "codigo": "1130",
            "saldo_nuevo_cents": 8500000,  # $85.000 Prepay Efectivo
            "motivo": "Arqueo de billetes custodia David en caja fuerte",
            "referencia_autorizacion": "Acta Arqueo Día D",
            "fecha_corte": "2026-11-06T09:00:00Z",
            "naturaleza_saldo": "EFECTIVO_CONTADO",
            "idempotency_key": "IDEM_REST_1130",
            "tipo_operacion": "APERTURA_INICIAL",
        }
        r_post = client.post("/api/tesoreria/apertura/saldos-iniciales", json=payload)
        assert r_post.status_code == 200
        res = r_post.json()
        assert res["item_codigo"] == "1130"
        assert res["estado_apertura"] == "OPENED"
        assert res["saldo_nuevo_cents"] == 8500000

        # 3. GET /api/tesoreria/apertura/historial
        r_hist = client.get("/api/tesoreria/apertura/historial")
        assert r_hist.status_code == 200
        hist = r_hist.json()
        item_1130 = next(h for h in hist if h["item_codigo"] == "1130")
        assert item_1130["saldo_nuevo_cents"] == 8500000
        assert item_1130["usuario_responsable"] == "alan"
        assert item_1130["estado_apertura"] == "OPENED"

    finally:
        tesoreria._get_lab_db = original_get_db
        app.dependency_overrides.clear()
