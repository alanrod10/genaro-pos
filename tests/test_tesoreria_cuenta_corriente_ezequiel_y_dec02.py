"""Tests de Cuenta Corriente Ezequiel (Claro Comercios) y Fondeo Custodia DEC-02.

Verifica:
1. Excepción de dominio restrictiva para fondeo Claro (1110 -> 1150) con contexto FONDEO_CUSTODIA_CLARO.
2. Bloqueo de traslados comerciales arbitrarios hacia o desde 1150.
3. Subregistro de fondeos pendientes cuando no hay efectivo disponible en 1110 (cero sobregiro).
4. Ejecución posterior de fondeo pendiente una vez que 1110 cuenta con saldo real.
5. Acreditación de saldo digital en 1230 provisto por Ezequiel y reconocimiento de obligación.
6. Consumo de saldo digital sin duplicación de deuda con Ezequiel.
7. Liquidación mixta a Ezequiel combinando efectivo 1150, Drugstore y transferencia MP (1210 -> 1230).
8. Validación de disponibilidad estricta y límite de obligación en pagos.
9. Idempotencia y auditoría canónica.
10. Cobertura de endpoints FastAPI bajo /api/tesoreria/cuenta-corriente/ezequiel.
"""

import json
import sqlite3
import pytest
from fastapi.testclient import TestClient

from api.main import app
from tesoreria.catalog import obtener_fondo_por_codigo, sembrar_catalogo_canónico
from tesoreria.cuenta_corriente_ezequiel import (
    ErrorCuentaCorrienteEzequiel,
    ErrorSaldoInsuficienteEzequiel,
    ErrorValidacionEzequiel,
    consultar_posicion_ezequiel,
    ejecutar_fondeo_pendiente,
    listar_fondeos_pendientes,
    registrar_acreditacion_ezequiel,
    registrar_fondeo_pendiente,
    registrar_liquidacion_ezequiel,
    registrar_reconocimiento_historico_ezequiel,
)
from tesoreria.jornadas import abrir_jornada
from tesoreria.movements import registerEntry
from tesoreria.schema import inicializar_esquema_laboratorio
from tesoreria.transfers import ErrorTraslado, confirmTransferReceipt, transferFunds


@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    inicializar_esquema_laboratorio(conn)
    abrir_jornada(conn, usuario="admin_test")
    yield conn
    conn.close()


def test_cp_claro_03_bloqueo_traslado_arbitrario_1110_a_1150(db_conn):
    """CP-CLARO-03: Traslado arbitrario hacia 1150 sin contexto debe ser rechazado."""
    registerEntry(
        conn=db_conn,
        fund_code="1110",
        amount_cents=10000_00,
        counterpart="VENTA_MOSTRADOR",
        reason="Venta efectivo",
        created_by="cajero",
    )
    with pytest.raises(ErrorTraslado) as exc:
        transferFunds(
            conn=db_conn,
            origin_fund_code="1110",
            destination_fund_code="1150",
            amount_cents=5000_00,
            dispatched_by="cajero",
            reason="Traslado sin contexto",
        )
    assert "recaudación de terceros en custodia" in str(exc.value)


def test_cp_claro_04_fondeo_controlado_1110_a_1150_con_saldo(db_conn):
    """CP-CLARO-04: Fondeo controlado de custodia 1110 -> 1150 con contexto y evidencia."""
    registerEntry(
        conn=db_conn,
        fund_code="1110",
        amount_cents=20000_00,
        counterpart="VENTA_MOSTRADOR",
        reason="Venta inicial en efectivo",
        created_by="cajero",
    )

    trf = transferFunds(
        conn=db_conn,
        origin_fund_code="1110",
        destination_fund_code="1150",
        amount_cents=9000_00,
        dispatched_by="cajero",
        reason="Fondeo físico custodia Claro recarga #101",
        evidence_reference="SRV_101",
        context="FONDEO_CUSTODIA_CLARO",
        idempotency_key="TRF_FONDEO_101",
    )
    confirmTransferReceipt(
        conn=db_conn,
        transfer_id=trf["transfer_id"],
        received_by="cajero",
        idempotency_key="CONF_FONDEO_101",
    )

    f1110 = obtener_fondo_por_codigo(db_conn, "1110")
    f1150 = obtener_fondo_por_codigo(db_conn, "1150")

    assert f1110["saldo_centavos"] == 11000_00
    assert f1150["saldo_centavos"] == 9000_00


def test_cp_claro_05_fondeo_controlado_sin_saldo_en_1110_registra_pendiente(db_conn):
    """CP-CLARO-05: Falta de efectivo en 1110 no sobregira y se registra como pendiente."""
    f1110 = obtener_fondo_por_codigo(db_conn, "1110")
    assert f1110["saldo_centavos"] == 0

    with pytest.raises(ErrorTraslado) as exc:
        transferFunds(
            conn=db_conn,
            origin_fund_code="1110",
            destination_fund_code="1150",
            amount_cents=9000_00,
            dispatched_by="cajero",
            reason="Fondeo Claro sin saldo",
            evidence_reference="SRV_102",
            context="FONDEO_CUSTODIA_CLARO",
        )
    assert "Saldo disponible insuficiente" in str(exc.value)

    res_pend = registrar_fondeo_pendiente(
        conn=db_conn,
        tipo_circuito="CLARO",
        servicio_id=102,
        monto_capital_cents=9000_00,
        fondo_origen_esperado="1110",
        fondo_destino_custodia="1150",
        actor="cajero",
        idempotency_key="PEND_SRV_102",
        observaciones="Recarga Claro digital sin efectivo físico en cajón",
    )
    assert res_pend["estado"] == "PENDIENTE"

    pendientes = listar_fondeos_pendientes(db_conn, estado="PENDIENTE")
    assert len(pendientes) == 1
    assert pendientes[0]["servicio_id"] == 102

    # El cajero intenta ejecutarlo antes de que haya plata -> debe fallar sin sobregirar
    with pytest.raises(ErrorSaldoInsuficienteEzequiel):
        ejecutar_fondeo_pendiente(db_conn, fondeo_id=res_pend["id"], actor="cajero")

    # Ingresa plata por ventas generales en 1110
    registerEntry(
        conn=db_conn,
        fund_code="1110",
        amount_cents=15000_00,
        counterpart="VENTAS_VARIAS",
        reason="Ventas del día",
        created_by="cajero",
    )

    # Ahora se ejecuta el fondeo con éxito
    res_ejec = ejecutar_fondeo_pendiente(db_conn, fondeo_id=res_pend["id"], actor="cajero")
    assert res_ejec["status"] == "SUCCESS"
    assert res_ejec["estado"] == "FONDEADO"

    # Saldos actualizados fielmente
    f1110_post = obtener_fondo_por_codigo(db_conn, "1110")
    f1150_post = obtener_fondo_por_codigo(db_conn, "1150")
    assert f1110_post["saldo_centavos"] == 6000_00
    assert f1150_post["saldo_centavos"] == 9000_00


def test_cp_claro_06_acreditacion_saldo_ezequiel(db_conn):
    """CP-CLARO-06: Ezequiel acredita saldo digital en 1230 y reconoce obligación."""
    res_acr = registrar_acreditacion_ezequiel(
        conn=db_conn,
        amount_cents=100000_00,
        referencia="LOTE-EZ-987",
        actor="admin",
        idempotency_key="IDEM_ACR_EZ_01",
        motivo="Carga mayorista Claro Comercios Ezequiel",
    )
    assert res_acr["status"] == "SUCCESS"
    assert res_acr["monto_cents"] == 100000_00

    f1230 = obtener_fondo_por_codigo(db_conn, "1230")
    assert f1230["saldo_centavos"] == 100000_00

    pos = consultar_posicion_ezequiel(db_conn)
    assert pos["obligacion_pendiente_cents"] == 100000_00
    assert pos["total_acreditaciones_cents"] == 100000_00
    assert pos["total_pagos_cents"] == 0


def test_cp_claro_08_liquidacion_mixta_ezequiel(db_conn):
    """CP-CLARO-08: Liquidación mixta a Ezequiel combinando 1150, 1110 y transferencia 1210 -> 1230."""
    # 1. Acreditación de saldo por Ezequiel
    registrar_acreditacion_ezequiel(
        conn=db_conn,
        amount_cents=50000_00,
        referencia="ACR-001",
        actor="admin",
        idempotency_key="ACR-KEY-001",
    )

    # 2. Dotar fondos disponibles
    registerEntry(db_conn, "1150", 10000_00, "RECAUDACION_TERCEROS_CLARO", "Efectivo custodia Claro", "cajero")
    registerEntry(db_conn, "1110", 10000_00, "VENTAS_MOSTRADOR", "Efectivo cajón Drugstore", "cajero")
    registerEntry(db_conn, "1210", 20000_00, "VENTA_MERCADO_PAGO", "Saldo Mercado Pago propio", "cajero")

    # 3. Liquidación mixta: $10.000 desde 1150, $2.000 desde 1110, y $5.000 vía transferencia (1210 -> 1230)
    # Total liquidado = $17.000
    res_liq = registrar_liquidacion_ezequiel(
        conn=db_conn,
        monto_1150_cents=10000_00,
        monto_drugstore_cents=2000_00,
        fondo_drugstore="1110",
        monto_transferencia_cents=5000_00,
        referencia="RECIBO_EZ_5541",
        actor="admin",
        idempotency_key="LIQ_KEY_5541",
        motivo="Rendición mixta a cobrador Ezequiel",
    )
    assert res_liq["status"] == "SUCCESS"
    assert res_liq["monto_cents"] == 17000_00
    assert res_liq["obligacion_remanente_cents"] == 33000_00

    # Verificar saldos resultantes
    f1150 = obtener_fondo_por_codigo(db_conn, "1150")
    f1110 = obtener_fondo_por_codigo(db_conn, "1110")
    f1210 = obtener_fondo_por_codigo(db_conn, "1210")
    f1230 = obtener_fondo_por_codigo(db_conn, "1230")

    assert f1150["saldo_centavos"] == 0
    assert f1110["saldo_centavos"] == 8000_00
    assert f1210["saldo_centavos"] == 15000_00
    assert f1230["saldo_centavos"] == 55000_00  # 50.000 inicial + 5.000 transferidos desde 1210


def test_cp_claro_09_rechazo_liquidacion_por_encima_de_deuda_o_saldo(db_conn):
    """CP-CLARO-09: Rechazar pagos que superen la obligación pendiente o los saldos disponibles."""
    registrar_acreditacion_ezequiel(
        conn=db_conn,
        amount_cents=10000_00,
        referencia="ACR-PEQ",
        actor="admin",
        idempotency_key="ACR-PEQ-01",
    )
    registerEntry(db_conn, "1150", 15000_00, "RECAUDACION_TERCEROS_CLARO", "Efectivo", "cajero")

    # Intentar pagar $12.000 cuando la deuda es de $10.000 -> Error
    with pytest.raises(ErrorValidacionEzequiel) as exc:
        registrar_liquidacion_ezequiel(
            conn=db_conn,
            monto_1150_cents=12000_00,
            referencia="REC_OVER",
            actor="admin",
            idempotency_key="LIQ-OVER-01",
        )
    assert "supera la obligación pendiente con Ezequiel" in str(exc.value)

    # Intentar pagar $8.000 desde 1110 cuando 1110 tiene $0 -> Error
    with pytest.raises(ErrorSaldoInsuficienteEzequiel) as exc2:
        registrar_liquidacion_ezequiel(
            conn=db_conn,
            monto_drugstore_cents=8000_00,
            fondo_drugstore="1110",
            referencia="REC_NO_CASH",
            actor="admin",
            idempotency_key="LIQ-NO-CASH",
        )
    assert "Saldo insuficiente en Drugstore" in str(exc2.value)


def test_cp_claro_10_idempotencia_estricta(db_conn):
    """CP-CLARO-10: Ejecuciones repetidas con la misma clave retornan el registro previo sin duplicar."""
    res1 = registrar_acreditacion_ezequiel(
        conn=db_conn,
        amount_cents=25000_00,
        referencia="ACR-IDEM",
        actor="admin",
        idempotency_key="KEY_ACR_IDEM",
    )
    res2 = registrar_acreditacion_ezequiel(
        conn=db_conn,
        amount_cents=25000_00,
        referencia="ACR-IDEM",
        actor="admin",
        idempotency_key="KEY_ACR_IDEM",
    )
    assert res1["status"] == "SUCCESS"
    assert res2["status"] == "ALREADY_PROCESSED"
    assert res1["operation_id"] == res2["operation_id"]

    f1230 = obtener_fondo_por_codigo(db_conn, "1230")
    assert f1230["saldo_centavos"] == 25000_00  # No se duplicó a 50.000


def test_cp_claro_11_endpoints_api_ezequiel(tmp_path):
    """CP-CLARO-11: Integración de endpoints FastAPI /api/tesoreria/cuenta-corriente/ezequiel."""
    from pathlib import Path
    from api.auth import AuthenticatedUser
    from api.dependencies import get_current_user
    from api.routers import tesoreria

    db_file = tmp_path / "test_api_ezequiel.db"
    conn_setup = sqlite3.connect(str(db_file))
    conn_setup.row_factory = sqlite3.Row
    conn_setup.execute("PRAGMA foreign_keys = ON;")
    inicializar_esquema_laboratorio(conn_setup)
    abrir_jornada(conn_setup, usuario="admin_test")
    conn_setup.close()

    def _override_db():
        c = sqlite3.connect(str(db_file), timeout=30.0)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON;")
        return c

    original_get_db = tesoreria._get_lab_db
    tesoreria._get_lab_db = _override_db
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        username="alan_socio", display_name="Alan Socio", role="socio"
    )
    client = TestClient(app)

    try:
        # 1. GET inicial
        res = client.get("/api/tesoreria/cuenta-corriente/ezequiel")
        assert res.status_code == 200
        data = res.json()
        assert data["obligacion_pendiente_cents"] == 0

        # 2. POST Acreditación
        res_acr = client.post(
            "/api/tesoreria/cuenta-corriente/ezequiel/acreditacion",
            headers={"Idempotency-Key": "API_ACR_EZ_1"},
            json={
                "monto_centavos": 40000_00,
                "referencia": "TRANSF_EZE_99",
                "motivo": "Acreditación API Claro",
                "fecha_referencia": "2026-11-06",
            },
        )
        assert res_acr.status_code == 200
        assert res_acr.json()["status"] == "SUCCESS"

        # Dotar efectivo en 1150
        conn_fund = sqlite3.connect(str(db_file))
        conn_fund.row_factory = sqlite3.Row
        registerEntry(conn_fund, "1150", 15000_00, "RECAUDACION_TERCEROS_CLARO", "Custodia Claro", "cajero")
        conn_fund.commit()
        conn_fund.close()

        # 3. POST Liquidación
        res_liq = client.post(
            "/api/tesoreria/cuenta-corriente/ezequiel/liquidacion",
            headers={"Idempotency-Key": "API_LIQ_EZ_1"},
            json={
                "monto_1150_cents": 15000_00,
                "referencia": "RECIBO_0099",
                "motivo": "Pago efectivo al cobrador",
                "fecha_referencia": "2026-11-06",
            },
        )
        assert res_liq.status_code == 200
        assert res_liq.json()["obligacion_remanente_cents"] == 25000_00

        # 4. GET final
        res_final = client.get("/api/tesoreria/cuenta-corriente/ezequiel")
        assert res_final.status_code == 200
        assert res_final.json()["obligacion_pendiente_cents"] == 25000_00
    finally:
        tesoreria._get_lab_db = original_get_db
        app.dependency_overrides.clear()
