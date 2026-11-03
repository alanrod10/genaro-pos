#!/usr/bin/env python3
"""Orquestador de Operaciones y Validación del Entorno de STAGING — Etapa 7.

Gestiona:
1. Inicialización y aislamiento estricto de las bases de Staging.
2. Aplicación transaccional de migraciones 1..12 y siembra del catálogo de 9 fondos.
3. Verificación de seguridad y desconexión de servicios remotos / producción.
4. Ejecución del piloto comercial con turnos sintéticos y escenarios adversariales.
5. Verificación de servicio continuo, exclusión mutua PID y diagnóstico de salud.
6. Conciliación matemática integral del libro mayor.
"""

import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any, Dict

# Añadir raíz del proyecto al path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from db.commercial_outbox_emitter import enqueue_commercial_event
from db.migrations_runner import ejecutar_migraciones, obtener_versiones_aplicadas
from scripts.run_commercial_bridge_worker import (
    PidLockManager,
    ServiceLockError,
    open_db_connection,
    run_health_check,
)
from tesoreria.availability import getTreasurySummary
from tesoreria.catalog import (
    FONDOS_CANONICOS,
    obtener_catalogo_fondos,
    obtener_fondo_por_codigo,
    sembrar_catalogo_canónico,
)
from tesoreria.closures import (
    beginClosure,
    confirmClosure,
    executeCashDrawerSweep,
    validateClosure,
)
from tesoreria.commercial_bridge_worker import (
    get_commercial_bridge_status,
    get_simplified_sync_status,
    process_pending_commercial_events,
    reconcile_treasury_ledger_balances,
    recover_orphaned_processing_events,
)
from tesoreria.jornadas import abrir_jornada, obtener_jornada_actual
from tesoreria.schema import inicializar_esquema_laboratorio
from tesoreria.transfers import confirmTransferReceipt, transferFunds

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [STAGING] %(message)s")
logger = logging.getLogger("genaro_pos.staging")

# Directorio persistente de Staging (contiene las bases operativas reales de Staging).
PERSISTENT_STAGING_DIR = PROJECT_ROOT / "staging"
# Puede redirigirse a un directorio temporal (pruebas) mediante GENARO_STAGING_DATA_DIR.
STAGING_DIR = Path(os.getenv("GENARO_STAGING_DATA_DIR") or PERSISTENT_STAGING_DIR)
STAGING_COMM_DB = STAGING_DIR / "genaro_pos_staging.db"
STAGING_TES_DB = STAGING_DIR / "tesoreria_staging.db"
STAGING_PID_FILE = STAGING_DIR / "staging_bridge.pid"


def verify_staging_isolation() -> Dict[str, Any]:
    """Valida el aislamiento absoluto de Staging respecto a producción y servicios remotos."""
    logger.info("Verificando aislamiento de red y bases de datos en Staging...")

    # 1. Comprobar que no haya URLs de Turso activas
    turso_dev = os.getenv("TURSO_DEV_DATABASE_URL", "")
    turso_prod = os.getenv("TURSO_PROD_DATABASE_URL", "")

    assert not turso_dev.startswith("libsql://"), f"Peligro: URL Turso remota configurada en dev ({turso_dev})"
    assert not turso_prod.startswith("libsql://"), f"Peligro: URL Turso remota configurada en prod ({turso_prod})"

    # 2. Comprobar que los backups comerciales preexistentes no sean las bases activas
    backups_dir = Path("/home/alanrod10/backups")
    for db_file in [STAGING_COMM_DB, STAGING_TES_DB]:
        if db_file.exists():
            resolved = db_file.resolve()
            assert not str(resolved).startswith(str(backups_dir.resolve())), (
                f"Violación de aislamiento: Staging apunta al directorio de backups: {resolved}"
            )

    return {
        "isolation_verified": True,
        "mode": "LOCAL_SQLITE_ISOLATED",
        "remote_turso_connected": False,
        "staging_dir": str(STAGING_DIR),
        "commercial_db": str(STAGING_COMM_DB),
        "tesoreria_db": str(STAGING_TES_DB),
    }


def _proteger_staging_persistente(operacion: str) -> None:
    """Impide operaciones destructivas o simulaciones sobre las bases persistentes de Staging.

    Las bases en PERSISTENT_STAGING_DIR contienen la operatoria real de Staging (jornadas,
    arqueos, expedientes). Reinicializarlas o inyectar una jornada simulada requiere
    autorización explícita mediante GENARO_ALLOW_STAGING_RESET=1.
    """
    if Path(STAGING_DIR).resolve() == PERSISTENT_STAGING_DIR.resolve() and os.getenv("GENARO_ALLOW_STAGING_RESET") != "1":
        raise RuntimeError(
            f"Operación '{operacion}' bloqueada sobre las bases persistentes de Staging ({PERSISTENT_STAGING_DIR}). "
            "Use GENARO_STAGING_DATA_DIR para un directorio aislado o GENARO_ALLOW_STAGING_RESET=1 con autorización."
        )


def init_staging_databases(clean: bool = True) -> Dict[str, Any]:
    """Inicializa las bases de datos de Staging aplicando migraciones y catálogo."""
    logger.info("Inicializando bases de datos de Staging...")
    if clean:
        _proteger_staging_persistente("init_staging_databases(clean=True)")
    STAGING_DIR.mkdir(parents=True, exist_ok=True)

    if clean:
        STAGING_COMM_DB.unlink(missing_ok=True)
        STAGING_TES_DB.unlink(missing_ok=True)
        STAGING_PID_FILE.unlink(missing_ok=True)

    # 1. Base Comercial de Staging
    comm_conn = open_db_connection(STAGING_COMM_DB)
    comm_conn.execute(
        """
        CREATE TABLE IF NOT EXISTS productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL COLLATE NOCASE,
            categoria TEXT NOT NULL DEFAULT 'Otro',
            proveedor TEXT NOT NULL DEFAULT 'General',
            unidad TEXT NOT NULL DEFAULT 'Unidad',
            costo INTEGER NOT NULL DEFAULT 0,
            precio_dia INTEGER NOT NULL DEFAULT 0,
            precio_noche INTEGER NOT NULL DEFAULT 0,
            activo INTEGER NOT NULL DEFAULT 1,
            fecha_actualizacion TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    comm_conn.commit()

    ejecutar_migraciones(comm_conn)
    versiones = obtener_versiones_aplicadas(comm_conn)
    comm_integ = comm_conn.execute("PRAGMA integrity_check;").fetchone()[0]
    comm_conn.close()

    assert len(versiones) >= 12, f"Se esperaban al menos 12 migraciones en staging, se obtuvieron {len(versiones)}"
    assert comm_integ == "ok", f"Integridad corrupta en commercial_db: {comm_integ}"

    # 2. Base de Tesorería de Staging
    tes_conn = open_db_connection(STAGING_TES_DB)
    inicializar_esquema_laboratorio(tes_conn)
    sembrar_catalogo_canónico(tes_conn)
    fondos = obtener_catalogo_fondos(tes_conn)
    tes_integ = tes_conn.execute("PRAGMA integrity_check;").fetchone()[0]

    # Reconciliación inicial
    rec_inicial = reconcile_treasury_ledger_balances(tes_conn)
    tes_conn.close()

    assert len(fondos) == len(FONDOS_CANONICOS), f"Se esperaban {len(FONDOS_CANONICOS)} fondos canónicos, se obtuvieron {len(fondos)}"
    assert tes_integ == "ok", f"Integridad corrupta en tesoreria_db: {tes_integ}"
    assert rec_inicial["cuadre_exacto"] is True, "El libro mayor inicial de staging no cuadra"

    logger.info("Bases de datos de Staging inicializadas y verificadas con éxito.")
    return {
        "status": "INITIALIZED",
        "commercial_migrations": len(versiones),
        "commercial_integrity": comm_integ,
        "tesoreria_funds": len(fondos),
        "tesoreria_integrity": tes_integ,
        "initial_ledger_exact": rec_inicial["cuadre_exacto"],
    }


def execute_staging_pilot_day() -> Dict[str, Any]:
    """Ejecuta una jornada piloto integral en Staging con todas las operaciones y contingencias."""
    logger.info("Iniciando simulación de jornada piloto en Staging...")
    _proteger_staging_persistente("execute_staging_pilot_day")

    comm_conn = open_db_connection(STAGING_COMM_DB)
    tes_conn = open_db_connection(STAGING_TES_DB)

    # 1. Apertura de jornada operativa de tesorería
    fecha_operativa = "2026-11-01"
    jornada = abrir_jornada(tes_conn, fecha_operativa, "alan_socio")
    logger.info("Jornada de Staging abierta: #%d (%s)", jornada["numero_jornada"], fecha_operativa)

    # Saldo inicial mayorista para recargas virtuales en 1220 y 1230 ($10.000 cada una)
    now_utc = datetime.now(timezone.utc).isoformat()
    tes_conn.execute("UPDATE tesoreria_fondos SET saldo_centavos = 1000000 WHERE codigo = '1220';")
    tes_conn.execute("UPDATE tesoreria_fondos SET saldo_centavos = 1000000 WHERE codigo = '1230';")
    f1220 = obtener_fondo_por_codigo(tes_conn, "1220")
    f1230 = obtener_fondo_por_codigo(tes_conn, "1230")
    tes_conn.execute(
        """
        INSERT INTO tesoreria_movimientos (
            movement_id, journal_id, fund_id, movement_type, amount_cents, currency,
            status, counterpart, reason, created_by, terminal_id, occurred_at, recorded_at
        ) VALUES
        ('init_stg_1220', ?, ?, 'ENTRY', 1000000, 'ARS', 'POSTED', 'APORTE_INICIAL', 'Saldo mayorista Prepay Staging', 'alan_socio', 'STG-01', ?, ?),
        ('init_stg_1230', ?, ?, 'ENTRY', 1000000, 'ARS', 'POSTED', 'APORTE_INICIAL', 'Saldo mayorista Claro Staging', 'alan_socio', 'STG-01', ?, ?);
        """,
        (jornada["id"], f1220["id"], now_utc, now_utc, jornada["id"], f1230["id"], now_utc, now_utc),
    )
    tes_conn.commit()

    # ------------------------------------------------------------------------
    # TURNO 1 (MAÑANA): OPERACIONES COMERCIALES SINTÉTICAS
    # ------------------------------------------------------------------------
    # T1.1: Venta Efectivo $5.500
    enqueue_commercial_event(
        comm_conn,
        event_type="SALE_CONFIRMED",
        entity_type="TICKET",
        entity_id="stg_sale_101",
        action_type="CONFIRM",
        payload={
            "ticket_id": "STG-101",
            "monto_efectivo_cents": 550000,
            "monto_transferencia_cents": 0,
            "total_venta_cents": 550000,
            "metodo_pago": "EFECTIVO",
            "usuario": "cajero_stg_1",
            "fecha_hora_utc": now_utc,
        },
        idempotency_key="idemp_stg_sale_101",
    )

    # T1.2: Venta Digital Mercado Pago $14.200
    enqueue_commercial_event(
        comm_conn,
        event_type="SALE_CONFIRMED",
        entity_type="TICKET",
        entity_id="stg_sale_102",
        action_type="CONFIRM",
        payload={
            "ticket_id": "STG-102",
            "monto_efectivo_cents": 0,
            "monto_transferencia_cents": 1420000,
            "total_venta_cents": 1420000,
            "metodo_pago": "MERCADO_PAGO",
            "usuario": "cajero_stg_1",
            "fecha_hora_utc": now_utc,
        },
        idempotency_key="idemp_stg_sale_102",
    )

    # T1.3: Venta Mixta $20.000 ($8.000 efectivo en 1110, $12.000 digital en 1210)
    enqueue_commercial_event(
        comm_conn,
        event_type="SALE_CONFIRMED",
        entity_type="TICKET",
        entity_id="stg_sale_103",
        action_type="CONFIRM",
        payload={
            "ticket_id": "STG-103",
            "monto_efectivo_cents": 800000,
            "monto_transferencia_cents": 1200000,
            "total_venta_cents": 2000000,
            "metodo_pago": "MIXTO",
            "usuario": "cajero_stg_1",
            "fecha_hora_utc": now_utc,
        },
        idempotency_key="idemp_stg_sale_103",
    )

    # T1.4: Servicio Prepay recarga $4.000 cobrada en efectivo (cajón 1130)
    enqueue_commercial_event(
        comm_conn,
        event_type="SERVICE_CONFIRMED",
        entity_type="SERVICIO",
        entity_id="stg_srv_201",
        action_type="CONFIRM",
        payload={
            "servicio_id": "stg_srv_201",
            "servicio": "PREPAY",
            "monto_carga_cents": 400000,
            "pago_efectivo_cents": 400000,
            "pago_transferencia_cents": 0,
            "total_cobrado_cents": 400000,
            "actor": "cajero_stg_1",
            "fecha_hora_utc": now_utc,
        },
        idempotency_key="idemp_stg_srv_201",
    )

    # T1.5: Servicio Claro recarga $3.500 cobrada en efectivo (custodia 1150)
    enqueue_commercial_event(
        comm_conn,
        event_type="SERVICE_CONFIRMED",
        entity_type="SERVICIO",
        entity_id="stg_srv_202",
        action_type="CONFIRM",
        payload={
            "servicio_id": "stg_srv_202",
            "servicio": "CLARO",
            "monto_carga_cents": 350000,
            "pago_efectivo_cents": 350000,
            "pago_transferencia_cents": 0,
            "total_cobrado_cents": 350000,
            "actor": "cajero_stg_1",
            "fecha_hora_utc": now_utc,
        },
        idempotency_key="idemp_stg_srv_202",
    )

    # T1.6: SUBE Mercado Pago $2.500 digital
    enqueue_commercial_event(
        comm_conn,
        event_type="SERVICE_CONFIRMED",
        entity_type="SERVICIO",
        entity_id="stg_srv_203",
        action_type="CONFIRM",
        payload={
            "servicio_id": "stg_srv_203",
            "servicio": "SUBE",
            "monto_carga_cents": 250000,
            "pago_efectivo_cents": 0,
            "pago_transferencia_cents": 250000,
            "total_cobrado_cents": 250000,
            "actor": "cajero_stg_1",
            "fecha_hora_utc": now_utc,
        },
        idempotency_key="idemp_stg_srv_203",
    )

    # T1.7: Venta con Adicional en Efectivo ($600 de adicional en total $3.600 en 1110)
    enqueue_commercial_event(
        comm_conn,
        event_type="SALE_CONFIRMED",
        entity_type="TICKET",
        entity_id="stg_sale_104",
        action_type="CONFIRM",
        payload={
            "ticket_id": "STG-104",
            "monto_efectivo_cents": 360000,
            "monto_transferencia_cents": 0,
            "total_venta_cents": 360000,
            "monto_adicional_cents": 60000,
            "metodo_pago": "EFECTIVO",
            "usuario": "cajero_stg_1",
            "fecha_hora_utc": now_utc,
        },
        idempotency_key="idemp_stg_sale_104",
    )

    # T1.8: Venta con Adicional Digital ($500 de adicional en total $4.000 en 1210)
    enqueue_commercial_event(
        comm_conn,
        event_type="SALE_CONFIRMED",
        entity_type="TICKET",
        entity_id="stg_sale_105",
        action_type="CONFIRM",
        payload={
            "ticket_id": "STG-105",
            "monto_efectivo_cents": 0,
            "monto_transferencia_cents": 400000,
            "total_venta_cents": 400000,
            "monto_adicional_cents": 50000,
            "metodo_pago": "MERCADO_PAGO",
            "usuario": "cajero_stg_1",
            "fecha_hora_utc": now_utc,
        },
        idempotency_key="idemp_stg_sale_105",
    )

    # T1.9: Gasto Autorizado desde caja diaria ($2.100 en 1110)
    enqueue_commercial_event(
        comm_conn,
        event_type="EXPENSE_CONFIRMED",
        entity_type="GASTO",
        entity_id="stg_exp_301",
        action_type="CONFIRM",
        payload={
            "gasto_id": "stg_exp_301",
            "detalle": "Compra de bolsas y rollos térmicos",
            "monto_salida_cents": 210000,
            "metodo_pago": "EFECTIVO",
            "actor": "cajero_stg_1",
            "fecha_hora_utc": now_utc,
        },
        idempotency_key="idemp_stg_exp_301",
    )

    # T1.10: Venta errónea $3.000 en 1110 confirmada
    enqueue_commercial_event(
        comm_conn,
        event_type="SALE_CONFIRMED",
        entity_type="TICKET",
        entity_id="stg_sale_106_err",
        action_type="CONFIRM",
        payload={
            "ticket_id": "STG-106-ERR",
            "monto_efectivo_cents": 300000,
            "monto_transferencia_cents": 0,
            "total_venta_cents": 300000,
            "metodo_pago": "EFECTIVO",
            "usuario": "cajero_stg_1",
            "fecha_hora_utc": now_utc,
        },
        idempotency_key="idemp_stg_sale_106",
    )

    # Drenar lote ordinario de Turno 1
    stats_t1_1 = process_pending_commercial_events(comm_conn, tes_conn, batch_size=20)
    assert stats_t1_1["published"] == 10
    assert stats_t1_1["failed"] == 0

    # T1.11: Anulación de la venta errónea mediante SALE_VOIDED
    enqueue_commercial_event(
        comm_conn,
        event_type="SALE_VOIDED",
        entity_type="TICKET",
        entity_id="stg_sale_106_err",
        action_type="VOID",
        payload={
            "ticket_id": "STG-106-ERR",
            "motivo": "Ticket duplicado por error de operador",
            "usuario": "supervisor_stg",
            "monto_efectivo_cents": 300000,
            "monto_transferencia_cents": 0,
            "reference_idempotency_key": "idemp_stg_sale_106",
        },
        idempotency_key="idemp_stg_void_106",
    )

    # T1.12: Inyección Adversarial en Staging:
    # A) Simular interrupción del worker con evento huérfano en PROCESSING
    enqueue_commercial_event(
        comm_conn,
        event_type="SALE_CONFIRMED",
        entity_type="TICKET",
        entity_id="stg_sale_107_orphan",
        action_type="CONFIRM",
        payload={
            "ticket_id": "STG-107",
            "monto_efectivo_cents": 150000,
            "monto_transferencia_cents": 0,
            "total_venta_cents": 150000,
            "metodo_pago": "EFECTIVO",
            "usuario": "cajero_stg_1",
            "fecha_hora_utc": now_utc,
        },
        idempotency_key="idemp_stg_sale_107",
    )
    comm_conn.execute(
        """
        UPDATE commercial_events_outbox
        SET status = 'PROCESSING', updated_at = '2026-10-30T00:00:00+00:00'
        WHERE idempotency_key = 'idemp_stg_sale_107';
        """
    )
    comm_conn.commit()

    # Recuperar evento huérfano
    rec_orphans = recover_orphaned_processing_events(comm_conn, timeout_seconds=1)
    assert rec_orphans == 1

    # B) Inyección de evento adulterado (tampering de hash)
    comm_conn.execute(
        """
        INSERT INTO commercial_events_outbox (
            event_id, event_type, entity_type, entity_id, action_type,
            idempotency_key, payload_canonical_hash, payload, status, created_at, updated_at
        ) VALUES (
            'stg_tampered_evt', 'SALE_CONFIRMED', 'TICKET', 'stg_bad', 'CONFIRM',
            'idemp_stg_tampered', 'hash_trucado_invalido_rfc8785', '{"monto_efectivo_cents": 888888}',
            'PENDING', datetime('now'), datetime('now')
        );
        """
    )
    comm_conn.commit()

    # Drenar lote adversarial de Turno 1
    stats_t1_2 = process_pending_commercial_events(comm_conn, tes_conn, batch_size=20)
    assert stats_t1_2["published"] == 2      # Void de 106 + Venta 107 recuperada
    assert stats_t1_2["dead_letter"] == 1    # Evento adulterado correctamente aislado

    # T1.13: Cierre de Turno 1 y Arqueo Informativo de Adicionales
    comm_conn.execute(
        """
        INSERT INTO commercial_adicionales_arqueo_turno (
            arqueo_id, cierre_turno_id, fecha_operativa, turno, responsable_caja,
            total_adicionales_efectivo_calculado_cents, total_adicionales_efectivo_declarado_cents,
            diferencia_cents, observaciones, traslado_ejecutado, created_at
        ) VALUES (
            'arq_stg_t1', 'turno_m_01', '2026-11-01', 'MAÑANA', 'cajero_stg_1',
            60000, 60000, 0, 'Arqueo informativo Turno Mañana Staging', 0, datetime('now')
        );
        """
    )
    comm_conn.commit()

    # Decisión voluntaria de socios: trasladar los $600 de adicionales físicos a caja 1140
    traslado_adic = transferFunds(
        conn=tes_conn,
        origin_fund_code="1110",
        destination_fund_code="1140",
        amount_cents=60000,
        dispatched_by="alan_socio",
        reason="Separación y traslado de adicionales Turno 1 a caja 1140",
        idempotency_key="idemp_stg_transf_adic_t1",
        evidence_reference="RECIBO_STG_ADIC_T1",
    )
    confirmTransferReceipt(
        conn=tes_conn,
        transfer_id=traslado_adic["transfer_id"],
        received_by="yilda_socia",
        idempotency_key="idemp_stg_conf_adic_t1",
    )

    # Actualizar estado de arqueo
    comm_conn.execute(
        """
        UPDATE commercial_adicionales_arqueo_turno
        SET traslado_ejecutado = 1, traslado_movement_id = ?
        WHERE arqueo_id = 'arq_stg_t1';
        """,
        (traslado_adic["transfer_id"],),
    )
    comm_conn.commit()

    # ------------------------------------------------------------------------
    # TURNO 2 (TARDE / NOCHE): CONTINUIDAD Y CIERRE DIARIO
    # ------------------------------------------------------------------------
    # Venta Efectivo T2 ($7.000)
    enqueue_commercial_event(
        comm_conn,
        event_type="SALE_CONFIRMED",
        entity_type="TICKET",
        entity_id="stg_sale_201_t2",
        action_type="CONFIRM",
        payload={
            "ticket_id": "STG-201",
            "monto_efectivo_cents": 700000,
            "monto_transferencia_cents": 0,
            "total_venta_cents": 700000,
            "metodo_pago": "EFECTIVO",
            "usuario": "cajero_stg_2",
            "fecha_hora_utc": now_utc,
        },
        idempotency_key="idemp_stg_sale_201_t2",
    )

    # Venta Digital T2 ($11.000)
    enqueue_commercial_event(
        comm_conn,
        event_type="SALE_CONFIRMED",
        entity_type="TICKET",
        entity_id="stg_sale_202_t2",
        action_type="CONFIRM",
        payload={
            "ticket_id": "STG-202",
            "monto_efectivo_cents": 0,
            "monto_transferencia_cents": 1100000,
            "total_venta_cents": 1100000,
            "metodo_pago": "MERCADO_PAGO",
            "usuario": "cajero_stg_2",
            "fecha_hora_utc": now_utc,
        },
        idempotency_key="idemp_stg_sale_202_t2",
    )

    # Drenar ventas de Turno 2
    stats_t2 = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats_t2["published"] == 2

    # Retiro preventivo de efectivo hacia caja fuerte (1110 -> 1120: $12.000)
    traslado_resguardo = transferFunds(
        conn=tes_conn,
        origin_fund_code="1110",
        destination_fund_code="1120",
        amount_cents=1200000,
        dispatched_by="alan_socio",
        reason="Resguardo preventivo tesoro Staging T2",
        idempotency_key="idemp_stg_resguardo_t2",
    )
    confirmTransferReceipt(
        conn=tes_conn,
        transfer_id=traslado_resguardo["transfer_id"],
        received_by="alan_socio",
        idempotency_key="idemp_stg_conf_resguardo_t2",
    )

    # ------------------------------------------------------------------------
    # CIERRE DIARIO DEFINITIVO Y BARRIDO OBLIGATORIO (LAB-010)
    # ------------------------------------------------------------------------
    beginClosure(tes_conn, jornada["id"], "alan_socio")

    # Barrido obligatorio de 1110 hacia 1120
    sweep_res = executeCashDrawerSweep(
        conn=tes_conn,
        journal_id=jornada["id"],
        user_id="alan_socio",
        reason="Barrido de cierre diario Staging 1110 -> 1120",
    )
    assert sweep_res["swept"] is True

    # Validar 1110 en 0 absoluto
    f1110_cierre = obtener_fondo_por_codigo(tes_conn, "1110")
    assert f1110_cierre["saldo_centavos"] == 0, "Fondo 1110 debe terminar en 0"

    # Confirmación de cierre con snapshot canónico SHA-256
    cierre_def = confirmClosure(
        conn=tes_conn,
        journal_id=jornada["id"],
        user_id="alan_socio",
        observations="Cierre de jornada Staging completado sin discrepancias",
    )
    assert cierre_def["status"] == "CONFIRMED"
    assert "canonical_hash" in cierre_def
    assert len(cierre_def["canonical_hash"]) == 64

    # ------------------------------------------------------------------------
    # AUDITORÍA MATEMÁTICA Y CONCILIACIÓN FINAL DEL LIBRO MAYOR
    # ------------------------------------------------------------------------
    rec_final = reconcile_treasury_ledger_balances(tes_conn)
    assert rec_final["cuadre_exacto"] is True, f"Discrepancias: {rec_final['discrepancias']}"
    assert rec_final["fondos_auditados"] == len(FONDOS_CANONICOS)
    assert len(rec_final["discrepancias"]) == 0

    summary = getTreasurySummary(tes_conn)

    reporte_jornada = {
        "status": "PILOT_DAY_SUCCESS",
        "jornada_id": jornada["id"],
        "fecha_operativa": fecha_operativa,
        "cierre_canonical_hash": cierre_def["canonical_hash"],
        "total_eventos_procesados": stats_t1_1["published"] + stats_t1_2["published"] + stats_t2["published"],
        "dead_letter_detectados": stats_t1_2["dead_letter"],
        "cuadre_exacto_libro_mayor": rec_final["cuadre_exacto"],
        "saldos_reconstruidos_por_fondo": rec_final["saldos_reconstruidos"],
        "resumen_tesoreria": summary,
    }

    comm_conn.close()
    tes_conn.close()

    logger.info("Jornada piloto en Staging finalizada con CUADRE EXACTO.")
    return reporte_jornada


def main() -> int:
    parser = argparse.ArgumentParser(description="Orquestador del Piloto Controlado en Staging — Etapa 7")
    parser.add_argument("--init", action="store_true", help="Inicializar bases de staging limpias")
    parser.add_argument("--verify-isolation", action="store_true", help="Verificar aislamiento de staging")
    parser.add_argument("--simulate-day", action="store_true", help="Ejecutar jornada piloto sintética")
    parser.add_argument("--health-check", action="store_true", help="Ejecutar diagnóstico de salud en staging")
    parser.add_argument("--full-pilot", action="store_true", help="Ejecutar flujo completo de Staging de punta a punta")

    args = parser.parse_args()

    if args.verify_isolation:
        res = verify_staging_isolation()
        print(json.dumps(res, indent=2))
        return 0

    if args.init:
        res = init_staging_databases()
        print(json.dumps(res, indent=2))
        return 0

    if args.simulate_day:
        res = execute_staging_pilot_day()
        print(json.dumps(res, indent=2))
        return 0

    if args.health_check:
        return run_health_check(STAGING_COMM_DB, STAGING_TES_DB)

    if args.full_pilot or len(sys.argv) == 1:
        logger.info("=== EJECUTANDO PROTOCOLO COMPLETO DE PILOTO EN STAGING ===")
        iso = verify_staging_isolation()
        init_res = init_staging_databases(clean=True)
        pilot_res = execute_staging_pilot_day()
        hc_code = run_health_check(STAGING_COMM_DB, STAGING_TES_DB)

        full_report = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "isolation": iso,
            "database_init": init_res,
            "pilot_simulation": pilot_res,
            "health_check_exit_code": hc_code,
            "certified": hc_code == 2 or hc_code == 0, # code 2 es DEGRADED si hubo dead_letter adversarial
        }
        print(json.dumps(full_report, indent=2, ensure_ascii=False))
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
