"""Pruebas de Cierres Diarios y Protocolo de Cierre — LAB-010 (CLS-001 a CLS-006)."""

import sqlite3
import pytest
from tesoreria.catalog import obtener_fondo_por_codigo, sembrar_catalogo_canónico
from tesoreria.closures import (
    ErrorCierre,
    beginClosure,
    confirmClosure,
    executeCashDrawerSweep,
    getClosure,
    openNextJournal,
    validateClosure,
)
from tesoreria.jornadas import abrir_jornada, obtener_jornada_actual
from tesoreria.movements import ErrorMovimiento, registerEntry
from tesoreria.schema import inicializar_esquema_laboratorio
from tesoreria.transfers import transferFunds


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


def test_cls_001_begin_closure_blocks_ordinary_operations(conn_lab):
    """CLS-001: beginClosure pasa jornada a EN_CIERRE, bloqueando entradas, salidas y traslados ordinarios."""
    jornada = obtener_jornada_actual(conn_lab)
    j_en_cierre = beginClosure(conn_lab, journal_id=jornada["id"], user_id="alan")
    assert j_en_cierre["estado"] == "EN_CIERRE"

    # Intentar registrar entrada ordinaria durante el cierre -> debe ser bloqueada
    with pytest.raises(ErrorMovimiento, match="EN_CIERRE"):
        registerEntry(
            conn_lab,
            fund_code="1110",
            amount_cents=500_00,
            counterpart="Venta",
            reason="Intento durante cierre",
            created_by="cajero",
        )


def test_cls_002_validate_closure_detects_pending_transfers(conn_lab):
    """CLS-002: validateClosure detecta traslados en tránsito e impide la confirmación del cierre."""
    # Fondeo inicial
    registerEntry(conn_lab, fund_code="1110", amount_cents=1000_00, counterpart="Aporte", reason="Fondeo", created_by="alan")

    # Iniciar traslado que queda en PENDING_RECEIPT
    transferFunds(conn_lab, origin_fund_code="1110", destination_fund_code="1130", amount_cents=300_00, dispatched_by="alan", reason="Hacia Tesoro")

    jornada = obtener_jornada_actual(conn_lab)
    beginClosure(conn_lab, journal_id=jornada["id"], user_id="alan")

    val = validateClosure(conn_lab, journal_id=jornada["id"])
    assert val["can_close"] is False
    assert val["pending_transfers_count"] == 1
    assert any("PENDING_RECEIPT" in r for r in val["reasons"])

    # Intentar confirmar cierre con traslados pendientes debe fallar
    with pytest.raises(ErrorCierre, match="No se puede confirmar el cierre"):
        confirmClosure(conn_lab, journal_id=jornada["id"], user_id="alan")


def test_cls_003_cash_drawer_sweep_1110_to_1120_clears_1110(conn_lab):
    """CLS-003: executeCashDrawerSweep traslada íntegramente el saldo de 1110 a 1120 dejándolo en exactamente 0 centavos."""
    registerEntry(conn_lab, fund_code="1110", amount_cents=4500_50, counterpart="Venta", reason="Fondeo", created_by="alan")

    jornada = obtener_jornada_actual(conn_lab)
    beginClosure(conn_lab, journal_id=jornada["id"], user_id="alan")

    f1110_antes = obtener_fondo_por_codigo(conn_lab, "1110")
    assert f1110_antes["saldo_centavos"] == 4500_50

    sweep_res = executeCashDrawerSweep(conn_lab, journal_id=jornada["id"], user_id="alan")
    assert sweep_res["swept"] is True
    assert sweep_res["amount_cents"] == 4500_50

    # Invariante crítica: 1110 = 0 centavos
    f1110_despues = obtener_fondo_por_codigo(conn_lab, "1110")
    assert f1110_despues["saldo_centavos"] == 0

    # 1120 debe haber recibido exactamente los 4500_50 centavos
    f1120_despues = obtener_fondo_por_codigo(conn_lab, "1120")
    assert f1120_despues["saldo_centavos"] == 4500_50


def test_cls_004_confirm_closure_canonical_snapshot_and_hash(conn_lab):
    """CLS-004: confirmClosure genera snapshot integral con hash SHA-256 canónico y cierra la jornada."""
    registerEntry(conn_lab, fund_code="1110", amount_cents=2000_00, counterpart="Aporte", reason="Fondeo", created_by="alan")

    jornada = obtener_jornada_actual(conn_lab)
    beginClosure(conn_lab, journal_id=jornada["id"], user_id="alan")
    executeCashDrawerSweep(conn_lab, journal_id=jornada["id"], user_id="alan")

    # Confirmar cierre
    cierre = confirmClosure(conn_lab, journal_id=jornada["id"], user_id="alan", observations="Cierre conforme diario")

    assert cierre["status"] == "CONFIRMED"
    assert len(cierre["canonical_hash"]) == 64
    assert cierre["total_registrado_cents"] == 2000_00
    assert cierre["snapshot"]["numero_jornada"] == jornada["numero_jornada"]

    # Estado de jornada en BD debe ser CERRADA
    j_actual = getClosure(conn_lab, journal_id=jornada["id"])
    assert j_actual["status"] == "CONFIRMED"
    assert j_actual["canonical_hash"] == cierre["canonical_hash"]


def test_cls_005_inmutabilidad_triggers_cierres(conn_lab):
    """CLS-005: Triggers de SQLite bloquean cualquier UPDATE o DELETE sobre un cierre confirmado."""
    jornada = obtener_jornada_actual(conn_lab)
    beginClosure(conn_lab, journal_id=jornada["id"], user_id="alan")
    executeCashDrawerSweep(conn_lab, journal_id=jornada["id"], user_id="alan")
    cierre = confirmClosure(conn_lab, journal_id=jornada["id"], user_id="alan")

    cursor = conn_lab.cursor()

    # Intento de modificar cierre confirmado
    with pytest.raises(sqlite3.IntegrityError, match="No está permitido modificar un cierre de jornada confirmado"):
        cursor.execute(
            "UPDATE tesoreria_cierres SET total_registrado_cents = 9999 WHERE closure_id = ?",
            (cierre["closure_id"],),
        )

    # Intento de borrar registro de cierre
    with pytest.raises(sqlite3.IntegrityError, match="No está permitido eliminar registros de cierres"):
        cursor.execute("DELETE FROM tesoreria_cierres WHERE closure_id = ?", (cierre["closure_id"],))


def test_cls_006_open_next_journal_preserves_1110_zero(conn_lab):
    """CLS-006: openNextJournal abre la jornada siguiente garantizando 1110 en 0 y continuidad contable."""
    registerEntry(conn_lab, fund_code="1110", amount_cents=1500_00, counterpart="Venta", reason="Fondeo", created_by="alan")

    j1 = obtener_jornada_actual(conn_lab)
    beginClosure(conn_lab, journal_id=j1["id"], user_id="alan")
    executeCashDrawerSweep(conn_lab, journal_id=j1["id"], user_id="alan")
    confirmClosure(conn_lab, journal_id=j1["id"], user_id="alan")

    # Abrir jornada siguiente
    j2 = openNextJournal(conn_lab, user_id="cajero_turno_manana", observations="Apertura turno mañana")

    assert j2["id"] != j1["id"]
    assert j2["numero_jornada"] == j1["numero_jornada"] + 1
    assert j2["estado"] == "ABIERTA"

    # Verificar que el fondo 1110 en los detalles de apertura inicia en 0
    detalles = {d["codigo_fondo"]: d["saldo_apertura_centavos"] for d in j2["detalles"]}
    assert detalles["1110"] == 0
    assert detalles["1120"] == 1500_00  # Recibió el barrido anterior
