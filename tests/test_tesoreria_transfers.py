"""Pruebas de Traslados de Fondos — LAB-006 (FLOW-029 a FLOW-034).

Verificación de traslados entre fondos de tesorería:
- FLOW-029: transferFunds debita origen y pone en tránsito (PENDING_RECEIPT).
- FLOW-030: Rechazo de mismo origen y destino.
- FLOW-031: Rechazo por saldo disponible insuficiente en origen.
- FLOW-032: confirmTransferReceipt acredita en destino y marca CONFIRMED.
- FLOW-033: cancelPendingTransfer cancela y restituye saldo al origen con REVERSAL.
- FLOW-034: Idempotencia de operaciones de traslado.
- Inmutabilidad de registros de traslado.
"""

import sqlite3
import pytest

from tesoreria.isolation import conectar_db_laboratorio
from tesoreria.schema import inicializar_esquema_laboratorio
from tesoreria.catalog import sembrar_catalogo_canónico, obtener_fondo_por_codigo
from tesoreria.jornadas import abrir_jornada
from tesoreria.movements import registerEntry, listar_movimientos
from tesoreria.transfers import (
    ErrorTraslado,
    cancelPendingTransfer,
    confirmTransferReceipt,
    listar_traslados,
    transferFunds,
)
from tesoreria.idempotency import ErrorIdempotencia


@pytest.fixture
def conn_lab(tmp_path):
    """Crea una base SQLite con catálogo, jornada abierta y $100.000 en 1110."""
    db_file = tmp_path / "tesoreria_transfers_test.db"
    conn = conectar_db_laboratorio(str(db_file))
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)
    abrir_jornada(conn, usuario="alan", observaciones="Jornada para pruebas LAB-006")
    # Cargar fondos en 1110
    registerEntry(
        conn=conn,
        fund_code="1110",
        amount_cents=100000_00,
        counterpart="Aporte inicial",
        reason="Dotación para traslados",
        created_by="alan",
    )
    yield conn
    conn.close()


def test_flow_029_transfer_funds_inicio_y_transito(conn_lab):
    """FLOW-029: transferFunds debita origen y pone en tránsito sin acreditar en destino."""
    fondo_1110_antes = obtener_fondo_por_codigo(conn_lab, "1110")
    fondo_1120_antes = obtener_fondo_por_codigo(conn_lab, "1120")
    assert fondo_1110_antes["saldo_centavos"] == 100000_00
    assert fondo_1120_antes["saldo_centavos"] == 0

    t = transferFunds(
        conn=conn_lab,
        origin_fund_code="1110",
        destination_fund_code="1120",
        amount_cents=30000_00,
        dispatched_by="alan",
        reason="Envío de cambio para Caja Secundaria",
        idempotency_key="idemp_trf_001",
    )

    assert t["status"] == "PENDING_RECEIPT"
    assert t["amount_cents"] == 30000_00
    assert t["received_by"] is None

    # Verificar saldos: Origen debitado, Destino AÚN EN CERO (dinero en tránsito)
    fondo_1110_despues = obtener_fondo_por_codigo(conn_lab, "1110")
    fondo_1120_despues = obtener_fondo_por_codigo(conn_lab, "1120")
    assert fondo_1110_despues["saldo_centavos"] == 70000_00
    assert fondo_1120_despues["saldo_centavos"] == 0, "El destino no debe recibir el dinero antes de confirmación física!"


def test_flow_030_rechazo_mismo_origen_y_destino(conn_lab):
    """FLOW-030: Rechazo si origen y destino son iguales."""
    with pytest.raises(ErrorTraslado, match="deben ser diferentes"):
        transferFunds(
            conn=conn_lab,
            origin_fund_code="1110",
            destination_fund_code="1110",
            amount_cents=1000_00,
            dispatched_by="alan",
            reason="Mismo fondo",
        )


def test_flow_031_rechazo_saldo_insuficiente_origen(conn_lab):
    """FLOW-031: Rechazo si el origen no tiene saldo disponible suficiente."""
    with pytest.raises(ErrorTraslado, match="Saldo disponible insuficiente"):
        transferFunds(
            conn=conn_lab,
            origin_fund_code="1110",
            destination_fund_code="1120",
            amount_cents=500000_00,  # Excede los 100.000 disponibles
            dispatched_by="alan",
            reason="Exceso",
        )


def test_flow_032_confirm_transfer_receipt(conn_lab):
    """FLOW-032: confirmTransferReceipt acredita fondos en destino y genera TRANSFER_IN."""
    t = transferFunds(
        conn=conn_lab,
        origin_fund_code="1110",
        destination_fund_code="1120",
        amount_cents=25000_00,
        dispatched_by="alan",
        reason="Traslado operativo",
    )

    # Confirmar recepción por cajero receptor
    res_conf = confirmTransferReceipt(
        conn=conn_lab,
        transfer_id=t["transfer_id"],
        received_by="yilda",
        idempotency_key="idemp_conf_001",
    )

    assert res_conf["status"] == "CONFIRMED"
    assert res_conf["received_by"] == "yilda"
    assert res_conf["in_movement_id"] is not None

    # Verificar que ahora 1120 tiene los 25.000 centavos
    fondo_1120 = obtener_fondo_por_codigo(conn_lab, "1120")
    assert fondo_1120["saldo_centavos"] == 25000_00

    # Movimiento TRANSFER_IN registrado
    movs = listar_movimientos(conn_lab, codigo_fondo="1120")
    assert len(movs) == 1
    assert movs[0]["movement_type"] == "TRANSFER_IN"


def test_flow_033_cancel_pending_transfer(conn_lab):
    """FLOW-033: cancelPendingTransfer cancela y restituye fondos al origen."""
    t = transferFunds(
        conn=conn_lab,
        origin_fund_code="1110",
        destination_fund_code="1120",
        amount_cents=15000_00,
        dispatched_by="alan",
        reason="Traslado que se cancelará",
    )

    assert obtener_fondo_por_codigo(conn_lab, "1110")["saldo_centavos"] == 85000_00

    # Cancelar traslado
    res_cancel = cancelPendingTransfer(
        conn=conn_lab,
        transfer_id=t["transfer_id"],
        cancelled_by="alan",
        reason="Se canceló la apertura de caja 2",
        idempotency_key="idemp_canc_001",
    )

    assert res_cancel["status"] == "CANCELLED"
    # Fondos restituidos en 1110 (vuelve a 100.000)
    assert obtener_fondo_por_codigo(conn_lab, "1110")["saldo_centavos"] == 100000_00

    # Movimiento REVERSAL registrado
    movs = listar_movimientos(conn_lab, codigo_fondo="1110")
    tipos = [m["movement_type"] for m in movs]
    assert "REVERSAL" in tipos


def test_flow_034_idempotencia_traslados(conn_lab):
    """FLOW-034: Reintentos de traslado o confirmación no duplican saldo."""
    k = "idemp_trf_repeat_test"

    t1 = transferFunds(
        conn=conn_lab,
        origin_fund_code="1110",
        destination_fund_code="1130",
        amount_cents=10000_00,
        dispatched_by="alan",
        reason="Envío a tesoro",
        idempotency_key=k,
    )

    # Reintento con misma clave
    t2 = transferFunds(
        conn=conn_lab,
        origin_fund_code="1110",
        destination_fund_code="1130",
        amount_cents=10000_00,
        dispatched_by="alan",
        reason="Envío a tesoro",
        idempotency_key=k,
    )

    assert t1["transfer_id"] == t2["transfer_id"]
    # 1110 solo fue debitado una vez por 10.000 (100.000 - 10.000 = 90.000)
    assert obtener_fondo_por_codigo(conn_lab, "1110")["saldo_centavos"] == 90000_00


def test_inmutabilidad_traslados_delete(conn_lab):
    """Verifica que el trigger trg_proteger_traslados_delete prohíba eliminar traslados."""
    t = transferFunds(
        conn=conn_lab,
        origin_fund_code="1110",
        destination_fund_code="1120",
        amount_cents=5000_00,
        dispatched_by="alan",
        reason="Prueba inmutabilidad",
    )

    cursor = conn_lab.cursor()
    with pytest.raises(sqlite3.IntegrityError, match="No está permitido eliminar registros de traslados"):
        cursor.execute("DELETE FROM tesoreria_traslados WHERE transfer_id = ?", (t["transfer_id"],))
    cursor.close()
