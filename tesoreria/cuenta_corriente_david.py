"""Módulo de Cuenta Corriente PREPAY / David — GENARO TESORERÍA.

Gobierna la posición financiera y deudor-acreedor con el proveedor de saldo PREPAY (David):
1. Acreditaciones efectivas de saldo: aumentan fondo 1220 y la obligación pendiente con David.
2. Pagos efectivos a David: salen exclusivamente del fondo físico 1130 acumulado, validando
   disponibilidad (cero sobregiro) y reduciendo la obligación pendiente.
3. Reconocimiento de deuda histórica: append-only, auditable, NO afecta fondos 1130 ni 1220,
   no crea movimientos comerciales.
4. Obligación pendiente: calculada dinámicamente desde el libro de movimientos:
   OBLIGACIÓN PENDIENTE = RECONOCIMIENTOS HISTÓRICOS + ACREDITACIONES - PAGOS.
   El saldo comienza en $0 y nunca se almacena como dato mutable.
"""

from datetime import datetime, timezone
import logging
import sqlite3
from typing import Any, Dict, List, Optional
import uuid

from tesoreria.availability import getFundBalance
from tesoreria.jornadas import obtener_jornada_actual
from tesoreria.money import addMoney, assertValidMoney, formatMoney, subtractMoney
from tesoreria.movements import _obtener_saldo_reservado_fondo, registerEntry, registerExit
from tesoreria.audit_chain import recordAuditEvent

logger = logging.getLogger("genaro_pos.cuenta_corriente_david")


class ErrorCuentaCorrienteDavid(Exception):
    """Excepción base para operaciones de Cuenta Corriente David."""
    pass


class ErrorSaldoInsuficienteDavid(ErrorCuentaCorrienteDavid):
    """Fondo físico 1130 no tiene saldo disponible suficiente para el pago a David."""
    pass


class ErrorValidacionDavid(ErrorCuentaCorrienteDavid):
    """Parámetros inválidos o violación de invariantes de negocio."""
    pass


class ErrorIdempotenciaDavid(ErrorCuentaCorrienteDavid):
    """Conflicto de clave o repetición no idempotente."""
    pass


def _obtener_saldo_disponible_1130(conn: sqlite3.Connection) -> int:
    """Retorna los centavos disponibles en el fondo físico 1130 descontando reservas."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, saldo_centavos, permite_sobregiro FROM tesoreria_fondos WHERE codigo = '1130' AND estado = 'ACTIVO';"
    )
    row = cursor.fetchone()
    cursor.close()
    if not row:
        raise ErrorValidacionDavid("Fondo activo 1130 (Prepay Efectivo) no encontrado.")
    fund_id, saldo_en_libros, permite_sobregiro = row
    if permite_sobregiro == 1:
        return 999_999_999_99
    reservado = _obtener_saldo_reservado_fondo(conn, fund_id)
    return saldo_en_libros - reservado


def registrar_acreditacion_david(
    conn: sqlite3.Connection,
    amount_cents: int,
    referencia: str,
    actor: str,
    idempotency_key: str,
    motivo: Optional[str] = None,
    fecha_referencia: Optional[str] = None,
    auto_commit: bool = True,
) -> Dict[str, Any]:
    """Registra una acreditación efectiva de saldo digital PREPAY provista por David.
    
    Efectos atómicos:
    - Incrementa el fondo digital 1220 vía registerEntry.
    - Incrementa la obligación pendiente con David en tesoreria_cuenta_corriente_david.
    - Vincula ambos registros mediante movement_id.
    - Garantiza idempotencia estricta.
    """
    assertValidMoney(amount_cents)
    if amount_cents <= 0:
        raise ErrorValidacionDavid(f"El importe de la acreditación debe ser estrictamente positivo (> 0): {amount_cents}")

    ref_limpia = (referencia or "").strip()
    if not ref_limpia:
        raise ErrorValidacionDavid("La referencia o comprobante de acreditación de David es obligatoria.")

    actor_limpio = (actor or "").strip()
    if not actor_limpio:
        raise ErrorValidacionDavid("La identidad del actor responsable es obligatoria.")

    key_limpia = (idempotency_key or "").strip()
    if not key_limpia:
        raise ErrorValidacionDavid("La clave de idempotencia es obligatoria para la acreditación.")

    cursor = conn.cursor()

    # 1. Verificar idempotencia en cuenta corriente
    cursor.execute(
        """
        SELECT id, operation_id, tipo_movimiento, monto_cents, movement_id, recorded_at_utc
        FROM tesoreria_cuenta_corriente_david
        WHERE idempotency_key = ?;
        """,
        (key_limpia,),
    )
    previo = cursor.fetchone()
    if previo:
        cursor.close()
        return {
            "status": "ALREADY_PROCESSED",
            "operation_id": previo[1],
            "tipo_movimiento": previo[2],
            "monto_cents": previo[3],
            "movement_id": previo[4],
            "recorded_at_utc": previo[5],
            "message": "Operación de acreditación ya procesada previamente con la misma clave de idempotencia.",
        }

    ahora_utc = datetime.now(timezone.utc).isoformat()
    fecha_ref = (fecha_referencia or "").strip() or ahora_utc[:10]
    motivo_final = (motivo or "").strip() or f"Acreditación efectiva de saldo digital PREPAY provista por David ref: {ref_limpia}"
    operation_id = f"OP-ACR-{uuid.uuid4().hex[:12]}"

    try:
        # 2. Registrar movimiento real de Tesorería en fondo 1220 (Prepay Digital Saldo)
        res_entry = registerEntry(
            conn=conn,
            fund_code="1220",
            amount_cents=amount_cents,
            counterpart="ACREDITACION_PREPAY_DAVID",
            reason=motivo_final,
            created_by=actor_limpio,
            terminal_id="TERM-POS-01",
            idempotency_key=f"mov_{key_limpia}",
            evidence_reference=ref_limpia,
            occurred_at=ahora_utc,
            auto_commit=False,
        )
        movement_id = res_entry["movement_id"]
        journal_id = res_entry.get("journal_id")

        # 3. Registrar en libro mayor de cuenta corriente David
        cursor.execute(
            """
            INSERT INTO tesoreria_cuenta_corriente_david (
                operation_id, idempotency_key, tipo_movimiento, monto_cents,
                fecha_referencia, recorded_at_utc, movement_id, fondo_afectado,
                referencia_externa, motivo, actor, jornada_id
            ) VALUES (?, ?, 'ACREDITACION_SALDO', ?, ?, ?, ?, '1220', ?, ?, ?, ?);
            """,
            (
                operation_id,
                key_limpia,
                amount_cents,
                fecha_ref,
                ahora_utc,
                movement_id,
                ref_limpia,
                motivo_final,
                actor_limpio,
                journal_id,
            ),
        )

        # 4. Auditoría de evento
        recordAuditEvent(
            conn=conn,
            event_type="DAVID_BALANCE_ACCREDITATION_POSTED",
            aggregate_type="CUENTA_CORRIENTE_DAVID",
            aggregate_id=operation_id,
            user_id=actor_limpio,
            payload={
                "operation_id": operation_id,
                "amount_cents": amount_cents,
                "movement_id": movement_id,
                "fund_code": "1220",
                "referencia": ref_limpia,
                "actor": actor_limpio,
            },
            occurred_at=ahora_utc,
        )

        if auto_commit:
            conn.commit()

        resultado = {
            "status": "SUCCESS",
            "operation_id": operation_id,
            "idempotency_key": key_limpia,
            "tipo_movimiento": "ACREDITACION_SALDO",
            "monto_cents": amount_cents,
            "monto_formatted": formatMoney(amount_cents),
            "fecha_referencia": fecha_ref,
            "recorded_at_utc": ahora_utc,
            "movement_id": movement_id,
            "fondo_afectado": "1220",
            "referencia_externa": ref_limpia,
            "motivo": motivo_final,
            "actor": actor_limpio,
            "jornada_id": journal_id,
        }
        return resultado

    except Exception as e:
        if auto_commit:
            conn.rollback()
        logger.error("Fallo atómico al registrar acreditación de David: %s", e)
        raise
    finally:
        cursor.close()


def registrar_pago_david(
    conn: sqlite3.Connection,
    amount_cents: int,
    referencia: str,
    actor: str,
    idempotency_key: str,
    motivo: Optional[str] = None,
    fecha_referencia: Optional[str] = None,
    auto_commit: bool = True,
) -> Dict[str, Any]:
    """Registra un pago efectivo a David cancelando parte o totalidad de la obligación pendiente.
    
    Efectos atómicos:
    - Valida disponibilidad en fondo físico 1130 (Prepay Efectivo).
    - Valida que el pago no supere la obligación pendiente actual con David.
    - Debita exclusivamente el fondo 1130 vía registerExit.
    - Reduce la obligación pendiente en tesoreria_cuenta_corriente_david.
    - Vincula ambos registros mediante movement_id.
    - Garantiza idempotencia estricta.
    """
    assertValidMoney(amount_cents)
    if amount_cents <= 0:
        raise ErrorValidacionDavid(f"El importe del pago debe ser estrictamente positivo (> 0): {amount_cents}")

    ref_limpia = (referencia or "").strip()
    actor_limpio = (actor or "").strip()
    if not actor_limpio:
        raise ErrorValidacionDavid("La identidad del actor responsable es obligatoria.")

    key_limpia = (idempotency_key or "").strip()
    if not key_limpia:
        raise ErrorValidacionDavid("La clave de idempotencia es obligatoria para el pago.")

    cursor = conn.cursor()

    # 1. Verificar idempotencia
    cursor.execute(
        """
        SELECT id, operation_id, tipo_movimiento, monto_cents, movement_id, recorded_at_utc
        FROM tesoreria_cuenta_corriente_david
        WHERE idempotency_key = ?;
        """,
        (key_limpia,),
    )
    previo = cursor.fetchone()
    if previo:
        cursor.close()
        return {
            "status": "ALREADY_PROCESSED",
            "operation_id": previo[1],
            "tipo_movimiento": previo[2],
            "monto_cents": previo[3],
            "movement_id": previo[4],
            "recorded_at_utc": previo[5],
            "message": "Operación de pago ya procesada previamente con la misma clave de idempotencia.",
        }

    # 2. Validar saldo disponible en fondo 1130 (Prepay Efectivo)
    disp_1130 = _obtener_saldo_disponible_1130(conn)
    if amount_cents > disp_1130:
        cursor.close()
        raise ErrorSaldoInsuficienteDavid(
            f"Saldo disponible insuficiente en fondo 1130 (Prepay Efectivo). "
            f"Disponible: {formatMoney(disp_1130)}, Requerido para el pago: {formatMoney(amount_cents)}."
        )

    # 3. Validar contra obligación pendiente actual
    estado_actual = obtener_estado_cuenta_david(conn)
    obligacion_actual = estado_actual["obligacion_pendiente_cents"]
    if amount_cents > obligacion_actual:
        cursor.close()
        raise ErrorValidacionDavid(
            f"El importe del pago ({formatMoney(amount_cents)}) no puede superar la obligación "
            f"pendiente acumulada con David ({formatMoney(obligacion_actual)})."
        )

    ahora_utc = datetime.now(timezone.utc).isoformat()
    fecha_ref = (fecha_referencia or "").strip() or ahora_utc[:10]
    motivo_final = (motivo or "").strip() or f"Pago efectivo a David ref: {ref_limpia}"
    operation_id = f"OP-PAG-{uuid.uuid4().hex[:12]}"

    try:
        # 4. Registrar salida real de Tesorería exclusivamente del fondo 1130
        res_exit = registerExit(
            conn=conn,
            fund_code="1130",
            amount_cents=amount_cents,
            counterpart="PAGO_OBLIGACION_PREPAY_DAVID",
            reason=motivo_final,
            created_by=actor_limpio,
            terminal_id="TERM-POS-01",
            idempotency_key=f"mov_{key_limpia}",
            evidence_reference=ref_limpia,
            occurred_at=ahora_utc,
            auto_commit=False,
        )
        movement_id = res_exit["movement_id"]
        journal_id = res_exit.get("journal_id")

        # 5. Registrar en cuenta corriente
        cursor.execute(
            """
            INSERT INTO tesoreria_cuenta_corriente_david (
                operation_id, idempotency_key, tipo_movimiento, monto_cents,
                fecha_referencia, recorded_at_utc, movement_id, fondo_afectado,
                referencia_externa, motivo, actor, jornada_id
            ) VALUES (?, ?, 'PAGO_EFECTIVO', ?, ?, ?, ?, '1130', ?, ?, ?, ?);
            """,
            (
                operation_id,
                key_limpia,
                amount_cents,
                fecha_ref,
                ahora_utc,
                movement_id,
                ref_limpia,
                motivo_final,
                actor_limpio,
                journal_id,
            ),
        )

        # 6. Registrar evento de auditoría inmutable
        recordAuditEvent(
            conn=conn,
            event_type="DAVID_PAYMENT_POSTED",
            aggregate_type="CUENTA_CORRIENTE_DAVID",
            aggregate_id=operation_id,
            user_id=actor_limpio,
            payload={
                "operation_id": operation_id,
                "amount_cents": amount_cents,
                "movement_id": movement_id,
                "fund_code": "1130",
                "referencia": ref_limpia,
                "actor": actor_limpio,
            },
            occurred_at=ahora_utc,
        )

        if auto_commit:
            conn.commit()

        resultado = {
            "status": "SUCCESS",
            "operation_id": operation_id,
            "idempotency_key": key_limpia,
            "tipo_movimiento": "PAGO_EFECTIVO",
            "monto_cents": amount_cents,
            "monto_formatted": formatMoney(amount_cents),
            "fecha_referencia": fecha_ref,
            "recorded_at_utc": ahora_utc,
            "movement_id": movement_id,
            "fondo_afectado": "1130",
            "referencia_externa": ref_limpia,
            "motivo": motivo_final,
            "actor": actor_limpio,
            "jornada_id": journal_id,
        }
        return resultado

    except Exception as e:
        if auto_commit:
            conn.rollback()
        logger.error("Fallo atómico al registrar pago a David: %s", e)
        raise
    finally:
        cursor.close()


def registrar_reconocimiento_deuda_historica(
    conn: sqlite3.Connection,
    amount_cents: int,
    fecha_referencia: str,
    motivo: str,
    actor: str,
    idempotency_key: str,
    confirmacion_explicita: bool,
    referencia_externa: Optional[str] = None,
    auto_commit: bool = True,
) -> Dict[str, Any]:
    """Registra el reconocimiento de una deuda histórica anterior con David.
    
    Invariantes estrictos:
    - Incrementa la obligación pendiente con David.
    - NO genera entrada en fondo 1220.
    - NO genera salida en fondo 1130.
    - NO genera ingresos comerciales ficticios.
    - Exige confirmación explícita (confirmacion_explicita=True).
    - Es inmutable y append-only (cero borrado o edición posterior).
    """
    assertValidMoney(amount_cents)
    if amount_cents <= 0:
        raise ErrorValidacionDavid(
            f"El importe de la deuda histórica debe ser estrictamente positivo (> 0): {amount_cents}"
        )

    if not confirmacion_explicita:
        raise ErrorValidacionDavid(
            "Se requiere confirmación explícita para registrar un reconocimiento de deuda histórica."
        )

    fecha_ref_limpia = (fecha_referencia or "").strip()
    if not fecha_ref_limpia:
        raise ErrorValidacionDavid("La fecha de referencia de la deuda histórica es obligatoria (YYYY-MM-DD).")

    motivo_limpio = (motivo or "").strip()
    if not motivo_limpio:
        raise ErrorValidacionDavid("El motivo u observación del reconocimiento histórico es obligatorio.")

    actor_limpio = (actor or "").strip()
    if not actor_limpio:
        raise ErrorValidacionDavid("La identidad del actor responsable es obligatoria.")

    key_limpia = (idempotency_key or "").strip()
    if not key_limpia:
        raise ErrorValidacionDavid("La clave de idempotencia es obligatoria para el reconocimiento histórico.")

    cursor = conn.cursor()

    # 1. Idempotencia
    cursor.execute(
        """
        SELECT id, operation_id, tipo_movimiento, monto_cents, recorded_at_utc
        FROM tesoreria_cuenta_corriente_david
        WHERE idempotency_key = ?;
        """,
        (key_limpia,),
    )
    previo = cursor.fetchone()
    if previo:
        cursor.close()
        return {
            "status": "ALREADY_PROCESSED",
            "operation_id": previo[1],
            "tipo_movimiento": previo[2],
            "monto_cents": previo[3],
            "movement_id": None,
            "recorded_at_utc": previo[4],
            "message": "Reconocimiento de deuda histórica ya procesado con esta clave de idempotencia.",
        }

    ahora_utc = datetime.now(timezone.utc).isoformat()
    operation_id = f"OP-HIS-{uuid.uuid4().hex[:12]}"
    ref_ext = (referencia_externa or "").strip() or None

    # Jornada opcional: si hay una abierta se asocia para auditoría, si no, queda en NULL
    jornada = obtener_jornada_actual(conn)
    jornada_id = jornada["id"] if (jornada and jornada.get("estado") == "ABIERTA") else None

    try:
        # 2. Insertar movimiento en cuenta corriente (sin movement_id ni fondo_afectado)
        cursor.execute(
            """
            INSERT INTO tesoreria_cuenta_corriente_david (
                operation_id, idempotency_key, tipo_movimiento, monto_cents,
                fecha_referencia, recorded_at_utc, movement_id, fondo_afectado,
                referencia_externa, motivo, actor, jornada_id
            ) VALUES (?, ?, 'RECONOCIMIENTO_DEUDA_HISTORICA', ?, ?, ?, NULL, NULL, ?, ?, ?, ?);
            """,
            (
                operation_id,
                key_limpia,
                amount_cents,
                fecha_ref_limpia,
                ahora_utc,
                ref_ext,
                motivo_limpio,
                actor_limpio,
                jornada_id,
            ),
        )

        # 3. Registrar auditoría canónica
        recordAuditEvent(
            conn=conn,
            event_type="DAVID_HISTORICAL_DEBT_RECOGNIZED",
            aggregate_type="CUENTA_CORRIENTE_DAVID",
            aggregate_id=operation_id,
            user_id=actor_limpio,
            payload={
                "operation_id": operation_id,
                "amount_cents": amount_cents,
                "fecha_referencia": fecha_ref_limpia,
                "referencia_externa": ref_ext,
                "motivo": motivo_limpio,
                "actor": actor_limpio,
            },
            occurred_at=ahora_utc,
        )

        if auto_commit:
            conn.commit()

        resultado = {
            "status": "SUCCESS",
            "operation_id": operation_id,
            "idempotency_key": key_limpia,
            "tipo_movimiento": "RECONOCIMIENTO_DEUDA_HISTORICA",
            "monto_cents": amount_cents,
            "monto_formatted": formatMoney(amount_cents),
            "fecha_referencia": fecha_ref_limpia,
            "recorded_at_utc": ahora_utc,
            "movement_id": None,
            "fondo_afectado": None,
            "referencia_externa": ref_ext,
            "motivo": motivo_limpio,
            "actor": actor_limpio,
            "jornada_id": jornada_id,
        }
        return resultado

    except Exception as e:
        if auto_commit:
            conn.rollback()
        logger.error("Fallo al registrar reconocimiento de deuda histórica: %s", e)
        raise
    finally:
        cursor.close()


def obtener_estado_cuenta_david(
    conn: sqlite3.Connection,
    fecha_corte: Optional[str] = None,
) -> Dict[str, Any]:
    """Retorna el estado integral de la Cuenta Corriente PREPAY / David y su detalle cronológico.
    
    Calcula:
    - total_deuda_historica_cents
    - total_acreditaciones_cents
    - total_pagos_cents
    - obligacion_pendiente_cents = historica + acreditaciones - pagos
    - saldo_disponible_1130_cents (efectivo disponible en Prepay Efectivo)
    - lista de movimientos cronológicos con saldo resultante derivado.
    """
    cursor = conn.cursor()

    query = """
        SELECT
            id, operation_id, idempotency_key, tipo_movimiento, monto_cents,
            fecha_referencia, recorded_at_utc, movement_id, fondo_afectado,
            referencia_externa, motivo, actor, jornada_id, created_at
        FROM tesoreria_cuenta_corriente_david
    """
    params: List[Any] = []
    if fecha_corte and fecha_corte.strip():
        query += " WHERE fecha_referencia <= ?"
        params.append(fecha_corte.strip())

    query += " ORDER BY fecha_referencia ASC, recorded_at_utc ASC, id ASC;"

    cursor.execute(query, params)
    filas = cursor.fetchall()
    cursor.close()

    total_historica = 0
    total_acreditado = 0
    total_pagado = 0
    running_balance = 0

    movimientos_formateados: List[Dict[str, Any]] = []

    for f in filas:
        (
            row_id, op_id, idem_key, tipo, monto,
            fecha_ref, rec_utc, mov_id, fondo,
            ref_ext, mot, act, j_id, cr_at
        ) = f

        if tipo == "RECONOCIMIENTO_DEUDA_HISTORICA":
            total_historica += monto
            running_balance += monto
        elif tipo == "ACREDITACION_SALDO":
            total_acreditado += monto
            running_balance += monto
        elif tipo == "PAGO_EFECTIVO":
            total_pagado += monto
            running_balance -= monto

        movimientos_formateados.append({
            "id": row_id,
            "operation_id": op_id,
            "idempotency_key": idem_key,
            "tipo_movimiento": tipo,
            "monto_cents": monto,
            "monto_formatted": formatMoney(monto),
            "fecha_referencia": fecha_ref,
            "recorded_at_utc": rec_utc,
            "movement_id": mov_id,
            "fondo_afectado": fondo,
            "referencia_externa": ref_ext,
            "motivo": mot,
            "actor": act,
            "jornada_id": j_id,
            "created_at": cr_at,
            "saldo_obligacion_resultante_cents": running_balance,
            "saldo_obligacion_resultante_formatted": formatMoney(running_balance),
        })

    obligacion_pendiente = total_historica + total_acreditado - total_pagado

    # Obtener disponibilidad actual del fondo 1130
    disp_1130 = _obtener_saldo_disponible_1130(conn)

    return {
        "obligacion_pendiente_cents": obligacion_pendiente,
        "obligacion_pendiente_formatted": formatMoney(obligacion_pendiente),
        "total_acreditaciones_cents": total_acreditado,
        "total_acreditaciones_formatted": formatMoney(total_acreditado),
        "total_pagos_cents": total_pagado,
        "total_pagos_formatted": formatMoney(total_pagado),
        "total_deuda_historica_cents": total_historica,
        "total_deuda_historica_formatted": formatMoney(total_historica),
        "saldo_disponible_1130_cents": disp_1130,
        "saldo_disponible_1130_formatted": formatMoney(disp_1130),
        "fecha_corte": fecha_corte.strip() if fecha_corte and fecha_corte.strip() else None,
        "cantidad_movimientos": len(movimientos_formateados),
        "movimientos": movimientos_formateados,
    }
