"""Módulo de Cuenta Corriente Claro Comercios / Ezequiel y Fondeos Físicos — GENARO TESORERÍA.

Gobierna la posición financiera y deudor-acreedor con el proveedor de saldo Claro Comercios (Ezequiel):
1. Acreditaciones efectivas de saldo: aumentan fondo 1230 y la obligación pendiente con Ezequiel.
2. Liquidaciones a Ezequiel: soportan pagos parciales y completos con liquidación mixta transparente:
   - Efectivo reservado en 1150 (Claro Físico en custodia).
   - Efectivo disponible de Drugstore (1110 Diario o 1120 Acumulado).
   - Transferencia bancaria real desde Mercado Pago hacia Claro Comercios (1210 -> 1230).
3. Reconocimiento de deuda histórica: append-only, auditable, no altera fondos.
4. Obligación pendiente calculada dinámicamente desde el libro mayor:
   OBLIGACIÓN PENDIENTE = RECONOCIMIENTOS HISTÓRICOS + ACREDITACIONES - PAGOS/LIQUIDACIONES.
5. Gestión de fondeos físicos pendientes (DEC-02) para recargas digitales con falta transitoria de efectivo.
"""

from datetime import datetime, timezone
import json
import logging
import sqlite3
from typing import Any, Dict, List, Optional
import uuid

from tesoreria.audit_chain import recordAuditEvent
from tesoreria.availability import getFundBalance
from tesoreria.idempotency import calcular_hash_solicitud, registrar_idempotencia, verificar_idempotencia
from tesoreria.jornadas import obtener_jornada_actual
from tesoreria.money import addMoney, assertValidMoney, formatMoney, subtractMoney
from tesoreria.movements import _obtener_saldo_reservado_fondo, registerEntry, registerExit
from tesoreria.transfers import confirmTransferReceipt, transferFunds

logger = logging.getLogger("genaro_pos.cuenta_corriente_ezequiel")


class ErrorCuentaCorrienteEzequiel(Exception):
    """Excepción base para operaciones de Cuenta Corriente Ezequiel."""
    pass


class ErrorSaldoInsuficienteEzequiel(ErrorCuentaCorrienteEzequiel):
    """Fondos seleccionados no poseen disponibilidad líquida suficiente para liquidar a Ezequiel."""
    pass


class ErrorValidacionEzequiel(ErrorCuentaCorrienteEzequiel):
    """Parámetros inválidos o violación de invariantes de negocio."""
    pass


class ErrorIdempotenciaEzequiel(ErrorCuentaCorrienteEzequiel):
    """Conflicto de clave o repetición no idempotente."""
    pass


def _obtener_saldo_disponible_fondo(conn: sqlite3.Connection, codigo_fondo: str) -> int:
    """Retorna los centavos disponibles en un fondo descontando reservas activas."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, saldo_centavos, permite_sobregiro FROM tesoreria_fondos WHERE codigo = ? AND estado = 'ACTIVO';",
        (codigo_fondo,),
    )
    row = cursor.fetchone()
    cursor.close()
    if not row:
        raise ErrorValidacionEzequiel(f"Fondo activo {codigo_fondo} no encontrado en catálogo.")
    fund_id, saldo_en_libros, permite_sobregiro = row
    if permite_sobregiro == 1:
        return 999_999_999_99
    reservado = _obtener_saldo_reservado_fondo(conn, fund_id)
    return max(0, saldo_en_libros - reservado)


def registrar_acreditacion_ezequiel(
    conn: sqlite3.Connection,
    amount_cents: int,
    referencia: str,
    actor: str,
    idempotency_key: str,
    motivo: Optional[str] = None,
    fecha_referencia: Optional[str] = None,
    auto_commit: bool = True,
) -> Dict[str, Any]:
    """Registra una acreditación efectiva de saldo digital Claro Comercios provista por Ezequiel.

    Efectos atómicos:
    - Incrementa el fondo digital 1230 vía registerEntry.
    - Incrementa la obligación pendiente con Ezequiel en tesoreria_cuenta_corriente_ezequiel.
    - Vincula ambos registros mediante movement_id.
    - Idempotencia estricta.
    """
    assertValidMoney(amount_cents)
    if amount_cents <= 0:
        raise ErrorValidacionEzequiel(f"El importe de acreditación debe ser estrictamente positivo (> 0): {amount_cents}")

    ref_limpia = (referencia or "").strip()
    if not ref_limpia:
        raise ErrorValidacionEzequiel("La referencia o comprobante de acreditación de Ezequiel es obligatoria.")

    actor_limpio = (actor or "").strip()
    if not actor_limpio:
        raise ErrorValidacionEzequiel("La identidad del actor responsable es obligatoria.")

    key_limpia = (idempotency_key or "").strip()
    if not key_limpia:
        raise ErrorValidacionEzequiel("La clave de idempotencia es obligatoria para la acreditación.")

    cursor = conn.cursor()

    # 1. Idempotencia
    cursor.execute(
        """
        SELECT id, operation_id, tipo_movimiento, monto_cents, movement_id, recorded_at_utc
        FROM tesoreria_cuenta_corriente_ezequiel
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
            "message": "Operación de acreditación ya procesada previamente con esta clave de idempotencia.",
        }

    ahora_utc = datetime.now(timezone.utc).isoformat()
    fecha_ref = (fecha_referencia or ahora_utc[:10]).strip()
    operation_id = f"OP-ACR-EZ-{uuid.uuid4().hex[:12]}"
    motivo_final = (motivo or f"Acreditación de saldo digital Claro Comercios (Ref: {ref_limpia})").strip()

    jornada = obtener_jornada_actual(conn)
    journal_id = jornada["id"] if (jornada and jornada.get("estado") == "ABIERTA") else None

    try:
        # 2. Entrada en fondo digital 1230
        res_entry = registerEntry(
            conn=conn,
            fund_code="1230",
            amount_cents=amount_cents,
            counterpart="ACREDITACION_PROVEEDOR_EZEQUIEL",
            reason=motivo_final,
            created_by=actor_limpio,
            terminal_id="TERM-POS-01",
            idempotency_key=f"MOV-ACR-EZ-{key_limpia}",
            evidence_reference=ref_limpia,
            occurred_at=ahora_utc,
            auto_commit=False,
        )
        movement_id = res_entry["movement_id"]

        # 3. Asiento en cuenta corriente
        cursor.execute(
            """
            INSERT INTO tesoreria_cuenta_corriente_ezequiel (
                operation_id, idempotency_key, tipo_movimiento, monto_cents,
                fecha_referencia, recorded_at_utc, movement_id, fondo_afectado,
                referencia_externa, motivo, actor, jornada_id
            ) VALUES (?, ?, 'ACREDITACION_SALDO', ?, ?, ?, ?, '1230', ?, ?, ?, ?);
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

        recordAuditEvent(
            conn=conn,
            event_type="EZEQUIEL_BALANCE_ACCREDITATION_POSTED",
            aggregate_type="CUENTA_CORRIENTE_EZEQUIEL",
            aggregate_id=operation_id,
            user_id=actor_limpio,
            payload={
                "operation_id": operation_id,
                "amount_cents": amount_cents,
                "movement_id": movement_id,
                "fund_code": "1230",
                "referencia": ref_limpia,
                "actor": actor_limpio,
            },
            occurred_at=ahora_utc,
        )

        if auto_commit:
            conn.commit()

        return {
            "status": "SUCCESS",
            "operation_id": operation_id,
            "idempotency_key": key_limpia,
            "tipo_movimiento": "ACREDITACION_SALDO",
            "monto_cents": amount_cents,
            "monto_formatted": formatMoney(amount_cents),
            "fecha_referencia": fecha_ref,
            "recorded_at_utc": ahora_utc,
            "movement_id": movement_id,
            "fondo_afectado": "1230",
            "referencia_externa": ref_limpia,
            "motivo": motivo_final,
            "actor": actor_limpio,
            "jornada_id": journal_id,
        }
    except Exception as e:
        if auto_commit:
            conn.rollback()
        logger.error("Fallo atómico al registrar acreditación de Ezequiel: %s", e)
        raise
    finally:
        cursor.close()


def registrar_liquidacion_ezequiel(
    conn: sqlite3.Connection,
    monto_1150_cents: int = 0,
    monto_drugstore_cents: int = 0,
    fondo_drugstore: str = "1110",
    monto_transferencia_cents: int = 0,
    referencia: str = "",
    actor: str = "",
    idempotency_key: str = "",
    motivo: Optional[str] = None,
    fecha_referencia: Optional[str] = None,
    auto_commit: bool = True,
) -> Dict[str, Any]:
    """Registra una liquidación (pago) a Ezequiel con soporte multi-fondo (efectivo Claro, Drugstore y transferencia MP).

    Permite pagos parciales y pagos completos, validando que el importe total abonado
    no supere la obligación pendiente acumulada ni exceda la disponibilidad real de cada fondo.
    """
    assertValidMoney(monto_1150_cents)
    assertValidMoney(monto_drugstore_cents)
    assertValidMoney(monto_transferencia_cents)

    total_pago = monto_1150_cents + monto_drugstore_cents + monto_transferencia_cents
    if total_pago <= 0:
        raise ErrorValidacionEzequiel("El importe total de la liquidación debe ser estrictamente positivo (> 0).")

    if monto_drugstore_cents > 0 and fondo_drugstore not in ("1110", "1120"):
        raise ErrorValidacionEzequiel(f"Fondo de Drugstore no autorizado para liquidación: {fondo_drugstore}. Debe ser 1110 o 1120.")

    ref_limpia = (referencia or "").strip()
    if not ref_limpia:
        raise ErrorValidacionEzequiel("La referencia o número de recibo del cobrador/Ezequiel es obligatoria.")

    actor_limpio = (actor or "").strip()
    if not actor_limpio:
        raise ErrorValidacionEzequiel("La identidad del actor responsable es obligatoria.")

    key_limpia = (idempotency_key or "").strip()
    if not key_limpia:
        raise ErrorValidacionEzequiel("La clave de idempotencia es obligatoria para la liquidación.")

    cursor = conn.cursor()

    # 1. Idempotencia
    cursor.execute(
        """
        SELECT id, operation_id, tipo_movimiento, monto_cents, recorded_at_utc, detalle_liquidacion
        FROM tesoreria_cuenta_corriente_ezequiel
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
            "recorded_at_utc": previo[4],
            "detalle_liquidacion": json.loads(previo[5]) if previo[5] else None,
            "message": "Liquidación a Ezequiel ya procesada previamente con esta clave de idempotencia.",
        }

    # 2. Validar que no se pague más de la obligación pendiente actual
    posicion = consultar_posicion_ezequiel(conn)
    deuda_pendiente = posicion["obligacion_pendiente_cents"]
    if total_pago > deuda_pendiente:
        cursor.close()
        raise ErrorValidacionEzequiel(
            f"El importe total liquidado ({formatMoney(total_pago)}) supera la obligación pendiente "
            f"con Ezequiel ({formatMoney(deuda_pendiente)})."
        )

    # 3. Validar disponibilidad de cada componente
    if monto_1150_cents > 0:
        disp_1150 = _obtener_saldo_disponible_fondo(conn, "1150")
        if disp_1150 < monto_1150_cents:
            cursor.close()
            raise ErrorSaldoInsuficienteEzequiel(
                f"Saldo insuficiente en Claro Efectivo (1150): Disponible {formatMoney(disp_1150)}, solicitado {formatMoney(monto_1150_cents)}."
            )

    if monto_drugstore_cents > 0:
        disp_ds = _obtener_saldo_disponible_fondo(conn, fondo_drugstore)
        if disp_ds < monto_drugstore_cents:
            cursor.close()
            raise ErrorSaldoInsuficienteEzequiel(
                f"Saldo insuficiente en Drugstore ({fondo_drugstore}): Disponible {formatMoney(disp_ds)}, solicitado {formatMoney(monto_drugstore_cents)}."
            )

    if monto_transferencia_cents > 0:
        disp_mp = _obtener_saldo_disponible_fondo(conn, "1210")
        if disp_mp < monto_transferencia_cents:
            cursor.close()
            raise ErrorSaldoInsuficienteEzequiel(
                f"Saldo insuficiente en Mercado Pago (1210): Disponible {formatMoney(disp_mp)}, solicitado {formatMoney(monto_transferencia_cents)}."
            )

    ahora_utc = datetime.now(timezone.utc).isoformat()
    fecha_ref = (fecha_referencia or ahora_utc[:10]).strip()
    operation_id = f"OP-LIQ-EZ-{uuid.uuid4().hex[:12]}"
    motivo_final = (motivo or f"Liquidación a Ezequiel Recibo #{ref_limpia} (Total: {formatMoney(total_pago)})").strip()

    jornada = obtener_jornada_actual(conn)
    journal_id = jornada["id"] if (jornada and jornada.get("estado") == "ABIERTA") else None

    movimientos_afectados: List[str] = []

    try:
        # A. Egreso de 1150
        if monto_1150_cents > 0:
            res_1150 = registerExit(
                conn=conn,
                fund_code="1150",
                amount_cents=monto_1150_cents,
                counterpart="RENDICION_PROVEEDOR_CLARO",
                reason=f"Liquidación Ezequiel en efectivo custodia (Recibo #{ref_limpia})",
                created_by=actor_limpio,
                idempotency_key=f"LIQ-1150-{key_limpia}",
                evidence_reference=ref_limpia,
                occurred_at=ahora_utc,
                auto_commit=False,
            )
            movimientos_afectados.append(res_1150["movement_id"])

        # B. Egreso de Drugstore
        if monto_drugstore_cents > 0:
            res_ds = registerExit(
                conn=conn,
                fund_code=fondo_drugstore,
                amount_cents=monto_drugstore_cents,
                counterpart="LIQUIDACION_EZEQUIEL_EFECTIVO_DRUGSTORE",
                reason=f"Complemento liquidación Ezequiel desde {fondo_drugstore} (Recibo #{ref_limpia})",
                created_by=actor_limpio,
                idempotency_key=f"LIQ-DS-{key_limpia}",
                evidence_reference=ref_limpia,
                occurred_at=ahora_utc,
                auto_commit=False,
            )
            movimientos_afectados.append(res_ds["movement_id"])

        # C. Transferencia bancaria 1210 -> 1230
        if monto_transferencia_cents > 0:
            res_trf = transferFunds(
                conn=conn,
                origin_fund_code="1210",
                destination_fund_code="1230",
                amount_cents=monto_transferencia_cents,
                dispatched_by=actor_limpio,
                reason=f"Transferencia liquidación Claro Comercios (Recibo #{ref_limpia})",
                idempotency_key=f"LIQ-TRF-{key_limpia}",
                evidence_reference=ref_limpia,
                context="LIQUIDACION_EZEQUIEL_TRANSFERENCIA",
                occurred_at=ahora_utc,
                auto_commit=False,
            )
            confirmTransferReceipt(
                conn=conn,
                transfer_id=res_trf["transfer_id"],
                received_by=actor_limpio,
                idempotency_key=f"LIQ-TRF-CONF-{key_limpia}",
                auto_commit=False,
            )
            movimientos_afectados.append(f"TRANSFER_{res_trf['transfer_id']}")

        detalle = {
            "monto_1150_cents": monto_1150_cents,
            "monto_drugstore_cents": monto_drugstore_cents,
            "fondo_drugstore": fondo_drugstore if monto_drugstore_cents > 0 else None,
            "monto_transferencia_cents": monto_transferencia_cents,
            "total_cents": total_pago,
            "movements": movimientos_afectados,
        }
        mov_master = movimientos_afectados[0] if movimientos_afectados else None
        fondo_afectado_label = "MIXTO" if len([m for m in [monto_1150_cents, monto_drugstore_cents, monto_transferencia_cents] if m > 0]) > 1 else (
            "1150" if monto_1150_cents > 0 else (fondo_drugstore if monto_drugstore_cents > 0 else "1210->1230")
        )

        cursor.execute(
            """
            INSERT INTO tesoreria_cuenta_corriente_ezequiel (
                operation_id, idempotency_key, tipo_movimiento, monto_cents,
                fecha_referencia, recorded_at_utc, movement_id, fondo_afectado,
                referencia_externa, motivo, actor, jornada_id, detalle_liquidacion
            ) VALUES (?, ?, 'PAGO_LIQUIDACION', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                operation_id,
                key_limpia,
                total_pago,
                fecha_ref,
                ahora_utc,
                mov_master,
                fondo_afectado_label,
                ref_limpia,
                motivo_final,
                actor_limpio,
                journal_id,
                json.dumps(detalle),
            ),
        )

        recordAuditEvent(
            conn=conn,
            event_type="EZEQUIEL_SETTLEMENT_POSTED",
            aggregate_type="CUENTA_CORRIENTE_EZEQUIEL",
            aggregate_id=operation_id,
            user_id=actor_limpio,
            payload={
                "operation_id": operation_id,
                "total_cents": total_pago,
                "detalle": detalle,
                "referencia": ref_limpia,
                "actor": actor_limpio,
            },
            occurred_at=ahora_utc,
        )

        if auto_commit:
            conn.commit()

        deuda_restante = deuda_pendiente - total_pago
        return {
            "status": "SUCCESS",
            "operation_id": operation_id,
            "idempotency_key": key_limpia,
            "tipo_movimiento": "PAGO_LIQUIDACION",
            "monto_cents": total_pago,
            "monto_formatted": formatMoney(total_pago),
            "obligacion_remanente_cents": deuda_restante,
            "obligacion_remanente_formatted": formatMoney(deuda_restante),
            "fecha_referencia": fecha_ref,
            "recorded_at_utc": ahora_utc,
            "detalle_liquidacion": detalle,
            "referencia_externa": ref_limpia,
            "motivo": motivo_final,
            "actor": actor_limpio,
            "jornada_id": journal_id,
        }
    except Exception as e:
        if auto_commit:
            conn.rollback()
        logger.error("Fallo atómico al registrar liquidación a Ezequiel: %s", e)
        raise
    finally:
        cursor.close()


def registrar_reconocimiento_historico_ezequiel(
    conn: sqlite3.Connection,
    amount_cents: int,
    motivo: str,
    actor: str,
    idempotency_key: str,
    fecha_referencia: str,
    referencia_externa: Optional[str] = None,
    confirmacion_explicita: bool = False,
    auto_commit: bool = True,
) -> Dict[str, Any]:
    """Registra un reconocimiento de saldo histórico o deuda inicial con Ezequiel sin movimientos de fondos."""
    assertValidMoney(amount_cents)
    if amount_cents <= 0:
        raise ErrorValidacionEzequiel(f"El importe de deuda histórica debe ser estrictamente positivo (> 0): {amount_cents}")

    if not confirmacion_explicita:
        raise ErrorValidacionEzequiel("Se requiere confirmación explícita para registrar deuda histórica con Ezequiel.")

    fecha_ref_limpia = (fecha_referencia or "").strip()
    if not fecha_ref_limpia:
        raise ErrorValidacionEzequiel("La fecha de referencia de la deuda histórica es obligatoria (YYYY-MM-DD).")

    motivo_limpio = (motivo or "").strip()
    if not motivo_limpio:
        raise ErrorValidacionEzequiel("El motivo u observación del reconocimiento histórico es obligatorio.")

    actor_limpio = (actor or "").strip()
    if not actor_limpio:
        raise ErrorValidacionEzequiel("La identidad del actor responsable es obligatoria.")

    key_limpia = (idempotency_key or "").strip()
    if not key_limpia:
        raise ErrorValidacionEzequiel("La clave de idempotencia es obligatoria para el reconocimiento histórico.")

    cursor = conn.cursor()

    # 1. Idempotencia
    cursor.execute(
        """
        SELECT id, operation_id, tipo_movimiento, monto_cents, recorded_at_utc
        FROM tesoreria_cuenta_corriente_ezequiel
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
    operation_id = f"OP-HIS-EZ-{uuid.uuid4().hex[:12]}"
    ref_ext = (referencia_externa or "").strip() or None

    jornada = obtener_jornada_actual(conn)
    jornada_id = jornada["id"] if (jornada and jornada.get("estado") == "ABIERTA") else None

    try:
        cursor.execute(
            """
            INSERT INTO tesoreria_cuenta_corriente_ezequiel (
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

        recordAuditEvent(
            conn=conn,
            event_type="EZEQUIEL_HISTORICAL_DEBT_RECOGNIZED",
            aggregate_type="CUENTA_CORRIENTE_EZEQUIEL",
            aggregate_id=operation_id,
            user_id=actor_limpio,
            payload={
                "operation_id": operation_id,
                "amount_cents": amount_cents,
                "fecha_referencia": fecha_ref_limpia,
                "actor": actor_limpio,
                "motivo": motivo_limpio,
            },
            occurred_at=ahora_utc,
        )

        if auto_commit:
            conn.commit()

        return {
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
    except Exception as e:
        if auto_commit:
            conn.rollback()
        logger.error("Fallo atómico al registrar reconocimiento histórico de Ezequiel: %s", e)
        raise
    finally:
        cursor.close()


def consultar_posicion_ezequiel(conn: sqlite3.Connection, fecha_corte: Optional[str] = None) -> Dict[str, Any]:
    """Consulta la posición neta con Ezequiel, saldos disponibles en fondos relacionados y detalle cronológico."""
    cursor = conn.cursor()

    query = """
        SELECT
            id, operation_id, idempotency_key, tipo_movimiento, monto_cents,
            fecha_referencia, recorded_at_utc, movement_id, fondo_afectado,
            referencia_externa, motivo, actor, jornada_id, detalle_liquidacion, created_at
        FROM tesoreria_cuenta_corriente_ezequiel
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
            ref_ext, mot, act, j_id, det_liq, cr_at
        ) = f

        if tipo == "RECONOCIMIENTO_DEUDA_HISTORICA":
            total_historica += monto
            running_balance += monto
        elif tipo == "ACREDITACION_SALDO":
            total_acreditado += monto
            running_balance += monto
        elif tipo == "PAGO_LIQUIDACION":
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
            "detalle_liquidacion": json.loads(det_liq) if det_liq else None,
            "created_at": cr_at,
            "saldo_obligacion_resultante_cents": running_balance,
            "saldo_obligacion_resultante_formatted": formatMoney(running_balance),
        })

    obligacion_pendiente = total_historica + total_acreditado - total_pagado

    disp_1150 = _obtener_saldo_disponible_fondo(conn, "1150")
    disp_1230 = _obtener_saldo_disponible_fondo(conn, "1230")
    disp_1110 = _obtener_saldo_disponible_fondo(conn, "1110")
    disp_1120 = _obtener_saldo_disponible_fondo(conn, "1120")

    return {
        "obligacion_pendiente_cents": obligacion_pendiente,
        "obligacion_pendiente_formatted": formatMoney(obligacion_pendiente),
        "total_acreditaciones_cents": total_acreditado,
        "total_acreditaciones_formatted": formatMoney(total_acreditado),
        "total_pagos_cents": total_pagado,
        "total_pagos_formatted": formatMoney(total_pagado),
        "total_deuda_historica_cents": total_historica,
        "total_deuda_historica_formatted": formatMoney(total_historica),
        "saldo_disponible_1150_cents": disp_1150,
        "saldo_disponible_1150_formatted": formatMoney(disp_1150),
        "saldo_disponible_1230_cents": disp_1230,
        "saldo_disponible_1230_formatted": formatMoney(disp_1230),
        "saldo_disponible_1110_cents": disp_1110,
        "saldo_disponible_1110_formatted": formatMoney(disp_1110),
        "saldo_disponible_1120_cents": disp_1120,
        "saldo_disponible_1120_formatted": formatMoney(disp_1120),
        "fecha_corte": fecha_corte.strip() if fecha_corte and fecha_corte.strip() else None,
        "cantidad_movimientos": len(movimientos_formateados),
        "movimientos": movimientos_formateados,
    }


# =============================================================================
# GESTIÓN DE FONDEOS FÍSICOS PENDIENTES (DEC-02)
# =============================================================================

def registrar_fondeo_pendiente(
    conn: sqlite3.Connection,
    tipo_circuito: str,
    servicio_id: int,
    monto_capital_cents: int,
    fondo_origen_esperado: str,
    fondo_destino_custodia: str,
    actor: str,
    idempotency_key: str,
    observaciones: Optional[str] = None,
    auto_commit: bool = True,
) -> Dict[str, Any]:
    """Registra una obligación de fondeo físico pendiente cuando la caja diaria no posee saldo suficiente."""
    assertValidMoney(monto_capital_cents)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id, estado FROM tesoreria_fondeos_pendientes WHERE idempotency_key = ?;",
        (idempotency_key,),
    )
    prev = cursor.fetchone()
    if prev:
        cursor.close()
        return {"id": prev[0], "status": "ALREADY_REGISTERED", "estado": prev[1]}

    ahora_utc = datetime.now(timezone.utc).isoformat()
    cursor.execute(
        """
        INSERT INTO tesoreria_fondeos_pendientes (
            tipo_circuito, servicio_id, idempotency_key, monto_capital_cents,
            fondo_origen_esperado, fondo_destino_custodia, estado, actor,
            created_at_utc, observaciones
        ) VALUES (?, ?, ?, ?, ?, ?, 'PENDIENTE', ?, ?, ?);
        """,
        (
            tipo_circuito,
            servicio_id,
            idempotency_key,
            monto_capital_cents,
            fondo_origen_esperado,
            fondo_destino_custodia,
            actor,
            ahora_utc,
            observaciones,
        ),
    )
    fondeo_id = cursor.lastrowid
    cursor.close()

    if auto_commit:
        conn.commit()

    return {
        "id": fondeo_id,
        "status": "CREATED",
        "estado": "PENDIENTE",
        "tipo_circuito": tipo_circuito,
        "servicio_id": servicio_id,
        "monto_capital_cents": monto_capital_cents,
        "fondo_origen_esperado": fondo_origen_esperado,
        "fondo_destino_custodia": fondo_destino_custodia,
    }


def ejecutar_fondeo_pendiente(
    conn: sqlite3.Connection,
    fondeo_id: int,
    actor: str,
    fondo_origen: Optional[str] = None,
    auto_commit: bool = True,
) -> Dict[str, Any]:
    """Ejecuta el traslado de fondos para un fondeo pendiente, verificando disponibilidad real sin sobregiro.
    
    Permite regularizar tanto desde 1110 (Drugstore Diario) como desde 1120 (Drugstore Acumulado).
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, tipo_circuito, servicio_id, monto_capital_cents, fondo_origen_esperado,
               fondo_destino_custodia, estado, idempotency_key
        FROM tesoreria_fondeos_pendientes
        WHERE id = ?;
        """,
        (fondeo_id,),
    )
    row = cursor.fetchone()
    if not row:
        cursor.close()
        raise ErrorValidacionEzequiel(f"Fondeo pendiente #{fondeo_id} no encontrado.")

    f_id, circuito, srv_id, monto, origen, destino, estado, key = row
    if estado == "FONDEADO":
        cursor.close()
        return {"id": f_id, "status": "ALREADY_FUNDED"}
    if estado == "CANCELADO":
        cursor.close()
        raise ErrorValidacionEzequiel(f"El fondeo #{fondeo_id} fue cancelado previamente.")

    # Selección y validación del fondo origen (1110 o 1120)
    if fondo_origen and fondo_origen.strip():
        fo_clean = fondo_origen.strip()
        if fo_clean not in ("1110", "1120"):
            cursor.close()
            raise ErrorValidacionEzequiel(
                f"Fondo de origen '{fo_clean}' no autorizado para fondeos. Debe ser '1110' o '1120'."
            )
        origen_final = fo_clean
        disp_origen = _obtener_saldo_disponible_fondo(conn, origen_final)
        if disp_origen < monto:
            cursor.close()
            raise ErrorSaldoInsuficienteEzequiel(
                f"Saldo insuficiente en {origen_final} para completar fondeo: disponible {formatMoney(disp_origen)}, requerido {formatMoney(monto)}."
            )
    else:
        disp_origen = _obtener_saldo_disponible_fondo(conn, origen)
        if disp_origen >= monto:
            origen_final = origen
        else:
            alt = "1120" if origen == "1110" else "1110"
            disp_alt = _obtener_saldo_disponible_fondo(conn, alt)
            if disp_alt >= monto:
                origen_final = alt
            else:
                cursor.close()
                raise ErrorSaldoInsuficienteEzequiel(
                    f"Saldo insuficiente en Drugstore para completar fondeo: "
                    f"disponible en {origen}: {formatMoney(disp_origen)}, en {alt}: {formatMoney(disp_alt)}, "
                    f"requerido {formatMoney(monto)}."
                )

    context = "FONDEO_CUSTODIA_CLARO" if circuito == "CLARO" else "PREPAY_COMPENSATION"
    ev_ref = f"TICKET_{srv_id}" if circuito == "PREPAY" else f"SRV_{srv_id}"
    ahora_utc = datetime.now(timezone.utc).isoformat()

    try:
        trf = transferFunds(
            conn=conn,
            origin_fund_code=origen_final,
            destination_fund_code=destino,
            amount_cents=monto,
            dispatched_by=actor,
            reason=f"Fondeo físico custodia {circuito} para servicio #{srv_id} desde {origen_final}",
            idempotency_key=f"EXEC-{key}",
            evidence_reference=ev_ref,
            context=context,
            occurred_at=ahora_utc,
            auto_commit=False,
        )
        confirmTransferReceipt(
            conn=conn,
            transfer_id=trf["transfer_id"],
            received_by=actor,
            idempotency_key=f"CONF-EXEC-{key}",
            auto_commit=False,
        )

        cursor.execute(
            """
            UPDATE tesoreria_fondeos_pendientes
            SET estado = 'FONDEADO',
                fondo_origen_esperado = ?,
                movement_id_traslado = ?,
                funded_at_utc = ?
            WHERE id = ?;
            """,
            (origen_final, f"TRANSFER_{trf['transfer_id']}", ahora_utc, f_id),
        )

        if auto_commit:
            conn.commit()

        return {
            "id": f_id,
            "status": "SUCCESS",
            "estado": "FONDEADO",
            "transfer_id": trf["transfer_id"],
            "monto_cents": monto,
            "monto_formatted": formatMoney(monto),
        }
    except Exception as e:
        if auto_commit:
            conn.rollback()
        logger.error("Error al ejecutar fondeo pendiente #%s: %s", fondeo_id, e)
        raise
    finally:
        cursor.close()


def listar_fondeos_pendientes(
    conn: sqlite3.Connection,
    estado: Optional[str] = "PENDIENTE",
    tipo_circuito: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Retorna listado de obligaciones de fondeo físico de custodia."""
    cursor = conn.cursor()
    query = """
        SELECT id, tipo_circuito, servicio_id, idempotency_key, monto_capital_cents,
               fondo_origen_esperado, fondo_destino_custodia, estado, movement_id_traslado,
               actor, created_at_utc, funded_at_utc, observaciones
        FROM tesoreria_fondeos_pendientes
        WHERE 1=1
    """
    params: List[Any] = []
    if estado and estado != "ALL":
        query += " AND estado = ?"
        params.append(estado)
    if tipo_circuito:
        query += " AND tipo_circuito = ?"
        params.append(tipo_circuito)

    query += " ORDER BY id DESC;"
    cursor.execute(query, params)
    rows = cursor.fetchall()
    cursor.close()

    resultado = []
    for r in rows:
        resultado.append({
            "id": r[0],
            "tipo_circuito": r[1],
            "servicio_id": r[2],
            "idempotency_key": r[3],
            "monto_capital_cents": r[4],
            "monto_capital_formatted": formatMoney(r[4]),
            "fondo_origen_esperado": r[5],
            "fondo_destino_custodia": r[6],
            "estado": r[7],
            "movement_id_traslado": r[8],
            "actor": r[9],
            "created_at_utc": r[10],
            "funded_at_utc": r[11],
            "observaciones": r[12],
        })
    return resultado
