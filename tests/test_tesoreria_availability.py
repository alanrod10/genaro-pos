"""Pruebas de Disponibilidad y Reportes — LAB-008 (FLOW-039 a FLOW-040).

Verificación de cálculo contable y disponibilidad:
- FLOW-039: getFundBalance y getFundAvailability reflejan con exactitud registrado, reservado y disponible.
- FLOW-040: getTreasurySummary separa fondos propios de administrados/custodia y dinero en tránsito.
"""

import pytest

from tesoreria.isolation import conectar_db_laboratorio
from tesoreria.schema import inicializar_esquema_laboratorio
from tesoreria.catalog import sembrar_catalogo_canónico
from tesoreria.jornadas import abrir_jornada
from tesoreria.movements import registerEntry
from tesoreria.transfers import transferFunds
from tesoreria.reservations import createReservation
from tesoreria.availability import getFundAvailability, getFundBalance, getTreasurySummary


@pytest.fixture
def conn_lab(tmp_path):
    """Crea una base SQLite con catálogo y jornada abierta."""
    db_file = tmp_path / "tesoreria_avail_test.db"
    conn = conectar_db_laboratorio(str(db_file))
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)
    abrir_jornada(conn, usuario="alan")
    yield conn
    conn.close()


def test_flow_039_fund_balance_and_availability(conn_lab):
    """FLOW-039: getFundBalance y getFundAvailability calculan registrado, reservado y disponible."""
    # 1. Dotar fondo 1110 con $80.000
    registerEntry(
        conn=conn_lab,
        fund_code="1110",
        amount_cents=80000_00,
        counterpart="Aporte capital",
        reason="Dotación",
        created_by="alan",
    )

    # 2. Reservar $25.000 de 1110
    createReservation(
        conn=conn_lab,
        fund_code="1110",
        amount_cents=25000_00,
        reason="Reserva alquiler",
        beneficiary="Inmobiliaria",
        created_by="alan",
    )

    b = getFundBalance(conn_lab, "1110")
    assert b["saldo_registrado_cents"] == 80000_00
    assert b["saldo_reservado_cents"] == 25000_00
    assert b["saldo_disponible_cents"] == 55000_00
    assert b["cantidad_reservas_activas"] == 1

    avail = getFundAvailability(conn_lab, "1110")
    assert len(avail["reservas_afectando_disponibilidad"]) == 1
    assert avail["reservas_afectando_disponibilidad"][0]["pending_amount_cents"] == 25000_00


def test_flow_040_treasury_summary_separacion_titularidad_y_transito(conn_lab):
    """FLOW-040: getTreasurySummary separa titularidad PROPIA de CUSTODIA y no duplica dinero en tránsito."""
    # 1. Ingreso en fondo propio 1110: $100.000
    registerEntry(
        conn=conn_lab,
        fund_code="1110",
        amount_cents=100000_00,
        counterpart="Venta propia",
        reason="Caja",
        created_by="alan",
    )

    # 2. Ingreso en fondo custodia 1150 (Servicios): $40.000
    registerEntry(
        conn=conn_lab,
        fund_code="1150",
        amount_cents=40000_00,
        counterpart="Recaudación servicios terceros",
        reason="Cobranza",
        created_by="alan",
    )

    # 3. Trasladar $30.000 de 1110 a 1120 (queda PENDING_RECEIPT en tránsito)
    transferFunds(
        conn=conn_lab,
        origin_fund_code="1110",
        destination_fund_code="1120",
        amount_cents=30000_00,
        dispatched_by="alan",
        reason="Traslado en tránsito",
    )

    summary = getTreasurySummary(conn_lab)

    # Fondos propios:
    # 1110: 100.000 - 30.000 = 70.000 registrado y disponible
    # 1120: 0 (aún no confirmó)
    # Total propio registrado en cuentas = 70.000
    assert summary["fondos_propios"]["total_registrado_cents"] == 70000_00
    assert summary["fondos_propios"]["total_disponible_cents"] == 70000_00

    # Fondos administrados / custodia:
    # 1150: 40.000 registrado
    assert summary["fondos_administrados_custodia"]["total_registrado_cents"] == 40000_00

    # Dinero en tránsito: 30.000
    assert summary["dinero_en_transito"]["amount_cents"] == 30000_00
    assert summary["dinero_en_transito"]["traslados_pendientes_count"] == 1

    # Total consolidado global: 70.000 (propio en cuenta) + 40.000 (custodia) + 30.000 (en tránsito) = 140.000
    # Exactamente los 100.000 + 40.000 ingresados originalmente, sin perder ni duplicar un solo centavo!
    assert summary["total_consolidado"]["total_registrado_cents"] == 140000_00
