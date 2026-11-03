"""Cierre Diario Definitivo y Snapshots Canónicos de Tesorería — LAB-010.

Reglas e Invariantes:
1. Una única jornada abierta por ámbito.
2. Bloqueo de operaciones ordinarias durante el proceso de cierre (estado 'EN_CIERRE').
3. Detección y bloqueo ante operaciones pendientes de revisión.
4. Detección y bloqueo ante traslados en tránsito no recibidos.
5. Identificación y documentación de reservas preventivas activas en snapshot.
6. Identificación de arqueos y expedientes de diferencias.
7. Vaciado completo obligatorio de Caja Principal Efectivo: 1110 -> 1120.
8. Verificación estricta: fondo 1110 = exactamente 0 centavos.
9. Generación de snapshot contable integral e inmutable.
10. Hash canónico SHA-256 determinista del snapshot.
11. Cierre inmutable protegido por triggers de base de datos.
12. Apertura de la siguiente jornada garantizando 1110 = 0.
13. No cerrar automáticamente reservas ni diferencias: se documentan en el snapshot.
14. Prohibición de que operaciones tardías modifiquen cierres pasados.
"""

from datetime import datetime, timezone
import hashlib
import json
import sqlite3
from typing import Any, Dict, List, Optional
import uuid

from tesoreria.audit_chain import canonical_json_dumps, recordAuditEvent
from tesoreria.availability import getFundAvailability, getTreasurySummary
from tesoreria.catalog import obtener_catalogo_fondos, obtener_fondo_por_codigo
from tesoreria.jornadas import abrir_jornada, obtener_jornada_actual, obtener_jornada_por_id
from tesoreria.money import formatMoney
from tesoreria.outbox import enqueueOutboxEvent
from tesoreria.transfers import confirmTransferReceipt, transferFunds

# Administradores con facultad exclusiva y plena para autorizar diferencias
ADMIN_USERS = {"alan", "yilda"}


class ErrorCierre(Exception):
    """Excepción para violaciones del protocolo de cierre de tesorería."""
    pass


def beginClosure(conn: sqlite3.Connection, journal_id: int, user_id: str) -> Dict[str, Any]:
    """Inicia el proceso de cierre de la jornada, bloqueando operaciones ordinarias."""
    if not user_id or not user_id.strip():
        raise ErrorCierre("El usuario que inicia el cierre es obligatorio.")

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, numero_jornada, fecha_operativa, estado, version
        FROM tesoreria_jornadas
        WHERE id = ?
        """,
        (journal_id,),
    )
    fila = cursor.fetchone()
    if not fila:
        cursor.close()
        raise ErrorCierre(f"Jornada #{journal_id} no encontrada.")

    jornada = dict(fila)
    if jornada["estado"] == "CERRADA":
        cursor.close()
        raise ErrorCierre(f"La jornada #{jornada['numero_jornada']} ya se encuentra cerrada definitivamente.")

    if jornada["estado"] == "EN_CIERRE":
        cursor.close()
        return obtener_jornada_por_id(conn, journal_id)

    # Pasar jornada a EN_CIERRE
    cursor.execute(
        """
        UPDATE tesoreria_jornadas
        SET estado = 'EN_CIERRE', version = version + 1
        WHERE id = ?
        """,
        (journal_id,),
    )

    now_utc = datetime.now(timezone.utc).isoformat()
    recordAuditEvent(
        conn=conn,
        event_type="CLOSURE_INITIATED",
        aggregate_type="JOURNAL",
        aggregate_id=str(journal_id),
        user_id=user_id.strip(),
        payload={"journal_id": journal_id, "numero_jornada": jornada["numero_jornada"], "estado": "EN_CIERRE"},
        occurred_at=now_utc,
    )

    conn.commit()
    cursor.close()
    return obtener_jornada_por_id(conn, journal_id)


def validateClosure(
    conn: sqlite3.Connection,
    journal_id: int,
    allow_pending_transfers: bool = False,
    allow_nonzero_1110: bool = False,
) -> Dict[str, Any]:
    """Evalúa todas las precondiciones necesarias para permitir el cierre definitivo."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, numero_jornada, estado
        FROM tesoreria_jornadas
        WHERE id = ?
        """,
        (journal_id,),
    )
    jornada_row = cursor.fetchone()
    if not jornada_row:
        cursor.close()
        raise ErrorCierre(f"Jornada #{journal_id} no encontrada.")

    reasons: List[str] = []
    warnings: List[str] = []

    # 1. Movimientos en revisión pendientes (siempre bloqueantes)
    cursor.execute(
        """
        SELECT COUNT(*) FROM tesoreria_movimientos
        WHERE journal_id = ? AND status = 'PENDING_REVIEW'
        """,
        (journal_id,),
    )
    pending_movs = cursor.fetchone()[0]
    if pending_movs > 0:
        reasons.append(f"Existen {pending_movs} movimientos en estado 'PENDING_REVIEW' sin resolver.")

    # 2. Traslados en tránsito no recibidos (se consultan a nivel global de tesorería)
    cursor.execute(
        """
        SELECT t.transfer_id, fo.codigo as origin_code, fo.nombre as origin_name,
               fd.codigo as dest_code, fd.nombre as dest_name,
               t.amount_cents, t.dispatched_by, t.dispatched_at, t.reason, t.out_movement_id
        FROM tesoreria_traslados t
        JOIN tesoreria_fondos fo ON t.origin_fund_id = fo.id
        JOIN tesoreria_fondos fd ON t.destination_fund_id = fd.id
        WHERE t.status = 'PENDING_RECEIPT'
        ORDER BY t.id ASC
        """
    )
    filas_trf = cursor.fetchall()
    pending_transfers_list = [
        {
            "transfer_id": r[0],
            "origin_fund_code": r[1],
            "origin_fund_name": r[2],
            "destination_fund_code": r[3],
            "destination_fund_name": r[4],
            "amount_cents": r[5],
            "amount_formatted": formatMoney(r[5]),
            "dispatched_by": r[6],
            "dispatched_at": r[7],
            "reason": r[8],
            "out_movement_id": r[9],
        }
        for r in filas_trf
    ]
    pending_transfers = len(pending_transfers_list)
    pending_transfers_amount = sum(t["amount_cents"] for t in pending_transfers_list)

    if pending_transfers > 0:
        msg_trf = (
            f"Existen {pending_transfers} traslados en tránsito ('PENDING_RECEIPT') "
            f"por un total de {formatMoney(pending_transfers_amount)}."
        )
        if allow_pending_transfers:
            warnings.append(
                f"{msg_trf} Se autorizó el cierre con traslados pendientes; "
                f"quedarán documentados en el snapshot sin computarse como recibidos en destino."
            )
        else:
            reasons.append(f"{msg_trf} Requiere confirmación/cancelación o autorización expresa del operador.")

    # 3. Reservas activas (se permiten pero se cuantifican)
    cursor.execute(
        """
        SELECT COUNT(*), COALESCE(SUM(pending_amount_cents), 0)
        FROM tesoreria_reservas
        WHERE journal_id = ? AND status IN ('ACTIVE', 'PARTIALLY_APPLIED')
        """,
        (journal_id,),
    )
    rsv_row = cursor.fetchone()
    active_reservations_count = rsv_row[0]
    active_reservations_amount = rsv_row[1]

    # 4. Diferencias de arqueo de la jornada (deben estar documentadas con observación)
    cursor.execute(
        """
        SELECT a.reconciliation_id, a.fund_id, f.codigo as fund_code, f.nombre as fund_name,
               a.saldo_registrado_cents, a.saldo_verificado_cents, a.diferencia_cents,
               a.authorized_by, a.authorized_at, a.authorization_notes, a.notes
        FROM tesoreria_arqueos a
        JOIN tesoreria_fondos f ON a.fund_id = f.id
        WHERE a.journal_id = ? AND a.diferencia_cents != 0
        ORDER BY a.id ASC
        """,
        (journal_id,),
    )
    diff_rows = cursor.fetchall()
    unauthorized_diffs: List[Dict[str, Any]] = []
    authorized_diffs: List[Dict[str, Any]] = []
    sobrantes_pendientes: List[Dict[str, Any]] = []
    for r in diff_rows:
        da = dict(r)
        has_obs = bool((da.get("notes") and da["notes"].strip()) or (da.get("authorization_notes") and da["authorization_notes"].strip()))
        tipo = "FALTANTE" if da["diferencia_cents"] < 0 else "SOBRANTE"
        da["tipo_diferencia"] = tipo
        da["is_authorized"] = has_obs
        da["is_documented"] = has_obs
        obs_text = (da.get("notes") or da.get("authorization_notes") or "").strip()
        if not has_obs:
            unauthorized_diffs.append(da)
            reasons.append(
                f"El fondo {da['fund_code']} ({da['fund_name']}) presenta una diferencia ({tipo}) de "
                f"{formatMoney(da['diferencia_cents'])} sin observación registrada (Saldo registrado: {formatMoney(da['saldo_registrado_cents'])}, "
                f"Contado: {formatMoney(da['saldo_verificado_cents'])}). "
                f"Debe ingresar una observación para documentarla antes de permitir el cierre."
            )
        else:
            authorized_diffs.append(da)
            if tipo == "SOBRANTE":
                msg_sob = (
                    f"Sobrante físico documentado en fondo {da['fund_code']} ({da['fund_name']}): "
                    f"{formatMoney(da['diferencia_cents'])}. Observación: {obs_text}."
                )
                warnings.append(msg_sob)
                sobrantes_pendientes.append({
                    "fund_code": da["fund_code"],
                    "fund_name": da["fund_name"],
                    "sobrante_cents": da["diferencia_cents"],
                    "sobrante_formatted": formatMoney(da["diferencia_cents"]),
                    "observaciones": obs_text,
                    "authorized_by": da.get("authorized_by"),
                    "authorized_at": da.get("authorized_at"),
                })
            else:
                warnings.append(
                    f"Faltante físico documentado en fondo {da['fund_code']} ({da['fund_name']}): "
                    f"{formatMoney(da['diferencia_cents'])}. Observación: {obs_text}."
                )

    # 5. Expedientes de diferencias abiertos
    cursor.execute(
        """
        SELECT COUNT(*), COALESCE(SUM(difference_cents), 0)
        FROM tesoreria_expedientes_diferencias
        WHERE journal_id = ? AND status IN ('OPEN', 'UNDER_REVIEW', 'PENDING')
        """,
        (journal_id,),
    )
    diff_row = cursor.fetchone()
    open_diff_cases_count = diff_row[0]
    open_diff_cases_amount = diff_row[1]

    # 6. Saldo en Caja Principal (1110)
    f1110 = obtener_fondo_por_codigo(conn, "1110")
    saldo_1110 = f1110["saldo_centavos"] if f1110 else 0
    needs_sweep = saldo_1110 > 0
    if needs_sweep:
        msg_sweep = f"El fondo 1110 (Caja Principal Efectivo) tiene un remanente de {formatMoney(saldo_1110)}."
        if allow_nonzero_1110:
            warnings.append(f"{msg_sweep} Se autorizó cierre sin vaciado forzado.")
        else:
            reasons.append(f"{msg_sweep} requiere vaciado a 1120 o autorización para conservar remanente.")

    # 7. Saldo en Caja PREPAY Diario Efectivo (1160)
    f1160 = obtener_fondo_por_codigo(conn, "1160")
    saldo_1160 = f1160["saldo_centavos"] if f1160 else 0
    needs_prepay_sweep = saldo_1160 > 0
    if needs_prepay_sweep:
        msg_prepay = f"El fondo 1160 (Prepay Diario Efectivo) tiene un remanente de {formatMoney(saldo_1160)}."
        reasons.append(f"{msg_prepay} requiere vaciado obligatorio a 1130 antes de confirmar el cierre.")

    cursor.close()

    can_close = len(reasons) == 0

    return {
        "can_close": can_close,
        "reasons": reasons,
        "blockers": reasons,
        "warnings": warnings,
        "needs_sweep": needs_sweep,
        "needs_prepay_sweep": needs_prepay_sweep,
        "saldo_1110_cents": saldo_1110,
        "saldo_1110_formatted": formatMoney(saldo_1110),
        "saldo_1160_cents": saldo_1160,
        "saldo_1160_formatted": formatMoney(saldo_1160),
        "cash_drawer_balance_cents": saldo_1110,
        "cash_drawer_balance_formatted": formatMoney(saldo_1110),
        "pending_movements_count": pending_movs,
        "pending_transfers_count": pending_transfers,
        "transfers_in_transit_count": pending_transfers,
        "pending_transfers_amount_cents": pending_transfers_amount,
        "pending_transfers_amount_formatted": formatMoney(pending_transfers_amount),
        "pending_transfers_list": pending_transfers_list,
        "active_reservations_count": active_reservations_count,
        "active_reservations_amount_cents": active_reservations_amount,
        "open_diff_cases_count": open_diff_cases_count,
        "open_differences_count": open_diff_cases_count,
        "open_diff_cases_amount_cents": open_diff_cases_amount,
        "unauthorized_differences": unauthorized_diffs,
        "authorized_differences": authorized_diffs,
        "has_unauthorized_differences": len(unauthorized_diffs) > 0,
        "sobrantes_pendientes_definicion": sobrantes_pendientes,
    }


def executeCashDrawerSweep(
    conn: sqlite3.Connection,
    journal_id: int,
    user_id: str,
    reason: str = "Vaciado obligatorio de cierre 1110 -> 1120",
    auto_commit: bool = True,
) -> Dict[str, Any]:
    """Ejecuta el vaciado de Caja Principal (1110) hacia Caja Secundaria / Tesoro (1120).

    Invariantes del cierre flexible con diferencias documentadas:
    1. Si hubo arqueo en 1110 con diferencia no autorizada, BLOQUEA el barrido.
    2. Si hubo arqueo con faltante autorizado, traslada EXCLUSIVAMENTE el efectivo contado.
    3. Si hubo arqueo con sobrante autorizado, traslada hasta el saldo disponible en 1110.
    4. El saldo contable residual de faltante en 1110 se resetea a 0 centavos sin generar
       movimientos ficticios, partidas compensatorias ni arrastrar saldo a la siguiente jornada.
    5. Al finalizar, el saldo de 1110 DEBE ser exactamente 0 centavos.
    """
    if not user_id or not user_id.strip():
        raise ErrorCierre("El usuario que ejecuta el vaciado es obligatorio.")

    f1110 = obtener_fondo_por_codigo(conn, "1110")
    if not f1110:
        raise ErrorCierre("Fondo 1110 no encontrado.")

    f1120 = obtener_fondo_por_codigo(conn, "1120")
    if not f1120:
        raise ErrorCierre("Fondo 1120 no encontrado.")

    jornada = obtener_jornada_por_id(conn, journal_id)
    if not jornada:
        raise ErrorCierre(f"Jornada #{journal_id} no encontrada.")
    if jornada["estado"] == "CERRADA":
        raise ErrorCierre(f"La jornada #{jornada['numero_jornada']} ya está cerrada.")

    cursor = conn.cursor()
    # Idempotencia: Verificar si ya existe un barrido confirmado para esta jornada
    cursor.execute(
        """
        SELECT transfer_id, amount_cents, status
        FROM tesoreria_traslados
        WHERE journal_id = ? AND evidence_reference = 'BARRIDO_CIERRE_DIARIO' AND status = 'CONFIRMED'
        ORDER BY id DESC LIMIT 1
        """,
        (journal_id,),
    )
    prev_sweep = cursor.fetchone()
    if prev_sweep:
        # Verificar si la operación completa fue confirmada o si es un registro parcial inconsistente
        cursor.execute(
            """
            SELECT COUNT(*) FROM tesoreria_auditoria_eventos
            WHERE event_type = 'CASH_DRAWER_SWEEP_EXECUTED' AND (aggregate_id = ? OR aggregate_id = ?)
            """,
            (prev_sweep[0], str(journal_id)),
        )
        has_audit = cursor.fetchone()[0] > 0
        is_clean_zero = (f1110["saldo_centavos"] == 0)

        if is_clean_zero and has_audit:
            cursor.close()
            return {
                "swept": False,
                "amount_cents": 0,
                "amount_formatted": "$0,00",
                "message": "El barrido de caja ya fue ejecutado previamente para esta jornada.",
                "status": "ALREADY_EXECUTED",
                "transfer_id": prev_sweep[0],
                "saldo_contado_cents": prev_sweep[1],
                "diferencia_cents": 0,
                "authorized_by": None,
            }
        else:
            cursor.close()
            raise ErrorCierre(
                f"Inconsistencia detectada: existe un registro de barrido parcial previo no completado para la jornada #{journal_id} "
                f"(traslado #{prev_sweep[0]}, saldo 1110: {formatMoney(f1110['saldo_centavos'])}). "
                f"No se alterará automáticamente. Requiere revisión técnica."
            )

    cursor.execute(
        """
        SELECT reconciliation_id, saldo_registrado_cents, saldo_verificado_cents, diferencia_cents,
               authorized_by, authorized_at, authorization_notes, notes, status
        FROM tesoreria_arqueos
        WHERE journal_id = ? AND fund_id = ?
        ORDER BY id DESC LIMIT 1
        """,
        (journal_id, f1110["id"]),
    )
    row_arq = cursor.fetchone()
    last_arq = dict(row_arq) if row_arq else None

    # Validar que si existe diferencia no resuelta, esté documentada con observación
    if last_arq and last_arq["diferencia_cents"] != 0 and last_arq.get("status") != "RESOLVED":
        has_obs = bool((last_arq.get("notes") and last_arq["notes"].strip()) or (last_arq.get("authorization_notes") and last_arq["authorization_notes"].strip()))
        if not has_obs:
            cursor.close()
            raise ErrorCierre(
                f"No se puede realizar el barrido: existe una diferencia de "
                f"{formatMoney(last_arq['diferencia_cents'])} en Caja 1110 sin observación registrada. "
                f"Ingrese una observación para documentarla antes de realizar el barrido."
            )

    saldo_teorico = f1110["saldo_centavos"]
    # Regla: Todo el efectivo contado pasa a 1120 (tanto faltantes como sobrantes).
    # Si la diferencia fue formalmente resuelta (status == 'RESOLVED'), el recuento físico fue aclarado y coincide con el teórico.
    if last_arq and last_arq.get("status") != "RESOLVED":
        saldo_sweep = last_arq["saldo_verificado_cents"]
    else:
        saldo_sweep = saldo_teorico

    # Caso en que 1110 ya está en 0 y contado es <= 0
    if saldo_sweep <= 0 and f1110["saldo_centavos"] == 0:
        cursor.close()
        return {
            "swept": False,
            "amount_cents": 0,
            "amount_formatted": "$0,00",
            "message": "Caja 1110 ya se encuentra en 0 centavos.",
            "status": "COMPLETED",
            "saldo_contado_cents": 0,
            "diferencia_cents": last_arq["diferencia_cents"] if last_arq else 0,
            "authorized_by": last_arq.get("authorized_by") if last_arq else None,
        }

    # Caso faltante total (contado <= 0 pero teórico > 0): corte a 0 atómico
    if saldo_sweep <= 0:
        try:
            now_utc = datetime.now(timezone.utc).isoformat()
            cursor.execute(
                "UPDATE tesoreria_fondos SET saldo_centavos = 0, version = version + 1, updated_at = ?, actualizado_por = ? WHERE id = ?",
                (now_utc, f"CORTE_FALTANTE_DOCUMENTADO_{user_id.strip()}", f1110["id"]),
            )
            recordAuditEvent(
                conn=conn,
                event_type="RECONCILIATION_BALANCE_CUTOFF",
                aggregate_type="JOURNAL",
                aggregate_id=str(journal_id),
                user_id=user_id.strip(),
                payload={
                    "journal_id": journal_id,
                    "fund_code": "1110",
                    "reconciliation_id": last_arq["reconciliation_id"] if last_arq else None,
                    "saldo_teorico_previo_cents": saldo_teorico,
                    "saldo_contado_trasladado_cents": 0,
                    "diferencia_cents": -saldo_teorico,
                    "tipo_diferencia": "FALTANTE_TOTAL",
                    "performed_by": user_id.strip(),
                    "justification": "Corte de jornada diaria: faltante total documentado, gaveta en cero para siguiente apertura.",
                },
                occurred_at=now_utc,
            )
            recordAuditEvent(
                conn=conn,
                event_type="CASH_DRAWER_SWEEP_EXECUTED",
                aggregate_type="JOURNAL",
                aggregate_id=str(journal_id),
                user_id=user_id.strip(),
                payload={
                    "journal_id": journal_id,
                    "origin_code": "1110",
                    "destination_code": "1120",
                    "amount_cents": 0,
                    "saldo_teorico_previo_cents": saldo_teorico,
                    "saldo_contado_cents": 0,
                    "diferencia_cents": -saldo_teorico,
                    "authorized_by": last_arq.get("authorized_by") if last_arq else None,
                    "transfer_id": None,
                    "sobrante_documentado_cents": 0,
                    "sobrante_destino_pendiente": False,
                },
                occurred_at=now_utc,
            )
            if auto_commit:
                conn.commit()
            cursor.close()
            return {
                "swept": False,
                "amount_cents": 0,
                "amount_formatted": "$0,00",
                "message": "Caja 1110 ajustada a 0 centavos por faltante total documentado.",
                "status": "COMPLETED",
                "saldo_contado_cents": 0,
                "diferencia_cents": -saldo_teorico,
                "authorized_by": last_arq.get("authorized_by") if last_arq else None,
            }
        except Exception:
            if auto_commit:
                conn.rollback()
            cursor.close()
            raise

    # Operación Atómica Integral:
    try:
        now_utc = datetime.now(timezone.utc).isoformat()
        transfer_key = f"sweep_{journal_id}_{uuid.uuid4().hex[:8]}"

        # 1. Despachar traslado 1110 -> 1120 por saldo_sweep (la totalidad del efectivo contado)
        transfer = transferFunds(
            conn=conn,
            origin_fund_code="1110",
            destination_fund_code="1120",
            amount_cents=saldo_sweep,
            dispatched_by=user_id.strip(),
            reason=reason.strip(),
            idempotency_key=transfer_key,
            evidence_reference="BARRIDO_CIERRE_DIARIO",
            auto_commit=False,
        )

        # 2. Confirmar recepción inmediata en 1120
        confirmed_transfer = confirmTransferReceipt(
            conn=conn,
            transfer_id=transfer["transfer_id"],
            received_by=user_id.strip(),
            idempotency_key=f"confirm_{transfer_key}",
            auto_commit=False,
        )

        # 3. Si hubo diferencia entre saldo teórico y efectivo contado trasladado,
        # ejecutar el corte documentado del remanente en 1110 a $0,00
        diff_cut_cents = saldo_teorico - saldo_sweep
        if diff_cut_cents != 0:
            tipo_dif = "FALTANTE" if diff_cut_cents > 0 else "SOBRANTE"
            cursor.execute(
                """
                UPDATE tesoreria_fondos
                SET saldo_centavos = 0,
                    version = version + 1,
                    updated_at = ?,
                    actualizado_por = ?
                WHERE id = ?
                """,
                (now_utc, f"CORTE_{tipo_dif}_DOCUMENTADO_{user_id.strip()}", f1110["id"]),
            )
            # 4. Registrar evento canónico de corte
            recordAuditEvent(
                conn=conn,
                event_type="RECONCILIATION_BALANCE_CUTOFF",
                aggregate_type="JOURNAL",
                aggregate_id=str(journal_id),
                user_id=user_id.strip(),
                payload={
                    "journal_id": journal_id,
                    "fund_code": "1110",
                    "reconciliation_id": last_arq["reconciliation_id"] if last_arq else None,
                    "saldo_teorico_previo_cents": saldo_teorico,
                    "saldo_contado_trasladado_cents": saldo_sweep,
                    "diferencia_cents": (saldo_sweep - saldo_teorico),
                    "diferencia_formatted": formatMoney(saldo_sweep - saldo_teorico),
                    "tipo_diferencia": tipo_dif,
                    "performed_by": user_id.strip(),
                    "observaciones": (last_arq.get("notes") if last_arq else None) or reason.strip(),
                    "justification": f"Corte de jornada diaria: traslado íntegro de efectivo físico contado ({formatMoney(saldo_sweep)}) a 1120 y corte de gaveta física a $0,00 con {tipo_dif.lower()} documentado.",
                },
                occurred_at=now_utc,
            )

        # 5. Verificar que 1110 quedó en exactamente 0
        f1110_post = obtener_fondo_por_codigo(conn, "1110")
        if f1110_post["saldo_centavos"] != 0:
            raise ErrorCierre(
                f"Fallo de integridad en vaciado: saldo posterior de 1110 es {f1110_post['saldo_centavos']}, se requería 0."
            )

        # 6. Re-anclar reservas activas de 1110 hacia 1120
        cursor.execute(
            """
            UPDATE tesoreria_reservas
            SET fund_id = ?, updated_at = ?
            WHERE fund_id = ? AND status IN ('ACTIVE', 'PARTIALLY_APPLIED')
            """,
            (f1120["id"], now_utc, f1110["id"]),
        )

        has_surplus = bool(last_arq and last_arq.get("diferencia_cents", 0) > 0)
        surplus_cents = last_arq["diferencia_cents"] if has_surplus else 0
        surplus_warning = (
            f"Sobrante físico documentado: {formatMoney(surplus_cents)}. "
            f"Diferencia documentada por {last_arq.get('authorized_by') or user_id.strip()}. "
            f"Destino económico pendiente de definición. No computa como disponibilidad operativa."
            if has_surplus else None
        )

        # 7. Registrar evento canónico CASH_DRAWER_SWEEP_EXECUTED dentro de la transacción
        recordAuditEvent(
            conn=conn,
            event_type="CASH_DRAWER_SWEEP_EXECUTED",
            aggregate_type="TRANSFER",
            aggregate_id=transfer["transfer_id"],
            user_id=user_id.strip(),
            payload={
                "journal_id": journal_id,
                "origin_code": "1110",
                "destination_code": "1120",
                "amount_cents": saldo_sweep,
                "saldo_teorico_previo_cents": saldo_teorico,
                "saldo_contado_cents": last_arq["saldo_verificado_cents"] if last_arq else saldo_teorico,
                "diferencia_cents": last_arq["diferencia_cents"] if last_arq else 0,
                "authorized_by": last_arq.get("authorized_by") if last_arq else None,
                "transfer_id": transfer["transfer_id"],
                "sobrante_documentado_cents": surplus_cents,
                "sobrante_destino_pendiente": has_surplus,
            },
            occurred_at=now_utc,
        )

        # 8. COMMIT ÚNICO de toda la operación atómica
        if auto_commit:
            conn.commit()
    except Exception:
        if auto_commit:
            conn.rollback()
        raise
    finally:
        cursor.close()

    return {
        "swept": True,
        "amount_cents": saldo_sweep,
        "amount_formatted": formatMoney(saldo_sweep),
        "transferred_to": "1120",
        "transferred_to_name": "Drugstore Acumulado",
        "origin_fund": "1110",
        "origin_fund_name": "Drugstore Diario",
        "caja_diaria_saldo_final_cents": 0,
        "caja_diaria_saldo_final_formatted": "$0,00",
        "transfer_id": transfer["transfer_id"],
        "status": confirmed_transfer["status"],
        "saldo_contado_cents": last_arq["saldo_verificado_cents"] if last_arq else saldo_sweep,
        "diferencia_cents": (saldo_sweep - saldo_teorico) if last_arq else 0,
        "authorized_by": last_arq.get("authorized_by") if last_arq else None,
        "sobrante_documentado_cents": surplus_cents,
        "sobrante_destino_pendiente": has_surplus,
        "advertencia_sobrante": surplus_warning,
        "message": f"Se trasladó la totalidad del efectivo contado ({formatMoney(saldo_sweep)}) a Drugstore Acumulado (1120). La caja diaria (1110) queda en $0,00.",
    }


def executePrepayDrawerSweep(
    conn: sqlite3.Connection,
    journal_id: int,
    user_id: str,
    reason: str = "Barrido obligatorio de cierre PREPAY 1160 -> 1130",
    auto_commit: bool = True,
) -> Dict[str, Any]:
    """Ejecuta el vaciado de Caja PREPAY Efectivo Diario (1160) hacia Prepay Efectivo Acumulado (1130).

    Invariantes del cierre:
    1. Si el fondo 1160 no existe en la base (base legacy sin migrar), no bloquea y retorna NOT_APPLICABLE.
    2. Si hubo arqueo en 1160 con diferencia no resuelta ni observada, bloquea el barrido.
    3. Todo el efectivo contado pasa a 1130.
    4. El saldo de 1160 al finalizar DEBE ser estrictamente 0 centavos.
    5. Las reservas activas en 1160 (si las hubiera) se re-anclan a 1130.
    6. Se registra el evento de auditoría canónico PREPAY_DRAWER_SWEEP_EXECUTED.
    """
    if not user_id or not user_id.strip():
        raise ErrorCierre("El usuario que ejecuta el vaciado PREPAY es obligatorio.")

    f1160 = obtener_fondo_por_codigo(conn, "1160")
    if not f1160:
        return {
            "swept": False,
            "amount_cents": 0,
            "amount_formatted": "$0,00",
            "message": "Fondo 1160 no existe en esta base de tesorería.",
            "status": "NOT_APPLICABLE",
        }

    f1130 = obtener_fondo_por_codigo(conn, "1130")
    if not f1130:
        raise ErrorCierre("Fondo 1130 (Prepay Efectivo) no encontrado.")

    jornada = obtener_jornada_por_id(conn, journal_id)
    if not jornada:
        raise ErrorCierre(f"Jornada #{journal_id} no encontrada.")
    if jornada["estado"] != "EN_CIERRE":
        raise ErrorCierre(
            f"El vaciado de Caja PREPAY (1160) solo puede ejecutarse cuando la jornada está en estado 'EN_CIERRE'. "
            f"Estado actual de jornada #{jornada['numero_jornada']}: '{jornada['estado']}'."
        )

    jornada_activa = obtener_jornada_actual(conn)
    if not jornada_activa or jornada_activa["id"] != journal_id:
        raise ErrorCierre(
            f"La jornada #{journal_id} no coincide con la jornada activa del sistema."
        )

    cursor = conn.cursor()
    # Idempotencia: Verificar si ya existe un barrido confirmado para esta jornada
    cursor.execute(
        """
        SELECT transfer_id, amount_cents, status
        FROM tesoreria_traslados
        WHERE journal_id = ? AND evidence_reference = 'BARRIDO_PREPAY_CIERRE_DIARIO' AND status = 'CONFIRMED'
        ORDER BY id DESC LIMIT 1
        """,
        (journal_id,),
    )
    prev_sweep = cursor.fetchone()
    if prev_sweep:
        cursor.execute(
            """
            SELECT COUNT(*) FROM tesoreria_auditoria_eventos
            WHERE event_type = 'PREPAY_DRAWER_SWEEP_EXECUTED' AND (aggregate_id = ? OR aggregate_id = ?)
            """,
            (prev_sweep[0], str(journal_id)),
        )
        has_audit = cursor.fetchone()[0] > 0
        is_clean_zero = (f1160["saldo_centavos"] == 0)

        if is_clean_zero and has_audit:
            cursor.close()
            return {
                "swept": False,
                "amount_cents": 0,
                "amount_formatted": "$0,00",
                "message": "El barrido de caja PREPAY ya fue ejecutado previamente para esta jornada.",
                "status": "ALREADY_EXECUTED",
                "transfer_id": prev_sweep[0],
                "saldo_contado_cents": prev_sweep[1],
                "diferencia_cents": 0,
                "authorized_by": None,
            }
        else:
            cursor.close()
            raise ErrorCierre(
                f"Inconsistencia detectada: existe un registro de barrido PREPAY previo no completado para la jornada #{journal_id} "
                f"(traslado #{prev_sweep[0]}, saldo 1160: {formatMoney(f1160['saldo_centavos'])}). "
                f"No se alterará automáticamente. Requiere revisión técnica."
            )

    # Caso en que se ejecutó previamente un barrido con importe 0 (faltante total o gaveta en 0)
    cursor.execute(
        """
        SELECT COUNT(*) FROM tesoreria_auditoria_eventos
        WHERE event_type = 'PREPAY_DRAWER_SWEEP_EXECUTED' AND aggregate_id = ?
        """,
        (str(journal_id),),
    )
    has_zero_sweep_audit = cursor.fetchone()[0] > 0
    if has_zero_sweep_audit and f1160["saldo_centavos"] == 0:
        cursor.close()
        return {
            "swept": False,
            "amount_cents": 0,
            "amount_formatted": "$0,00",
            "message": "El barrido de caja PREPAY ya fue ejecutado previamente para esta jornada.",
            "status": "ALREADY_EXECUTED",
            "transfer_id": None,
            "saldo_contado_cents": 0,
            "diferencia_cents": 0,
            "authorized_by": None,
        }

    # Arqueo físico obligatorio en la jornada activa para el fondo 1160
    cursor.execute(
        """
        SELECT reconciliation_id, journal_id, saldo_registrado_cents, saldo_verificado_cents, diferencia_cents,
               authorized_by, authorized_at, authorization_notes, notes, status
        FROM tesoreria_arqueos
        WHERE journal_id = ? AND fund_id = ?
        ORDER BY id DESC LIMIT 1
        """,
        (journal_id, f1160["id"]),
    )
    row_arq = cursor.fetchone()
    if not row_arq:
        cursor.close()
        raise ErrorCierre(
            f"No se puede realizar el barrido PREPAY: se requiere un arqueo físico registrado en la jornada activa "
            f"#{jornada['numero_jornada']} para el fondo 1160. Prohibido utilizar el saldo teórico contable como sustituto del efectivo contado."
        )

    last_arq = dict(row_arq)
    if last_arq["journal_id"] != journal_id:
        cursor.close()
        raise ErrorCierre(
            f"No se puede realizar el barrido PREPAY: el arqueo no pertenece a la jornada activa #{journal_id}."
        )

    # Validar que si existe diferencia no resuelta, esté documentada con observación
    if last_arq["diferencia_cents"] != 0 and last_arq.get("status") != "RESOLVED":
        has_obs = bool((last_arq.get("notes") and last_arq["notes"].strip()) or (last_arq.get("authorization_notes") and last_arq["authorization_notes"].strip()))
        if not has_obs:
            cursor.close()
            raise ErrorCierre(
                f"No se puede realizar el barrido PREPAY: existe una diferencia de "
                f"{formatMoney(last_arq['diferencia_cents'])} en Caja PREPAY 1160 sin observación registrada. "
                f"Ingrese una observación para documentarla antes de realizar el barrido."
            )

    saldo_teorico = f1160["saldo_centavos"]
    # El monto a barrer es ESTRICTAMENTE el saldo verificado del arqueo físico contado.
    # NUNCA utilizar el saldo teórico contable como sustituto del efectivo contado.
    saldo_sweep = last_arq["saldo_verificado_cents"]

    if saldo_sweep <= 0 and f1160["saldo_centavos"] == 0:
        now_utc = datetime.now(timezone.utc).isoformat()
        # Re-anclar reservas activas si existieran
        cursor.execute(
            """
            UPDATE tesoreria_reservas
            SET fund_id = ?, updated_at = ?
            WHERE fund_id = ? AND status IN ('ACTIVE', 'PARTIALLY_APPLIED')
            """,
            (f1130["id"], now_utc, f1160["id"]),
        )
        recordAuditEvent(
            conn=conn,
            event_type="PREPAY_DRAWER_SWEEP_EXECUTED",
            aggregate_type="JOURNAL",
            aggregate_id=str(journal_id),
            user_id=user_id.strip(),
            payload={
                "journal_id": journal_id,
                "origin_code": "1160",
                "destination_code": "1130",
                "amount_cents": 0,
                "saldo_teorico_previo_cents": 0,
                "saldo_contado_cents": 0,
                "diferencia_cents": 0,
                "authorized_by": last_arq.get("authorized_by"),
                "transfer_id": None,
            },
            occurred_at=now_utc,
        )
        if auto_commit:
            conn.commit()
        cursor.close()
        return {
            "swept": False,
            "amount_cents": 0,
            "amount_formatted": "$0,00",
            "message": "Caja PREPAY 1160 ya se encuentra en 0 centavos.",
            "status": "COMPLETED",
            "saldo_contado_cents": 0,
            "diferencia_cents": last_arq["diferencia_cents"] if last_arq else 0,
            "authorized_by": last_arq.get("authorized_by") if last_arq else None,
        }

    # Caso faltante total: corte a 0 atómico
    if saldo_sweep <= 0:
        try:
            now_utc = datetime.now(timezone.utc).isoformat()
            cursor.execute(
                "UPDATE tesoreria_fondos SET saldo_centavos = 0, version = version + 1, updated_at = ?, actualizado_por = ? WHERE id = ?",
                (now_utc, f"CORTE_FALTANTE_DOCUMENTADO_{user_id.strip()}", f1160["id"]),
            )
            # Re-anclar reservas activas si existieran
            cursor.execute(
                """
                UPDATE tesoreria_reservas
                SET fund_id = ?, updated_at = ?
                WHERE fund_id = ? AND status IN ('ACTIVE', 'PARTIALLY_APPLIED')
                """,
                (f1130["id"], now_utc, f1160["id"]),
            )
            recordAuditEvent(
                conn=conn,
                event_type="RECONCILIATION_BALANCE_CUTOFF",
                aggregate_type="JOURNAL",
                aggregate_id=str(journal_id),
                user_id=user_id.strip(),
                payload={
                    "journal_id": journal_id,
                    "fund_code": "1160",
                    "reconciliation_id": last_arq["reconciliation_id"] if last_arq else None,
                    "saldo_teorico_previo_cents": saldo_teorico,
                    "saldo_contado_trasladado_cents": 0,
                    "diferencia_cents": -saldo_teorico,
                    "tipo_diferencia": "FALTANTE_TOTAL",
                    "performed_by": user_id.strip(),
                    "justification": "Corte de jornada diaria PREPAY: faltante total documentado, gaveta 1160 en cero.",
                },
                occurred_at=now_utc,
            )
            recordAuditEvent(
                conn=conn,
                event_type="PREPAY_DRAWER_SWEEP_EXECUTED",
                aggregate_type="JOURNAL",
                aggregate_id=str(journal_id),
                user_id=user_id.strip(),
                payload={
                    "journal_id": journal_id,
                    "origin_code": "1160",
                    "destination_code": "1130",
                    "amount_cents": 0,
                    "saldo_teorico_previo_cents": saldo_teorico,
                    "saldo_contado_cents": 0,
                    "diferencia_cents": -saldo_teorico,
                    "authorized_by": last_arq.get("authorized_by") if last_arq else None,
                    "transfer_id": None,
                },
                occurred_at=now_utc,
            )
            if auto_commit:
                conn.commit()
            cursor.close()
            return {
                "swept": False,
                "amount_cents": 0,
                "amount_formatted": "$0,00",
                "message": "Caja PREPAY 1160 ajustada a 0 centavos por faltante total documentado.",
                "status": "COMPLETED",
                "saldo_contado_cents": 0,
                "diferencia_cents": -saldo_teorico,
                "authorized_by": last_arq.get("authorized_by") if last_arq else None,
            }
        except Exception:
            if auto_commit:
                conn.rollback()
            cursor.close()
            raise

    # Operación Atómica de Barrido PREPAY 1160 -> 1130
    try:
        now_utc = datetime.now(timezone.utc).isoformat()
        transfer_key = f"prepay_sweep_{journal_id}_{uuid.uuid4().hex[:8]}"

        transfer = transferFunds(
            conn=conn,
            origin_fund_code="1160",
            destination_fund_code="1130",
            amount_cents=saldo_sweep,
            dispatched_by=user_id.strip(),
            reason=reason.strip(),
            idempotency_key=transfer_key,
            evidence_reference="BARRIDO_PREPAY_CIERRE_DIARIO",
            context="PREPAY_DRAWER_SWEEP",
            auto_commit=False,
        )

        confirmed_transfer = confirmTransferReceipt(
            conn=conn,
            transfer_id=transfer["transfer_id"],
            received_by=user_id.strip(),
            idempotency_key=f"confirm_{transfer_key}",
            auto_commit=False,
        )

        diff_cut_cents = saldo_teorico - saldo_sweep
        if diff_cut_cents != 0:
            tipo_dif = "FALTANTE" if diff_cut_cents > 0 else "SOBRANTE"
            cursor.execute(
                """
                UPDATE tesoreria_fondos
                SET saldo_centavos = 0,
                    version = version + 1,
                    updated_at = ?,
                    actualizado_por = ?
                WHERE id = ?
                """,
                (now_utc, f"CORTE_{tipo_dif}_DOCUMENTADO_{user_id.strip()}", f1160["id"]),
            )
            recordAuditEvent(
                conn=conn,
                event_type="RECONCILIATION_BALANCE_CUTOFF",
                aggregate_type="JOURNAL",
                aggregate_id=str(journal_id),
                user_id=user_id.strip(),
                payload={
                    "journal_id": journal_id,
                    "fund_code": "1160",
                    "reconciliation_id": last_arq["reconciliation_id"] if last_arq else None,
                    "saldo_teorico_previo_cents": saldo_teorico,
                    "saldo_contado_trasladado_cents": saldo_sweep,
                    "diferencia_cents": (saldo_sweep - saldo_teorico),
                    "diferencia_formatted": formatMoney(saldo_sweep - saldo_teorico),
                    "tipo_diferencia": tipo_dif,
                    "performed_by": user_id.strip(),
                    "observaciones": (last_arq.get("notes") if last_arq else None) or reason.strip(),
                    "justification": f"Corte PREPAY: traslado íntegro de efectivo contado ({formatMoney(saldo_sweep)}) a 1130 y corte de gaveta física 1160 a $0,00.",
                },
                occurred_at=now_utc,
            )

        # Verificar que 1160 quedó en exactamente 0
        f1160_post = obtener_fondo_por_codigo(conn, "1160")
        if f1160_post["saldo_centavos"] != 0:
            raise ErrorCierre(
                f"Fallo de integridad en vaciado PREPAY: saldo posterior de 1160 es {f1160_post['saldo_centavos']}, se requería 0."
            )

        # Re-anclar reservas activas de 1160 hacia 1130 si hubiera
        cursor.execute(
            """
            UPDATE tesoreria_reservas
            SET fund_id = ?, updated_at = ?
            WHERE fund_id = ? AND status IN ('ACTIVE', 'PARTIALLY_APPLIED')
            """,
            (f1130["id"], now_utc, f1160["id"]),
        )

        recordAuditEvent(
            conn=conn,
            event_type="PREPAY_DRAWER_SWEEP_EXECUTED",
            aggregate_type="JOURNAL",
            aggregate_id=str(journal_id),
            user_id=user_id.strip(),
            payload={
                "journal_id": journal_id,
                "origin_code": "1160",
                "destination_code": "1130",
                "amount_cents": saldo_sweep,
                "saldo_teorico_previo_cents": saldo_teorico,
                "saldo_contado_cents": last_arq["saldo_verificado_cents"] if last_arq else saldo_sweep,
                "diferencia_cents": last_arq["diferencia_cents"] if last_arq else 0,
                "authorized_by": last_arq.get("authorized_by") if last_arq else None,
                "transfer_id": transfer["transfer_id"],
            },
            occurred_at=now_utc,
        )

        if auto_commit:
            conn.commit()
    except Exception:
        if auto_commit:
            conn.rollback()
        raise
    finally:
        cursor.close()

    return {
        "swept": True,
        "amount_cents": saldo_sweep,
        "amount_formatted": formatMoney(saldo_sweep),
        "transferred_to": "1130",
        "transferred_to_name": "Prepay Efectivo",
        "origin_fund": "1160",
        "origin_fund_name": "Prepay Diario Efectivo",
        "caja_prepay_saldo_final_cents": 0,
        "caja_prepay_saldo_final_formatted": "$0,00",
        "transfer_id": transfer["transfer_id"],
        "status": confirmed_transfer["status"],
        "saldo_contado_cents": last_arq["saldo_verificado_cents"] if last_arq else saldo_sweep,
        "diferencia_cents": (saldo_sweep - saldo_teorico) if last_arq else 0,
        "authorized_by": last_arq.get("authorized_by") if last_arq else None,
        "message": f"Se trasladó la totalidad del efectivo PREPAY contado ({formatMoney(saldo_sweep)}) a Prepay Efectivo (1130). La caja PREPAY diaria (1160) queda en $0,00.",
    }


def confirmClosure(
    conn: sqlite3.Connection,
    journal_id: int,
    user_id: str,
    observations: Optional[str] = None,
    allow_pending_transfers: bool = False,
    allow_nonzero_1110: bool = False,
) -> Dict[str, Any]:
    """Confirma definitivamente el cierre de la jornada, generando el snapshot inmutable y su hash canónico."""
    if not user_id or not user_id.strip():
        raise ErrorCierre("El usuario que confirma el cierre es obligatorio.")

    val = validateClosure(
        conn,
        journal_id,
        allow_pending_transfers=allow_pending_transfers,
        allow_nonzero_1110=allow_nonzero_1110,
    )
    if not val["can_close"]:
        raise ErrorCierre(f"No se puede confirmar el cierre: {', '.join(val['reasons'])}")

    # 1. Obtener la jornada
    jornada = obtener_jornada_por_id(conn, journal_id)
    if not jornada:
        raise ErrorCierre(f"Jornada #{journal_id} no encontrada.")

    if jornada["estado"] == "CERRADA":
        raise ErrorCierre(f"La jornada #{jornada['numero_jornada']} ya está cerrada.")

    cursor = conn.cursor()

    # 2. Generar Snapshot contable exhaustivo de los 9 fondos con arqueos asociados
    summary = getTreasurySummary(conn)
    fondos_snapshot = []
    for f in summary["fondos"]:
        cursor.execute(
            """
            SELECT saldo_verificado_cents, diferencia_cents, status, performed_by, performed_at, notes,
                   authorized_by, authorized_at, authorization_notes
            FROM tesoreria_arqueos
            WHERE journal_id = ? AND fund_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (journal_id, f["fund_id"]),
        )
        arq_row = cursor.fetchone()
        diff_cents = arq_row[1] if arq_row else None
        estado_rev = "CONCILIADO" if diff_cents == 0 else ("CON DIFERENCIA" if diff_cents is not None else "PENDIENTE")
        auth_by = arq_row[6] if arq_row else None
        auth_at = arq_row[7] if arq_row else None
        auth_notes = arq_row[8] if arq_row else None

        fondos_snapshot.append({
            "fund_code": f["fund_code"],
            "fund_name": f["fund_name"],
            "nature": f["nature"],
            "ownership": f["ownership"],
            "status": f["status"],
            "saldo_registrado_cents": f["saldo_registrado_cents"],
            "saldo_verificado_cents": arq_row[0] if arq_row else None,
            "diferencia_cents": diff_cents,
            "estado_revision": estado_rev,
            "saldo_reservado_cents": f["saldo_reservado_cents"],
            "saldo_disponible_cents": f["saldo_disponible_cents"],
            "authorized_by": auth_by,
            "authorized_at": auth_at,
            "authorization_notes": auth_notes,
            "tipo_diferencia": ("FALTANTE" if diff_cents < 0 else "SOBRANTE") if diff_cents and diff_cents != 0 else None,
        })

    # Listado de arqueos y conciliaciones realizados en esta jornada
    cursor.execute(
        """
        SELECT a.reconciliation_id, a.fund_id, f.codigo as fund_code, f.nombre as fund_name,
               a.reconciliation_type, a.status, a.saldo_registrado_cents, a.saldo_verificado_cents,
               a.diferencia_cents, a.performed_by, a.performed_at, a.notes,
               a.authorized_by, a.authorized_at, a.authorization_notes
        FROM tesoreria_arqueos a
        JOIN tesoreria_fondos f ON a.fund_id = f.id
        WHERE a.journal_id = ?
        ORDER BY a.id ASC
        """,
        (journal_id,),
    )
    arqueos_snapshot = [dict(r) for r in cursor.fetchall()]

    # Listado de reservas activas
    cursor.execute(
        """
        SELECT reservation_id, fund_id, original_amount_cents, pending_amount_cents,
               reason, beneficiary, obligation_id, status
        FROM tesoreria_reservas
        WHERE journal_id = ? AND status IN ('ACTIVE', 'PARTIALLY_APPLIED')
        ORDER BY id ASC
        """,
        (journal_id,),
    )
    reservas_snapshot = [dict(r) for r in cursor.fetchall()]

    # Listado de expedientes de diferencias abiertos
    cursor.execute(
        """
        SELECT e.case_id, e.reconciliation_id, e.fund_id, f.codigo as fund_code, f.nombre as fund_name,
               e.difference_cents, e.status, e.investigation_notes, e.created_at,
               e.authorized_by, e.authorized_at, e.authorization_notes
        FROM tesoreria_expedientes_diferencias e
        JOIN tesoreria_fondos f ON e.fund_id = f.id
        WHERE e.journal_id = ? AND e.status IN ('OPEN', 'UNDER_REVIEW', 'PENDING')
        ORDER BY e.id ASC
        """,
        (journal_id,),
    )
    diferencias_snapshot = [dict(r) for r in cursor.fetchall()]

    now_utc = datetime.now(timezone.utc).isoformat()
    closure_id = str(uuid.uuid4())

    snapshot_obj = {
        "closure_id": closure_id,
        "journal_id": journal_id,
        "numero_jornada": jornada["numero_jornada"],
        "fecha_operativa": jornada["fecha_operativa"],
        "closed_by": user_id.strip(),
        "closed_at": now_utc,
        "total_registrado_cents": summary["total_consolidado"]["total_registrado_cents"],
        "total_disponible_cents": summary["fondos_propios"]["total_disponible_cents"],
        "total_reservado_cents": summary["fondos_propios"]["total_reservado_cents"] + summary["fondos_administrados_custodia"]["total_reservado_cents"],
        "total_diferencias_cents": val["open_diff_cases_amount_cents"],
        "fondos": fondos_snapshot,
        "arqueos_realizados": arqueos_snapshot,
        "traslados_pendientes": val["pending_transfers_list"],
        "reservas_pendientes": reservas_snapshot,
        "expedientes_diferencias_abiertos": diferencias_snapshot,
        "advertencias_cierre": val.get("warnings", []),
        "diferencias_autorizadas": val.get("authorized_differences", []),
        "sobrantes_pendientes_definicion": val.get("sobrantes_pendientes_definicion", []),
        "observations": observations,
    }

    # Hash canónico SHA-256 del snapshot (claves estrictamente ordenadas)
    snapshot_json = canonical_json_dumps(snapshot_obj)
    canonical_hash = hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()

    # 3. Insertar en tesoreria_cierres
    cursor.execute(
        """
        INSERT INTO tesoreria_cierres (
            closure_id, journal_id, status, closed_by, closed_at,
            total_registrado_cents, total_disponible_cents, total_reservado_cents,
            total_diferencias_cents, snapshot_json, canonical_hash, observations, version
        ) VALUES (?, ?, 'CONFIRMED', ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            closure_id,
            journal_id,
            user_id.strip(),
            now_utc,
            summary["total_consolidado"]["total_registrado_cents"],
            summary["fondos_propios"]["total_disponible_cents"],
            snapshot_obj["total_reservado_cents"],
            val["open_diff_cases_amount_cents"],
            snapshot_json,
            canonical_hash,
            observations,
        ),
    )

    # 4. Actualizar tesoreria_jornadas a CERRADA
    cursor.execute(
        """
        UPDATE tesoreria_jornadas
        SET estado = 'CERRADA',
            cierre_timestamp_utc = ?,
            usuario_cierre = ?,
            total_cierre_centavos = ?,
            observaciones = COALESCE(?, observaciones),
            version = version + 1
        WHERE id = ?
        """,
        (
            now_utc,
            user_id.strip(),
            summary["total_consolidado"]["total_registrado_cents"],
            observations,
            journal_id,
        ),
    )

    # Actualizar saldos finales y diferencias en tesoreria_jornada_detalles
    for f in summary["fondos"]:
        cursor.execute(
            """
            SELECT diferencia_cents FROM tesoreria_arqueos
            WHERE journal_id = ? AND fund_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (journal_id, f["fund_id"]),
        )
        arq_d = cursor.fetchone()
        diff_cents_jd = arq_d[0] if arq_d else None
        cursor.execute(
            """
            UPDATE tesoreria_jornada_detalles
            SET saldo_cierre_centavos = ?,
                diferencia_centavos = ?
            WHERE jornada_id = ? AND codigo_fondo = ?
            """,
            (f["saldo_registrado_cents"], diff_cents_jd, journal_id, f["fund_code"]),
        )

    # 5. Auditoría canónica encadenada y Outbox
    recordAuditEvent(
        conn=conn,
        event_type="JOURNAL_CLOSED",
        aggregate_type="JOURNAL",
        aggregate_id=str(journal_id),
        user_id=user_id.strip(),
        payload={
            "closure_id": closure_id,
            "journal_id": journal_id,
            "canonical_hash": canonical_hash,
            "total_registrado_cents": summary["total_consolidado"]["total_registrado_cents"],
        },
        occurred_at=now_utc,
    )

    enqueueOutboxEvent(
        conn=conn,
        event_type="JOURNAL_CLOSED",
        aggregate_id=str(journal_id),
        payload={
            "closure_id": closure_id,
            "journal_id": journal_id,
            "canonical_hash": canonical_hash,
        },
    )

    conn.commit()
    cursor.close()

    closure_dict = {
        "closure_id": closure_id,
        "journal_id": journal_id,
        "numero_jornada": jornada["numero_jornada"],
        "status": "CONFIRMED",
        "closed_by": user_id.strip(),
        "closed_at": now_utc,
        "canonical_hash": canonical_hash,
        "snapshot_hash": canonical_hash,
        "snapshot_json": snapshot_json,
        "total_registrado_cents": summary["total_consolidado"]["total_registrado_cents"],
        "total_registrado_formatted": formatMoney(summary["total_consolidado"]["total_registrado_cents"]),
        "snapshot": snapshot_obj,
    }
    return closure_dict


def getClosure(conn: sqlite3.Connection, journal_id: int) -> Optional[Dict[str, Any]]:
    """Recupera el registro de cierre y su snapshot para una jornada dada."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT c.id, c.closure_id, c.journal_id, c.status, c.closed_by, c.closed_at,
               c.total_registrado_cents, c.total_disponible_cents, c.total_reservado_cents,
               c.total_diferencias_cents, c.snapshot_json, c.canonical_hash, c.observations,
               j.numero_jornada, j.fecha_operativa
        FROM tesoreria_cierres c
        JOIN tesoreria_jornadas j ON c.journal_id = j.id
        WHERE c.journal_id = ?
        """,
        (journal_id,),
    )
    row = cursor.fetchone()
    cursor.close()
    if not row:
        return None

    d = dict(row)
    try:
        d["snapshot"] = json.loads(d["snapshot_json"])
    except Exception:
        d["snapshot"] = {}

    d["snapshot_data"] = d["snapshot"]
    d["snapshot_hash"] = d.get("canonical_hash")
    d["total_registrado_formatted"] = formatMoney(d["total_registrado_cents"])
    d["total_disponible_formatted"] = formatMoney(d["total_disponible_cents"])
    d["total_reservado_formatted"] = formatMoney(d["total_reservado_cents"])
    d["total_diferencias_formatted"] = formatMoney(d["total_diferencias_cents"])
    return d


def getClosureHistory(conn: sqlite3.Connection, limit: int = 50) -> List[Dict[str, Any]]:
    """Consulta histórica de cierres definitivos de jornada con sus snapshots canónicos."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT c.id, c.closure_id, c.journal_id, c.status, c.closed_by, c.closed_at,
               c.total_registrado_cents, c.total_disponible_cents, c.total_reservado_cents,
               c.total_diferencias_cents, c.snapshot_json, c.canonical_hash, c.observations,
               j.numero_jornada, j.fecha_operativa
        FROM tesoreria_cierres c
        JOIN tesoreria_jornadas j ON c.journal_id = j.id
        ORDER BY c.id DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cursor.fetchall()
    cursor.close()

    result = []
    for r in rows:
        d = dict(r)
        try:
            d["snapshot"] = json.loads(d["snapshot_json"])
        except Exception:
            d["snapshot"] = {}
        d["snapshot_data"] = d["snapshot"]
        d["snapshot_hash"] = d.get("canonical_hash")
        d["total_registrado_formatted"] = formatMoney(d["total_registrado_cents"])
        d["total_disponible_formatted"] = formatMoney(d["total_disponible_cents"])
        d["total_reservado_formatted"] = formatMoney(d["total_reservado_cents"])
        d["total_diferencias_formatted"] = formatMoney(d["total_diferencias_cents"])
        result.append(d)
    return result


def openNextJournal(
    conn: sqlite3.Connection,
    user_id: str,
    observations: Optional[str] = None,
) -> Dict[str, Any]:
    """Abre la siguiente jornada operativa asegurando continuidad contable y 1110 = 0."""
    if not user_id or not user_id.strip():
        raise ErrorCierre("El usuario es obligatorio.")

    jornada_activa = obtener_jornada_actual(conn)
    if jornada_activa:
        raise ErrorCierre(
            f"No se puede abrir una nueva jornada: la jornada #{jornada_activa['numero_jornada']} está {jornada_activa['estado']}."
        )

    f1110 = obtener_fondo_por_codigo(conn, "1110")
    if f1110 and f1110["saldo_centavos"] != 0:
        raise ErrorCierre(
            f"Invariante violada: El fondo 1110 (Caja Principal Efectivo) debe iniciar en cero centavos. Saldo actual: {f1110['saldo_centavos']}."
        )

    nueva_jornada = abrir_jornada(conn=conn, usuario=user_id, observaciones=observations)

    now_utc = datetime.now(timezone.utc).isoformat()
    recordAuditEvent(
        conn=conn,
        event_type="NEXT_JOURNAL_OPENED",
        aggregate_type="JOURNAL",
        aggregate_id=str(nueva_jornada["id"]),
        user_id=user_id.strip(),
        payload={"journal_id": nueva_jornada["id"], "numero_jornada": nueva_jornada["numero_jornada"]},
        occurred_at=now_utc,
    )

    return nueva_jornada
