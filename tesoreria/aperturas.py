"""Gestión y Auditoría de Saldos Iniciales y Apertura de Tesorería.

Conforme a las directivas 2 y 3 del MEGA SYSTEM PROMPT:
- Inicialización estricta de todos los fondos físicos, digitales y obligaciones en $0.
- Consulta centralizada de aperturas y trazabilidad (quién configuró, cuándo, motivo).
- Modificación auditada append-only de saldos iniciales restringida a Alan y Yilda.
- Regla de inmutabilidad histórica: Si existen movimientos, no se altera silenciosamente
  la apertura previa para no reescribir la historia contable.
"""

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Dict, List, Optional
from tesoreria.money import assertValidMoney, formatMoney

FONDOS_FISICOS_CODIGOS = {"1110", "1120", "1130", "1140", "1150", "1160"}
CUENTAS_DIGITALES_CODIGOS = {"1210", "1220", "1230", "1310"}
OBLIGACIONES_CODIGOS = {"DAVID", "CLARO", "PREVENTISTAS"}


class ErrorAperturaTesoreria(Exception):
    """Error base en la configuración de aperturas de tesorería."""
    pass


class ErrorFondoConMovimientos(ErrorAperturaTesoreria):
    """Se intenta alterar la apertura retroactivamente en un fondo con movimientos activos."""
    pass


def obtener_saldos_iniciales_completos(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Obtiene el estado completo de saldos iniciales y actuales de fondos y obligaciones."""
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 1. Fondos
    cur.execute("""
        SELECT id, codigo, nombre, naturaleza, titularidad, saldo_centavos,
               COALESCE(saldo_inicial_cents, 0) as saldo_inicial_cents,
               saldo_inicial_configurado_por, saldo_inicial_configurado_en
        FROM tesoreria_fondos
        ORDER BY codigo ASC;
    """)
    fondos_rows = cur.fetchall()

    fondos_fisicos = []
    cuentas_digitales = []
    total_ff_cents = 0
    total_cd_cents = 0

    for f in fondos_rows:
        fondo_id = f["id"]
        cod = str(f["codigo"]).strip()
        nombre = f["nombre"]
        saldo_actual = int(f["saldo_centavos"])
        saldo_ini = int(f["saldo_inicial_cents"])
        config_por = f["saldo_inicial_configurado_por"] or "SISTEMA_INICIAL"
        config_en = f["saldo_inicial_configurado_en"] or "2026-11-05T00:00:00Z"

        # Comprobar movimientos posteriores
        cur.execute("SELECT COUNT(*) FROM tesoreria_movimientos WHERE fund_id = ?;", (fondo_id,))
        cant_movs = cur.fetchone()[0]
        tiene_movs = cant_movs > 0

        categoria = "FONDO_FISICO" if cod in FONDOS_FISICOS_CODIGOS else "CUENTA_DIGITAL"
        estado_conf = "EN_CERO" if saldo_ini == 0 else "CONFIGURADO"

        item = {
            "codigo": cod,
            "nombre": nombre,
            "categoria": categoria,
            "saldo_inicial_cents": saldo_ini,
            "saldo_actual_cents": saldo_actual,
            "saldo_inicial_formateado": formatMoney(saldo_ini),
            "saldo_actual_formateado": formatMoney(saldo_actual),
            "configurado_por": config_por,
            "configurado_en": config_en,
            "tiene_movimientos": tiene_movs,
            "puede_editar_apertura": not tiene_movs,
            "estado_configuracion": estado_conf,
        }

        if cod in FONDOS_FISICOS_CODIGOS:
            fondos_fisicos.append(item)
            total_ff_cents += saldo_ini
        else:
            cuentas_digitales.append(item)
            total_cd_cents += saldo_ini

    # 2. Obligaciones
    cur.execute("""
        SELECT codigo, nombre, categoria, saldo_inicial_cents, saldo_actual_cents,
               configurado_por, configurado_en, motivo, updated_at
        FROM tesoreria_obligaciones_apertura
        ORDER BY codigo ASC;
    """)
    oblig_rows = cur.fetchall()
    obligaciones = []
    total_ob_cents = 0

    for ob in oblig_rows:
        cod = str(ob["codigo"]).strip().upper()
        nombre = ob["nombre"]
        saldo_ini = int(ob["saldo_inicial_cents"])
        saldo_act = int(ob["saldo_actual_cents"])
        config_por = ob["configurado_por"] or "SISTEMA_INICIAL"
        config_en = ob["configurado_en"] or "2026-11-05T00:00:00Z"
        estado_conf = "EN_CERO" if saldo_ini == 0 else "CONFIGURADO"

        item = {
            "codigo": cod,
            "nombre": nombre,
            "categoria": "OBLIGACION",
            "saldo_inicial_cents": saldo_ini,
            "saldo_actual_cents": saldo_act,
            "saldo_inicial_formateado": formatMoney(saldo_ini),
            "saldo_actual_formateado": formatMoney(saldo_act),
            "configurado_por": config_por,
            "configurado_en": config_en,
            "tiene_movimientos": False,
            "puede_editar_apertura": True,
            "estado_configuracion": estado_conf,
        }
        obligaciones.append(item)
        total_ob_cents += saldo_ini

    return {
        "fondos_fisicos": fondos_fisicos,
        "cuentas_digitales": cuentas_digitales,
        "obligaciones": obligaciones,
        "total_fondos_fisicos_cents": total_ff_cents,
        "total_cuentas_digitales_cents": total_cd_cents,
        "total_obligaciones_cents": total_ob_cents,
    }


def modificar_saldo_inicial(
    conn: sqlite3.Connection,
    codigo: str,
    saldo_nuevo_cents: int,
    usuario: str,
    motivo: str,
    referencia_autorizacion: Optional[str] = None,
    jornada_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Modifica de manera auditada y append-only el saldo inicial de un fondo u obligación."""
    assertValidMoney(saldo_nuevo_cents)
    codigo_norm = str(codigo).strip().upper()
    motivo_clean = str(motivo or "").strip()
    if len(motivo_clean) < 3:
        raise ErrorAperturaTesoreria("El motivo de la modificación de saldo inicial es obligatorio y debe tener al menos 3 caracteres.")

    usuario_clean = str(usuario or "").strip()
    if not usuario_clean:
        raise ErrorAperturaTesoreria("El usuario responsable de la modificación es obligatorio.")

    now_iso = datetime.now(timezone.utc).isoformat()
    cur = conn.cursor()

    # Es fondo o es obligación?
    es_fondo = codigo_norm in FONDOS_FISICOS_CODIGOS or codigo_norm in CUENTAS_DIGITALES_CODIGOS
    es_obligacion = codigo_norm in OBLIGACIONES_CODIGOS

    if not es_fondo and not es_obligacion:
        raise ErrorAperturaTesoreria(f"Código no reconocido para saldos iniciales: {codigo_norm}")

    if es_fondo:
        cur.execute("""
            SELECT id, codigo, nombre, saldo_centavos, COALESCE(saldo_inicial_cents, 0) as saldo_inicial_cents
            FROM tesoreria_fondos WHERE codigo = ?;
        """, (codigo_norm,))
        row = cur.fetchone()
        if not row:
            raise ErrorAperturaTesoreria(f"Fondo no encontrado: {codigo_norm}")

        fondo_id = row[0]
        nombre = row[2]
        saldo_ant_cents = int(row[4])

        # Verificar si tiene movimientos registrados
        cur.execute("SELECT COUNT(*) FROM tesoreria_movimientos WHERE fund_id = ?;", (fondo_id,))
        cant_movs = cur.fetchone()[0]
        if cant_movs > 0:
            raise ErrorFondoConMovimientos(
                f"No está permitido alterar retroactivamente el saldo inicial del fondo {codigo_norm} "
                f"porque ya registra {cant_movs} movimientos contables. Realice un ajuste o arqueo formal."
            )

        categoria = "FONDO_FISICO" if codigo_norm in FONDOS_FISICOS_CODIGOS else "CUENTA_DIGITAL"

        # Actualizar apertura y saldo en tesoreria_fondos
        cur.execute("""
            UPDATE tesoreria_fondos
            SET saldo_inicial_cents = ?,
                saldo_centavos = ?,
                saldo_inicial_configurado_por = ?,
                saldo_inicial_configurado_en = ?,
                actualizado_por = ?,
                updated_at = ?
            WHERE id = ?;
        """, (saldo_nuevo_cents, saldo_nuevo_cents, usuario_clean, now_iso, usuario_clean, now_iso, fondo_id))

        # Registrar en auditoría append-only
        cur.execute("""
            INSERT INTO tesoreria_saldos_iniciales_auditoria (
                item_codigo, item_nombre, categoria, saldo_anterior_cents, saldo_nuevo_cents,
                fecha_hora, usuario_responsable, motivo, referencia_autorizacion, jornada_id, es_apertura_inicial
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0);
        """, (codigo_norm, nombre, categoria, saldo_ant_cents, saldo_nuevo_cents, now_iso, usuario_clean, motivo_clean, referencia_autorizacion, jornada_id))

        conn.commit()

        return {
            "item_codigo": codigo_norm,
            "item_nombre": nombre,
            "categoria": categoria,
            "saldo_anterior_cents": saldo_ant_cents,
            "saldo_nuevo_cents": saldo_nuevo_cents,
            "saldo_anterior_formateado": formatMoney(saldo_ant_cents),
            "saldo_nuevo_formateado": formatMoney(saldo_nuevo_cents),
            "usuario_responsable": usuario_clean,
            "motivo": motivo_clean,
            "referencia_autorizacion": referencia_autorizacion,
            "fecha_hora": now_iso,
        }

    else:
        # Obligación
        cur.execute("""
            SELECT codigo, nombre, saldo_inicial_cents FROM tesoreria_obligaciones_apertura WHERE codigo = ?;
        """, (codigo_norm,))
        row = cur.fetchone()
        if not row:
            raise ErrorAperturaTesoreria(f"Obligación no encontrada: {codigo_norm}")

        nombre = row[1]
        saldo_ant_cents = int(row[2])

        cur.execute("""
            UPDATE tesoreria_obligaciones_apertura
            SET saldo_inicial_cents = ?,
                saldo_actual_cents = ?,
                configurado_por = ?,
                configurado_en = ?,
                motivo = ?,
                referencia_autorizacion = ?,
                updated_at = ?
            WHERE codigo = ?;
        """, (saldo_nuevo_cents, saldo_nuevo_cents, usuario_clean, now_iso, motivo_clean, referencia_autorizacion, now_iso, codigo_norm))

        # Registrar en auditoría append-only
        cur.execute("""
            INSERT INTO tesoreria_saldos_iniciales_auditoria (
                item_codigo, item_nombre, categoria, saldo_anterior_cents, saldo_nuevo_cents,
                fecha_hora, usuario_responsable, motivo, referencia_autorizacion, jornada_id, es_apertura_inicial
            ) VALUES (?, ?, 'OBLIGACION', ?, ?, ?, ?, ?, ?, ?, 0);
        """, (codigo_norm, nombre, saldo_ant_cents, saldo_nuevo_cents, now_iso, usuario_clean, motivo_clean, referencia_autorizacion, jornada_id))

        conn.commit()

        return {
            "item_codigo": codigo_norm,
            "item_nombre": nombre,
            "categoria": "OBLIGACION",
            "saldo_anterior_cents": saldo_ant_cents,
            "saldo_nuevo_cents": saldo_nuevo_cents,
            "saldo_anterior_formateado": formatMoney(saldo_ant_cents),
            "saldo_nuevo_formateado": formatMoney(saldo_nuevo_cents),
            "usuario_responsable": usuario_clean,
            "motivo": motivo_clean,
            "referencia_autorizacion": referencia_autorizacion,
            "fecha_hora": now_iso,
        }


def obtener_historial_aperturas(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Consulta la pista inmutable append-only de modificaciones a saldos iniciales."""
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT id, item_codigo, item_nombre, categoria, saldo_anterior_cents, saldo_nuevo_cents,
               fecha_hora, usuario_responsable, motivo, referencia_autorizacion, es_apertura_inicial
        FROM tesoreria_saldos_iniciales_auditoria
        ORDER BY id DESC;
    """)
    rows = cur.fetchall()
    res = []
    for r in rows:
        d = dict(r)
        d["saldo_anterior_formateado"] = formatMoney(d["saldo_anterior_cents"])
        d["saldo_nuevo_formateado"] = formatMoney(d["saldo_nuevo_cents"])
        d["es_apertura_inicial"] = bool(d["es_apertura_inicial"])
        res.append(d)
    return res
