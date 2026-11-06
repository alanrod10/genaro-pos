"""Gestión de Traslados de Fondos — GENARO TESORERÍA LABORATORIO.

Implementación de LAB-006:
- transferFunds (Despacho / Inicio de traslado, deducción de origen y pase a tránsito)
- confirmTransferReceipt (Confirmación de recepción y acreditación en destino)
- cancelPendingTransfer (Cancelación de traslado pendiente y restitución a origen)
- Tratamiento explícito del dinero en tránsito (el destino NO muestra disponibilidad antes de confirmar).
- Idempotencia y atomicidad transaccional completa.
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


class ErrorTraslado(Exception):
    """Excepción de regla de negocio o integridad para traslados."""
    pass


# Matriz canónica de transferencias internas autorizadas por la política de fondos
TRANSFERENCIAS_PERMITIDAS = {
    ("1110", "1120"),  # Drugstore Diario -> Drugstore Acumulado (Resguardo / Barrido de cierre)
    ("1120", "1110"),  # Drugstore Acumulado -> Drugstore Diario (Dotación de cambio / reposición)
    ("1110", "1140"),  # Drugstore Diario -> Adicionales Efectivo (Separación de adicionales de turno)
    ("1140", "1120"),  # Adicionales Efectivo -> Drugstore Acumulado (Resguardo de adicionales en tesoro)
    ("1130", "1120"),  # Prepay Efectivo -> Drugstore Acumulado (Rendición de recaudación Prepay a tesoro)
    ("1130", "1110"),  # Prepay Efectivo -> Drugstore Diario (Cambio operativo de mostrador)
    ("1110", "1130"),  # Drugstore Diario -> Prepay Efectivo (Dotación de efectivo para recargas)
    ("1120", "1130"),  # Drugstore Acumulado -> Prepay Efectivo (Compensación física PREPAY transferencia y reposición)
    ("1160", "1130"),  # Prepay Diario Efectivo -> Prepay Efectivo (Barrido de cierre PREPAY)
    ("1110", "1210"),  # Drugstore Diario -> Mercado Pago Propio (Depósito bancario con comprobante)
    ("1310", "1110"),  # Tránsito Preventistas -> Drugstore Diario (Rendición de cobranzas en mostrador)
    ("1310", "1120"),  # Tránsito Preventistas -> Drugstore Acumulado (Rendición de cobranzas en tesoro)
}


def validar_politica_traslado(
    origin_fund_code: str,
    destination_fund_code: str,
    context: Optional[str] = None,
    evidence_reference: Optional[str] = None,
    reason: Optional[str] = None,
) -> None:
    """Valida que la combinación de fondos esté explícitamente autorizada en la matriz operativa."""
    if origin_fund_code == destination_fund_code:
        raise ErrorTraslado("El fondo origen y destino deben ser diferentes.")

    # Excepción de dominio restrictiva: Fondeo de Custodia Claro (DEC-02)
    if origin_fund_code == "1110" and destination_fund_code == "1150":
        ctx_upper = (context or "").strip().upper()
        if ctx_upper != "FONDEO_CUSTODIA_CLARO":
            raise ErrorTraslado(
                "Fondo 1150 (Claro Efectivo) es recaudación de terceros en custodia; "
                "no admite traslados internos comerciales hacia o desde fondos propios del negocio "
                "salvo fondeo controlado con contexto 'FONDEO_CUSTODIA_CLARO'."
            )
        if not evidence_reference or not evidence_reference.strip():
            raise ErrorTraslado(
                "FONDEO CLARO: El traslado 1110 -> 1150 exige referencia obligatoria al servicio en evidence_reference."
            )
        return

    # Excepción de dominio restrictiva: Liquidación a Ezequiel vía transferencia digital Mercado Pago -> Claro Comercios
    if origin_fund_code == "1210" and destination_fund_code == "1230":
        ctx_upper = (context or "").strip().upper()
        if ctx_upper != "LIQUIDACION_EZEQUIEL_TRANSFERENCIA":
            raise ErrorTraslado(
                "Transferencia 1210 -> 1230 solo autorizada bajo contexto 'LIQUIDACION_EZEQUIEL_TRANSFERENCIA'."
            )
        if not evidence_reference or not evidence_reference.strip():
            raise ErrorTraslado(
                "LIQUIDACION EZEQUIEL: La transferencia 1210 -> 1230 exige comprobante o referencia en evidence_reference."
            )
        return

    if (origin_fund_code, destination_fund_code) not in TRANSFERENCIAS_PERMITIDAS:
        # Validaciones explicativas según la política económica violada
        if "1150" in (origin_fund_code, destination_fund_code):
            raise ErrorTraslado(
                "Fondo 1150 (Claro Efectivo) es recaudación de terceros en custodia; "
                "no admite traslados internos comerciales hacia o desde fondos propios del negocio."
            )

        if destination_fund_code == "1310":
            raise ErrorTraslado(
                "El fondo 1310 (Tránsito Preventistas) es una cuenta segregada externa; "
                "no puede recibir fondos propios del negocio."
            )

        if origin_fund_code in ("1220", "1230") or destination_fund_code in ("1220", "1230"):
            target = origin_fund_code if origin_fund_code in ("1220", "1230") else destination_fund_code
            raise ErrorTraslado(
                f"Las cuentas mayoristas de plataformas ({target}) requieren definición formal del propietario "
                "para operaciones de fondeo o retiro."
            )

        origen_fisico = origin_fund_code.startswith("11")
        destino_fisico = destination_fund_code.startswith("11")
        origen_digital = origin_fund_code.startswith("12")
        destino_digital = destination_fund_code.startswith("12")

        if (origen_fisico and destino_digital) or (origen_digital and destino_fisico):
            raise ErrorTraslado(
                f"VIOLACIÓN DE AISLAMIENTO ECONÓMICO: Queda prohibido transferir fondos entre cuentas físicas y "
                f"cuentas digitales ({origin_fund_code} -> {destination_fund_code})."
            )

        raise ErrorTraslado(
            f"La combinación de traslado entre {origin_fund_code} y {destination_fund_code} "
            "no está autorizada por la política de fondos de Tesorería."
        )

    # Gobernanza estricta sobre traslados específicos
    if origin_fund_code == "1120" and destination_fund_code == "1130":
        # Proteger ruta 1120 -> 1130: prohibir traslados manuales y reposiciones no formalizadas
        ctx_upper = (context or "").strip().upper()
        evi_upper = (evidence_reference or "").strip().upper()
        rea_upper = (reason or "").strip().upper()

        if ctx_upper == "REPOSICION_PREVENTISTAS":
            if not evidence_reference or not evidence_reference.strip():
                raise ErrorTraslado(
                    "GOBERNANZA DE FONDOS: La reposición de preventistas (1120 -> 1130) exige el identificador de expediente en evidence_reference."
                )
        elif ctx_upper == "PREPAY_COMPENSATION":
            if not evidence_reference or not evidence_reference.strip():
                raise ErrorTraslado(
                    "GOBERNANZA DE FONDOS: El traslado 1120 -> 1130 exige el identificador de ticket en evidence_reference (ej: 'TICKET_<id>')."
                )
        else:
            if (
                "REPOSICION" in ctx_upper
                or "REPOSICIÓN" in ctx_upper
                or "REPOSICION" in evi_upper
                or "REPOSICIÓN" in evi_upper
                or "REPOSICION" in rea_upper
                or "REPOSICIÓN" in rea_upper
            ):
                raise ErrorTraslado(
                    "GOBERNANZA DE FONDOS: Las reposiciones de preventistas exigen el contexto formal 'REPOSICION_PREVENTISTAS' "
                    "y el identificador de expediente en evidence_reference. Traslados manuales no formalizados quedan bloqueados."
                )
            raise ErrorTraslado(
                "GOBERNANZA DE FONDOS: El traslado de Drugstore Acumulado (1120) a Prepay Efectivo (1130) "
                "está restringido exclusivamente a compensaciones automáticas de PREPAY (context='PREPAY_COMPENSATION') "
                "o reposición de anticipos formalizados (context='REPOSICION_PREVENTISTAS'). "
                "Operaciones manuales o motivos genéricos de texto están estrictamente prohibidos."
            )

    if origin_fund_code == "1110" and destination_fund_code == "1130":
        ctx_upper = (context or "").strip().upper()
        if ctx_upper == "REPOSICION_PREVENTISTAS":
            if not evidence_reference or not evidence_reference.strip():
                raise ErrorTraslado(
                    "GOBERNANZA DE FONDOS: La reposición de preventistas (1110 -> 1130) exige el identificador de expediente en evidence_reference."
                )

    if origin_fund_code == "1160" and destination_fund_code == "1130":
        ctx_upper = (context or "").strip().upper()
        if evidence_reference != "BARRIDO_PREPAY_CIERRE_DIARIO" or ctx_upper != "PREPAY_DRAWER_SWEEP":
            raise ErrorTraslado(
                "GOBERNANZA DE FONDOS: El traslado de Prepay Diario (1160) a Prepay Efectivo (1130) "
                "está reservado exclusivamente al proceso de cierre diario (executePrepayDrawerSweep) "
                "y no admite operaciones manuales ordinarias."
            )


def transferFunds(
    conn: sqlite3.Connection,
    origin_fund_code: str,
    destination_fund_code: str,
    amount_cents: int,
    dispatched_by: str,
    reason: str,
    idempotency_key: Optional[str] = None,
    evidence_reference: Optional[str] = None,
    occurred_at: Optional[str] = None,
    context: Optional[str] = None,
    auto_commit: bool = True,
) -> Dict[str, Any]:
    """Inicia un traslado de fondos entre dos cuentas de tesorería."""
    assertValidMoney(amount_cents)
    if amount_cents <= 0:
        raise ErrorTraslado(f"El importe de traslado debe ser estrictamente positivo (> 0), recibido: {amount_cents}")

    validar_politica_traslado(
        origin_fund_code=origin_fund_code,
        destination_fund_code=destination_fund_code,
        context=context,
        evidence_reference=evidence_reference,
        reason=reason,
    )

    if not reason or not reason.strip():
        raise ErrorTraslado("El motivo del traslado es obligatorio.")

    if not dispatched_by or not dispatched_by.strip():
        raise ErrorTraslado("El usuario responsable del despacho es requerido.")

    # Idempotencia previa
    payload_hash = calcular_hash_solicitud({
        "origen": origin_fund_code,
        "destino": destination_fund_code,
        "monto_centavos": amount_cents,
        "motivo": reason.strip(),
        "dispatched_by": dispatched_by.strip(),
    })
    previo = verificar_idempotencia(conn, idempotency_key, "TRANSFER_INIT", payload_hash)
    if previo is not None:
        return previo

    jornada = obtener_jornada_actual(conn)

    # Gobernanza estricta de barrido PREPAY 1160 -> 1130:
    # Solo puede ejecutarse si la jornada activa está estrictamente en estado 'EN_CIERRE'
    if origin_fund_code == "1160" and destination_fund_code == "1130":
        if not jornada or jornada["estado"] != "EN_CIERRE":
            current_state = jornada["estado"] if jornada else "INEXISTENTE"
            j_num = f"#{jornada['numero_jornada']}" if jornada else ""
            raise ErrorTraslado(
                f"GOBERNANZA: El barrido de Caja PREPAY (1160 -> 1130) solo puede ejecutarse "
                f"cuando la jornada activa está estrictamente en estado 'EN_CIERRE'. "
                f"Estado actual de jornada {j_num}: '{current_state}'."
            )

    is_sweep = evidence_reference in ("BARRIDO_CIERRE_DIARIO", "BARRIDO_PREPAY_CIERRE_DIARIO")
    if not jornada or (jornada["estado"] != "ABIERTA" and not (is_sweep and jornada["estado"] == "EN_CIERRE")):
        if jornada and jornada["estado"] == "EN_CIERRE":
            raise ErrorTraslado("La jornada está en proceso de cierre. Operaciones ordinarias de traslado bloqueadas.")
        raise ErrorTraslado("Se requiere una jornada de tesorería ABIERTA para operar traslados.")

    fondo_origen = obtener_fondo_por_codigo(conn, origin_fund_code)
    fondo_destino = obtener_fondo_por_codigo(conn, destination_fund_code)

    if not fondo_origen or fondo_origen["estado"] != "ACTIVO":
        raise ErrorTraslado(f"Fondo origen {origin_fund_code} no disponible o no activo.")
    if not fondo_destino or fondo_destino["estado"] != "ACTIVO":
        raise ErrorTraslado(f"Fondo destino {destination_fund_code} no disponible o no activo.")

    saldo_origen = fondo_origen["saldo_centavos"]
    saldo_reservado_origen = _obtener_saldo_reservado_fondo(conn, fondo_origen["id"])
    disponible_origen = subtractMoney(saldo_origen, saldo_reservado_origen)

    # Validación canónica y relacional para 1120 -> 1130 con base de datos
    if origin_fund_code == "1120" and destination_fund_code == "1130":
        ctx_upper = (context or "").strip().upper()
        if ctx_upper == "REPOSICION_PREVENTISTAS":
            raw_ref = (evidence_reference or "").strip()
            cur_ant = conn.cursor()
            cur_ant.execute(
                """
                SELECT a.id, a.monto_original_cents, COALESCE(SUM(r.monto_cents), 0) as monto_repuesto_cents
                FROM tesoreria_anticipos_preventistas a
                LEFT JOIN tesoreria_reposiciones_preventistas r ON r.anticipo_id = a.id
                WHERE a.id = ?
                GROUP BY a.id;
                """,
                (raw_ref,),
            )
            row_ant = cur_ant.fetchone()
            cur_ant.close()
            if not row_ant:
                raise ErrorTraslado(
                    f"GOBERNANZA: No existe expediente de anticipo de preventista válido asociado a '{evidence_reference}'."
                )
            monto_orig = row_ant[1]
            monto_rep = row_ant[2]
            monto_pend = monto_orig - monto_rep
            if amount_cents > monto_pend:
                raise ErrorTraslado(
                    f"GOBERNANZA: El monto del traslado ({formatMoney(amount_cents)}) supera el saldo pendiente "
                    f"del anticipo #{raw_ref} ({formatMoney(monto_pend)})."
                )
        else:
            raw_ref = (evidence_reference or "").strip()
            ticket_key = raw_ref[7:] if raw_ref.upper().startswith("TICKET_") else raw_ref

            cur_coord = conn.cursor()
            cur_coord.execute(
                """
                SELECT id, coordination_id, commercial_ticket_id, monto_capital_cents, monto_adicional_cents, pago_transf_cents, estado
                FROM tesoreria_prepay_coordinacion
                WHERE commercial_ticket_id = ? OR coordination_id = ?
                LIMIT 1
                """,
                (ticket_key, raw_ref),
            )
            row_coord = cur_coord.fetchone()
            if not row_coord:
                cur_coord.close()
                raise ErrorTraslado(
                    f"GOBERNANZA: No existe coordinación PREPAY válida en tesorería asociada a la evidencia '{evidence_reference}'."
                )

            coord_data = dict(row_coord) if isinstance(row_coord, sqlite3.Row) else {
                "id": row_coord[0], "coordination_id": row_coord[1], "commercial_ticket_id": row_coord[2],
                "monto_capital_cents": row_coord[3], "monto_adicional_cents": row_coord[4],
                "pago_transf_cents": row_coord[5], "estado": row_coord[6]
            }

            if coord_data["pago_transf_cents"] <= 0:
                cur_coord.close()
                raise ErrorTraslado(
                    f"GOBERNANZA: La coordinación PREPAY para el ticket #{coord_data['commercial_ticket_id']} "
                    f"no registra cobro por transferencia bancaria (pago_transf_cents=0)."
                )

            if coord_data["estado"] not in ("COMMERCIAL_COMMITTED", "APPLICATION_PENDING", "FULLY_APPLIED"):
                cur_coord.close()
                raise ErrorTraslado(
                    f"GOBERNANZA: El ticket #{coord_data['commercial_ticket_id']} se encuentra en estado '{coord_data['estado']}', "
                    f"no admite compensación física."
                )

            if amount_cents != coord_data["monto_capital_cents"]:
                cur_coord.close()
                raise ErrorTraslado(
                    f"GOBERNANZA: El monto del traslado ({formatMoney(amount_cents)}) no coincide con el capital neto "
                    f"de la recarga ({formatMoney(coord_data['monto_capital_cents'])}). Prohibido transferir comisiones o importes arbitrarios."
                )

            # Evitar doble compensación física entre traslados con distintas claves de idempotencia
            cur_coord.execute(
                """
                SELECT transfer_id, idempotency_key, status
                FROM tesoreria_traslados
                WHERE origin_fund_id = ? AND destination_fund_id = ?
                  AND evidence_reference = ?
                  AND status != 'CANCELLED'
                LIMIT 1
                """,
                (fondo_origen["id"], fondo_destino["id"], raw_ref),
            )
            dup_trf = cur_coord.fetchone()
            cur_coord.close()
            if dup_trf:
                dup_id = dup_trf[0] if not isinstance(dup_trf, sqlite3.Row) else dup_trf["transfer_id"]
                dup_key = dup_trf[1] if not isinstance(dup_trf, sqlite3.Row) else dup_trf["idempotency_key"]
                if idempotency_key is None or dup_key != idempotency_key:
                    raise ErrorTraslado(
                        f"GOBERNANZA: Ya existe una compensación física activa para el ticket #{coord_data['commercial_ticket_id']} "
                        f"(traslado #{dup_id}). Prohibida la doble compensación."
                    )

    # Validación canónica para Fondeo Claro 1110 -> 1150
    if origin_fund_code == "1110" and destination_fund_code == "1150":
        ctx_upper = (context or "").strip().upper()
        if ctx_upper != "FONDEO_CUSTODIA_CLARO":
            raise ErrorTraslado(
                "GOBERNANZA: El traslado 1110 -> 1150 requiere obligatoriamente contexto 'FONDEO_CUSTODIA_CLARO'."
            )
        if not evidence_reference or not evidence_reference.strip():
            raise ErrorTraslado(
                "GOBERNANZA: El traslado 1110 -> 1150 requiere referencia obligatoria al servicio en evidence_reference."
            )

    # Validación canónica y relacional para 1110 -> 1130 con base de datos en contexto de reposición
    if origin_fund_code == "1110" and destination_fund_code == "1130":
        ctx_upper = (context or "").strip().upper()
        if ctx_upper == "REPOSICION_PREVENTISTAS":
            raw_ref = (evidence_reference or "").strip()
            cur_ant = conn.cursor()
            cur_ant.execute(
                """
                SELECT a.id, a.monto_original_cents, COALESCE(SUM(r.monto_cents), 0) as monto_repuesto_cents
                FROM tesoreria_anticipos_preventistas a
                LEFT JOIN tesoreria_reposiciones_preventistas r ON r.anticipo_id = a.id
                WHERE a.id = ?
                GROUP BY a.id;
                """,
                (raw_ref,),
            )
            row_ant = cur_ant.fetchone()
            cur_ant.close()
            if not row_ant:
                raise ErrorTraslado(
                    f"GOBERNANZA: No existe expediente de anticipo de preventista válido asociado a '{evidence_reference}'."
                )
            monto_orig = row_ant[1]
            monto_rep = row_ant[2]
            monto_pend = monto_orig - monto_rep
            if amount_cents > monto_pend:
                raise ErrorTraslado(
                    f"GOBERNANZA: El monto del traslado ({formatMoney(amount_cents)}) supera el saldo pendiente "
                    f"del anticipo #{raw_ref} ({formatMoney(monto_pend)})."
                )

    # Validación canónica y relacional para barrido 1160 -> 1130 con base de datos
    if origin_fund_code == "1160" and destination_fund_code == "1130":
        # 1. Verificar arqueo físico registrado en la jornada activa para el fondo 1160
        cur_arq = conn.cursor()
        cur_arq.execute(
            """
            SELECT reconciliation_id, journal_id, saldo_registrado_cents, saldo_verificado_cents,
                   diferencia_cents, authorized_by, notes, authorization_notes, status
            FROM tesoreria_arqueos
            WHERE journal_id = ? AND fund_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (jornada["id"], fondo_origen["id"]),
        )
        row_arq = cur_arq.fetchone()
        cur_arq.close()

        if not row_arq:
            raise ErrorTraslado(
                f"GOBERNANZA: El traslado de barrido 1160 -> 1130 exige un arqueo físico registrado en la jornada activa "
                f"#{jornada['numero_jornada']} para el fondo 1160. Prohibido utilizar el saldo teórico contable como sustituto del efectivo contado."
            )

        arq_data = dict(row_arq) if isinstance(row_arq, sqlite3.Row) else {
            "reconciliation_id": row_arq[0], "journal_id": row_arq[1],
            "saldo_registrado_cents": row_arq[2], "saldo_verificado_cents": row_arq[3],
            "diferencia_cents": row_arq[4], "authorized_by": row_arq[5],
            "notes": row_arq[6], "authorization_notes": row_arq[7],
            "status": row_arq[8],
        }

        # 2. Verificar que el arqueo pertenezca a la jornada activa
        if arq_data["journal_id"] != jornada["id"]:
            raise ErrorTraslado(
                f"GOBERNANZA: El arqueo encontrado (reconciliation_id={arq_data['reconciliation_id']}) "
                f"pertenece a la jornada #{arq_data['journal_id']}, no a la jornada activa en cierre #{jornada['id']}."
            )

        # 3. El monto a trasladar DEBE coincidir exactamente con el saldo verificado físico contado del arqueo
        if amount_cents != arq_data["saldo_verificado_cents"]:
            raise ErrorTraslado(
                f"GOBERNANZA: El monto a trasladar ({formatMoney(amount_cents)}) no coincide con el efectivo contado verificado "
                f"en el arqueo de la jornada ({formatMoney(arq_data['saldo_verificado_cents'])}). "
                f"Prohibido trasladar importes arbitrarios o desacoplados del recuento físico."
            )

        # 4. Si existe diferencia entre el arqueo y el saldo teórico, debe estar debidamente justificada / observada
        if arq_data["diferencia_cents"] != 0 and arq_data.get("status") != "RESOLVED":
            has_obs = bool(
                (arq_data.get("notes") and arq_data["notes"].strip())
                or (arq_data.get("authorization_notes") and arq_data["authorization_notes"].strip())
            )
            if not has_obs:
                raise ErrorTraslado(
                    f"GOBERNANZA: El arqueo del fondo 1160 registra una diferencia de "
                    f"{formatMoney(arq_data['diferencia_cents'])} sin observación registrada. "
                    f"Prohibido ejecutar el traslado hasta registrar la observación correspondiente."
                )

    is_sweep_drawer = (
        (evidence_reference == "BARRIDO_CIERRE_DIARIO" and origin_fund_code == "1110" and destination_fund_code == "1120")
        or (evidence_reference == "BARRIDO_PREPAY_CIERRE_DIARIO" and origin_fund_code == "1160" and destination_fund_code == "1130")
    ) and (jornada is not None)

    # 1. Si es un barrido de gaveta diaria (1110 -> 1120 o 1160 -> 1130), no se bloquea por reservas locales porque las reservas
    #    activas se re-anclan automáticamente al fondo acumulado durante el mismo vaciado atómico.
    # 2. Si el importe solicitado supera el saldo teórico (amount_cents > saldo_origen, es decir, sobrante físico),
    #    la excepción se permite EXCLUSIVAMENTE si el barrido está respaldado por un arqueo de la misma jornada
    #    donde el efectivo contado verificado coincide con dicho importe.
    if is_sweep_drawer:
        if amount_cents > saldo_origen:
            cur_chk = conn.cursor()
            cur_chk.execute(
                """
                SELECT saldo_verificado_cents
                FROM tesoreria_arqueos
                WHERE journal_id = ? AND fund_id = ?
                ORDER BY id DESC LIMIT 1
                """,
                (jornada["id"], fondo_origen["id"]),
            )
            row_chk = cur_chk.fetchone()
            cur_chk.close()
            if not row_chk or row_chk[0] != amount_cents:
                raise ErrorTraslado(
                    f"El barrido con sobrante ({formatMoney(amount_cents)}) excede el saldo teórico ({formatMoney(saldo_origen)}) "
                    f"sin un arqueo válido que verifique el recuento físico contado en la jornada #{jornada['id']}."
                )
    else:
        # Traslados ordinarios: disponibilidad estricta considerando reservas y prohibición de sobregiro
        if disponible_origen < amount_cents and fondo_origen["permite_sobregiro"] == 0:
            raise ErrorTraslado(
                f"Saldo disponible insuficiente en origen {origin_fund_code}: "
                f"Disponible {formatMoney(disponible_origen)} para traslado de {formatMoney(amount_cents)}."
            )

    ahora_utc = datetime.now(timezone.utc).isoformat()
    fecha_despacho = occurred_at or ahora_utc
    transfer_id = str(uuid.uuid4())
    out_movement_id = str(uuid.uuid4())

    cursor = conn.cursor()
    try:
        nuevo_saldo_origen = subtractMoney(saldo_origen, amount_cents)

        # 1. Deducir de fondo origen
        cursor.execute(
            """
            UPDATE tesoreria_fondos
            SET saldo_centavos = ?,
                version = version + 1,
                updated_at = ?,
                actualizado_por = ?
            WHERE id = ? AND version = ?
            """,
            (nuevo_saldo_origen, ahora_utc, dispatched_by.strip(), fondo_origen["id"], fondo_origen["version"]),
        )
        if cursor.rowcount == 0:
            raise ErrorTraslado("Conflicto de concurrencia al debitar fondo origen.")

        # 2. Registrar movimiento de salida en origen
        cursor.execute(
            """
            INSERT INTO tesoreria_movimientos (
                movement_id, idempotency_key, journal_id, fund_id, movement_type,
                amount_cents, currency, counterpart, reason, evidence_reference,
                created_by, terminal_id, status, occurred_at, recorded_at
            ) VALUES (?, ?, ?, ?, 'TRANSFER_OUT', ?, 'ARS', ?, ?, ?, ?, 'TERM-01', 'POSTED', ?, ?)
            """,
            (
                out_movement_id,
                f"out_{transfer_id}",
                jornada["id"],
                fondo_origen["id"],
                amount_cents,
                f"Traslado hacia {fondo_destino['nombre']} ({destination_fund_code})",
                reason.strip(),
                evidence_reference,
                dispatched_by.strip(),
                fecha_despacho,
                ahora_utc,
            ),
        )

        # 3. Registrar traslado en estado PENDING_RECEIPT (dinero en tránsito, no acreditado en destino aún)
        cursor.execute(
            """
            INSERT INTO tesoreria_traslados (
                transfer_id, idempotency_key, journal_id, origin_fund_id, destination_fund_id,
                amount_cents, currency, status, dispatched_by, received_by,
                dispatched_at, received_at, reason, evidence_reference,
                out_movement_id, in_movement_id
            ) VALUES (?, ?, ?, ?, ?, ?, 'ARS', 'PENDING_RECEIPT', ?, NULL, ?, NULL, ?, ?, ?, NULL)
            """,
            (
                transfer_id,
                idempotency_key,
                jornada["id"],
                fondo_origen["id"],
                fondo_destino["id"],
                amount_cents,
                dispatched_by.strip(),
                fecha_despacho,
                reason.strip(),
                evidence_reference,
                out_movement_id,
            ),
        )

        # 4. Auditoría
        _registrar_auditoria(
            conn,
            entity_type="TRASLADO",
            entity_id=transfer_id,
            action="DISPATCH",
            user_id=dispatched_by.strip(),
            details={
                "transfer_id": transfer_id,
                "origin": origin_fund_code,
                "destination": destination_fund_code,
                "amount_cents": amount_cents,
                "status": "PENDING_RECEIPT",
            },
            timestamp_utc=ahora_utc,
        )

        resultado = {
            "transfer_id": transfer_id,
            "idempotency_key": idempotency_key,
            "journal_id": jornada["id"],
            "origin_fund_code": origin_fund_code,
            "origin_fund_name": fondo_origen["nombre"],
            "destination_fund_code": destination_fund_code,
            "destination_fund_name": fondo_destino["nombre"],
            "amount_cents": amount_cents,
            "amount_formatted": formatMoney(amount_cents),
            "status": "PENDING_RECEIPT",
            "dispatched_by": dispatched_by.strip(),
            "dispatched_at": fecha_despacho,
            "received_by": None,
            "received_at": None,
            "reason": reason.strip(),
            "out_movement_id": out_movement_id,
        }

        registrar_idempotencia(
            conn,
            idempotency_key=idempotency_key,
            operation_type="TRANSFER_INIT",
            resource_id=transfer_id,
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


def confirmTransferReceipt(
    conn: sqlite3.Connection,
    transfer_id: str,
    received_by: str,
    idempotency_key: Optional[str] = None,
    occurred_at: Optional[str] = None,
    auto_commit: bool = True,
) -> Dict[str, Any]:
    """Confirma la recepción física/digital del traslado, acreditando los fondos en el destino."""
    if not received_by or not received_by.strip():
        raise ErrorTraslado("El responsable de recepción es obligatorio.")

    payload_hash = calcular_hash_solicitud({
        "transfer_id": transfer_id,
        "received_by": received_by.strip(),
    })
    previo = verificar_idempotencia(conn, idempotency_key, "TRANSFER_CONFIRM", payload_hash)
    if previo is not None:
        return previo

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT t.id, t.transfer_id, t.journal_id, t.origin_fund_id, t.destination_fund_id,
               t.amount_cents, t.status, t.dispatched_by, t.dispatched_at, t.reason,
               fo.codigo as cod_origen, fo.nombre as nom_origen,
               fd.codigo as cod_destino, fd.nombre as nom_destino, fd.saldo_centavos as saldo_dest, fd.version as ver_dest
        FROM tesoreria_traslados t
        JOIN tesoreria_fondos fo ON t.origin_fund_id = fo.id
        JOIN tesoreria_fondos fd ON t.destination_fund_id = fd.id
        WHERE t.transfer_id = ?
        """,
        (transfer_id,),
    )
    fila = cursor.fetchone()
    if not fila:
        cursor.close()
        raise ErrorTraslado(f"Traslado #{transfer_id} no encontrado.")

    d = dict(fila)
    if d["status"] != "PENDING_RECEIPT":
        cursor.close()
        raise ErrorTraslado(f"El traslado #{transfer_id} no está pendiente de recepción (estado actual: {d['status']}).")

    ahora_utc = datetime.now(timezone.utc).isoformat()
    fecha_recepcion = occurred_at or ahora_utc
    in_movement_id = str(uuid.uuid4())

    try:
        nuevo_saldo_destino = addMoney(d["saldo_dest"], d["amount_cents"])

        # 1. Acreditar fondo destino
        cursor.execute(
            """
            UPDATE tesoreria_fondos
            SET saldo_centavos = ?,
                version = version + 1,
                updated_at = ?,
                actualizado_por = ?
            WHERE id = ? AND version = ?
            """,
            (nuevo_saldo_destino, ahora_utc, received_by.strip(), d["destination_fund_id"], d["ver_dest"]),
        )
        if cursor.rowcount == 0:
            raise ErrorTraslado("Conflicto de concurrencia al acreditar fondo destino.")

        # 2. Registrar movimiento de entrada en destino
        cursor.execute(
            """
            INSERT INTO tesoreria_movimientos (
                movement_id, idempotency_key, journal_id, fund_id, movement_type,
                amount_cents, currency, counterpart, reason, evidence_reference,
                created_by, terminal_id, status, occurred_at, recorded_at
            ) VALUES (?, ?, ?, ?, 'TRANSFER_IN', ?, 'ARS', ?, ?, NULL, ?, 'TERM-01', 'POSTED', ?, ?)
            """,
            (
                in_movement_id,
                f"in_{transfer_id}",
                d["journal_id"],
                d["destination_fund_id"],
                d["amount_cents"],
                f"Recepción de traslado desde {d['nom_origen']} ({d['cod_origen']})",
                d["reason"],
                received_by.strip(),
                fecha_recepcion,
                ahora_utc,
            ),
        )

        # 3. Actualizar estado del traslado a CONFIRMED
        cursor.execute(
            """
            UPDATE tesoreria_traslados
            SET status = 'CONFIRMED',
                received_by = ?,
                received_at = ?,
                in_movement_id = ?
            WHERE transfer_id = ?
            """,
            (received_by.strip(), fecha_recepcion, in_movement_id, transfer_id),
        )

        # 4. Auditoría
        _registrar_auditoria(
            conn,
            entity_type="TRASLADO",
            entity_id=transfer_id,
            action="CONFIRM_RECEIPT",
            user_id=received_by.strip(),
            details={
                "transfer_id": transfer_id,
                "status": "CONFIRMED",
                "in_movement_id": in_movement_id,
            },
            timestamp_utc=ahora_utc,
        )

        resultado = {
            "transfer_id": transfer_id,
            "origin_fund_code": d["cod_origen"],
            "destination_fund_code": d["cod_destino"],
            "amount_cents": d["amount_cents"],
            "amount_formatted": formatMoney(d["amount_cents"]),
            "status": "CONFIRMED",
            "dispatched_by": d["dispatched_by"],
            "dispatched_at": d["dispatched_at"],
            "received_by": received_by.strip(),
            "received_at": fecha_recepcion,
            "in_movement_id": in_movement_id,
        }

        registrar_idempotencia(
            conn,
            idempotency_key=idempotency_key,
            operation_type="TRANSFER_CONFIRM",
            resource_id=transfer_id,
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


def cancelPendingTransfer(
    conn: sqlite3.Connection,
    transfer_id: str,
    cancelled_by: str,
    reason: str,
    idempotency_key: Optional[str] = None,
    auto_commit: bool = True,
) -> Dict[str, Any]:
    """Cancela un traslado pendiente y devuelve los fondos al origen."""
    if not cancelled_by or not cancelled_by.strip():
        raise ErrorTraslado("El responsable de cancelación es obligatorio.")
    if not reason or not reason.strip():
        raise ErrorTraslado("El motivo de cancelación es obligatorio.")

    payload_hash = calcular_hash_solicitud({
        "transfer_id": transfer_id,
        "cancelled_by": cancelled_by.strip(),
        "reason": reason.strip(),
    })
    previo = verificar_idempotencia(conn, idempotency_key, "TRANSFER_CANCEL", payload_hash)
    if previo is not None:
        return previo

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT t.id, t.transfer_id, t.journal_id, t.origin_fund_id, t.amount_cents, t.status,
               t.out_movement_id,
               fo.codigo as cod_origen, fo.nombre as nom_origen, fo.saldo_centavos as saldo_orig, fo.version as ver_orig
        FROM tesoreria_traslados t
        JOIN tesoreria_fondos fo ON t.origin_fund_id = fo.id
        WHERE t.transfer_id = ?
        """,
        (transfer_id,),
    )
    fila = cursor.fetchone()
    if not fila:
        cursor.close()
        raise ErrorTraslado(f"Traslado #{transfer_id} no encontrado.")

    d = dict(fila)
    if d["status"] != "PENDING_RECEIPT":
        cursor.close()
        raise ErrorTraslado(f"No se puede cancelar un traslado en estado '{d['status']}'.")

    ahora_utc = datetime.now(timezone.utc).isoformat()
    rev_movement_id = str(uuid.uuid4())

    try:
        nuevo_saldo_origen = addMoney(d["saldo_orig"], d["amount_cents"])

        # Restituir fondos en origen
        cursor.execute(
            """
            UPDATE tesoreria_fondos
            SET saldo_centavos = ?,
                version = version + 1,
                updated_at = ?,
                actualizado_por = ?
            WHERE id = ? AND version = ?
            """,
            (nuevo_saldo_origen, ahora_utc, cancelled_by.strip(), d["origin_fund_id"], d["ver_orig"]),
        )
        if cursor.rowcount == 0:
            raise ErrorTraslado("Conflicto de concurrencia al restituir saldo al fondo origen.")

        # Registrar movimiento de reversión vinculado al movimiento de salida original
        cursor.execute(
            """
            INSERT INTO tesoreria_movimientos (
                movement_id, idempotency_key, journal_id, fund_id, movement_type,
                amount_cents, currency, counterpart, reason, evidence_reference,
                created_by, terminal_id, status, occurred_at, recorded_at,
                is_reversal, reversal_of_id, reversed_amount_cents
            ) VALUES (?, ?, ?, ?, 'REVERSAL', ?, 'ARS', ?, ?, NULL, ?, 'TERM-01', 'POSTED', ?, ?, 1, ?, ?)
            """,
            (
                rev_movement_id,
                f"rev_{transfer_id}",
                d["journal_id"],
                d["origin_fund_id"],
                d["amount_cents"],
                f"Reversión de traslado #{transfer_id}",
                f"Cancelación: {reason.strip()}",
                cancelled_by.strip(),
                ahora_utc,
                ahora_utc,
                d["out_movement_id"],
                d["amount_cents"],
            ),
        )

        # Actualizar reversed_amount_cents en el movimiento de salida original
        if d.get("out_movement_id"):
            cursor.execute(
                """
                UPDATE tesoreria_movimientos
                SET reversed_amount_cents = ?
                WHERE movement_id = ?
                """,
                (d["amount_cents"], d["out_movement_id"]),
            )

        cursor.execute(
            """
            UPDATE tesoreria_traslados
            SET status = 'CANCELLED'
            WHERE transfer_id = ?
            """,
            (transfer_id,),
        )

        _registrar_auditoria(
            conn,
            entity_type="TRASLADO",
            entity_id=transfer_id,
            action="CANCEL",
            user_id=cancelled_by.strip(),
            details={"transfer_id": transfer_id, "reason": reason.strip()},
            timestamp_utc=ahora_utc,
        )

        resultado = {
            "transfer_id": transfer_id,
            "status": "CANCELLED",
            "cancelled_by": cancelled_by.strip(),
            "reason": reason.strip(),
            "reversal_movement_id": rev_movement_id,
        }

        registrar_idempotencia(
            conn,
            idempotency_key=idempotency_key,
            operation_type="TRANSFER_CANCEL",
            resource_id=transfer_id,
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


def listar_traslados(
    conn: sqlite3.Connection,
    limite: int = 50,
    status: Optional[str] = None,
    origin_fund_code: Optional[str] = None,
    destination_fund_code: Optional[str] = None,
    search: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Consulta la lista de traslados con sus fondos correspondientes y filtros opcionales."""
    cursor = conn.cursor()

    query = """
        SELECT t.id, t.transfer_id, t.journal_id, t.amount_cents, t.currency, t.status,
               fo.codigo as origin_fund_code, fo.nombre as origin_fund_name,
               fd.codigo as destination_fund_code, fd.nombre as destination_fund_name,
               t.dispatched_by, t.received_by, t.dispatched_at, t.received_at,
               t.reason, t.evidence_reference, t.out_movement_id, t.in_movement_id
        FROM tesoreria_traslados t
        JOIN tesoreria_fondos fo ON t.origin_fund_id = fo.id
        JOIN tesoreria_fondos fd ON t.destination_fund_id = fd.id
        WHERE 1=1
    """
    params: List[Any] = []

    if status and status.upper() != "ALL":
        query += " AND t.status = ?"
        params.append(status.upper())

    if origin_fund_code:
        query += " AND fo.codigo = ?"
        params.append(origin_fund_code)

    if destination_fund_code:
        query += " AND fd.codigo = ?"
        params.append(destination_fund_code)

    if search and search.strip():
        term = f"%{search.strip()}%"
        query += " AND (t.transfer_id LIKE ? OR t.reason LIKE ? OR t.dispatched_by LIKE ? OR t.received_by LIKE ? OR t.evidence_reference LIKE ?)"
        params.extend([term, term, term, term, term])

    query += " ORDER BY t.id DESC LIMIT ?"
    params.append(limite)

    cursor.execute(query, tuple(params))
    filas = cursor.fetchall()
    cursor.close()

    resultado = []
    for r in filas:
        d = dict(r)
        d["amount_formatted"] = formatMoney(d["amount_cents"])
        resultado.append(d)
    return resultado
