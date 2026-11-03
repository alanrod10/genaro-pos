"""Validación Financiera y Avance de Tesorería — GENARO POS.

Pruebas automatizadas sobre el entorno aislado de Staging / Memoria:
1. PREPAY -> SUBE: dimensiones independientes (plataforma, medio de pago, fondo receptor).
2. MERCADO PAGO -> PREPAY: persistencia y visibilidad del estado PENDING_DEFINITION_MERCADO_PAGO_PREPAY sin asientos contables.
3. Anulación y reversión simétrica en base descartable sin duplicación.
4. Derivación matemática del saldo teórico del libro mayor (tesoreria_movimientos).
5. Historial de movimientos con trazabilidad de reversiones, traslados y filtros.
"""

import sqlite3
import pytest

from db.migrations_runner import ejecutar_migraciones
from db.commercial_outbox_emitter import enqueue_commercial_event
from tesoreria.schema import inicializar_esquema_laboratorio
from tesoreria.catalog import sembrar_catalogo_canónico, obtener_fondo_por_codigo
from tesoreria.jornadas import abrir_jornada
from tesoreria.availability import getFundBalance, getTreasurySummary
from tesoreria.movements import listar_movimientos, registerEntry, registerExit
from tesoreria.reversals import reverseMovement
from tesoreria.transfers import transferFunds, confirmTransferReceipt
from tesoreria.commercial_bridge_worker import (
    process_pending_commercial_events,
    list_commercial_events_detailed,
    reconcile_treasury_ledger_balances,
)


@pytest.fixture
def disposable_dbs():
    """Provee un par de bases SQLite en memoria totalmente aisladas y descartables."""
    comm = sqlite3.connect(":memory:")
    comm.row_factory = sqlite3.Row
    ejecutar_migraciones(comm)

    tes = sqlite3.connect(":memory:")
    tes.row_factory = sqlite3.Row
    inicializar_esquema_laboratorio(tes)
    sembrar_catalogo_canónico(tes)
    abrir_jornada(tes, usuario="test_setup")

    yield comm, tes
    comm.close()
    tes.close()


def test_prepay_sube_dimensiones_independientes(disposable_dbs):
    """Verifica que PREPAY -> SUBE separe plataforma, medio de pago y fondo receptor."""
    comm_conn, tes_conn = disposable_dbs

    # Caso mixto: $500 carga en efectivo + $500 carga en transferencia
    enqueue_commercial_event(
        conn=comm_conn,
        event_type="SERVICE_CONFIRMED",
        entity_type="SERVICIO",
        entity_id=201,
        action_type="CONFIRM",
        idempotency_key="COMM_SERVICE_201_CONFIRM",
        payload={
            "servicio_id": 201,
            "servicio": "SUBE",
            "plataforma_origen": "PREPAY",
            "servicio_destino": "SUBE",
            "monto_carga_cents": 100000,
            "monto_adicional_cents": 10000,
            "total_cobrado_cents": 110000,
            "pago_efectivo_cents": 60000,       # $600 efvo
            "pago_transferencia_cents": 50000,  # $500 digital
            "fecha_hora_utc": "2026-11-01T12:00:00Z",
            "actor": "cajero_test",
        },
        created_at="2026-11-01T12:00:00Z",
    )
    comm_conn.commit()

    stats = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats["published"] == 1
    assert stats["failed"] == 0

    # 1. Verificar que NO hubo egreso de capital desde 1210 (Mercado Pago Propio)
    exits_1210 = tes_conn.execute(
        "SELECT COUNT(*) FROM tesoreria_movimientos m JOIN tesoreria_fondos f ON m.fund_id=f.id WHERE f.codigo='1210' AND m.movement_type='EXIT'"
    ).fetchone()[0]
    assert exits_1210 == 0

    # 2. Verificar que el efectivo ($600) ingresó a 1130 (Prepay Efectivo)
    entry_1130 = tes_conn.execute(
        "SELECT amount_cents, counterpart FROM tesoreria_movimientos m JOIN tesoreria_fondos f ON m.fund_id=f.id WHERE f.codigo='1130'"
    ).fetchone()
    assert entry_1130["amount_cents"] == 60000
    assert entry_1130["counterpart"] == "RECAUDACION_PREPAY_EFECTIVO"

    # 3. Verificar que la transferencia ($500) ingresó a 1210 con contrapartida RECAUDACION_PREPAY_DIGITAL
    entry_1210 = tes_conn.execute(
        "SELECT amount_cents, counterpart FROM tesoreria_movimientos m JOIN tesoreria_fondos f ON m.fund_id=f.id WHERE f.codigo='1210' AND m.movement_type='ENTRY'"
    ).fetchone()
    assert entry_1210["amount_cents"] == 50000
    assert entry_1210["counterpart"] == "RECAUDACION_PREPAY_DIGITAL"


def test_mercado_pago_prepay_pending_definition_persistencia(disposable_dbs):
    """Verifica que MERCADO PAGO -> PREPAY almacene PENDING_DEFINITION de forma persistente y sin movimientos contables."""
    comm_conn, tes_conn = disposable_dbs

    enqueue_commercial_event(
        conn=comm_conn,
        event_type="SERVICE_CONFIRMED",
        entity_type="SERVICIO",
        entity_id=301,
        action_type="CONFIRM",
        idempotency_key="COMM_SERVICE_301_CONFIRM",
        payload={
            "servicio_id": 301,
            "servicio": "PREPAY",
            "plataforma_origen": "MERCADO PAGO",
            "servicio_destino": "PREPAY",
            "monto_carga_cents": 500000,
            "monto_adicional_cents": 0,
            "total_cobrado_cents": 500000,
            "pago_efectivo_cents": 500000,
            "pago_transferencia_cents": 0,
            "fecha_hora_utc": "2026-11-01T12:00:00Z",
            "actor": "cajero_test",
        },
        created_at="2026-11-01T12:00:00Z",
    )
    comm_conn.commit()

    stats = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats["published"] == 1
    assert stats["failed"] == 0

    # 1. Movimientos contables DEBEN ser 0
    mov_count = tes_conn.execute("SELECT COUNT(*) FROM tesoreria_movimientos").fetchone()[0]
    assert mov_count == 0

    # 2. Outbox debe contener el identificador PENDING_DEFINITION_MERCADO_PAGO_PREPAY_301
    row = comm_conn.execute(
        "SELECT status, tesoreria_movement_id FROM commercial_events_outbox WHERE entity_id = 301"
    ).fetchone()
    assert row["status"] == "PUBLISHED"
    assert row["tesoreria_movement_id"] == "PENDING_DEFINITION_MERCADO_PAGO_PREPAY_301"

    # 3. Consultabilidad vía list_commercial_events_detailed con filtro PENDING_DEFINITION
    reporte = list_commercial_events_detailed(comm_conn, status_filter="PENDING_DEFINITION")
    assert reporte["total"] == 1
    assert reporte["events"][0]["tesoreria_movement_id"] == "PENDING_DEFINITION_MERCADO_PAGO_PREPAY_301"

    # 4. Idempotencia: re-ejecutar worker no genera asientos
    stats2 = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats2["published"] == 0
    assert tes_conn.execute("SELECT COUNT(*) FROM tesoreria_movimientos").fetchone()[0] == 0


def test_prepay_sube_anulacion_y_reversion_simetrica(disposable_dbs):
    """Verifica la anulación de PREPAY -> SUBE con coincidencia exacta de piernas y cero duplicación."""
    comm_conn, tes_conn = disposable_dbs

    # 1. Carga inicial
    enqueue_commercial_event(
        conn=comm_conn,
        event_type="SERVICE_CONFIRMED",
        entity_type="SERVICIO",
        entity_id=401,
        action_type="CONFIRM",
        idempotency_key="COMM_SERVICE_401_CONFIRM",
        payload={
            "servicio_id": 401,
            "servicio": "SUBE",
            "plataforma_origen": "PREPAY",
            "servicio_destino": "SUBE",
            "monto_carga_cents": 200000,
            "monto_adicional_cents": 20000,
            "total_cobrado_cents": 220000,
            "pago_efectivo_cents": 220000,
            "pago_transferencia_cents": 0,
            "fecha_hora_utc": "2026-11-01T12:00:00Z",
            "actor": "cajero_test",
        },
        created_at="2026-11-01T12:00:00Z",
    )
    comm_conn.commit()
    process_pending_commercial_events(comm_conn, tes_conn)

    saldo_1130_post_alta = getFundBalance(tes_conn, "1130")["saldo_registrado_cents"]
    assert saldo_1130_post_alta == 220000

    # 2. Anulación comercial
    enqueue_commercial_event(
        conn=comm_conn,
        event_type="SERVICE_VOIDED",
        entity_type="SERVICIO",
        entity_id=401,
        action_type="VOID",
        idempotency_key="COMM_SERVICE_401_VOID",
        payload={
            "servicio_id": 401,
            "servicio": "SUBE",
            "motivo": "Cancelación por error de carga en terminal SUBE",
            "actor": "supervisor_test",
            "reference_idempotency_key": "COMM_SERVICE_401_CONFIRM",
            "fecha_hora_utc": "2026-11-01T12:15:00Z",
        },
        created_at="2026-11-01T12:15:00Z",
    )
    comm_conn.commit()
    stats_void = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats_void["published"] == 1

    # 3. Pierna de reversión debe coincidir exactamente en fondo 1130
    movs = listar_movimientos(tes_conn, codigo_fondo="1130")
    assert len(movs) == 2
    mov_rev = next(m for m in movs if m["movement_type"] == "REVERSAL")
    mov_orig = next(m for m in movs if m["movement_type"] == "ENTRY")

    assert mov_rev["is_reversal"] == 1
    assert mov_rev["reversal_of_id"] == mov_orig["movement_id"]
    assert mov_rev["amount_cents"] == mov_orig["amount_cents"]

    # Saldo final debe regresar a 0 centavos
    saldo_1130_post_void = getFundBalance(tes_conn, "1130")["saldo_registrado_cents"]
    assert saldo_1130_post_void == 0

    # Saldo 1210 debe permanecer inalterado en 0 centavos
    assert getFundBalance(tes_conn, "1210")["saldo_registrado_cents"] == 0


def test_saldo_teorico_libro_mayor(disposable_dbs):
    """Verifica que el saldo teórico derivado de tesoreria_movimientos coincida con el saldo registrado."""
    _, tes_conn = disposable_dbs

    registerEntry(
        conn=tes_conn,
        fund_code="1110",
        amount_cents=150000,
        counterpart="VENTA_TEST",
        reason="Venta efectivo",
        created_by="tester",
    )
    registerExit(
        conn=tes_conn,
        fund_code="1110",
        amount_cents=50000,
        counterpart="GASTO_TEST",
        reason="Gasto insumos",
        created_by="tester",
    )

    bal = getFundBalance(tes_conn, "1110")
    assert bal["saldo_registrado_cents"] == 100000
    assert bal["saldo_teorico_cents"] == 100000
    assert bal["diferencia_teorica_cents"] == 0

    reconcile = reconcile_treasury_ledger_balances(tes_conn)
    assert reconcile["cuadre_exacto"] is True
    assert reconcile["saldos_reconstruidos"]["1110"] == 100000


def test_historial_movimientos_filtros_y_reversiones(disposable_dbs):
    """Verifica los filtros por fondo, tipo de movimiento y búsqueda de texto con metadata de reversión."""
    _, tes_conn = disposable_dbs

    res_entry = registerEntry(
        conn=tes_conn,
        fund_code="1130",
        amount_cents=80000,
        counterpart="COBRANZA_PREPAY",
        reason="Recarga SUBE mostrador #901",
        created_by="alan",
        evidence_reference="SUBE_EVID_901",
    )
    reverseMovement(
        conn=tes_conn,
        movement_id=res_entry["movement_id"],
        user_id="supervisor",
        reason="Anulación por error de cliente",
        evidence_reference="VOID_SUBE_901",
    )

    # 1. Filtro por tipo REVERSAL
    rev_only = listar_movimientos(tes_conn, movement_type="REVERSAL")
    assert len(rev_only) == 1
    assert rev_only[0]["is_reversal"] == 1
    assert rev_only[0]["reversal_of_id"] == res_entry["movement_id"]

    # 2. Búsqueda por texto (referencia de evidencia o motivo)
    search_res = listar_movimientos(tes_conn, search="SUBE_EVID_901")
    assert len(search_res) == 1
    assert search_res[0]["movement_id"] == res_entry["movement_id"]

    # 3. Filtro por fondo 1130
    fondo_res = listar_movimientos(tes_conn, codigo_fondo="1130")
    assert len(fondo_res) == 2
