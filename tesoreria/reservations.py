"""Gestión de Reservas de Fondos — GENARO TESORERÍA LABORATORIO.

Implementación de LAB-007:
- createReservation (Crea afectación preventiva sin mover efectivo ni generar ingresos/gastos)
- applyReservation (Aplica parcial o totalmente la reserva contra una obligación)
- releaseReservation (Libera parcial o totalmente los fondos reservados hacia disponible)
- getReservationHistory (Historial completo de aplicaciones y liberaciones)

INVARIANTE INNEGOCIABLE:
original_amount_cents = applied_amount_cents + released_amount_cents + pending_amount_cents
"""

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Dict, List, Optional
import uuid

from tesoreria.catalog import obtener_fondo_por_codigo
from tesoreria.idempotency import (
    ErrorIdempotencia,
    calcular_hash_solicitud,
    registrar_idempotencia,
    verificar_idempotencia,
)
from tesoreria.jornadas import obtener_jornada_actual
from tesoreria.money import addMoney, assertValidMoney, formatMoney, subtractMoney
from tesoreria.movements import _obtener_saldo_reservado_fondo, _registrar_auditoria


class ErrorReserva(Exception):
    """Excepción para violaciones de reglas de negocio en reservas."""
    pass


def createReservation(
    conn: sqlite3.Connection,
    fund_code: str,
    amount_cents: int,
    reason: str,
    beneficiary: str,
    created_by: str,
    obligation_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    evidence_reference: Optional[str] = None,
    auto_commit: bool = True,
) -> Dict[str, Any]:
    """Crea una reserva preventiva de fondos sobre una cuenta habilitada."""
    assertValidMoney(amount_cents)
    if amount_cents <= 0:
        raise ErrorReserva(f"El importe de la reserva debe ser estrictamente positivo (> 0), recibido: {amount_cents}")

    if not reason or not reason.strip():
        raise ErrorReserva("El motivo de la reserva es obligatorio.")

    if not beneficiary or not beneficiary.strip():
        raise ErrorReserva("El beneficiario o destino previsto es obligatorio.")

    if not created_by or not created_by.strip():
        raise ErrorReserva("El usuario creador de la reserva es requerido.")

    payload_hash = calcular_hash_solicitud({
        "codigo_fondo": fund_code,
        "monto_centavos": amount_cents,
        "motivo": reason.strip(),
        "beneficiario": beneficiary.strip(),
        "obligation_id": obligation_id.strip() if obligation_id else None,
    })
    previo = verificar_idempotencia(conn, idempotency_key, "RESERVATION_CREATE", payload_hash)
    if previo is not None:
        return previo

    jornada = obtener_jornada_actual(conn)
    if not jornada or jornada["estado"] != "ABIERTA":
        raise ErrorReserva("Se requiere una jornada de tesorería ABIERTA para crear reservas.")

    fondo = obtener_fondo_por_codigo(conn, fund_code)
    if not fondo or fondo["estado"] != "ACTIVO":
        raise ErrorReserva(f"El fondo {fund_code} no está activo para registrar reservas.")

    cursor = conn.cursor()
    # Protección anti-doble reserva para la misma obligación
    if obligation_id and obligation_id.strip():
        cursor.execute(
            """
            SELECT reservation_id, pending_amount_cents
            FROM tesoreria_reservas
            WHERE obligation_id = ? AND status IN ('ACTIVE', 'PARTIALLY_APPLIED')
            """,
            (obligation_id.strip(),),
        )
        duplicada = cursor.fetchone()
        if duplicada:
            cursor.close()
            raise ErrorReserva(
                f"Doble reserva rechazada: Ya existe la reserva #{duplicada[0]} activa para la obligación '{obligation_id}'."
            )

    # Validar que no genere saldos negativos (saldo disponible >= monto a reservar)
    saldo_registrado = fondo["saldo_centavos"]
    saldo_reservado_previo = _obtener_saldo_reservado_fondo(conn, fondo["id"])
    disponible_actual = subtractMoney(saldo_registrado, saldo_reservado_previo)

    if disponible_actual < amount_cents:
        cursor.close()
        raise ErrorReserva(
            f"Saldo disponible insuficiente en {fund_code} para reservar {formatMoney(amount_cents)}. "
            f"Disponible actual: {formatMoney(disponible_actual)} (Registrado: {formatMoney(saldo_registrado)} - Reservado previo: {formatMoney(saldo_reservado_previo)})."
        )

    ahora_utc = datetime.now(timezone.utc).isoformat()
    reservation_id = str(uuid.uuid4())

    try:
        # La reserva NO mueve efectivo, no altera saldo_centavos de tesoreria_fondos!
        cursor.execute(
            """
            INSERT INTO tesoreria_reservas (
                reservation_id, idempotency_key, journal_id, fund_id,
                original_amount_cents, applied_amount_cents, released_amount_cents, pending_amount_cents,
                reason, beneficiary, obligation_id, evidence_reference, created_by,
                created_at, updated_at, status
            ) VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE')
            """,
            (
                reservation_id,
                idempotency_key,
                jornada["id"],
                fondo["id"],
                amount_cents,
                amount_cents,
                reason.strip(),
                beneficiary.strip(),
                obligation_id.strip() if obligation_id else None,
                evidence_reference,
                created_by.strip(),
                ahora_utc,
                ahora_utc,
            ),
        )

        _registrar_auditoria(
            conn,
            entity_type="RESERVA",
            entity_id=reservation_id,
            action="CREATE",
            user_id=created_by.strip(),
            details={
                "reservation_id": reservation_id,
                "fund_code": fund_code,
                "amount_cents": amount_cents,
                "obligation_id": obligation_id,
                "reason": reason.strip(),
            },
            timestamp_utc=ahora_utc,
        )

        resultado = {
            "reservation_id": reservation_id,
            "idempotency_key": idempotency_key,
            "journal_id": jornada["id"],
            "fund_code": fund_code,
            "fund_name": fondo["nombre"],
            "original_amount_cents": amount_cents,
            "applied_amount_cents": 0,
            "released_amount_cents": 0,
            "pending_amount_cents": amount_cents,
            "amount_formatted": formatMoney(amount_cents),
            "reason": reason.strip(),
            "beneficiary": beneficiary.strip(),
            "obligation_id": obligation_id.strip() if obligation_id else None,
            "created_by": created_by.strip(),
            "created_at": ahora_utc,
            "status": "ACTIVE",
        }

        registrar_idempotencia(
            conn,
            idempotency_key=idempotency_key,
            operation_type="RESERVATION_CREATE",
            resource_id=reservation_id,
            payload_hash=payload_hash,
            response_data=resultado,
            status="SUCCESS",
            created_at_iso=ahora_utc,
        )

        if auto_commit:
            conn.commit()
        return resultado

    except Exception:
        if auto_commit:
            conn.rollback()
        raise
    finally:
        cursor.close()


def applyReservation(
    conn: sqlite3.Connection,
    reservation_id: str,
    amount_cents: int,
    performed_by: str,
    reason: str,
    idempotency_key: Optional[str] = None,
    evidence_reference: Optional[str] = None,
    auto_commit: bool = True,
) -> Dict[str, Any]:
    """Aplica parcial o totalmente una reserva de fondos contra una obligación confirmada."""
    assertValidMoney(amount_cents)
    if amount_cents <= 0:
        raise ErrorReserva(f"El importe a aplicar debe ser estrictamente positivo (> 0), recibido: {amount_cents}")

    if not reason or not reason.strip():
        raise ErrorReserva("El motivo de aplicación es obligatorio.")

    if not performed_by or not performed_by.strip():
        raise ErrorReserva("El usuario que ejecuta la aplicación es requerido.")

    payload_hash = calcular_hash_solicitud({
        "reservation_id": reservation_id,
        "amount_cents": amount_cents,
        "performed_by": performed_by.strip(),
        "reason": reason.strip(),
    })
    previo = verificar_idempotencia(conn, idempotency_key, "RESERVATION_APPLY", payload_hash)
    if previo is not None:
        return previo

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT r.id, r.reservation_id, r.original_amount_cents, r.applied_amount_cents,
               r.released_amount_cents, r.pending_amount_cents, r.status, f.codigo as fund_code
        FROM tesoreria_reservas r
        JOIN tesoreria_fondos f ON r.fund_id = f.id
        WHERE r.reservation_id = ?
        """,
        (reservation_id,),
    )
    fila = cursor.fetchone()
    if not fila:
        cursor.close()
        raise ErrorReserva(f"Reserva #{reservation_id} no encontrada.")

    d = dict(fila)
    if d["status"] not in ("ACTIVE", "PARTIALLY_APPLIED"):
        cursor.close()
        raise ErrorReserva(f"No se puede aplicar sobre la reserva #{reservation_id} en estado '{d['status']}'.")

    if amount_cents > d["pending_amount_cents"]:
        cursor.close()
        raise ErrorReserva(
            f"El importe a aplicar ({formatMoney(amount_cents)}) excede el saldo pendiente reservado ({formatMoney(d['pending_amount_cents'])})."
        )

    ahora_utc = datetime.now(timezone.utc).isoformat()
    try:
        nuevo_applied = addMoney(d["applied_amount_cents"], amount_cents)
        nuevo_pending = subtractMoney(d["pending_amount_cents"], amount_cents)

        # Invariante: original = applied + released + pending
        invariante_check = d["original_amount_cents"] == (nuevo_applied + d["released_amount_cents"] + nuevo_pending)
        if not invariante_check:
            raise ErrorReserva("Violación del invariante de preservación de reserva.")

        nuevo_estado = "FULLY_APPLIED" if nuevo_pending == 0 else "PARTIALLY_APPLIED"

        cursor.execute(
            """
            UPDATE tesoreria_reservas
            SET applied_amount_cents = ?,
                pending_amount_cents = ?,
                status = ?,
                updated_at = ?
            WHERE reservation_id = ?
            """,
            (nuevo_applied, nuevo_pending, nuevo_estado, ahora_utc, reservation_id),
        )

        # Registrar en tesoreria_reserva_movimientos
        cursor.execute(
            """
            INSERT INTO tesoreria_reserva_movimientos (
                reservation_id, action_type, amount_cents, performed_by, reason,
                evidence_reference, occurred_at, idempotency_key
            ) VALUES (?, 'APPLY', ?, ?, ?, ?, ?, ?)
            """,
            (
                reservation_id,
                amount_cents,
                performed_by.strip(),
                reason.strip(),
                evidence_reference,
                ahora_utc,
                idempotency_key,
            ),
        )

        _registrar_auditoria(
            conn,
            entity_type="RESERVA",
            entity_id=reservation_id,
            action="APPLY",
            user_id=performed_by.strip(),
            details={
                "reservation_id": reservation_id,
                "applied_amount_cents": amount_cents,
                "nuevo_pending": nuevo_pending,
                "status": nuevo_estado,
                "reason": reason.strip(),
            },
            timestamp_utc=ahora_utc,
        )

        resultado = {
            "reservation_id": reservation_id,
            "action": "APPLY",
            "amount_cents": amount_cents,
            "amount_formatted": formatMoney(amount_cents),
            "applied_amount_cents": nuevo_applied,
            "released_amount_cents": d["released_amount_cents"],
            "pending_amount_cents": nuevo_pending,
            "status": nuevo_estado,
            "updated_at": ahora_utc,
        }

        registrar_idempotencia(
            conn,
            idempotency_key=idempotency_key,
            operation_type="RESERVATION_APPLY",
            resource_id=reservation_id,
            payload_hash=payload_hash,
            response_data=resultado,
            status="SUCCESS",
            created_at_iso=ahora_utc,
        )

        if auto_commit:
            conn.commit()
        return resultado

    except Exception:
        if auto_commit:
            conn.rollback()
        raise
    finally:
        cursor.close()


def releaseReservation(
    conn: sqlite3.Connection,
    reservation_id: str,
    amount_cents: int,
    performed_by: str,
    reason: str,
    idempotency_key: Optional[str] = None,
    evidence_reference: Optional[str] = None,
    auto_commit: bool = True,
) -> Dict[str, Any]:
    """Libera parcial o totalmente los fondos de una reserva, retornándolos a saldo disponible."""
    assertValidMoney(amount_cents)
    if amount_cents <= 0:
        raise ErrorReserva(f"El importe a liberar debe ser estrictamente positivo (> 0), recibido: {amount_cents}")

    if not reason or not reason.strip():
        raise ErrorReserva("El motivo de liberación es obligatorio.")

    if not performed_by or not performed_by.strip():
        raise ErrorReserva("El usuario que ejecuta la liberación es requerido.")

    payload_hash = calcular_hash_solicitud({
        "reservation_id": reservation_id,
        "amount_cents": amount_cents,
        "performed_by": performed_by.strip(),
        "reason": reason.strip(),
    })
    previo = verificar_idempotencia(conn, idempotency_key, "RESERVATION_RELEASE", payload_hash)
    if previo is not None:
        return previo

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT r.id, r.reservation_id, r.original_amount_cents, r.applied_amount_cents,
               r.released_amount_cents, r.pending_amount_cents, r.status, f.codigo as fund_code
        FROM tesoreria_reservas r
        JOIN tesoreria_fondos f ON r.fund_id = f.id
        WHERE r.reservation_id = ?
        """,
        (reservation_id,),
    )
    fila = cursor.fetchone()
    if not fila:
        cursor.close()
        raise ErrorReserva(f"Reserva #{reservation_id} no encontrada.")

    d = dict(fila)
    if d["status"] not in ("ACTIVE", "PARTIALLY_APPLIED"):
        cursor.close()
        raise ErrorReserva(f"No se puede liberar sobre la reserva #{reservation_id} en estado '{d['status']}'.")

    if amount_cents > d["pending_amount_cents"]:
        cursor.close()
        raise ErrorReserva(
            f"El importe a liberar ({formatMoney(amount_cents)}) excede el saldo pendiente ({formatMoney(d['pending_amount_cents'])})."
        )

    ahora_utc = datetime.now(timezone.utc).isoformat()
    try:
        nuevo_released = addMoney(d["released_amount_cents"], amount_cents)
        nuevo_pending = subtractMoney(d["pending_amount_cents"], amount_cents)

        # Invariante: original = applied + released + pending
        invariante_check = d["original_amount_cents"] == (d["applied_amount_cents"] + nuevo_released + nuevo_pending)
        if not invariante_check:
            raise ErrorReserva("Violación del invariante de preservación de reserva.")

        if nuevo_pending == 0:
            nuevo_estado = "RELEASED" if d["applied_amount_cents"] == 0 else "PARTIALLY_APPLIED"
        else:
            nuevo_estado = "PARTIALLY_APPLIED"

        cursor.execute(
            """
            UPDATE tesoreria_reservas
            SET released_amount_cents = ?,
                pending_amount_cents = ?,
                status = ?,
                updated_at = ?
            WHERE reservation_id = ?
            """,
            (nuevo_released, nuevo_pending, nuevo_estado, ahora_utc, reservation_id),
        )

        cursor.execute(
            """
            INSERT INTO tesoreria_reserva_movimientos (
                reservation_id, action_type, amount_cents, performed_by, reason,
                evidence_reference, occurred_at, idempotency_key
            ) VALUES (?, 'RELEASE', ?, ?, ?, ?, ?, ?)
            """,
            (
                reservation_id,
                amount_cents,
                performed_by.strip(),
                reason.strip(),
                evidence_reference,
                ahora_utc,
                idempotency_key,
            ),
        )

        _registrar_auditoria(
            conn,
            entity_type="RESERVA",
            entity_id=reservation_id,
            action="RELEASE",
            user_id=performed_by.strip(),
            details={
                "reservation_id": reservation_id,
                "released_amount_cents": amount_cents,
                "nuevo_pending": nuevo_pending,
                "status": nuevo_estado,
                "reason": reason.strip(),
            },
            timestamp_utc=ahora_utc,
        )

        resultado = {
            "reservation_id": reservation_id,
            "action": "RELEASE",
            "amount_cents": amount_cents,
            "amount_formatted": formatMoney(amount_cents),
            "applied_amount_cents": d["applied_amount_cents"],
            "released_amount_cents": nuevo_released,
            "pending_amount_cents": nuevo_pending,
            "status": nuevo_estado,
            "updated_at": ahora_utc,
        }

        registrar_idempotencia(
            conn,
            idempotency_key=idempotency_key,
            operation_type="RESERVATION_RELEASE",
            resource_id=reservation_id,
            payload_hash=payload_hash,
            response_data=resultado,
            status="SUCCESS",
            created_at_iso=ahora_utc,
        )

        if auto_commit:
            conn.commit()
        return resultado

    except Exception:
        if auto_commit:
            conn.rollback()
        raise
    finally:
        cursor.close()


def cancelReservation(
    conn: sqlite3.Connection,
    reservation_id: str,
    performed_by: str,
    reason: str,
    idempotency_key: Optional[str] = None,
    evidence_reference: Optional[str] = None,
    auto_commit: bool = True,
) -> Dict[str, Any]:
    """Cancela y libera la totalidad del importe pendiente de una reserva activa."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT pending_amount_cents, status FROM tesoreria_reservas WHERE reservation_id = ?",
        (reservation_id,),
    )
    row = cursor.fetchone()
    cursor.close()
    if not row:
        raise ErrorReserva(f"Reserva '{reservation_id}' no encontrada.")
    pending_amount, status_res = row[0], row[1]
    if status_res not in ("ACTIVE", "PARTIALLY_APPLIED"):
        raise ErrorReserva(f"No se puede cancelar una reserva en estado '{status_res}'.")
    return releaseReservation(
        conn=conn,
        reservation_id=reservation_id,
        amount_cents=pending_amount,
        performed_by=performed_by,
        reason=reason,
        idempotency_key=idempotency_key,
        evidence_reference=evidence_reference,
        auto_commit=auto_commit,
    )


def getReservationHistory(conn: sqlite3.Connection, reservation_id: str) -> Dict[str, Any]:
    """Retorna la cabecera de la reserva y todo su historial de aplicaciones y liberaciones."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT r.id, r.reservation_id, r.journal_id, r.fund_id,
               f.codigo as fund_code, f.nombre as fund_name,
               r.original_amount_cents, r.applied_amount_cents, r.released_amount_cents, r.pending_amount_cents,
               r.reason, r.beneficiary, r.obligation_id, r.evidence_reference,
               r.created_by, r.created_at, r.updated_at, r.status
        FROM tesoreria_reservas r
        JOIN tesoreria_fondos f ON r.fund_id = f.id
        WHERE r.reservation_id = ?
        """,
        (reservation_id,),
    )
    fila = cursor.fetchone()
    if not fila:
        cursor.close()
        raise ErrorReserva(f"Reserva #{reservation_id} no existe.")

    reserva = dict(fila)
    reserva["original_amount_formatted"] = formatMoney(reserva["original_amount_cents"])
    reserva["applied_amount_formatted"] = formatMoney(reserva["applied_amount_cents"])
    reserva["released_amount_formatted"] = formatMoney(reserva["released_amount_cents"])
    reserva["pending_amount_formatted"] = formatMoney(reserva["pending_amount_cents"])

    cursor.execute(
        """
        SELECT id, reservation_id, action_type, amount_cents, performed_by,
               reason, evidence_reference, occurred_at, idempotency_key
        FROM tesoreria_reserva_movimientos
        WHERE reservation_id = ?
        ORDER BY id ASC
        """,
        (reservation_id,),
    )
    movs = cursor.fetchall()
    cursor.close()

    reserva["movimientos"] = [
        {**dict(m), "amount_formatted": formatMoney(m["amount_cents"])} for m in movs
    ]
    return reserva


def listar_reservas(
    conn: sqlite3.Connection,
    codigo_fondo: Optional[str] = None,
    solo_activas: bool = False,
    limite: int = 50,
) -> List[Dict[str, Any]]:
    """Consulta la lista de reservas registradas."""
    cursor = conn.cursor()
    query = """
        SELECT r.id, r.reservation_id, r.journal_id, r.fund_id,
               f.codigo as fund_code, f.nombre as fund_name,
               r.original_amount_cents, r.applied_amount_cents, r.released_amount_cents, r.pending_amount_cents,
               r.reason, r.beneficiary, r.obligation_id, r.evidence_reference,
               r.created_by, r.created_at, r.updated_at, r.status
        FROM tesoreria_reservas r
        JOIN tesoreria_fondos f ON r.fund_id = f.id
        WHERE 1=1
    """
    params: List[Any] = []
    if codigo_fondo:
        query += " AND f.codigo = ?"
        params.append(codigo_fondo)
    if solo_activas:
        query += " AND r.status IN ('ACTIVE', 'PARTIALLY_APPLIED')"

    query += " ORDER BY r.id DESC LIMIT ?"
    params.append(limite)

    cursor.execute(query, tuple(params))
    filas = cursor.fetchall()
    cursor.close()

    resultado = []
    for r in filas:
        d = dict(r)
        d["original_amount_formatted"] = formatMoney(d["original_amount_cents"])
        d["applied_amount_formatted"] = formatMoney(d["applied_amount_cents"])
        d["released_amount_formatted"] = formatMoney(d["released_amount_cents"])
        d["pending_amount_formatted"] = formatMoney(d["pending_amount_cents"])
        resultado.append(d)
    return resultado


def executeReservedExit(
    conn: sqlite3.Connection,
    reservation_id: str,
    amount_cents: int,
    counterpart: str,
    reason: str,
    performed_by: str,
    idempotency_key: Optional[str] = None,
    evidence_reference: Optional[str] = None,
    auto_commit: bool = True,
) -> Dict[str, Any]:
    """FASE 0 — Requisito 1: Aplica la reserva y registra el egreso de fondos como UNA ÚNICA OPERACIÓN ATÓMICA.

    Invariantes:
    - Se descuenta de la reserva preventiva (transición de pending a applied).
    - Se descuenta el saldo real en libros del fondo (saldo_centavos -= amount_cents).
    - Se registra el movimiento de egreso (EXIT) en tesoreria_movimientos con contrapartida y motivo.
    - Se audita y encola en Outbox dentro de la misma transacción inmediata. Si algo falla, se revierte todo (ROLLBACK).
    """
    assertValidMoney(amount_cents)
    if amount_cents <= 0:
        raise ErrorReserva(f"El importe del egreso reservado debe ser estrictamente positivo (> 0), recibido: {amount_cents}")

    if not counterpart or not counterpart.strip():
        raise ErrorReserva("La contrapartida económica es obligatoria para el egreso.")

    if not reason or not reason.strip():
        raise ErrorReserva("El motivo del egreso es obligatorio.")

    if not performed_by or not performed_by.strip():
        raise ErrorReserva("El usuario autorizante es obligatorio.")

    payload_hash = calcular_hash_solicitud({
        "reservation_id": reservation_id,
        "amount_cents": amount_cents,
        "counterpart": counterpart.strip(),
        "reason": reason.strip(),
        "performed_by": performed_by.strip(),
    })
    previo = verificar_idempotencia(conn, idempotency_key, "RESERVED_EXIT", payload_hash)
    if previo is not None:
        return previo

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT r.id, r.reservation_id, r.journal_id, r.fund_id,
               r.original_amount_cents, r.applied_amount_cents, r.released_amount_cents,
               r.pending_amount_cents, r.status, f.codigo as fund_code, f.saldo_centavos
        FROM tesoreria_reservas r
        JOIN tesoreria_fondos f ON r.fund_id = f.id
        WHERE r.reservation_id = ?
        """,
        (reservation_id,),
    )
    fila = cursor.fetchone()
    if not fila:
        cursor.close()
        raise ErrorReserva(f"Reserva #{reservation_id} no encontrada.")

    res = dict(fila)
    if res["status"] not in ("ACTIVE", "PARTIALLY_APPLIED"):
        cursor.close()
        raise ErrorReserva(f"No se puede ejecutar egreso sobre la reserva #{reservation_id} en estado '{res['status']}'.")

    if amount_cents > res["pending_amount_cents"]:
        cursor.close()
        raise ErrorReserva(
            f"El importe a egresar ({formatMoney(amount_cents)}) excede el saldo pendiente reservado ({formatMoney(res['pending_amount_cents'])})."
        )

    if amount_cents > res["saldo_centavos"]:
        cursor.close()
        raise ErrorReserva(
            f"Saldo registrado insuficiente en fondo {res['fund_code']}: saldo {formatMoney(res['saldo_centavos'])}, requerido {formatMoney(amount_cents)}."
        )

    # Validar jornada abierta
    cursor.execute(
        "SELECT id, estado FROM tesoreria_jornadas WHERE id = ?",
        (res["journal_id"],),
    )
    jornada_row = cursor.fetchone()
    if not jornada_row or jornada_row["estado"] != "ABIERTA":
        cursor.close()
        raise ErrorReserva("La jornada asociada a la reserva no está en estado 'ABIERTA'.")

    ahora_utc = datetime.now(timezone.utc).isoformat()
    movement_id = str(uuid.uuid4())

    try:
        nuevo_applied = addMoney(res["applied_amount_cents"], amount_cents)
        nuevo_pending = subtractMoney(res["pending_amount_cents"], amount_cents)
        nuevo_estado_res = "FULLY_APPLIED" if nuevo_pending == 0 else "PARTIALLY_APPLIED"
        nuevo_saldo_fondo = subtractMoney(res["saldo_centavos"], amount_cents)

        # 1. Actualizar reserva
        cursor.execute(
            """
            UPDATE tesoreria_reservas
            SET applied_amount_cents = ?,
                pending_amount_cents = ?,
                status = ?,
                updated_at = ?
            WHERE reservation_id = ?
            """,
            (nuevo_applied, nuevo_pending, nuevo_estado_res, ahora_utc, reservation_id),
        )

        # 2. Registrar aplicación en historial de reserva
        cursor.execute(
            """
            INSERT INTO tesoreria_reserva_movimientos (
                reservation_id, action_type, amount_cents, performed_by,
                reason, evidence_reference, occurred_at, idempotency_key
            ) VALUES (?, 'APPLY', ?, ?, ?, ?, ?, ?)
            """,
            (
                reservation_id,
                amount_cents,
                performed_by.strip(),
                reason.strip(),
                evidence_reference,
                ahora_utc,
                f"op_{movement_id}",
            ),
        )

        # 3. Registrar movimiento de salida real en tesoreria_movimientos
        cursor.execute(
            """
            INSERT INTO tesoreria_movimientos (
                movement_id, idempotency_key, journal_id, fund_id,
                movement_type, amount_cents, currency, counterpart,
                reason, evidence_reference, created_by, terminal_id,
                status, occurred_at, recorded_at
            ) VALUES (?, ?, ?, ?, 'EXIT', ?, 'ARS', ?, ?, ?, ?, 'TERM-01', 'POSTED', ?, ?)
            """,
            (
                movement_id,
                idempotency_key,
                res["journal_id"],
                res["fund_id"],
                amount_cents,
                counterpart.strip(),
                f"[RESERVA {reservation_id[:8]}] {reason.strip()}",
                evidence_reference,
                performed_by.strip(),
                ahora_utc,
                ahora_utc,
            ),
        )

        # 4. Descontar saldo real en tesoreria_fondos
        cursor.execute(
            """
            UPDATE tesoreria_fondos
            SET saldo_centavos = ?,
                updated_at = ?,
                actualizado_por = ?,
                version = version + 1
            WHERE id = ?
            """,
            (nuevo_saldo_fondo, ahora_utc, performed_by.strip(), res["fund_id"]),
        )

        _registrar_auditoria(
            conn,
            entity_type="RESERVA",
            entity_id=reservation_id,
            action="APPLY_AND_EXIT",
            user_id=performed_by.strip(),
            details={
                "reservation_id": reservation_id,
                "movement_id": movement_id,
                "amount_cents": amount_cents,
                "counterpart": counterpart.strip(),
                "nuevo_saldo_fondo": nuevo_saldo_fondo,
            },
            timestamp_utc=ahora_utc,
        )

        resultado = {
            "success": True,
            "reservation_id": reservation_id,
            "movement_id": movement_id,
            "amount_cents": amount_cents,
            "amount_formatted": formatMoney(amount_cents),
            "fund_code": res["fund_code"],
            "new_fund_balance_cents": nuevo_saldo_fondo,
            "new_fund_balance_formatted": formatMoney(nuevo_saldo_fondo),
            "new_reservation_pending_cents": nuevo_pending,
            "new_reservation_pending_formatted": formatMoney(nuevo_pending),
            "reservation_status": nuevo_estado_res,
            "executed_at": ahora_utc,
        }

        if idempotency_key:
            registrar_idempotencia(
                conn,
                idempotency_key=idempotency_key,
                operation_type="RESERVED_EXIT",
                resource_id=reservation_id,
                payload_hash=payload_hash,
                response_data=resultado,
                status="SUCCESS",
                created_at_iso=ahora_utc,
            )

        if auto_commit:
            conn.commit()
        return resultado
    except Exception:
        if auto_commit:
            conn.rollback()
        raise
    finally:
        cursor.close()
