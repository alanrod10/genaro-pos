"""Cálculo y Determinación de Disponibilidad de Fondos — GENARO TESORERÍA LABORATORIO.

Implementación de LAB-008:
- getFundBalance (Saldo registrado, reservado y disponible de un fondo específico)
- getFundAvailability (Detalle exhaustivo de disponibilidad y afectaciones)
- getTreasurySummary (Consolidado integral separando fondos propios de administrados/tránsito)

FÓRMULAS CONTABLES ESTRICTAS:
- Saldo Registrado = apertura + entradas - salidas (saldo_centavos en libros)
- Saldo Reservado = SUM(pending_amount_cents) de reservas activas/parcialmente aplicadas
- Saldo Disponible = Saldo Registrado - Saldo Reservado
- Dinero en Tránsito = SUM(amount_cents) de traslados en estado PENDING_RECEIPT
"""

import sqlite3
from typing import Any, Dict, List, Optional

from tesoreria.catalog import obtener_catalogo_fondos, obtener_fondo_por_codigo
from tesoreria.money import addMoney, assertValidMoney, formatMoney, subtractMoney


def getFundBalance(conn: sqlite3.Connection, fund_code: str) -> Dict[str, Any]:
    """Retorna los saldos registrados, reservados y disponibles de un fondo."""
    fondo = obtener_fondo_por_codigo(conn, fund_code)
    if not fondo:
        raise ValueError(f"Fondo {fund_code} no encontrado.")

    cursor = conn.cursor()

    # 1. Saldo reservado pendiente
    cursor.execute(
        """
        SELECT COALESCE(SUM(pending_amount_cents), 0), COUNT(id)
        FROM tesoreria_reservas
        WHERE fund_id = ? AND status IN ('ACTIVE', 'PARTIALLY_APPLIED')
        """,
        (fondo["id"],),
    )
    res_reservas = cursor.fetchone()
    saldo_reservado = int(res_reservas[0])
    cantidad_reservas_activas = int(res_reservas[1])

    # 2. Último saldo verificado en jornada cerrada
    cursor.execute(
        """
        SELECT jd.saldo_cierre_centavos, jd.diferencia_centavos, j.fecha_operativa, j.numero_jornada
        FROM tesoreria_jornada_detalles jd
        JOIN tesoreria_jornadas j ON jd.jornada_id = j.id
        WHERE jd.fondo_id = ? AND j.estado = 'CERRADA'
        ORDER BY j.id DESC
        LIMIT 1
        """,
        (fondo["id"],),
    )
    ultimo_arqueo = cursor.fetchone()

    # 3. Saldo teórico derivado matemáticamente del libro mayor (tesoreria_movimientos)
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
        (fondo["id"],),
    )
    calc = cursor.fetchone()
    cursor.close()

    total_entradas = int(calc[0]) if calc else 0
    total_salidas = int(calc[1]) if calc else 0
    rev_entradas = int(calc[2]) if calc else 0
    rev_salidas = int(calc[3]) if calc else 0
    saldo_teorico = (total_entradas + rev_salidas) - (total_salidas + rev_entradas)
    diferencia_teorica = fondo["saldo_centavos"] - saldo_teorico

    saldo_registrado = fondo["saldo_centavos"]
    saldo_disponible = subtractMoney(saldo_registrado, saldo_reservado)

    ultimo_verificado = int(ultimo_arqueo[0]) if ultimo_arqueo and ultimo_arqueo[0] is not None else None
    diferencia_arqueo = int(ultimo_arqueo[1]) if ultimo_arqueo and ultimo_arqueo[1] is not None else None

    return {
        "fund_id": fondo["id"],
        "fund_code": fondo["codigo"],
        "fund_name": fondo["nombre"],
        "nature": fondo["naturaleza"],
        "ownership": fondo["titularidad"],
        "status": fondo["estado"],
        "saldo_registrado_cents": saldo_registrado,
        "saldo_registrado_formatted": formatMoney(saldo_registrado),
        "saldo_teorico_cents": saldo_teorico,
        "saldo_teorico_formatted": formatMoney(saldo_teorico),
        "diferencia_teorica_cents": diferencia_teorica,
        "diferencia_teorica_formatted": formatMoney(diferencia_teorica),
        "saldo_reservado_cents": saldo_reservado,
        "saldo_reservado_formatted": formatMoney(saldo_reservado),
        "saldo_disponible_cents": saldo_disponible,
        "saldo_disponible_formatted": formatMoney(saldo_disponible),
        "cantidad_reservas_activas": cantidad_reservas_activas,
        "ultimo_saldo_verificado_cents": ultimo_verificado,
        "ultimo_saldo_verificado_formatted": formatMoney(ultimo_verificado) if ultimo_verificado is not None else None,
        "diferencia_arqueo_cents": diferencia_arqueo,
        "diferencia_arqueo_formatted": formatMoney(diferencia_arqueo) if diferencia_arqueo is not None else None,
        "fecha_ultimo_arqueo": ultimo_arqueo[2] if ultimo_arqueo else None,
    }


def getFundAvailability(conn: sqlite3.Connection, fund_code: str) -> Dict[str, Any]:
    """Retorna el desglose analítico de disponibilidad de un fondo."""
    balance = getFundBalance(conn, fund_code)

    cursor = conn.cursor()
    # Listar reservas activas que afectan la disponibilidad
    cursor.execute(
        """
        SELECT reservation_id, pending_amount_cents, reason, beneficiary, created_at, status
        FROM tesoreria_reservas
        WHERE fund_id = ? AND status IN ('ACTIVE', 'PARTIALLY_APPLIED')
        ORDER BY id ASC
        """,
        (balance["fund_id"],),
    )
    reservas_filas = cursor.fetchall()
    cursor.close()

    reservas_detalle = [
        {
            "reservation_id": r[0],
            "pending_amount_cents": r[1],
            "pending_amount_formatted": formatMoney(r[1]),
            "reason": r[2],
            "beneficiary": r[3],
            "created_at": r[4],
            "status": r[5],
        }
        for r in reservas_filas
    ]

    # Listar traslados en tránsito que afectan a este fondo
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT t.transfer_id, fo.codigo as origin_code, fo.nombre as origin_name,
               fd.codigo as dest_code, fd.nombre as dest_name,
               t.amount_cents, t.dispatched_by, t.dispatched_at, t.reason, t.out_movement_id
        FROM tesoreria_traslados t
        JOIN tesoreria_fondos fo ON t.origin_fund_id = fo.id
        JOIN tesoreria_fondos fd ON t.destination_fund_id = fd.id
        WHERE t.status = 'PENDING_RECEIPT'
          AND (t.origin_fund_id = ? OR t.destination_fund_id = ?)
        ORDER BY t.id ASC
        """,
        (balance["fund_id"], balance["fund_id"]),
    )
    filas_trf = cursor.fetchall()
    cursor.close()

    traslados_detalle = [
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
            "es_origen": r[1] == fund_code,
            "es_destino": r[3] == fund_code,
        }
        for r in filas_trf
    ]

    balance["reservas_afectando_disponibilidad"] = reservas_detalle
    balance["traslados_en_transito"] = traslados_detalle
    balance["total_en_transito_cents"] = sum(t["amount_cents"] for t in traslados_detalle)
    balance["total_en_transito_formatted"] = formatMoney(balance["total_en_transito_cents"])
    return balance


def getTreasurySummary(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Retorna el resumen consolidado de tesorería diferenciando fondos propios y administrados."""
    fondos = obtener_catalogo_fondos(conn)

    cursor = conn.cursor()
    # Calcular dinero en tránsito (traslados pendientes de confirmación)
    cursor.execute(
        """
        SELECT COALESCE(SUM(amount_cents), 0), COUNT(id)
        FROM tesoreria_traslados
        WHERE status = 'PENDING_RECEIPT'
        """
    )
    res_transito = cursor.fetchone()
    dinero_en_transito_cents = int(res_transito[0])
    traslados_pendientes_count = int(res_transito[1])
    cursor.close()

    fondos_detalle: List[Dict[str, Any]] = []

    total_registrado_propio = 0
    total_reservado_propio = 0
    total_disponible_propio = 0

    total_registrado_custodia = 0
    total_reservado_custodia = 0
    total_disponible_custodia = 0

    total_registrado_transito_prev = 0
    total_reservado_transito_prev = 0
    total_disponible_transito_prev = 0

    for f in fondos:
        b = getFundBalance(conn, f["codigo"])
        fondos_detalle.append(b)

        if b["ownership"] == "PROPIA":
            total_registrado_propio = addMoney(total_registrado_propio, b["saldo_registrado_cents"])
            total_reservado_propio = addMoney(total_reservado_propio, b["saldo_reservado_cents"])
            total_disponible_propio = addMoney(total_disponible_propio, b["saldo_disponible_cents"])
        elif b["ownership"] == "CUSTODIA":
            total_registrado_custodia = addMoney(total_registrado_custodia, b["saldo_registrado_cents"])
            total_reservado_custodia = addMoney(total_reservado_custodia, b["saldo_reservado_cents"])
            total_disponible_custodia = addMoney(total_disponible_custodia, b["saldo_disponible_cents"])
        elif b["ownership"] == "TRANSITO":
            total_registrado_transito_prev = addMoney(total_registrado_transito_prev, b["saldo_registrado_cents"])
            total_reservado_transito_prev = addMoney(total_reservado_transito_prev, b["saldo_reservado_cents"])
            total_disponible_transito_prev = addMoney(total_disponible_transito_prev, b["saldo_disponible_cents"])

    total_registrado_administrado = addMoney(total_registrado_custodia, total_registrado_transito_prev)
    total_reservado_administrado = addMoney(total_reservado_custodia, total_reservado_transito_prev)
    total_disponible_administrado = addMoney(total_disponible_custodia, total_disponible_transito_prev)

    # Total consolidado global (Fondos en cuentas + dinero en tránsito entre cuentas)
    total_consolidado_registrado = addMoney(
        addMoney(total_registrado_propio, total_registrado_administrado),
        dinero_en_transito_cents,
    )

    return {
        "fondos_propios": {
            "total_registrado_cents": total_registrado_propio,
            "total_registrado_formatted": formatMoney(total_registrado_propio),
            "total_reservado_cents": total_reservado_propio,
            "total_reservado_formatted": formatMoney(total_reservado_propio),
            "total_disponible_cents": total_disponible_propio,
            "total_disponible_formatted": formatMoney(total_disponible_propio),
        },
        "fondos_custodia": {
            "total_registrado_cents": total_registrado_custodia,
            "total_registrado_formatted": formatMoney(total_registrado_custodia),
            "total_reservado_cents": total_reservado_custodia,
            "total_reservado_formatted": formatMoney(total_reservado_custodia),
            "total_disponible_cents": total_disponible_custodia,
            "total_disponible_formatted": formatMoney(total_disponible_custodia),
        },
        "fondos_transito_preventistas": {
            "total_registrado_cents": total_registrado_transito_prev,
            "total_registrado_formatted": formatMoney(total_registrado_transito_prev),
            "total_reservado_cents": total_reservado_transito_prev,
            "total_reservado_formatted": formatMoney(total_reservado_transito_prev),
            "total_disponible_cents": total_disponible_transito_prev,
            "total_disponible_formatted": formatMoney(total_disponible_transito_prev),
        },
        "fondos_administrados_custodia": {
            "total_registrado_cents": total_registrado_administrado,
            "total_registrado_formatted": formatMoney(total_registrado_administrado),
            "total_reservado_cents": total_reservado_administrado,
            "total_reservado_formatted": formatMoney(total_reservado_administrado),
            "total_disponible_cents": total_disponible_administrado,
            "total_disponible_formatted": formatMoney(total_disponible_administrado),
        },
        "dinero_en_transito": {
            "amount_cents": dinero_en_transito_cents,
            "amount_formatted": formatMoney(dinero_en_transito_cents),
            "traslados_pendientes_count": traslados_pendientes_count,
        },
        "total_consolidado": {
            "total_registrado_cents": total_consolidado_registrado,
            "total_registrado_formatted": formatMoney(total_consolidado_registrado),
        },
        "fondos": fondos_detalle,
    }
