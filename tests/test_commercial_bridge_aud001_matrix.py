"""Pruebas obligatorias de despacho financiero del Commercial Bridge (AUD-001).

Cubre:
1. PREPAY -> SUBE: no debita 1210 por error, acredita recaudación en 1130 (o 1210 si digital).
2. MERCADO PAGO -> SUBE: conserva el tratamiento confirmado (débito 1210, cobranza 1110/1210).
3. PREPAY -> Personal, Movistar y Claro: no altera circuitos existentes (1110 para Personal/Movistar, 1150 custodia para Claro).
4. CLARO COMERCIOS -> Claro: conserva el circuito en custodia (1150).
5. MERCADO PAGO -> PREPAY: no crea obligaciones, transferencias ni liquidaciones no aprobadas (estado trazable PENDING_DEFINITION).
6. Evento repetido: idempotencia estricta, no duplica movimientos.
7. Evento sin plataforma: no infiere origen ambiguo ni genera imputación insegura en 1210.
8. Evento con combinación inválida: estado controlado y trazable sin movimiento financiero.
"""
import sqlite3
import pytest
from pathlib import Path

from db.commercial_outbox_emitter import enqueue_commercial_event
from tesoreria.catalog import sembrar_catalogo_canónico
from tesoreria.schema import inicializar_esquema_laboratorio
from tesoreria.commercial_bridge_worker import (
    process_pending_commercial_events,
    reconcile_treasury_ledger_balances,
)
from tesoreria.movements import registerEntry, registerExit
from tesoreria.jornadas import abrir_jornada


@pytest.fixture
def lab_env():
    """Crea conexiones en memoria para comercial y tesorería con esquemas completos."""
    comm_conn = sqlite3.connect(":memory:", check_same_thread=False)
    comm_conn.row_factory = sqlite3.Row
    comm_conn.execute(
        """
        CREATE TABLE commercial_events_outbox (
            sequence_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            action_type TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            payload_canonical_hash TEXT NOT NULL,
            payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            retry_count INTEGER NOT NULL DEFAULT 0,
            max_retries INTEGER NOT NULL DEFAULT 5,
            error_message TEXT,
            tesoreria_movement_id TEXT,
            reprocessed_by TEXT,
            reprocessed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            published_at TEXT
        );
        """
    )
    comm_conn.commit()

    tes_conn = sqlite3.connect(":memory:", check_same_thread=False)
    tes_conn.row_factory = sqlite3.Row
    inicializar_esquema_laboratorio(tes_conn)
    sembrar_catalogo_canónico(tes_conn)

    # Abrir jornada inicial para permitir asentar movimientos
    abrir_jornada(tes_conn, "2026-11-01", "auditor_tesoreria")

    try:
        yield comm_conn, tes_conn
    finally:
        comm_conn.close()
        tes_conn.close()


def test_prepay_sube_no_debita_1210_y_acredita_1130(lab_env):
    """AUD-001: PREPAY -> SUBE no debe debitar el fondo 1210 Mercado Pago Propio."""
    comm_conn, tes_conn = lab_env

    # 1. Fondear inicialmente 1210 con $10.000
    registerEntry(
        conn=tes_conn,
        fund_code="1210",
        amount_cents=1000000,
        counterpart="FONDEO_INICIAL",
        reason="Fondeo prueba 1210",
        created_by="admin",
    )

    # 2. Emitir carga PREPAY -> SUBE: Capital $1.000, adicional $100, cobrado efectivo $1.100
    enqueue_commercial_event(
        comm_conn,
        event_type="SERVICE_CONFIRMED",
        entity_type="SERVICIO",
        entity_id="srv_prepay_sube_1",
        action_type="CONFIRM",
        idempotency_key="COMM_SERVICE_srv_prepay_sube_1_CONFIRM",
        payload={
            "servicio_id": "srv_prepay_sube_1",
            "servicio": "SUBE",
            "plataforma_origen": "PREPAY",
            "servicio_destino": "SUBE",
            "monto_carga_cents": 100000,
            "monto_adicional_cents": 10000,
            "total_cobrado_cents": 110000,
            "pago_efectivo_cents": 110000,
            "pago_transferencia_cents": 0,
            "actor": "cajero_1",
        },
    )

    stats = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats["published"] == 1

    saldo_1210 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1210';").fetchone()[0]
    saldo_1130 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1130';").fetchone()[0]
    saldo_1110 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1110';").fetchone()[0]

    # ¡VERIFICACIÓN CRÍTICA!: 1210 permanece INTACTO en 1.000.000 centavos ($10.000)
    assert saldo_1210 == 1000000, "ERROR CRÍTICO: PREPAY -> SUBE debitó indebidamente el fondo 1210"

    # La cobranza en mostrador ingresa a 1130 Prepay Efectivo
    assert saldo_1130 == 110000  # $1.100 en cajón Prepay
    assert saldo_1110 == 0


def test_mercado_pago_sube_conserva_circuito_confirmado(lab_env):
    """MERCADO PAGO -> SUBE: debe conservar el circuito canónico (débito de saldo 1210 y cobro en 1110)."""
    comm_conn, tes_conn = lab_env

    # Fondear 1210 con $5.000
    registerEntry(
        conn=tes_conn,
        fund_code="1210",
        amount_cents=500000,
        counterpart="FONDEO_INICIAL",
        reason="Fondeo prueba 1210",
        created_by="admin",
    )

    enqueue_commercial_event(
        comm_conn,
        event_type="SERVICE_CONFIRMED",
        entity_type="SERVICIO",
        entity_id="srv_mp_sube_1",
        action_type="CONFIRM",
        idempotency_key="COMM_SERVICE_srv_mp_sube_1_CONFIRM",
        payload={
            "servicio_id": "srv_mp_sube_1",
            "servicio": "SUBE",
            "plataforma_origen": "MERCADO PAGO",
            "servicio_destino": "SUBE",
            "monto_carga_cents": 100000,
            "monto_adicional_cents": 15000,
            "total_cobrado_cents": 115000,
            "pago_efectivo_cents": 115000,
            "pago_transferencia_cents": 0,
            "actor": "cajero_1",
        },
    )

    stats = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats["published"] == 1

    saldo_1210 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1210';").fetchone()[0]
    saldo_1110 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1110';").fetchone()[0]

    # En 1210: 500.000 - 100.000 = 400.000 centavos ($4.000)
    assert saldo_1210 == 400000
    # En 1110: 115.000 centavos ($1.150)
    assert saldo_1110 == 115000


def test_prepay_personal_movistar_claro_circuitos_existentes(lab_env):
    """PREPAY -> Personal, Movistar y Claro preservan sus circuitos contables canónicos."""
    comm_conn, tes_conn = lab_env

    # 1. PREPAY -> Personal ($500 carga + $50 adic = $550 efectivo) -> ingresa a 1110
    enqueue_commercial_event(
        comm_conn,
        event_type="SERVICE_CONFIRMED",
        entity_type="SERVICIO",
        entity_id="srv_p_pers",
        action_type="CONFIRM",
        idempotency_key="COMM_SERVICE_srv_p_pers_CONFIRM",
        payload={
            "servicio_id": "srv_p_pers",
            "servicio": "Personal",
            "plataforma_origen": "PREPAY",
            "servicio_destino": "Personal",
            "monto_carga_cents": 50000,
            "monto_adicional_cents": 5000,
            "total_cobrado_cents": 55000,
            "pago_efectivo_cents": 55000,
            "pago_transferencia_cents": 0,
        },
    )

    # 2. PREPAY -> Movistar ($500 carga + $50 adic = $550 efectivo) -> ingresa a 1110
    enqueue_commercial_event(
        comm_conn,
        event_type="SERVICE_CONFIRMED",
        entity_type="SERVICIO",
        entity_id="srv_p_mov",
        action_type="CONFIRM",
        idempotency_key="COMM_SERVICE_srv_p_mov_CONFIRM",
        payload={
            "servicio_id": "srv_p_mov",
            "servicio": "Movistar",
            "plataforma_origen": "PREPAY",
            "servicio_destino": "Movistar",
            "monto_carga_cents": 50000,
            "monto_adicional_cents": 5000,
            "total_cobrado_cents": 55000,
            "pago_efectivo_cents": 55000,
            "pago_transferencia_cents": 0,
        },
    )

    # 3. PREPAY -> Claro ($500 carga + $50 adic = $550 efectivo) -> ingresa a 1150 (Custodia Claro)
    enqueue_commercial_event(
        comm_conn,
        event_type="SERVICE_CONFIRMED",
        entity_type="SERVICIO",
        entity_id="srv_p_claro",
        action_type="CONFIRM",
        idempotency_key="COMM_SERVICE_srv_p_claro_CONFIRM",
        payload={
            "servicio_id": "srv_p_claro",
            "servicio": "Claro",
            "plataforma_origen": "PREPAY",
            "servicio_destino": "Claro",
            "monto_carga_cents": 50000,
            "monto_adicional_cents": 5000,
            "total_cobrado_cents": 55000,
            "pago_efectivo_cents": 55000,
            "pago_transferencia_cents": 0,
        },
    )

    stats = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats["published"] == 3

    saldo_1110 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1110';").fetchone()[0]
    saldo_1150 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1150';").fetchone()[0]
    saldo_1210 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1210';").fetchone()[0]

    # 1110 tiene $550 (Personal) + $550 (Movistar) = $1.100 = 110.000 centavos
    assert saldo_1110 == 110000
    # 1150 tiene $550 (Claro custodia) = 55.000 centavos
    assert saldo_1150 == 55000
    # 1210 jamás fue tocado ni debitado
    assert saldo_1210 == 0


def test_claro_comercios_claro_circuito_existente(lab_env):
    """CLARO COMERCIOS -> Claro preserva la custodia estricta en fondo 1150."""
    comm_conn, tes_conn = lab_env

    enqueue_commercial_event(
        comm_conn,
        event_type="SERVICE_CONFIRMED",
        entity_type="SERVICIO",
        entity_id="srv_cc_claro",
        action_type="CONFIRM",
        idempotency_key="COMM_SERVICE_srv_cc_claro_CONFIRM",
        payload={
            "servicio_id": "srv_cc_claro",
            "servicio": "Claro",
            "plataforma_origen": "CLARO COMERCIOS",
            "servicio_destino": "Claro",
            "monto_carga_cents": 80000,
            "monto_adicional_cents": 0,
            "total_cobrado_cents": 80000,
            "pago_efectivo_cents": 80000,
            "pago_transferencia_cents": 0,
        },
    )

    stats = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats["published"] == 1

    saldo_1150 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1150';").fetchone()[0]
    assert saldo_1150 == 80000


def test_mercado_pago_prepay_no_crea_obligaciones_ni_movimientos(lab_env):
    """MERCADO PAGO -> PREPAY: no genera movimientos automáticos, deudas con David ni imputaciones."""
    comm_conn, tes_conn = lab_env

    # Registrar saldos iniciales de todos los fondos
    saldos_antes = {
        row["codigo"]: row["saldo_centavos"]
        for row in tes_conn.execute("SELECT codigo, saldo_centavos FROM tesoreria_fondos").fetchall()
    }
    movs_antes = tes_conn.execute("SELECT count(*) FROM tesoreria_movimientos;").fetchone()[0]

    enqueue_commercial_event(
        comm_conn,
        event_type="SERVICE_CONFIRMED",
        entity_type="SERVICIO",
        entity_id="srv_mp_prepay_pending",
        action_type="CONFIRM",
        idempotency_key="COMM_SERVICE_srv_mp_prepay_pending_CONFIRM",
        payload={
            "servicio_id": "srv_mp_prepay_pending",
            "servicio": "PREPAY",
            "plataforma_origen": "MERCADO PAGO",
            "servicio_destino": "PREPAY",
            "monto_carga_cents": 100000,
            "monto_adicional_cents": 10000,
            "total_cobrado_cents": 110000,
            "pago_efectivo_cents": 110000,
            "pago_transferencia_cents": 0,
        },
    )

    stats = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats["published"] == 1

    # Verificar que NO se crearon movimientos en tesorería
    movs_despues = tes_conn.execute("SELECT count(*) FROM tesoreria_movimientos;").fetchone()[0]
    assert movs_despues == movs_antes

    # Verificar que los saldos de los 9 fondos siguen idénticos
    saldos_despues = {
        row["codigo"]: row["saldo_centavos"]
        for row in tes_conn.execute("SELECT codigo, saldo_centavos FROM tesoreria_fondos").fetchall()
    }
    assert saldos_despues == saldos_antes

    # Verificar que el evento quedó publicado con resultado trazable
    row_evt = comm_conn.execute(
        "SELECT status, tesoreria_movement_id FROM commercial_events_outbox WHERE entity_id = 'srv_mp_prepay_pending'"
    ).fetchone()
    assert row_evt["status"] == "PUBLISHED"
    assert "PENDING_DEFINITION_MERCADO_PAGO_PREPAY" in row_evt["tesoreria_movement_id"]


def test_evento_repetido_no_duplica_movimientos(lab_env):
    """La idempotencia del worker garantiza que un evento ya procesado no duplica asientos."""
    comm_conn, tes_conn = lab_env

    payload = {
        "servicio_id": "srv_idemp_check",
        "servicio": "SUBE",
        "plataforma_origen": "PREPAY",
        "servicio_destino": "SUBE",
        "monto_carga_cents": 50000,
        "monto_adicional_cents": 5000,
        "total_cobrado_cents": 55000,
        "pago_efectivo_cents": 55000,
        "pago_transferencia_cents": 0,
    }
    enqueue_commercial_event(
        comm_conn,
        event_type="SERVICE_CONFIRMED",
        entity_type="SERVICIO",
        entity_id="srv_idemp_check",
        action_type="CONFIRM",
        idempotency_key="COMM_SERVICE_srv_idemp_check_CONFIRM",
        payload=payload,
    )

    stats1 = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats1["published"] == 1

    movs_count_1 = tes_conn.execute("SELECT count(*) FROM tesoreria_movimientos;").fetchone()[0]
    saldo_1130_1 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1130';").fetchone()[0]

    # Reintentar el procesamiento del lote de eventos
    stats2 = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats2["published"] == 0
    assert stats2["failed"] == 0

    movs_count_2 = tes_conn.execute("SELECT count(*) FROM tesoreria_movimientos;").fetchone()[0]
    saldo_1130_2 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1130';").fetchone()[0]

    assert movs_count_2 == movs_count_1
    assert saldo_1130_2 == saldo_1130_1


def test_evento_sin_plataforma_no_infiere_origen_ni_imputacion_insegura(lab_env):
    """Un evento sin plataforma (SUBE) no debe inferir Mercado Pago ni debitar 1210."""
    comm_conn, tes_conn = lab_env

    # Fondear 1210
    registerEntry(conn=tes_conn, fund_code="1210", amount_cents=500000, counterpart="FONDEO", reason="MP", created_by="admin")

    enqueue_commercial_event(
        comm_conn,
        event_type="SERVICE_CONFIRMED",
        entity_type="SERVICIO",
        entity_id="srv_sin_plataforma_sube",
        action_type="CONFIRM",
        idempotency_key="COMM_SERVICE_srv_sin_plataforma_sube_CONFIRM",
        payload={
            "servicio_id": "srv_sin_plataforma_sube",
            "servicio": "SUBE",
            "plataforma_origen": None,
            "servicio_destino": None,
            "monto_carga_cents": 100000,
            "monto_adicional_cents": 10000,
            "total_cobrado_cents": 110000,
            "pago_efectivo_cents": 110000,
            "pago_transferencia_cents": 0,
        },
    )

    stats = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats["published"] == 1

    saldo_1210 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1210';").fetchone()[0]
    assert saldo_1210 == 500000, "ERROR: No debió debitarse 1210 sin plataforma de origen"

    row_evt = comm_conn.execute(
        "SELECT tesoreria_movement_id FROM commercial_events_outbox WHERE entity_id = 'srv_sin_plataforma_sube'"
    ).fetchone()
    assert "UNSPECIFIED_PLATFORM_AMBIGUOUS_SERVICE_SUBE" in row_evt["tesoreria_movement_id"]


def test_evento_combinacion_invalida_estado_controlado_sin_movimientos(lab_env):
    """Una combinación incompatible en outbox queda en estado controlado sin generar movimientos."""
    comm_conn, tes_conn = lab_env

    movs_antes = tes_conn.execute("SELECT count(*) FROM tesoreria_movimientos;").fetchone()[0]

    enqueue_commercial_event(
        comm_conn,
        event_type="SERVICE_CONFIRMED",
        entity_type="SERVICIO",
        entity_id="srv_invalido_outbox",
        action_type="CONFIRM",
        idempotency_key="COMM_SERVICE_srv_invalido_outbox_CONFIRM",
        payload={
            "servicio_id": "srv_invalido_outbox",
            "servicio": "SUBE",
            "plataforma_origen": "CLARO COMERCIOS",
            "servicio_destino": "SUBE",
            "monto_carga_cents": 100000,
            "monto_adicional_cents": 10000,
            "total_cobrado_cents": 110000,
            "pago_efectivo_cents": 110000,
            "pago_transferencia_cents": 0,
        },
    )

    stats = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats["published"] == 1

    movs_despues = tes_conn.execute("SELECT count(*) FROM tesoreria_movimientos;").fetchone()[0]
    assert movs_despues == movs_antes

    row_evt = comm_conn.execute(
        "SELECT tesoreria_movement_id FROM commercial_events_outbox WHERE entity_id = 'srv_invalido_outbox'"
    ).fetchone()
    assert "INVALID_SERVICE_COMBINATION_CLARO COMERCIOS_SUBE" in row_evt["tesoreria_movement_id"]
