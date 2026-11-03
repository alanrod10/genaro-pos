"""Pruebas Automatizadas de Traslados Internos de Tesorería — GENARO POS.

Cubre exhaustivamente las 16 condiciones requeridas:
1. Despacho válido (1110 -> 1120, etc.).
2. Rechazo por importe <= 0.
3. Rechazo por saldo disponible insuficiente en origen.
4. Rechazo si origen == destino.
5. Rechazo de combinaciones de fondos no autorizadas (matriz de políticas).
6. Confirmación de recepción (PENDING_RECEIPT -> CONFIRMED, in_movement_id, saldo acreditado).
7. Rechazo de confirmación duplicada (segunda confirmación no altera saldos).
8. Anulación de traslado pendiente (CANCELLED, REVERSAL vinculado, restituye origen).
9. Rechazo de anulación después de recibir.
10. Rechazo de recepción después de anular.
11. Rechazo de anulación duplicada.
12. Rollback transaccional ante fallo intermedio.
13. Verificación de saldos registrados y teóricos (diferencia_teorica_cents == 0).
14. Verificación de vínculos de trazabilidad (out_movement_id, in_movement_id, reversal_of_id).
15. Verificación de no generación de ingresos/egresos comerciales.
16. Idempotencia ante reintentos con misma idempotency_key.
"""

import sqlite3
from unittest.mock import patch
import pytest

from db.migrations_runner import ejecutar_migraciones
from tesoreria.schema import inicializar_esquema_laboratorio
from tesoreria.catalog import sembrar_catalogo_canónico, obtener_fondo_por_codigo
from tesoreria.jornadas import abrir_jornada
from tesoreria.availability import getFundBalance
from tesoreria.movements import registerEntry, listar_movimientos
from tesoreria.transfers import (
    transferFunds,
    confirmTransferReceipt,
    cancelPendingTransfer,
    listar_traslados,
    ErrorTraslado,
    TRANSFERENCIAS_PERMITIDAS,
)


@pytest.fixture
def disposable_dbs():
    """Provee un par de bases SQLite en memoria totalmente aisladas y descartables."""
    comm = sqlite3.connect(":memory:")
    comm.row_factory = sqlite3.Row
    ejecutar_migraciones(comm)

    tes = sqlite3.connect(":memory:")
    tes.row_factory = sqlite3.Row
    inicializar_esquema_laboratorio(tes)
    sembrar_catalogo_canónico(tes)
    abrir_jornada(tes, usuario="alan_tester", observaciones="Jornada de pruebas de traslados")

    # Dotación inicial: $100.000 en 1110 (Drugstore Diario)
    registerEntry(
        conn=tes,
        fund_code="1110",
        amount_cents=100000_00,
        counterpart="Aporte de Caja",
        reason="Dotación para pruebas",
        created_by="alan_tester",
    )

    yield comm, tes
    comm.close()
    tes.close()


# -------------------------------------------------------------------------
# 1. Despacho válido
# -------------------------------------------------------------------------
def test_01_despacho_valido(disposable_dbs):
    """1. Despacho válido debita origen y deja fondos en tránsito (PENDING_RECEIPT)."""
    _, tes = disposable_dbs
    f1110_antes = obtener_fondo_por_codigo(tes, "1110")
    f1120_antes = obtener_fondo_por_codigo(tes, "1120")
    assert f1110_antes["saldo_centavos"] == 100000_00
    assert f1120_antes["saldo_centavos"] == 0

    t = transferFunds(
        conn=tes,
        origin_fund_code="1110",
        destination_fund_code="1120",
        amount_cents=30000_00,
        dispatched_by="alan_tester",
        reason="Envío de cambio a Drugstore Acumulado",
        evidence_reference="ENVIO-001",
    )

    assert t["status"] == "PENDING_RECEIPT"
    assert t["amount_cents"] == 30000_00
    assert t["origin_fund_code"] == "1110"
    assert t["destination_fund_code"] == "1120"
    assert t["received_by"] is None
    assert t["received_at"] is None
    assert t["out_movement_id"] is not None

    # Verificar saldos
    f1110_despues = obtener_fondo_por_codigo(tes, "1110")
    f1120_despues = obtener_fondo_por_codigo(tes, "1120")
    assert f1110_despues["saldo_centavos"] == 70000_00
    assert f1120_despues["saldo_centavos"] == 0  # En tránsito, aún no acreditado

    # Verificar movimiento TRANSFER_OUT
    movs = listar_movimientos(tes, codigo_fondo="1110")
    out_mov = next((m for m in movs if m["movement_id"] == t["out_movement_id"]), None)
    assert out_mov is not None
    assert out_mov["movement_type"] == "TRANSFER_OUT"
    assert out_mov["amount_cents"] == 30000_00


# -------------------------------------------------------------------------
# 2. Rechazo por importe <= 0
# -------------------------------------------------------------------------
def test_02_rechazo_importe_invalido(disposable_dbs):
    """2. Rechazo por importe igual a cero o negativo."""
    _, tes = disposable_dbs

    with pytest.raises(ErrorTraslado, match="estrictamente positivo"):
        transferFunds(
            conn=tes,
            origin_fund_code="1110",
            destination_fund_code="1120",
            amount_cents=0,
            dispatched_by="alan_tester",
            reason="Importe cero",
        )

    with pytest.raises(Exception):  # ErrorTraslado or AssertValidMoney
        transferFunds(
            conn=tes,
            origin_fund_code="1110",
            destination_fund_code="1120",
            amount_cents=-1000,
            dispatched_by="alan_tester",
            reason="Importe negativo",
        )


# -------------------------------------------------------------------------
# 3. Rechazo por saldo insuficiente en origen
# -------------------------------------------------------------------------
def test_03_rechazo_saldo_insuficiente(disposable_dbs):
    """3. Rechazo si el origen no dispone de saldo suficiente."""
    _, tes = disposable_dbs
    with pytest.raises(ErrorTraslado, match="Saldo disponible insuficiente"):
        transferFunds(
            conn=tes,
            origin_fund_code="1110",
            destination_fund_code="1120",
            amount_cents=150000_00,  # Disponible son 100.000
            dispatched_by="alan_tester",
            reason="Exceso de fondos",
        )


# -------------------------------------------------------------------------
# 4. Rechazo si origen == destino
# -------------------------------------------------------------------------
def test_04_rechazo_mismo_origen_destino(disposable_dbs):
    """4. Rechazo si origen y destino son idénticos."""
    _, tes = disposable_dbs
    with pytest.raises(ErrorTraslado, match="deben ser diferentes"):
        transferFunds(
            conn=tes,
            origin_fund_code="1110",
            destination_fund_code="1110",
            amount_cents=5000_00,
            dispatched_by="alan_tester",
            reason="Mismo fondo",
        )


# -------------------------------------------------------------------------
# 5. Rechazo de combinaciones de fondos no autorizadas (matriz de políticas)
# -------------------------------------------------------------------------
def test_05_rechazo_combinaciones_no_autorizadas(disposable_dbs):
    """5. Rechazo de combinaciones no autorizadas según matriz de políticas."""
    _, tes = disposable_dbs

    # 1150 (Claro Físico custodia) no puede transferir a Drugstore
    with pytest.raises(ErrorTraslado, match="Claro"):
        transferFunds(
            conn=tes,
            origin_fund_code="1150",
            destination_fund_code="1110",
            amount_cents=1000_00,
            dispatched_by="alan_tester",
            reason="Claro a Drugstore",
        )

    # Fondear Tránsito preventistas (1310) no permitido
    with pytest.raises(ErrorTraslado, match="Tránsito Preventistas"):
        transferFunds(
            conn=tes,
            origin_fund_code="1110",
            destination_fund_code="1310",
            amount_cents=1000_00,
            dispatched_by="alan_tester",
            reason="Fondear tránsito",
        )

    # PREPAY Digital (1220) requiere definición del titular
    with pytest.raises(ErrorTraslado, match="cuentas mayoristas de plataformas"):
        transferFunds(
            conn=tes,
            origin_fund_code="1210",
            destination_fund_code="1220",
            amount_cents=1000_00,
            dispatched_by="alan_tester",
            reason="A PREPAY Digital",
        )

    # Claro Digital (1230) requiere definición del titular
    with pytest.raises(ErrorTraslado, match="cuentas mayoristas de plataformas"):
        transferFunds(
            conn=tes,
            origin_fund_code="1230",
            destination_fund_code="1110",
            amount_cents=1000_00,
            dispatched_by="alan_tester",
            reason="Claro Digital a Drugstore",
        )

    # Físico a Digital que no sea 1110 -> 1210 (por ejemplo 1120 -> 1210)
    with pytest.raises(ErrorTraslado, match="cuentas físicas y cuentas digitales"):
        transferFunds(
            conn=tes,
            origin_fund_code="1120",
            destination_fund_code="1210",
            amount_cents=1000_00,
            dispatched_by="alan_tester",
            reason="1120 a MP",
        )


# -------------------------------------------------------------------------
# 6. Confirmación de recepción
# -------------------------------------------------------------------------
def test_06_confirmacion_recepcion(disposable_dbs):
    """6. Confirmación de recepción acredita en destino, marca CONFIRMED y vincula in_movement_id."""
    _, tes = disposable_dbs
    t = transferFunds(
        conn=tes,
        origin_fund_code="1110",
        destination_fund_code="1120",
        amount_cents=25000_00,
        dispatched_by="alan_tester",
        reason="Traslado operativo a caja 2",
    )

    conf = confirmTransferReceipt(
        conn=tes,
        transfer_id=t["transfer_id"],
        received_by="yilda_cajera",
    )

    assert conf["status"] == "CONFIRMED"
    assert conf["received_by"] == "yilda_cajera"
    assert conf["received_at"] is not None
    assert conf["in_movement_id"] is not None

    # Verificar fondos
    f1110 = obtener_fondo_por_codigo(tes, "1110")
    f1120 = obtener_fondo_por_codigo(tes, "1120")
    assert f1110["saldo_centavos"] == 75000_00
    assert f1120["saldo_centavos"] == 25000_00

    # Movimiento TRANSFER_IN verificado
    movs_1120 = listar_movimientos(tes, codigo_fondo="1120")
    in_mov = next((m for m in movs_1120 if m["movement_id"] == conf["in_movement_id"]), None)
    assert in_mov is not None
    assert in_mov["movement_type"] == "TRANSFER_IN"
    assert in_mov["amount_cents"] == 25000_00


# -------------------------------------------------------------------------
# 7. Rechazo de confirmación duplicada
# -------------------------------------------------------------------------
def test_07_rechazo_confirmacion_duplicada(disposable_dbs):
    """7. Rechazo de segunda confirmación sin alterar saldos."""
    _, tes = disposable_dbs
    t = transferFunds(
        conn=tes,
        origin_fund_code="1110",
        destination_fund_code="1120",
        amount_cents=10000_00,
        dispatched_by="alan_tester",
        reason="Traslado prueba duplicada",
    )

    confirmTransferReceipt(conn=tes, transfer_id=t["transfer_id"], received_by="cajero_1")
    assert obtener_fondo_por_codigo(tes, "1120")["saldo_centavos"] == 10000_00

    # Intento de reconfirmación sin idempotencia
    with pytest.raises(ErrorTraslado, match="no está pendiente de recepción"):
        confirmTransferReceipt(conn=tes, transfer_id=t["transfer_id"], received_by="cajero_2")

    # Saldo en destino no fue incrementado de nuevo
    assert obtener_fondo_por_codigo(tes, "1120")["saldo_centavos"] == 10000_00


# -------------------------------------------------------------------------
# 8. Anulación de traslado pendiente
# -------------------------------------------------------------------------
def test_08_anulacion_traslado_pendiente(disposable_dbs):
    """8. Anulación restituye fondos a origen con REVERSAL vinculado y no toca destino."""
    _, tes = disposable_dbs
    t = transferFunds(
        conn=tes,
        origin_fund_code="1110",
        destination_fund_code="1120",
        amount_cents=15000_00,
        dispatched_by="alan_tester",
        reason="Traslado para anular",
    )

    assert obtener_fondo_por_codigo(tes, "1110")["saldo_centavos"] == 85000_00
    assert obtener_fondo_por_codigo(tes, "1120")["saldo_centavos"] == 0

    canc = cancelPendingTransfer(
        conn=tes,
        transfer_id=t["transfer_id"],
        cancelled_by="supervisor",
        reason="Caja secundaria no operativa",
    )

    assert canc["status"] == "CANCELLED"

    # Saldo en origen restituido
    assert obtener_fondo_por_codigo(tes, "1110")["saldo_centavos"] == 100000_00
    # Destino nunca recibió nada
    assert obtener_fondo_por_codigo(tes, "1120")["saldo_centavos"] == 0

    # Verificar REVERSAL en movimientos
    movs = listar_movimientos(tes, codigo_fondo="1110")
    rev = next((m for m in movs if m["movement_type"] == "REVERSAL"), None)
    assert rev is not None
    assert rev["is_reversal"] == 1
    assert rev["reversal_of_id"] == t["out_movement_id"]
    assert rev["reversed_amount_cents"] == 15000_00

    # Verificar que el out_movement_id original tiene reversed_amount_cents actualizado
    out_mov = next((m for m in movs if m["movement_id"] == t["out_movement_id"]), None)
    assert out_mov["reversed_amount_cents"] == 15000_00


# -------------------------------------------------------------------------
# 9. Rechazo de anulación después de recibir
# -------------------------------------------------------------------------
def test_09_rechazo_anulacion_despues_de_recibir(disposable_dbs):
    """9. Rechazo de anulación de un traslado ya CONFIRMED."""
    _, tes = disposable_dbs
    t = transferFunds(
        conn=tes,
        origin_fund_code="1110",
        destination_fund_code="1120",
        amount_cents=5000_00,
        dispatched_by="alan_tester",
        reason="Traslado para recibir y luego intentar anular",
    )
    confirmTransferReceipt(conn=tes, transfer_id=t["transfer_id"], received_by="cajero_1")

    with pytest.raises(ErrorTraslado, match="No se puede cancelar un traslado en estado 'CONFIRMED'"):
        cancelPendingTransfer(
            conn=tes,
            transfer_id=t["transfer_id"],
            cancelled_by="supervisor",
            reason="Intento extemporáneo",
        )

    # Saldos siguen iguales
    assert obtener_fondo_por_codigo(tes, "1110")["saldo_centavos"] == 95000_00
    assert obtener_fondo_por_codigo(tes, "1120")["saldo_centavos"] == 5000_00


# -------------------------------------------------------------------------
# 10. Rechazo de recepción después de anular
# -------------------------------------------------------------------------
def test_10_rechazo_recepcion_despues_de_anular(disposable_dbs):
    """10. Rechazo de confirmación de recepción de un traslado ya CANCELLED."""
    _, tes = disposable_dbs
    t = transferFunds(
        conn=tes,
        origin_fund_code="1110",
        destination_fund_code="1120",
        amount_cents=5000_00,
        dispatched_by="alan_tester",
        reason="Traslado para anular y luego intentar recibir",
    )
    cancelPendingTransfer(
        conn=tes,
        transfer_id=t["transfer_id"],
        cancelled_by="supervisor",
        reason="Anulado de antemano",
    )

    with pytest.raises(ErrorTraslado, match="no está pendiente de recepción"):
        confirmTransferReceipt(conn=tes, transfer_id=t["transfer_id"], received_by="cajero_1")

    # Saldos siguen consistentes
    assert obtener_fondo_por_codigo(tes, "1110")["saldo_centavos"] == 100000_00
    assert obtener_fondo_por_codigo(tes, "1120")["saldo_centavos"] == 0


# -------------------------------------------------------------------------
# 11. Rechazo de anulación duplicada
# -------------------------------------------------------------------------
def test_11_rechazo_anulacion_duplicada(disposable_dbs):
    """11. Rechazo de segunda anulación sin alterar saldos."""
    _, tes = disposable_dbs
    t = transferFunds(
        conn=tes,
        origin_fund_code="1110",
        destination_fund_code="1120",
        amount_cents=8000_00,
        dispatched_by="alan_tester",
        reason="Prueba doble anulación",
    )
    cancelPendingTransfer(conn=tes, transfer_id=t["transfer_id"], cancelled_by="sup1", reason="Motivo 1")
    assert obtener_fondo_por_codigo(tes, "1110")["saldo_centavos"] == 100000_00

    with pytest.raises(ErrorTraslado, match="No se puede cancelar un traslado en estado 'CANCELLED'"):
        cancelPendingTransfer(conn=tes, transfer_id=t["transfer_id"], cancelled_by="sup2", reason="Motivo 2")

    assert obtener_fondo_por_codigo(tes, "1110")["saldo_centavos"] == 100000_00


# -------------------------------------------------------------------------
# 12. Rollback transaccional ante fallo intermedio
# -------------------------------------------------------------------------
def test_12_rollback_transaccional_ante_fallo(disposable_dbs):
    """12. Si una etapa falla durante el despacho, rollback revierte cualquier cambio."""
    _, tes = disposable_dbs
    saldo_inicial = obtener_fondo_por_codigo(tes, "1110")["saldo_centavos"]

    # Inyectar fallo simulado dentro del insert del traslado
    with patch("tesoreria.transfers._registrar_auditoria", side_effect=RuntimeError("Fallo simulado en auditoría")):
        with pytest.raises(RuntimeError, match="Fallo simulado"):
            transferFunds(
                conn=tes,
                origin_fund_code="1110",
                destination_fund_code="1120",
                amount_cents=12000_00,
                dispatched_by="alan_tester",
                reason="Fallo provocado",
            )

    # Verificar que el saldo volvió a su estado inicial
    assert obtener_fondo_por_codigo(tes, "1110")["saldo_centavos"] == saldo_inicial
    # Ningún traslado quedó registrado
    traslados = listar_traslados(tes)
    assert len(traslados) == 0
    # Ningún movimiento TRANSFER_OUT quedó huérfano
    movs = listar_movimientos(tes, codigo_fondo="1110")
    assert not any(m["movement_type"] == "TRANSFER_OUT" for m in movs)


# -------------------------------------------------------------------------
# 13. Verificación de saldos registrados y teóricos (diferencia_teorica == 0)
# -------------------------------------------------------------------------
def test_13_saldos_registrados_y_teoricos_consistentes(disposable_dbs):
    """13. En todo momento saldo_registrado == saldo_teorico (diferencia_teorica_cents == 0)."""
    _, tes = disposable_dbs

    # 1. Estado inicial
    bal_1110 = getFundBalance(tes, "1110")
    bal_1120 = getFundBalance(tes, "1120")
    assert bal_1110["diferencia_teorica_cents"] == 0
    assert bal_1120["diferencia_teorica_cents"] == 0

    # 2. Despacho (en tránsito)
    t = transferFunds(
        conn=tes,
        origin_fund_code="1110",
        destination_fund_code="1120",
        amount_cents=20000_00,
        dispatched_by="alan",
        reason="Test paridad teorica",
    )
    bal_1110 = getFundBalance(tes, "1110")
    bal_1120 = getFundBalance(tes, "1120")
    assert bal_1110["diferencia_teorica_cents"] == 0
    assert bal_1120["diferencia_teorica_cents"] == 0
    assert bal_1110["saldo_registrado_cents"] == 80000_00
    assert bal_1120["saldo_registrado_cents"] == 0

    # 3. Confirmación
    confirmTransferReceipt(conn=tes, transfer_id=t["transfer_id"], received_by="cajero")
    bal_1110 = getFundBalance(tes, "1110")
    bal_1120 = getFundBalance(tes, "1120")
    assert bal_1110["diferencia_teorica_cents"] == 0
    assert bal_1120["diferencia_teorica_cents"] == 0
    assert bal_1110["saldo_registrado_cents"] == 80000_00
    assert bal_1120["saldo_registrado_cents"] == 20000_00

    # 4. Despacho y posterior anulación
    t2 = transferFunds(
        conn=tes,
        origin_fund_code="1110",
        destination_fund_code="1120",
        amount_cents=10000_00,
        dispatched_by="alan",
        reason="Para anular y verificar teorico",
    )
    bal_1110 = getFundBalance(tes, "1110")
    assert bal_1110["diferencia_teorica_cents"] == 0

    cancelPendingTransfer(conn=tes, transfer_id=t2["transfer_id"], cancelled_by="alan", reason="Anulacion test")
    bal_1110 = getFundBalance(tes, "1110")
    bal_1120 = getFundBalance(tes, "1120")
    assert bal_1110["diferencia_teorica_cents"] == 0
    assert bal_1120["diferencia_teorica_cents"] == 0
    assert bal_1110["saldo_registrado_cents"] == 80000_00
    assert bal_1120["saldo_registrado_cents"] == 20000_00


# -------------------------------------------------------------------------
# 14. Verificación de vínculos de trazabilidad
# -------------------------------------------------------------------------
def test_14_vinculos_trazabilidad(disposable_dbs):
    """14. Trazabilidad completa: traslado <-> out_movement <-> in_movement <-> reversal_of_id."""
    _, tes = disposable_dbs

    # Caso 1: Despacho y Confirmación
    t1 = transferFunds(
        conn=tes,
        origin_fund_code="1110",
        destination_fund_code="1120",
        amount_cents=15000_00,
        dispatched_by="alan",
        reason="Vínculo confirmación",
    )
    c1 = confirmTransferReceipt(conn=tes, transfer_id=t1["transfer_id"], received_by="cajero")

    cur = tes.cursor()
    cur.execute("SELECT out_movement_id, in_movement_id FROM tesoreria_traslados WHERE transfer_id = ?", (t1["transfer_id"],))
    row = cur.fetchone()
    assert row["out_movement_id"] == t1["out_movement_id"]
    assert row["in_movement_id"] == c1["in_movement_id"]

    # Caso 2: Despacho y Anulación
    t2 = transferFunds(
        conn=tes,
        origin_fund_code="1110",
        destination_fund_code="1120",
        amount_cents=5000_00,
        dispatched_by="alan",
        reason="Vínculo anulación",
    )
    cancelPendingTransfer(conn=tes, transfer_id=t2["transfer_id"], cancelled_by="alan", reason="Anulado")

    cur.execute("SELECT movement_id, is_reversal, reversal_of_id FROM tesoreria_movimientos WHERE reversal_of_id = ?", (t2["out_movement_id"],))
    rev_row = cur.fetchone()
    assert rev_row is not None
    assert rev_row["is_reversal"] == 1
    assert rev_row["reversal_of_id"] == t2["out_movement_id"]
    cur.close()


# -------------------------------------------------------------------------
# 15. Verificación de no generación de ingresos/egresos comerciales
# -------------------------------------------------------------------------
def test_15_no_genera_ingresos_egresos_comerciales(disposable_dbs):
    """15. Los traslados de tesorería no crean ni modifican registros de ventas ni gastos."""
    comm, tes = disposable_dbs

    cur_c = comm.cursor()
    cur_c.execute("SELECT COUNT(*) FROM tickets")
    ventas_antes = cur_c.fetchone()[0]
    cur_c.execute("SELECT COUNT(*) FROM gastos_retiros")
    gastos_antes = cur_c.fetchone()[0]
    cur_c.execute("SELECT COUNT(*) FROM commercial_events_outbox")
    outbox_antes = cur_c.fetchone()[0]

    # Ejecutar ciclo completo de traslado
    t = transferFunds(
        conn=tes,
        origin_fund_code="1110",
        destination_fund_code="1120",
        amount_cents=10000_00,
        dispatched_by="alan",
        reason="No comercial",
    )
    confirmTransferReceipt(conn=tes, transfer_id=t["transfer_id"], received_by="cajero")

    cur_c.execute("SELECT COUNT(*) FROM tickets")
    assert cur_c.fetchone()[0] == ventas_antes
    cur_c.execute("SELECT COUNT(*) FROM gastos_retiros")
    assert cur_c.fetchone()[0] == gastos_antes
    cur_c.execute("SELECT COUNT(*) FROM commercial_events_outbox")
    assert cur_c.fetchone()[0] == outbox_antes
    cur_c.close()


# -------------------------------------------------------------------------
# 16. Idempotencia ante reintentos
# -------------------------------------------------------------------------
def test_16_idempotencia_reintentos(disposable_dbs):
    """16. Idempotencia: reintentos con misma idempotency_key retornan sin duplicar operaciones."""
    _, tes = disposable_dbs

    k_trf = "idemp_test_key_dispatch_999"
    t1 = transferFunds(
        conn=tes,
        origin_fund_code="1110",
        destination_fund_code="1120",
        amount_cents=10000_00,
        dispatched_by="alan",
        reason="Idempotencia despacho",
        idempotency_key=k_trf,
    )
    t2 = transferFunds(
        conn=tes,
        origin_fund_code="1110",
        destination_fund_code="1120",
        amount_cents=10000_00,
        dispatched_by="alan",
        reason="Idempotencia despacho",
        idempotency_key=k_trf,
    )
    assert t1["transfer_id"] == t2["transfer_id"]
    assert obtener_fondo_por_codigo(tes, "1110")["saldo_centavos"] == 90000_00  # Solo un débito de 10.000

    # Idempotencia en confirmación
    k_conf = "idemp_test_key_conf_999"
    c1 = confirmTransferReceipt(conn=tes, transfer_id=t1["transfer_id"], received_by="cajero", idempotency_key=k_conf)
    c2 = confirmTransferReceipt(conn=tes, transfer_id=t1["transfer_id"], received_by="cajero", idempotency_key=k_conf)
    assert c1["in_movement_id"] == c2["in_movement_id"]
    assert obtener_fondo_por_codigo(tes, "1120")["saldo_centavos"] == 10000_00  # Solo un crédito de 10.000

    # Idempotencia en anulación
    k_canc_trf = "idemp_test_key_canc_dispatch_999"
    t3 = transferFunds(
        conn=tes,
        origin_fund_code="1110",
        destination_fund_code="1120",
        amount_cents=5000_00,
        dispatched_by="alan",
        reason="Para anular idempotente",
        idempotency_key=k_canc_trf,
    )
    assert obtener_fondo_por_codigo(tes, "1110")["saldo_centavos"] == 85000_00

    k_canc = "idemp_test_key_canc_999"
    canc1 = cancelPendingTransfer(conn=tes, transfer_id=t3["transfer_id"], cancelled_by="alan", reason="Anulando", idempotency_key=k_canc)
    canc2 = cancelPendingTransfer(conn=tes, transfer_id=t3["transfer_id"], cancelled_by="alan", reason="Anulando", idempotency_key=k_canc)
    assert canc1["status"] == "CANCELLED"
    assert canc2["status"] == "CANCELLED"
    assert obtener_fondo_por_codigo(tes, "1110")["saldo_centavos"] == 90000_00  # Restituido exactamente 5.000 una sola vez


# -------------------------------------------------------------------------
# Test End-to-End API Router HTTP
# -------------------------------------------------------------------------
def test_17_api_router_endpoints_http(tmp_path, monkeypatch):
    """Verifica los endpoints HTTP de FastAPI (/api/tesoreria/traslados)."""
    from fastapi.testclient import TestClient
    from api.main import app
    from api.dependencies import get_current_user
    from api.auth import AuthenticatedUser
    from tesoreria.isolation import conectar_db_laboratorio

    db_file = tmp_path / "tesoreria_api_test.db"
    monkeypatch.setenv("GENARO_TESORERIA_DB_PATH", str(db_file))
    monkeypatch.setenv("GENARO_ENV", "STRICT_LOCAL_SQLITE")

    conn = conectar_db_laboratorio(str(db_file))
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)
    abrir_jornada(conn, usuario="alan")
    registerEntry(conn, fund_code="1110", amount_cents=50000_00, counterpart="Caja", reason="Fondo", created_by="alan")
    conn.close()

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        username="alan", role="ADMIN", display_name="Alan"
    )
    client = TestClient(app)

    try:
        # 1. Iniciar traslado vía POST /api/tesoreria/traslados
        res = client.post(
            "/api/tesoreria/traslados",
            json={
                "origin_fund_code": "1110",
                "destination_fund_code": "1120",
                "amount_cents": 10000_00,
                "reason": "Traslado API HTTP",
                "evidence_reference": "REM-HTTP-01",
            },
        )
        assert res.status_code == 200, res.text
        data = res.json()
        transfer_id = data["transfer_id"]
        assert data["status"] == "PENDING_RECEIPT"

        # 2. Filtrar traslados vía GET /api/tesoreria/traslados?status=PENDING_RECEIPT
        res_list = client.get("/api/tesoreria/traslados", params={"status": "PENDING_RECEIPT"})
        assert res_list.status_code == 200
        items = res_list.json()
        assert len(items) == 1
        assert items[0]["transfer_id"] == transfer_id

        # 3. Confirmar traslado vía POST /api/tesoreria/traslados/{id}/confirmar
        res_conf = client.post(f"/api/tesoreria/traslados/{transfer_id}/confirmar")
        assert res_conf.status_code == 200
        assert res_conf.json()["status"] == "CONFIRMED"

        # 4. Despachar un segundo traslado y cancelarlo vía POST /api/tesoreria/traslados/{id}/cancelar
        res_t2 = client.post(
            "/api/tesoreria/traslados",
            json={
                "origin_fund_code": "1110",
                "destination_fund_code": "1120",
                "amount_cents": 5000_00,
                "reason": "Para cancelar",
            },
        )
        assert res_t2.status_code == 200
        t2_id = res_t2.json()["transfer_id"]

        res_canc = client.post(
            f"/api/tesoreria/traslados/{t2_id}/cancelar",
            json={"reason": "Cancelado desde HTTP"},
        )
        assert res_canc.status_code == 200
        assert res_canc.json()["status"] == "CANCELLED"
    finally:
        app.dependency_overrides.clear()

