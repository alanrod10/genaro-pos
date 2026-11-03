"""Escenario Sintético Integral de 14 Pasos — SPRINT 3 (FASE 8).

Verificación matemática estricta de:
1. Apertura de jornada.
2. Entradas y salidas.
3. Traslado pendiente (tránsito).
4. Confirmación de recepción.
5. Reserva preventiva parcial con obligación.
6. Aplicación de reserva y egreso atómico (executeReservedExit).
7. Arqueo con diferencia y apertura de expediente.
8. Investigación y resolución documentada.
9. Reversión parcial vinculada.
10. Preparación del cierre (beginClosure y validateClosure).
11. Vaciado obligatorio de Caja Principal 1110 -> 1120 (executeCashDrawerSweep a 0 centavos).
12. Cierre inmutable y hash SHA-256 de snapshot canónico (confirmClosure).
13. Apertura de jornada siguiente con 1110 = 0 (openNextJournal).
14. Verificación matemática de auditoría (verifyAuditChain) y vaciado de Outbox (processOutbox).
"""

import sqlite3
import pytest

from tesoreria.audit_chain import verifyAuditChain
from tesoreria.catalog import obtener_fondo_por_codigo, sembrar_catalogo_canónico
from tesoreria.closures import (
    beginClosure,
    confirmClosure,
    executeCashDrawerSweep,
    getClosure,
    openNextJournal,
    validateClosure,
)
from tesoreria.jornadas import abrir_jornada, obtener_jornada_actual
from tesoreria.movements import registerEntry, registerExit
from tesoreria.outbox import getOutboxStatus, processOutbox
from tesoreria.reconciliations import (
    createReconciliation,
    getDifferenceCases,
    resolveDifferenceCase,
)
from tesoreria.reservations import createReservation, executeReservedExit
from tesoreria.reversals import reverseMovement
from tesoreria.schema import inicializar_esquema_laboratorio
from tesoreria.transfers import confirmTransferReceipt, transferFunds


def test_escenario_sintetico_integral_14_pasos():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)

    # =================================================================
    # PASO 1: Apertura de jornada
    # =================================================================
    j1 = abrir_jornada(conn, usuario="alan", observaciones="Apertura Jornada 1 Lab")
    assert j1["numero_jornada"] == 1
    assert j1["estado"] == "ABIERTA"
    for d in j1["detalles"]:
        assert d["saldo_apertura_centavos"] == 0

    # =================================================================
    # PASO 2: Entradas y salidas
    # =================================================================
    # Entrada en 1110: $100.000,00
    e1 = registerEntry(conn, fund_code="1110", amount_cents=100000_00, counterpart="Aporte Capital", reason="Fondeo inicial", created_by="alan")
    assert obtener_fondo_por_codigo(conn, "1110")["saldo_centavos"] == 100000_00

    # Entrada en 1150: $50.000,00 (Custodia de terceros)
    e2 = registerEntry(conn, fund_code="1150", amount_cents=50000_00, counterpart="Cobranza Facturas Terceros", reason="Recaudación servicios", created_by="alan")
    assert obtener_fondo_por_codigo(conn, "1150")["saldo_centavos"] == 50000_00

    # Salida ordinaria en 1110: $10.000,00
    s1 = registerExit(conn, fund_code="1110", amount_cents=10000_00, counterpart="Proveedor Artículos", reason="Pago insumos", created_by="alan")
    assert obtener_fondo_por_codigo(conn, "1110")["saldo_centavos"] == 90000_00

    # =================================================================
    # PASO 3: Traslado pendiente (en tránsito)
    # =================================================================
    trf = transferFunds(conn, origin_fund_code="1110", destination_fund_code="1130", amount_cents=25000_00, dispatched_by="alan", reason="Envío a Tesoro")
    assert trf["status"] == "PENDING_RECEIPT"
    assert obtener_fondo_por_codigo(conn, "1110")["saldo_centavos"] == 65000_00
    assert obtener_fondo_por_codigo(conn, "1130")["saldo_centavos"] == 0

    # =================================================================
    # PASO 4: Confirmación de recepción
    # =================================================================
    c_trf = confirmTransferReceipt(conn, transfer_id=trf["transfer_id"], received_by="tesorero_roberto")
    assert c_trf["status"] == "CONFIRMED"
    assert obtener_fondo_por_codigo(conn, "1130")["saldo_centavos"] == 25000_00

    # =================================================================
    # PASO 5: Reserva preventiva parcial
    # =================================================================
    rsv = createReservation(
        conn,
        fund_code="1110",
        amount_cents=30000_00,
        reason="Reserva Transporte Logística",
        beneficiary="Logística Expreso",
        obligation_id="OBL-FLE-01",
        created_by="alan",
    )
    assert rsv["status"] == "ACTIVE"
    assert rsv["pending_amount_cents"] == 30000_00

    # =================================================================
    # PASO 6: Aplicación de reserva y egreso atómico (executeReservedExit)
    # =================================================================
    # Flete real resultó en $22.000,00 (queda $8.000,00 de reserva pendiente)
    res_exit = executeReservedExit(
        conn,
        reservation_id=rsv["reservation_id"],
        amount_cents=22000_00,
        counterpart="Transporte Logística SA",
        reason="Pago flete insumos según remito R-889",
        performed_by="alan",
    )
    assert res_exit["success"] is True
    assert res_exit["new_reservation_pending_cents"] == 8000_00
    assert res_exit["new_fund_balance_cents"] == 43000_00
    assert obtener_fondo_por_codigo(conn, "1110")["saldo_centavos"] == 43000_00

    # =================================================================
    # PASO 7: Arqueo con diferencia y apertura de expediente
    # =================================================================
    # Saldo registrado es 43.000,00. Conteo físico da 42.500,00 (diferencia de -500,00)
    recon = createReconciliation(
        conn,
        fund_code="1110",
        reconciliation_type="CLOSING",
        verified_amount_cents=42500_00,
        user_id="cajero_turno",
        notes="Recuento de caja previo al cierre",
        create_difference_case=True,
        investigation_notes="Faltante de $500,00 en recuento de billetes",
    )
    assert recon["status"] == "OPEN"
    assert recon["diferencia_cents"] == -500_00
    assert recon["difference_case_id"] is not None
    # El saldo del fondo no se modifica
    assert obtener_fondo_por_codigo(conn, "1110")["saldo_centavos"] == 43000_00

    # =================================================================
    # PASO 8: Investigación y resolución documentada
    # =================================================================
    case_id = recon["difference_case_id"]
    res_case = resolveDifferenceCase(
        conn,
        case_id=case_id,
        resolution_notes="Se ubicó ticket de librería extraviado por $500,00 registrado conforme",
        evidence_reference="TICKET-LIB-442",
        resolved_by="auditor_jefe",
    )
    assert res_case["status"] == "RESOLVED"

    # =================================================================
    # PASO 9: Reversión parcial
    # =================================================================
    # Del egreso s1 ($10.000,00), se revierte parcialmente $2.000,00 por duplicación
    rev = reverseMovement(
        conn,
        movement_id=s1["movement_id"],
        user_id="supervisor",
        reason="Devolución por cobro duplicado de proveedor",
        partial_amount_cents=2000_00,
    )
    assert rev["status"] == "COMPLETED"
    assert rev["reversed_amount_cents"] == 2000_00
    # Saldo 1110 pasa de 43.000,00 a 45.000,00
    assert obtener_fondo_por_codigo(conn, "1110")["saldo_centavos"] == 45000_00

    # =================================================================
    # PASO 10: Preparación del cierre
    # =================================================================
    j_closing = beginClosure(conn, journal_id=j1["id"], user_id="alan")
    assert j_closing["estado"] == "EN_CIERRE"

    val = validateClosure(conn, journal_id=j1["id"])
    assert val["needs_sweep"] is True
    assert val["pending_transfers_count"] == 0

    # =================================================================
    # PASO 11: Vaciado 1110 -> 1120 (executeCashDrawerSweep)
    # =================================================================
    sweep = executeCashDrawerSweep(conn, journal_id=j1["id"], user_id="alan")
    assert sweep["swept"] is True
    assert sweep["amount_cents"] == 45000_00

    # Verificación estricta: 1110 = 0
    assert obtener_fondo_por_codigo(conn, "1110")["saldo_centavos"] == 0
    # 1120 recibe los 45.000,00
    assert obtener_fondo_por_codigo(conn, "1120")["saldo_centavos"] == 45000_00

    # =================================================================
    # PASO 12: Cierre inmutable y hash SHA-256 de snapshot canónico
    # =================================================================
    cierre = confirmClosure(conn, journal_id=j1["id"], user_id="alan", observations="Cierre Jornada 1 conforme")
    assert cierre["status"] == "CONFIRMED"
    assert len(cierre["canonical_hash"]) == 64
    # Total consolidado: 45.000 (1120) + 25.000 (1130) + 50.000 (1150) = 120.000,00 ($12.000.000 centavos)
    assert cierre["total_registrado_cents"] == 120000_00

    # =================================================================
    # PASO 13: Apertura de jornada siguiente
    # =================================================================
    j2 = openNextJournal(conn, user_id="alan_jornada_2", observations="Apertura Jornada 2")
    assert j2["numero_jornada"] == 2
    assert j2["estado"] == "ABIERTA"

    detalles_j2 = {d["codigo_fondo"]: d["saldo_apertura_centavos"] for d in j2["detalles"]}
    assert detalles_j2["1110"] == 0
    assert detalles_j2["1120"] == 45000_00
    assert detalles_j2["1130"] == 25000_00
    assert detalles_j2["1150"] == 50000_00
    assert j2["total_apertura_centavos"] == 120000_00

    # =================================================================
    # PASO 14: Verificación de auditoría y Outbox
    # =================================================================
    audit_res = verifyAuditChain(conn)
    assert audit_res["valid"] is True
    assert audit_res["total_events"] > 0
    assert len(audit_res["errors"]) == 0

    outbox_proc = processOutbox(conn, consumer_id="FINAL_VERIFIER")
    assert outbox_proc["published"] > 0
    assert outbox_proc["failed"] == 0

    status = getOutboxStatus(conn)
    assert status["resumen"]["PENDING"] == 0
    assert status["resumen"]["FAILED"] == 0
    assert status["resumen"]["DEAD_LETTER"] == 0

    conn.close()
