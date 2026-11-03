"""
Pruebas exhaustivas de atomicidad, fallos inducidos e idempotencia del barrido 1110.
Valida el cumplimiento estricto del requerimiento:
1. Fallos inducidos en cada una de las 8 etapas con rollback total verificable mediante triggers nativos de SQLite y mocks de eventos.
2. Reintento exitoso tras fallo inducido.
3. Barrido exitoso con saldo exacto, faltante y sobrante.
4. Idempotencia y detección de barridos parciales históricos.
5. Protección contra sobregiros en traslados ordinarios.
6. Cierres posteriores e inmutabilidad de producción.
"""

from datetime import datetime, timezone
import hashlib
import sqlite3
import pytest
from unittest.mock import patch

from tesoreria.schema import inicializar_esquema_laboratorio
from tesoreria.catalog import sembrar_catalogo_canónico, obtener_fondo_por_codigo
from tesoreria.jornadas import abrir_jornada, obtener_jornada_actual
from tesoreria.movements import registerEntry
from tesoreria.reconciliations import reconcilePhysicalFund
from tesoreria.reservations import createReservation
from tesoreria.transfers import transferFunds, ErrorTraslado
from tesoreria.closures import (
    beginClosure,
    validateClosure,
    executeCashDrawerSweep,
    confirmClosure,
    ErrorCierre,
)
import tesoreria.closures as closures_mod


@pytest.fixture
def conn_sweep():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)
    abrir_jornada(conn, usuario="alan")
    yield conn
    conn.close()


def _setup_jornada_con_fondos_y_reserva(conn, saldo_cents=600_000, contado_cents=580_000, nota="Faltante"):
    """Configura 1110 con fondos, reserva activa y arqueo físico."""
    registerEntry(conn, fund_code="1110", amount_cents=saldo_cents, counterpart="Venta", reason="Fondeo inicial", created_by="alan")
    rsv = createReservation(
        conn,
        fund_code="1110",
        amount_cents=50_000,
        reason="Reserva logística",
        beneficiary="Flete Express",
        created_by="alan",
    )
    j = obtener_jornada_actual(conn)
    arq = reconcilePhysicalFund(
        conn,
        fund_code="1110",
        verified_amount_cents=contado_cents,
        user_id="alan",
        notes=nota,
    )
    beginClosure(conn, journal_id=j["id"], user_id="alan")
    return j, arq, rsv


def _assert_estado_inicial_preservado(conn, j_id, saldo_inicial_1110=600_000):
    """Verifica los invariantes de rollback completo: nada parcial queda registrado."""
    f1110 = obtener_fondo_por_codigo(conn, "1110")
    f1120 = obtener_fondo_por_codigo(conn, "1120")
    assert f1110["saldo_centavos"] == saldo_inicial_1110, f"Saldo 1110 alterado: {f1110['saldo_centavos']}"
    assert f1120["saldo_centavos"] == 0, f"Saldo 1120 alterado: {f1120['saldo_centavos']}"

    c = conn.cursor()
    c.execute(
        "SELECT COUNT(*) FROM tesoreria_movimientos WHERE journal_id = ? AND movement_type IN ('TRANSFER_OUT', 'TRANSFER_IN')",
        (j_id,),
    )
    assert c.fetchone()[0] == 0

    c.execute(
        "SELECT COUNT(*) FROM tesoreria_traslados WHERE journal_id = ? AND evidence_reference = 'BARRIDO_CIERRE_DIARIO'",
        (j_id,),
    )
    assert c.fetchone()[0] == 0

    c.execute(
        """
        SELECT COUNT(*) FROM tesoreria_auditoria_eventos 
        WHERE aggregate_id = ? AND event_type IN ('RECONCILIATION_BALANCE_CUTOFF', 'CASH_DRAWER_SWEEP_EXECUTED')
        """,
        (str(j_id),),
    )
    assert c.fetchone()[0] == 0

    c.execute("SELECT fund_id FROM tesoreria_reservas WHERE status = 'ACTIVE'")
    for r in c.fetchall():
        assert r[0] == f1110["id"]
    c.close()


# =====================================================================
# 8 PRUEBAS OBLIGATORIAS DE FALLOS INDUCIDOS
# =====================================================================

def test_fallo_inducido_01_al_debitar_1110(conn_sweep):
    """Fallo 1: Error inducido al debitar fondo 1110 durante el despacho."""
    j, arq, rsv = _setup_jornada_con_fondos_y_reserva(conn_sweep)
    f1110 = obtener_fondo_por_codigo(conn_sweep, "1110")

    conn_sweep.execute(
        f"""
        CREATE TRIGGER trg_fail_debit_1110
        BEFORE UPDATE OF saldo_centavos ON tesoreria_fondos
        WHEN NEW.id = {f1110['id']} AND NEW.saldo_centavos > 0
        BEGIN
            SELECT RAISE(ABORT, 'FALLO_INDUCIDO_ETAPA_1_DEBITAR_1110');
        END;
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="FALLO_INDUCIDO_ETAPA_1_DEBITAR_1110"):
        executeCashDrawerSweep(conn_sweep, journal_id=j["id"], user_id="alan")

    conn_sweep.execute("DROP TRIGGER trg_fail_debit_1110;")
    _assert_estado_inicial_preservado(conn_sweep, j["id"])

    # Reintento tras fallo: éxito completo y atómico
    sweep = executeCashDrawerSweep(conn_sweep, journal_id=j["id"], user_id="alan")
    assert sweep["swept"] is True
    assert sweep["amount_cents"] == 580_000
    assert obtener_fondo_por_codigo(conn_sweep, "1110")["saldo_centavos"] == 0
    assert obtener_fondo_por_codigo(conn_sweep, "1120")["saldo_centavos"] == 580_000


def test_fallo_inducido_02_al_acreditar_1120(conn_sweep):
    """Fallo 2: Error inducido al acreditar fondo 1120 durante la recepción."""
    j, arq, rsv = _setup_jornada_con_fondos_y_reserva(conn_sweep)
    f1120 = obtener_fondo_por_codigo(conn_sweep, "1120")

    conn_sweep.execute(
        f"""
        CREATE TRIGGER trg_fail_credit_1120
        BEFORE UPDATE OF saldo_centavos ON tesoreria_fondos
        WHEN NEW.id = {f1120['id']}
        BEGIN
            SELECT RAISE(ABORT, 'FALLO_INDUCIDO_ETAPA_2_ACREDITAR_1120');
        END;
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="FALLO_INDUCIDO_ETAPA_2_ACREDITAR_1120"):
        executeCashDrawerSweep(conn_sweep, journal_id=j["id"], user_id="alan")

    conn_sweep.execute("DROP TRIGGER trg_fail_credit_1120;")
    _assert_estado_inicial_preservado(conn_sweep, j["id"])

    sweep = executeCashDrawerSweep(conn_sweep, journal_id=j["id"], user_id="alan")
    assert sweep["swept"] is True
    assert obtener_fondo_por_codigo(conn_sweep, "1110")["saldo_centavos"] == 0
    assert obtener_fondo_por_codigo(conn_sweep, "1120")["saldo_centavos"] == 580_000


def test_fallo_inducido_03_al_insertar_transfer_out(conn_sweep):
    """Fallo 3: Error inducido al insertar movimiento TRANSFER_OUT."""
    j, arq, rsv = _setup_jornada_con_fondos_y_reserva(conn_sweep)

    conn_sweep.execute(
        """
        CREATE TRIGGER trg_fail_transfer_out
        BEFORE INSERT ON tesoreria_movimientos
        WHEN NEW.movement_type = 'TRANSFER_OUT'
        BEGIN
            SELECT RAISE(ABORT, 'FALLO_INDUCIDO_ETAPA_3_TRANSFER_OUT');
        END;
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="FALLO_INDUCIDO_ETAPA_3_TRANSFER_OUT"):
        executeCashDrawerSweep(conn_sweep, journal_id=j["id"], user_id="alan")

    conn_sweep.execute("DROP TRIGGER trg_fail_transfer_out;")
    _assert_estado_inicial_preservado(conn_sweep, j["id"])

    sweep = executeCashDrawerSweep(conn_sweep, journal_id=j["id"], user_id="alan")
    assert sweep["swept"] is True
    assert obtener_fondo_por_codigo(conn_sweep, "1110")["saldo_centavos"] == 0
    assert obtener_fondo_por_codigo(conn_sweep, "1120")["saldo_centavos"] == 580_000


def test_fallo_inducido_04_al_insertar_transfer_in(conn_sweep):
    """Fallo 4: Error inducido al insertar movimiento TRANSFER_IN."""
    j, arq, rsv = _setup_jornada_con_fondos_y_reserva(conn_sweep)

    conn_sweep.execute(
        """
        CREATE TRIGGER trg_fail_transfer_in
        BEFORE INSERT ON tesoreria_movimientos
        WHEN NEW.movement_type = 'TRANSFER_IN'
        BEGIN
            SELECT RAISE(ABORT, 'FALLO_INDUCIDO_ETAPA_4_TRANSFER_IN');
        END;
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="FALLO_INDUCIDO_ETAPA_4_TRANSFER_IN"):
        executeCashDrawerSweep(conn_sweep, journal_id=j["id"], user_id="alan")

    conn_sweep.execute("DROP TRIGGER trg_fail_transfer_in;")
    _assert_estado_inicial_preservado(conn_sweep, j["id"])

    sweep = executeCashDrawerSweep(conn_sweep, journal_id=j["id"], user_id="alan")
    assert sweep["swept"] is True
    assert obtener_fondo_por_codigo(conn_sweep, "1110")["saldo_centavos"] == 0
    assert obtener_fondo_por_codigo(conn_sweep, "1120")["saldo_centavos"] == 580_000


def test_fallo_inducido_05_al_ejecutar_corte_1110(conn_sweep):
    """Fallo 5: Error inducido al ejecutar el UPDATE de corte a cero sobre 1110."""
    j, arq, rsv = _setup_jornada_con_fondos_y_reserva(conn_sweep)
    f1110 = obtener_fondo_por_codigo(conn_sweep, "1110")

    conn_sweep.execute(
        f"""
        CREATE TRIGGER trg_fail_corte_1110
        BEFORE UPDATE OF saldo_centavos ON tesoreria_fondos
        WHEN NEW.id = {f1110['id']} AND NEW.saldo_centavos = 0
        BEGIN
            SELECT RAISE(ABORT, 'FALLO_INDUCIDO_ETAPA_5_CORTE_1110');
        END;
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="FALLO_INDUCIDO_ETAPA_5_CORTE_1110"):
        executeCashDrawerSweep(conn_sweep, journal_id=j["id"], user_id="alan")

    conn_sweep.execute("DROP TRIGGER trg_fail_corte_1110;")
    _assert_estado_inicial_preservado(conn_sweep, j["id"])

    # Reintento exitoso: no se bloquea por ALREADY_EXECUTED porque no hubo commit parcial
    sweep = executeCashDrawerSweep(conn_sweep, journal_id=j["id"], user_id="alan")
    assert sweep["swept"] is True
    assert obtener_fondo_por_codigo(conn_sweep, "1110")["saldo_centavos"] == 0
    assert obtener_fondo_por_codigo(conn_sweep, "1120")["saldo_centavos"] == 580_000


def test_fallo_inducido_06_al_registrar_reconciliation_balance_cutoff(conn_sweep):
    """Fallo 6: Error inducido al registrar el evento canónico RECONCILIATION_BALANCE_CUTOFF."""
    j, arq, rsv = _setup_jornada_con_fondos_y_reserva(conn_sweep)

    original_record = closures_mod.recordAuditEvent

    def mock_record(conn, event_type, *args, **kwargs):
        if event_type == "RECONCILIATION_BALANCE_CUTOFF":
            raise RuntimeError("FALLO_INDUCIDO_ETAPA_6_AUDIT_CUTOFF")
        return original_record(conn, event_type, *args, **kwargs)

    with patch.object(closures_mod, "recordAuditEvent", side_effect=mock_record):
        with pytest.raises(RuntimeError, match="FALLO_INDUCIDO_ETAPA_6_AUDIT_CUTOFF"):
            executeCashDrawerSweep(conn_sweep, journal_id=j["id"], user_id="alan")

    _assert_estado_inicial_preservado(conn_sweep, j["id"])

    sweep = executeCashDrawerSweep(conn_sweep, journal_id=j["id"], user_id="alan")
    assert sweep["swept"] is True
    assert obtener_fondo_por_codigo(conn_sweep, "1110")["saldo_centavos"] == 0
    assert obtener_fondo_por_codigo(conn_sweep, "1120")["saldo_centavos"] == 580_000


def test_fallo_inducido_07_al_registrar_cash_drawer_sweep_executed(conn_sweep):
    """Fallo 7: Error inducido al registrar el evento canónico CASH_DRAWER_SWEEP_EXECUTED."""
    j, arq, rsv = _setup_jornada_con_fondos_y_reserva(conn_sweep)

    original_record = closures_mod.recordAuditEvent

    def mock_record(conn, event_type, *args, **kwargs):
        if event_type == "CASH_DRAWER_SWEEP_EXECUTED":
            raise RuntimeError("FALLO_INDUCIDO_ETAPA_7_AUDIT_SWEEP")
        return original_record(conn, event_type, *args, **kwargs)

    with patch.object(closures_mod, "recordAuditEvent", side_effect=mock_record):
        with pytest.raises(RuntimeError, match="FALLO_INDUCIDO_ETAPA_7_AUDIT_SWEEP"):
            executeCashDrawerSweep(conn_sweep, journal_id=j["id"], user_id="alan")

    _assert_estado_inicial_preservado(conn_sweep, j["id"])

    sweep = executeCashDrawerSweep(conn_sweep, journal_id=j["id"], user_id="alan")
    assert sweep["swept"] is True
    assert obtener_fondo_por_codigo(conn_sweep, "1110")["saldo_centavos"] == 0
    assert obtener_fondo_por_codigo(conn_sweep, "1120")["saldo_centavos"] == 580_000


def test_fallo_inducido_08_al_actualizar_reservas(conn_sweep):
    """Fallo 8: Error inducido al re-anclar las reservas activas hacia 1120."""
    j, arq, rsv = _setup_jornada_con_fondos_y_reserva(conn_sweep)

    conn_sweep.execute(
        """
        CREATE TRIGGER trg_fail_update_reservas
        BEFORE UPDATE OF fund_id ON tesoreria_reservas
        BEGIN
            SELECT RAISE(ABORT, 'FALLO_INDUCIDO_ETAPA_8_UPDATE_RESERVAS');
        END;
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="FALLO_INDUCIDO_ETAPA_8_UPDATE_RESERVAS"):
        executeCashDrawerSweep(conn_sweep, journal_id=j["id"], user_id="alan")

    conn_sweep.execute("DROP TRIGGER trg_fail_update_reservas;")
    _assert_estado_inicial_preservado(conn_sweep, j["id"])

    sweep = executeCashDrawerSweep(conn_sweep, journal_id=j["id"], user_id="alan")
    assert sweep["swept"] is True
    assert obtener_fondo_por_codigo(conn_sweep, "1110")["saldo_centavos"] == 0
    assert obtener_fondo_por_codigo(conn_sweep, "1120")["saldo_centavos"] == 580_000


# =====================================================================
# CASOS COMPLEMENTARIOS REQUERIDOS
# =====================================================================

def test_barrido_exitoso_saldo_exacto(conn_sweep):
    """Verifica barrido exitoso cuando recuento físico y teórico coinciden."""
    registerEntry(conn_sweep, fund_code="1110", amount_cents=600_000, counterpart="Venta", reason="Fondeo", created_by="alan")
    j = obtener_jornada_actual(conn_sweep)
    reconcilePhysicalFund(conn_sweep, fund_code="1110", verified_amount_cents=600_000, user_id="alan", notes="Conforme")
    beginClosure(conn_sweep, journal_id=j["id"], user_id="alan")

    sweep = executeCashDrawerSweep(conn_sweep, journal_id=j["id"], user_id="alan")
    assert sweep["swept"] is True
    assert sweep["amount_cents"] == 600_000
    assert obtener_fondo_por_codigo(conn_sweep, "1110")["saldo_centavos"] == 0
    assert obtener_fondo_por_codigo(conn_sweep, "1120")["saldo_centavos"] == 600_000

    # No se emite evento CUTOFF porque no hubo diferencia
    c = conn_sweep.cursor()
    c.execute(
        "SELECT COUNT(*) FROM tesoreria_auditoria_eventos WHERE aggregate_id = ? AND event_type = 'RECONCILIATION_BALANCE_CUTOFF'",
        (str(j["id"]),),
    )
    assert c.fetchone()[0] == 0
    # Sí se emite el evento canónico de sweep
    c.execute(
        "SELECT COUNT(*) FROM tesoreria_auditoria_eventos WHERE aggregate_id = ? AND event_type = 'CASH_DRAWER_SWEEP_EXECUTED'",
        (sweep["transfer_id"],),
    )
    assert c.fetchone()[0] == 1
    c.close()


def test_barrido_con_sobrante_y_autorizacion_arqueo(conn_sweep):
    """Verifica barrido con sobrante documentado: se traslada el efectivo contado físico completo ($6.200)."""
    registerEntry(conn_sweep, fund_code="1110", amount_cents=600_000, counterpart="Venta", reason="Fondeo", created_by="alan")
    j = obtener_jornada_actual(conn_sweep)
    reconcilePhysicalFund(conn_sweep, fund_code="1110", verified_amount_cents=620_000, user_id="yilda", notes="Sobrante cliente")
    beginClosure(conn_sweep, journal_id=j["id"], user_id="yilda")

    sweep = executeCashDrawerSweep(conn_sweep, journal_id=j["id"], user_id="yilda")
    assert sweep["swept"] is True
    assert sweep["amount_cents"] == 620_000
    assert obtener_fondo_por_codigo(conn_sweep, "1110")["saldo_centavos"] == 0
    assert obtener_fondo_por_codigo(conn_sweep, "1120")["saldo_centavos"] == 620_000


def test_reintento_despues_de_operacion_completa_retorna_already_executed(conn_sweep):
    """Verifica que un reintento tras completar el barrido atómico retorne ALREADY_EXECUTED sin duplicar."""
    registerEntry(conn_sweep, fund_code="1110", amount_cents=600_000, counterpart="Venta", reason="Fondeo", created_by="alan")
    j = obtener_jornada_actual(conn_sweep)
    reconcilePhysicalFund(conn_sweep, fund_code="1110", verified_amount_cents=580_000, user_id="alan", notes="Faltante")
    beginClosure(conn_sweep, journal_id=j["id"], user_id="alan")

    res1 = executeCashDrawerSweep(conn_sweep, journal_id=j["id"], user_id="alan")
    assert res1["swept"] is True

    # Reintento idéntico
    res2 = executeCashDrawerSweep(conn_sweep, journal_id=j["id"], user_id="alan")
    assert res2["swept"] is False
    assert res2["status"] == "ALREADY_EXECUTED"
    assert res2["transfer_id"] == res1["transfer_id"]

    # Reintento con otro usuario o motivo
    res3 = executeCashDrawerSweep(conn_sweep, journal_id=j["id"], user_id="alan", reason="Otro motivo")
    assert res3["swept"] is False
    assert res3["status"] == "ALREADY_EXECUTED"
    assert res3["transfer_id"] == res1["transfer_id"]

    # Verificar que solo hay 1 transferencia y 2 movimientos (OUT e IN)
    c = conn_sweep.cursor()
    c.execute("SELECT COUNT(*) FROM tesoreria_traslados WHERE journal_id = ?", (j["id"],))
    assert c.fetchone()[0] == 1
    c.execute("SELECT COUNT(*) FROM tesoreria_movimientos WHERE journal_id = ? AND movement_type IN ('TRANSFER_OUT', 'TRANSFER_IN')", (j["id"],))
    assert c.fetchone()[0] == 2
    c.close()


def test_deteccion_de_barrido_parcial_historico(conn_sweep):
    """Verifica que si existe un registro histórico inconsistente (traslado confirmado pero 1110 no en cero),
    se detecta y reporta sin alterar automáticamente."""
    registerEntry(conn_sweep, fund_code="1110", amount_cents=600_000, counterpart="Venta", reason="Fondeo", created_by="alan")
    j = obtener_jornada_actual(conn_sweep)
    now_utc = datetime.now(timezone.utc).isoformat()

    # Inyectar artificialmente un traslado previo como si viniera de una versión anterior incompleta
    c = conn_sweep.cursor()
    c.execute(
        """
        INSERT INTO tesoreria_traslados (
            transfer_id, idempotency_key, journal_id, origin_fund_id, destination_fund_id,
            amount_cents, currency, status, dispatched_by, received_by, dispatched_at, received_at, reason, evidence_reference
        ) VALUES ('fake-trf-01', 'key-fake', ?, 1, 2, 580000, 'ARS', 'CONFIRMED', 'alan', 'alan', ?, ?, 'Fake sweep', 'BARRIDO_CIERRE_DIARIO')
        """,
        (j["id"], now_utc, now_utc),
    )
    conn_sweep.commit()
    c.close()

    # 1110 sigue en 600.000 (no está en cero)
    with pytest.raises(ErrorCierre, match="Inconsistencia detectada: existe un registro de barrido parcial previo"):
        executeCashDrawerSweep(conn_sweep, journal_id=j["id"], user_id="alan")


def test_traslados_ordinarios_siguen_rechazando_sobregiros(conn_sweep):
    """Verifica que los traslados ordinarios siguen aplicando las validaciones de sobregiro y disponibilidad."""
    registerEntry(conn_sweep, fund_code="1110", amount_cents=100_000, counterpart="Venta", reason="Fondeo", created_by="alan")
    # Intentar trasladar más del saldo disponible hacia 1130
    with pytest.raises(ErrorTraslado, match="Saldo disponible insuficiente en origen 1110"):
        transferFunds(
            conn_sweep,
            origin_fund_code="1110",
            destination_fund_code="1130",
            amount_cents=150_000,
            dispatched_by="alan",
            reason="Traslado excesivo",
        )

    # Intentar sobregiro pasando un evidence_reference falso no autorizado
    with pytest.raises(ErrorTraslado, match="Saldo disponible insuficiente en origen 1110"):
        transferFunds(
            conn_sweep,
            origin_fund_code="1110",
            destination_fund_code="1130",
            amount_cents=150_000,
            dispatched_by="alan",
            reason="Intento de bypass",
            evidence_reference="BARRIDO_CIERRE_DIARIO",
        )


def test_barrido_sobrante_sin_arqueo_previo_es_rechazado(conn_sweep):
    """Verifica que un barrido de importe mayor al teórico (sobrante) sin arqueo que lo respalde es rechazado."""
    registerEntry(conn_sweep, fund_code="1110", amount_cents=600_000, counterpart="Venta", reason="Fondeo", created_by="alan")
    j = obtener_jornada_actual(conn_sweep)
    beginClosure(conn_sweep, journal_id=j["id"], user_id="alan")

    with pytest.raises(ErrorTraslado, match="sin un arqueo válido que verifique el recuento físico contado"):
        transferFunds(
            conn_sweep,
            origin_fund_code="1110",
            destination_fund_code="1120",
            amount_cents=650_000,
            dispatched_by="alan",
            reason="Intento sobrante sin arqueo",
            evidence_reference="BARRIDO_CIERRE_DIARIO",
        )


def test_cierre_definitivo_y_apertura_posterior(conn_sweep):
    """Verifica el flujo completo: barrido atómico, confirmación de cierre y apertura de nueva jornada limpia en 0."""
    registerEntry(conn_sweep, fund_code="1110", amount_cents=600_000, counterpart="Venta", reason="Fondeo", created_by="alan")
    j = obtener_jornada_actual(conn_sweep)
    reconcilePhysicalFund(conn_sweep, fund_code="1110", verified_amount_cents=580_000, user_id="alan", notes="Faltante")
    beginClosure(conn_sweep, journal_id=j["id"], user_id="alan")

    sweep = executeCashDrawerSweep(conn_sweep, journal_id=j["id"], user_id="alan")
    assert sweep["swept"] is True

    val = validateClosure(conn_sweep, journal_id=j["id"])
    assert val["can_close"] is True

    cierre = confirmClosure(conn_sweep, journal_id=j["id"], user_id="alan", observations="Cierre conforme")
    assert cierre["status"] == "CONFIRMED"

    j2 = abrir_jornada(conn_sweep, usuario="alan")
    assert j2["numero_jornada"] == 2
    f1110_j2 = obtener_fondo_por_codigo(conn_sweep, "1110")
    assert f1110_j2["saldo_centavos"] == 0


def test_verificacion_integridad_produccion(hashes_bases_protegidas_iniciales):
    """Verifica que la base de datos de producción permanece inalterada durante la sesión de pruebas.

    El hash de referencia se captura al inicio de la sesión (tests/conftest.py), ya que la base
    productiva evoluciona legítimamente con la operación real y las migraciones autorizadas.
    """
    prod_path = "/home/alanrod10/production/data/genaro-pos.db"
    with open(prod_path, "rb") as f:
        h = hashlib.sha256(f.read()).hexdigest()
    assert h == hashes_bases_protegidas_iniciales["produccion_comercial"]
