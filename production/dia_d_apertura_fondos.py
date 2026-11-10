#!/usr/bin/env python3
"""Procedimiento de Establecimiento de Saldos Iniciales — PLAN DÍA D (Etapa 8).

Reglas Innegociables Aprobadas:
1. CERO importación de saldos históricos desactualizados de Excel.
2. CERO reconstrucción contable artificial o saldo ficticio.
3. Arqueo físico presencial para fondos en efectivo (1110, 1120, 1130, 1140, 1150).
4. Verificación en pantalla de saldos de billeteras/bancos (1210, 1220, 1230).
5. Documentación de cada saldo con usuario responsable y acta de apertura.
6. Mantenimiento estricto de los 9 fondos canónicos sin crear fondos no autorizados.
7. Segregación del fondo 1150 Claro Efectivo en CUSTODIA (excluido de disponibilidad propia).
8. Emisión de Acta de Apertura y Certificado Digital firmado con hash canónico SHA-256.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
import sys
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_commercial_bridge_worker import open_db_connection
from tesoreria.availability import getTreasurySummary
from tesoreria.catalog import FONDOS_CANONICOS, obtener_catalogo_fondos, obtener_fondo_por_codigo
from tesoreria.commercial_bridge_worker import reconcile_treasury_ledger_balances
from tesoreria.jornadas import abrir_jornada, obtener_jornada_actual
from tesoreria.money import formatMoney
from tesoreria.movements import registerEntry

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [DÍA-D] %(message)s")
logger = logging.getLogger("genaro_pos.dia_d")


class ErrorDiaD(Exception):
    """Excepción para violaciones del protocolo del Día D."""
    pass


def canonical_json_hash(data: Dict[str, Any]) -> str:
    """Calcula hash SHA-256 determinista RFC 8785 de una estructura JSON."""
    raw = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ejecutar_apertura_dia_d(
    tes_conn: sqlite3.Connection,
    saldos_iniciales_cents: Dict[str, int],
    usuario_responsable: str,
    fecha_operativa: str,
    acta_referencia: str = "ACTA_ARQUEO_INICIAL_DIA_D",
    observaciones: Optional[str] = None,
) -> Dict[str, Any]:
    """Ejecuta el protocolo de apertura del Día D registrando asientos iniciales inmutables."""
    logger.info("Iniciando protocolo de apertura de fondos del DÍA D...")

    if not usuario_responsable or not usuario_responsable.strip():
        raise ErrorDiaD("El usuario responsable del arqueo inicial es obligatorio.")

    # 1. Validar fondos canónicos
    codigos_validos = {f["codigo"] for f in FONDOS_CANONICOS}
    for cod in saldos_iniciales_cents.keys():
        if cod not in codigos_validos:
            raise ErrorDiaD(f"Código de fondo desconocido o no canónico: {cod}")

    # 2. Control de no negatividad para fondos físicos
    fondos_fisicos = {"1110", "1120", "1130", "1140", "1150", "1160"}
    for cod, monto in saldos_iniciales_cents.items():
        if cod in fondos_fisicos and monto < 0:
            raise ErrorDiaD(f"Prohibido saldo físico negativo en fondo {cod}: {monto}")

    # 3. Asegurar jornada de apertura
    jornada = obtener_jornada_actual(tes_conn)
    if not jornada:
        jornada = abrir_jornada(tes_conn, usuario=usuario_responsable.strip(), observaciones=f"Apertura Día D {fecha_operativa}")
        logger.info("Jornada inicial #%d abierta para la fecha %s", jornada["numero_jornada"], fecha_operativa)

    now_utc = datetime.now(timezone.utc).isoformat()
    asientos_emitidos = []

    # 4. Registrar asientos iniciales formales en el libro mayor
    for fondo_def in FONDOS_CANONICOS:
        cod = fondo_def["codigo"]
        monto = saldos_iniciales_cents.get(cod, 0)
        fondo_db = obtener_fondo_por_codigo(tes_conn, cod)

        # Si el saldo actual en base ya era mayor a 0, verificar si se sobreescribe o complementa
        if fondo_db["saldo_centavos"] > 0:
            logger.warning(
                "Fondo %s ya cuenta con saldo previo de %s. Se respetará el estado contable.",
                cod, formatMoney(fondo_db["saldo_centavos"])
            )
            continue

        if monto > 0:
            idem_key = f"DIA_D_INIT_{cod}_{jornada['numero_jornada']}"
            res = registerEntry(
                conn=tes_conn,
                fund_code=cod,
                amount_cents=monto,
                counterpart="APORTE_INICIAL_DIA_D",
                reason=f"Arqueo de apertura inicial Día D: {fondo_def['nombre']}",
                created_by=usuario_responsable.strip(),
                terminal_id="TERM-ADM-01",
                idempotency_key=idem_key,
                evidence_reference=acta_referencia,
                occurred_at=now_utc,
            )
            asientos_emitidos.append({
                "codigo": cod,
                "nombre": fondo_def["nombre"],
                "monto_cents": monto,
                "monto_formateado": formatMoney(monto),
                "movement_id": res["movement_id"],
                "titularidad": fondo_def["titularidad"],
            })
            logger.info("Fondo %s (%s) cargado con saldo inicial: %s", cod, fondo_def["nombre"], formatMoney(monto))
        else:
            asientos_emitidos.append({
                "codigo": cod,
                "nombre": fondo_def["nombre"],
                "monto_cents": 0,
                "monto_formateado": "$0,00",
                "movement_id": None,
                "titularidad": fondo_def["titularidad"],
            })

    # 5. Conciliación matemática estricta inmediata
    rec = reconcile_treasury_ledger_balances(tes_conn)
    if not rec["cuadre_exacto"]:
        raise ErrorDiaD(f"Fallo de cuadre contable tras apertura Día D: {rec['discrepancias']}")

    # 6. Generar Certificado Digital del Día D
    summary = getTreasurySummary(tes_conn)

    acta_dict = {
        "tipo_documento": "ACTA_CONSTITUTIVA_SALDOS_INICIALES_DIA_D",
        "fecha_operativa": fecha_operativa,
        "timestamp_utc": now_utc,
        "usuario_responsable": usuario_responsable.strip(),
        "acta_referencia": acta_referencia,
        "observaciones": observaciones or "Apertura formal de tesorería sin saldos ficticios",
        "asientos_iniciales": asientos_emitidos,
        "resumen_fondos_propios_disponibles_cents": summary["fondos_propios"]["total_disponible_cents"],
        "resumen_fondos_custodia_cents": sum(
            f["saldo_registrado_cents"] for f in summary["fondos"] if f["ownership"] == "CUSTODIA"
        ),
        "total_consolidado_cents": summary["total_consolidado"]["total_registrado_cents"],
    }

    acta_hash = canonical_json_hash(acta_dict)
    acta_dict["certificado_sha256"] = acta_hash

    logger.info("Protocolo del DÍA D completado exitosamente. Hash de Acta: %s", acta_hash)
    return acta_dict


def main() -> int:
    parser = argparse.ArgumentParser(description="Protocolo de Apertura del Día D — GenaroPOS Tesorería")
    parser.add_argument("--tes-db", type=str, default="/home/alanrod10/production/data/tesoreria.db", help="Ruta DB tesorería")
    parser.add_argument("--user-id", type=str, default="alan_socio", help="Usuario responsable de la apertura")
    parser.add_argument("--fecha", type=str, default=datetime.now(timezone.utc).strftime("%Y-%m-%d"), help="Fecha operativa YYYY-MM-DD")
    parser.add_argument("--saldos-json", type=str, help="Ruta a JSON o string JSON con saldos iniciales en centavos")
    parser.add_argument("--acta", type=str, default="ACTA_DIA_D_ARQUEO_PRESENCIAL", help="Referencia de comprobante o acta")

    args = parser.parse_args()
    tes_path = Path(args.tes_db).resolve()

    if not tes_path.exists():
        logger.error("Base de tesorería no encontrada en %s", tes_path)
        return 1

    saldos_cents = {}
    if args.saldos_json:
        if Path(args.saldos_json).exists():
            with open(args.saldos_json, "r", encoding="utf-8") as f:
                saldos_cents = json.load(f)
        else:
            saldos_cents = json.loads(args.saldos_json)

    conn = open_db_connection(tes_path)
    try:
        acta = ejecutar_apertura_dia_d(
            tes_conn=conn,
            saldos_iniciales_cents=saldos_cents,
            usuario_responsable=args.user_id,
            fecha_operativa=args.fecha,
            acta_referencia=args.acta,
        )
        print(json.dumps(acta, indent=2, ensure_ascii=False))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
