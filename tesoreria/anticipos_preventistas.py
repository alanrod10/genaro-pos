"""Módulo de Anticipos y Reposición de Preventistas — GENARO TESORERÍA.

Gobierna el uso temporal de efectivo del fondo 1130 (Prepay Efectivo) para el pago
a preventistas y su posterior reposición obligatoria desde fondos de Drugstore (1120 y 1110).

Invariantes económicas obligatorias:
1. Fondo 1130: Puede utilizarse como puente temporal para pagar preventistas si posee saldo suficiente.
   Cero sobregiro permitido.
2. Salida inicial: Registra egreso real en 1130 con importe real, proveedor, motivo y referencia.
   Genera una obligación operativa de reposición a favor de 1130.
   NO constituye un segundo gasto comercial ni se imputa como deuda personal de socio o cajero.
3. Cascada de reposición obligatoria:
   - 1° Prioridad: Debita el saldo disponible en 1120 (Drugstore Acumulado / Tesoro).
   - 2° Prioridad: Si 1120 no alcanza, debita el saldo restante necesario en 1110 (Drugstore Diario).
   - Ambos débitos acreditan 1130 mediante traslados internos atómicos e idempotentes.
4. Trazabilidad: Relación 1 anticipo -> 1 egreso original en 1130 -> N reposiciones parciales o totales.
5. Estado inmutable derivado: El monto repuesto y el pendiente se calculan dinámicamente
   desde las reposiciones reales vinculadas al expediente. No se almacena saldo mutable.
"""

from datetime import datetime, timezone
import logging
import sqlite3
from typing import Any, Dict, List, Optional
import uuid

from tesoreria.availability import getFundBalance
from tesoreria.jornadas import obtener_jornada_actual
from tesoreria.money import addMoney, assertValidMoney, formatMoney, subtractMoney
from tesoreria.movements import _obtener_saldo_reservado_fondo, registerExit
from tesoreria.transfers import confirmTransferReceipt, transferFunds
from tesoreria.audit_chain import recordAuditEvent

logger = logging.getLogger("genaro_pos.anticipos_preventistas")


class ErrorAnticipoPreventista(Exception):
    """Excepción base para el circuito de anticipos y reposiciones de preventistas."""
    pass


class ErrorSaldoInsuficienteAnticipo(ErrorAnticipoPreventista):
    """Fondo físico 1130 no tiene saldo disponible para otorgar el anticipo."""
    pass


class ErrorSaldoInsuficienteReposicion(ErrorAnticipoPreventista):
    """Los fondos 1120 y 1110 no suman saldo disponible suficiente para la reposición solicitada."""
    pass


class ErrorValidacionAnticipo(ErrorAnticipoPreventista):
    """Parámetros inválidos o violación de invariantes de negocio del anticipo."""
    pass


class ErrorIdempotenciaAnticipo(ErrorAnticipoPreventista):
    """Conflicto de clave de idempotencia en anticipos o reposiciones."""
    pass


class AnticipoNoEncontrado(ErrorAnticipoPreventista):
    """El expediente de anticipo solicitado no existe."""
    pass


def _obtener_saldo_disponible_fondo(conn: sqlite3.Connection, codigo_fondo: str) -> int:
    """Retorna los centavos disponibles en un fondo activo descontando reservas."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, saldo_centavos, permite_sobregiro FROM tesoreria_fondos WHERE codigo = ? AND estado = 'ACTIVO';",
        (codigo_fondo,),
    )
    row = cursor.fetchone()
    cursor.close()
    if not row:
        raise ErrorValidacionAnticipo(f"Fondo activo {codigo_fondo} no encontrado en catálogo de Tesorería.")
    fund_id, saldo_en_libros, permite_sobregiro = row
    if permite_sobregiro == 1:
        return 999_999_999_99
    reservado = _obtener_saldo_reservado_fondo(conn, fund_id)
    return saldo_en_libros - reservado


def crear_anticipo_preventista(
    conn: sqlite3.Connection,
    proveedor: str,
    monto_cents: int,
    motivo: str,
    actor: str,
    idempotency_key: str,
    referencia_documento: Optional[str] = None,
    pedido_id: Optional[str] = None,
    auto_commit: bool = True,
) -> Dict[str, Any]:
    """Registra un anticipo para preventista debitando efectivamente el fondo 1130.

    Efectos atómicos:
    - Valida saldo disponible en 1130 (cero sobregiro).
    - Registra egreso real en tesoreria_movimientos vía registerExit.
    - Crea el expediente en tesoreria_anticipos_preventistas.
    - Emite evento de auditoría canónica.
    - Garantiza idempotencia estricta.
    """
    assertValidMoney(monto_cents)
    if monto_cents <= 0:
        raise ErrorValidacionAnticipo(f"El importe del anticipo debe ser estrictamente positivo (> 0): {monto_cents}")

    prov_limpio = (proveedor or "").strip()
    if not prov_limpio:
        raise ErrorValidacionAnticipo("La identificación del destinatario/preventista es obligatoria.")

    motivo_limpio = (motivo or "").strip()
    if not motivo_limpio:
        raise ErrorValidacionAnticipo("El motivo o concepto del anticipo es obligatorio.")

    actor_limpio = (actor or "").strip()
    if not actor_limpio:
        raise ErrorValidacionAnticipo("La identidad del operador responsable es obligatoria.")

    key_limpia = (idempotency_key or "").strip()
    if not key_limpia:
        raise ErrorValidacionAnticipo("La clave de idempotencia es obligatoria para registrar el anticipo.")

    ref_limpia = (referencia_documento or "").strip() or None
    pedido_limpio = (pedido_id or "").strip() or None

    cursor = conn.cursor()

    # 1. Verificar idempotencia
    cursor.execute(
        """
        SELECT id FROM tesoreria_anticipos_preventistas WHERE idempotency_key = ?;
        """,
        (key_limpia,),
    )
    previo = cursor.fetchone()
    if previo:
        cursor.close()
        res_previo = obtener_anticipo_preventista(conn, previo[0])
        res_previo["status"] = "ALREADY_PROCESSED"
        res_previo["message"] = "Anticipo ya procesado previamente con la misma clave de idempotencia."
        return res_previo

    # 2. Validar jornada operativa abierta
    jornada = obtener_jornada_actual(conn)
    if not jornada or jornada["estado"] != "ABIERTA":
        cursor.close()
        raise ErrorValidacionAnticipo("Se requiere una jornada de tesorería ABIERTA para registrar un anticipo.")

    # 3. Validar saldo disponible en fondo 1130
    disp_1130 = _obtener_saldo_disponible_fondo(conn, "1130")
    if monto_cents > disp_1130:
        cursor.close()
        raise ErrorSaldoInsuficienteAnticipo(
            f"Saldo disponible insuficiente en fondo 1130 (Prepay Efectivo). "
            f"Disponible: {formatMoney(disp_1130)}, Requerido para anticipo: {formatMoney(monto_cents)}."
        )

    ahora_utc = datetime.now(timezone.utc).isoformat()
    hoy_str = datetime.now().strftime("%Y%m%d")
    anticipo_id = f"ANT-{hoy_str}-{uuid.uuid4().hex[:6].upper()}"

    try:
        # 4. Registrar salida real de Tesorería exclusivamente del fondo 1130
        res_exit = registerExit(
            conn=conn,
            fund_code="1130",
            amount_cents=monto_cents,
            counterpart=f"ANTICIPO_PREVENTISTA_{prov_limpio.upper()}",
            reason=f"Anticipo entrega mercadería {prov_limpio}: {motivo_limpio}",
            created_by=actor_limpio,
            terminal_id="TERM-POS-01",
            idempotency_key=f"mov_ant_{key_limpia}",
            evidence_reference=ref_limpia or anticipo_id,
            occurred_at=ahora_utc,
            auto_commit=False,
        )
        salida_movement_id = res_exit["movement_id"]

        # 5. Insertar expediente en tesoreria_anticipos_preventistas
        cursor.execute(
            """
            INSERT INTO tesoreria_anticipos_preventistas (
                id, idempotency_key, proveedor, monto_original_cents,
                motivo, referencia_documento, pedido_id, salida_movement_id,
                creado_por, creado_en_utc, jornada_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                anticipo_id,
                key_limpia,
                prov_limpio,
                monto_cents,
                motivo_limpio,
                ref_limpia,
                pedido_limpio,
                salida_movement_id,
                actor_limpio,
                ahora_utc,
                jornada["id"],
            ),
        )

        # 6. Evento de auditoría canónica
        recordAuditEvent(
            conn=conn,
            event_type="PREVENTISTA_ANTICIPO_POSTED",
            aggregate_type="ANTICIPO_PREVENTISTA",
            aggregate_id=anticipo_id,
            user_id=actor_limpio,
            payload={
                "anticipo_id": anticipo_id,
                "proveedor": prov_limpio,
                "monto_cents": monto_cents,
                "salida_movement_id": salida_movement_id,
                "referencia_documento": ref_limpia,
                "pedido_id": pedido_limpio,
                "motivo": motivo_limpio,
                "actor": actor_limpio,
            },
            occurred_at=ahora_utc,
        )

        if auto_commit:
            conn.commit()

        resultado = obtener_anticipo_preventista(conn, anticipo_id)
        resultado["status"] = "SUCCESS"
        resultado["message"] = f"Anticipo #{anticipo_id} registrado exitosamente con salida de Fondo 1130."
        return resultado

    except Exception as e:
        if auto_commit:
            conn.rollback()
        logger.error("Fallo atómico al crear anticipo de preventista: %s", e)
        raise
    finally:
        cursor.close()


def reponer_anticipo_preventista(
    conn: sqlite3.Connection,
    anticipo_id: str,
    actor: str,
    idempotency_key: str,
    monto_cents: Optional[int] = None,
    motivo: Optional[str] = None,
    auto_commit: bool = True,
) -> Dict[str, Any]:
    """Registra la reposición jerárquica del anticipo debitando 1120 y luego 1110 hacia 1130.

    Cascada obligatoria:
    1. Debita primero el saldo disponible en 1120 (Drugstore Acumulado).
    2. Si 1120 no alcanza, debita el saldo restante necesario en 1110 (Drugstore Diario).
    3. Si ambos fondos no cubren el monto solicitado, rechaza la operación sin mutaciones.
    4. Cada tramo genera transferencia interna y se vincula en tesoreria_reposiciones_preventistas.
    5. NO constituye un segundo gasto comercial.
    """
    key_limpia = (idempotency_key or "").strip()
    if not key_limpia:
        raise ErrorValidacionAnticipo("La clave de idempotencia es obligatoria para la reposición.")

    actor_limpio = (actor or "").strip()
    if not actor_limpio:
        raise ErrorValidacionAnticipo("La identidad del operador responsable es obligatoria.")

    cursor = conn.cursor()

    # 1. Verificar idempotencia en reposiciones (detectando tanto clave exacta como prefijos de tramos)
    cursor.execute(
        """
        SELECT r.anticipo_id, r.reposicion_id
        FROM tesoreria_reposiciones_preventistas r
        WHERE r.idempotency_key = ? OR r.idempotency_key LIKE ?
        LIMIT 1;
        """,
        (key_limpia, f"{key_limpia}_%"),
    )
    previo = cursor.fetchone()
    if previo:
        cursor.close()
        res_previo = obtener_anticipo_preventista(conn, previo[0])
        res_previo["status"] = "ALREADY_PROCESSED"
        res_previo["message"] = "Reposición ya procesada previamente con esta clave de idempotencia."
        return res_previo

    # 2. Buscar anticipo y verificar saldo pendiente derivado
    anticipo = obtener_anticipo_preventista(conn, anticipo_id)
    if not anticipo:
        cursor.close()
        raise AnticipoNoEncontrado(f"Anticipo #{anticipo_id} no encontrado.")

    pendiente = anticipo["monto_pendiente_cents"]
    if pendiente <= 0:
        cursor.close()
        raise ErrorValidacionAnticipo(
            f"El anticipo #{anticipo_id} ya se encuentra totalmente repuesto (pendiente: $0,00)."
        )

    # 3. Determinar importe a reponer
    if monto_cents is not None:
        assertValidMoney(monto_cents)
        if monto_cents <= 0:
            cursor.close()
            raise ErrorValidacionAnticipo(f"El importe de reposición debe ser estrictamente positivo: {monto_cents}")
        if monto_cents > pendiente:
            cursor.close()
            raise ErrorValidacionAnticipo(
                f"El importe solicitado para reponer ({formatMoney(monto_cents)}) no puede superar "
                f"el saldo pendiente del anticipo ({formatMoney(pendiente)})."
            )
        monto_a_reponer = monto_cents
    else:
        monto_a_reponer = pendiente

    # 4. Validar jornada operativa abierta
    jornada = obtener_jornada_actual(conn)
    if not jornada or jornada["estado"] != "ABIERTA":
        cursor.close()
        raise ErrorValidacionAnticipo("Se requiere una jornada de tesorería ABIERTA para registrar reposiciones.")

    # 5. Consultar disponibilidad jerárquica: 1° 1120, 2° 1110
    disp_1120 = _obtener_saldo_disponible_fondo(conn, "1120")
    disp_1110 = _obtener_saldo_disponible_fondo(conn, "1110")
    disp_total = disp_1120 + disp_1110

    if monto_a_reponer > disp_total:
        cursor.close()
        raise ErrorSaldoInsuficienteReposicion(
            f"Disponibilidad combinada insuficiente para reponer {formatMoney(monto_a_reponer)}. "
            f"Disponible en 1120 (Tesoro): {formatMoney(disp_1120)}, "
            f"Disponible en 1110 (Diario): {formatMoney(disp_1110)}. "
            f"Total disponible en Drugstore: {formatMoney(disp_total)}."
        )

    # 6. Desglosar cascada
    monto_1120 = min(monto_a_reponer, disp_1120)
    monto_1110 = monto_a_reponer - monto_1120

    ahora_utc = datetime.now(timezone.utc).isoformat()
    motivo_base = (motivo or "").strip() or f"Reposición operativa anticipo {anticipo_id}"

    tramos: List[tuple[str, int]] = []
    if monto_1120 > 0:
        tramos.append(("1120", monto_1120))
    if monto_1110 > 0:
        tramos.append(("1110", monto_1110))

    reposiciones_generadas = []

    try:
        # 7. Ejecutar cada tramo atómicamente dentro de la misma transacción
        for fondo_origen, monto_tramo in tramos:
            sub_key = f"{key_limpia}_{fondo_origen}"
            reason_tramo = f"{motivo_base} [{fondo_origen}->1130]"

            # Transferencia interna debita fondo_origen
            res_trf = transferFunds(
                conn=conn,
                origin_fund_code=fondo_origen,
                destination_fund_code="1130",
                amount_cents=monto_tramo,
                dispatched_by=actor_limpio,
                reason=reason_tramo,
                evidence_reference=anticipo_id,
                context="REPOSICION_PREVENTISTAS",
                idempotency_key=f"trf_rep_{sub_key}",
                occurred_at=ahora_utc,
                auto_commit=False,
            )
            out_mov_id = res_trf["out_movement_id"]

            # Confirmación acredita fondo 1130
            res_rcv = confirmTransferReceipt(
                conn=conn,
                transfer_id=res_trf["transfer_id"],
                received_by=actor_limpio,
                idempotency_key=f"rcv_rep_{sub_key}",
                occurred_at=ahora_utc,
                auto_commit=False,
            )
            in_mov_id = res_rcv["in_movement_id"]

            # Registrar en tesoreria_reposiciones_preventistas
            rep_id = f"REP-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
            cursor.execute(
                """
                INSERT INTO tesoreria_reposiciones_preventistas (
                    reposicion_id, idempotency_key, anticipo_id, fondo_origen, fondo_destino,
                    monto_cents, movement_id_egreso, movement_id_ingreso, motivo, actor,
                    creado_en_utc, jornada_id
                ) VALUES (?, ?, ?, ?, '1130', ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    rep_id,
                    sub_key,
                    anticipo_id,
                    fondo_origen,
                    monto_tramo,
                    out_mov_id,
                    in_mov_id,
                    reason_tramo,
                    actor_limpio,
                    ahora_utc,
                    jornada["id"] if jornada else None,
                ),
            )

            reposiciones_generadas.append({
                "reposicion_id": rep_id,
                "fondo_origen": fondo_origen,
                "monto_cents": monto_tramo,
                "monto_formatted": formatMoney(monto_tramo),
                "movement_id_egreso": out_mov_id,
                "movement_id_ingreso": in_mov_id,
            })

        # 8. Evento de auditoría canónica
        recordAuditEvent(
            conn=conn,
            event_type="PREVENTISTA_REPOSICION_POSTED",
            aggregate_type="ANTICIPO_PREVENTISTA",
            aggregate_id=anticipo_id,
            user_id=actor_limpio,
            payload={
                "anticipo_id": anticipo_id,
                "monto_total_repuesto_cents": monto_a_reponer,
                "desglose_tramos": reposiciones_generadas,
                "actor": actor_limpio,
            },
            occurred_at=ahora_utc,
        )

        if auto_commit:
            conn.commit()

        anticipo_actualizado = obtener_anticipo_preventista(conn, anticipo_id)
        anticipo_actualizado["status"] = "SUCCESS"
        anticipo_actualizado["reposicion_aplicada_cents"] = monto_a_reponer
        anticipo_actualizado["reposicion_aplicada_formatted"] = formatMoney(monto_a_reponer)
        anticipo_actualizado["tramos_reposicion"] = reposiciones_generadas
        anticipo_actualizado["message"] = (
            f"Reposición de {formatMoney(monto_a_reponer)} aplicada con éxito a anticipo #{anticipo_id} "
            f"({len(reposiciones_generadas)} tramo(s) ejecutado(s))."
        )
        return anticipo_actualizado

    except Exception as e:
        if auto_commit:
            conn.rollback()
        logger.error("Fallo atómico al reponer anticipo de preventista: %s", e)
        raise
    finally:
        cursor.close()


def obtener_anticipo_preventista(
    conn: sqlite3.Connection,
    anticipo_id: str,
) -> Optional[Dict[str, Any]]:
    """Consulta el detalle íntegro de un anticipo derivando montos repuestos y pendientes."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            id, idempotency_key, proveedor, monto_original_cents,
            motivo, referencia_documento, pedido_id, salida_movement_id,
            creado_por, creado_en_utc, jornada_id
        FROM tesoreria_anticipos_preventistas
        WHERE id = ?;
        """,
        (anticipo_id,),
    )
    fila = cursor.fetchone()
    if not fila:
        cursor.close()
        return None

    (
        a_id, idem_key, prov, monto_orig,
        mot, ref_doc, ped_id, sal_mov_id,
        creador, cr_utc, j_id
    ) = fila

    # Obtener todas las reposiciones vinculadas al anticipo
    cursor.execute(
        """
        SELECT
            id, reposicion_id, idempotency_key, fondo_origen, fondo_destino,
            monto_cents, movement_id_egreso, movement_id_ingreso, motivo,
            actor, creado_en_utc, jornada_id
        FROM tesoreria_reposiciones_preventistas
        WHERE anticipo_id = ?
        ORDER BY creado_en_utc ASC, id ASC;
        """,
        (anticipo_id,),
    )
    filas_rep = cursor.fetchall()
    cursor.close()

    total_repuesto = 0
    reposiciones_list: List[Dict[str, Any]] = []
    for r in filas_rep:
        (
            r_id, r_rep_id, r_idem, f_orig, f_dest,
            m_cents, mov_egr, mov_ing, r_mot,
            r_act, r_utc, r_jid
        ) = r
        total_repuesto += m_cents
        reposiciones_list.append({
            "id": r_id,
            "reposicion_id": r_rep_id,
            "idempotency_key": r_idem,
            "fondo_origen": f_orig,
            "fondo_destino": f_dest,
            "monto_cents": m_cents,
            "monto_formatted": formatMoney(m_cents),
            "movement_id_egreso": mov_egr,
            "movement_id_ingreso": mov_ing,
            "motivo": r_mot,
            "actor": r_act,
            "creado_en_utc": r_utc,
            "jornada_id": r_jid,
        })

    monto_pendiente = max(0, monto_orig - total_repuesto)

    if total_repuesto == 0:
        estado = "PENDIENTE_REPOSICION"
    elif total_repuesto < monto_orig:
        estado = "PARCIALMENTE_REPUESTO"
    else:
        estado = "TOTALMENTE_REPUESTO"

    return {
        "id": a_id,
        "idempotency_key": idem_key,
        "proveedor": prov,
        "monto_original_cents": monto_orig,
        "monto_original_formatted": formatMoney(monto_orig),
        "monto_repuesto_cents": total_repuesto,
        "monto_repuesto_formatted": formatMoney(total_repuesto),
        "monto_pendiente_cents": monto_pendiente,
        "monto_pendiente_formatted": formatMoney(monto_pendiente),
        "estado": estado,
        "motivo": mot,
        "referencia_documento": ref_doc,
        "pedido_id": ped_id,
        "salida_movement_id": sal_mov_id,
        "creado_por": creador,
        "creado_en_utc": cr_utc,
        "jornada_id": j_id,
        "cantidad_reposiciones": len(reposiciones_list),
        "reposiciones": reposiciones_list,
    }


def listar_anticipos_preventistas(
    conn: sqlite3.Connection,
    estado: Optional[str] = None,
    proveedor: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Consulta la lista de anticipos con filtros opcionales de estado y proveedor."""
    cursor = conn.cursor()
    query = """
        SELECT
            a.id, a.idempotency_key, a.proveedor, a.monto_original_cents,
            a.motivo, a.referencia_documento, a.pedido_id, a.salida_movement_id,
            a.creado_por, a.creado_en_utc, a.jornada_id,
            COALESCE(SUM(r.monto_cents), 0) AS total_repuesto_cents
        FROM tesoreria_anticipos_preventistas a
        LEFT JOIN tesoreria_reposiciones_preventistas r ON r.anticipo_id = a.id
    """
    where_clauses = []
    params: List[Any] = []

    if proveedor and proveedor.strip():
        where_clauses.append("LOWER(TRIM(a.proveedor)) = LOWER(TRIM(?))")
        params.append(proveedor.strip())

    if where_clauses:
        query += " WHERE " + " AND ".join(where_clauses)

    query += " GROUP BY a.id ORDER BY a.creado_en_utc DESC, a.id DESC;"

    cursor.execute(query, params)
    filas = cursor.fetchall()
    cursor.close()

    resultado: List[Dict[str, Any]] = []
    filtro_estado = (estado or "").strip().upper()

    for f in filas:
        (
            a_id, idem_key, prov, monto_orig,
            mot, ref_doc, ped_id, sal_mov_id,
            creador, cr_utc, j_id, tot_rep
        ) = f

        pendiente = max(0, monto_orig - tot_rep)
        if tot_rep == 0:
            est = "PENDIENTE_REPOSICION"
        elif tot_rep < monto_orig:
            est = "PARCIALMENTE_REPUESTO"
        else:
            est = "TOTALMENTE_REPUESTO"

        if filtro_estado and filtro_estado != "TODOS" and est != filtro_estado:
            continue

        resultado.append({
            "id": a_id,
            "idempotency_key": idem_key,
            "proveedor": prov,
            "monto_original_cents": monto_orig,
            "monto_original_formatted": formatMoney(monto_orig),
            "monto_repuesto_cents": tot_rep,
            "monto_repuesto_formatted": formatMoney(tot_rep),
            "monto_pendiente_cents": pendiente,
            "monto_pendiente_formatted": formatMoney(pendiente),
            "estado": est,
            "motivo": mot,
            "referencia_documento": ref_doc,
            "pedido_id": ped_id,
            "salida_movement_id": sal_mov_id,
            "creado_por": creador,
            "creado_en_utc": cr_utc,
            "jornada_id": j_id,
        })

    return resultado


def obtener_historial_reposiciones(
    conn: sqlite3.Connection,
    anticipo_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Consulta el historial global o por anticipo de todas las reposiciones ejecutadas."""
    cursor = conn.cursor()
    query = """
        SELECT
            r.id, r.reposicion_id, r.idempotency_key, r.anticipo_id,
            r.fondo_origen, r.fondo_destino, r.monto_cents,
            r.movement_id_egreso, r.movement_id_ingreso, r.motivo,
            r.actor, r.creado_en_utc, r.jornada_id, a.proveedor
        FROM tesoreria_reposiciones_preventistas r
        JOIN tesoreria_anticipos_preventistas a ON r.anticipo_id = a.id
    """
    params: List[Any] = []
    if anticipo_id and anticipo_id.strip():
        query += " WHERE r.anticipo_id = ?"
        params.append(anticipo_id.strip())

    query += " ORDER BY r.creado_en_utc DESC, r.id DESC;"

    cursor.execute(query, params)
    filas = cursor.fetchall()
    cursor.close()

    resultado = []
    for f in filas:
        (
            r_id, rep_id, idem, ant_id, f_orig, f_dest,
            monto, mov_egr, mov_ing, mot, act, cr_utc, j_id, prov
        ) = f
        resultado.append({
            "id": r_id,
            "reposicion_id": rep_id,
            "idempotency_key": idem,
            "anticipo_id": ant_id,
            "proveedor": prov,
            "fondo_origen": f_orig,
            "fondo_destino": f_dest,
            "monto_cents": monto,
            "monto_formatted": formatMoney(monto),
            "movement_id_egreso": mov_egr,
            "movement_id_ingreso": mov_ing,
            "motivo": mot,
            "actor": act,
            "creado_en_utc": cr_utc,
            "jornada_id": j_id,
        })
    return resultado


def obtener_resumen_obligaciones_preventistas(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Retorna un consolidado contable de las obligaciones de reposición a favor del fondo 1130."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            COALESCE(SUM(a.monto_original_cents), 0) as total_anticipado,
            COUNT(a.id) as cantidad_anticipos
        FROM tesoreria_anticipos_preventistas a;
        """
    )
    row_ant = cursor.fetchone()
    total_anticipado = int(row_ant[0]) if row_ant else 0
    cantidad_anticipos = int(row_ant[1]) if row_ant else 0

    cursor.execute(
        """
        SELECT COALESCE(SUM(r.monto_cents), 0), COUNT(r.id)
        FROM tesoreria_reposiciones_preventistas r;
        """
    )
    row_rep = cursor.fetchone()
    total_repuesto = int(row_rep[0]) if row_rep else 0
    cantidad_reposiciones = int(row_rep[1]) if row_rep else 0
    cursor.close()

    total_pendiente = max(0, total_anticipado - total_repuesto)

    # Disponibilidades actuales
    disp_1130 = _obtener_saldo_disponible_fondo(conn, "1130")
    disp_1120 = _obtener_saldo_disponible_fondo(conn, "1120")
    disp_1110 = _obtener_saldo_disponible_fondo(conn, "1110")

    return {
        "total_anticipado_cents": total_anticipado,
        "total_anticipado_formatted": formatMoney(total_anticipado),
        "total_repuesto_cents": total_repuesto,
        "total_repuesto_formatted": formatMoney(total_repuesto),
        "total_pendiente_reposicion_cents": total_pendiente,
        "total_pendiente_reposicion_formatted": formatMoney(total_pendiente),
        "cantidad_anticipos": cantidad_anticipos,
        "cantidad_reposiciones": cantidad_reposiciones,
        "saldo_disponible_1130_cents": disp_1130,
        "saldo_disponible_1130_formatted": formatMoney(disp_1130),
        "saldo_disponible_1120_cents": disp_1120,
        "saldo_disponible_1120_formatted": formatMoney(disp_1120),
        "saldo_disponible_1110_cents": disp_1110,
        "saldo_disponible_1110_formatted": formatMoney(disp_1110),
    }
