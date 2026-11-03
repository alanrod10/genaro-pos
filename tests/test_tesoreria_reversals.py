"""Pruebas de Reversiones Inmutables y Contra-asientos — LAB-011 (REV-001 a REV-005)."""

import sqlite3
import pytest
from tesoreria.catalog import obtener_fondo_por_codigo, sembrar_catalogo_canónico
from tesoreria.jornadas import abrir_jornada
from tesoreria.movements import registerEntry, registerExit
from tesoreria.reservations import applyReservation, createReservation, releaseReservation
from tesoreria.reversals import (
    ErrorReversion,
    getReversals,
    reverseMovement,
    reverseReservationOperation,
    reverseTransfer,
)
from tesoreria.schema import inicializar_esquema_laboratorio
from tesoreria.transfers import confirmTransferReceipt, transferFunds


@pytest.fixture
def conn_lab():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)
    abrir_jornada(conn, usuario="alan")
    yield conn
    conn.close()


def test_rev_001_reverse_entry_creates_counter_movement_and_preserves_original(conn_lab):
    """REV-001: Reversión total de ENTRY genera contra-movimiento vinculado, descuenta saldo y el original queda intacto."""
    entry = registerEntry(
        conn_lab,
        fund_code="1110",
        amount_cents=1000_00,
        counterpart="Cobro Cliente",
        reason="Cobro erróneo",
        created_by="alan",
    )

    f_antes = obtener_fondo_por_codigo(conn_lab, "1110")
    assert f_antes["saldo_centavos"] == 1000_00

    rev = reverseMovement(
        conn=conn_lab,
        movement_id=entry["movement_id"],
        user_id="supervisor_ana",
        reason="Error de imputación: comprobante duplicado",
        evidence_reference="DOC-ANULACION-102",
        idempotency_key="rev_key_001",
    )

    assert rev["status"] == "COMPLETED"
    assert rev["reversed_amount_cents"] == 1000_00
    assert rev["new_fund_balance_cents"] == 0

    # Invariante: El original NO se elimina ni se cambian sus campos canónicos
    cursor = conn_lab.cursor()
    cursor.execute("SELECT * FROM tesoreria_movimientos WHERE movement_id = ?", (entry["movement_id"],))
    orig_row = cursor.fetchone()
    assert orig_row["status"] == "POSTED"
    assert orig_row["amount_cents"] == 1000_00
    assert orig_row["reversed_amount_cents"] == 1000_00

    # Verificar que el contra-movimiento existe y está vinculado
    cursor.execute("SELECT * FROM tesoreria_movimientos WHERE movement_id = ?", (rev["counter_movement_id"],))
    counter_row = cursor.fetchone()
    assert counter_row["movement_type"] == "REVERSAL"
    assert counter_row["reversal_of_id"] == entry["movement_id"]
    assert counter_row["amount_cents"] == 1000_00


def test_rev_002_reverse_exit_restores_fund_balance(conn_lab):
    """REV-002: Reversión total de EXIT restituye saldo al fondo con contra-movimiento vinculado."""
    # Fondeo inicial
    registerEntry(conn_lab, fund_code="1110", amount_cents=2000_00, counterpart="Aporte", reason="Fondeo", created_by="alan")

    # Salida por 600 pesos
    exit_mov = registerExit(
        conn_lab,
        fund_code="1110",
        amount_cents=600_00,
        counterpart="Proveedor",
        reason="Pago anticipo",
        created_by="alan",
    )
    assert obtener_fondo_por_codigo(conn_lab, "1110")["saldo_centavos"] == 1400_00

    rev = reverseMovement(
        conn=conn_lab,
        movement_id=exit_mov["movement_id"],
        user_id="supervisor_ana",
        reason="Pago cancelado por el proveedor",
        evidence_reference="NC-PROV-991",
    )

    assert rev["reversed_amount_cents"] == 600_00
    assert rev["new_fund_balance_cents"] == 2000_00
    assert obtener_fondo_por_codigo(conn_lab, "1110")["saldo_centavos"] == 2000_00


def test_rev_003_partial_reversal_and_limit_enforcement(conn_lab):
    """REV-003: Reversiones parciales acumulativas sin exceder el importe original."""
    entry = registerEntry(
        conn_lab,
        fund_code="1110",
        amount_cents=1000_00,
        counterpart="Cliente Mayorista",
        reason="Pago",
        created_by="alan",
    )

    # 1. Primera reversión parcial por 300 pesos
    rev1 = reverseMovement(
        conn=conn_lab,
        movement_id=entry["movement_id"],
        user_id="alan",
        reason="Devolución parcial de mercadería",
        partial_amount_cents=300_00,
    )
    assert rev1["reversed_amount_cents"] == 300_00
    assert rev1["total_reversed_cents"] == 300_00
    assert obtener_fondo_por_codigo(conn_lab, "1110")["saldo_centavos"] == 700_00

    # 2. Segunda reversión parcial por 400 pesos
    rev2 = reverseMovement(
        conn=conn_lab,
        movement_id=entry["movement_id"],
        user_id="alan",
        reason="Descuento por falla de lote",
        partial_amount_cents=400_00,
    )
    assert rev2["reversed_amount_cents"] == 400_00
    assert rev2["total_reversed_cents"] == 700_00
    assert obtener_fondo_por_codigo(conn_lab, "1110")["saldo_centavos"] == 300_00

    # 3. Intentar revertir 500 pesos (excede el saldo restante de 300_00) -> DEBE FALLAR
    with pytest.raises(ErrorReversion, match="excede el saldo reversible disponible"):
        reverseMovement(
            conn=conn_lab,
            movement_id=entry["movement_id"],
            user_id="alan",
            reason="Exceso",
            partial_amount_cents=500_00,
        )

    # 4. Revertir el remanente exacto de 300 pesos
    rev3 = reverseMovement(
        conn=conn_lab,
        movement_id=entry["movement_id"],
        user_id="alan",
        reason="Cancelación del saldo restante",
        partial_amount_cents=300_00,
    )
    assert rev3["total_reversed_cents"] == 1000_00
    assert obtener_fondo_por_codigo(conn_lab, "1110")["saldo_centavos"] == 0

    # 5. Intentar revertir de nuevo cuando ya está en cero reversible -> DEBE FALLAR
    with pytest.raises(ErrorReversion, match="ya fue totalmente revertido"):
        reverseMovement(
            conn=conn_lab,
            movement_id=entry["movement_id"],
            user_id="alan",
            reason="Intento posterior",
        )


def test_rev_004_reverse_confirmed_transfer(conn_lab):
    """REV-004: Reversión de traslado confirmado restituye los saldos entre origen y destino."""
    registerEntry(conn_lab, fund_code="1110", amount_cents=1000_00, counterpart="Fondeo", reason="Fondeo", created_by="alan")

    transfer = transferFunds(
        conn_lab,
        origin_fund_code="1110",
        destination_fund_code="1130",
        amount_cents=400_00,
        dispatched_by="alan",
        reason="Pase a Tesoro",
    )
    confirmTransferReceipt(conn_lab, transfer_id=transfer["transfer_id"], received_by="tesorero")

    assert obtener_fondo_por_codigo(conn_lab, "1110")["saldo_centavos"] == 600_00
    assert obtener_fondo_por_codigo(conn_lab, "1130")["saldo_centavos"] == 400_00

    # Revertir traslado
    rev = reverseTransfer(
        conn=conn_lab,
        transfer_id=transfer["transfer_id"],
        user_id="auditor",
        reason="Error de pase a tesoro: se requería en caja",
    )
    assert rev["status"] == "COMPLETED"
    assert obtener_fondo_por_codigo(conn_lab, "1110")["saldo_centavos"] == 1000_00
    assert obtener_fondo_por_codigo(conn_lab, "1130")["saldo_centavos"] == 0


def test_rev_005_reverse_reservation_operation_preserves_invariant(conn_lab):
    """REV-005: Reversión de aplicación de reserva restituye el saldo pendiente conservando el invariante."""
    registerEntry(conn_lab, fund_code="1130", amount_cents=5000_00, counterpart="Fondeo", reason="Fondeo", created_by="alan")

    res = createReservation(
        conn_lab,
        fund_code="1130",
        amount_cents=2000_00,
        reason="Reserva Alquiler",
        beneficiary="Inmobiliaria",
        created_by="alan",
    )
    app = applyReservation(conn_lab, reservation_id=res["reservation_id"], amount_cents=800_00, performed_by="alan", reason="Pago parcial")

    # Invariante post-aplicación: original 2000 = applied 800 + released 0 + pending 1200
    # Obtenemos la operación id de la aplicación
    cursor = conn_lab.cursor()
    cursor.execute(
        "SELECT id FROM tesoreria_reserva_movimientos WHERE reservation_id = ? AND action_type = 'APPLY'",
        (res["reservation_id"],),
    )
    op_id = cursor.fetchone()[0]

    # Revertir la aplicación de 800 pesos
    rev = reverseReservationOperation(
        conn=conn_lab,
        reservation_id=res["reservation_id"],
        operation_id=op_id,
        user_id="supervisor",
        reason="Pago de alquiler rechazado por banco",
    )

    assert rev["new_pending_amount_cents"] == 2000_00
    assert rev["new_status"] == "ACTIVE"

    # Invariante comprobada en BD
    cursor.execute(
        "SELECT original_amount_cents, applied_amount_cents, released_amount_cents, pending_amount_cents FROM tesoreria_reservas WHERE reservation_id = ?",
        (res["reservation_id"],),
    )
    r_bd = cursor.fetchone()
    assert r_bd["original_amount_cents"] == (r_bd["applied_amount_cents"] + r_bd["released_amount_cents"] + r_bd["pending_amount_cents"])


def test_rev_idempotencia_duplicada_no_duplica_efecto(conn_lab):
    """Reversión duplicada con la misma clave de idempotencia no duplica la salida de fondos."""
    entry = registerEntry(conn_lab, fund_code="1110", amount_cents=1000_00, counterpart="Venta", reason="Fondeo", created_by="alan")

    rev1 = reverseMovement(
        conn=conn_lab,
        movement_id=entry["movement_id"],
        user_id="alan",
        reason="Error",
        idempotency_key="REV_IDEMP_TEST_1",
        partial_amount_cents=400_00,
    )
    f_post_rev1 = obtener_fondo_por_codigo(conn_lab, "1110")["saldo_centavos"]
    assert f_post_rev1 == 600_00

    # Reintento idéntico con la misma clave
    rev2 = reverseMovement(
        conn=conn_lab,
        movement_id=entry["movement_id"],
        user_id="alan",
        reason="Error",
        idempotency_key="REV_IDEMP_TEST_1",
        partial_amount_cents=400_00,
    )
    assert rev2["reversal_id"] == rev1["reversal_id"]

    # El saldo del fondo sigue siendo 600_00, no 200_00
    f_post_rev2 = obtener_fondo_por_codigo(conn_lab, "1110")["saldo_centavos"]
    assert f_post_rev2 == 600_00


def test_rev_inmutabilidad_triggers_reversiones(conn_lab):
    """Trigger de base de datos prohíbe eliminar registros de la tabla tesoreria_reversiones."""
    entry = registerEntry(conn_lab, fund_code="1110", amount_cents=1000_00, counterpart="Venta", reason="Fondeo", created_by="alan")
    rev = reverseMovement(conn_lab, movement_id=entry["movement_id"], user_id="alan", reason="Error")

    cursor = conn_lab.cursor()
    with pytest.raises(sqlite3.IntegrityError, match="No está permitido eliminar registros de reversiones"):
        cursor.execute("DELETE FROM tesoreria_reversiones WHERE reversal_id = ?", (rev["reversal_id"],))
