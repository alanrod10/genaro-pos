"""Escenario Sintético Integral de Certificación (15 Pasos Obligatorios) — SPRINT 4 FASE I.

Demuestra la operación coordinada de LAB-001 a LAB-012 sobre la base SQLite aislada:
1. Apertura de jornada 1 con usuario tesorero.
2. Entrada en 1110 (Drugstore Diario) por ventas en efectivo ($150.000 = 15_000_000 cents).
3. Entrada en 1150 (Claro Efectivo) por recaudación de terceros ($80.000 = 8_000_000 cents) - Segregación Custodia.
4. Salida directa en 1110 para gastos menores autorizados ($10.000 = 1_000_000 cents).
5. Fondeo específico y egreso en 1140 (Adicionales Efectivo) ($5.000 = 500_000 cents) - Retiro particular autorizado.
6. Creación de reserva preventiva en 1110 para flete ($30.000 = 3_000_000 cents).
7. Egreso reservado atómico executeReservedExit de $20.000 (2_000_000 cents) sobre dicha reserva.
8. Liberación del remanente de $10.000 (1_000_000 cents) de la reserva preventiva.
9. Traslado de 1110 hacia Mercado Pago Propio 1210 ($40.000 = 4_000_000 cents) - Despacho PENDING_RECEIPT.
10. Confirmación de recepción en 1210.
11. Reversión parcial documentada de la salida de gastos ($2.000 = 200_000 cents) por reintegro de proveedor.
12. Arqueo físico de 1110 con faltante detectado de $500 (50_000 cents) y apertura de expediente de diferencias.
13. Resolución documentada del expediente de diferencias.
14. Barrido/vaciado de 1110 hacia 1120 (Drugstore Acumulado) - Verificación 1110 = 0 centavos.
15. Cierre definitivo de jornada 1 con snapshot canónico y SHA-256; verificación matemática de cadena de auditoría; despacho y deduplicación de Outbox; y apertura de jornada 2 con 1110 iniciando en 0.
"""

import sqlite3
import pytest

from tesoreria.audit_chain import verifyAuditChain
from tesoreria.availability import getTreasurySummary
from tesoreria.catalog import obtener_fondo_por_codigo, sembrar_catalogo_canónico
from tesoreria.closures import (
    beginClosure,
    confirmClosure,
    executeCashDrawerSweep,
    openNextJournal,
    validateClosure,
)
from tesoreria.isolation import conectar_db_laboratorio
from tesoreria.jornadas import abrir_jornada, obtener_jornada_actual
from tesoreria.movements import registerEntry, registerExit
from tesoreria.outbox import processOutbox
from tesoreria.reconciliations import (
    createReconciliation,
    resolveDifferenceCase,
)
from tesoreria.reservations import (
    createReservation,
    executeReservedExit,
    getReservationHistory,
    releaseReservation,
)
from tesoreria.reversals import reverseMovement
from tesoreria.schema import inicializar_esquema_laboratorio
from tesoreria.transfers import (
    confirmTransferReceipt,
    transferFunds,
)


def test_escenario_sintetico_integral_certificacion(tmp_path):
    """Ejecución secuencial integral de los 15 pasos de certificación del laboratorio."""
    db_file = tmp_path / "certificacion_integral_lab.db"
    conn = conectar_db_laboratorio(str(db_file))
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)

    # -------------------------------------------------------------------------
    # PASO 1: Apertura de jornada 1 con usuario tesorero
    # -------------------------------------------------------------------------
    j1 = abrir_jornada(conn, usuario="tesorero_cert", observaciones="Jornada de certificación integral")
    assert j1["estado"] == "ABIERTA"
    assert j1["numero_jornada"] == 1
    jid = j1["id"]

    # -------------------------------------------------------------------------
    # PASO 2: Entrada en 1110 (Drugstore Diario) por ventas en efectivo ($150.000)
    # -------------------------------------------------------------------------
    m_ventas = registerEntry(
        conn=conn,
        fund_code="1110",
        amount_cents=15_000_000,
        counterpart="Ventas del día Drugstore",
        reason="Recaudación diaria de ventas",
        created_by="tesorero_cert",
        evidence_reference="TICKET-Z-001",
    )
    assert m_ventas["status"] == "POSTED"
    f1110 = obtener_fondo_por_codigo(conn, "1110")
    assert f1110["saldo_centavos"] == 15_000_000

    # -------------------------------------------------------------------------
    # PASO 3: Entrada en 1150 (Claro Efectivo) por recaudación de terceros ($80.000) - Custodia
    # -------------------------------------------------------------------------
    m_claro = registerEntry(
        conn=conn,
        fund_code="1150",
        amount_cents=8_000_000,
        counterpart="Cobranza Facturas Claro",
        reason="Recaudación en custodia Claro",
        created_by="tesorero_cert",
        evidence_reference="LOTE-CLARO-101",
    )
    assert m_claro["status"] == "POSTED"
    summary_p3 = getTreasurySummary(conn)
    # 1150 es CUSTODIA: debe estar en fondos_custodia y NO sumar a disponibilidad propia
    assert summary_p3["fondos_custodia"]["total_registrado_cents"] == 8_000_000
    assert summary_p3["fondos_propios"]["total_disponible_cents"] == 15_000_000

    # -------------------------------------------------------------------------
    # PASO 4: Salida directa en 1110 para gastos menores autorizados ($10.000)
    # -------------------------------------------------------------------------
    m_gasto = registerExit(
        conn=conn,
        fund_code="1110",
        amount_cents=1_000_000,
        counterpart="Librería Central",
        reason="Artículos de limpieza y oficina",
        created_by="tesorero_cert",
        evidence_reference="FAC-LIBRERIA-88",
    )
    assert m_gasto["status"] == "POSTED"
    f1110 = obtener_fondo_por_codigo(conn, "1110")
    assert f1110["saldo_centavos"] == 14_000_000

    # -------------------------------------------------------------------------
    # PASO 5: Fondeo específico y egreso en 1140 (Adicionales Efectivo) ($5.000)
    # -------------------------------------------------------------------------
    # 1140 es para retiros particulares autorizados, no caja chica genérica
    registerEntry(
        conn=conn,
        fund_code="1140",
        amount_cents=1_000_000,
        counterpart="Aporte de socios para retiros",
        reason="Fondeo fondo adicional particular",
        created_by="tesorero_cert",
    )
    m_retiro_part = registerExit(
        conn=conn,
        fund_code="1140",
        amount_cents=500_000,
        counterpart="Socio Fundador A",
        reason="Retiro particular autorizado según acta",
        created_by="tesorero_cert",
        evidence_reference="ACTA-RETIRO-04",
    )
    assert m_retiro_part["status"] == "POSTED"
    f1140 = obtener_fondo_por_codigo(conn, "1140")
    assert f1140["saldo_centavos"] == 500_000

    # -------------------------------------------------------------------------
    # PASO 6: Creación de reserva preventiva en 1110 para flete ($30.000)
    # -------------------------------------------------------------------------
    rsv_flete = createReservation(
        conn=conn,
        fund_code="1110",
        amount_cents=3_000_000,
        reason="Reserva flete mercadería pesada",
        beneficiary="Transporte Veloz SRL",
        created_by="tesorero_cert",
    )
    rid = rsv_flete["reservation_id"]
    summary_p6 = getTreasurySummary(conn)
    # Disponibilidad en 1110: 14.000.000 saldo - 3.000.000 reservado = 11.000.000
    f1110_info = next(f for f in summary_p6["fondos"] if f["fund_code"] == "1110")
    assert f1110_info["saldo_registrado_cents"] == 14_000_000
    assert f1110_info["saldo_reservado_cents"] == 3_000_000
    assert f1110_info["saldo_disponible_cents"] == 11_000_000

    # -------------------------------------------------------------------------
    # PASO 7: Egreso reservado atómico executeReservedExit de $20.000 sobre dicha reserva
    # -------------------------------------------------------------------------
    res_exit = executeReservedExit(
        conn=conn,
        reservation_id=rid,
        amount_cents=2_000_000,
        counterpart="Transporte Veloz SRL",
        reason="Pago anticipo de flete contra entrega",
        performed_by="tesorero_cert",
        evidence_reference="REMITO-FLETE-502",
    )
    assert res_exit["success"] is True
    # Saldo en 1110 pasa de 14.000.000 a 12.000.000
    f1110 = obtener_fondo_por_codigo(conn, "1110")
    assert f1110["saldo_centavos"] == 12_000_000
    # Pendiente en reserva pasa de 3.000.000 a 1.000.000
    h_rsv_p7 = getReservationHistory(conn, rid)
    assert h_rsv_p7["applied_amount_cents"] == 2_000_000
    assert h_rsv_p7["pending_amount_cents"] == 1_000_000
    assert h_rsv_p7["status"] == "PARTIALLY_APPLIED"

    # -------------------------------------------------------------------------
    # PASO 8: Liberación del remanente de $10.000 de la reserva preventiva
    # -------------------------------------------------------------------------
    rel_res = releaseReservation(
        conn=conn,
        reservation_id=rid,
        amount_cents=1_000_000,
        reason="Sobrante de flete liberado por liquidación final",
        performed_by="tesorero_cert",
    )
    h_rsv_p8 = getReservationHistory(conn, rid)
    assert h_rsv_p8["pending_amount_cents"] == 0
    assert h_rsv_p8["released_amount_cents"] == 1_000_000
    # Invariante 3.000.000 = 2.000.000 (applied) + 1.000.000 (released) + 0 (pending)
    assert h_rsv_p8["original_amount_cents"] == (
        h_rsv_p8["applied_amount_cents"] + h_rsv_p8["released_amount_cents"] + h_rsv_p8["pending_amount_cents"]
    )

    # -------------------------------------------------------------------------
    # PASO 9: Traslado de 1110 hacia Mercado Pago Propio (1210) ($40.000) - Despacho PENDING_RECEIPT
    # -------------------------------------------------------------------------
    trf_banco = transferFunds(
        conn=conn,
        origin_fund_code="1110",
        destination_fund_code="1210",
        amount_cents=4_000_000,
        dispatched_by="tesorero_cert",
        reason="Transferencia a Mercado Pago Propio",
        evidence_reference="TRANSFER-MP-991",
    )
    assert trf_banco["status"] == "PENDING_RECEIPT"
    f1110 = obtener_fondo_por_codigo(conn, "1110")
    assert f1110["saldo_centavos"] == 8_000_000
    f1210 = obtener_fondo_por_codigo(conn, "1210")
    assert f1210["saldo_centavos"] == 0  # Aún en tránsito

    # -------------------------------------------------------------------------
    # PASO 10: Confirmación de recepción en 1210
    # -------------------------------------------------------------------------
    trf_conf = confirmTransferReceipt(
        conn=conn,
        transfer_id=trf_banco["transfer_id"],
        received_by="tesorero_cert",
    )
    assert trf_conf["status"] == "CONFIRMED"
    f1210 = obtener_fondo_por_codigo(conn, "1210")
    assert f1210["saldo_centavos"] == 4_000_000

    # -------------------------------------------------------------------------
    # PASO 11: Reversión parcial documentada de la salida de gastos ($2.000)
    # -------------------------------------------------------------------------
    # Movimiento original fue de 1_000_000 cents en 1110
    rev_parcial = reverseMovement(
        conn=conn,
        movement_id=m_gasto["movement_id"],
        user_id="supervisor_cert",
        reason="Reintegro por devolución de artículos de librería defectuosos",
        evidence_reference="NC-LIBRERIA-012",
        partial_amount_cents=200_000,
    )
    assert rev_parcial["reversed_amount_cents"] == 200_000
    # 1110 se restituye en $2.000: de 8_000_000 a 8_200_000
    f1110 = obtener_fondo_por_codigo(conn, "1110")
    assert f1110["saldo_centavos"] == 8_200_000

    # -------------------------------------------------------------------------
    # PASO 12: Arqueo físico de 1110 con faltante de $500 y apertura de expediente
    # -------------------------------------------------------------------------
    # Saldo en libros = 8_200_000 cents. Verificado = 8_150_000 cents (diferencia = -50_000)
    rec_cierre = createReconciliation(
        conn=conn,
        fund_code="1110",
        reconciliation_type="CLOSING",
        verified_amount_cents=8_150_000,
        user_id="auditor_cert",
        notes="Arqueo de cierre de jornada. Faltante detectado de $500",
        create_difference_case=True,
        investigation_notes="Se inicia averiguación por posible billete deteriorado no recibido",
    )
    assert rec_cierre["diferencia_cents"] == -50_000
    diff_case_id = rec_cierre["difference_case_id"]
    assert diff_case_id is not None
    # El arqueo NO muta el saldo contable
    f1110 = obtener_fondo_por_codigo(conn, "1110")
    assert f1110["saldo_centavos"] == 8_200_000

    # -------------------------------------------------------------------------
    # PASO 13: Resolución documentada del expediente de diferencias
    # -------------------------------------------------------------------------
    res_diff = resolveDifferenceCase(
        conn=conn,
        case_id=diff_case_id,
        resolution_notes="Aclarado: error de redondeo involuntario en vuelto de cliente autorizado como pérdida menor",
        evidence_reference="INFORME-AUDITORIA-01",
        resolved_by="auditor_cert",
    )
    assert res_diff["status"] == "RESOLVED"

    # -------------------------------------------------------------------------
    # PASO 14: Barrido/vaciado de 1110 hacia 1120 (Drugstore Acumulado)
    # -------------------------------------------------------------------------
    beginClosure(conn, journal_id=jid, user_id="supervisor_cert")
    sweep = executeCashDrawerSweep(
        conn=conn,
        journal_id=jid,
        user_id="supervisor_cert",
        reason="Vaciado obligatorio diario de 1110 hacia tesoro acumulado 1120",
    )
    assert sweep["swept"] is True
    assert sweep["amount_cents"] == 8_200_000

    # Verificación ineludible: 1110 DEBE ser exactamente 0
    f1110 = obtener_fondo_por_codigo(conn, "1110")
    assert f1110["saldo_centavos"] == 0

    f1120 = obtener_fondo_por_codigo(conn, "1120")
    assert f1120["saldo_centavos"] == 8_200_000

    # -------------------------------------------------------------------------
    # PASO 15: Cierre definitivo de jornada 1, verificación criptográfica,
    #          procesamiento Outbox con deduplicación y apertura de jornada 2
    # -------------------------------------------------------------------------
    # 15.A: Validar y Confirmar Cierre
    val = validateClosure(conn, journal_id=jid)
    assert val["can_close"] is True

    cierre_conf = confirmClosure(
        conn=conn,
        journal_id=jid,
        user_id="supervisor_cert",
        observations="Cierre integral completado exitosamente",
    )
    assert cierre_conf["status"] == "CONFIRMED"
    canonical_hash = cierre_conf["canonical_hash"]
    assert len(canonical_hash) == 64

    # 15.B: Verificación matemática estricta de la cadena de auditoría append-only
    audit_check = verifyAuditChain(conn)
    assert audit_check["valid"] is True
    assert audit_check["total_events"] == 6
    assert len(audit_check["errors"]) == 0

    # 15.C: Despacho y deduplicación de eventos del Outbox transaccional
    outbox_res = processOutbox(conn, consumer_id="CERT_DISPATCHER")
    assert outbox_res["processed"] == 4
    assert outbox_res["published"] == 4
    assert outbox_res["failed"] == 0
    assert outbox_res["dead_letter"] == 0

    # Segundo pase para verificar deduplicación idempotente
    outbox_res2 = processOutbox(conn, consumer_id="CERT_DISPATCHER")
    assert outbox_res2["processed"] == 0

    # 15.D: Apertura de jornada 2 con 1110 iniciando en 0 exacto
    j2 = openNextJournal(conn, user_id="tesorero_cert", observations="Apertura Jornada 2")
    assert j2["estado"] == "ABIERTA"
    assert j2["numero_jornada"] == 2

    f1110_j2 = obtener_fondo_por_codigo(conn, "1110")
    assert f1110_j2["saldo_centavos"] == 0

    # Saldo acumulado seguro en tesoro 1120
    f1120_j2 = obtener_fondo_por_codigo(conn, "1120")
    assert f1120_j2["saldo_centavos"] == 8_200_000

    conn.close()
