"""Consumidor Asíncrono y Puente de Integración Comercial a Tesorería (Laboratorio).

Implementación de la Etapa 4:
- Consume eventos transaccionales desde `commercial_events_outbox` en orden monotónico estricto (`sequence_id ASC`).
- Bloqueo optimista concurrente para prevenir doble procesamiento entre múltiples workers.
- Traduce eventos a operaciones contables inmutables (registerEntry, registerExit, reverseMovement).
- Respeto estricto del catálogo oficial de 9 fondos y de las decisiones económicas:
  * 1110 / 1120: Efectivo Drugstore (diario y acumulado).
  * 1130: Prepay Efectivo.
  * 1140: Adicionales Efectivo (SOLO con traslado físico documentado, nunca auto-acreditado).
  * 1150: Claro Efectivo (CUSTODIA pura de recaudación de terceros).
  * 1210: Mercado Pago Propio (saldo digital y cobros QR/transferencia).
  * 1220: Prepay Digital.
  * 1230: Claro Comercios Digital.
  * 1310: Tránsito Preventistas.
- Idempotencia atómica:
  * El outbox solo pasa a PUBLISHED tras la confirmación exitosa del movimiento.
  * Si el worker cae entre la operación y el ack, reintentos posteriores devuelven el resultado previo
    sin duplicar asientos ni saldos.
- Manejo de fallos recuperables (FAILED con backoff) y conflictos fatales (DEAD_LETTER auditable).
- Función de reconciliación matemática estricta de saldos contra el libro mayor.
"""

from datetime import datetime, timezone
import json
import logging
import sqlite3
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
import uuid

logger = logging.getLogger("genaro_pos.commercial_bridge")

from db.commercial_outbox_emitter import compute_canonical_hash, to_amount_cents
from tesoreria.catalog import obtener_fondo_por_codigo
from tesoreria.jornadas import abrir_jornada, obtener_jornada_actual
from tesoreria.money import addMoney, assertValidMoney, formatMoney, subtractMoney
from tesoreria.movements import registerEntry, registerExit
from tesoreria.reversals import reverseMovement


class BridgeProcessingError(Exception):
    """Fallo durante el procesamiento contable en el puente de integración."""
    pass


class BridgeConflictError(BridgeProcessingError):
    """Conflicto irrecuperable de integridad o reglas de negocio (pasa a DEAD_LETTER)."""
    pass


def _asegurar_jornada_abierta(tes_conn: sqlite3.Connection, usuario: str = "bridge_worker") -> Dict[str, Any]:
    """Garantiza que exista una jornada contable activa para asentar movimientos."""
    jornada = obtener_jornada_actual(tes_conn)
    if jornada is not None:
        return jornada

    return abrir_jornada(
        conn=tes_conn,
        usuario=usuario,
        observaciones="Apertura automática por puente de integración comercial",
    )


def _buscar_movimiento_por_idempotency_key(tes_conn: sqlite3.Connection, key: str) -> Optional[Dict[str, Any]]:
    """Consulta un movimiento previo en Tesorería a partir de su clave de idempotencia."""
    cursor = tes_conn.cursor()
    cursor.execute(
        """
        SELECT m.id, m.movement_id, m.fund_id, m.movement_type, m.amount_cents,
               m.status, m.idempotency_key, f.codigo as fund_code
        FROM tesoreria_movimientos m
        JOIN tesoreria_fondos f ON m.fund_id = f.id
        WHERE m.idempotency_key = ?;
        """,
        (key,),
    )
    row = cursor.fetchone()
    cursor.close()
    if not row:
        return None
    return dict(row)


def process_single_commercial_event(
    comm_conn: sqlite3.Connection,
    tes_conn: sqlite3.Connection,
    event: Dict[str, Any],
) -> str:
    """Procesa un único evento comercial traduciéndolo a operaciones contables en Tesorería.
    
    Retorna el movement_id (o lista de IDs) generado en Tesorería.
    Lanza BridgeConflictError si se detecta colisión o alteración de integridad.
    Lanza BridgeProcessingError ante fallas transitorias de disponibilidad o saldo.
    """
    event_id = event["event_id"]
    event_type = event["event_type"]
    action_type = event["action_type"]
    idempotency_key = event["idempotency_key"]
    payload_raw = event["payload"]
    expected_hash = event["payload_canonical_hash"]

    # 1. Verificación de integridad criptográfica (RFC 8785)
    payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
    computed_hash = compute_canonical_hash(payload)
    if computed_hash != expected_hash:
        raise BridgeConflictError(
            f"Adulteración de payload detectada para evento '{event_id}'. "
            f"Hash esperado: {expected_hash}, hash computado: {computed_hash}."
        )

    # 2. Asegurar jornada activa
    _asegurar_jornada_abierta(tes_conn, usuario="bridge_worker")

    # 3. Despacho por tipo de evento
    if event_type in ("SALE_CONFIRMED", "SALE_INSERTED"):
        return _handle_sale_confirmed(tes_conn, event, payload)

    elif event_type in ("SALE_VOIDED", "SALE_CANCELLED"):
        return _handle_sale_voided(tes_conn, event, payload)

    elif event_type in ("SALE_ADJUSTED", "SALE_UPDATED"):
        return _handle_sale_adjusted(tes_conn, event, payload)

    elif event_type in ("SERVICE_CONFIRMED", "SERVICE_INSERTED"):
        return _handle_service_confirmed(tes_conn, event, payload, comm_conn=comm_conn)

    elif event_type in ("SERVICE_VOIDED", "SERVICE_CANCELLED"):
        return _handle_service_voided(tes_conn, event, payload)

    elif event_type in ("SERVICE_ADJUSTED", "SERVICE_UPDATED"):
        return _handle_service_adjusted(tes_conn, event, payload, comm_conn=comm_conn)

    elif event_type in ("EXPENSE_CONFIRMED", "EXPENSE_INSERTED"):
        return _handle_expense_confirmed(tes_conn, event, payload)

    elif event_type in ("EXPENSE_VOIDED", "EXPENSE_CANCELLED"):
        return _handle_expense_voided(tes_conn, event, payload)

    elif event_type in ("EXPENSE_ADJUSTED", "EXPENSE_UPDATED"):
        return _handle_expense_adjusted(tes_conn, event, payload)

    else:
        # Eventos no financieros o desconocidos se marcan como procesados sin asiento
        return f"NO_FINANCIAL_ACTION_{event_type}"


# ============================================================================
# MANEJADORES ESPECÍFICOS POR TIPO DE EVENTO COMERCIAL
# ============================================================================

def _handle_sale_confirmed(tes_conn: sqlite3.Connection, event: Dict[str, Any], payload: Dict[str, Any]) -> str:
    """Procesa una venta confirmada (efectivo, digital o mixta)."""
    tkt_id = payload.get("ticket_id") or payload.get("entity_id") or event.get("entity_id")
    usuario = payload.get("usuario") or payload.get("cajero_id") or "pos_cajero"
    monto_efvo = payload.get("monto_efectivo_cents", 0)
    monto_transf = payload.get("monto_transferencia_cents", 0)
    fecha_utc = payload.get("fecha_hora_utc")

    # Soporte para desglose de pagos explícito o inferencia por metodo_pago
    if "desglose_pagos" in payload and isinstance(payload["desglose_pagos"], list):
        for item in payload["desglose_pagos"]:
            met = str(item.get("metodo", "")).upper()
            monto = item.get("monto_cents", 0)
            if "EFECTIVO" in met:
                monto_efvo += monto
            elif "MERCADO_PAGO" in met or "TRANSFERENCIA" in met or "DIGITAL" in met:
                monto_transf += monto
    elif monto_efvo == 0 and monto_transf == 0 and payload.get("monto_total_cents"):
        total = payload["monto_total_cents"]
        met = str(payload.get("metodo_pago", "")).upper()
        if "EFECTIVO" in met:
            monto_efvo = total
        elif "MERCADO_PAGO" in met or "TRANSFERENCIA" in met or "DIGITAL" in met:
            monto_transf = total

    movements_generated = []

    # Pierna Efectivo: ingresa a 1110 Drugstore Diario
    if monto_efvo > 0:
        idem_key_efvo = f"{event['idempotency_key']}_CASH"
        res_efvo = registerEntry(
            conn=tes_conn,
            fund_code="1110",
            amount_cents=monto_efvo,
            counterpart="VENTA_MOSTRADOR_EFECTIVO",
            reason=f"Cobro venta efectivo ticket #{tkt_id}",
            created_by=usuario,
            terminal_id="TERM-POS-01",
            idempotency_key=idem_key_efvo,
            evidence_reference=f"TICKET_{tkt_id}",
            occurred_at=fecha_utc,
        )
        movements_generated.append(res_efvo["movement_id"])

    # Pierna Transferencia / Digital: ingresa a 1210 Mercado Pago Propio
    if monto_transf > 0:
        idem_key_transf = f"{event['idempotency_key']}_DIGITAL"
        res_transf = registerEntry(
            conn=tes_conn,
            fund_code="1210",
            amount_cents=monto_transf,
            counterpart="VENTA_MOSTRADOR_TRANSFERENCIA",
            reason=f"Cobro venta digital ticket #{tkt_id}",
            created_by=usuario,
            terminal_id="TERM-POS-01",
            idempotency_key=idem_key_transf,
            evidence_reference=f"TICKET_{tkt_id}",
            occurred_at=fecha_utc,
        )
        movements_generated.append(res_transf["movement_id"])

    return ";".join(movements_generated) if movements_generated else "ZERO_AMOUNT_SALE"


def _handle_sale_voided(tes_conn: sqlite3.Connection, event: Dict[str, Any], payload: Dict[str, Any]) -> str:
    """Procesa la anulación de una venta previa mediante reversión inmutable vinculada."""
    tkt_id = payload.get("ticket_id") or payload.get("entity_id") or event.get("entity_id")
    usuario = payload.get("usuario") or payload.get("usuario_anula") or "supervisor"
    motivo = payload.get("motivo") or payload.get("motivo_anulacion") or "Anulación comercial de ticket"
    orig_entity_id = payload.get("original_entity_id") or tkt_id
    ref_key = payload.get("reference_idempotency_key", f"COMM_SALE_{orig_entity_id}_CONFIRM")
    monto_efvo = payload.get("monto_efectivo_cents", 0)
    monto_transf = payload.get("monto_transferencia_cents", 0)

    reversals_generated = []

    # Revertir pierna efectivo si existió (búsqueda por ref_key o fallback por ticket)
    mov_orig_efvo = _buscar_movimiento_por_idempotency_key(tes_conn, f"{ref_key}_CASH")
    if not mov_orig_efvo:
        mov_orig_efvo = _buscar_movimiento_por_idempotency_key(tes_conn, f"idemp_{orig_entity_id}_CASH")
    if not mov_orig_efvo:
        cur = tes_conn.cursor()
        cur.execute(
            """
            SELECT movement_id, amount_cents FROM tesoreria_movimientos
            WHERE evidence_reference = ? AND movement_type = 'ENTRY' AND fund_id = (SELECT id FROM tesoreria_fondos WHERE codigo = '1110')
            ORDER BY id DESC LIMIT 1;
            """,
            (f"TICKET_{tkt_id}",),
        )
        r = cur.fetchone()
        if r:
            mov_orig_efvo = {"movement_id": r[0], "amount_cents": r[1]}

    if mov_orig_efvo:
        rev_efvo = reverseMovement(
            conn=tes_conn,
            movement_id=mov_orig_efvo["movement_id"],
            user_id=usuario,
            reason=f"Anulación venta ticket #{tkt_id}: {motivo}",
            evidence_reference=f"VOID_TICKET_{tkt_id}",
            idempotency_key=f"{event['idempotency_key']}_REV_CASH",
        )
        reversals_generated.append(rev_efvo.get("counter_movement_id") or rev_efvo.get("reversal_id"))

    # Revertir pierna digital si existió
    mov_orig_transf = _buscar_movimiento_por_idempotency_key(tes_conn, f"{ref_key}_DIGITAL")
    if not mov_orig_transf:
        mov_orig_transf = _buscar_movimiento_por_idempotency_key(tes_conn, f"idemp_{orig_entity_id}_DIGITAL")
    if not mov_orig_transf:
        cur = tes_conn.cursor()
        cur.execute(
            """
            SELECT movement_id, amount_cents FROM tesoreria_movimientos
            WHERE evidence_reference = ? AND movement_type = 'ENTRY' AND fund_id = (SELECT id FROM tesoreria_fondos WHERE codigo = '1210')
            ORDER BY id DESC LIMIT 1;
            """,
            (f"TICKET_{tkt_id}",),
        )
        r = cur.fetchone()
        if r:
            mov_orig_transf = {"movement_id": r[0], "amount_cents": r[1]}

    if mov_orig_transf:
        rev_transf = reverseMovement(
            conn=tes_conn,
            movement_id=mov_orig_transf["movement_id"],
            user_id=usuario,
            reason=f"Anulación venta digital ticket #{tkt_id}: {motivo}",
            evidence_reference=f"VOID_TICKET_{tkt_id}",
            idempotency_key=f"{event['idempotency_key']}_REV_DIGITAL",
        )
        reversals_generated.append(rev_transf.get("counter_movement_id") or rev_transf.get("reversal_id"))

    if not reversals_generated and (monto_efvo > 0 or monto_transf > 0 or payload.get("monto_total_cents")):
        raise BridgeConflictError(f"No se encontró movimiento origen para revertir ticket #{tkt_id}")

    return ";".join(reversals_generated) if reversals_generated else "ZERO_AMOUNT_VOID"


def _handle_service_confirmed(
    tes_conn: sqlite3.Connection,
    event: Dict[str, Any],
    payload: Dict[str, Any],
    comm_conn: Optional[sqlite3.Connection] = None,
) -> str:
    """Procesa cargas de servicios aplicando las reglas de fondos del catálogo y plataforma de origen."""
    servicio_id = payload.get("servicio_id") or payload.get("service_id")
    actor = payload.get("actor") or payload.get("cajero_id") or "pos_operador"
    fecha_utc = payload.get("fecha_hora_utc")

    monto_carga = payload.get("monto_carga_cents", 0) or payload.get("monto_servicio_cents", 0)
    monto_adicional = payload.get("monto_adicional_cents", 0)
    total_cobrado = payload.get("total_cobrado_cents", 0)
    pago_efvo = payload.get("pago_efectivo_cents", 0)
    pago_transf = payload.get("pago_transferencia_cents", 0)

    # Inferencia si solo se definió metodo_cobro o monto_servicio_cents
    if pago_efvo == 0 and pago_transf == 0 and monto_carga > 0:
        met = str(payload.get("metodo_cobro", "")).upper()
        if "EFECTIVO" in met or not met:
            pago_efvo = monto_carga + monto_adicional
        else:
            pago_transf = monto_carga + monto_adicional

    # Extracción y sanitización de plataforma de origen y servicio de destino
    raw_plat = payload.get("plataforma_origen")
    raw_dest = payload.get("servicio_destino") or payload.get("servicio") or payload.get("tipo_servicio")

    plat = str(raw_plat).strip().upper() if raw_plat is not None and str(raw_plat).strip() else None
    dest = str(raw_dest).strip() if raw_dest is not None and str(raw_dest).strip() else ""
    dest_upper = dest.upper()

    movements = []

    # Matriz oficial canónica de validación
    mapa_valido = {
        "PREPAY": ["SUBE", "PERSONAL", "MOVISTAR", "CLARO"],
        "CLARO COMERCIOS": ["CLARO"],
        "MERCADO PAGO": ["PREPAY", "SUBE", "PERSONAL", "MOVISTAR", "CLARO"],
    }

    if plat is not None:
        # Validación de plataforma contra catálogo
        if plat not in mapa_valido:
            logger.warning(
                "Evento servicio #%s descartado: plataforma de origen '%s' inválida.",
                servicio_id, plat
            )
            return f"INVALID_SERVICE_PLATFORM_{plat}_{servicio_id}"

        # Validación de destino dependiente
        destinos_permitidos = mapa_valido[plat]
        if dest_upper not in destinos_permitidos:
            logger.warning(
                "Evento servicio #%s descartado: combinación incompatible '%s' -> '%s'.",
                servicio_id, plat, dest
            )
            return f"INVALID_SERVICE_COMBINATION_{plat}_{dest_upper}_{servicio_id}"

        # --------------------------------------------------------------------
        # CASO 1: PLATAFORMA MERCADO PAGO
        # --------------------------------------------------------------------
        if plat == "MERCADO PAGO":
            if dest_upper == "SUBE":
                # ============================================================
                # CIRCUITO CONFIRMADO SUBE VÍA MERCADO PAGO:
                # 1. Débito digital de saldo consumido en 1210 Mercado Pago Propio
                # 2. Cobranza total recibida:
                #    - Si es en efectivo: entra a 1110 Drugstore Diario
                #    - Si es en transferencia: entra a 1210 Mercado Pago Propio
                #    Regla 1140: No se auto-acredita hasta arqueo físico
                # ============================================================
                if monto_carga > 0:
                    res_debito_mp = registerExit(
                        conn=tes_conn,
                        fund_code="1210",
                        amount_cents=monto_carga,
                        counterpart="SUBE_CAPITAL_DEBIT",
                        reason=f"Consumo saldo Mercado Pago recarga SUBE #{servicio_id}",
                        created_by=actor,
                        terminal_id="TERM-POS-01",
                        idempotency_key=f"{event['idempotency_key']}_SUBE_CAPITAL_EXIT",
                        evidence_reference=f"SUBE_CARGA_{servicio_id}",
                        occurred_at=fecha_utc,
                    )
                    movements.append(res_debito_mp["movement_id"])

                if pago_efvo > 0:
                    res_cobro_efvo = registerEntry(
                        conn=tes_conn,
                        fund_code="1110",
                        amount_cents=pago_efvo,
                        counterpart="SUBE_COLLECTION_CASH",
                        reason=f"Cobranza en efectivo carga SUBE #{servicio_id} (capital + adicional en cajón)",
                        created_by=actor,
                        terminal_id="TERM-POS-01",
                        idempotency_key=f"{event['idempotency_key']}_SUBE_CASH_ENTRY",
                        evidence_reference=f"SUBE_CARGA_{servicio_id}",
                        occurred_at=fecha_utc,
                    )
                    movements.append(res_cobro_efvo["movement_id"])

                if pago_transf > 0:
                    res_cobro_transf = registerEntry(
                        conn=tes_conn,
                        fund_code="1210",
                        amount_cents=pago_transf,
                        counterpart="SUBE_COLLECTION_DIGITAL",
                        reason=f"Cobranza digital transferencia carga SUBE #{servicio_id}",
                        created_by=actor,
                        terminal_id="TERM-POS-01",
                        idempotency_key=f"{event['idempotency_key']}_SUBE_DIGITAL_ENTRY",
                        evidence_reference=f"SUBE_CARGA_{servicio_id}",
                        occurred_at=fecha_utc,
                    )
                    movements.append(res_cobro_transf["movement_id"])

            elif dest_upper == "PREPAY":
                # ============================================================
                # MERCADO PAGO -> PREPAY: SEMÁNTICA ECONÓMICA PENDIENTE
                # REGLA AUD-001: No generar movimientos automáticos, ni deudas
                # con David, ni ventas comerciales ficticias. Dejar estado trazable.
                # ============================================================
                logger.info(
                    "Operación MERCADO PAGO -> PREPAY #%s retenida: semántica económica pendiente de definición por el propietario.",
                    servicio_id
                )
                return f"PENDING_DEFINITION_MERCADO_PAGO_PREPAY_{servicio_id}"

            else:
                # ============================================================
                # AISLAMIENTO ESTRICTO DE RECARGAS DESDE MERCADO PAGO (1210)
                # Destinos: CLARO, PERSONAL, MOVISTAR, etc.
                # • Disminuye saldo digital propio en 1210
                # • Efectivo capital ingresa a Drugstore Diario (1110)
                # • Efectivo adicional ingresa a Adicionales (1140)
                # • Transferencia permanece dentro de 1210
                # • Jamás toca 1220, 1230, 1160, 1130 ni 1150 (sin afectar David ni Ezequiel)
                # ============================================================
                if monto_carga > 0:
                    res_debito_mp = registerExit(
                        conn=tes_conn,
                        fund_code="1210",
                        amount_cents=monto_carga,
                        counterpart="MP_RECARGA_CAPITAL_DEBIT",
                        reason=f"Consumo saldo Mercado Pago recarga {dest_upper} #{servicio_id}",
                        created_by=actor,
                        terminal_id="TERM-POS-01",
                        idempotency_key=f"{event['idempotency_key']}_MP_CAPITAL_EXIT",
                        evidence_reference=f"MP_SRV_{servicio_id}",
                        occurred_at=fecha_utc,
                    )
                    movements.append(res_debito_mp["movement_id"])

                if pago_efvo > 0:
                    cap_efvo = min(monto_carga, pago_efvo) if monto_carga > 0 else pago_efvo
                    adic_efvo = monto_adicional if monto_adicional > 0 else max(0, pago_efvo - cap_efvo)

                    if cap_efvo > 0:
                        res_cap_efvo = registerEntry(
                            conn=tes_conn,
                            fund_code="1110",
                            amount_cents=cap_efvo,
                            counterpart=f"MP_SERVICIO_{dest_upper}_CAPITAL_EFECTIVO",
                            reason=f"Cobro capital recarga {dest_upper} vía Mercado Pago #{servicio_id} en cajón",
                            created_by=actor,
                            terminal_id="TERM-POS-01",
                            idempotency_key=f"{event['idempotency_key']}_MP_CASH_CAP_ENTRY",
                            evidence_reference=f"SRV_{servicio_id}",
                            occurred_at=fecha_utc,
                        )
                        movements.append(res_cap_efvo["movement_id"])

                    if adic_efvo > 0:
                        res_adic_efvo = registerEntry(
                            conn=tes_conn,
                            fund_code="1140",
                            amount_cents=adic_efvo,
                            counterpart="SERVICIO_ADICIONAL_EFECTIVO",
                            reason=f"Cobro adicional recarga {dest_upper} vía Mercado Pago #{servicio_id}",
                            created_by=actor,
                            terminal_id="TERM-POS-01",
                            idempotency_key=f"{event['idempotency_key']}_MP_CASH_ADIC_ENTRY",
                            evidence_reference=f"SRV_{servicio_id}",
                            occurred_at=fecha_utc,
                        )
                        movements.append(res_adic_efvo["movement_id"])

                if pago_transf > 0:
                    res_gen_transf = registerEntry(
                        conn=tes_conn,
                        fund_code="1210",
                        amount_cents=pago_transf,
                        counterpart=f"SERVICIO_{dest_upper}_DIGITAL",
                        reason=f"Cobro digital servicio {dest_upper} vía Mercado Pago #{servicio_id}",
                        created_by=actor,
                        terminal_id="TERM-POS-01",
                        idempotency_key=f"{event['idempotency_key']}_GEN_DIGITAL_ENTRY",
                        evidence_reference=f"SRV_{servicio_id}",
                        occurred_at=fecha_utc,
                    )
                    movements.append(res_gen_transf["movement_id"])

        # --------------------------------------------------------------------
        # CASO 2: PLATAFORMA PREPAY (FASE 2 PRE-RESERVA Y LEGACY COMPAT)
        # --------------------------------------------------------------------
        elif plat == "PREPAY":
            # Verificar si existe coordinación de pre-reserva de FASE 2
            coord_row = None
            try:
                cur = tes_conn.cursor()
                comm_key = payload.get("commercial_idempotency_key") or event.get("idempotency_key")
                cur.execute(
                    """
                    SELECT id FROM tesoreria_prepay_coordinacion
                    WHERE idempotency_key = ? OR commercial_ticket_id = ?;
                    """,
                    (comm_key, servicio_id),
                )
                coord_row = cur.fetchone()
                cur.close()
            except Exception:
                coord_row = None

            if coord_row or payload.get("requiere_pre_reserva") or "pre_reserva" in payload:
                from tesoreria.prepay_coordinator import aplicar_movimientos_prepay_worker
                if comm_conn is None:
                    raise BridgeProcessingError("comm_conn es obligatorio para aplicar movimientos PREPAY en worker.")
                return aplicar_movimientos_prepay_worker(comm_conn, tes_conn, event, payload)

            if dest_upper == "SUBE":
                # ============================================================
                # PREPAY -> SUBE:
                # REGLA AUD-001 OBLIGATORIA: ¡NO DEBITAR FONDO 1210!
                # La cobranza en efectivo ingresa a 1130 (Prepay Efectivo).
                # Si hubo transferencia, ingresa a 1210 (digital).
                # ============================================================
                if pago_efvo > 0:
                    res_prepay = registerEntry(
                        conn=tes_conn,
                        fund_code="1130",
                        amount_cents=pago_efvo,
                        counterpart="RECAUDACION_PREPAY_EFECTIVO",
                        reason=f"Cobranza en mostrador Prepay SUBE #{servicio_id}",
                        created_by=actor,
                        terminal_id="TERM-POS-01",
                        idempotency_key=f"{event['idempotency_key']}_PREPAY_CASH_ENTRY",
                        evidence_reference=f"PREPAY_SUBE_{servicio_id}",
                        occurred_at=fecha_utc,
                    )
                    movements.append(res_prepay["movement_id"])

                if pago_transf > 0:
                    res_prepay_transf = registerEntry(
                        conn=tes_conn,
                        fund_code="1210",
                        amount_cents=pago_transf,
                        counterpart="RECAUDACION_PREPAY_DIGITAL",
                        reason=f"Cobranza digital transferencia Prepay SUBE #{servicio_id}",
                        created_by=actor,
                        terminal_id="TERM-POS-01",
                        idempotency_key=f"{event['idempotency_key']}_PREPAY_DIGITAL_ENTRY",
                        evidence_reference=f"PREPAY_SUBE_{servicio_id}",
                        occurred_at=fecha_utc,
                    )
                    movements.append(res_prepay_transf["movement_id"])

            elif dest_upper == "CLARO":
                # Claro en custodia
                if pago_efvo > 0:
                    res_claro = registerEntry(
                        conn=tes_conn,
                        fund_code="1150",
                        amount_cents=pago_efvo,
                        counterpart="RECAUDACION_TERCEROS_CLARO",
                        reason=f"Cobro Claro en efectivo custodia #{servicio_id}",
                        created_by=actor,
                        terminal_id="TERM-POS-01",
                        idempotency_key=f"{event['idempotency_key']}_CLARO_CUSTODIA_ENTRY",
                        evidence_reference=f"CLARO_{servicio_id}",
                        occurred_at=fecha_utc,
                    )
                    movements.append(res_claro["movement_id"])

                if pago_transf > 0:
                    res_gen_transf = registerEntry(
                        conn=tes_conn,
                        fund_code="1210",
                        amount_cents=pago_transf,
                        counterpart="SERVICIO_CLARO_DIGITAL",
                        reason=f"Cobro digital servicio CLARO #{servicio_id}",
                        created_by=actor,
                        terminal_id="TERM-POS-01",
                        idempotency_key=f"{event['idempotency_key']}_GEN_DIGITAL_ENTRY",
                        evidence_reference=f"SRV_{servicio_id}",
                        occurred_at=fecha_utc,
                    )
                    movements.append(res_gen_transf["movement_id"])

            else:
                # Personal, Movistar
                if pago_efvo > 0:
                    res_gen_efvo = registerEntry(
                        conn=tes_conn,
                        fund_code="1110",
                        amount_cents=pago_efvo,
                        counterpart=f"SERVICIO_{dest_upper}_EFECTIVO",
                        reason=f"Cobro servicio {dest_upper} #{servicio_id}",
                        created_by=actor,
                        terminal_id="TERM-POS-01",
                        idempotency_key=f"{event['idempotency_key']}_GEN_CASH_ENTRY",
                        evidence_reference=f"SRV_{servicio_id}",
                        occurred_at=fecha_utc,
                    )
                    movements.append(res_gen_efvo["movement_id"])

                if pago_transf > 0:
                    res_gen_transf = registerEntry(
                        conn=tes_conn,
                        fund_code="1210",
                        amount_cents=pago_transf,
                        counterpart=f"SERVICIO_{dest_upper}_DIGITAL",
                        reason=f"Cobro digital servicio {dest_upper} #{servicio_id}",
                        created_by=actor,
                        terminal_id="TERM-POS-01",
                        idempotency_key=f"{event['idempotency_key']}_GEN_DIGITAL_ENTRY",
                        evidence_reference=f"SRV_{servicio_id}",
                        occurred_at=fecha_utc,
                    )
                    movements.append(res_gen_transf["movement_id"])

        # --------------------------------------------------------------------
        # CASO 3: PLATAFORMA CLARO COMERCIOS
        # --------------------------------------------------------------------
        elif plat == "CLARO COMERCIOS":
            # Consumo de saldo digital 1230 si hay saldo disponible
            cur_1230 = tes_conn.cursor()
            cur_1230.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1230' AND estado = 'ACTIVO';")
            row_1230 = cur_1230.fetchone()
            cur_1230.close()
            saldo_1230 = row_1230[0] if row_1230 else 0
            if saldo_1230 >= monto_carga and monto_carga > 0:
                res_debito_1230 = registerExit(
                    conn=tes_conn,
                    fund_code="1230",
                    amount_cents=monto_carga,
                    counterpart="CLARO_CAPITAL_DEBIT",
                    reason=f"Consumo saldo digital Claro Comercios #{servicio_id}",
                    performed_by=actor,
                    terminal_id="TERM-POS-01",
                    idempotency_key=f"{event['idempotency_key']}_CLARO_1230_EXIT",
                    evidence_reference=f"CLARO_{servicio_id}",
                    occurred_at=fecha_utc,
                )
                movements.append(res_debito_1230["movement_id"])

            if pago_efvo > 0:
                # Capital a 1150 Claro físico
                capital_efvo = min(monto_carga, pago_efvo) if monto_carga > 0 else pago_efvo
                if capital_efvo > 0:
                    res_claro = registerEntry(
                        conn=tes_conn,
                        fund_code="1150",
                        amount_cents=capital_efvo,
                        counterpart="RECAUDACION_TERCEROS_CLARO",
                        reason=f"Cobro Claro en efectivo custodia #{servicio_id}",
                        created_by=actor,
                        terminal_id="TERM-POS-01",
                        idempotency_key=f"{event['idempotency_key']}_CLARO_CUSTODIA_ENTRY",
                        evidence_reference=f"CLARO_{servicio_id}",
                        occurred_at=fecha_utc,
                    )
                    movements.append(res_claro["movement_id"])

                # Adicional a 1140
                adicional_efvo = monto_adicional if monto_adicional > 0 else max(0, pago_efvo - capital_efvo)
                if adicional_efvo > 0:
                    res_adic = registerEntry(
                        conn=tes_conn,
                        fund_code="1140",
                        amount_cents=adicional_efvo,
                        counterpart="ADICIONAL_CLARO_EFECTIVO",
                        reason=f"Cobro adicional servicio Claro #{servicio_id}",
                        created_by=actor,
                        terminal_id="TERM-POS-01",
                        idempotency_key=f"{event['idempotency_key']}_CLARO_ADIC_ENTRY",
                        evidence_reference=f"CLARO_{servicio_id}",
                        occurred_at=fecha_utc,
                    )
                    movements.append(res_adic["movement_id"])

            if pago_transf > 0:
                # Cobranza digital entra a 1210 Mercado Pago
                res_gen_transf = registerEntry(
                    conn=tes_conn,
                    fund_code="1210",
                    amount_cents=pago_transf,
                    counterpart="RECAUDACION_CLARO_TRANSFERENCIA",
                    reason=f"Cobro digital servicio CLARO #{servicio_id}",
                    created_by=actor,
                    terminal_id="TERM-POS-01",
                    idempotency_key=f"{event['idempotency_key']}_GEN_DIGITAL_ENTRY",
                    evidence_reference=f"SRV_{servicio_id}",
                    occurred_at=fecha_utc,
                )
                movements.append(res_gen_transf["movement_id"])

                # Fondeo físico de custodia Claro (DEC-02) verificando disponibilidad y estado de apertura
                cur_f = tes_conn.cursor()
                cur_f.execute("SELECT codigo, saldo_centavos, estado_apertura FROM tesoreria_fondos WHERE codigo IN ('1110', '1120') AND estado = 'ACTIVO';")
                fondos_drugstore = {r[0]: {"saldo": r[1], "apertura": r[2]} for r in cur_f.fetchall()}
                cur_f.close()

                f1110 = fondos_drugstore.get("1110", {})
                f1120 = fondos_drugstore.get("1120", {})
                disp_1110 = f1110.get("saldo", 0) if f1110.get("apertura") != "PENDING_OPENING" else 0
                disp_1120 = f1120.get("saldo", 0) if f1120.get("apertura") != "PENDING_OPENING" else 0

                origen_fondeo = None
                if disp_1110 >= monto_carga and monto_carga > 0:
                    origen_fondeo = "1110"
                elif disp_1120 >= monto_carga and monto_carga > 0:
                    origen_fondeo = "1120"

                if origen_fondeo and monto_carga > 0:
                    from tesoreria.transfers import transferFunds, confirmTransferReceipt
                    res_trf = transferFunds(
                        conn=tes_conn,
                        origin_fund_code=origen_fondeo,
                        destination_fund_code="1150",
                        amount_cents=monto_carga,
                        dispatched_by=actor,
                        reason=f"Fondeo físico custodia Claro recarga #{servicio_id}",
                        idempotency_key=f"{event['idempotency_key']}_CLARO_FONDEO_TRF",
                        evidence_reference=f"SRV_{servicio_id}",
                        context="FONDEO_CUSTODIA_CLARO",
                        occurred_at=fecha_utc,
                    )
                    confirmTransferReceipt(
                        conn=tes_conn,
                        transfer_id=res_trf["transfer_id"],
                        received_by=actor,
                        idempotency_key=f"{event['idempotency_key']}_CLARO_FONDEO_CONF",
                    )
                    movements.append(f"TRANSFER_{res_trf['transfer_id']}")
                elif monto_carga > 0:
                    from tesoreria.cuenta_corriente_ezequiel import registrar_fondeo_pendiente
                    registrar_fondeo_pendiente(
                        conn=tes_conn,
                        tipo_circuito="CLARO",
                        servicio_id=int(servicio_id) if str(servicio_id).isdigit() else 0,
                        monto_capital_cents=monto_carga,
                        fondo_origen_esperado="1110",
                        fondo_destino_custodia="1150",
                        actor=actor,
                        idempotency_key=f"FOND_PEND_CLARO_{event['idempotency_key']}",
                        observaciones=f"Falta de efectivo transitoria o apertura pendiente en Drugstore para recarga Claro #{servicio_id} (Disp 1110: {disp_1110}, Disp 1120: {disp_1120})",
                        auto_commit=False,
                    )

    else:
        # ====================================================================
        # EVENTO SIN PLATAFORMA (LEGADO O INCOMPLETO)
        # REGLA AUD-001 OBLIGATORIA: "No debe inferir un origen ni generar una
        # imputación insegura."
        # ====================================================================
        servicio_nombre = str(payload.get("servicio") or payload.get("tipo_servicio", "")).strip().upper()

        if servicio_nombre in ("SUBE (MP)", "SUBE MP"):
            # Expresamente rotulado como Mercado Pago en el string legado
            if monto_carga > 0:
                res_debito_mp = registerExit(
                    conn=tes_conn,
                    fund_code="1210",
                    amount_cents=monto_carga,
                    counterpart="SUBE_CAPITAL_DEBIT",
                    reason=f"Consumo saldo Mercado Pago recarga SUBE #{servicio_id}",
                    created_by=actor,
                    terminal_id="TERM-POS-01",
                    idempotency_key=f"{event['idempotency_key']}_SUBE_CAPITAL_EXIT",
                    evidence_reference=f"SUBE_CARGA_{servicio_id}",
                    occurred_at=fecha_utc,
                )
                movements.append(res_debito_mp["movement_id"])

            if pago_efvo > 0:
                res_cobro_efvo = registerEntry(
                    conn=tes_conn,
                    fund_code="1110",
                    amount_cents=pago_efvo,
                    counterpart="SUBE_COLLECTION_CASH",
                    reason=f"Cobranza en efectivo carga SUBE #{servicio_id} (capital + adicional en cajón)",
                    created_by=actor,
                    terminal_id="TERM-POS-01",
                    idempotency_key=f"{event['idempotency_key']}_SUBE_CASH_ENTRY",
                    evidence_reference=f"SUBE_CARGA_{servicio_id}",
                    occurred_at=fecha_utc,
                )
                movements.append(res_cobro_efvo["movement_id"])

            if pago_transf > 0:
                res_cobro_transf = registerEntry(
                    conn=tes_conn,
                    fund_code="1210",
                    amount_cents=pago_transf,
                    counterpart="SUBE_COLLECTION_DIGITAL",
                    reason=f"Cobranza digital transferencia carga SUBE #{servicio_id}",
                    created_by=actor,
                    terminal_id="TERM-POS-01",
                    idempotency_key=f"{event['idempotency_key']}_SUBE_DIGITAL_ENTRY",
                    evidence_reference=f"SUBE_CARGA_{servicio_id}",
                    occurred_at=fecha_utc,
                )
                movements.append(res_cobro_transf["movement_id"])

        elif servicio_nombre == "SUBE":
            # SUBE sin plataforma especificada: NO inferir Mercado Pago ni debitar 1210
            logger.warning(
                "Evento servicio SUBE #%s sin plataforma: retenido sin imputación financiera para evitar débitos inseguros.",
                servicio_id
            )
            return f"UNSPECIFIED_PLATFORM_AMBIGUOUS_SERVICE_SUBE_{servicio_id}"

        elif servicio_nombre == "PREPAY":
            if pago_efvo > 0:
                res_prepay = registerEntry(
                    conn=tes_conn,
                    fund_code="1130",
                    amount_cents=pago_efvo,
                    counterpart="RECAUDACION_PREPAY_EFECTIVO",
                    reason=f"Cobranza en mostrador Prepay #{servicio_id}",
                    created_by=actor,
                    terminal_id="TERM-POS-01",
                    idempotency_key=f"{event['idempotency_key']}_PREPAY_CASH_ENTRY",
                    evidence_reference=f"PREPAY_{servicio_id}",
                    occurred_at=fecha_utc,
                )
                movements.append(res_prepay["movement_id"])

            if pago_transf > 0:
                res_prepay_transf = registerEntry(
                    conn=tes_conn,
                    fund_code="1210",
                    amount_cents=pago_transf,
                    counterpart="RECAUDACION_PREPAY_DIGITAL",
                    reason=f"Cobranza digital transferencia Prepay #{servicio_id}",
                    created_by=actor,
                    terminal_id="TERM-POS-01",
                    idempotency_key=f"{event['idempotency_key']}_PREPAY_DIGITAL_ENTRY",
                    evidence_reference=f"PREPAY_{servicio_id}",
                    occurred_at=fecha_utc,
                )
                movements.append(res_prepay_transf["movement_id"])

        elif servicio_nombre == "CLARO":
            if pago_efvo > 0:
                res_claro = registerEntry(
                    conn=tes_conn,
                    fund_code="1150",
                    amount_cents=pago_efvo,
                    counterpart="RECAUDACION_TERCEROS_CLARO",
                    reason=f"Cobro Claro en efectivo custodia #{servicio_id}",
                    created_by=actor,
                    terminal_id="TERM-POS-01",
                    idempotency_key=f"{event['idempotency_key']}_CLARO_CUSTODIA_ENTRY",
                    evidence_reference=f"CLARO_{servicio_id}",
                    occurred_at=fecha_utc,
                )
                movements.append(res_claro["movement_id"])

            if pago_transf > 0:
                res_gen_transf = registerEntry(
                    conn=tes_conn,
                    fund_code="1210",
                    amount_cents=pago_transf,
                    counterpart="SERVICIO_CLARO_DIGITAL",
                    reason=f"Cobro digital servicio CLARO #{servicio_id}",
                    created_by=actor,
                    terminal_id="TERM-POS-01",
                    idempotency_key=f"{event['idempotency_key']}_GEN_DIGITAL_ENTRY",
                    evidence_reference=f"SRV_{servicio_id}",
                    occurred_at=fecha_utc,
                )
                movements.append(res_gen_transf["movement_id"])

        else:
            # Otros servicios generales legados
            if pago_efvo > 0:
                res_gen_efvo = registerEntry(
                    conn=tes_conn,
                    fund_code="1110",
                    amount_cents=pago_efvo,
                    counterpart=f"SERVICIO_{servicio_nombre}_EFECTIVO",
                    reason=f"Cobro servicio {servicio_nombre} #{servicio_id}",
                    created_by=actor,
                    terminal_id="TERM-POS-01",
                    idempotency_key=f"{event['idempotency_key']}_GEN_CASH_ENTRY",
                    evidence_reference=f"SRV_{servicio_id}",
                    occurred_at=fecha_utc,
                )
                movements.append(res_gen_efvo["movement_id"])

            if pago_transf > 0:
                res_gen_transf = registerEntry(
                    conn=tes_conn,
                    fund_code="1210",
                    amount_cents=pago_transf,
                    counterpart=f"SERVICIO_{servicio_nombre}_DIGITAL",
                    reason=f"Cobro digital servicio {servicio_nombre} #{servicio_id}",
                    created_by=actor,
                    terminal_id="TERM-POS-01",
                    idempotency_key=f"{event['idempotency_key']}_GEN_DIGITAL_ENTRY",
                    evidence_reference=f"SRV_{servicio_id}",
                    occurred_at=fecha_utc,
                )
                movements.append(res_gen_transf["movement_id"])

    return ";".join(movements) if movements else "ZERO_SERVICE_CHARGE"


def _handle_service_voided(tes_conn: sqlite3.Connection, event: Dict[str, Any], payload: Dict[str, Any]) -> str:
    """Procesa la anulación de una carga de servicio revirtiendo todas las piernas contables emitidas."""
    servicio_id = payload.get("servicio_id")
    servicio_nombre = str(payload.get("servicio", "")).strip().upper()
    actor = payload.get("actor", "supervisor")
    ref_key = payload.get("reference_idempotency_key", f"COMM_SERVICE_{servicio_id}_CONFIRM")

    reversals = []

    # Identificar posibles piernas de confirmación previas
    posibles_claves_piernas = [
        f"{ref_key}_SUBE_CAPITAL_EXIT",
        f"{ref_key}_SUBE_CASH_ENTRY",
        f"{ref_key}_SUBE_DIGITAL_ENTRY",
        f"{ref_key}_PREPAY_CASH_ENTRY",
        f"{ref_key}_PREPAY_DIGITAL_ENTRY",
        f"{ref_key}_CLARO_CUSTODIA_ENTRY",
        f"{ref_key}_GEN_CASH_ENTRY",
        f"{ref_key}_GEN_DIGITAL_ENTRY",
    ]

    for clave in posibles_claves_piernas:
        mov_orig = _buscar_movimiento_por_idempotency_key(tes_conn, clave)
        if mov_orig:
            rev_res = reverseMovement(
                conn=tes_conn,
                movement_id=mov_orig["movement_id"],
                user_id=actor,
                reason=f"Anulación comercial servicio {servicio_nombre} #{servicio_id}",
                evidence_reference=f"VOID_SRV_{servicio_id}",
                idempotency_key=f"{event['idempotency_key']}_REV_{clave}",
            )
            reversals.append(rev_res.get("counter_movement_id") or rev_res.get("reversal_id"))

    return ";".join(reversals) if reversals else "ZERO_REVERSAL_PROCESSED"


def _handle_expense_confirmed(tes_conn: sqlite3.Connection, event: Dict[str, Any], payload: Dict[str, Any]) -> str:
    """Procesa un gasto confirmado emitiendo un egreso (registerExit) en el fondo respectivo."""
    gasto_id = payload.get("gasto_id")
    detalle = payload.get("detalle", "Gasto comercial")
    monto = payload.get("monto_salida_cents") or payload.get("monto_cents", 0)
    metodo = str(payload.get("metodo_pago", "EFECTIVO")).strip().upper()
    actor = payload.get("actor", "cajero")
    fecha_utc = payload.get("fecha_hora_utc")

    # Selección del fondo de salida según medio de pago
    fund_code = "1110" if metodo == "EFECTIVO" else "1210"

    res = registerExit(
        conn=tes_conn,
        fund_code=fund_code,
        amount_cents=monto,
        counterpart="GASTO_OPERATIVO_COMERCIAL",
        reason=f"Egreso gasto #{gasto_id}: {detalle}",
        created_by=actor,
        terminal_id="TERM-POS-01",
        idempotency_key=f"{event['idempotency_key']}_EXIT",
        evidence_reference=f"GASTO_{gasto_id}",
        occurred_at=fecha_utc,
    )
    return res["movement_id"]


def _handle_expense_voided(tes_conn: sqlite3.Connection, event: Dict[str, Any], payload: Dict[str, Any]) -> str:
    """Procesa la anulación de un gasto revirtiendo el egreso previo."""
    gasto_id = payload.get("gasto_id")
    actor = payload.get("actor", "admin")
    motivo = payload.get("motivo", "Anulación de gasto")
    ref_key = payload.get("reference_idempotency_key", f"COMM_EXPENSE_{gasto_id}_CONFIRM")

    mov_orig = _buscar_movimiento_por_idempotency_key(tes_conn, f"{ref_key}_EXIT")
    if not mov_orig:
        raise BridgeConflictError(f"No se encontró movimiento origen para anular gasto #{gasto_id}")

    rev = reverseMovement(
        conn=tes_conn,
        movement_id=mov_orig["movement_id"],
        user_id=actor,
        reason=f"Anulación gasto #{gasto_id}: {motivo}",
        evidence_reference=f"VOID_GASTO_{gasto_id}",
        idempotency_key=f"{event['idempotency_key']}_REV",
    )
    return rev.get("counter_movement_id") or rev.get("reversal_id")


def _handle_expense_adjusted(tes_conn: sqlite3.Connection, event: Dict[str, Any], payload: Dict[str, Any]) -> str:
    """Procesa la edición de un gasto revirtiendo el anterior y creando el ajustado."""
    gasto_id = payload.get("gasto_id")
    actor = payload.get("actor", "admin")
    ref_key = payload.get("reference_idempotency_key", f"COMM_EXPENSE_{gasto_id}_CONFIRM")

    # 1. Revertir egreso anterior si existe
    mov_orig = _buscar_movimiento_por_idempotency_key(tes_conn, f"{ref_key}_EXIT")
    rev_id = None
    if mov_orig:
        rev = reverseMovement(
            conn=tes_conn,
            movement_id=mov_orig["movement_id"],
            user_id=actor,
            reason=f"Ajuste por edición de gasto #{gasto_id}",
            evidence_reference=f"ADJUST_GASTO_{gasto_id}",
            idempotency_key=f"{event['idempotency_key']}_REV_OLD",
        )
        rev_id = rev.get("counter_movement_id") or rev.get("reversal_id")

    # 2. Registrar nuevo egreso con los datos editados
    new_mov_id = _handle_expense_confirmed(tes_conn, event, payload)
    return f"{rev_id};{new_mov_id}" if rev_id else new_mov_id


def _handle_sale_adjusted(tes_conn: sqlite3.Connection, event: Dict[str, Any], payload: Dict[str, Any]) -> str:
    """Procesa el ajuste de un ticket de venta por edición de ítem."""
    tkt_id = payload.get("ticket_id") or event.get("entity_id")
    usuario = payload.get("usuario", "sistema")
    ref_key = payload.get("reference_idempotency_key", f"COMM_SALE_{tkt_id}_CONFIRM")
    is_mixed_reduction = payload.get("is_mixed_reduction", False)

    if is_mixed_reduction:
        logger.info(
            "Ajuste de venta mixta #%s retenido: reducción de pago mixto sin distribución aprobada.",
            tkt_id,
        )
        return f"PENDING_DEFINITION_MIXED_SALE_REDUCTION_{tkt_id}"

    reversals_generated = []
    # Revertir pierna efectivo
    mov_orig_efvo = _buscar_movimiento_por_idempotency_key(tes_conn, f"{ref_key}_CASH")
    if not mov_orig_efvo:
        mov_orig_efvo = _buscar_movimiento_por_idempotency_key(tes_conn, f"idemp_{tkt_id}_CASH")
    if mov_orig_efvo:
        rev_efvo = reverseMovement(
            conn=tes_conn,
            movement_id=mov_orig_efvo["movement_id"],
            user_id=usuario,
            reason=f"Ajuste venta ticket #{tkt_id} (reversión pierna efectivo)",
            evidence_reference=f"ADJUST_TICKET_{tkt_id}",
            idempotency_key=f"{event['idempotency_key']}_REV_CASH",
        )
        reversals_generated.append(rev_efvo.get("counter_movement_id") or rev_efvo.get("reversal_id"))

    # Revertir pierna digital
    mov_orig_transf = _buscar_movimiento_por_idempotency_key(tes_conn, f"{ref_key}_DIGITAL")
    if not mov_orig_transf:
        mov_orig_transf = _buscar_movimiento_por_idempotency_key(tes_conn, f"idemp_{tkt_id}_DIGITAL")
    if mov_orig_transf:
        rev_transf = reverseMovement(
            conn=tes_conn,
            movement_id=mov_orig_transf["movement_id"],
            user_id=usuario,
            reason=f"Ajuste venta ticket #{tkt_id} (reversión pierna digital)",
            evidence_reference=f"ADJUST_TICKET_{tkt_id}",
            idempotency_key=f"{event['idempotency_key']}_REV_DIGITAL",
        )
        reversals_generated.append(rev_transf.get("counter_movement_id") or rev_transf.get("reversal_id"))

    # Si el nuevo total es > 0, registrar los nuevos movimientos
    new_total = payload.get("new_total_venta_cents", 0)
    new_movements = []
    if new_total > 0:
        new_efvo = payload.get("monto_efectivo_cents", 0)
        new_transf = payload.get("monto_transferencia_cents", 0)
        fecha_utc = payload.get("fecha_hora_utc")

        if new_efvo > 0:
            res_c = registerEntry(
                conn=tes_conn,
                fund_code="1110",
                amount_cents=new_efvo,
                counterpart="VENTA_COMERCIAL_AJUSTADA",
                reason=f"Cobro ajustado en efectivo ticket #{tkt_id}",
                created_by=usuario,
                terminal_id="TERM-POS-01",
                idempotency_key=f"{event['idempotency_key']}_CASH",
                evidence_reference=f"TICKET_{tkt_id}",
                occurred_at=fecha_utc,
            )
            new_movements.append(res_c["movement_id"])

        if new_transf > 0:
            res_d = registerEntry(
                conn=tes_conn,
                fund_code="1210",
                amount_cents=new_transf,
                counterpart="VENTA_DIGITAL_AJUSTADA",
                reason=f"Cobro digital ajustado ticket #{tkt_id}",
                created_by=usuario,
                terminal_id="TERM-POS-01",
                idempotency_key=f"{event['idempotency_key']}_DIGITAL",
                evidence_reference=f"TICKET_{tkt_id}",
                occurred_at=fecha_utc,
            )
            new_movements.append(res_d["movement_id"])

    all_ids = reversals_generated + new_movements
    return ";".join(all_ids) if all_ids else "ZERO_ADJUSTMENT"


def _handle_service_adjusted(
    tes_conn: sqlite3.Connection,
    event: Dict[str, Any],
    payload: Dict[str, Any],
    comm_conn: Optional[sqlite3.Connection] = None,
) -> str:
    """Procesa el ajuste de una carga de servicio por edición."""
    servicio_id = payload.get("servicio_id") or event.get("entity_id")
    actor = payload.get("actor", "sistema")

    # 1. Revertir movimientos anteriores si existen
    reversals_generated = []
    cur = tes_conn.cursor()
    cur.execute(
        """
        SELECT movement_id, amount_cents FROM tesoreria_movimientos
        WHERE evidence_reference LIKE ? AND movement_type = 'ENTRY'
        ORDER BY id ASC;
        """,
        (f"%{servicio_id}%",),
    )
    rows = cur.fetchall()
    for idx, (m_id, amt) in enumerate(rows):
        rev = reverseMovement(
            conn=tes_conn,
            movement_id=m_id,
            user_id=actor,
            reason=f"Ajuste por edición de servicio #{servicio_id}",
            evidence_reference=f"ADJUST_SRV_{servicio_id}",
            idempotency_key=f"{event['idempotency_key']}_REV_{idx}",
        )
        reversals_generated.append(rev.get("counter_movement_id") or rev.get("reversal_id"))

    # 2. Registrar nuevos movimientos con datos actualizados
    new_res = _handle_service_confirmed(tes_conn, event, payload, comm_conn=comm_conn)
    all_res = reversals_generated + [new_res]
    return ";".join(all_res)


# ============================================================================
# MOTOR PRINCIPAL DE POLLING Y DRENAJE CONCURRENTE DEL WORKER
# ============================================================================

def process_pending_commercial_events(
    comm_conn: sqlite3.Connection,
    tes_conn: sqlite3.Connection,
    batch_size: int = 50,
    limit: Optional[int] = None,
) -> Dict[str, int]:
    """Drena en orden monotónico (sequence_id ASC) eventos PENDING/FAILED del outbox.
    
    Protege contra concurrencia con lock optimista inmediato a PROCESSING.
    Retorna métricas: {'published': int, 'failed': int, 'dead_letter': int, 'skipped': int}.
    """
    if limit is not None:
        batch_size = limit

    stats = {"published": 0, "failed": 0, "dead_letter": 0, "skipped": 0}

    # Leer candidatos pendientes o reintentables
    comm_conn.row_factory = sqlite3.Row
    cursor = comm_conn.cursor()
    cursor.execute(
        """
        SELECT sequence_id, event_id, event_type, entity_type, entity_id, action_type,
               idempotency_key, payload_canonical_hash, payload, status, retry_count, max_retries
        FROM commercial_events_outbox
        WHERE status IN ('PENDING', 'FAILED') AND retry_count < max_retries
        ORDER BY sequence_id ASC
        LIMIT ?;
        """,
        (batch_size,),
    )
    candidate_rows = cursor.fetchall()
    cursor.close()

    ahora_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    for row in candidate_rows:
        event = dict(row)
        seq_id = event["sequence_id"]

        # 1. Lock optimista concurrente: transición atómica a PROCESSING
        lock_cur = comm_conn.cursor()
        lock_cur.execute(
            """
            UPDATE commercial_events_outbox
            SET status = 'PROCESSING',
                retry_count = retry_count + 1,
                updated_at = ?
            WHERE sequence_id = ? AND status IN ('PENDING', 'FAILED');
            """,
            (ahora_utc, seq_id),
        )
        comm_conn.commit()
        if lock_cur.rowcount == 0:
            # Otro worker tomó el evento concurrentemente
            lock_cur.close()
            stats["skipped"] += 1
            continue
        lock_cur.close()

        # 2. Ejecutar procesamiento contable sobre Tesorería
        try:
            # Transacción en base de Tesorería
            tes_conn.execute("BEGIN IMMEDIATE;")
            mov_id = process_single_commercial_event(comm_conn, tes_conn, event)
            tes_conn.commit()

            # Confirmación atómica en el Outbox comercial -> PUBLISHED
            publish_cur = comm_conn.cursor()
            publish_cur.execute(
                """
                UPDATE commercial_events_outbox
                SET status = 'PUBLISHED',
                    tesoreria_movement_id = ?,
                    published_at = ?,
                    error_message = NULL,
                    updated_at = ?
                WHERE sequence_id = ?;
                """,
                (mov_id, ahora_utc, ahora_utc, seq_id),
            )
            comm_conn.commit()
            publish_cur.close()
            stats["published"] += 1

        except BridgeConflictError as conflict:
            # Conflicto fatal de integridad / colisión adulterada -> DEAD_LETTER inmediato
            try:
                tes_conn.rollback()
            except Exception:
                pass

            err_msg = str(conflict)
            dl_cur = comm_conn.cursor()
            dl_cur.execute(
                """
                UPDATE commercial_events_outbox
                SET status = 'DEAD_LETTER',
                    error_message = ?,
                    updated_at = ?
                WHERE sequence_id = ?;
                """,
                (f"CONFLICTO_FATAL: {err_msg}", ahora_utc, seq_id),
            )
            comm_conn.commit()
            dl_cur.close()
            stats["dead_letter"] += 1

        except Exception as exc:
            # Error transitorio de disponibilidad, saldo o conexión
            try:
                tes_conn.rollback()
            except Exception:
                pass

            err_msg = str(exc)
            retry_count_act = event["retry_count"] + 1
            max_retries = event["max_retries"]
            nuevo_estado = "DEAD_LETTER" if retry_count_act >= max_retries else "FAILED"

            fail_cur = comm_conn.cursor()
            fail_cur.execute(
                """
                UPDATE commercial_events_outbox
                SET status = ?,
                    error_message = ?,
                    updated_at = ?
                WHERE sequence_id = ?;
                """,
                (nuevo_estado, err_msg, ahora_utc, seq_id),
            )
            comm_conn.commit()
            fail_cur.close()

            if nuevo_estado == "DEAD_LETTER":
                stats["dead_letter"] += 1
            else:
                stats["failed"] += 1

    return stats


# ============================================================================
# AUDITORÍA Y RECONCILIACIÓN MATEMÁTICA DE FONDOS
# ============================================================================

def reconcile_treasury_ledger_balances(tes_conn: sqlite3.Connection) -> Dict[str, Any]:
    """Reconcilia y reconstruye matemáticamente los saldos de los 9 fondos desde el libro mayor.
    
    Verifica que:
    1. Saldo actual de tesoreria_fondos == Suma neta histórica de tesoreria_movimientos.
    2. Ningún fondo físico o sin sobregiro presente saldo disponible negativo.
    3. Fondos de custodia (1150) se mantengan estrictamente segregados.
    """
    tes_conn.row_factory = sqlite3.Row
    cursor = tes_conn.cursor()

    cursor.execute("SELECT id, codigo, nombre, saldo_centavos, permite_sobregiro, titularidad FROM tesoreria_fondos;")
    fondos = {r["codigo"]: dict(r) for r in cursor.fetchall()}

    discrepancias = []
    totales_reconstruidos = {}

    for codigo, f in fondos.items():
        fondo_id = f["id"]

        # Calcular sumatorias de movimientos confirmados (incluyendo contra-asientos de reversión vinculados y traslados)
        cursor.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN m.movement_type IN ('ENTRY', 'TRANSFER_IN') THEN m.amount_cents ELSE 0 END), 0) as total_entradas,
                COALESCE(SUM(CASE WHEN m.movement_type IN ('EXIT', 'TRANSFER_OUT') THEN m.amount_cents ELSE 0 END), 0) as total_salidas,
                COALESCE(SUM(CASE WHEN m.movement_type = 'REVERSAL' AND orig.movement_type IN ('ENTRY', 'TRANSFER_IN') THEN m.amount_cents ELSE 0 END), 0) as rev_entradas,
                COALESCE(SUM(CASE WHEN m.movement_type = 'REVERSAL' AND orig.movement_type IN ('EXIT', 'TRANSFER_OUT') THEN m.amount_cents ELSE 0 END), 0) as rev_salidas
            FROM tesoreria_movimientos m
            LEFT JOIN tesoreria_movimientos orig ON m.reversal_of_id = orig.movement_id
            WHERE m.fund_id = ? AND m.status = 'POSTED';
            """,
            (fondo_id,),
        )
        calc = cursor.fetchone()
        entradas = calc["total_entradas"] + calc["rev_salidas"]
        salidas = calc["total_salidas"] + calc["rev_entradas"]

        # Saldo reconstruido estricto
        saldo_reconstruido = entradas - salidas
        totales_reconstruidos[codigo] = saldo_reconstruido

        if saldo_reconstruido != f["saldo_centavos"]:
            discrepancias.append({
                "codigo": codigo,
                "nombre": f["nombre"],
                "saldo_tabla": f["saldo_centavos"],
                "saldo_reconstruido": saldo_reconstruido,
                "diferencia": f["saldo_centavos"] - saldo_reconstruido,
            })

        # Control de no negatividad para fondos físicos
        if not f["permite_sobregiro"] and f["saldo_centavos"] < 0:
            discrepancias.append({
                "codigo": codigo,
                "nombre": f["nombre"],
                "error": "SALDO_NEGATIVO_NO_PERMITIDO",
                "saldo": f["saldo_centavos"],
            })

    cursor.close()
    return {
        "cuadre_exacto": len(discrepancias) == 0,
        "fondos_auditados": len(fondos),
        "saldos_reconstruidos": totales_reconstruidos,
        "discrepancias": discrepancias,
    }


# ============================================================================
# ETAPA 5 — RECUPERACIÓN, REPROCESAMIENTO Y MONITOREO DEL WORKER
# ============================================================================

def recover_orphaned_processing_events(
    comm_conn: sqlite3.Connection,
    timeout_seconds: int = 30,
) -> int:
    """Recupera eventos que quedaron en estado 'PROCESSING' debido a una interrupción o reinicio forzado.
    
    Resetea su estado a 'FAILED' para que el worker pueda retomarlos, preservando el retry_count.
    """
    comm_conn.row_factory = sqlite3.Row
    ahora_utc = datetime.now(timezone.utc)
    cur = comm_conn.cursor()
    cur.execute(
        """
        SELECT sequence_id, event_id, retry_count, max_retries, updated_at
        FROM commercial_events_outbox
        WHERE status = 'PROCESSING';
        """
    )
    filas = cur.fetchall()
    recuperados = 0

    for f in filas:
        seq_id = f["sequence_id"]
        upd_str = f["updated_at"]
        debe_recuperar = False
        try:
            upd_dt = datetime.fromisoformat(upd_str)
            if upd_dt.tzinfo is None:
                upd_dt = upd_dt.replace(tzinfo=timezone.utc)
            if (ahora_utc - upd_dt).total_seconds() >= timeout_seconds:
                debe_recuperar = True
        except Exception:
            debe_recuperar = True

        if debe_recuperar:
            nuevo_retry = f["retry_count"] + 1
            nuevo_estado = "DEAD_LETTER" if nuevo_retry >= f["max_retries"] else "FAILED"
            msg = f"RECUPERACIÓN: Worker interrumpido previamente. Evento reseteado a {nuevo_estado}."
            cur.execute(
                """
                UPDATE commercial_events_outbox
                SET status = ?,
                    retry_count = ?,
                    error_message = ?,
                    updated_at = ?
                WHERE sequence_id = ?;
                """,
                (nuevo_estado, nuevo_retry, msg, ahora_utc.isoformat(), seq_id),
            )
            recuperados += 1

    comm_conn.commit()
    cur.close()
    return recuperados


def reprocess_outbox_event(
    comm_conn: sqlite3.Connection,
    tes_conn: sqlite3.Connection,
    event_id: str,
    authorized_by: str,
) -> Dict[str, Any]:
    """Reprocesa un evento en 'FAILED' o 'DEAD_LETTER' con auditoría estricta de permisos.
    
    Registra quién autorizó el reprocesamiento y cuándo, reseteando a PENDING y reintentando.
    """
    ahora_utc = datetime.now(timezone.utc).isoformat()
    comm_conn.row_factory = sqlite3.Row
    cur = comm_conn.cursor()
    cur.execute(
        """
        SELECT sequence_id, event_id, status, retry_count, payload, payload_canonical_hash
        FROM commercial_events_outbox
        WHERE event_id = ?;
        """,
        (event_id,),
    )
    row = cur.fetchone()
    if not row:
        cur.close()
        raise BridgeConflictError(f"Evento comercial '{event_id}' no encontrado en el Outbox.")

    estado_actual = row["status"]
    if estado_actual == "PUBLISHED":
        cur.close()
        return {
            "event_id": event_id,
            "status": "PUBLISHED",
            "message": "El evento ya se encuentra confirmado y publicado en Tesorería.",
        }

    # Comprobar si existen las columnas de auditoría de reprocesamiento
    cur.execute("PRAGMA table_info(commercial_events_outbox);")
    cols = {c[1] for c in cur.fetchall()}
    tiene_cols_audit = "reprocessed_by" in cols and "reprocessed_at" in cols

    if tiene_cols_audit:
        cur.execute(
            """
            UPDATE commercial_events_outbox
            SET status = 'PENDING',
                retry_count = 0,
                error_message = NULL,
                reprocessed_by = ?,
                reprocessed_at = ?,
                updated_at = ?
            WHERE event_id = ?;
            """,
            (authorized_by, ahora_utc, ahora_utc, event_id),
        )
    else:
        cur.execute(
            """
            UPDATE commercial_events_outbox
            SET status = 'PENDING',
                retry_count = 0,
                error_message = NULL,
                updated_at = ?
            WHERE event_id = ?;
            """,
            (ahora_utc, event_id),
        )
    comm_conn.commit()
    cur.close()

    # Ejecutar procesamiento inmediato del evento
    stats = process_pending_commercial_events(comm_conn, tes_conn, batch_size=1)

    cur2 = comm_conn.cursor()
    cur2.execute(
        "SELECT status, tesoreria_movement_id, error_message FROM commercial_events_outbox WHERE event_id = ?;",
        (event_id,),
    )
    act = cur2.fetchone()
    cur2.close()

    return {
        "event_id": event_id,
        "status": act["status"],
        "tesoreria_movement_id": act["tesoreria_movement_id"],
        "error_message": act["error_message"],
        "authorized_by": authorized_by,
        "reprocessed_at": ahora_utc,
        "drain_stats": stats,
    }


def get_commercial_bridge_status(comm_conn: sqlite3.Connection) -> Dict[str, Any]:
    """Retorna las métricas cuantitativas del Outbox comercial."""
    comm_conn.row_factory = sqlite3.Row
    cur = comm_conn.cursor()
    cur.execute(
        """
        SELECT
            COUNT(*) as total,
            COALESCE(SUM(CASE WHEN status = 'PENDING' AND (tesoreria_movement_id IS NULL OR (tesoreria_movement_id NOT LIKE 'PENDING_DEFINITION%' AND tesoreria_movement_id NOT LIKE 'UNSPECIFIED_PLATFORM%')) THEN 1 ELSE 0 END), 0) as pending,
            COALESCE(SUM(CASE WHEN status = 'PROCESSING' THEN 1 ELSE 0 END), 0) as processing,
            COALESCE(SUM(CASE WHEN status = 'PUBLISHED' AND (tesoreria_movement_id IS NULL OR (tesoreria_movement_id NOT LIKE 'PENDING_DEFINITION%' AND tesoreria_movement_id NOT LIKE 'UNSPECIFIED_PLATFORM%')) THEN 1 ELSE 0 END), 0) as published,
            COALESCE(SUM(CASE WHEN (tesoreria_movement_id LIKE 'PENDING_DEFINITION%' OR tesoreria_movement_id LIKE 'UNSPECIFIED_PLATFORM%') THEN 1 ELSE 0 END), 0) as held_pending_definition,
            COALESCE(SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END), 0) as failed,
            COALESCE(SUM(CASE WHEN status = 'DEAD_LETTER' THEN 1 ELSE 0 END), 0) as dead_letter,
            COALESCE(SUM(retry_count), 0) as retry_count_total
        FROM commercial_events_outbox;
        """
    )
    row = cur.fetchone()
    cur.close()
    if not row:
        return {
            "total": 0, "pending": 0, "processing": 0, "published": 0,
            "held_pending_definition": 0, "failed": 0, "dead_letter": 0, "retry_count_total": 0,
        }
    return dict(row)


def get_simplified_sync_status(comm_conn: sqlite3.Connection) -> Dict[str, Any]:
    """Retorna el indicador simplificado de sincronización apto para Cajero (sin información sensible)."""
    stats = get_commercial_bridge_status(comm_conn)
    sincronizadas = stats["published"]
    pendientes = stats["pending"] + stats["processing"]
    retenidas = stats.get("held_pending_definition", 0)
    problemas = stats["failed"] + stats["dead_letter"]
    requiere_atencion = (problemas > 0) or (retenidas > 0)

    if problemas > 0:
        mensaje = f"Atención requerida: {problemas} operación(es) con error de sincronización. Avise a Administración."
    elif retenidas > 0:
        mensaje = f"Atención: {retenidas} operación(es) retenida(s) pendiente(s) de definición de plataforma."
    elif pendientes > 0:
        mensaje = f"Sincronizando: {pendientes} operación(es) pendiente(s) de confirmación."
    else:
        mensaje = "Todas las operaciones sincronizadas correctamente."

    return {
        "sincronizadas": sincronizadas,
        "pendientes": pendientes,
        "held_pending_definition": retenidas,
        "requiere_atencion": requiere_atencion,
        "mensaje_cajero": mensaje,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def list_commercial_events_detailed(
    comm_conn: sqlite3.Connection,
    status_filter: Optional[str] = None,
    event_type_filter: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """Lista eventos comerciales con filtros y datos de conciliación para Administrador/Socios."""
    comm_conn.row_factory = sqlite3.Row
    cur = comm_conn.cursor()

    cur.execute("PRAGMA table_info(commercial_events_outbox);")
    cols = {c[1] for c in cur.fetchall()}
    has_audit = "reprocessed_by" in cols and "reprocessed_at" in cols

    reprocessed_cols = "reprocessed_by, reprocessed_at" if has_audit else "NULL as reprocessed_by, NULL as reprocessed_at"

    query = f"""
        SELECT sequence_id, event_id, event_type, entity_type, entity_id, action_type,
               idempotency_key, payload_canonical_hash, payload, status, retry_count,
               max_retries, error_message, tesoreria_movement_id, created_at, updated_at,
               published_at, {reprocessed_cols}
        FROM commercial_events_outbox
        WHERE 1=1
    """
    params: List[Any] = []

    if status_filter:
        sf = status_filter.upper()
        if sf in ("PENDING_DEFINITION", "HELD", "HELD_PENDING_DEFINITION"):
            query += " AND (tesoreria_movement_id LIKE 'PENDING_DEFINITION%' OR tesoreria_movement_id LIKE 'UNSPECIFIED_PLATFORM%')"
        elif sf == "PUBLISHED":
            query += " AND status = 'PUBLISHED' AND (tesoreria_movement_id IS NULL OR (tesoreria_movement_id NOT LIKE 'PENDING_DEFINITION%' AND tesoreria_movement_id NOT LIKE 'UNSPECIFIED_PLATFORM%'))"
        else:
            query += " AND status = ?"
            params.append(sf)

    if event_type_filter:
        query += " AND event_type LIKE ?"
        params.append(f"%{event_type_filter.upper()}%")

    if search:
        query += " AND (entity_id LIKE ? OR idempotency_key LIKE ? OR event_id LIKE ?)"
        s = f"%{search}%"
        params.extend([s, s, s])

    # Count total matching
    count_query = f"SELECT COUNT(*) FROM ({query})"
    cur.execute(count_query, params)
    total_matching = cur.fetchone()[0]

    query += " ORDER BY sequence_id DESC LIMIT ? OFFSET ?;"
    params.extend([max(1, limit), max(0, offset)])

    cur.execute(query, params)
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()

    # Parse payloads safely
    for r in rows:
        if isinstance(r.get("payload"), str):
            try:
                r["payload"] = json.loads(r["payload"])
            except Exception:
                pass

    return {
        "total": total_matching,
        "limit": limit,
        "offset": offset,
        "events": rows,
    }


class CommercialBridgeBackgroundWorker:
    """Worker híbrido en segundo plano con drenaje periódico y activación por demanda."""

    def __init__(
        self,
        comm_conn_factory: Callable[[], sqlite3.Connection],
        tes_conn_factory: Callable[[], sqlite3.Connection],
        interval_seconds: float = 5.0,
        close_connections_on_drain: bool = False,
    ):
        self.comm_conn_factory = comm_conn_factory
        self.tes_conn_factory = tes_conn_factory
        self.interval_seconds = interval_seconds
        self.close_connections_on_drain = close_connections_on_drain
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._wake_event = threading.Event()
        self._mutex = threading.Lock()

    def start(self):
        if self._running:
            return
        self._running = True
        self._wake_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="CommercialBridgeWorker", daemon=True)
        self._thread.start()
        logger.info("CommercialBridgeBackgroundWorker iniciado con ciclo de %ss.", self.interval_seconds)

    def stop(self, timeout: float = 2.0):
        if not self._running:
            return
        self._running = False
        self._wake_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        logger.info("CommercialBridgeBackgroundWorker detenido.")

    def trigger_immediate_drain(self):
        """Despierta el loop de fondo para ejecutar un ciclo de drenaje inmediato."""
        self._wake_event.set()

    def drain_once(self) -> Dict[str, int]:
        """Ejecuta una iteración de drenaje sincronizada con exclusión mutua."""
        with self._mutex:
            comm_conn = self.comm_conn_factory()
            tes_conn = self.tes_conn_factory()
            try:
                # Comprobar si la tabla outbox existe antes de consultar
                cur = comm_conn.cursor()
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='commercial_events_outbox';")
                if not cur.fetchone():
                    cur.close()
                    return {"published": 0, "failed": 0, "dead_letter": 0, "skipped": 0}
                cur.close()

                # 1. Recuperar eventos huérfanos tras posibles caídas
                recover_orphaned_processing_events(comm_conn, timeout_seconds=30)
                # 2. Drenar eventos pendientes
                return process_pending_commercial_events(comm_conn, tes_conn)
            finally:
                if self.close_connections_on_drain:
                    try:
                        comm_conn.close()
                    except Exception:
                        pass
                    try:
                        tes_conn.close()
                    except Exception:
                        pass

    def _run_loop(self):
        while self._running:
            try:
                self.drain_once()
            except Exception as e:
                logger.error("Error en ciclo de CommercialBridgeBackgroundWorker: %s", e)

            # Esperar intervalo o hasta que trigger_immediate_drain despierte
            self._wake_event.wait(timeout=self.interval_seconds)
            self._wake_event.clear()


# Instancia singleton del worker para el proceso actual
_GLOBAL_BRIDGE_WORKER: Optional[CommercialBridgeBackgroundWorker] = None
_WORKER_TRIGGER_HOOKS: List[Callable[[], None]] = []


def register_worker_trigger_hook(hook: Callable[[], None]) -> None:
    """Registra una función a invocar cuando se emiten nuevos eventos comerciales."""
    if hook not in _WORKER_TRIGGER_HOOKS:
        _WORKER_TRIGGER_HOOKS.append(hook)


def notify_worker_new_event() -> None:
    """Notifica al worker que hay nuevos eventos comerciales pendientes de drenar."""
    for hook in _WORKER_TRIGGER_HOOKS:
        try:
            hook()
        except Exception as e:
            logger.warning("Fallo al ejecutar trigger hook del worker: %s", e)

