"""Pruebas de Reservas de Fondos — LAB-007 (FLOW-035 a FLOW-038).

Verificación de reservas preventivas de fondos:
- FLOW-035: createReservation crea reserva activa sin mover efectivo ni generar movimientos.
- FLOW-036: Cumplimiento estricto del invariante original = applied + released + pending.
- FLOW-037: applyReservation aplica parcial y totalmente con auditoría.
- FLOW-038: releaseReservation libera parcial y totalmente retornando fondos a disponible.
- Protección anti-doble reserva de una misma obligación.
- Rechazo de reserva que exceda saldo disponible.
- getReservationHistory completo.
"""

import pytest

from tesoreria.isolation import conectar_db_laboratorio
from tesoreria.schema import inicializar_esquema_laboratorio
from tesoreria.catalog import sembrar_catalogo_canónico, obtener_fondo_por_codigo
from tesoreria.jornadas import abrir_jornada
from tesoreria.movements import registerEntry
from tesoreria.reservations import (
    ErrorReserva,
    applyReservation,
    createReservation,
    getReservationHistory,
    listar_reservas,
    releaseReservation,
)
from tesoreria.availability import getFundBalance


@pytest.fixture
def conn_lab(tmp_path):
    """Crea una base SQLite con catálogo, jornada abierta y $50.000 en 1130 (Tesoro)."""
    db_file = tmp_path / "tesoreria_reservations_test.db"
    conn = conectar_db_laboratorio(str(db_file))
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)
    abrir_jornada(conn, usuario="alan", observaciones="Jornada para pruebas LAB-007")
    registerEntry(
        conn=conn,
        fund_code="1130",
        amount_cents=50000_00,  # $50.000
        counterpart="Dotación Tesoro",
        reason="Fondo de contingencia",
        created_by="alan",
    )
    yield conn
    conn.close()


def test_flow_035_create_reservation_sin_mover_efectivo(conn_lab):
    """FLOW-035: createReservation no mueve efectivo (saldo_centavos permanece idéntico)."""
    fondo_antes = obtener_fondo_por_codigo(conn_lab, "1130")
    assert fondo_antes["saldo_centavos"] == 50000_00

    res = createReservation(
        conn=conn_lab,
        fund_code="1130",
        amount_cents=20000_00,
        reason="Reserva pago flete mensual",
        beneficiary="Logística Jujuy SRL",
        created_by="alan",
        obligation_id="OBL-2026-FLETE-01",
        idempotency_key="idemp_res_001",
    )

    assert res["status"] == "ACTIVE"
    assert res["original_amount_cents"] == 20000_00
    assert res["pending_amount_cents"] == 20000_00
    assert res["applied_amount_cents"] == 0
    assert res["released_amount_cents"] == 0

    # REGLA FUNDAMENTAL: El saldo registrado en tesoreria_fondos NO se modifica!
    fondo_despues = obtener_fondo_por_codigo(conn_lab, "1130")
    assert fondo_despues["saldo_centavos"] == 50000_00

    # Pero el saldo DISPONIBLE ahora es 30.000
    balance = getFundBalance(conn_lab, "1130")
    assert balance["saldo_registrado_cents"] == 50000_00
    assert balance["saldo_reservado_cents"] == 20000_00
    assert balance["saldo_disponible_cents"] == 30000_00


def test_flow_036_invariante_reserva(conn_lab):
    """FLOW-036: Invariante original = applied + released + pending en todo momento."""
    res = createReservation(
        conn=conn_lab,
        fund_code="1130",
        amount_cents=10000_00,
        reason="Mantenimiento preventivo aire",
        beneficiary="Técnico Refrigeración",
        created_by="alan",
    )
    rid = res["reservation_id"]

    # Paso 1: Original
    h1 = getReservationHistory(conn_lab, rid)
    assert h1["original_amount_cents"] == h1["applied_amount_cents"] + h1["released_amount_cents"] + h1["pending_amount_cents"]

    # Paso 2: Aplicación parcial $4.000
    applyReservation(conn_lab, reservation_id=rid, amount_cents=4000_00, performed_by="alan", reason="Anticipo repuesto")
    h2 = getReservationHistory(conn_lab, rid)
    assert h2["applied_amount_cents"] == 4000_00
    assert h2["pending_amount_cents"] == 6000_00
    assert h2["original_amount_cents"] == h2["applied_amount_cents"] + h2["released_amount_cents"] + h2["pending_amount_cents"]

    # Paso 3: Liberación parcial $2.000
    releaseReservation(conn_lab, reservation_id=rid, amount_cents=2000_00, performed_by="alan", reason="Sobrante presupuesto")
    h3 = getReservationHistory(conn_lab, rid)
    assert h3["applied_amount_cents"] == 4000_00
    assert h3["released_amount_cents"] == 2000_00
    assert h3["pending_amount_cents"] == 4000_00
    assert h3["original_amount_cents"] == h3["applied_amount_cents"] + h3["released_amount_cents"] + h3["pending_amount_cents"]


def test_flow_037_apply_reservation_total(conn_lab):
    """FLOW-037: applyReservation hasta consumir el 100% pasa a FULLY_APPLIED."""
    res = createReservation(
        conn=conn_lab,
        fund_code="1130",
        amount_cents=5000_00,
        reason="Pago seguro local",
        beneficiary="La Caja",
        created_by="alan",
    )
    rid = res["reservation_id"]

    res_app = applyReservation(conn_lab, reservation_id=rid, amount_cents=5000_00, performed_by="alan", reason="Pago póliza")
    assert res_app["status"] == "FULLY_APPLIED"
    assert res_app["pending_amount_cents"] == 0

    # No se puede aplicar más
    with pytest.raises(ErrorReserva, match="No se puede aplicar"):
        applyReservation(conn_lab, reservation_id=rid, amount_cents=100, performed_by="alan", reason="Exceso")


def test_flow_038_release_reservation_total(conn_lab):
    """FLOW-038: releaseReservation total retorna el estado a RELEASED y fondos a disponible."""
    res = createReservation(
        conn=conn_lab,
        fund_code="1130",
        amount_cents=7000_00,
        reason="Reserva flete cancelado",
        beneficiary="Transporte",
        created_by="alan",
    )
    rid = res["reservation_id"]

    res_rel = releaseReservation(conn_lab, reservation_id=rid, amount_cents=7000_00, performed_by="alan", reason="Flete bonificado")
    assert res_rel["status"] == "RELEASED"
    assert res_rel["pending_amount_cents"] == 0

    balance = getFundBalance(conn_lab, "1130")
    # Saldo reservado vuelve a 0
    assert balance["saldo_reservado_cents"] == 0
    assert balance["saldo_disponible_cents"] == balance["saldo_registrado_cents"]


def test_proteccion_anti_doble_reserva_obligacion(conn_lab):
    """Verifica que no se permita duplicar reserva sobre una misma obligación activa."""
    createReservation(
        conn=conn_lab,
        fund_code="1130",
        amount_cents=10000_00,
        reason="Reserva factura 401",
        beneficiary="Proveedor Pan",
        obligation_id="FACT-PAN-401",
        created_by="alan",
    )

    with pytest.raises(ErrorReserva, match="Doble reserva rechazada"):
        createReservation(
            conn=conn_lab,
            fund_code="1130",
            amount_cents=10000_00,
            reason="Intento duplicado de reserva",
            beneficiary="Proveedor Pan",
            obligation_id="FACT-PAN-401",
            created_by="alan",
        )


def test_rechazo_reserva_excede_disponible(conn_lab):
    """Verifica que una reserva no pueda crearse si excede el saldo disponible actual."""
    # Fondo 1130 tiene 50.000 disponibles
    with pytest.raises(ErrorReserva, match="Saldo disponible insuficiente"):
        createReservation(
            conn=conn_lab,
            fund_code="1130",
            amount_cents=60000_00,  # Excede disponible
            reason="Exceso",
            beneficiary="Nadie",
            created_by="alan",
        )
