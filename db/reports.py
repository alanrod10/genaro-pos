"""Adaptador de reportes y métricas consolidadas del Dashboard para GenaroPOS.

Calcula métricas financieras consolidadas sobre la base central en Turso / SQLite:
- Caja A (Drugstore / Ventas con análisis de costo unitario histórico congelado)
- Caja B (SUBE / Prepay capital de servicios)
- Caja C (Adicionales y comisión neta de servicios)
- Caja D (Banco y transferencias netas conciliadas)
- Caja E (Claro telefonía capital)
- Gastos y retiros
- Conciliación de cajón físico (Arqueo)
- Serie temporal para evolución gráfica diaria
- Cierres diarios registrados en el período
"""

from datetime import datetime, timedelta
import logging
from typing import Any, Dict, List, Optional
import zoneinfo

from db.services import clasificar_servicio

logger = logging.getLogger("genaro_pos.reports")

TZ_ARGENTINA = zoneinfo.ZoneInfo("America/Argentina/Jujuy")


def _fecha_hoy_argentina() -> str:
    """Retorna la fecha actual en Argentina en formato ISO (YYYY-MM-DD)."""
    return datetime.now(TZ_ARGENTINA).strftime("%Y-%m-%d")


def _extraer_fecha_iso(valor: Optional[str]) -> str:
    """Normaliza cualquier formato de fecha (YYYY-MM-DD, DD/MM/YYYY, ISO 8601) a YYYY-MM-DD."""
    if not valor:
        return _fecha_hoy_argentina()
    s = str(valor).strip()
    if "/" in s:
        partes = s.split(" ")[0].split("/")
        if len(partes) >= 3:
            return f"{int(partes[2]):04d}-{int(partes[1]):02d}-{int(partes[0]):02d}"
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return _fecha_hoy_argentina()


def _fecha_hora_utc_a_local_iso(fecha_hora_str: Optional[str]) -> str:
    """Convierte un timestamp UTC almacenado en SQLite a la fecha local argentina (YYYY-MM-DD).

    Garantiza que ventas y movimientos realizados a última hora de la tarde o noche en Argentina
    (UTC-3) no se excluyan ni se trasladen erróneamente al día siguiente.
    """
    if not fecha_hora_str:
        return _fecha_hoy_argentina()
    s = str(fecha_hora_str).strip()
    if "/" in s:
        partes = s.split(" ")[0].split("/")
        if len(partes) >= 3:
            return f"{int(partes[2]):04d}-{int(partes[1]):02d}-{int(partes[0]):02d}"
    if len(s) >= 19 and (" " in s or "T" in s):
        try:
            iso_str = s.replace(" ", "T")
            if not iso_str.endswith("Z") and "+" not in iso_str[10:] and "-" not in iso_str[10:]:
                iso_str += "+00:00"
            dt = datetime.fromisoformat(iso_str)
            dt_local = dt.astimezone(TZ_ARGENTINA)
            return dt_local.strftime("%Y-%m-%d")
        except Exception:
            pass
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return _fecha_hoy_argentina()


def _tabla_existe(conn: Any, nombre_tabla: str) -> bool:
    """Verifica si una tabla existe en la base de datos."""
    try:
        cur = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (nombre_tabla,),
        )
        return cur.fetchone() is not None
    except Exception:
        return False


def _columna_existe(conn: Any, nombre_tabla: str, nombre_columna: str) -> bool:
    """Verifica si una columna existe dentro de una tabla."""
    try:
        cur = conn.execute(f"PRAGMA table_info({nombre_tabla})")
        columnas = [row[1] for row in cur.fetchall()]
        return nombre_columna in columnas
    except Exception:
        return False


def calcular_metricas_dashboard_turso(
    desde_param: Optional[str] = None,
    hasta_param: Optional[str] = None,
    periodo_tipo: str = "hoy",
    conn: Optional[Any] = None,
) -> Dict[str, Any]:
    """Calcula las métricas consolidadas del Dashboard Financiero sobre la persistencia central."""
    from db.expenses import _conexion

    hoy_iso = _fecha_hoy_argentina()
    hoy_dt = datetime.strptime(hoy_iso, "%Y-%m-%d")

    # 1. Definición estricta del rango de fechas
    if periodo_tipo == "hoy":
        desde = hoy_iso
        hasta = hoy_iso
    elif periodo_tipo == "ultimos_7_dias":
        d = hoy_dt - timedelta(days=6)
        desde = d.strftime("%Y-%m-%d")
        hasta = hoy_iso
    elif periodo_tipo == "ultimos_30_dias":
        d = hoy_dt - timedelta(days=29)
        desde = d.strftime("%Y-%m-%d")
        hasta = hoy_iso
    elif periodo_tipo == "personalizado":
        desde = _extraer_fecha_iso(desde_param) if desde_param else hoy_iso
        hasta = _extraer_fecha_iso(hasta_param) if hasta_param else hoy_iso
        if desde > hasta:
            desde, hasta = hasta, desde
    else:
        desde = hoy_iso
        hasta = hoy_iso

    # 2. Generar lista de días para serie temporal (máximo 31 días)
    dias_serie: List[str] = []
    try:
        curr = datetime.strptime(desde, "%Y-%m-%d")
        end = datetime.strptime(hasta, "%Y-%m-%d")
        safe_count = 0
        while curr <= end and safe_count <= 31:
            dias_serie.append(curr.strftime("%Y-%m-%d"))
            curr += timedelta(days=1)
            safe_count += 1
    except Exception:
        dias_serie.append(hoy_iso)

    if not dias_serie:
        dias_serie.append(hoy_iso)

    serie_map = {
        d: {
            "ventas_total": 0,
            "servicios_total": 0,
            "gastos_total": 0,
            "adicionales_ganancia": 0,
            "ventas_transf": 0,
            "recargas_transf": 0,
            "sube_mp_capital": 0,
            "gastos_transf": 0,
        }
        for d in dias_serie
    }

    # 3. Consultar datos centrales bajo conexión
    # Ventana de consulta SQL con margen de 1 día para garantizar cobertura de zona horaria (UTC vs Argentina UTC-3)
    try:
        desde_sql = (datetime.strptime(desde, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        hasta_sql = (datetime.strptime(hasta, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    except Exception:
        desde_sql = desde
        hasta_sql = hasta

    with _conexion(conn) as conexion:
        # Diagnóstico de integridad de esquema en base de datos
        tablas_faltantes = []
        if not _tabla_existe(conexion, "tickets"):
            tablas_faltantes.append("tickets")
        if not _tabla_existe(conexion, "cargas_servicios"):
            tablas_faltantes.append("cargas_servicios")
        if not _tabla_existe(conexion, "gastos_retiros"):
            tablas_faltantes.append("gastos_retiros")
        if not _tabla_existe(conexion, "cierres_diarios"):
            tablas_faltantes.append("cierres_diarios")

        esquema_completo = len(tablas_faltantes) == 0
        advertencia_esquema = None
        if not esquema_completo:
            advertencia_esquema = (
                f"Esquema incompleto en base de datos: faltan las tablas requeridas: "
                f"{', '.join(tablas_faltantes)}. Los valores reflejan datos parciales "
                f"y no constituyen un cierre contable definitivo hasta aplicar las migraciones pendientes."
            )

        # A. VENTAS Y ANÁLISIS DE COSTO HISTÓRICO CONGELADO (CAJA A)
        total_ventas = 0
        ventas_efvo = 0
        ventas_transf = 0
        cantidad_tickets = 0

        costo_historico_total = 0
        suma_ganancia_bruta = 0
        suma_subtotal_con_costo = 0
        ventas_con_costo_completo = 0
        ventas_con_costo_incompleto = 0
        items_con_costo_desconocido = 0

        if _tabla_existe(conexion, "tickets"):
            # Detectar nombres de columnas de clave primaria y montos
            pk_col = "id" if _columna_existe(conexion, "tickets", "id") else "ticket_id"
            transf_col = (
                "monto_transferencia"
                if _columna_existe(conexion, "tickets", "monto_transferencia")
                else "monto_transf"
            )
            has_anulado = _columna_existe(conexion, "tickets", "anulado")
            anulado_clause = "WHERE anulado = 0" if has_anulado else "WHERE 1=1"

            query_tickets = f"""
                SELECT {pk_col}, fecha_hora_utc, total_venta, monto_efectivo, {transf_col}
                FROM tickets
                {anulado_clause}
                AND substr(fecha_hora_utc, 1, 10) >= ?
                AND substr(fecha_hora_utc, 1, 10) <= ?
            """
            filas_tickets = conexion.execute(query_tickets, (desde_sql, hasta_sql)).fetchall()

            has_ticket_items = _tabla_existe(conexion, "ticket_items")
            has_costo = has_ticket_items and _columna_existe(conexion, "ticket_items", "costo_unitario")

            for t_row in filas_tickets:
                f_iso = _fecha_hora_utc_a_local_iso(t_row[1])
                if f_iso < desde or f_iso > hasta:
                    continue

                t_id = str(t_row[0])
                tot = int(t_row[2] or 0)
                efv = int(t_row[3] or 0)
                trf = int(t_row[4] or 0)
                total_ventas += tot
                ventas_efvo += efv
                ventas_transf += trf
                cantidad_tickets += 1

                # Análisis de costos congelados en ticket_items
                if has_ticket_items and has_costo:
                    items_rows = conexion.execute(
                        "SELECT cantidad, costo_unitario, subtotal FROM ticket_items WHERE ticket_id = ?",
                        (t_id,),
                    ).fetchall()

                    if not items_rows:
                        ventas_con_costo_incompleto += 1
                    else:
                        ticket_tiene_todos_costos = True
                        ticket_costo_total = 0
                        for it_cant, it_costo, _ in items_rows:
                            if it_costo is not None:
                                ticket_costo_total += int(it_costo) * float(it_cant or 1)
                            else:
                                ticket_tiene_todos_costos = False
                                items_con_costo_desconocido += 1

                        if ticket_tiene_todos_costos:
                            ventas_con_costo_completo += 1
                            costo_int = int(round(ticket_costo_total))
                            costo_historico_total += costo_int
                            suma_ganancia_bruta += (tot - costo_int)
                            suma_subtotal_con_costo += tot
                        else:
                            ventas_con_costo_incompleto += 1
                else:
                    ventas_con_costo_incompleto += 1

                if f_iso in serie_map:
                    serie_map[f_iso]["ventas_total"] += tot
                    serie_map[f_iso]["ventas_transf"] += trf

        ganancia_bruta_real = (
            int(round(suma_ganancia_bruta)) if ventas_con_costo_completo > 0 else None
        )
        margen_bruto_porcentaje = (
            round((suma_ganancia_bruta / suma_subtotal_con_costo) * 1000) / 10
            if (ventas_con_costo_completo > 0 and suma_subtotal_con_costo > 0)
            else None
        )
        tiene_costos_completos = (
            ventas_con_costo_completo > 0 and ventas_con_costo_incompleto == 0
        )

        # B. SERVICIOS Y CARGAS (CAJAS B, C, D recargas, E)
        d_recargas = 0
        servicios_efvo_total = 0

        b_total = 0
        b_efvo = 0
        b_transf = 0
        b_count = 0

        c_total = 0
        c_efvo = 0
        c_transf = 0
        c_count = 0

        e_total = 0
        e_efvo = 0
        e_transf = 0
        e_count = 0

        sube_mp_capital = 0
        desglose_servicios_map: Dict[str, Dict[str, Any]] = {}

        if _tabla_existe(conexion, "cargas_servicios"):
            has_anulado_cargas = _columna_existe(conexion, "cargas_servicios", "anulado")
            has_plat_origen = _columna_existe(conexion, "cargas_servicios", "plataforma_origen")
            anulado_cargas_clause = "WHERE anulado = 0" if has_anulado_cargas else "WHERE 1=1"
            plat_select = "plataforma_origen" if has_plat_origen else "NULL"

            query_cargas = f"""
                SELECT fecha_hora_utc, servicio, monto_carga, monto_adicional,
                       total_cobrado, pago_efectivo, pago_transferencia, {plat_select}
                FROM cargas_servicios
                {anulado_cargas_clause}
                AND substr(fecha_hora_utc, 1, 10) >= ?
                AND substr(fecha_hora_utc, 1, 10) <= ?
            """
            filas_cargas = conexion.execute(query_cargas, (desde_sql, hasta_sql)).fetchall()

            for c_row in filas_cargas:
                f_iso = _fecha_hora_utc_a_local_iso(c_row[0])
                if f_iso < desde or f_iso > hasta:
                    continue

                plat_raw = c_row[7] if len(c_row) > 7 else None
                categoria_srv = clasificar_servicio(c_row[1], plat_raw)
                monto_carga = int(c_row[2] or 0)
                monto_adic = int(c_row[3] or 0)
                tot_cobrado = int(c_row[4] or 0)
                p_efvo = int(c_row[5] or 0)
                p_transf = int(c_row[6] or 0)

                d_recargas += p_transf
                servicios_efvo_total += p_efvo

                cap_transf = min(p_transf, monto_carga)
                cap_efvo = max(0, monto_carga - cap_transf)
                adic_transf = max(0, p_transf - monto_carga)
                adic_efvo = max(0, monto_adic - adic_transf)

                if categoria_srv == "CLARO":
                    e_total += monto_carga
                    e_efvo += cap_efvo
                    e_transf += cap_transf
                    e_count += 1
                elif categoria_srv == "SUBE_MP":
                    sube_mp_capital += monto_carga
                else:
                    b_total += monto_carga
                    b_efvo += cap_efvo
                    b_transf += cap_transf
                    b_count += 1

                c_total += monto_adic
                c_efvo += adic_efvo
                c_transf += adic_transf
                c_count += 1

                srv_nombre = str(c_row[1] or "Servicio").strip()
                plat_origen = (
                    str(plat_raw).strip()
                    if plat_raw and str(plat_raw).strip()
                    else "Histórico (sin origen especificado)"
                )
                key_desg = f"{plat_origen}::{srv_nombre}"
                if key_desg not in desglose_servicios_map:
                    desglose_servicios_map[key_desg] = {
                        "plataforma_origen": plat_origen,
                        "servicio_destino": srv_nombre,
                        "categoria": categoria_srv,
                        "cantidad_operaciones": 0,
                        "capital_total": 0,
                        "adicional_total": 0,
                        "total_cobrado": 0,
                        "cobrado_efectivo": 0,
                        "cobrado_transferencia": 0,
                        "medio_pago": "EFECTIVO",
                        "fondo_receptor": "",
                        "cuenta_utilizada": (
                            "1230 — Claro Comercios Digital"
                            if plat_origen == "CLARO COMERCIOS" or categoria_srv == "CLARO"
                            else "1210 — Mercado Pago Propio"
                            if plat_origen == "MERCADO PAGO" or categoria_srv == "SUBE_MP"
                            else "1220 — PREPAY Digital"
                        ),
                    }
                desglose_servicios_map[key_desg]["cantidad_operaciones"] += 1
                desglose_servicios_map[key_desg]["capital_total"] += monto_carga
                desglose_servicios_map[key_desg]["adicional_total"] += monto_adic
                desglose_servicios_map[key_desg]["total_cobrado"] += tot_cobrado
                desglose_servicios_map[key_desg]["cobrado_efectivo"] += p_efvo
                desglose_servicios_map[key_desg]["cobrado_transferencia"] += p_transf
                tot_efvo_d = desglose_servicios_map[key_desg]["cobrado_efectivo"]
                tot_trf_d = desglose_servicios_map[key_desg]["cobrado_transferencia"]
                desglose_servicios_map[key_desg]["medio_pago"] = (
                    "MIXTO"
                    if tot_efvo_d > 0 and tot_trf_d > 0
                    else "TRANSFERENCIA"
                    if tot_trf_d > 0
                    else "EFECTIVO"
                )
                if plat_origen == "CLARO COMERCIOS" or categoria_srv == "CLARO":
                    desglose_servicios_map[key_desg]["fondo_receptor"] = (
                        "1150 (Claro físico) + 1140 (Adicionales) / 1210 (Mercado Pago)"
                        if tot_efvo_d > 0 and tot_trf_d > 0
                        else "1210 (Mercado Pago Propio)"
                        if tot_trf_d > 0
                        else "1150 (Claro físico) + 1140 (Adicionales)"
                    )
                elif plat_origen == "MERCADO PAGO" or categoria_srv == "SUBE_MP":
                    desglose_servicios_map[key_desg]["fondo_receptor"] = (
                        "1110 (Drugstore diario) + 1140 (Adicionales) / 1210 (Mercado Pago)"
                        if tot_efvo_d > 0 and tot_trf_d > 0
                        else "1210 (Mercado Pago Propio)"
                        if tot_trf_d > 0
                        else "1110 (Drugstore diario) + 1140 (Adicionales)"
                    )
                else:
                    desglose_servicios_map[key_desg]["fondo_receptor"] = (
                        "1160 (PREPAY diario) + 1140 (Adicionales) / 1210 (MP) + Comp. 1120 → 1130"
                        if tot_efvo_d > 0 and tot_trf_d > 0
                        else "1210 (Mercado Pago) + Compensación 1120 → 1130"
                        if tot_trf_d > 0
                        else "1160 (PREPAY diario) + 1140 (Adicionales)"
                    )

                if f_iso in serie_map:
                    serie_map[f_iso]["servicios_total"] += tot_cobrado
                    serie_map[f_iso]["adicionales_ganancia"] += monto_adic
                    serie_map[f_iso]["recargas_transf"] += p_transf
                    if categoria_srv == "SUBE_MP":
                        serie_map[f_iso]["sube_mp_capital"] += monto_carga

        # C. GASTOS Y RETIROS
        gastos_efvo = 0
        gastos_transf = 0
        cantidad_gastos = 0

        if _tabla_existe(conexion, "gastos_retiros"):
            has_anulado_gastos = _columna_existe(conexion, "gastos_retiros", "anulado")
            anulado_gastos_clause = "WHERE anulado = 0" if has_anulado_gastos else "WHERE 1=1"

            query_gastos = f"""
                SELECT fecha_hora_utc, monto_salida, metodo_pago
                FROM gastos_retiros
                {anulado_gastos_clause}
                AND substr(fecha_hora_utc, 1, 10) >= ?
                AND substr(fecha_hora_utc, 1, 10) <= ?
            """
            filas_gastos = conexion.execute(query_gastos, (desde_sql, hasta_sql)).fetchall()

            for g_row in filas_gastos:
                f_iso = _fecha_hora_utc_a_local_iso(g_row[0])
                if f_iso < desde or f_iso > hasta:
                    continue

                monto = int(g_row[1] or 0)
                metodo = str(g_row[2] or "EFECTIVO").upper()

                if metodo == "EFECTIVO":
                    gastos_efvo += monto
                else:
                    gastos_transf += monto
                cantidad_gastos += 1

                if f_iso in serie_map:
                    serie_map[f_iso]["gastos_total"] += monto
                    if metodo == "TRANSFERENCIA":
                        serie_map[f_iso]["gastos_transf"] += monto

        # D. CIERRES DIARIOS
        cierres_list: List[Dict[str, Any]] = []
        if _tabla_existe(conexion, "cierres_diarios"):
            has_cerrado_en = _columna_existe(conexion, "cierres_diarios", "cerrado_en_utc")
            cerrado_en_select = "cerrado_en_utc" if has_cerrado_en else "''"
            query_cierres = f"""
                SELECT id, fecha, efectivo_esperado, efectivo_contado, diferencia,
                       total_ventas_efvo, total_ventas_transf, total_servicios_efvo, total_servicios_transf,
                       total_gastos_efvo, total_gastos_transf, total_banco, observaciones, cerrado_por, {cerrado_en_select}
                FROM cierres_diarios
                WHERE fecha >= ? AND fecha <= ?
                ORDER BY fecha DESC
            """
            filas_cierres = conexion.execute(query_cierres, (desde, hasta)).fetchall()
            for r in filas_cierres:
                cierres_list.append({
                    "id": r[0],
                    "fecha": r[1],
                    "efectivo_esperado": r[2],
                    "efectivo_contado": r[3],
                    "diferencia": r[4],
                    "total_ventas_efvo": r[5],
                    "total_ventas_transf": r[6],
                    "total_servicios_efvo": r[7],
                    "total_servicios_transf": r[8],
                    "total_gastos_efvo": r[9],
                    "total_gastos_transf": r[10],
                    "total_banco": r[11],
                    "observaciones": r[12] or "",
                    "cerrado_por": r[13],
                    "cerrado_en_utc": r[14] or "",
                })

    # 4. Conciliaciones y cálculos finales
    # Caja A: Efectivo disponible atribuible exclusivamente a ventas drugstore
    efectivo_disponible_drugstore = ventas_efvo
    total_banco_neto = ventas_transf + d_recargas - sube_mp_capital - gastos_transf
    # Arqueo físico de cajón: exclusivamente movimientos de efectivo físico real
    # (sin restar d_recargas que son transferencias bancarias ni ocultar faltantes con max(0, ...))
    efectivo_esperado_cajon = ventas_efvo + servicios_efvo_total - gastos_efvo

    # 5. Formatear serie temporal
    serie_temporal = []
    for d_iso in dias_serie:
        s = serie_map.get(d_iso, {})
        b_neto = (
            s.get("ventas_transf", 0)
            + s.get("recargas_transf", 0)
            - s.get("sube_mp_capital", 0)
            - s.get("gastos_transf", 0)
        )
        etiqueta = d_iso[8:10] + "/" + d_iso[5:7]  # DD/MM
        serie_temporal.append({
            "fecha": d_iso,
            "etiqueta": etiqueta,
            "ventas_total": s.get("ventas_total", 0),
            "servicios_total": s.get("servicios_total", 0),
            "gastos_total": s.get("gastos_total", 0),
            "adicionales_ganancia": s.get("adicionales_ganancia", 0),
            "banco_neto": b_neto,
        })

    return {
        "periodo_tipo": periodo_tipo,
        "desde": desde,
        "hasta": hasta,
        "desglose_servicios": list(desglose_servicios_map.values()),
        "caja_a": {
            "total_ventas": total_ventas,
            "ventas_efvo": ventas_efvo,
            "ventas_transf": ventas_transf,
            "efectivo_disponible_drugstore": efectivo_disponible_drugstore,
            "ganancia_estimada_ventas": int(round(total_ventas * 0.10)),
            "es_estimacion_heredada": True,
            "cantidad_tickets": cantidad_tickets,
            "ganancia_bruta_real": ganancia_bruta_real,
            "costo_historico_total": costo_historico_total,
            "ventas_con_costo_completo": ventas_con_costo_completo,
            "ventas_con_costo_incompleto": ventas_con_costo_incompleto,
            "items_con_costo_desconocido": items_con_costo_desconocido,
            "margen_bruto_porcentaje": margen_bruto_porcentaje,
            "tiene_costos_completos": tiene_costos_completos,
        },
        "caja_b": {
            "capital_total": b_total,
            "capital_efvo": b_efvo,
            "capital_transf": b_transf,
            "cantidad_operaciones": b_count,
        },
        "caja_c": {
            "ganancia_adicional_total": c_total,
            "adicional_efvo": c_efvo,
            "adicional_transf": c_transf,
            "cantidad_operaciones": c_count,
        },
        "caja_d": {
            "ventas_transf": ventas_transf,
            "recargas_transf": d_recargas,
            "sube_mp_capital": sube_mp_capital,
            "gastos_transf": gastos_transf,
            "total_banco_neto": total_banco_neto,
        },
        "caja_e": {
            "capital_total": e_total,
            "capital_efvo": e_efvo,
            "capital_transf": e_transf,
            "cantidad_operaciones": e_count,
        },
        "gastos": {
            "gastos_total": gastos_efvo + gastos_transf,
            "gastos_efvo": gastos_efvo,
            "gastos_transf": gastos_transf,
            "cantidad_gastos": cantidad_gastos,
        },
        "conciliacion_cajon": {
            "ventas_efvo": ventas_efvo,
            "servicios_efvo": servicios_efvo_total,
            "gastos_efvo": gastos_efvo,
            "d_recargas": d_recargas,
            "efectivo_esperado_cajon": efectivo_esperado_cajon,
        },
        "serie_temporal": serie_temporal,
        "cierres": cierres_list,
        "esquema_completo": esquema_completo,
        "tablas_faltantes": tablas_faltantes,
        "advertencia_esquema": advertencia_esquema,
    }
