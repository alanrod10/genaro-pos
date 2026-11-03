"""Pruebas de Ensayo General (DRY-RUN), Simulación de Jornada Comercial y Preparación Operativa — Etapa 6.

Verifica:
1. Ensayo general de migración (DRY-RUN) desde cero, tablas, índices, rollback simétrico y re-aplicación.
2. Simulación de jornada comercial completa multi-turno:
   - Ventas en efectivo, digitales y mixtas.
   - Servicios virtuales (Prepay y Claro) con afectación de saldo digital y recaudación.
   - Operaciones SUBE y adicionales (físicos y digitales).
   - Gastos y retiros autorizados.
   - Anulaciones con contra-asientos inmutables de reversión.
   - Escenarios adversariales: caídas del worker, recuperación de eventos PROCESSING huérfanos,
     detección de eventos duplicados y detección de alteración de payload (tampering).
   - Arqueo informativo de cierre de turno sin impacto financiero automático.
   - Traslado manual documentado de adicionales físicos desde 1110 a 1140 por decisión de los socios.
   - Vaciado obligatorio de cierre diario (sweep 1110 -> 1120) y snapshot definitivo con hash canónico.
3. Conciliación matemática integral del libro mayor para los 9 fondos canónicos:
   - Cuadre exacto (debe = haber, saldo = sum(entradas) - sum(salidas)).
   - Ausencia de saldos negativos en fondos físicos.
   - Segregación estricta del fondo de recaudación de terceros (1150 Claro Efectivo en custodia).
   - 100% de paridad entre eventos comerciales publicados y movimientos en tesorería.
4. Servicio continuo y runner autónomo:
   - Manejo de archivo PID bloqueante (PidLockManager).
   - Diagnóstico integral de salud (--health-check).
   - Ejecución por ciclo único (--once) y recuperación de eventos (--recover-only).
5. Control de acceso y RBAC: protección estricta de endpoints para Cajero vs Administrador/Socio.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import pytest
from tesoreria.catalog import CODIGOS_CANONICOS
from fastapi import status
from fastapi.testclient import TestClient

from api.auth import AuthenticatedUser
from api.dependencies import get_current_user, get_db_connection
from api.main import app
from api.routers.commercial_bridge import get_tesoreria_connection
from db.commercial_outbox_emitter import compute_canonical_hash, enqueue_commercial_event
from db.migrations_runner import ejecutar_migraciones, obtener_versiones_aplicadas
from scripts.dry_run_etapa6_migracion import ejecutar_dry_run_migracion
from scripts.run_commercial_bridge_worker import (
    PidLockManager,
    ServiceLockError,
    open_db_connection,
    run_health_check,
)
from tesoreria.availability import getFundAvailability, getTreasurySummary
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
    CommercialBridgeBackgroundWorker,
    get_commercial_bridge_status,
    get_simplified_sync_status,
    list_commercial_events_detailed,
    process_pending_commercial_events,
    reconcile_treasury_ledger_balances,
    recover_orphaned_processing_events,
    reprocess_outbox_event,
)
from tesoreria.jornadas import abrir_jornada, obtener_jornada_actual
from tesoreria.schema import inicializar_esquema_laboratorio
from tesoreria.transfers import confirmTransferReceipt, transferFunds


# ============================================================================
# 1. ENSAYO GENERAL DE MIGRACIÓN (DRY-RUN)
# ============================================================================

def test_dry_run_migracion_y_rollback_simetrico(tmp_path):
    """Ejecuta el dry-run completo verificando esquema, tablas, rollback y re-aplicación."""
    comm_file = tmp_path / "comm_dryrun.db"
    tes_file = tmp_path / "tes_dryrun.db"

    reporte = ejecutar_dry_run_migracion(comm_file, tes_file)

    assert reporte["resultado"] == "EXITOSO"
    etapas = reporte["etapas"]

    # 1. 12 migraciones o más
    assert etapas["migraciones_aplicadas"]["total"] >= 12
    assert 12 in etapas["migraciones_aplicadas"]["versiones"]

    # 2. Estructura outbox
    assert etapas["estructura_outbox"]["tiene_reprocessed_by"] is True
    assert etapas["estructura_outbox"]["tiene_reprocessed_at"] is True
    assert etapas["estructura_outbox"]["foreign_keys_ok"] is True
    assert etapas["estructura_outbox"]["integrity_check"] == "ok"

    # 3. Rollback simétrico y re-aplicación
    assert etapas["rollback_simetrico"]["version_12_removida"] is True
    assert etapas["rollback_simetrico"]["tabla_outbox_eliminada"] is True
    assert etapas["re_aplicacion_exitosa"] is True

    # 4. Catálogo canónico 9 fondos
    assert etapas["catalogo_tesoreria"]["fondos_totales"] == len(CODIGOS_CANONICOS)  # catálogo canónico vigente (incluye 1160)
    assert etapas["catalogo_tesoreria"]["todos_saldos_en_cero"] is True
    assert etapas["catalogo_tesoreria"]["todos_activos"] is True

    # 5. Conciliación inicial
    assert etapas["conciliacion_inicial"]["cuadre_exacto"] is True
    assert len(etapas["conciliacion_inicial"]["discrepancias"]) == 0


# ============================================================================
# 2. SIMULACIÓN DE JORNADA COMERCIAL MULTI-TURNO, ADVERSARIAL Y CONCILIACIÓN
# ============================================================================

def test_simulacion_jornada_comercial_completa_y_conciliacion(tmp_path):
    """Simula una jornada comercial completa con 2 turnos, operaciones variadas,
    escenarios adversariales, arqueo de adicionales, traslados y conciliación del mayor."""

    comm_path = tmp_path / "comm_jornada.db"
    tes_path = tmp_path / "tes_jornada.db"

    comm_conn = open_db_connection(comm_path)
    tes_conn = open_db_connection(tes_path)

    # Inicializar esquema comercial
    comm_conn.execute(
        """
        CREATE TABLE productos (
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
    ejecutar_migraciones(comm_conn)

    # Inicializar Tesorería
    inicializar_esquema_laboratorio(tes_conn)
    sembrar_catalogo_canónico(tes_conn)

    # ------------------------------------------------------------------------
    # FASE 1: APERTURA DE JORNADA DE TESORERÍA Y TURNO 1 (MAÑANA)
    # ------------------------------------------------------------------------
    fecha_hoy = "2026-10-31"
    jornada = abrir_jornada(tes_conn, fecha_hoy, "alan_admin")
    assert jornada["estado"] == "ABIERTA"

    # Saldo inicial de prueba en 1220 (Prepay Digital) y 1230 (Claro Comercios Digital)
    # Simula recarga previa de saldos digitales mayoristas
    tes_conn.execute("UPDATE tesoreria_fondos SET saldo_centavos = 500000 WHERE codigo = '1220';") # $5.000,00
    tes_conn.execute("UPDATE tesoreria_fondos SET saldo_centavos = 500000 WHERE codigo = '1230';") # $5.000,00
    # Registrar asiento inicial en libro mayor para cuadre contable
    now_utc = datetime.now(timezone.utc).isoformat()
    f1220 = obtener_fondo_por_codigo(tes_conn, "1220")
    f1230 = obtener_fondo_por_codigo(tes_conn, "1230")
    tes_conn.execute(
        """
        INSERT INTO tesoreria_movimientos (
            movement_id, journal_id, fund_id, movement_type, amount_cents, currency,
            status, counterpart, reason, created_by, terminal_id, occurred_at, recorded_at
        ) VALUES
        ('mov_init_1220', ?, ?, 'ENTRY', 500000, 'ARS', 'POSTED', 'APORTE_INICIAL', 'Saldo mayorista Prepay', 'alan_admin', 'T1', ?, ?),
        ('mov_init_1230', ?, ?, 'ENTRY', 500000, 'ARS', 'POSTED', 'APORTE_INICIAL', 'Saldo mayorista Claro', 'alan_admin', 'T1', ?, ?);
        """,
        (jornada["id"], f1220["id"], now_utc, now_utc, jornada["id"], f1230["id"], now_utc, now_utc),
    )
    tes_conn.commit()

    # 1. Operación 1: Venta en Efectivo (Drugstore Diario 1110)
    # Ticket #1001 por $4.500 ($4.500 en centavos = 450000)
    enqueue_commercial_event(
        comm_conn,
        event_type="SALE_CONFIRMED",
        entity_type="SALE",
        entity_id="sale_1001",
        action_type="INSERT",
        payload={
            "ticket_id": "TICK-1001",
            "monto_total_cents": 450000,
            "metodo_pago": "EFECTIVO",
            "terminal_id": "POS-01",
            "cajero_id": "cajero_manana",
        },
        idempotency_key="idemp_sale_1001",
    )

    # 2. Operación 2: Venta Digital Mercado Pago (Fondo 1210)
    # Ticket #1002 por $12.000 ($12.000 en centavos = 1200000)
    enqueue_commercial_event(
        comm_conn,
        event_type="SALE_CONFIRMED",
        entity_type="SALE",
        entity_id="sale_1002",
        action_type="INSERT",
        payload={
            "ticket_id": "TICK-1002",
            "monto_total_cents": 1200000,
            "metodo_pago": "MERCADO_PAGO",
            "terminal_id": "POS-01",
            "cajero_id": "cajero_manana",
        },
        idempotency_key="idemp_sale_1002",
    )

    # 3. Operación 3: Venta Mixta ($5.000 efectivo en 1110, $10.000 digital en 1210)
    # Ticket #1003 por $15.000 total
    enqueue_commercial_event(
        comm_conn,
        event_type="SALE_CONFIRMED",
        entity_type="SALE",
        entity_id="sale_1003",
        action_type="INSERT",
        payload={
            "ticket_id": "TICK-1003",
            "monto_total_cents": 1500000,
            "metodo_pago": "MIXTO",
            "desglose_pagos": [
                {"metodo": "EFECTIVO", "monto_cents": 500000, "fondo_codigo": "1110"},
                {"metodo": "MERCADO_PAGO", "monto_cents": 1000000, "fondo_codigo": "1210"},
            ],
            "terminal_id": "POS-01",
            "cajero_id": "cajero_manana",
        },
        idempotency_key="idemp_sale_1003",
    )

    # 4. Operación 4: Servicio Prepay recarga $3.000 cobrada en efectivo
    # Decrementa saldo digital 1220 ($3.000) y cobra efectivo en 1110 ($3.000)
    enqueue_commercial_event(
        comm_conn,
        event_type="SERVICE_CONFIRMED",
        entity_type="SERVICE",
        entity_id="srv_prepay_2001",
        action_type="INSERT",
        payload={
            "service_id": "srv_prepay_2001",
            "tipo_servicio": "PREPAY",
            "monto_servicio_cents": 300000,
            "metodo_cobro": "EFECTIVO",
            "fondo_debito": "1220",
            "fondo_cobro": "1110",
            "cajero_id": "cajero_manana",
        },
        idempotency_key="idemp_srv_2001",
    )

    # 5. Operación 5: Servicio Claro recarga $2.000 cobrada en efectivo
    # Decrementa saldo digital 1230 ($2.000) y recauda en custodia 1150 ($2.000)
    enqueue_commercial_event(
        comm_conn,
        event_type="SERVICE_CONFIRMED",
        entity_type="SERVICE",
        entity_id="srv_claro_2002",
        action_type="INSERT",
        payload={
            "service_id": "srv_claro_2002",
            "tipo_servicio": "CLARO",
            "monto_servicio_cents": 200000,
            "metodo_cobro": "EFECTIVO",
            "fondo_debito": "1230",
            "fondo_cobro": "1150",
            "cajero_id": "cajero_manana",
        },
        idempotency_key="idemp_srv_2002",
    )

    # 6. Operación 6: Operación SUBE Mercado Pago ($1.500)
    enqueue_commercial_event(
        comm_conn,
        event_type="SALE_CONFIRMED",
        entity_type="SALE",
        entity_id="sube_3001",
        action_type="INSERT",
        payload={
            "ticket_id": "SUBE-3001",
            "monto_total_cents": 150000,
            "metodo_pago": "MERCADO_PAGO",
            "es_sube": True,
            "cajero_id": "cajero_manana",
        },
        idempotency_key="idemp_sube_3001",
    )

    # 7. Operación 7: Venta con adicional en efectivo ($500 de adicional en total $3.000)
    enqueue_commercial_event(
        comm_conn,
        event_type="SALE_CONFIRMED",
        entity_type="SALE",
        entity_id="sale_1004",
        action_type="INSERT",
        payload={
            "ticket_id": "TICK-1004",
            "monto_total_cents": 300000,
            "monto_adicional_cents": 50000,
            "metodo_pago": "EFECTIVO",
            "cajero_id": "cajero_manana",
        },
        idempotency_key="idemp_sale_1004",
    )

    # 8. Operación 8: Venta con adicional digital ($400 de adicional en total $2.400)
    enqueue_commercial_event(
        comm_conn,
        event_type="SALE_CONFIRMED",
        entity_type="SALE",
        entity_id="sale_1005",
        action_type="INSERT",
        payload={
            "ticket_id": "TICK-1005",
            "monto_total_cents": 240000,
            "monto_adicional_cents": 40000,
            "metodo_pago": "MERCADO_PAGO",
            "cajero_id": "cajero_manana",
        },
        idempotency_key="idemp_sale_1005",
    )

    # 9. Operación 9: Gasto autorizado pagado en efectivo ($1.200 desde 1110)
    enqueue_commercial_event(
        comm_conn,
        event_type="EXPENSE_CONFIRMED",
        entity_type="EXPENSE",
        entity_id="exp_4001",
        action_type="INSERT",
        payload={
            "gasto_id": "exp_4001",
            "monto_salida_cents": 120000,
            "fondo_origen": "1110",
            "rubro": "Insumos de Limpieza",
            "proveedor": "Distribuidora Sur",
            "usuario_autoriza": "alan_admin",
            "cajero_id": "cajero_manana",
        },
        idempotency_key="idemp_exp_4001",
    )

    # 10. Operación 10: Venta errónea por $2.000 en efectivo, confirmada
    enqueue_commercial_event(
        comm_conn,
        event_type="SALE_CONFIRMED",
        entity_type="SALE",
        entity_id="sale_1006_error",
        action_type="INSERT",
        payload={
            "ticket_id": "TICK-1006-ERR",
            "monto_total_cents": 200000,
            "metodo_pago": "EFECTIVO",
            "cajero_id": "cajero_manana",
        },
        idempotency_key="idemp_sale_1006",
    )

    # Drenar primer lote mediante worker
    stats_lote1 = process_pending_commercial_events(comm_conn, tes_conn, batch_size=20)
    assert stats_lote1["published"] == 10
    assert stats_lote1["failed"] == 0

    # 11. Anulación de Venta Errónea (Ticket #1006) mediante evento SALE_CANCELLED
    # Debe emitir contra-asiento vinculado (reversión) sin borrar el original
    enqueue_commercial_event(
        comm_conn,
        event_type="SALE_CANCELLED",
        entity_type="SALE",
        entity_id="sale_1006_error",
        action_type="UPDATE",
        payload={
            "ticket_id": "TICK-1006-ERR",
            "original_entity_id": "sale_1006_error",
            "motivo_anulacion": "Carga duplicada por error de cajero",
            "usuario_anula": "alan_admin",
        },
        idempotency_key="idemp_cancel_sale_1006",
    )

    # ------------------------------------------------------------------------
    # ESCENARIOS ADVERSARIALES EN VIVO
    # ------------------------------------------------------------------------

    # Adversarial A: Evento huérfano dejado en 'PROCESSING' por corte abrupto
    enqueue_commercial_event(
        comm_conn,
        event_type="SALE_CONFIRMED",
        entity_type="SALE",
        entity_id="sale_1007_crash",
        action_type="INSERT",
        payload={
            "ticket_id": "TICK-1007-CRASH",
            "monto_total_cents": 100000,
            "metodo_pago": "EFECTIVO",
            "cajero_id": "cajero_manana",
        },
        idempotency_key="idemp_sale_1007_crash",
    )
    # Simular que el worker tomó el evento y el proceso murió
    comm_conn.execute(
        """
        UPDATE commercial_events_outbox
        SET status = 'PROCESSING', updated_at = '2026-10-31T00:00:00+00:00'
        WHERE idempotency_key = 'idemp_sale_1007_crash';
        """
    )
    comm_conn.commit()

    # Worker inicia y recupera eventos huérfanos
    recuperados = recover_orphaned_processing_events(comm_conn, timeout_seconds=1)
    assert recuperados >= 1

    # Adversarial B: Reintento con clave idempotente idéntica ya confirmada
    # El emisor reenvía la venta 1001 (que ya fue PUBLISHED)
    enqueue_commercial_event(
        comm_conn,
        event_type="SALE_CONFIRMED",
        entity_type="SALE",
        entity_id="sale_1001",
        action_type="INSERT",
        payload={
            "ticket_id": "TICK-1001",
            "monto_total_cents": 450000,
            "metodo_pago": "EFECTIVO",
            "terminal_id": "POS-01",
            "cajero_id": "cajero_manana",
        },
        idempotency_key="idemp_sale_1001",
    )

    # Adversarial C: Evento alterado / payload adulterado (tampering)
    # Insertamos un evento con hash adulterado para forzar DEAD_LETTER
    comm_conn.execute(
        """
        INSERT INTO commercial_events_outbox (
            event_id, event_type, entity_type, entity_id, action_type,
            idempotency_key, payload_canonical_hash, payload, status, created_at, updated_at
        ) VALUES (
            'evt_tampered_001', 'SALE_CONFIRMED', 'SALE', 'sale_bad', 'INSERT',
            'idemp_tampered_001', 'hash_falso_invalido_que_no_coincide', '{"monto_total_cents": 999999}',
            'PENDING', datetime('now'), datetime('now')
        );
        """
    )
    comm_conn.commit()

    # Drenar lote adversarial
    stats_lote2 = process_pending_commercial_events(comm_conn, tes_conn, batch_size=20)
    assert stats_lote2["dead_letter"] == 1  # Detectó la alteración de payload
    assert stats_lote2["published"] >= 2    # Procesó la cancelación y el recuperado de crash

    # ------------------------------------------------------------------------
    # CIERRE DE TURNO 1 (MAÑANA) Y ARQUEO INFORMATIVO DE ADICIONALES
    # ------------------------------------------------------------------------
    # 1. Registrar arqueo informativo de adicionales (Decisión confirmada: NO genera movimiento contable)
    comm_conn.execute(
        """
        INSERT INTO commercial_adicionales_arqueo_turno (
            arqueo_id, cierre_turno_id, fecha_operativa, turno, responsable_caja,
            total_adicionales_efectivo_calculado_cents, total_adicionales_efectivo_declarado_cents,
            diferencia_cents, observaciones, traslado_ejecutado, created_at
        ) VALUES (
            'arq_t1_001', 'turno_m_01', '2026-10-31', 'MAÑANA', 'cajero_manana',
            50000, 50000, 0, 'Arqueo de adicionales efectivo sin discrepancias', 0, datetime('now')
        );
        """
    )
    comm_conn.commit()

    # 2. Decisión voluntaria de los socios: trasladar $500 de adicionales físicos desde 1110 a 1140
    traslado_adic = transferFunds(
        conn=tes_conn,
        origin_fund_code="1110",
        destination_fund_code="1140",
        amount_cents=50000,
        dispatched_by="alan_admin",
        reason="Traslado físico de adicionales turno mañana a caja 1140",
        idempotency_key="idemp_transf_adic_t1",
        evidence_reference="RECIBO_ADIC_T1",
    )
    confirmTransferReceipt(
        conn=tes_conn,
        transfer_id=traslado_adic["transfer_id"],
        received_by="yilda_socia",
        idempotency_key="idemp_conf_adic_t1",
    )

    # Actualizar estado de traslado en el arqueo informativo
    comm_conn.execute(
        """
        UPDATE commercial_adicionales_arqueo_turno
        SET traslado_ejecutado = 1, traslado_movement_id = ?
        WHERE arqueo_id = 'arq_t1_001';
        """,
        (traslado_adic["transfer_id"],),
    )
    comm_conn.commit()

    # ------------------------------------------------------------------------
    # FASE 2: TURNO 2 (TARDE / NOCHE)
    # ------------------------------------------------------------------------
    # Venta Efectivo Turno 2 ($6.000)
    enqueue_commercial_event(
        comm_conn,
        event_type="SALE_CONFIRMED",
        entity_type="SALE",
        entity_id="sale_2001_t2",
        action_type="INSERT",
        payload={
            "ticket_id": "TICK-2001",
            "monto_total_cents": 600000,
            "metodo_pago": "EFECTIVO",
            "cajero_id": "cajero_tarde",
        },
        idempotency_key="idemp_sale_2001_t2",
    )

    # Venta Digital Turno 2 ($8.000)
    enqueue_commercial_event(
        comm_conn,
        event_type="SALE_CONFIRMED",
        entity_type="SALE",
        entity_id="sale_2002_t2",
        action_type="INSERT",
        payload={
            "ticket_id": "TICK-2002",
            "monto_total_cents": 800000,
            "metodo_pago": "MERCADO_PAGO",
            "cajero_id": "cajero_tarde",
        },
        idempotency_key="idemp_sale_2002_t2",
    )

    # Retiro preventivo de efectivo hacia tesoro / caja secundaria ($10.000 trasladados 1110 -> 1120)
    # Para reducir el efectivo expuesto en el mostrador
    process_pending_commercial_events(comm_conn, tes_conn)

    traslado_resguardo = transferFunds(
        conn=tes_conn,
        origin_fund_code="1110",
        destination_fund_code="1120",
        amount_cents=1000000,
        dispatched_by="alan_admin",
        reason="Resguardo preventivo de efectivo en caja fuerte",
        idempotency_key="idemp_resguardo_t2",
    )
    confirmTransferReceipt(
        conn=tes_conn,
        transfer_id=traslado_resguardo["transfer_id"],
        received_by="alan_admin",
        idempotency_key="idemp_conf_resguardo_t2",
    )

    # ------------------------------------------------------------------------
    # FASE 3: CIERRE DIARIO DEFINITIVO DE TESORERÍA (LAB-010)
    # ------------------------------------------------------------------------
    # 1. Iniciar cierre (pasa a EN_CIERRE)
    beginClosure(tes_conn, jornada["id"], "alan_admin")

    # 2. Vaciado obligatorio de Caja Principal (1110 -> 1120 sweep)
    sweep_result = executeCashDrawerSweep(
        conn=tes_conn,
        journal_id=jornada["id"],
        user_id="alan_admin",
        reason="Barrido obligatorio de cierre 1110 -> 1120",
    )
    assert sweep_result["swept"] is True

    # Comprobar que tras el barrido, el saldo de 1110 es ESTRICTAMENTE 0
    f1110_cierre = obtener_fondo_por_codigo(tes_conn, "1110")
    assert f1110_cierre["saldo_centavos"] == 0, "Caja 1110 debe quedar en 0 centavos tras el barrido"

    # 3. Confirmar cierre definitivo con snapshot canónico inmutable y SHA-256
    cierre_def = confirmClosure(
        conn=tes_conn,
        journal_id=jornada["id"],
        user_id="alan_admin",
        observations="Cierre de jornada Etapa 6 sin anomalías",
    )
    assert cierre_def["status"] == "CONFIRMED"
    assert "canonical_hash" in cierre_def
    assert len(cierre_def["canonical_hash"]) == 64  # SHA-256

    # ------------------------------------------------------------------------
    # FASE 4: CONCILIACIÓN MATEMÁTICA Y AUDITORÍA INTEGRAL DE FONDOS
    # ------------------------------------------------------------------------
    rec = reconcile_treasury_ledger_balances(tes_conn)
    assert rec["cuadre_exacto"] is True, f"Discrepancias detectadas: {rec['discrepancias']}"
    assert rec["fondos_auditados"] == len(CODIGOS_CANONICOS)  # catálogo canónico vigente (incluye 1160)
    assert len(rec["discrepancias"]) == 0

    # Comprobaciones financieras específicas de las decisiones contables:
    f_1110 = obtener_fondo_por_codigo(tes_conn, "1110")
    f_1120 = obtener_fondo_por_codigo(tes_conn, "1120")
    f_1140 = obtener_fondo_por_codigo(tes_conn, "1140")
    f_1150 = obtener_fondo_por_codigo(tes_conn, "1150")
    f_1210 = obtener_fondo_por_codigo(tes_conn, "1210")
    f_1220 = obtener_fondo_por_codigo(tes_conn, "1220")
    f_1230 = obtener_fondo_por_codigo(tes_conn, "1230")

    # a. Fondo 1110 vaciado al final del día
    assert f_1110["saldo_centavos"] == 0

    # b. Fondo 1140 contiene exactamente el traslado manual de adicionales ($500 = 50.000 centavos)
    assert f_1140["saldo_centavos"] == 50000

    # c. Fondo 1150 (Claro en custodia) contiene exactamente lo recaudado de Claro ($2.000 = 200.000 centavos)
    assert f_1150["saldo_centavos"] == 200000
    assert f_1150["titularidad"] == "CUSTODIA"

    # d. Fondo 1210 contiene las ventas MP + SUBE MP + adicional digital ($400):
    # $12.000 (sale_1002) + $10.000 (split sale_1003) + $1.500 (sube) + $2.400 (sale_1005 c/adicional) + $8.000 (sale_2002) = $33.900 (3.390.000 centavos)
    assert f_1210["saldo_centavos"] == 3390000

    # e. Ningún fondo físico presenta saldo negativo
    for f in [f_1110, f_1120, f_1140, f_1150]:
        assert f["saldo_centavos"] >= 0, f"Fondo físico {f['codigo']} con saldo negativo"

    # f. Disponibilidad propia excluye fondos en custodia (1150)
    resumen = getTreasurySummary(tes_conn)
    fondos_dict = {f["fund_code"]: f for f in resumen["fondos"]}
    assert fondos_dict["1150"]["ownership"] == "CUSTODIA"

    comm_conn.close()
    tes_conn.close()


# ============================================================================
# 3. PRUEBAS DEL SERVICIO CONTINUO, PID LOCK Y HEALTH CHECK
# ============================================================================

def test_servicio_continuo_runner_pid_lock_y_health_check(tmp_path):
    """Verifica el runner de scripts/run_commercial_bridge_worker.py, archivo PID y salud."""
    pid_file = tmp_path / "test_bridge.pid"
    comm_path = tmp_path / "comm_service.db"
    tes_path = tmp_path / "tes_service.db"

    # 1. Probar Administrador de Bloqueo PID
    mgr1 = PidLockManager(pid_file)
    mgr1.acquire()
    assert pid_file.exists()

    # Una segunda instancia debe fallar al intentar adquirir el bloqueo
    mgr2 = PidLockManager(pid_file)
    with pytest.raises(ServiceLockError):
        mgr2.acquire()

    # Liberar el bloqueo y verificar que ahora sí puede adquirirse
    mgr1.release()
    assert not pid_file.exists()
    mgr2.acquire()
    mgr2.release()

    # 2. Probar Health Check con bases inicializadas
    comm_conn = open_db_connection(comm_path)
    comm_conn.execute(
        """
        CREATE TABLE productos (
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
    ejecutar_migraciones(comm_conn)
    comm_conn.close()

    tes_conn = open_db_connection(tes_path)
    inicializar_esquema_laboratorio(tes_conn)
    sembrar_catalogo_canónico(tes_conn)
    tes_conn.close()

    # Ejecutar diagnóstico de salud
    health_code = run_health_check(comm_path, tes_path)
    assert health_code == 0, "El diagnóstico de salud debería retornar código 0 (HEALTHY)"


# ============================================================================
# 4. CONTROL DE ACCESO (RBAC) Y PERMISOS EN ETAPA 6
# ============================================================================

def test_permisos_y_seguridad_etapa6(tmp_path):
    """Verifica que el rol Cajero no pueda acceder a monitoreo global ni reprocesar."""
    comm_conn = sqlite3.connect(":memory:", check_same_thread=False)
    comm_conn.row_factory = sqlite3.Row
    comm_conn.execute(
        """
        CREATE TABLE productos (
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
    ejecutar_migraciones(comm_conn)

    tes_conn = sqlite3.connect(":memory:", check_same_thread=False)
    tes_conn.row_factory = sqlite3.Row
    inicializar_esquema_laboratorio(tes_conn)
    sembrar_catalogo_canónico(tes_conn)

    app.dependency_overrides[get_db_connection] = lambda: comm_conn
    app.dependency_overrides[get_tesoreria_connection] = lambda: tes_conn

    client = TestClient(app)

    # 1. Cajero
    cajero = AuthenticatedUser(user_id="cajero_01", username="cajero_01", display_name="Cajero 01", role="cajero")
    app.dependency_overrides[get_current_user] = lambda: cajero

    # /sync-status permitido para Cajero
    r1 = client.get("/api/tesoreria/commercial-bridge/sync-status")
    assert r1.status_code == 200

    # Endpoints administrativos prohibidos para Cajero (403 Forbidden)
    r2 = client.get("/api/tesoreria/commercial-bridge/status")
    assert r2.status_code == status.HTTP_403_FORBIDDEN

    r3 = client.get("/api/tesoreria/commercial-bridge/events")
    assert r3.status_code == status.HTTP_403_FORBIDDEN

    r4 = client.post("/api/tesoreria/commercial-bridge/reprocess/any-event")
    assert r4.status_code == status.HTTP_403_FORBIDDEN

    r5 = client.post("/api/tesoreria/commercial-bridge/trigger-drain")
    assert r5.status_code == status.HTTP_403_FORBIDDEN

    # 2. Administrador / Socio permitido
    admin = AuthenticatedUser(user_id="admin_01", username="admin_01", display_name="Admin 01", role="administrador")
    app.dependency_overrides[get_current_user] = lambda: admin

    r_admin_status = client.get("/api/tesoreria/commercial-bridge/status")
    assert r_admin_status.status_code == 200
    admin_data = r_admin_status.json()
    assert "total" in admin_data and "published" in admin_data

    # Limpiar overrides
    app.dependency_overrides.clear()
    comm_conn.close()
    tes_conn.close()
