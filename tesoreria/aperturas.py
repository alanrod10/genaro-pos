"""Gestión y Auditoría de Saldos Iniciales y Apertura de Tesorería Diferida.

Conforme a las directivas de Apertura Económica Diferida (Etapa V):
- Estados canónicos de apertura: NOT_CONFIGURED, PENDING_OPENING, OPENED, RECONCILIATION_REQUIRED, BLOCKED.
- Ningún fondo ni obligación abre con disponibilidad ficticia ni con saldo inventado.
- La ausencia de saldos iniciales se representa con estado persistente PENDING_OPENING.
- Si se registran ventas o ingresos comerciales antes de la apertura, se identifica:
  'SALDO INICIAL PENDIENTE — MOVIMIENTOS POSTERIORES REGISTRADOS'.
- Asistente de apertura manual para Alan y Yilda:
  * Registro de saldo real por fondo u obligación individual.
  * Fecha y hora de corte (fecha_corte).
  * Naturaleza del saldo (EFECTIVO_CONTADO, SALDO_PLATAFORMA, OBLIGACION_EXIGIBLE).
  * Observaciones y referencia o comprobante.
  * Prevención estricta contra duplicaciones mediante Idempotency Key.
  * Modificación posterior mediante Ajustes Compensatorios auditados (sin sobrescribir historia).
"""

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Dict, List, Optional
from tesoreria.money import assertValidMoney, formatMoney

FONDOS_FISICOS_CODIGOS = {"1110", "1120", "1130", "1140", "1150", "1160"}
CUENTAS_DIGITALES_CODIGOS = {"1210", "1220", "1230", "1310"}
OBLIGACIONES_CODIGOS = {"DAVID", "CLARO", "PREVENTISTAS"}

ESTADOS_APERTURA_VALIDOS = {
    "NOT_CONFIGURED",
    "PENDING_OPENING",
    "OPENED",
    "RECONCILIATION_REQUIRED",
    "BLOCKED",
}


class ErrorAperturaTesoreria(Exception):
    """Error base en la configuración de aperturas de tesorería."""
    pass


class ErrorFondoConMovimientos(ErrorAperturaTesoreria):
    """Se intenta alterar la apertura retroactivamente en un fondo con movimientos activos sin ajuste formal."""
    pass


class ErrorAperturaPendiente(ErrorAperturaTesoreria):
    """Se intenta ejecutar una operación de egreso o liquidación sobre un fondo sin saldo inicial verificado."""
    pass


def _asegurar_columnas_apertura(conn: sqlite3.Connection) -> None:
    """Garantiza la presencia de columnas de apertura diferida en tablas SQLite."""
    tablas_columnas = {
        "tesoreria_fondos": [
            ("estado_apertura", "TEXT NOT NULL DEFAULT 'PENDING_OPENING'"),
            ("fecha_corte", "TEXT DEFAULT NULL"),
            ("naturaleza_saldo", "TEXT DEFAULT NULL"),
            ("observaciones_apertura", "TEXT DEFAULT NULL"),
            ("comprobante_apertura", "TEXT DEFAULT NULL"),
            ("idempotency_key", "TEXT DEFAULT NULL"),
        ],
        "tesoreria_obligaciones_apertura": [
            ("estado_apertura", "TEXT NOT NULL DEFAULT 'PENDING_OPENING'"),
            ("fecha_corte", "TEXT DEFAULT NULL"),
            ("observaciones_apertura", "TEXT DEFAULT NULL"),
            ("comprobante_apertura", "TEXT DEFAULT NULL"),
            ("idempotency_key", "TEXT DEFAULT NULL"),
        ],
        "tesoreria_saldos_iniciales_auditoria": [
            ("fecha_corte", "TEXT DEFAULT NULL"),
            ("naturaleza_saldo", "TEXT DEFAULT NULL"),
            ("idempotency_key", "TEXT DEFAULT NULL"),
            ("tipo_operacion", "TEXT NOT NULL DEFAULT 'APERTURA_INICIAL'"),
            ("estado_apertura", "TEXT NOT NULL DEFAULT 'OPENED'"),
        ],
    }
    cur = conn.cursor()
    for tabla, columnas in tablas_columnas.items():
        # Comprobar si la tabla existe
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?;", (tabla,))
        if not cur.fetchone():
            continue
        cols_existentes = {col[1] for col in cur.execute(f"PRAGMA table_info({tabla});").fetchall()}
        for col_name, col_def in columnas:
            if col_name not in cols_existentes:
                try:
                    conn.execute(f"ALTER TABLE {tabla} ADD COLUMN {col_name} {col_def};")
                except sqlite3.OperationalError:
                    pass


def obtener_saldos_iniciales_completos(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Obtiene el estado completo de saldos iniciales, estado de apertura y trazabilidad."""
    _asegurar_columnas_apertura(conn)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 1. Fondos
    cur.execute("""
        SELECT id, codigo, nombre, naturaleza, titularidad, saldo_centavos,
               COALESCE(saldo_inicial_cents, 0) as saldo_inicial_cents,
               saldo_inicial_configurado_por, saldo_inicial_configurado_en,
               estado_apertura, fecha_corte, naturaleza_saldo, observaciones_apertura, comprobante_apertura
        FROM tesoreria_fondos
        ORDER BY codigo ASC;
    """)
    fondos_rows = cur.fetchall()

    fondos_fisicos = []
    cuentas_digitales = []
    total_ff_cents = 0
    total_cd_cents = 0
    fondos_abiertos = 0
    fondos_pendientes = 0

    for f in fondos_rows:
        fondo_id = f["id"]
        cod = str(f["codigo"]).strip()
        nombre = f["nombre"]
        saldo_actual = int(f["saldo_centavos"])
        saldo_ini = int(f["saldo_inicial_cents"])
        config_por = f["saldo_inicial_configurado_por"] or "SISTEMA_INICIAL"
        config_en = f["saldo_inicial_configurado_en"]
        raw_estado_apertura = f["estado_apertura"] if "estado_apertura" in f.keys() else None

        # Comprobar movimientos posteriores en tesoreria_movimientos
        cur.execute("SELECT COUNT(*) FROM tesoreria_movimientos WHERE fund_id = ?;", (fondo_id,))
        cant_movs = cur.fetchone()[0]
        tiene_movs = cant_movs > 0

        # Determinar estado canónico de apertura
        if raw_estado_apertura and raw_estado_apertura in ESTADOS_APERTURA_VALIDOS:
            estado_ap = raw_estado_apertura
        else:
            if not config_en or config_por == "SISTEMA_INICIAL":
                estado_ap = "PENDING_OPENING"
            else:
                estado_ap = "OPENED"

        if estado_ap == "OPENED":
            fondos_abiertos += 1
            disponibilidad_confirmada = True
            subestado = "CONFIRMADO"
            estado_conf = "CONFIGURADO"
        else:
            fondos_pendientes += 1
            disponibilidad_confirmada = False
            subestado = "MOVIMIENTOS_POSTERIORES_REGISTRADOS" if tiene_movs else "SIN_MOVIMIENTOS"
            estado_conf = "EN_CERO"

        categoria = "FONDO_FISICO" if cod in FONDOS_FISICOS_CODIGOS else "CUENTA_DIGITAL"

        item = {
            "codigo": cod,
            "nombre": nombre,
            "categoria": categoria,
            "naturaleza": f["naturaleza"],
            "saldo_inicial_cents": saldo_ini,
            "saldo_actual_cents": saldo_actual,
            "saldo_inicial_formateado": formatMoney(saldo_ini),
            "saldo_actual_formateado": formatMoney(saldo_actual),
            "estado_apertura": estado_ap,
            "subestado_apertura": subestado,
            "disponibilidad_confirmada": disponibilidad_confirmada,
            "fecha_corte": f["fecha_corte"] if "fecha_corte" in f.keys() else None,
            "naturaleza_saldo": f["naturaleza_saldo"] if "naturaleza_saldo" in f.keys() else None,
            "observaciones": f["observaciones_apertura"] if "observaciones_apertura" in f.keys() else None,
            "comprobante": f["comprobante_apertura"] if "comprobante_apertura" in f.keys() else None,
            "configurado_por": config_por,
            "configurado_en": config_en,
            "tiene_movimientos": tiene_movs,
            "puede_editar_apertura": True,
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
               configurado_por, configurado_en, motivo, updated_at,
               estado_apertura, fecha_corte, observaciones_apertura, comprobante_apertura
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
        config_en = ob["configurado_en"]
        raw_estado_apertura = ob["estado_apertura"] if "estado_apertura" in ob.keys() else None

        if raw_estado_apertura and raw_estado_apertura in ESTADOS_APERTURA_VALIDOS:
            estado_ap = raw_estado_apertura
        else:
            if not config_en or config_por == "SISTEMA_INICIAL":
                estado_ap = "PENDING_OPENING"
            else:
                estado_ap = "OPENED"

        estado_conf = "CONFIGURADO" if estado_ap == "OPENED" else "EN_CERO"

        item = {
            "codigo": cod,
            "nombre": nombre,
            "categoria": "OBLIGACION",
            "saldo_inicial_cents": saldo_ini,
            "saldo_actual_cents": saldo_act,
            "saldo_inicial_formateado": formatMoney(saldo_ini),
            "saldo_actual_formateado": formatMoney(saldo_act),
            "estado_apertura": estado_ap,
            "disponibilidad_confirmada": (estado_ap == "OPENED"),
            "fecha_corte": ob["fecha_corte"] if "fecha_corte" in ob.keys() else None,
            "observaciones": ob["observaciones_apertura"] if "observaciones_apertura" in ob.keys() else ob["motivo"],
            "comprobante": ob["comprobante_apertura"] if "comprobante_apertura" in ob.keys() else None,
            "configurado_por": config_por,
            "configurado_en": config_en,
            "tiene_movimientos": False,
            "puede_editar_apertura": True,
            "estado_configuracion": estado_conf,
        }
        obligaciones.append(item)
        total_ob_cents += saldo_ini

    apertura_completa = (fondos_pendientes == 0)
    estado_general = "COMPLETO" if apertura_completa else "DIFERIDO_PENDIENTE"

    return {
        "fondos_fisicos": fondos_fisicos,
        "cuentas_digitales": cuentas_digitales,
        "obligaciones": obligaciones,
        "total_fondos_fisicos_cents": total_ff_cents,
        "total_cuentas_digitales_cents": total_cd_cents,
        "total_obligaciones_cents": total_ob_cents,
        "conteo_abiertos": fondos_abiertos,
        "conteo_pendientes": fondos_pendientes,
        "apertura_completa": apertura_completa,
        "estado_general_apertura": estado_general,
    }


def modificar_saldo_inicial(
    conn: sqlite3.Connection,
    codigo: str,
    saldo_nuevo_cents: int,
    usuario: str,
    motivo: str,
    referencia_autorizacion: Optional[str] = None,
    fecha_corte: Optional[str] = None,
    naturaleza_saldo: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    tipo_operacion: str = "APERTURA_INICIAL",
    jornada_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Registra de manera auditada y append-only la apertura inicial o un ajuste compensatorio.
    
    Protecciones:
    - Idempotencia estricta: reintentos con la misma idempotency_key devuelven el resultado previo sin duplicar.
    - Apertura diferida: fondos en PENDING_OPENING se abren y conservan cualquier movimiento comercial posterior.
    - Si el fondo ya estaba ABIERTO y posee movimientos:
      * Se bloquea la alteración silenciosa retroactiva (ErrorFondoConMovimientos).
      * Si se especifica tipo_operacion='AJUSTE_COMPENSATORIO', se registra formalmente el delta y auditoría.
    """
    _asegurar_columnas_apertura(conn)
    assertValidMoney(saldo_nuevo_cents)
    codigo_norm = str(codigo).strip().upper()
    if codigo_norm.startswith("OBL_"):
        codigo_norm = codigo_norm[4:]

    motivo_clean = str(motivo or "").strip()
    if len(motivo_clean) < 3:
        raise ErrorAperturaTesoreria("El motivo de la modificación de saldo inicial es obligatorio y debe tener al menos 3 caracteres.")

    usuario_clean = str(usuario or "").strip()
    if not usuario_clean:
        raise ErrorAperturaTesoreria("El usuario responsable de la modificación es obligatorio.")

    idempotency_clean = str(idempotency_key or "").strip() or None

    # Idempotencia: Verificar si esta misma clave ya fue procesada
    if idempotency_clean:
        cur_idem = conn.cursor()
        cur_idem.execute("""
            SELECT item_codigo, item_nombre, categoria, saldo_anterior_cents, saldo_nuevo_cents,
                   usuario_responsable, motivo, referencia_autorizacion, fecha_hora, tipo_operacion
            FROM tesoreria_saldos_iniciales_auditoria
            WHERE idempotency_key = ?;
        """, (idempotency_clean,))
        idem_row = cur_idem.fetchone()
        if idem_row:
            return {
                "item_codigo": idem_row[0],
                "item_nombre": idem_row[1],
                "categoria": idem_row[2],
                "saldo_anterior_cents": idem_row[3],
                "saldo_nuevo_cents": idem_row[4],
                "saldo_anterior_formateado": formatMoney(idem_row[3]),
                "saldo_nuevo_formateado": formatMoney(idem_row[4]),
                "usuario_responsable": idem_row[5],
                "motivo": idem_row[6],
                "referencia_autorizacion": idem_row[7],
                "fecha_hora": idem_row[8],
                "tipo_operacion": idem_row[9],
                "idempotente": True,
            }

    now_iso = datetime.now(timezone.utc).isoformat()
    corte_iso = fecha_corte.strip() if (fecha_corte and str(fecha_corte).strip()) else now_iso
    cur = conn.cursor()

    es_fondo = codigo_norm in FONDOS_FISICOS_CODIGOS or codigo_norm in CUENTAS_DIGITALES_CODIGOS
    es_obligacion = codigo_norm in OBLIGACIONES_CODIGOS

    if not es_fondo and not es_obligacion:
        raise ErrorAperturaTesoreria(f"Código no reconocido para saldos iniciales: {codigo_norm}")

    if es_fondo:
        cur.execute("""
            SELECT id, codigo, nombre, saldo_centavos, COALESCE(saldo_inicial_cents, 0) as saldo_inicial_cents,
                   estado_apertura
            FROM tesoreria_fondos WHERE codigo = ?;
        """, (codigo_norm,))
        row = cur.fetchone()
        if not row:
            raise ErrorAperturaTesoreria(f"Fondo no encontrado: {codigo_norm}")

        fondo_id = row[0]
        nombre = row[2]
        saldo_actual_cents = int(row[3])
        saldo_ini_ant_cents = int(row[4])
        estado_ap_actual = row[5] or "PENDING_OPENING"

        # Comprobar movimientos contables en tesoreria_movimientos
        cur.execute("SELECT COUNT(*) FROM tesoreria_movimientos WHERE fund_id = ?;", (fondo_id,))
        cant_movs = cur.fetchone()[0]

        categoria = "FONDO_FISICO" if codigo_norm in FONDOS_FISICOS_CODIGOS else "CUENTA_DIGITAL"
        tipo_op = str(tipo_operacion or "APERTURA_INICIAL").upper()

        if cant_movs > 0 and tipo_op != "AJUSTE_COMPENSATORIO":
            raise ErrorFondoConMovimientos(
                f"No está permitido alterar retroactivamente el saldo inicial del fondo {codigo_norm} "
                f"porque ya registra {cant_movs} movimientos contables. Realice un ajuste o arqueo formal."
            )

        if tipo_op == "AJUSTE_COMPENSATORIO":
            delta_cents = saldo_nuevo_cents - saldo_ini_ant_cents
            nuevo_saldo_centavos = saldo_actual_cents + delta_cents
            es_apertura_flag = 0
            op_label = "AJUSTE_COMPENSATORIO"
        else:
            # APERTURA INICIAL: Se incorpora el saldo verificado sumándolo a cualquier movimiento previo
            delta_cents = saldo_nuevo_cents
            nuevo_saldo_centavos = saldo_actual_cents + saldo_nuevo_cents
            es_apertura_flag = 1
            op_label = "APERTURA_INICIAL"

        # Actualizar tesoreria_fondos
        cur.execute("""
            UPDATE tesoreria_fondos
            SET saldo_inicial_cents = ?,
                saldo_centavos = ?,
                estado_apertura = 'OPENED',
                fecha_corte = ?,
                naturaleza_saldo = ?,
                observaciones_apertura = ?,
                comprobante_apertura = ?,
                idempotency_key = ?,
                saldo_inicial_configurado_por = ?,
                saldo_inicial_configurado_en = ?,
                actualizado_por = ?,
                updated_at = ?
            WHERE id = ?;
        """, (
            saldo_nuevo_cents,
            nuevo_saldo_centavos,
            corte_iso,
            naturaleza_saldo or ("EFECTIVO_CONTADO" if categoria == "FONDO_FISICO" else "SALDO_PLATAFORMA"),
            motivo_clean,
            referencia_autorizacion,
            idempotency_clean,
            usuario_clean,
            now_iso,
            usuario_clean,
            now_iso,
            fondo_id,
        ))

        # Registrar en auditoría inmutable append-only
        cur.execute("""
            INSERT INTO tesoreria_saldos_iniciales_auditoria (
                item_codigo, item_nombre, categoria, saldo_anterior_cents, saldo_nuevo_cents,
                fecha_hora, usuario_responsable, motivo, referencia_autorizacion, jornada_id,
                es_apertura_inicial, fecha_corte, naturaleza_saldo, idempotency_key, tipo_operacion, estado_apertura
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPENED');
        """, (
            codigo_norm,
            nombre,
            categoria,
            saldo_ini_ant_cents,
            saldo_nuevo_cents,
            now_iso,
            usuario_clean,
            motivo_clean,
            referencia_autorizacion,
            jornada_id,
            es_apertura_flag,
            corte_iso,
            naturaleza_saldo,
            idempotency_clean,
            op_label,
        ))

        conn.commit()

        return {
            "item_codigo": codigo_norm,
            "item_nombre": nombre,
            "categoria": categoria,
            "saldo_anterior_cents": saldo_ini_ant_cents,
            "saldo_nuevo_cents": saldo_nuevo_cents,
            "saldo_anterior_formateado": formatMoney(saldo_ini_ant_cents),
            "saldo_nuevo_formateado": formatMoney(saldo_nuevo_cents),
            "estado_apertura": "OPENED",
            "tipo_operacion": op_label,
            "usuario_responsable": usuario_clean,
            "motivo": motivo_clean,
            "referencia_autorizacion": referencia_autorizacion,
            "fecha_corte": corte_iso,
            "fecha_hora": now_iso,
        }

    else:
        # Obligación (DAVID, CLARO, PREVENTISTAS)
        cur.execute("""
            SELECT codigo, nombre, saldo_inicial_cents, saldo_actual_cents, estado_apertura
            FROM tesoreria_obligaciones_apertura WHERE codigo = ?;
        """, (codigo_norm,))
        row = cur.fetchone()
        if not row:
            raise ErrorAperturaTesoreria(f"Obligación no encontrada: {codigo_norm}")

        nombre = row[1]
        saldo_ini_ant_cents = int(row[2])
        saldo_act_cents = int(row[3])
        tipo_op = str(tipo_operacion or "APERTURA_INICIAL").upper()

        if tipo_op == "AJUSTE_COMPENSATORIO":
            delta = saldo_nuevo_cents - saldo_ini_ant_cents
            nuevo_act = saldo_act_cents + delta
            es_apertura_flag = 0
            op_label = "AJUSTE_COMPENSATORIO"
        else:
            nuevo_act = saldo_nuevo_cents
            es_apertura_flag = 1
            op_label = "APERTURA_INICIAL"

        cur.execute("""
            UPDATE tesoreria_obligaciones_apertura
            SET saldo_inicial_cents = ?,
                saldo_actual_cents = ?,
                estado_apertura = 'OPENED',
                fecha_corte = ?,
                observaciones_apertura = ?,
                comprobante_apertura = ?,
                idempotency_key = ?,
                configurado_por = ?,
                configurado_en = ?,
                motivo = ?,
                referencia_autorizacion = ?,
                updated_at = ?
            WHERE codigo = ?;
        """, (
            saldo_nuevo_cents,
            nuevo_act,
            corte_iso,
            motivo_clean,
            referencia_autorizacion,
            idempotency_clean,
            usuario_clean,
            now_iso,
            motivo_clean,
            referencia_autorizacion,
            now_iso,
            codigo_norm,
        ))

        cur.execute("""
            INSERT INTO tesoreria_saldos_iniciales_auditoria (
                item_codigo, item_nombre, categoria, saldo_anterior_cents, saldo_nuevo_cents,
                fecha_hora, usuario_responsable, motivo, referencia_autorizacion, jornada_id,
                es_apertura_inicial, fecha_corte, naturaleza_saldo, idempotency_key, tipo_operacion, estado_apertura
            ) VALUES (?, ?, 'OBLIGACION', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OBLIGACION_EXIGIBLE', ?, ?, 'OPENED');
        """, (
            codigo_norm,
            nombre,
            saldo_ini_ant_cents,
            saldo_nuevo_cents,
            now_iso,
            usuario_clean,
            motivo_clean,
            referencia_autorizacion,
            jornada_id,
            es_apertura_flag,
            corte_iso,
            idempotency_clean,
            op_label,
        ))

        conn.commit()

        return {
            "item_codigo": codigo_norm,
            "item_nombre": nombre,
            "categoria": "OBLIGACION",
            "saldo_anterior_cents": saldo_ini_ant_cents,
            "saldo_nuevo_cents": saldo_nuevo_cents,
            "saldo_anterior_formateado": formatMoney(saldo_ini_ant_cents),
            "saldo_nuevo_formateado": formatMoney(saldo_nuevo_cents),
            "estado_apertura": "OPENED",
            "tipo_operacion": op_label,
            "usuario_responsable": usuario_clean,
            "motivo": motivo_clean,
            "referencia_autorizacion": referencia_autorizacion,
            "fecha_corte": corte_iso,
            "fecha_hora": now_iso,
        }


def obtener_historial_aperturas(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Consulta la pista inmutable append-only de modificaciones a saldos iniciales y aperturas."""
    _asegurar_columnas_apertura(conn)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT id, item_codigo, item_nombre, categoria, saldo_anterior_cents, saldo_nuevo_cents,
               fecha_hora, usuario_responsable, motivo, referencia_autorizacion, es_apertura_inicial,
               fecha_corte, naturaleza_saldo, idempotency_key, tipo_operacion, estado_apertura
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
