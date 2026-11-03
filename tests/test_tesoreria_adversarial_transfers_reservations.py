"""Pruebas Adversariales de Traslados, Reservas y Dinero en Tránsito — SPRINT 4 FASE C.

Verificación de:
- Estados de traslado: PENDING_RECEIPT, CONFIRMED, CANCELLED, REVERSED.
- Invariante de conservación de masa monetaria (sin duplicación de saldos ni pérdidas).
- Ciclo de vida completo de reserva: creación, aplicación parcial, aplicación total, liberación parcial, liberación total.
- Reversión de aplicación y reversión de liberación de reserva.
- Invariante estricto de reservas: original_cents == applied_cents + released_cents + pending_cents.
- Re-anclaje de reservas durante vaciado de caja 1110 -> 1120 con saldo 1110 = 0 exacto.
"""

import sqlite3
import pytest

from tesoreria.catalog import obtener_fondo_por_codigo, sembrar_catalogo_canónico
from tesoreria.closures import executeCashDrawerSweep
from tesoreria.isolation import conectar_db_laboratorio
from tesoreria.jornadas import abrir_jornada
from tesoreria.movements import registerEntry
from tesoreria.reservations import (
    applyReservation,
    createReservation,
    getReservationHistory,
    releaseReservation,
)
from tesoreria.reversals import reverseReservationOperation, reverseTransfer
from tesoreria.schema import inicializar_esquema_laboratorio
from tesoreria.transfers import (
    cancelPendingTransfer,
    confirmTransferReceipt,
    transferFunds,
)


@pytest.fixture
def conn_transfers_rsv(tmp_path):
    """Base SQLite en memoria/aislada con jornada activa y fondos sembrados."""
    db_file = tmp_path / "tesoreria_adv_transfers_rsv.db"
    conn = conectar_db_laboratorio(str(db_file))
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)
    abrir_jornada(conn, usuario="tesorero_adv")
    # Fondeo inicial en 1110: $1.000.000 (100_000_000 cents)
    registerEntry(
        conn,
        fund_code="1110",
        amount_cents=100_000_000,
        counterpart="Fondeo Inicial",
        reason="Fondeo inicial para laboratorio C",
        created_by="tesorero_adv",
    )
    yield conn
    conn.close()


def test_transfer_states_lifecycle_and_conservation_invariants(conn_transfers_rsv):
    """Prueba exhaustiva de estados de traslado y conservación de saldos:
    1. Origen 1110 ($1.000.000), Destino 1210 ($0). Total sistema = $1.000.000.
    2. Traslado 1 ($200.000) a 1210 -> PENDING_RECEIPT.
       - Saldo 1110 = $800.000.
       - En tránsito = $200.000.
       - Saldo 1210 = $0.
       - Total sistema (1110 + 1210 + tránsito) = $1.000.000. Cero duplicación.
    3. Confirmación de Traslado 1 -> CONFIRMED.
       - Saldo 1110 = $800.000. En tránsito = $0. Saldo 1210 = $200.000.
       - Total = $1.000.000.
    4. Reversión de Traslado 1 -> REVERSED.
       - Saldo 1210 se debita en $200.000 -> $0.
       - Saldo 1110 se restituye en $200.000 -> $1.000.000.
       - Total = $1.000.000.
    5. Traslado 2 ($150.000) a 1210 -> PENDING_RECEIPT.
       - Cancelación de Traslado 2 -> CANCELLED.
       - Saldo 1110 restituido a $1.000.000.
       - Saldo 1210 queda en $0.
       - En tránsito = $0.
       - Total = $1.000.000.
    """
    f1110 = obtener_fondo_por_codigo(conn_transfers_rsv, "1110")
    f1210 = obtener_fondo_por_codigo(conn_transfers_rsv, "1210")
    assert f1110["saldo_centavos"] == 100_000_000
    assert f1210["saldo_centavos"] == 0

    # 1. Iniciar traslado de $200.000
    t1 = transferFunds(
        conn=conn_transfers_rsv,
        origin_fund_code="1110",
        destination_fund_code="1210",
        amount_cents=20_000_000,
        dispatched_by="tesorero_adv",
        reason="Envío a banco",
        idempotency_key="t1_dispatch",
    )
    assert t1["status"] == "PENDING_RECEIPT"

    f1110 = obtener_fondo_por_codigo(conn_transfers_rsv, "1110")
    f1210 = obtener_fondo_por_codigo(conn_transfers_rsv, "1210")
    assert f1110["saldo_centavos"] == 80_000_000
    assert f1210["saldo_centavos"] == 0
    # Masa monetaria total (saldo 1110 + saldo 1210 + pendiente de recepción) == 100_000_000
    assert f1110["saldo_centavos"] + f1210["saldo_centavos"] + t1["amount_cents"] == 100_000_000

    # 2. Confirmar recepción en 1210
    t1_conf = confirmTransferReceipt(
        conn=conn_transfers_rsv,
        transfer_id=t1["transfer_id"],
        received_by="tesorero_adv",
        idempotency_key="t1_confirm",
    )
    assert t1_conf["status"] == "CONFIRMED"

    f1110 = obtener_fondo_por_codigo(conn_transfers_rsv, "1110")
    f1210 = obtener_fondo_por_codigo(conn_transfers_rsv, "1210")
    assert f1110["saldo_centavos"] == 80_000_000
    assert f1210["saldo_centavos"] == 20_000_000
    assert f1110["saldo_centavos"] + f1210["saldo_centavos"] == 100_000_000

    # 3. Reversión del traslado confirmado
    rev = reverseTransfer(
        conn=conn_transfers_rsv,
        transfer_id=t1["transfer_id"],
        user_id="supervisor_adv",
        reason="Error operativo en banco",
        idempotency_key="t1_reverse",
    )
    assert rev["amount_cents"] == 20_000_000

    f1110 = obtener_fondo_por_codigo(conn_transfers_rsv, "1110")
    f1210 = obtener_fondo_por_codigo(conn_transfers_rsv, "1210")
    assert f1110["saldo_centavos"] == 100_000_000
    assert f1210["saldo_centavos"] == 0
    assert f1110["saldo_centavos"] + f1210["saldo_centavos"] == 100_000_000

    # 4. Despachar nuevo traslado y cancelarlo en tránsito
    t2 = transferFunds(
        conn=conn_transfers_rsv,
        origin_fund_code="1110",
        destination_fund_code="1210",
        amount_cents=15_000_000,
        dispatched_by="tesorero_adv",
        reason="Envío cancelable",
        idempotency_key="t2_dispatch",
    )
    assert t2["status"] == "PENDING_RECEIPT"

    f1110 = obtener_fondo_por_codigo(conn_transfers_rsv, "1110")
    assert f1110["saldo_centavos"] == 85_000_000

    t2_canc = cancelPendingTransfer(
        conn=conn_transfers_rsv,
        transfer_id=t2["transfer_id"],
        cancelled_by="tesorero_adv",
        reason="Cancelado por cambio de instrucción",
        idempotency_key="t2_cancel",
    )
    assert t2_canc["status"] == "CANCELLED"

    f1110 = obtener_fondo_por_codigo(conn_transfers_rsv, "1110")
    f1210 = obtener_fondo_por_codigo(conn_transfers_rsv, "1210")
    assert f1110["saldo_centavos"] == 100_000_000
    assert f1210["saldo_centavos"] == 0


def test_reservation_full_lifecycle_and_invariants(conn_transfers_rsv):
    """Verifica el ciclo de vida completo de una reserva, la preservación matemática
    invariante: original == applied + released + pending en cada paso, y la reversión de operaciones."""
    # 1. Crear reserva de $500.000 sobre 1110
    rsv = createReservation(
        conn=conn_transfers_rsv,
        fund_code="1110",
        amount_cents=50_000_000,
        reason="Reserva Integral Proveedor",
        beneficiary="Proveedor ABC",
        created_by="tesorero_adv",
        idempotency_key="rsv_init",
    )
    rid = rsv["reservation_id"]
    h1 = getReservationHistory(conn_transfers_rsv, rid)
    assert h1["original_amount_cents"] == 50_000_000
    assert h1["applied_amount_cents"] == 0
    assert h1["released_amount_cents"] == 0
    assert h1["pending_amount_cents"] == 50_000_000
    assert h1["status"] == "ACTIVE"
    assert h1["original_amount_cents"] == (h1["applied_amount_cents"] + h1["released_amount_cents"] + h1["pending_amount_cents"])

    # 2. Aplicación parcial: $200.000
    ap1 = applyReservation(
        conn=conn_transfers_rsv,
        reservation_id=rid,
        amount_cents=20_000_000,
        reason="Pago cuota 1",
        performed_by="tesorero_adv",
        idempotency_key="rsv_ap1",
    )
    assert ap1["status"] == "PARTIALLY_APPLIED"
    h2 = getReservationHistory(conn_transfers_rsv, rid)
    assert h2["applied_amount_cents"] == 20_000_000
    assert h2["released_amount_cents"] == 0
    assert h2["pending_amount_cents"] == 30_000_000
    assert h2["original_amount_cents"] == (h2["applied_amount_cents"] + h2["released_amount_cents"] + h2["pending_amount_cents"])

    # 3. Liberación parcial: $100.000
    rel1 = releaseReservation(
        conn=conn_transfers_rsv,
        reservation_id=rid,
        amount_cents=10_000_000,
        reason="Descuento negociado",
        performed_by="tesorero_adv",
        idempotency_key="rsv_rel1",
    )
    assert rel1["status"] == "PARTIALLY_APPLIED"
    h3 = getReservationHistory(conn_transfers_rsv, rid)
    assert h3["applied_amount_cents"] == 20_000_000
    assert h3["released_amount_cents"] == 10_000_000
    assert h3["pending_amount_cents"] == 20_000_000
    assert h3["original_amount_cents"] == (h3["applied_amount_cents"] + h3["released_amount_cents"] + h3["pending_amount_cents"])

    # 4. Reversión de la liberación parcial de $100.000
    # Buscar el ID del movimiento de reserva correspondiente a 'RELEASE'
    mov_rel = [m for m in h3["movimientos"] if m["action_type"] == "RELEASE"][-1]
    rev_rel = reverseReservationOperation(
        conn=conn_transfers_rsv,
        reservation_id=rid,
        operation_id=mov_rel["id"],
        user_id="supervisor_adv",
        reason="Cancelación de acuerdo de descuento",
        idempotency_key="rev_rel_1",
    )
    assert rev_rel["reversed_action"] == "RELEASE"
    h4 = getReservationHistory(conn_transfers_rsv, rid)
    assert h4["released_amount_cents"] == 0
    assert h4["pending_amount_cents"] == 30_000_000
    assert h4["applied_amount_cents"] == 20_000_000
    assert h4["original_amount_cents"] == (h4["applied_amount_cents"] + h4["released_amount_cents"] + h4["pending_amount_cents"])

    # 5. Reversión de la aplicación parcial de $200.000 (filtrando solo la original, no reversiones)
    mov_ap = [m for m in h4["movimientos"] if m["action_type"] == "APPLY" and not m.get("is_reversal")][0]
    rev_ap = reverseReservationOperation(
        conn=conn_transfers_rsv,
        reservation_id=rid,
        operation_id=mov_ap["id"],
        user_id="supervisor_adv",
        reason="Reversión de pago fallido",
        idempotency_key="rev_ap_1",
    )
    assert rev_ap["reversed_action"] == "APPLY"
    h5 = getReservationHistory(conn_transfers_rsv, rid)
    assert h5["applied_amount_cents"] == 0
    assert h5["released_amount_cents"] == 0
    assert h5["pending_amount_cents"] == 50_000_000
    assert h5["status"] == "ACTIVE"
    assert h5["original_amount_cents"] == (h5["applied_amount_cents"] + h5["released_amount_cents"] + h5["pending_amount_cents"])

    # 6. Aplicación total directa: $500.000
    ap_tot = applyReservation(
        conn=conn_transfers_rsv,
        reservation_id=rid,
        amount_cents=50_000_000,
        reason="Pago total definitivo",
        performed_by="tesorero_adv",
        idempotency_key="rsv_ap_tot",
    )
    assert ap_tot["status"] == "FULLY_APPLIED"
    h6 = getReservationHistory(conn_transfers_rsv, rid)
    assert h6["applied_amount_cents"] == 50_000_000
    assert h6["pending_amount_cents"] == 0
    assert h6["released_amount_cents"] == 0
    assert h6["status"] == "FULLY_APPLIED"
    assert h6["original_amount_cents"] == (h6["applied_amount_cents"] + h6["released_amount_cents"] + h6["pending_amount_cents"])


def test_reanchoring_reservations_during_cash_drawer_sweep(conn_transfers_rsv):
    """Verifica que el barrido/vaciado de 1110 -> 1120:
    1. Vacia completamente 1110 dejándolo en exactamente 0 centavos.
    2. Acredita la totalidad en 1120.
    3. Re-ancla automáticamente todas las reservas activas/pendientes de 1110 hacia 1120.
    4. Conserva intactos los IDs, importes originales, aplicados, liberados y pendientes de las reservas."""
    # Crear dos reservas en 1110
    r1 = createReservation(
        conn=conn_transfers_rsv,
        fund_code="1110",
        amount_cents=10_000_000,
        reason="Reserva alquiler",
        beneficiary="Inmobiliaria",
        created_by="tesorero_adv",
    )
    r2 = createReservation(
        conn=conn_transfers_rsv,
        fund_code="1110",
        amount_cents=15_000_000,
        reason="Reserva seguridad",
        beneficiary="Empresa Alarmas",
        created_by="tesorero_adv",
    )

    # Aplicar parcialmente r1
    applyReservation(
        conn=conn_transfers_rsv,
        reservation_id=r1["reservation_id"],
        amount_cents=4_000_000,
        reason="Seña",
        performed_by="tesorero_adv",
    )

    # Verificar que ambas reservas apuntan a 1110
    h_r1 = getReservationHistory(conn_transfers_rsv, r1["reservation_id"])
    h_r2 = getReservationHistory(conn_transfers_rsv, r2["reservation_id"])
    assert h_r1["fund_code"] == "1110"
    assert h_r2["fund_code"] == "1110"
    assert h_r1["status"] == "PARTIALLY_APPLIED"
    assert h_r2["status"] == "ACTIVE"

    # Obtener ID de la jornada activa
    cur = conn_transfers_rsv.cursor()
    cur.execute("SELECT id FROM tesoreria_jornadas WHERE estado = 'ABIERTA'")
    jid = cur.fetchone()["id"]
    cur.close()

    # Ejecutar Cash Drawer Sweep
    sweep_res = executeCashDrawerSweep(
        conn=conn_transfers_rsv,
        journal_id=jid,
        user_id="tesorero_adv",
        reason="Vaciado de caja 1110 para cierre",
    )
    assert sweep_res["swept"] is True

    # 1. Verificar que 1110 quedó en exactamente 0
    f1110 = obtener_fondo_por_codigo(conn_transfers_rsv, "1110")
    assert f1110["saldo_centavos"] == 0

    # 2. Verificar que 1120 recibió todo el saldo
    f1120 = obtener_fondo_por_codigo(conn_transfers_rsv, "1120")
    assert f1120["saldo_centavos"] == 100_000_000

    # 3. Verificar que las reservas migraron a 1120 preservando datos
    h_r1_post = getReservationHistory(conn_transfers_rsv, r1["reservation_id"])
    h_r2_post = getReservationHistory(conn_transfers_rsv, r2["reservation_id"])

    assert h_r1_post["fund_code"] == "1120"
    assert h_r1_post["applied_amount_cents"] == 4_000_000
    assert h_r1_post["pending_amount_cents"] == 6_000_000
    assert h_r1_post["status"] == "PARTIALLY_APPLIED"

    assert h_r2_post["fund_code"] == "1120"
    assert h_r2_post["original_amount_cents"] == 15_000_000
    assert h_r2_post["pending_amount_cents"] == 15_000_000
    assert h_r2_post["status"] == "ACTIVE"
