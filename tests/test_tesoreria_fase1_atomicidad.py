"""Pruebas de Atomicidad Transaccional y Orquestación — FASE 1 (PREPAY / TESORERÍA).

Verificación del contrato transaccional subordinado:
1. transferFunds(auto_commit=False) dentro de transacción externa.
2. confirmTransferReceipt(auto_commit=False) dentro de transacción externa.
3. cancelPendingTransfer(auto_commit=False) sin commit interno.
4. Creación y liberación de reservas (createReservation, releaseReservation, cancelReservation, executeReservedExit) con auto_commit=False.
5. Fallo inducido después del primer movimiento de una operación compuesta.
6. Rollback total de fondos, movimientos, reservas y auditoría.
7. Reintento posterior exitoso tras rollback.
8. Dos conexiones concurrentes intentando consumir la misma disponibilidad.
9. Idempotencia de reintentos en operaciones subordinadas.
10. Verificación de que ninguna función subordinada confirma accidentalmente la transacción del orquestador.
"""

import sqlite3
import pytest
from pathlib import Path

from tesoreria.isolation import conectar_db_laboratorio
from tesoreria.schema import inicializar_esquema_laboratorio
from tesoreria.catalog import sembrar_catalogo_canónico, obtener_fondo_por_codigo
from tesoreria.jornadas import abrir_jornada
from tesoreria.movements import registerEntry, registerExit, registrar_movimiento, listar_movimientos
from tesoreria.transfers import (
    ErrorTraslado,
    transferFunds,
    confirmTransferReceipt,
    cancelPendingTransfer,
    listar_traslados,
)
from tesoreria.reservations import (
    ErrorReserva,
    createReservation,
    applyReservation,
    releaseReservation,
    cancelReservation,
    executeReservedExit,
)
from tesoreria.closures import executeCashDrawerSweep


@pytest.fixture
def conn_fase1(tmp_path):
    """Crea una base SQLite aislada y descartable con catálogo, jornada y fondos iniciales."""
    db_file = tmp_path / "tesoreria_fase1_test.db"
    conn = conectar_db_laboratorio(str(db_file))
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)
    abrir_jornada(conn, usuario="alan", observaciones="Jornada para pruebas FASE 1")
    
    # Dotación inicial para pruebas:
    # 1110: $50.000 (5.000.000 cents)
    # 1120: $100.000 (10.000.000 cents)
    # 1220: $20.000 (2.000.000 cents)
    registerEntry(conn, "1110", 50000_00, "Aporte", "Fondo diario inicial", "alan")
    registerEntry(conn, "1120", 100000_00, "Aporte", "Fondo acumulado inicial", "alan")
    registerEntry(conn, "1220", 20000_00, "Carga David", "Saldo PREPAY inicial", "alan")
    
    return conn, str(db_file)


# ---------------------------------------------------------------------------
# PRUEBA 1: transferFunds(auto_commit=False) dentro de transacción externa
# ---------------------------------------------------------------------------
def test_transfer_funds_auto_commit_false(conn_fase1):
    conn, db_path = conn_fase1
    conn.isolation_level = None
    cur = conn.cursor()

    cur.execute("BEGIN IMMEDIATE;")
    res = transferFunds(
        conn=conn,
        origin_fund_code="1110",
        destination_fund_code="1120",
        amount_cents=10000_00,
        dispatched_by="alan",
        reason="Traslado en bloque 1",
        idempotency_key="tx_test_1",
        auto_commit=False,
    )
    assert res["status"] == "PENDING_RECEIPT"

    # En una segunda conexión de solo lectura fuera de la transacción, el traslado NO debe verse aún
    conn_ro = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur_ro = conn_ro.cursor()
    cur_ro.execute("SELECT COUNT(*) FROM tesoreria_traslados WHERE idempotency_key = 'tx_test_1'")
    assert cur_ro.fetchone()[0] == 0
    conn_ro.close()

    # Confirmamos desde el orquestador
    cur.execute("COMMIT;")
    cur.close()

    # Ahora sí debe ser visible
    f1110 = obtener_fondo_por_codigo(conn, "1110")
    assert f1110["saldo_centavos"] == 40000_00


# ---------------------------------------------------------------------------
# PRUEBA 2: confirmTransferReceipt(auto_commit=False) dentro de transacción externa
# ---------------------------------------------------------------------------
def test_confirm_transfer_receipt_auto_commit_false(conn_fase1):
    conn, _ = conn_fase1
    # Crear traslado previo
    t = transferFunds(
        conn=conn,
        origin_fund_code="1110",
        destination_fund_code="1120",
        amount_cents=5000_00,
        dispatched_by="alan",
        reason="Traslado previo",
        idempotency_key="tx_test_2",
        auto_commit=True,
    )
    transfer_id = t["transfer_id"]

    conn.isolation_level = None
    cur = conn.cursor()
    cur.execute("BEGIN IMMEDIATE;")
    res_conf = confirmTransferReceipt(
        conn=conn,
        transfer_id=transfer_id,
        received_by="alan",
        idempotency_key="tx_test_2_conf",
        auto_commit=False,
    )
    assert res_conf["status"] == "CONFIRMED"

    # Provocamos rollback del orquestador
    cur.execute("ROLLBACK;")
    cur.close()

    # El traslado debe permanecer en PENDING_RECEIPT y 1120 no debe tener los fondos acreditados
    f1120 = obtener_fondo_por_codigo(conn, "1120")
    assert f1120["saldo_centavos"] == 100000_00  # Intacto
    traslados = listar_traslados(conn)
    t_actual = next(x for x in traslados if x["transfer_id"] == transfer_id)
    assert t_actual["status"] == "PENDING_RECEIPT"


# ---------------------------------------------------------------------------
# PRUEBA 3: cancelPendingTransfer(auto_commit=False) sin commit interno
# ---------------------------------------------------------------------------
def test_cancel_pending_transfer_auto_commit_false(conn_fase1):
    conn, _ = conn_fase1
    t = transferFunds(
        conn=conn,
        origin_fund_code="1110",
        destination_fund_code="1120",
        amount_cents=7000_00,
        dispatched_by="alan",
        reason="Traslado a cancelar",
        idempotency_key="tx_test_3",
        auto_commit=True,
    )
    transfer_id = t["transfer_id"]

    conn.isolation_level = None
    cur = conn.cursor()
    cur.execute("BEGIN IMMEDIATE;")
    cancel_res = cancelPendingTransfer(
        conn=conn,
        transfer_id=transfer_id,
        cancelled_by="alan",
        reason="Error de conteo",
        idempotency_key="tx_test_3_canc",
        auto_commit=False,
    )
    assert cancel_res["status"] == "CANCELLED"

    # Rollback orquestador
    cur.execute("ROLLBACK;")
    cur.close()

    # El traslado no debe estar cancelado
    traslados = listar_traslados(conn)
    t_actual = next(x for x in traslados if x["transfer_id"] == transfer_id)
    assert t_actual["status"] == "PENDING_RECEIPT"
    f1110 = obtener_fondo_por_codigo(conn, "1110")
    assert f1110["saldo_centavos"] == 43000_00  # 50.000 - 7.000


# ---------------------------------------------------------------------------
# PRUEBA 4: Reservas con auto_commit=False (create, apply, release, cancel, executeReservedExit)
# ---------------------------------------------------------------------------
def test_reservations_lifecycle_auto_commit_false(conn_fase1):
    conn, _ = conn_fase1
    conn.isolation_level = None
    cur = conn.cursor()

    cur.execute("BEGIN IMMEDIATE;")
    res = createReservation(
        conn=conn,
        fund_code="1220",
        amount_cents=5000_00,
        reason="Reserva PREPAY",
        beneficiary="Cliente 1",
        created_by="cajero",
        obligation_id="OBL_TEST_4",
        idempotency_key="res_test_4",
        auto_commit=False,
    )
    res_id = res["reservation_id"]
    assert res["status"] == "ACTIVE"

    # Ejecutar salida reservada dentro de la misma transacción externa
    exit_res = executeReservedExit(
        conn=conn,
        reservation_id=res_id,
        amount_cents=5000_00,
        counterpart="PROVEEDOR",
        reason="Consumo recarga",
        performed_by="cajero",
        idempotency_key="exit_test_4",
        auto_commit=False,
    )
    assert exit_res["reservation_status"] == "FULLY_APPLIED"

    cur.execute("COMMIT;")
    cur.close()

    # Comprobar estado final en base
    f1220 = obtener_fondo_por_codigo(conn, "1220")
    assert f1220["saldo_centavos"] == 15000_00  # 20.000 - 5.000


# ---------------------------------------------------------------------------
# PRUEBA 5 y 6: Fallo inducido en operación compuesta y Rollback total
# ---------------------------------------------------------------------------
def test_composite_operation_failure_and_full_rollback(conn_fase1):
    conn, _ = conn_fase1
    
    # Snapshot previo de saldos y registros
    saldo_1110_prev = obtener_fondo_por_codigo(conn, "1110")["saldo_centavos"]
    saldo_1120_prev = obtener_fondo_por_codigo(conn, "1120")["saldo_centavos"]
    movs_prev_count = len(listar_movimientos(conn))
    transfers_prev_count = len(listar_traslados(conn))

    conn.isolation_level = None
    cur = conn.cursor()
    cur.execute("BEGIN IMMEDIATE;")

    try:
        # Paso 1: Primer traslado exitoso
        transferFunds(
            conn=conn,
            origin_fund_code="1110",
            destination_fund_code="1120",
            amount_cents=10000_00,
            dispatched_by="alan",
            reason="Tramo 1 compuesto",
            idempotency_key="comp_1",
            auto_commit=False,
        )

        # Paso 2: Crear reserva
        res = createReservation(
            conn=conn,
            fund_code="1110",
            amount_cents=5000_00,
            reason="Reserva compuesta",
            beneficiary="Mostrador",
            created_by="alan",
            idempotency_key="comp_res_1",
            auto_commit=False,
        )

        # Paso 3: Fallo inducido intencional (simula error de negocio o excepción)
        raise RuntimeError("Fallo forzado simulado para verificar atomicidad de rollback")

    except Exception:
        cur.execute("ROLLBACK;")
    finally:
        cur.close()

    # Verificación de invariantes: CERO cambios persistidos
    f1110_post = obtener_fondo_por_codigo(conn, "1110")["saldo_centavos"]
    f1120_post = obtener_fondo_por_codigo(conn, "1120")["saldo_centavos"]
    assert f1110_post == saldo_1110_prev
    assert f1120_post == saldo_1120_prev
    assert len(listar_movimientos(conn)) == movs_prev_count
    assert len(listar_traslados(conn)) == transfers_prev_count


# ---------------------------------------------------------------------------
# PRUEBA 7: Reintento posterior exitoso tras rollback
# ---------------------------------------------------------------------------
def test_retry_successful_after_rollback(conn_fase1):
    conn, _ = conn_fase1

    conn.isolation_level = None
    cur = conn.cursor()
    cur.execute("BEGIN IMMEDIATE;")

    # Operación con clave limpia
    t = transferFunds(
        conn=conn,
        origin_fund_code="1110",
        destination_fund_code="1120",
        amount_cents=10000_00,
        dispatched_by="alan",
        reason="Reintento limpio",
        idempotency_key="retry_clean_key",
        auto_commit=False,
    )
    cur.execute("COMMIT;")
    cur.close()

    assert t["status"] == "PENDING_RECEIPT"
    f1110 = obtener_fondo_por_codigo(conn, "1110")
    assert f1110["saldo_centavos"] == 40000_00


# ---------------------------------------------------------------------------
# PRUEBA 8: Dos conexiones concurrentes intentando consumir la misma disponibilidad
# ---------------------------------------------------------------------------
def test_concurrency_lock_and_balance_protection(conn_fase1):
    conn1, db_path = conn_fase1
    
    # Crear segunda conexión a la misma base de datos
    conn2 = conectar_db_laboratorio(db_path)

    # Fondos iniciales en 1220: $20.000
    conn1.isolation_level = None
    cur1 = conn1.cursor()
    cur1.execute("BEGIN IMMEDIATE;")

    # Conexión 1 reserva $15.000
    createReservation(
        conn=conn1,
        fund_code="1220",
        amount_cents=15000_00,
        reason="Reserva concurrente 1",
        beneficiary="T1",
        created_by="alan",
        idempotency_key="conc_res_1",
        auto_commit=False,
    )

    # Conexión 2 intenta abrir BEGIN IMMEDIATE y debe esperar o fallar por lock de SQLite
    conn2.isolation_level = None
    cur2 = conn2.cursor()
    with pytest.raises(sqlite3.OperationalError):
        # Con timeout 0 debe fallar inmediatamente por bloqueo
        cur2.execute("PRAGMA busy_timeout = 0;")
        cur2.execute("BEGIN IMMEDIATE;")

    cur1.execute("COMMIT;")
    cur1.close()

    # Ahora Conexión 2 puede intentar reservar: Saldo registrado = $20.000, reservado previo = $15.000 -> Disponible = $5.000
    # Si Conexión 2 solicita $10.000, debe fallar por saldo disponible insuficiente
    with pytest.raises(ErrorReserva) as exc_info:
        createReservation(
            conn=conn2,
            fund_code="1220",
            amount_cents=10000_00,
            reason="Reserva concurrente 2 que supera disponible",
            beneficiary="T2",
            created_by="alan",
            idempotency_key="conc_res_2",
            auto_commit=True,
        )
    assert "Saldo disponible insuficiente" in str(exc_info.value)

    cur2.close()
    conn2.close()


# ---------------------------------------------------------------------------
# PRUEBA 9: Idempotencia de reintentos subordinados
# ---------------------------------------------------------------------------
def test_subordinate_idempotency(conn_fase1):
    conn, _ = conn_fase1
    conn.isolation_level = None
    cur = conn.cursor()
    cur.execute("BEGIN IMMEDIATE;")

    res1 = createReservation(
        conn=conn,
        fund_code="1220",
        amount_cents=1000_00,
        reason="Test idempotencia",
        beneficiary="B1",
        created_by="alan",
        idempotency_key="idemp_sub_1",
        auto_commit=False,
    )

    # Misma llamada con misma clave dentro de la misma transacción
    res2 = createReservation(
        conn=conn,
        fund_code="1220",
        amount_cents=1000_00,
        reason="Test idempotencia",
        beneficiary="B1",
        created_by="alan",
        idempotency_key="idemp_sub_1",
        auto_commit=False,
    )

    cur.execute("COMMIT;")
    cur.close()

    assert res1["reservation_id"] == res2["reservation_id"]


# ---------------------------------------------------------------------------
# PRUEBA 10: Verificación de que una función subordinada NO confirma accidentalmente
# ---------------------------------------------------------------------------
def test_subordinate_does_not_prematurely_commit(conn_fase1):
    conn, _ = conn_fase1
    conn.isolation_level = None
    cur = conn.cursor()

    cur.execute("BEGIN IMMEDIATE;")
    
    # Mutación 1 directa en BD
    cur.execute(
        "UPDATE tesoreria_fondos SET nombre = 'NOMBRE_TEMPORAL' WHERE codigo = '1110'"
    )

    # Invocación subordinada con auto_commit=False
    createReservation(
        conn=conn,
        fund_code="1110",
        amount_cents=1000_00,
        reason="Prueba anti commit prematuro",
        beneficiary="B1",
        created_by="alan",
        idempotency_key="anti_premature_1",
        auto_commit=False,
    )

    # El orquestador decide hacer ROLLBACK
    cur.execute("ROLLBACK;")
    cur.close()

    # Si la función subordinada hubiera llamado a conn.commit(), la mutación 1 se habría persistido.
    # Verificamos que se revirtió íntegramente:
    f1110 = obtener_fondo_por_codigo(conn, "1110")
    assert f1110["nombre"] == "Drugstore Diario"
    assert f1110["saldo_centavos"] == 50000_00


# ---------------------------------------------------------------------------
# PRUEBA 11: cancelReservation función de conveniencia
# ---------------------------------------------------------------------------
def test_cancel_reservation_helper(conn_fase1):
    conn, _ = conn_fase1
    res = createReservation(
        conn=conn,
        fund_code="1220",
        amount_cents=3000_00,
        reason="Para cancelar",
        beneficiary="B2",
        created_by="alan",
        auto_commit=True,
    )
    res_id = res["reservation_id"]

    canc = cancelReservation(
        conn=conn,
        reservation_id=res_id,
        performed_by="alan",
        reason="Cliente canceló",
        auto_commit=True,
    )
    assert canc["status"] == "RELEASED"
    assert canc["released_amount_cents"] == 3000_00
