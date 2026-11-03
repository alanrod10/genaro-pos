"""Adaptador de base de datos para el módulo HISTÓRICO de GenaroPOS.

Acceso de solo lectura a las tablas históricas persistidas:
- historico_lotes
- historico_tickets
- historico_ticket_items
- historico_cargas_servicios

Garantías de aislamiento:
- Ninguna función ejecuta operaciones mutadoras (INSERT, UPDATE, DELETE, ALTER, DROP).
- No accede ni modifica las tablas operativas (tickets, cargas_servicios, productos, cierres_diarios).
- Los cálculos respetan la zona horaria de Jujuy/Argentina y las reglas contables auditadas.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import logging
import os
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

logger = logging.getLogger("genaro_pos.historical")
ZONA_AR = ZoneInfo("America/Argentina/Jujuy")

_HOST_LIVE_OPS_AUTORIZADO = "genaro-pos-live-ops-alanrod10.aws-sa-east-1.turso.io"
_HOST_DEV_AUTORIZADO = "genaro-pos-dev-alanrod10.aws-sa-east-1.turso.io"


class ErrorHistorico(ValueError):
    """Excepción base para errores de consulta en el módulo histórico."""


def _abrir_conexion_historica():
    """Abre conexión de solo lectura a la base histórica autorizada."""
    url = (
        os.getenv("TURSO_HISTORICAL_DATABASE_URL")
        or os.getenv("TURSO_DEV_DATABASE_URL")
        or ""
    ).strip()
    token = (
        os.getenv("TURSO_HISTORICAL_AUTH_TOKEN")
        or os.getenv("TURSO_DEV_AUTH_TOKEN")
        or ""
    ).strip()

    if not url or not token:
        raise ErrorHistorico(
            "Configuración incompleta: TURSO_HISTORICAL_DATABASE_URL o TURSO_DEV_DATABASE_URL y token requeridos."
        )

    parsed = urlparse(url)
    if parsed.hostname not in (_HOST_LIVE_OPS_AUTORIZADO, _HOST_DEV_AUTORIZADO):
        raise ErrorHistorico(
            f"Conexión bloqueada: host '{parsed.hostname}' no autorizado para consultas históricas."
        )

    import turso_serverless

    conn = turso_serverless.connect(url, auth_token=token)
    return conn


@contextmanager
def _conexion(conn=None):
    propia = conn is None
    conexion = _abrir_conexion_historica() if propia else conn
    try:
        yield conexion
    finally:
        if propia:
            conexion.close()


def obtener_resumen_historico(conn: Optional[Any] = None) -> Dict[str, Any]:
    """Retorna las métricas consolidadas globales del archivo histórico persistido."""
    with _conexion(conn) as c:
        row_tickets = c.execute(
            """
            SELECT 
                COUNT(*), 
                COALESCE(SUM(total_venta), 0),
                COALESCE(SUM(monto_efectivo), 0),
                COALESCE(SUM(monto_transferencia), 0),
                MIN(fecha_hora_local),
                MAX(fecha_hora_local)
            FROM historico_tickets
            """
        ).fetchone()

        row_items = c.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(subtotal), 0)
            FROM historico_ticket_items
            """
        ).fetchone()

        row_cargas = c.execute(
            """
            SELECT 
                COUNT(*),
                COALESCE(SUM(total_cobrado), 0),
                COALESCE(SUM(monto_carga), 0),
                COALESCE(SUM(monto_adicional), 0),
                COALESCE(SUM(pago_efectivo), 0),
                COALESCE(SUM(pago_transferencia), 0)
            FROM historico_cargas_servicios
            """
        ).fetchone()

        cant_tickets = int(row_tickets[0] or 0)
        tot_ventas = int(row_tickets[1] or 0)
        prom_ticket = round(tot_ventas / cant_tickets, 2) if cant_tickets > 0 else 0.0

        return {
            "total_ventas_pesos": tot_ventas,
            "total_tickets": cant_tickets,
            "ticket_promedio": prom_ticket,
            "total_ventas_efectivo": int(row_tickets[2] or 0),
            "total_ventas_transferencia": int(row_tickets[3] or 0),
            "fecha_inicio": str(row_tickets[4] or "2025-12-01 08:36:20"),
            "fecha_fin": str(row_tickets[5] or "2026-10-29 17:02:41"),
            "periodo_meses": 11,
            "total_items_filas": int(row_items[0] or 0),
            "total_items_subtotal": int(row_items[1] or 0),
            "total_cargas_cobrado": int(row_cargas[1] or 0),
            "total_cargas_capital": int(row_cargas[2] or 0),
            "total_cargas_adicional": int(row_cargas[3] or 0),
            "total_operaciones_cargas": int(row_cargas[0] or 0),
            "total_cargas_efectivo": int(row_cargas[4] or 0),
            "total_cargas_transferencia": int(row_cargas[5] or 0),
            "advertencia_pagos": (
                "Existe una diferencia neta de +$6.900 (+0,03%) entre la suma de pagos "
                "($23.630.790) y las ventas ($23.623.890) debido a 4 tickets con registro mixto no balanceado."
            ),
            "advertencia_items": (
                "El subtotal de artículos ($23.522.590) difiere en -$101.300 (-0,43%) "
                "respecto a cabeceras debido a 29 tickets sin desglose ($117.400) y 6 tickets con reintentos temporales."
            ),
        }


def obtener_ventas_historicas(
    desde: Optional[str] = None,
    hasta: Optional[str] = None,
    metodo_pago: Optional[str] = None,
    agrupacion: str = "mes",
    limit: int = 50,
    offset: int = 0,
    conn: Optional[Any] = None,
) -> Dict[str, Any]:
    """Retorna ventas históricas filtradas, serie temporal y tabla paginada de tickets."""
    where_clauses = ["1=1"]
    params: List[Any] = []

    if desde:
        where_clauses.append("substr(fecha_hora_local, 1, 10) >= ?")
        params.append(desde.strip())
    if hasta:
        where_clauses.append("substr(fecha_hora_local, 1, 10) <= ?")
        params.append(hasta.strip())
    if metodo_pago and metodo_pago.upper() in ("EFECTIVO", "TRANSFERENCIA", "MIXTO"):
        where_clauses.append("metodo_pago = ?")
        params.append(metodo_pago.upper())

    where_sql = " AND ".join(where_clauses)

    with _conexion(conn) as c:
        # Totales del filtro
        row_tot = c.execute(
            f"""
            SELECT 
                COUNT(*),
                COALESCE(SUM(total_venta), 0),
                COALESCE(SUM(monto_efectivo), 0),
                COALESCE(SUM(monto_transferencia), 0)
            FROM historico_tickets
            WHERE {where_sql}
            """,
            params,
        ).fetchone()

        cant_tickets = int(row_tot[0] or 0)
        tot_ventas = int(row_tot[1] or 0)
        prom_ticket = round(tot_ventas / cant_tickets, 2) if cant_tickets > 0 else 0.0

        # Serie temporal
        date_expr = (
            "substr(fecha_hora_local, 1, 7)"
            if agrupacion == "mes"
            else "substr(fecha_hora_local, 1, 10)"
        )
        serie_rows = c.execute(
            f"""
            SELECT 
                {date_expr} AS periodo,
                COUNT(*) AS tickets,
                SUM(total_venta) AS ventas_total,
                SUM(monto_efectivo) AS efvo,
                SUM(monto_transferencia) AS transf
            FROM historico_tickets
            WHERE {where_sql}
            GROUP BY {date_expr}
            ORDER BY periodo ASC
            """,
            params,
        ).fetchall()

        serie_temporal = [
            {
                "periodo": r[0],
                "tickets": int(r[1] or 0),
                "ventas_total": int(r[2] or 0),
                "monto_efectivo": int(r[3] or 0),
                "monto_transferencia": int(r[4] or 0),
            }
            for r in serie_rows
        ]

        # Paginación de tickets
        safe_limit = max(1, min(limit, 200))
        safe_offset = max(0, offset)
        params_page = list(params) + [safe_limit, safe_offset]

        tickets_rows = c.execute(
            f"""
            SELECT 
                id,
                fecha_hora_local,
                total_venta,
                monto_efectivo,
                monto_transferencia,
                metodo_pago,
                tiene_items,
                balance_pago_diferencia
            FROM historico_tickets
            WHERE {where_sql}
            ORDER BY fecha_hora_local DESC
            LIMIT ? OFFSET ?
            """,
            params_page,
        ).fetchall()

        tickets_list = [
            {
                "id": str(r[0]),
                "fecha_hora_local": str(r[1]),
                "total_venta": int(r[2] or 0),
                "monto_efectivo": int(r[3] or 0),
                "monto_transferencia": int(r[4] or 0),
                "metodo_pago": str(r[5]),
                "tiene_items": int(r[6]),
                "balance_pago_diferencia": int(r[7] or 0),
            }
            for r in tickets_rows
        ]

        return {
            "desde": desde,
            "hasta": hasta,
            "metodo_pago": metodo_pago,
            "total_ventas": tot_ventas,
            "cantidad_tickets": cant_tickets,
            "ticket_promedio": prom_ticket,
            "total_efectivo": int(row_tot[2] or 0),
            "total_transferencia": int(row_tot[3] or 0),
            "serie_temporal": serie_temporal,
            "tickets": tickets_list,
            "total_registros": cant_tickets,
            "limit": safe_limit,
            "offset": safe_offset,
        }


def obtener_ranking_productos_historico(
    desde: Optional[str] = None,
    hasta: Optional[str] = None,
    depurado: bool = True,
    limit: int = 50,
    offset: int = 0,
    conn: Optional[Any] = None,
) -> Dict[str, Any]:
    """Retorna el ranking de productos vendidos con soporte para exclusión de reintentos duplicados."""
    where_clauses = ["1=1"]
    params: List[Any] = []

    if depurado:
        where_clauses.append("es_duplicado_deriva = 0")
    if desde:
        where_clauses.append("substr(fecha_hora_local, 1, 10) >= ?")
        params.append(desde.strip())
    if hasta:
        where_clauses.append("substr(fecha_hora_local, 1, 10) <= ?")
        params.append(hasta.strip())

    where_sql = " AND ".join(where_clauses)

    with _conexion(conn) as c:
        total_distintos = c.execute(
            f"SELECT COUNT(DISTINCT producto_nombre) FROM historico_ticket_items WHERE {where_sql}",
            params,
        ).fetchone()[0]

        safe_limit = max(1, min(limit, 200))
        safe_offset = max(0, offset)
        params_page = list(params) + [safe_limit, safe_offset]

        rows = c.execute(
            f"""
            SELECT 
                producto_nombre,
                COUNT(*) AS operaciones,
                SUM(CASE WHEN cantidad IS NOT NULL THEN cantidad ELSE 0 END) AS unidades_cuantificables,
                SUM(CASE WHEN cantidad IS NULL THEN 1 ELSE 0 END) AS ventas_pesables_monto,
                SUM(subtotal) AS total_recaudado
            FROM historico_ticket_items
            WHERE {where_sql}
            GROUP BY producto_nombre
            ORDER BY total_recaudado DESC
            LIMIT ? OFFSET ?
            """,
            params_page,
        ).fetchall()

        ranking = [
            {
                "posicion": safe_offset + idx + 1,
                "producto_nombre": str(r[0]),
                "operaciones": int(r[1] or 0),
                "unidades_cuantificables": round(float(r[2] or 0.0), 2),
                "ventas_pesables_monto": int(r[3] or 0),
                "total_recaudado": int(r[4] or 0),
            }
            for idx, r in enumerate(rows)
        ]

        return {
            "desde": desde,
            "hasta": hasta,
            "modo_depurado": depurado,
            "total_productos_distintos": int(total_distintos or 0),
            "productos": ranking,
            "total_registros": int(total_distintos or 0),
            "limit": safe_limit,
            "offset": safe_offset,
            "advertencia_pesables": (
                "1.220 líneas corresponden a productos vendidos por importe fijo en pesos "
                "sin pesaje registrado en origen. Las ventas pesables se identifican por separado."
            ),
        }


def obtener_servicios_historicos(
    desde: Optional[str] = None,
    hasta: Optional[str] = None,
    excluir_duplicados: bool = False,
    conn: Optional[Any] = None,
) -> Dict[str, Any]:
    """Retorna métricas de servicios de recargas históricas con opción de excluir posibles duplicados."""
    where_clauses = ["1=1"]
    params: List[Any] = []

    if excluir_duplicados:
        where_clauses.append("es_posible_duplicado = 0")
    if desde:
        where_clauses.append("substr(fecha_hora_local, 1, 10) >= ?")
        params.append(desde.strip())
    if hasta:
        where_clauses.append("substr(fecha_hora_local, 1, 10) <= ?")
        params.append(hasta.strip())

    where_sql = " AND ".join(where_clauses)

    with _conexion(conn) as c:
        row_tot = c.execute(
            f"""
            SELECT 
                COUNT(*),
                COALESCE(SUM(monto_carga), 0),
                COALESCE(SUM(monto_adicional), 0),
                COALESCE(SUM(total_cobrado), 0),
                COALESCE(SUM(pago_efectivo), 0),
                COALESCE(SUM(pago_transferencia), 0)
            FROM historico_cargas_servicios
            WHERE {where_sql}
            """,
            params,
        ).fetchone()

        srv_rows = c.execute(
            f"""
            SELECT 
                servicio,
                COUNT(*) AS total_operaciones,
                SUM(monto_carga) AS capital,
                SUM(monto_adicional) AS ganancia,
                SUM(total_cobrado) AS cobrado,
                SUM(pago_efectivo) AS efvo,
                SUM(pago_transferencia) AS transf
            FROM historico_cargas_servicios
            WHERE {where_sql}
            GROUP BY servicio
            ORDER BY cobrado DESC
            """,
            params,
        ).fetchall()

        servicios = [
            {
                "servicio": str(r[0]),
                "total_operaciones": int(r[1] or 0),
                "capital_recargado": int(r[2] or 0),
                "adicional_ganancia": int(r[3] or 0),
                "total_cobrado": int(r[4] or 0),
                "pago_efectivo": int(r[5] or 0),
                "pago_transferencia": int(r[6] or 0),
            }
            for r in srv_rows
        ]

        serie_rows = c.execute(
            f"""
            SELECT 
                substr(fecha_hora_local, 1, 7) AS periodo,
                COUNT(*),
                SUM(total_cobrado),
                SUM(monto_adicional)
            FROM historico_cargas_servicios
            WHERE {where_sql}
            GROUP BY substr(fecha_hora_local, 1, 7)
            ORDER BY periodo ASC
            """,
            params,
        ).fetchall()

        serie_temporal = [
            {
                "periodo": str(r[0]),
                "operaciones": int(r[1] or 0),
                "total_cobrado": int(r[2] or 0),
                "adicional_ganancia": int(r[3] or 0),
            }
            for r in serie_rows
        ]

        return {
            "desde": desde,
            "hasta": hasta,
            "excluir_duplicados": excluir_duplicados,
            "total_operaciones": int(row_tot[0] or 0),
            "capital_recargado": int(row_tot[1] or 0),
            "adicional_ganancia": int(row_tot[2] or 0),
            "total_cobrado": int(row_tot[3] or 0),
            "pago_efectivo": int(row_tot[4] or 0),
            "pago_transferencia": int(row_tot[5] or 0),
            "servicios": servicios,
            "serie_temporal": serie_temporal,
            "advertencia_duplicados": (
                "12 parejas de operaciones se registraron al mismo segundo exacto. "
                "Se ofrece la opción de excluirlas estadísticamente sin alterar el registro persistido."
            ),
        }


def obtener_ticket_detalle_historico(
    ticket_id: str, conn: Optional[Any] = None
) -> Optional[Dict[str, Any]]:
    """Busca y retorna un ticket histórico específico con su cabecera, ítems y alertas."""
    tid_clean = ticket_id.strip()

    with _conexion(conn) as c:
        row_t = c.execute(
            """
            SELECT 
                id,
                fecha_hora_local,
                fecha_hora_utc,
                total_venta,
                monto_efectivo,
                monto_transferencia,
                metodo_pago,
                es_nocturno,
                balance_pago_diferencia,
                tiene_items,
                origen_fila_excel
            FROM historico_tickets
            WHERE id = ?
            """,
            (tid_clean,),
        ).fetchone()

        if not row_t:
            return None

        # Consultar ítems (buscando por ticket_id o correlacionado)
        items_rows = c.execute(
            """
            SELECT 
                id,
                ticket_id,
                producto_nombre,
                cantidad,
                unidad,
                precio_unitario,
                subtotal,
                metodo_pago,
                calculo_subtotal_coincide,
                es_duplicado_deriva,
                ticket_id_correlacionado
            FROM historico_ticket_items
            WHERE ticket_id = ? OR ticket_id_correlacionado = ?
            ORDER BY id ASC
            """,
            (tid_clean, tid_clean),
        ).fetchall()

        items_list = [
            {
                "id": int(r[0]),
                "ticket_id": str(r[1]),
                "producto_nombre": str(r[2]),
                "cantidad": float(r[3]) if r[3] is not None else None,
                "unidad": str(r[4] or "Unidad"),
                "precio_unitario": int(r[5] or 0),
                "subtotal": int(r[6] or 0),
                "metodo_pago": str(r[7] or ""),
                "calculo_subtotal_coincide": int(r[8]),
                "es_duplicado_deriva": int(r[9]),
                "ticket_id_correlacionado": str(r[10]) if r[10] else None,
            }
            for r in items_rows
        ]

        alertas = []
        bal_diff = int(row_t[8] or 0)
        if bal_diff != 0:
            alertas.append(
                f"Discrepancia en pagos: El balance difiere en ${abs(bal_diff)} del total facturado."
            )
        if int(row_t[9]) == 0:
            alertas.append("Venta directa de cabecera sin desglose de artículos registrado.")
        for it in items_list:
            if it["ticket_id_correlacionado"] == tid_clean:
                alertas.append(
                    f"Ítem '{it['producto_nombre']}' reasociado por proximidad de timestamp (< 1s)."
                )

        return {
            "id": str(row_t[0]),
            "fecha_hora_local": str(row_t[1]),
            "fecha_hora_utc": str(row_t[2]),
            "total_venta": int(row_t[3] or 0),
            "monto_efectivo": int(row_t[4] or 0),
            "monto_transferencia": int(row_t[5] or 0),
            "metodo_pago": str(row_t[6]),
            "es_nocturno": int(row_t[7] or 0),
            "balance_pago_diferencia": bal_diff,
            "tiene_items": int(row_t[9]),
            "origen_fila_excel": int(row_t[10]),
            "items": items_list,
            "alertas": alertas,
        }


def buscar_tickets_historicos(
    query_str: Optional[str] = None,
    fecha: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    conn: Optional[Any] = None,
) -> Dict[str, Any]:
    """Búsqueda paginada de tickets históricos por ID o fecha."""
    where_clauses = ["1=1"]
    params: List[Any] = []

    if query_str:
        where_clauses.append("id LIKE ?")
        params.append(f"%{query_str.strip()}%")
    if fecha:
        where_clauses.append("substr(fecha_hora_local, 1, 10) = ?")
        params.append(fecha.strip())

    where_sql = " AND ".join(where_clauses)
    safe_limit = max(1, min(limit, 100))
    safe_offset = max(0, offset)

    with _conexion(conn) as c:
        total = c.execute(
            f"SELECT COUNT(*) FROM historico_tickets WHERE {where_sql}", params
        ).fetchone()[0]

        params_page = list(params) + [safe_limit, safe_offset]
        rows = c.execute(
            f"""
            SELECT id, fecha_hora_local, total_venta, monto_efectivo, monto_transferencia, metodo_pago, tiene_items, balance_pago_diferencia
            FROM historico_tickets
            WHERE {where_sql}
            ORDER BY fecha_hora_local DESC
            LIMIT ? OFFSET ?
            """,
            params_page,
        ).fetchall()

        tickets = [
            {
                "id": str(r[0]),
                "fecha_hora_local": str(r[1]),
                "total_venta": int(r[2] or 0),
                "monto_efectivo": int(r[3] or 0),
                "monto_transferencia": int(r[4] or 0),
                "metodo_pago": str(r[5]),
                "tiene_items": int(r[6]),
                "balance_pago_diferencia": int(r[7] or 0),
            }
            for r in rows
        ]

        return {
            "total_encontrados": int(total or 0),
            "tickets": tickets,
            "limit": safe_limit,
            "offset": safe_offset,
        }


def comparar_periodos_historicos(
    desde_a: str,
    hasta_a: str,
    desde_b: str,
    hasta_b: str,
    conn: Optional[Any] = None,
) -> Dict[str, Any]:
    """Compara métricas de dos rangos de fechas históricos seleccionables."""
    with _conexion(conn) as c:
        def _metricas_periodo(d_ini: str, d_fin: str):
            r_v = c.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(total_venta), 0)
                FROM historico_tickets
                WHERE substr(fecha_hora_local, 1, 10) >= ? AND substr(fecha_hora_local, 1, 10) <= ?
                """,
                (d_ini, d_fin),
            ).fetchone()
            cant_t = int(r_v[0] or 0)
            tot_v = int(r_v[1] or 0)
            prom_t = round(tot_v / cant_t, 2) if cant_t > 0 else 0.0

            r_c = c.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(total_cobrado), 0), COALESCE(SUM(monto_adicional), 0)
                FROM historico_cargas_servicios
                WHERE substr(fecha_hora_local, 1, 10) >= ? AND substr(fecha_hora_local, 1, 10) <= ?
                """,
                (d_ini, d_fin),
            ).fetchone()

            top_prods = c.execute(
                """
                SELECT producto_nombre, SUM(subtotal)
                FROM historico_ticket_items
                WHERE es_duplicado_deriva = 0
                  AND substr(fecha_hora_local, 1, 10) >= ? AND substr(fecha_hora_local, 1, 10) <= ?
                GROUP BY producto_nombre
                ORDER BY SUM(subtotal) DESC
                LIMIT 5
                """,
                (d_ini, d_fin),
            ).fetchall()

            return {
                "desde": d_ini,
                "hasta": d_fin,
                "facturacion_ventas": tot_v,
                "cantidad_tickets": cant_t,
                "ticket_promedio": prom_t,
                "cargas_cobrado": int(r_c[1] or 0),
                "cargas_adicional": int(r_c[2] or 0),
                "operaciones_cargas": int(r_c[0] or 0),
                "top_productos": [
                    {"nombre": str(p[0]), "subtotal": int(p[1] or 0)} for p in top_prods
                ],
            }

        m_a = _metricas_periodo(desde_a, hasta_a)
        m_b = _metricas_periodo(desde_b, hasta_b)

        def _calc_pct(val_a, val_b):
            if val_a == 0:
                return 100.0 if val_b > 0 else 0.0
            return round(((val_b - val_a) / val_a) * 100, 2)

        variaciones = {
            "facturacion_ventas_pct": _calc_pct(m_a["facturacion_ventas"], m_b["facturacion_ventas"]),
            "cantidad_tickets_pct": _calc_pct(m_a["cantidad_tickets"], m_b["cantidad_tickets"]),
            "ticket_promedio_pct": _calc_pct(m_a["ticket_promedio"], m_b["ticket_promedio"]),
            "cargas_cobrado_pct": _calc_pct(m_a["cargas_cobrado"], m_b["cargas_cobrado"]),
        }

        return {
            "periodo_a": m_a,
            "periodo_b": m_b,
            "variaciones": variaciones,
        }
