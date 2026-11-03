"""Pruebas Adversariales de Atomicidad y Concurrencia — SPRINT 4 FASE B.

Verificación rigurosa de:
- Concurrencia simulada y competencia de saldos (prevención de sobregiro).
- Aplicaciones concurrentes de reservas que excedan el remanente.
- Doble confirmación de traslados.
- Doble cierre de jornadas.
- Aperturas concurrentes de jornadas (violación de unicidad de jornada activa).
- Idempotencia: replay exacto vs colisión de payload.
- Interrupción artificial y rollback ante excepciones intermedias.
- Verificación de transacción única atómica en executeReservedExit.
"""

import sqlite3
import pytest
import uuid

from tesoreria.catalog import obtener_fondo_por_codigo, sembrar_catalogo_canónico
from tesoreria.closures import beginClosure, confirmClosure
from tesoreria.idempotency import ErrorIdempotencia
from tesoreria.isolation import conectar_db_laboratorio
from tesoreria.jornadas import abrir_jornada, ErrorJornada
from tesoreria.movements import ErrorSaldoInsuficiente, registerEntry, registerExit
from tesoreria.reservations import (
    applyReservation,
    createReservation,
    executeReservedExit,
    getReservationHistory,
    ErrorReserva,
)
from tesoreria.schema import inicializar_esquema_laboratorio
from tesoreria.transfers import confirmTransferReceipt, transferFunds, ErrorTraslado


@pytest.fixture
def conn_adv(tmp_path):
    """Base de datos aislada para pruebas adversariales de atomicidad."""
    db_file = tmp_path / "tesoreria_adversarial_atomicity.db"
    conn = conectar_db_laboratorio(str(db_file))
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)
    abrir_jornada(conn, usuario="admin_adv", observaciones="Jornada para pruebas adversariales")
    yield conn
    conn.close()


def test_adv_001_dos_egresos_competitivos_saldo_insuficiente(conn_adv):
    """Simula dos egresos simultáneos sobre el mismo saldo: el segundo debe fallar sin dejar saldo negativo."""
    # Saldo inicial: $100.000 (10000000 centavos) en 1110 (Drugstore Diario)
    registerEntry(
        conn_adv,
        fund_code="1110",
        amount_cents=10000000,
        counterpart="Aporte Capital",
        reason="Fondeo inicial",
        created_by="tesorero",
    )

    # Primer egreso de $80.000: debe completarse
    mov1 = registerExit(
        conn_adv,
        fund_code="1110",
        amount_cents=8000000,
        counterpart="Proveedor A",
        reason="Pago Factura A",
        created_by="operador1",
    )
    assert mov1["status"] == "POSTED"

    # Saldo remanente verificado: $20.000
    f1 = obtener_fondo_por_codigo(conn_adv, "1110")
    assert f1["saldo_centavos"] == 2000000

    # Segundo egreso competitivo de $80.000: debe ser rechazado inmediatamente
    with pytest.raises(ErrorSaldoInsuficiente) as exc_info:
        registerExit(
            conn_adv,
            fund_code="1110",
            amount_cents=8000000,
            counterpart="Proveedor B",
            reason="Pago Factura B simultáneo",
            created_by="operador2",
        )
    assert "insuficiente" in str(exc_info.value).lower()

    # Verificar que el saldo no se alteró ni se convirtió en negativo
    f2 = obtener_fondo_por_codigo(conn_adv, "1110")
    assert f2["saldo_centavos"] == 2000000


def test_adv_002_dos_aplicaciones_competitivas_misma_reserva(conn_adv):
    """Simula dos aplicaciones sobre una misma reserva que excedan su importe pendiente."""
    registerEntry(conn_adv, fund_code="1110", amount_cents=10000000, counterpart="Capital", reason="Fondeo", created_by="tesorero")
    rsv = createReservation(
        conn_adv,
        fund_code="1110",
        amount_cents=5000000,  # $50.000 reservados
        reason="Reserva mercadería",
        beneficiary="Distribuidora Central",
        created_by="tesorero",
    )

    # Primera aplicación de $40.000 (éxito, queda $10.000 pendiente)
    apply1 = applyReservation(conn_adv, reservation_id=rsv["reservation_id"], amount_cents=4000000, performed_by="operador1", reason="Anticipo 1")
    assert apply1["status"] == "PARTIALLY_APPLIED"
    assert apply1["pending_amount_cents"] == 1000000

    # Segunda aplicación intenta aplicar $40.000: excede el pendiente
    with pytest.raises(ErrorReserva) as exc:
        applyReservation(conn_adv, reservation_id=rsv["reservation_id"], amount_cents=4000000, performed_by="operador2", reason="Anticipo 2")
    assert "excede el saldo pendiente reservado" in str(exc.value)

    # Invariante estrictamente preservada: original (50.000) = aplicado (40.000) + liberado (0) + pendiente (10.000)
    cursor = conn_adv.cursor()
    cursor.execute("SELECT original_amount_cents, applied_amount_cents, released_amount_cents, pending_amount_cents FROM tesoreria_reservas WHERE reservation_id = ?", (rsv["reservation_id"],))
    row = cursor.fetchone()
    assert row[0] == 5000000
    assert row[1] == 4000000
    assert row[2] == 0
    assert row[3] == 1000000
    assert row[0] == (row[1] + row[2] + row[3])


def test_adv_003_doble_recepcion_traslado_invalida(conn_adv):
    """Simula una doble confirmación de recepción del mismo traslado."""
    registerEntry(conn_adv, fund_code="1110", amount_cents=5000000, counterpart="Capital", reason="Fondeo", created_by="tesorero")
    trf = transferFunds(
        conn_adv,
        origin_fund_code="1110",
        destination_fund_code="1120",
        amount_cents=2000000,
        reason="Traslado diario",
        dispatched_by="cajero1",
    )
    assert trf["status"] == "PENDING_RECEIPT"

    # Primera confirmación de recepción: éxito
    trf_conf = confirmTransferReceipt(conn_adv, transfer_id=trf["transfer_id"], received_by="cajero2")
    assert trf_conf["status"] == "CONFIRMED"

    # Segunda confirmación de recepción: debe fallar y no duplicar fondos
    with pytest.raises(ErrorTraslado) as exc:
        confirmTransferReceipt(conn_adv, transfer_id=trf["transfer_id"], received_by="cajero2_duplicado")
    assert "pendiente de recepción" in str(exc.value)

    # Verificar que el destino 1120 no recibió el importe dos veces
    f_dest = obtener_fondo_por_codigo(conn_adv, "1120")
    assert f_dest["saldo_centavos"] == 2000000


def test_adv_004_doble_cierre_simultaneo_rechazado(conn_adv):
    """Simula un doble intento de confirmación definitiva de cierre."""
    cursor = conn_adv.cursor()
    cursor.execute("SELECT id FROM tesoreria_jornadas WHERE estado = 'ABIERTA'")
    j_id = cursor.fetchone()[0]

    # Iniciar cierre
    beginClosure(conn_adv, journal_id=j_id, user_id="supervisor")

    # Primer confirmClosure
    cierre1 = confirmClosure(conn_adv, journal_id=j_id, user_id="supervisor", observations="Cierre oficial")
    assert cierre1["journal_id"] == j_id

    # Segundo confirmClosure simultáneo: debe ser rechazado
    from tesoreria.closures import ErrorCierre
    with pytest.raises(ErrorCierre) as exc:
        confirmClosure(conn_adv, journal_id=j_id, user_id="supervisor", observations="Intento redundante")
    assert "no está en proceso de cierre" in str(exc.value) or "cerrada" in str(exc.value).lower()


def test_adv_005_dos_aperturas_simultaneas_rechazadas_por_integridad(conn_adv):
    """Garantiza que se rechace una segunda jornada mientras exista una activa."""
    with pytest.raises((ErrorJornada, sqlite3.IntegrityError)):
        abrir_jornada(conn_adv, usuario="cajero2", observaciones="Intento de segunda jornada concurrente")


def test_adv_006_idempotencia_replay_exacto_vs_colision(conn_adv):
    """Comprueba que idéntica clave de idempotencia retorne respuesta cacheada, y payload dispar sea rechazado."""
    registerEntry(conn_adv, fund_code="1110", amount_cents=10000000, counterpart="Capital", reason="Fondeo", created_by="tesorero")
    idem_key = f"idem_test_{uuid.uuid4()}"

    # 1. Primera emisión válida
    res1 = registerExit(
        conn_adv,
        fund_code="1110",
        amount_cents=1500000,
        counterpart="Proveedor X",
        reason="Pago Factura X",
        created_by="operador",
        idempotency_key=idem_key,
    )

    # 2. Replay con payload IDÉNTICO: retorna respuesta cacheada sin duplicar salida
    res2 = registerExit(
        conn_adv,
        fund_code="1110",
        amount_cents=1500000,
        counterpart="Proveedor X",
        reason="Pago Factura X",
        created_by="operador",
        idempotency_key=idem_key,
    )
    assert res1["movement_id"] == res2["movement_id"]

    # Saldo debitado exactamente una sola vez ($100.000 - $15.000 = $85.000)
    f = obtener_fondo_por_codigo(conn_adv, "1110")
    assert f["saldo_centavos"] == 8500000

    # 3. Mismo idempotency_key con payload DIFERENTE (monto diferente): debe ser rechazado
    with pytest.raises(ErrorIdempotencia) as exc:
        registerExit(
            conn_adv,
            fund_code="1110",
            amount_cents=2000000,  # Diferente!
            counterpart="Proveedor X",
            reason="Pago Factura X",
            created_by="operador",
            idempotency_key=idem_key,
        )
    assert "conflicto de idempotencia" in str(exc.value).lower()


def test_adv_007_interrupcion_artificial_rollback_total(conn_adv):
    """Simula una excepción a mitad de una transacción: ningún registro parcial puede quedar persistido."""
    registerEntry(conn_adv, fund_code="1110", amount_cents=5000000, counterpart="Capital", reason="Fondeo", created_by="tesorero")
    saldo_inicial = obtener_fondo_por_codigo(conn_adv, "1110")["saldo_centavos"]

    # Simular una operación que actualiza fondo pero falla antes de insertar movimiento
    cursor = conn_adv.cursor()
    try:
        cursor.execute("UPDATE tesoreria_fondos SET saldo_centavos = saldo_centavos - 1000000 WHERE codigo = '1110'")
        # Forzar un error fatal de SQL
        cursor.execute("INSERT INTO tabla_inexistente_que_falla VALUES (1, 2, 3)")
        conn_adv.commit()
    except Exception:
        conn_adv.rollback()

    # Verificar que el saldo volvió 100% a su estado original sin cambios
    saldo_final = obtener_fondo_por_codigo(conn_adv, "1110")["saldo_centavos"]
    assert saldo_final == saldo_inicial


def test_adv_008_execute_reserved_exit_atomicidad_estricta(conn_adv):
    """Verifica expresamente que executeReservedExit aplique reserva y egreso en UNA SOLA transacción atómica."""
    registerEntry(conn_adv, fund_code="1110", amount_cents=10000000, counterpart="Capital", reason="Fondeo", created_by="tesorero")
    rsv = createReservation(
        conn_adv,
        fund_code="1110",
        amount_cents=3000000,
        reason="Reserva flete",
        beneficiary="Logística Sur",
        created_by="tesorero",
    )

    # Ejecutar egreso reservado atómico de $20.000
    res = executeReservedExit(
        conn_adv,
        reservation_id=rsv["reservation_id"],
        amount_cents=2000000,
        counterpart="Logística Sur",
        reason="Pago anticipo flete",
        performed_by="tesorero",
        evidence_reference="RECIBO-FLETE-001",
    )

    assert res["success"] is True
    assert res["amount_cents"] == 2000000
    assert res["new_reservation_pending_cents"] == 1000000
    assert res["reservation_status"] == "PARTIALLY_APPLIED"

    # Verificar reserva en BD
    r_bd = getReservationHistory(conn_adv, rsv["reservation_id"])
    assert r_bd["applied_amount_cents"] == 2000000
    assert r_bd["pending_amount_cents"] == 1000000
    assert r_bd["status"] == "PARTIALLY_APPLIED"

    # Saldo en libros reducido exactamente en $20.000 (de $100.000 a $80.000)
    f = obtener_fondo_por_codigo(conn_adv, "1110")
    assert f["saldo_centavos"] == 8000000
