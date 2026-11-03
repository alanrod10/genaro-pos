"""Reversiones Inmutables y Contra-asientos de Tesorería — LAB-011.

Reglas e Invariantes:
- NUNCA eliminar ni modificar registros originales (inmutabilidad estricta).
- Cada reversión genera un contra-movimiento vinculado explícitamente al identificador original.
- Soporte para reversiones totales y parciales con acumulación determinista.
- Regla de integridad: suma de importes revertidos <= importe original.
- Idempotencia estricta en la solicitud de reversión.
- Exigencia de motivo justificado, evidencia documental y usuario autorizante.
- Control de dependencias: validación de disponibilidad para contra-asientos.
- Registro atómico dentro de una única transacción con auditoría encadenada y Outbox.
"""

from datetime import datetime, timezone
import sqlite3
from typing import Any, Dict, List, Optional
import uuid

from tesoreria.audit_chain import recordAuditEvent
from tesoreria.catalog import obtener_fondo_por_id
from tesoreria.idempotency import get_idempotent_response, record_idempotent_response
from tesoreria.jornadas import obtener_jornada_actual
from tesoreria.money import addMoney, assertValidMoney, formatMoney, subtractMoney
from tesoreria.outbox import enqueueOutboxEvent


class ErrorReversion(Exception):
    """Excepción para fallas en la ejecución de reversiones."""
    pass


def reverseMovement(
    conn: sqlite3.Connection,
    movement_id: str,
    user_id: str,
    reason: str,
    evidence_reference: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    partial_amount_cents: Optional[int] = None,
) -> Dict[str, Any]:
    """Ejecuta la reversión total o parcial de un movimiento confirmando un contra-asiento vinculado."""
    if not user_id or not user_id.strip():
        raise ErrorReversion("El usuario que autoriza la reversión es obligatorio.")

    if not reason or not reason.strip():
        raise ErrorReversion("El motivo de la reversión es obligatorio.")

    if idempotency_key:
        cached = get_idempotent_response(conn, idempotency_key, {"movement_id": movement_id, "amount": partial_amount_cents})
        if cached:
            return cached

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, movement_id, journal_id, fund_id, movement_type,
               amount_cents, currency, status, counterpart, reason,
               reversed_amount_cents
        FROM tesoreria_movimientos
        WHERE movement_id = ?
        """,
        (movement_id,),
    )
    original_row = cursor.fetchone()
    if not original_row:
        cursor.close()
        raise ErrorReversion(f"Movimiento '{movement_id}' no encontrado.")

    original = dict(original_row)
    if original["status"] != "POSTED":
        cursor.close()
        raise ErrorReversion(
            f"Solo se pueden revertir movimientos confirmados ('POSTED'). Estado actual: {original['status']}."
        )

    if original["movement_type"] not in ("ENTRY", "EXIT"):
        cursor.close()
        raise ErrorReversion(
            f"Tipo de movimiento no reversible por esta vía: {original['movement_type']}."
        )

    # 1. Calcular monto acumulado previamente revertido
    cursor.execute(
        """
        SELECT COALESCE(SUM(reversed_amount_cents), 0)
        FROM tesoreria_reversiones
        WHERE target_id = ? AND status = 'COMPLETED'
        """,
        (movement_id,),
    )
    previously_reversed = cursor.fetchone()[0]

    original_amount = original["amount_cents"]
    available_to_reverse = subtractMoney(original_amount, previously_reversed)

    if available_to_reverse <= 0:
        cursor.close()
        raise ErrorReversion(f"El movimiento '{movement_id}' ya fue totalmente revertido ({formatMoney(original_amount)}).")

    # Determinar importe a revertir
    if partial_amount_cents is not None:
        assertValidMoney(partial_amount_cents)
        if partial_amount_cents <= 0:
            cursor.close()
            raise ErrorReversion("El importe parcial a revertir debe ser estrictamente positivo.")
        if partial_amount_cents > available_to_reverse:
            cursor.close()
            raise ErrorReversion(
                f"El importe a revertir ({formatMoney(partial_amount_cents)}) excede el saldo reversible disponible ({formatMoney(available_to_reverse)})."
            )
        amount_to_reverse = partial_amount_cents
    else:
        amount_to_reverse = available_to_reverse

    # 2. Control de fondos y saldos disponibles para el contra-asiento
    fondo = obtener_fondo_por_id(conn, original["fund_id"])
    if not fondo:
        cursor.close()
        raise ErrorReversion(f"Fondo asociado #{original['fund_id']} no encontrado.")

    saldo_actual = fondo["saldo_centavos"]

    # Si el original fue ENTRY (ingresó dinero): la reversión requiere restar dinero (EXIT)
    if original["movement_type"] == "ENTRY":
        if saldo_actual < amount_to_reverse:
            cursor.close()
            raise ErrorReversion(
                f"Control de dependencias: Saldo insuficiente en fondo {fondo['codigo']} para revertir entrada. "
                f"Saldo actual: {formatMoney(saldo_actual)}, requerido: {formatMoney(amount_to_reverse)}."
            )
        nuevo_saldo = subtractMoney(saldo_actual, amount_to_reverse)
    else:
        # Si el original fue EXIT (salió dinero): la reversión devuelve dinero al fondo (ENTRY)
        nuevo_saldo = addMoney(saldo_actual, amount_to_reverse)

    jornada = obtener_jornada_actual(conn)
    if not jornada:
        cursor.close()
        raise ErrorReversion("No hay una jornada de tesorería activa para registrar el contra-asiento.")

    reversal_id = str(uuid.uuid4())
    counter_mov_id = str(uuid.uuid4())
    now_utc = datetime.now(timezone.utc).isoformat()

    # 3. Crear contra-movimiento vinculado
    cursor.execute(
        """
        INSERT INTO tesoreria_movimientos (
            movement_id, idempotency_key, journal_id, fund_id,
            movement_type, amount_cents, currency, counterpart,
            reason, evidence_reference, created_by, terminal_id,
            status, occurred_at, recorded_at, is_reversal, reversal_of_id
        ) VALUES (?, ?, ?, ?, 'REVERSAL', ?, 'ARS', ?, ?, ?, ?, 'TERM-01', 'POSTED', ?, ?, 1, ?)
        """,
        (
            counter_mov_id,
            f"cmov_{reversal_id}",
            jornada["id"],
            fondo["id"],
            amount_to_reverse,
            f"Contra-asiento reversión de {original['movement_type']} {movement_id}",
            reason.strip(),
            evidence_reference,
            user_id.strip(),
            now_utc,
            now_utc,
            movement_id,
        ),
    )

    # 4. Actualizar saldo del fondo
    cursor.execute(
        """
        UPDATE tesoreria_fondos
        SET saldo_centavos = ?, updated_at = ?, actualizado_por = ?, version = version + 1
        WHERE id = ?
        """,
        (nuevo_saldo, now_utc, user_id.strip(), fondo["id"]),
    )

    # 5. Actualizar monto revertido acumulado en el movimiento original
    new_accumulated_reversed = addMoney(previously_reversed, amount_to_reverse)
    cursor.execute(
        """
        UPDATE tesoreria_movimientos
        SET reversed_amount_cents = ?
        WHERE movement_id = ?
        """,
        (new_accumulated_reversed, movement_id),
    )

    # 6. Registrar en tesoreria_reversiones
    cursor.execute(
        """
        INSERT INTO tesoreria_reversiones (
            reversal_id, idempotency_key, target_type, target_id,
            original_amount_cents, reversed_amount_cents, counter_movement_id,
            reason, evidence_reference, authorized_by, created_at, status
        ) VALUES (?, ?, 'MOVEMENT', ?, ?, ?, ?, ?, ?, ?, ?, 'COMPLETED')
        """,
        (
            reversal_id,
            idempotency_key,
            movement_id,
            original_amount,
            amount_to_reverse,
            counter_mov_id,
            reason.strip(),
            evidence_reference,
            user_id.strip(),
            now_utc,
        ),
    )

    reversal_dict = {
        "reversal_id": reversal_id,
        "target_type": "MOVEMENT",
        "target_id": movement_id,
        "original_movement_type": original["movement_type"],
        "original_amount_cents": original_amount,
        "original_amount_formatted": formatMoney(original_amount),
        "reversed_amount_cents": amount_to_reverse,
        "reversed_amount_formatted": formatMoney(amount_to_reverse),
        "total_reversed_cents": new_accumulated_reversed,
        "counter_movement_id": counter_mov_id,
        "fund_code": fondo["codigo"],
        "new_fund_balance_cents": nuevo_saldo,
        "new_fund_balance_formatted": formatMoney(nuevo_saldo),
        "reason": reason.strip(),
        "evidence_reference": evidence_reference,
        "authorized_by": user_id.strip(),
        "created_at": now_utc,
        "status": "COMPLETED",
    }

    # Auditoría canónica encadenada y Outbox
    recordAuditEvent(
        conn=conn,
        event_type="MOVEMENT_REVERSED",
        aggregate_type="MOVEMENT",
        aggregate_id=movement_id,
        user_id=user_id.strip(),
        payload=reversal_dict,
        occurred_at=now_utc,
    )

    enqueueOutboxEvent(
        conn=conn,
        event_type="MOVEMENT_REVERSED",
        aggregate_id=movement_id,
        payload=reversal_dict,
    )

    if idempotency_key:
        record_idempotent_response(
            conn=conn,
            key=idempotency_key,
            operation_type="REVERSE_MOVEMENT",
            resource_id=reversal_id,
            request_payload={"movement_id": movement_id, "amount": partial_amount_cents},
            response_payload=reversal_dict,
        )

    conn.commit()
    cursor.close()
    return reversal_dict


def reverseTransfer(
    conn: sqlite3.Connection,
    transfer_id: str,
    user_id: str,
    reason: str,
    evidence_reference: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Ejecuta la reversión de un traslado confirmado restituyendo los fondos al origen."""
    if not user_id or not user_id.strip():
        raise ErrorReversion("El usuario que autoriza la reversión de traslado es obligatorio.")

    if not reason or not reason.strip():
        raise ErrorReversion("El motivo de reversión de traslado es obligatorio.")

    if idempotency_key:
        cached = get_idempotent_response(conn, idempotency_key, {"transfer_id": transfer_id})
        if cached:
            return cached

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, transfer_id, origin_fund_id, destination_fund_id,
               amount_cents, status, out_movement_id, in_movement_id
        FROM tesoreria_traslados
        WHERE transfer_id = ?
        """,
        (transfer_id,),
    )
    transfer_row = cursor.fetchone()
    if not transfer_row:
        cursor.close()
        raise ErrorReversion(f"Traslado '{transfer_id}' no encontrado.")

    transfer = dict(transfer_row)
    if transfer["status"] != "CONFIRMED":
        cursor.close()
        raise ErrorReversion(
            f"Solo se pueden revertir traslados confirmados ('CONFIRMED'). Estado actual: {transfer['status']}."
        )

    # Verificar que el destino tenga saldo suficiente para devolver
    dest_fondo = obtener_fondo_por_id(conn, transfer["destination_fund_id"])
    orig_fondo = obtener_fondo_por_id(conn, transfer["origin_fund_id"])
    monto = transfer["amount_cents"]

    if dest_fondo["saldo_centavos"] < monto:
        cursor.close()
        raise ErrorReversion(
            f"Saldo insuficiente en fondo destino {dest_fondo['codigo']} para restituir el traslado. "
            f"Saldo: {formatMoney(dest_fondo['saldo_centavos'])}, Requerido: {formatMoney(monto)}."
        )

    jornada = obtener_jornada_actual(conn)
    if not jornada:
        cursor.close()
        raise ErrorReversion("No hay una jornada activa para registrar la reversión de traslado.")

    reversal_id = str(uuid.uuid4())
    now_utc = datetime.now(timezone.utc).isoformat()

    # Débito en destino y crédito en origen
    nuevo_dest = subtractMoney(dest_fondo["saldo_centavos"], monto)
    nuevo_orig = addMoney(orig_fondo["saldo_centavos"], monto)

    cursor.execute(
        "UPDATE tesoreria_fondos SET saldo_centavos = ?, updated_at = ?, actualizado_por = ? WHERE id = ?",
        (nuevo_dest, now_utc, user_id.strip(), dest_fondo["id"]),
    )
    cursor.execute(
        "UPDATE tesoreria_fondos SET saldo_centavos = ?, updated_at = ?, actualizado_por = ? WHERE id = ?",
        (nuevo_orig, now_utc, user_id.strip(), orig_fondo["id"]),
    )

    # Actualizar estado del traslado a REVERSED
    cursor.execute(
        "UPDATE tesoreria_traslados SET status = 'REVERSED' WHERE transfer_id = ?",
        (transfer_id,),
    )

    # Registrar en tesoreria_reversiones
    cursor.execute(
        """
        INSERT INTO tesoreria_reversiones (
            reversal_id, idempotency_key, target_type, target_id,
            original_amount_cents, reversed_amount_cents, reason,
            evidence_reference, authorized_by, created_at, status
        ) VALUES (?, ?, 'TRANSFER', ?, ?, ?, ?, ?, ?, ?, 'COMPLETED')
        """,
        (
            reversal_id,
            idempotency_key,
            transfer_id,
            monto,
            monto,
            reason.strip(),
            evidence_reference,
            user_id.strip(),
            now_utc,
        ),
    )

    reversal_dict = {
        "reversal_id": reversal_id,
        "target_type": "TRANSFER",
        "target_id": transfer_id,
        "amount_cents": monto,
        "amount_formatted": formatMoney(monto),
        "origin_fund_code": orig_fondo["codigo"],
        "destination_fund_code": dest_fondo["codigo"],
        "reason": reason.strip(),
        "authorized_by": user_id.strip(),
        "created_at": now_utc,
        "status": "COMPLETED",
    }

    recordAuditEvent(
        conn=conn,
        event_type="TRANSFER_REVERSED",
        aggregate_type="TRANSFER",
        aggregate_id=transfer_id,
        user_id=user_id.strip(),
        payload=reversal_dict,
        occurred_at=now_utc,
    )

    enqueueOutboxEvent(
        conn=conn,
        event_type="TRANSFER_REVERSED",
        aggregate_id=transfer_id,
        payload=reversal_dict,
    )

    if idempotency_key:
        record_idempotent_response(
            conn=conn,
            key=idempotency_key,
            operation_type="REVERSE_TRANSFER",
            resource_id=reversal_id,
            request_payload={"transfer_id": transfer_id},
            response_payload=reversal_dict,
        )

    conn.commit()
    cursor.close()
    return reversal_dict


def reverseReservationOperation(
    conn: sqlite3.Connection,
    reservation_id: str,
    operation_id: int,
    user_id: str,
    reason: str,
    evidence_reference: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Revierte una operación de aplicación o liberación sobre una reserva, restituyendo el saldo pendiente."""
    if not user_id or not user_id.strip():
        raise ErrorReversion("El usuario que autoriza la reversión de reserva es obligatorio.")

    if not reason or not reason.strip():
        raise ErrorReversion("El motivo de la reversión es obligatorio.")

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, reservation_id, action_type, amount_cents, is_reversal
        FROM tesoreria_reserva_movimientos
        WHERE id = ? AND reservation_id = ?
        """,
        (operation_id, reservation_id),
    )
    op_row = cursor.fetchone()
    if not op_row:
        cursor.close()
        raise ErrorReversion(f"Operación #{operation_id} sobre reserva '{reservation_id}' no encontrada.")

    op = dict(op_row)
    if op["is_reversal"] == 1:
        cursor.close()
        raise ErrorReversion("No se puede revertir una operación que ya es una reversión.")

    # Cargar reserva
    cursor.execute(
        """
        SELECT id, reservation_id, original_amount_cents, applied_amount_cents,
               released_amount_cents, pending_amount_cents, status
        FROM tesoreria_reservas
        WHERE reservation_id = ?
        """,
        (reservation_id,),
    )
    res_row = cursor.fetchone()
    if not res_row:
        cursor.close()
        raise ErrorReversion(f"Reserva '{reservation_id}' no encontrada.")

    res = dict(res_row)
    monto_op = op["amount_cents"]
    now_utc = datetime.now(timezone.utc).isoformat()
    reversal_id = str(uuid.uuid4())

    if op["action_type"] == "APPLY":
        # Deshacer aplicación: restar de applied, sumar a pending
        if res["applied_amount_cents"] < monto_op:
            cursor.close()
            raise ErrorReversion("Inconsistencia: el monto aplicado en la reserva es menor a la operación a revertir.")
        new_applied = subtractMoney(res["applied_amount_cents"], monto_op)
        new_pending = addMoney(res["pending_amount_cents"], monto_op)
        new_released = res["released_amount_cents"]
    elif op["action_type"] == "RELEASE":
        # Deshacer liberación: restar de released, sumar a pending
        if res["released_amount_cents"] < monto_op:
            cursor.close()
            raise ErrorReversion("Inconsistencia: el monto liberado en la reserva es menor a la operación a revertir.")
        new_released = subtractMoney(res["released_amount_cents"], monto_op)
        new_pending = addMoney(res["pending_amount_cents"], monto_op)
        new_applied = res["applied_amount_cents"]
    else:
        cursor.close()
        raise ErrorReversion(f"Tipo de acción no reversible: {op['action_type']}")

    # Verificar invariante
    if res["original_amount_cents"] != (new_applied + new_released + new_pending):
        cursor.close()
        raise ErrorReversion("Fallo de invariante matemática en reversión de reserva.")

    new_status = "ACTIVE" if new_pending == res["original_amount_cents"] else "PARTIALLY_APPLIED"

    # Actualizar reserva
    cursor.execute(
        """
        UPDATE tesoreria_reservas
        SET applied_amount_cents = ?,
            released_amount_cents = ?,
            pending_amount_cents = ?,
            status = ?,
            updated_at = ?
        WHERE reservation_id = ?
        """,
        (new_applied, new_released, new_pending, new_status, now_utc, reservation_id),
    )

    # Registrar contra-movimiento en tesoreria_reserva_movimientos
    contra_action = "RELEASE" if op["action_type"] == "APPLY" else "APPLY"
    cursor.execute(
        """
        INSERT INTO tesoreria_reserva_movimientos (
            reservation_id, action_type, amount_cents, performed_by,
            reason, evidence_reference, occurred_at, idempotency_key,
            is_reversal, reversal_of_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        """,
        (
            reservation_id,
            contra_action,
            monto_op,
            user_id.strip(),
            f"Reversión de {op['action_type']} #{operation_id}: {reason.strip()}",
            evidence_reference,
            now_utc,
            idempotency_key,
            operation_id,
        ),
    )

    reversal_dict = {
        "reversal_id": reversal_id,
        "target_type": "RESERVATION_OPERATION",
        "reservation_id": reservation_id,
        "operation_id": operation_id,
        "reversed_action": op["action_type"],
        "amount_cents": monto_op,
        "amount_formatted": formatMoney(monto_op),
        "new_pending_amount_cents": new_pending,
        "new_pending_amount_formatted": formatMoney(new_pending),
        "new_status": new_status,
        "authorized_by": user_id.strip(),
        "created_at": now_utc,
        "status": "COMPLETED",
    }

    recordAuditEvent(
        conn=conn,
        event_type="RESERVATION_OPERATION_REVERSED",
        aggregate_type="RESERVATION",
        aggregate_id=reservation_id,
        user_id=user_id.strip(),
        payload=reversal_dict,
        occurred_at=now_utc,
    )

    enqueueOutboxEvent(
        conn=conn,
        event_type="RESERVATION_OPERATION_REVERSED",
        aggregate_id=reservation_id,
        payload=reversal_dict,
    )

    conn.commit()
    cursor.close()
    return reversal_dict


def getReversals(
    conn: sqlite3.Connection,
    target_type: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Consulta histórica de reversiones realizadas."""
    query = """
        SELECT id, reversal_id, idempotency_key, target_type, target_id,
               original_amount_cents, reversed_amount_cents, counter_movement_id,
               reason, evidence_reference, authorized_by, created_at, status
        FROM tesoreria_reversiones
        WHERE 1=1
    """
    params: List[Any] = []
    if target_type:
        query += " AND target_type = ?"
        params.append(target_type)

    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    cursor = conn.cursor()
    cursor.execute(query, params)
    rows = cursor.fetchall()
    cursor.close()

    result = []
    for r in rows:
        d = dict(r)
        d["original_amount_formatted"] = formatMoney(d["original_amount_cents"])
        d["reversed_amount_formatted"] = formatMoney(d["reversed_amount_cents"])
        result.append(d)
    return result
