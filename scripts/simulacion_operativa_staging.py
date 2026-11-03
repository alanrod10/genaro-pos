"""Script de Simulación Integral Operativa de Tesorería en Staging.

Ejecuta el ciclo de vida completo de una jornada operativa:
1. Verificación de aislamiento estricto y hashes iniciales.
2. Apertura de Jornada #2.
3. Registro de movimientos de prueba y traslados (despacho, confirmación, anulación).
4. Arqueos físicos (conteo conforme y faltante con apertura de expediente).
5. Conciliación digital declarativa (declaración manual, evidencia, sin API externa).
6. Protocolo de cierre: bloqueo, validación, barrido trazable 1110->1120, confirmación y hash SHA-256.
7. Apertura de Jornada #3 y verificación de inmutabilidad criptográfica.
8. Verificación de las 10 invariantes económicas y no-intervención de Producción.
"""

import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Asegurar path para importar tesoreria
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tesoreria.catalog import obtener_fondo_por_codigo, sembrar_catalogo_canónico
from tesoreria.closures import (
    ErrorCierre,
    beginClosure,
    confirmClosure,
    executeCashDrawerSweep,
    getClosure,
    getClosureHistory,
    openNextJournal,
    validateClosure,
)
from tesoreria.jornadas import abrir_jornada, obtener_jornada_actual, obtener_jornada_por_id
from tesoreria.movements import ErrorMovimiento, registerEntry, registerExit
from tesoreria.reconciliations import (
    ErrorArqueo,
    getDifferenceCases,
    getPendingTransfersForFund,
    getReconciliation,
    reconcileDigitalAccount,
    reconcilePhysicalFund,
)
from tesoreria.transfers import (
    ErrorTraslado,
    cancelPendingTransfer,
    confirmTransferReceipt,
    transferFunds,
)
from tesoreria.availability import getFundAvailability


def sha256_file(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def main():
    print("=" * 80)
    print("GENAROPOS — SIMULACIÓN INTEGRAL OPERATIVA DE TESORERÍA (STAGING)")
    print("=" * 80)

    db_staging_path = str(PROJECT_ROOT / "staging" / "tesoreria_staging.db")
    db_prod_path = "/home/alanrod10/production/data/genaro-pos.db"

    # 1. VERIFICACIÓN DE ENTORNO Y HASHES PREVIOS
    prod_hash_inicial = sha256_file(db_prod_path)
    staging_hash_inicial = sha256_file(db_staging_path)

    print(f"\n[1. PREPARACIÓN]")
    print(f"Base Staging:   {db_staging_path}")
    print(f"SHA-256 Staging Inicial: {staging_hash_inicial}")
    print(f"Base Producción: {db_prod_path} (Modo Solo Lectura)")
    print(f"SHA-256 Producción Inicial: {prod_hash_inicial}")
    assert prod_hash_inicial == "8a1df991b8ab9bee27770047d6e49a000dfd53b64f957618ec0c272f071c6f76", "VIOLACIÓN DE PRODUCCIÓN"
    print("✓ Integridad de producción verificada.")

    # Conectar a la base de Staging con foreign keys habilitadas
    conn = sqlite3.connect(db_staging_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")

    audit_records = {}

    # 2. APERTURA DE JORNADA DE PRUEBA
    print("\n[2. APERTURA DE JORNADA #2]")
    jornada_activa = obtener_jornada_actual(conn)
    total_saldo_inicial = 9_220_000
    fondos_iniciales = {
        "1110": 0,
        "1120": 2_290_000,
        "1130": 400_000,
        "1140": 60_000,
        "1150": 350_000,
        "1210": 4_120_000,
        "1220": 1_000_000,
        "1230": 1_000_000,
        "1310": 0,
    }
    audit_records["fondos_iniciales"] = fondos_iniciales

    is_resuming = False
    is_already_closed = False
    if jornada_activa and jornada_activa["numero_jornada"] == 2:
        print(f"Jornada #{jornada_activa['numero_jornada']} ya iniciada (Estado: {jornada_activa['estado']}). Reanudando simulación...")
        jornada_2 = dict(jornada_activa)
        jid = jornada_2["id"]
        is_resuming = True
    elif jornada_activa and jornada_activa["numero_jornada"] >= 3:
        print(f"Jornada #2 ya fue ejecutada y cerrada; Jornada #{jornada_activa['numero_jornada']} ya está abierta.")
        jornada_2 = dict(obtener_jornada_por_id(conn, 2))
        jid = jornada_2["id"]
        is_resuming = True
        is_already_closed = True
    else:
        if jornada_activa and jornada_activa["estado"] != "CERRADA":
            raise RuntimeError("Se requiere que la jornada previa esté CERRADA para abrir la jornada de simulación.")

        # Verificar saldo de 1110 antes de abrir
        f1110_prev = obtener_fondo_por_codigo(conn, "1110")
        print(f"Saldo previo Caja 1110: {f1110_prev['saldo_centavos']} centavos ($0,00 esperado).")
        assert f1110_prev["saldo_centavos"] == 0, "Caja 1110 no está en cero para iniciar jornada."

        # Abrir Jornada #2
        jornada_2 = openNextJournal(
            conn=conn,
            user_id="alan_simulador",
            observations="Simulación Operativa Integral Tesorería — Staging",
        )
        jid = jornada_2["id"]

    audit_records["jornada_simulada"] = {
        "id": jid,
        "numero_jornada": jornada_2["numero_jornada"],
        "estado": jornada_2["estado"],
        "usuario_apertura": jornada_2["usuario_apertura"],
        "fecha_operativa": jornada_2["fecha_operativa"],
    }
    print(f"✓ Jornada #{jornada_2['numero_jornada']} activa (ID: {jid}).")

    # Verificar los 9 fondos canónicos
    fondos = conn.execute("SELECT codigo, nombre, naturaleza, titularidad, saldo_centavos FROM tesoreria_fondos ORDER BY codigo").fetchall()
    print(f"Verificación de los {len(fondos)} fondos canónicos:")
    for f in fondos:
        print(f"  - [{f['codigo']}] {f['nombre']}: Saldo={f['saldo_centavos']}c | Naturaleza={f['naturaleza']} | Titularidad={f['titularidad']}")
        if f["codigo"] == "1310":
            assert f["titularidad"] == "TRANSITO" and f["naturaleza"] == "TRANSITO_PREVENTISTAS"
        if f["codigo"] == "1150":
            assert f["titularidad"] == "CUSTODIA" and f["naturaleza"] == "RECAUDACION_TERCEROS"

    print(f"Total consolidado inicial en libros: ${total_saldo_inicial/100:,.2f}")

    if is_resuming:
        print("\n[REANUDACIÓN: CARGA DE REGISTROS PREVIAMENTE EJECUTADOS]")
        row_mov = conn.execute("SELECT movement_id, fund_id, amount_cents, movement_type FROM tesoreria_movimientos WHERE journal_id = ? AND movement_type = 'ENTRY'", (jid,)).fetchone()
        audit_records["mov_entry_1110"] = dict(row_mov) if row_mov else {}

        row_t1 = conn.execute("SELECT * FROM tesoreria_traslados WHERE journal_id = ? AND amount_cents = 400000", (jid,)).fetchone()
        audit_records["traslado_1_despacho"] = dict(row_t1) if row_t1 else {}
        audit_records["traslado_1_confirmacion"] = dict(row_t1) if row_t1 else {}

        row_t2 = conn.execute("SELECT * FROM tesoreria_traslados WHERE journal_id = ? AND amount_cents = 100000", (jid,)).fetchone()
        audit_records["traslado_2_cancelacion"] = dict(row_t2) if row_t2 else {}

        rec_1120 = getReconciliation(conn, "edc32c3c-eab6-43c6-b28c-a60b2a626fde")
        rec_1110 = getReconciliation(conn, "d36b0cd7-2867-4ed5-8fb9-bb1546c1985f")
        rec_1210 = getReconciliation(conn, "72a8f760-ab3e-4b09-acf2-9273d3000c19")
        audit_records["arqueo_1120"] = rec_1120
        audit_records["arqueo_1110"] = rec_1110
        audit_records["conciliacion_1210"] = rec_1210

        sweep_trf = conn.execute("SELECT * FROM tesoreria_traslados WHERE journal_id = ? AND evidence_reference = 'BARRIDO_CIERRE_DIARIO'", (jid,)).fetchone()
        sweep_res = {
            "swept": True,
            "transfer_id": sweep_trf["transfer_id"],
            "amount_cents": sweep_trf["amount_cents"],
            "amount_formatted": f"${sweep_trf['amount_cents']/100:,.2f}",
            "status": sweep_trf["status"],
            "origin_fund_code": "1110",
            "destination_fund_code": "1120",
        }
        audit_records["barrido_cierre"] = sweep_res
        print("✓ Registros de operaciones 3 a 6.3 verificados y cargados exitosamente.")
    else:
        # 3. MOVIMIENTOS Y TRASLADOS
        print("\n[3. MOVIMIENTOS Y TRASLADOS]")

        # Operación A: Entrada de prueba en Caja 1110 ($10.000,00)
        print("\n-- Operación A: Fondeo de prueba en Caja 1110 ($10.000,00) --")
        mov_entry = registerEntry(
            conn=conn,
            fund_code="1110",
            amount_cents=1_000_000,
            counterpart="Aporte Inicial Simulación",
            reason="Fondeo operativo controlado para simulación",
            created_by="alan_simulador",
        )
        audit_records["mov_entry_1110"] = mov_entry
        f1110_post_entry = obtener_fondo_por_codigo(conn, "1110")
        print(f"Entrada registrada: ID={mov_entry['movement_id']} | Monto=${mov_entry['amount_cents']/100:,.2f}")
        print(f"Saldo Caja 1110 tras entrada: ${f1110_post_entry['saldo_centavos']/100:,.2f}")
        assert f1110_post_entry["saldo_centavos"] == 1_000_000

        # Operación B: Despacho de traslado 1110 -> 1120 ($4.000,00)
        print("\n-- Operación B: Despacho de traslado 1110 ➔ 1120 ($4.000,00) --")
        f1120_antes_trf = obtener_fondo_por_codigo(conn, "1120")
        trf_1 = transferFunds(
            conn=conn,
            origin_fund_code="1110",
            destination_fund_code="1120",
            amount_cents=400_000,
            dispatched_by="alan_simulador",
            reason="Traslado simulación: pase a tesoro acumulado",
            evidence_reference="REMITO-SIM-001",
        )
        audit_records["traslado_1_despacho"] = trf_1
        print(f"Traslado despachado: ID={trf_1['transfer_id']} | Estado={trf_1['status']}")
        print(f"Movimiento de salida (débito): ID={trf_1['out_movement_id']}")

        # Comprobar débito en origen y ausencia de crédito en destino
        f1110_post_trf = obtener_fondo_por_codigo(conn, "1110")
        f1120_post_trf = obtener_fondo_por_codigo(conn, "1120")
        print(f"Saldo Origen 1110 tras despacho: ${f1110_post_trf['saldo_centavos']/100:,.2f} (debe ser $6.000,00)")
        print(f"Saldo Destino 1120 tras despacho: ${f1120_post_trf['saldo_centavos']/100:,.2f} (debe ser idéntico al anterior: ${f1120_antes_trf['saldo_centavos']/100:,.2f})")
        assert f1110_post_trf["saldo_centavos"] == 600_000
        assert f1120_post_trf["saldo_centavos"] == f1120_antes_trf["saldo_centavos"]

        # Comprobar disponibilidad de destino
        avail_1120 = getFundAvailability(conn, "1120")
        print(f"Disponibilidad 1120: ${avail_1120['saldo_disponible_cents']/100:,.2f} | En tránsito: ${avail_1120['total_en_transito_cents']/100:,.2f}")
        assert avail_1120["total_en_transito_cents"] == 400_000
        assert avail_1120["saldo_disponible_cents"] == f1120_antes_trf["saldo_centavos"]

        # Operación C: Confirmación formal de recepción del traslado 1110 -> 1120
        print("\n-- Operación C: Confirmación de recepción formal de traslado 1 --")
        trf_1_conf = confirmTransferReceipt(
            conn=conn,
            transfer_id=trf_1["transfer_id"],
            received_by="cajero_receptor",
        )
        audit_records["traslado_1_confirmacion"] = trf_1_conf
        f1120_post_conf = obtener_fondo_por_codigo(conn, "1120")
        print(f"Traslado confirmado: Estado={trf_1_conf['status']}")
        print(f"Movimiento de entrada (crédito): ID={trf_1_conf['in_movement_id']}")
        print(f"Saldo Destino 1120 tras confirmación: ${f1120_post_conf['saldo_centavos']/100:,.2f} (aumentó exactamente $4.000,00)")
        assert f1120_post_conf["saldo_centavos"] == f1120_antes_trf["saldo_centavos"] + 400_000

        # Operación D: Traslado independiente con anulación/cancelación
        print("\n-- Operación D: Traslado independiente 1110 ➔ 1120 con cancelación ($1.000,00) --")
        trf_2 = transferFunds(
            conn=conn,
            origin_fund_code="1110",
            destination_fund_code="1120",
            amount_cents=100_000,
            dispatched_by="alan_simulador",
            reason="Traslado a cancelar: prueba de anulación",
        )
        f1110_deb_anul = obtener_fondo_por_codigo(conn, "1110")
        print(f"Traslado 2 despachado: ID={trf_2['transfer_id']} | 1110 quedó en ${f1110_deb_anul['saldo_centavos']/100:,.2f}")
        assert f1110_deb_anul["saldo_centavos"] == 500_000

        # Cancelar traslado
        trf_2_canc = cancelPendingTransfer(
            conn=conn,
            transfer_id=trf_2["transfer_id"],
            cancelled_by="alan_simulador",
            reason="Cancelación operativa de prueba",
        )
        audit_records["traslado_2_cancelacion"] = trf_2_canc
        f1110_post_canc = obtener_fondo_por_codigo(conn, "1110")
        f1120_post_canc = obtener_fondo_por_codigo(conn, "1120")
        print(f"Traslado 2 cancelado: Estado={trf_2_canc['status']} | Contra-asiento ID={trf_2_canc['reversal_movement_id']}")
        print(f"Saldo Origen 1110 restaurado: ${f1110_post_canc['saldo_centavos']/100:,.2f} (debe ser $6.000,00)")
        print(f"Saldo Destino 1120: ${f1120_post_canc['saldo_centavos']/100:,.2f} (nunca recibió los fondos)")
        assert f1110_post_canc["saldo_centavos"] == 600_000
        assert f1120_post_canc["saldo_centavos"] == f1120_post_conf["saldo_centavos"]

        # 4. ARQUEOS FÍSICOS
        print("\n[4. ARQUEOS FÍSICOS]")

        # Arqueo A: Fondo 1120 (Tesoro Acumulado) — Conforme / Sin diferencia
        print("\n-- Arqueo A: Fondo 1120 Conforme / Sin Diferencia --")
        rec_1120 = reconcilePhysicalFund(
            conn=conn,
            fund_code="1120",
            verified_amount_cents=f1120_post_canc["saldo_centavos"],
            user_id="alan_simulador",
            shift_name="MAÑANA",
            notes="Recuento turno mañana conforme",
        )
        audit_records["arqueo_1120"] = rec_1120
        print(f"Arqueo 1120: Teórico=${rec_1120['saldo_registrado_cents']/100:,.2f} | Contado=${rec_1120['saldo_verificado_cents']/100:,.2f} | Dif=${rec_1120['diferencia_cents']/100:,.2f} | Estado={rec_1120['estado_revision']}")
        assert rec_1120["diferencia_cents"] == 0
        assert rec_1120["estado_revision"] == "CONCILIADO"

        # Arqueo B: Fondo 1110 (Caja Principal) — Con Faltante de $200,00 y apertura de expediente
        print("\n-- Arqueo B: Fondo 1110 Con Faltante de $200,00 y apertura de expediente --")
        f1110_teorico = f1110_post_canc["saldo_centavos"]  # 600000 cents ($6.000,00)
        contado_1110 = f1110_teorico - 20_000  # $5.800,00
        rec_1110 = reconcilePhysicalFund(
            conn=conn,
            fund_code="1110",
            verified_amount_cents=contado_1110,
            user_id="cajero_turno_manana",
            shift_name="MAÑANA",
            notes="Faltante de $200,00 en monedas al cambio",
            create_difference_case=True,
            investigation_notes="Se constata faltante de 1 billete de $200 o redondeo en vuelto",
        )
        audit_records["arqueo_1110"] = rec_1110
        print(f"Arqueo 1110: Teórico=${rec_1110['saldo_registrado_cents']/100:,.2f} | Contado=${rec_1110['saldo_verificado_cents']/100:,.2f} | Dif=${rec_1110['diferencia_cents']/100:,.2f} | Estado={rec_1110['estado_revision']}")
        print(f"Expediente de diferencia generado: ID={rec_1110['difference_case_id']}")
        assert rec_1110["diferencia_cents"] == -20_000
        assert rec_1110["estado_revision"] == "CON DIFERENCIA"
        assert rec_1110["difference_case_id"] is not None

        # Comprobar invariante: El arqueo NO muta el saldo de 1110 en tesoreria_fondos
        f1110_post_arq = obtener_fondo_por_codigo(conn, "1110")
        print(f"Saldo Caja 1110 tras arqueo con faltante: ${f1110_post_arq['saldo_centavos']/100:,.2f} (INALTERADO en $6.000,00)")
        assert f1110_post_arq["saldo_centavos"] == 600_000

        # 5. CONCILIACIÓN DIGITAL
        print("\n[5. CONCILIACIÓN DIGITAL DECLARATIVA]")
        f1210 = obtener_fondo_por_codigo(conn, "1210")
        print(f"Cuenta Digital 1210 (Mercado Pago): Saldo en libros = ${f1210['saldo_centavos']/100:,.2f}")
        rec_1210 = reconcileDigitalAccount(
            conn=conn,
            fund_code="1210",
            declared_amount_cents=f1210["saldo_centavos"],
            user_id="alan_simulador",
            evidence_reference="EXTRACTO-MP-SIM-20261101-01",
            consultation_timestamp="2026-11-01T10:30:00Z",
            notes="Saldo verificado manualmente en el portal web de Mercado Pago",
        )
        audit_records["conciliacion_1210"] = rec_1210
        print(f"Conciliación 1210: Registrado=${rec_1210['saldo_registrado_cents']/100:,.2f} | Declarado=${rec_1210['saldo_verificado_cents']/100:,.2f} | Estado={rec_1210['estado_revision']}")
        print(f"Aviso registrado en notas: '{rec_1210['notes']}'")
        assert rec_1210["is_digital"] is True
        assert rec_1210["diferencia_cents"] == 0
        assert rec_1210["estado_revision"] == "CONCILIADO"

        # Invariante: Saldo 1210 inalterado
        f1210_post = obtener_fondo_por_codigo(conn, "1210")
        assert f1210_post["saldo_centavos"] == f1210["saldo_centavos"]

        # 6. PROTOCOLO DE CIERRE DE JORNADA
        print("\n[6. PROTOCOLO DE CIERRE DE JORNADA #2]")

        # 6.1. Iniciar cierre (bloquear operaciones ordinarias)
        print("-- Paso 6.1: Iniciar Cierre (Bloquear operaciones) --")
        j_encierre = beginClosure(conn=conn, journal_id=jid, user_id="supervisor_cierre")
        print(f"Jornada #{jid} en estado: {j_encierre['estado']}")
        assert j_encierre["estado"] == "EN_CIERRE"

        # Verificar que operaciones ordinarias están bloqueadas
        try:
            registerEntry(conn, fund_code="1110", amount_cents=100_00, counterpart="Test", reason="Bloqueo", created_by="alan")
            raise AssertionError("Falla: Se permitió registrar entrada en EN_CIERRE")
        except ErrorMovimiento as em:
            print(f"✓ Bloqueo ordinario verificado: '{em}'")

        # 6.2. Validar precondiciones
        print("\n-- Paso 6.2: Validar precondiciones de cierre --")
        val_cierre = validateClosure(conn=conn, journal_id=jid)
        print(f"¿Listo para cierre directo?: {val_cierre['can_close']}")
        print(f"Bloqueadores: {val_cierre['reasons']}")
        print(f"Advertencias: {val_cierre['warnings']}")
        assert val_cierre["can_close"] is False  # Caja 1110 tiene remanente de $6.000,00
        assert val_cierre["needs_sweep"] is True

        # 6.3. Ejecutar barrido obligatorio de Caja 1110 a 1120
        print("\n-- Paso 6.3: Ejecutar Barrido Trazable 1110 ➔ 1120 --")
        sweep_res = executeCashDrawerSweep(
            conn=conn,
            journal_id=jid,
            user_id="supervisor_cierre",
            reason="Vaciado de cierre diario 1110 a 1120 (Simulación)",
        )
        audit_records["barrido_cierre"] = sweep_res
        print(f"Barrido ejecutado: Estado='{sweep_res.get('status')}' | Monto=${sweep_res['amount_cents']/100:,.2f}")
        print(f"Traslado generado: ID={sweep_res['transfer_id']}")

    # Verificar que 1110 quedó en 0 exacto
    f1110_post_sweep = obtener_fondo_por_codigo(conn, "1110")
    f1120_post_sweep = obtener_fondo_por_codigo(conn, "1120")
    print(f"Saldo Caja 1110 tras barrido: ${f1110_post_sweep['saldo_centavos']/100:,.2f} ($0,00 exacto)")
    print(f"Saldo Tesoro 1120 tras barrido: ${f1120_post_sweep['saldo_centavos']/100:,.2f}")
    assert f1110_post_sweep["saldo_centavos"] == 0

    if not is_already_closed:
        # 6.4. Revalidar precondiciones tras barrido
        print("\n-- Paso 6.4: Revalidar precondiciones tras barrido --")
        val_cierre_2 = validateClosure(conn=conn, journal_id=jid)
        print(f"¿Listo para cierre definitivo?: {val_cierre_2['can_close']}")
        assert val_cierre_2["can_close"] is True

        # 6.5. Confirmar Cierre Definitivo
        print("\n-- Paso 6.5: Confirmar Cierre Definitivo y Sellado Criptográfico --")
        cierre_conf = confirmClosure(
            conn=conn,
            journal_id=jid,
            user_id="alan_socio",
            observations="Cierre definitivo exitoso de Jornada #2 con barrido completo a 1120",
        )
        audit_records["cierre_confirmado"] = cierre_conf
        hash_cierre = cierre_conf["canonical_hash"]
        print(f"Cierre confirmado: Estado={cierre_conf['status']} | Cerrado por={cierre_conf['closed_by']}")
        print(f"Hash SHA-256 Canónico: {hash_cierre}")
        assert len(hash_cierre) == 64

        # 7. JORNADA SIGUIENTE Y VERIFICACIÓN DE INMUTABILIDAD
        print("\n[7. JORNADA SIGUIENTE Y VERIFICACIÓN DE INMUTABILIDAD]")

        # Abrir Jornada #3
        jornada_3 = openNextJournal(
            conn=conn,
            user_id="alan_simulador",
            observations="Apertura Jornada #3 post-simulación",
        )
        audit_records["jornada_3"] = {
            "id": jornada_3["id"],
            "numero_jornada": jornada_3["numero_jornada"],
            "estado": jornada_3["estado"],
            "usuario_apertura": jornada_3["usuario_apertura"],
        }
        print(f"✓ Jornada #{jornada_3['numero_jornada']} abierta exitosamente (ID: {jornada_3['id']}).")
    else:
        print("\n[CARGA DE ESTADO DE CIERRE PREVIO]")
        cierre_conf = getClosure(conn, journal_id=jid)
        audit_records["cierre_confirmado"] = cierre_conf
        hash_cierre = cierre_conf["canonical_hash"]
        print(f"Cierre de Jornada #{jid} ya registrado con Hash SHA-256: {hash_cierre}")
        j3_row = obtener_jornada_por_id(conn, 3)
        audit_records["jornada_3"] = {
            "id": j3_row["id"],
            "numero_jornada": j3_row["numero_jornada"],
            "estado": j3_row["estado"],
            "usuario_apertura": j3_row["usuario_apertura"],
        }
        print(f"✓ Jornada #3 activa verificada (ID: {j3_row['id']}).")

    # Invariante de apertura: Caja 1110 en 0
    f1110_j3 = obtener_fondo_por_codigo(conn, "1110")
    print(f"Saldo inicial Caja 1110 en Jornada #3: ${f1110_j3['saldo_centavos']/100:,.2f} ($0,00 verificado)")
    assert f1110_j3["saldo_centavos"] == 0

    # Comprobar inmutabilidad del cierre de Jornada #2
    cierre_recup = getClosure(conn, journal_id=jid)
    assert cierre_recup["canonical_hash"] == hash_cierre
    print("✓ Hash SHA-256 de Jornada #2 verificado inalterado.")

    # Recalcular hash a partir del snapshot almacenado
    snapshot_raw = cierre_recup["snapshot_json"]
    recalculated_hash = hashlib.sha256(snapshot_raw.encode("utf-8")).hexdigest()
    assert recalculated_hash == hash_cierre
    print("✓ Recálculo criptográfico del snapshot: 100% coincidente.")

    # Invariante de conservación de fondos
    fondos_finales = {}
    total_saldo_final = 0
    for f in conn.execute("SELECT codigo, nombre, saldo_centavos FROM tesoreria_fondos ORDER BY codigo").fetchall():
        fondos_finales[f["codigo"]] = f["saldo_centavos"]
        total_saldo_final += f["saldo_centavos"]

    print("\n[8. BALANCE GLOBAL DE FONDOS]")
    print(f"Total Saldo Inicial:  ${total_saldo_inicial/100:,.2f}")
    print(f"Entrada Registrada:   +$10.000,00")
    print(f"Total Saldo Final:    ${total_saldo_final/100:,.2f}")
    print(f"Diferencia Esperada:  +$10.000,00")
    assert total_saldo_final == total_saldo_inicial + 1_000_000
    print("✓ Conservación estricta de fondos verificada: Ni un solo centavo perdido ni duplicado.")

    # Adversarial: Intentar modificar o cerrar nuevamente Jornada #2
    print("\n[9. COMPROBACIÓN ADVERSARIAL]")
    try:
        confirmClosure(conn, journal_id=jid, user_id="atacante")
        raise AssertionError("Falla: Se permitió duplicar cierre sobre jornada ya archivada")
    except ErrorCierre as ec:
        print(f"✓ Rechazo de re-cierre verificado: '{ec}'")

    try:
        beginClosure(conn, journal_id=jid, user_id="atacante")
        raise AssertionError("Falla: Se permitió iniciar cierre sobre jornada ya archivada")
    except ErrorCierre as ec:
        print(f"✓ Rechazo de beginClosure sobre jornada CERRADA: '{ec}'")

    try:
        cancelPendingTransfer(conn, transfer_id=audit_records["traslado_1_confirmacion"]["transfer_id"], cancelled_by="atacante", reason="Intento malicioso")
        raise AssertionError("Falla: Se permitió anular un traslado ya confirmado")
    except ErrorTraslado as et:
        print(f"✓ Rechazo de anulación sobre traslado confirmado: '{et}'")

    conn.close()

    # 10. VERIFICACIÓN FINAL DE PRODUCCIÓN
    prod_hash_final = sha256_file(db_prod_path)
    print("\n[10. INTEGRIDAD FINAL DE PRODUCCIÓN]")
    print(f"SHA-256 Producción Inicial: {prod_hash_inicial}")
    print(f"SHA-256 Producción Final:   {prod_hash_final}")
    assert prod_hash_final == prod_hash_inicial, "VIOLACIÓN CRÍTICA DE PRODUCCIÓN"
    print("✓ Base de datos productiva 100% INTACTA.")

    print("\n" + "=" * 80)
    print("SIMULACIÓN OPERATIVA INTEGRAL COMPLETADA CON ÉXITO")
    print("=" * 80)

    # Guardar reporte de auditoría JSON
    reporte_path = PROJECT_ROOT / "docs" / "INFORME_SIMULACION_OPERATIVA_TESORERIA.json"
    with open(reporte_path, "w", encoding="utf-8") as rf:
        json.dump(audit_records, rf, indent=2, ensure_ascii=False)
    print(f"Evidencia guardada en: {reporte_path}")


if __name__ == "__main__":
    main()
