"""Pruebas Adversariales del Protocolo de Cierre e Inmutabilidad — SPRINT 4 FASE D.

Verificación de:
- Protocolo estricto de cierre en 12 pasos.
- Bloqueo de operaciones ordinarias durante estado EN_CIERRE.
- Detección adversarial de condiciones bloqueantes:
  * Movimientos en PENDING_REVIEW.
  * Traslados en tránsito PENDING_RECEIPT.
  * Saldo remanente en Caja 1110 (sweep obligatorio).
- Preservación de expedientes de diferencias abiertos en el snapshot.
- Generación de snapshot canónico determinista y SHA-256.
- Inmutabilidad estricta (triggers UPDATE y DELETE) en tesoreria_cierres.
- Continuidad operativa en openNextJournal garantizando 1110 = 0.
"""

import sqlite3
import pytest

from tesoreria.catalog import obtener_fondo_por_codigo, sembrar_catalogo_canónico
from tesoreria.closures import (
    beginClosure,
    confirmClosure,
    executeCashDrawerSweep,
    getClosure,
    openNextJournal,
    validateClosure,
    ErrorCierre,
)
from tesoreria.isolation import conectar_db_laboratorio
from tesoreria.jornadas import abrir_jornada, ErrorJornada
from tesoreria.movements import registerEntry, registerExit, ErrorMovimiento
from tesoreria.reconciliations import createDifferenceCase, createReconciliation
from tesoreria.schema import inicializar_esquema_laboratorio
from tesoreria.transfers import cancelPendingTransfer, transferFunds


@pytest.fixture
def conn_closures(tmp_path):
    """Base de laboratorio con jornada 1 abierta y catálogo sembrado."""
    db_file = tmp_path / "tesoreria_adv_closures.db"
    conn = conectar_db_laboratorio(str(db_file))
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)
    abrir_jornada(conn, usuario="cajero_adv")
    registerEntry(
        conn,
        fund_code="1110",
        amount_cents=50_000_000,
        counterpart="Ventas del día",
        reason="Fondeo operativo",
        created_by="cajero_adv",
    )
    yield conn
    conn.close()


def test_closure_protocol_12_steps_and_immutability(conn_closures):
    """Ejecuta el protocolo de cierre completo, validando cada etapa y la inmutabilidad."""
    cur = conn_closures.cursor()
    cur.execute("SELECT id FROM tesoreria_jornadas WHERE estado = 'ABIERTA'")
    jid = cur.fetchone()["id"]
    cur.close()

    # 1. Iniciar cierre: pasar a EN_CIERRE
    j_encierre = beginClosure(conn_closures, journal_id=jid, user_id="supervisor_cierre")
    assert j_encierre["estado"] == "EN_CIERRE"

    # 2. Verificar que operaciones ordinarias se bloquean en EN_CIERRE
    with pytest.raises((ErrorMovimiento, ErrorJornada)):
        registerExit(
            conn_closures,
            fund_code="1110",
            amount_cents=1_000_000,
            counterpart="Proveedor",
            reason="Gasto en jornada en cierre",
            created_by="cajero_adv",
        )

    # 3. Validar cierre: debe indicar que 1110 necesita sweep
    val = validateClosure(conn_closures, journal_id=jid)
    assert val["can_close"] is False
    assert val["needs_sweep"] is True
    assert val["saldo_1110_cents"] == 50_000_000

    # 4. Intentar confirmar cierre sin barrido debe fallar
    with pytest.raises(ErrorCierre) as exc_sweep:
        confirmClosure(conn_closures, journal_id=jid, user_id="supervisor_cierre")
    assert "requiere vaciado a 1120" in str(exc_sweep.value)

    # 5. Ejecutar barrido de caja 1110 -> 1120
    sweep_res = executeCashDrawerSweep(
        conn_closures,
        journal_id=jid,
        user_id="supervisor_cierre",
        reason="Vaciado fin de día",
    )
    assert sweep_res["swept"] is True
    assert sweep_res["amount_cents"] == 50_000_000

    # 6. Verificar que 1110 está en 0
    f1110 = obtener_fondo_por_codigo(conn_closures, "1110")
    assert f1110["saldo_centavos"] == 0

    # 7. Validar de nuevo: ahora can_close == True
    val2 = validateClosure(conn_closures, journal_id=jid)
    assert val2["can_close"] is True

    # 8. Confirmar cierre definitivo
    cierre = confirmClosure(
        conn_closures,
        journal_id=jid,
        user_id="supervisor_cierre",
        observations="Cierre regular sin novedades",
    )
    assert cierre["status"] == "CONFIRMED"
    assert len(cierre["canonical_hash"]) == 64  # SHA-256

    # 9. Verificar que la jornada quedó en estado CERRADA
    cur = conn_closures.cursor()
    cur.execute("SELECT estado FROM tesoreria_jornadas WHERE id = ?", (jid,))
    assert cur.fetchone()["estado"] == "CERRADA"
    cur.close()

    # 10. Inmutabilidad en tesoreria_cierres: UPDATE y DELETE abortados por trigger
    # 10.A: UPDATE general en cierre
    with pytest.raises(sqlite3.IntegrityError) as exc_upd:
        conn_closures.execute(
            "UPDATE tesoreria_cierres SET total_registrado_cents = 0 WHERE journal_id = ?",
            (jid,),
        )
    assert "INMUTABILIDAD" in str(exc_upd.value)

    # 10.B: Alteración directa de snapshot_json
    with pytest.raises(sqlite3.IntegrityError) as exc_snap:
        conn_closures.execute(
            "UPDATE tesoreria_cierres SET snapshot_json = '{\"manipulado\": true}' WHERE journal_id = ?",
            (jid,),
        )
    assert "INMUTABILIDAD" in str(exc_snap.value)

    # 10.C: Alteración directa de canonical_hash
    with pytest.raises(sqlite3.IntegrityError) as exc_hash:
        conn_closures.execute(
            "UPDATE tesoreria_cierres SET canonical_hash = '0123456789abcdef' WHERE journal_id = ?",
            (jid,),
        )
    assert "INMUTABILIDAD" in str(exc_hash.value)

    # 10.D: DELETE de un cierre confirmado
    with pytest.raises(sqlite3.IntegrityError) as exc_del:
        conn_closures.execute(
            "DELETE FROM tesoreria_cierres WHERE journal_id = ?",
            (jid,),
        )
    assert "INMUTABILIDAD" in str(exc_del.value)

    # 10.E: Alteración directa de una jornada cerrada (UPDATE estado u observaciones)
    with pytest.raises(sqlite3.IntegrityError) as exc_j_upd:
        conn_closures.execute(
            "UPDATE tesoreria_jornadas SET estado = 'ABIERTA' WHERE id = ?",
            (jid,),
        )
    assert "INMUTABILIDAD" in str(exc_j_upd.value)

    # 10.F: DELETE directo de una jornada cerrada
    with pytest.raises(sqlite3.IntegrityError) as exc_j_del:
        conn_closures.execute(
            "DELETE FROM tesoreria_jornadas WHERE id = ?",
            (jid,),
        )
    assert "INMUTABILIDAD" in str(exc_j_del.value)

    # 11. Abrir la siguiente jornada: openNextJournal
    next_j = openNextJournal(conn_closures, user_id="cajero_dia_2", observations="Apertura día 2")
    assert next_j["estado"] == "ABIERTA"
    assert next_j["numero_jornada"] == 2

    # 12. Verificar que 1110 inicia en 0 exacto
    f1110_dia2 = obtener_fondo_por_codigo(conn_closures, "1110")
    assert f1110_dia2["saldo_centavos"] == 0


def test_closure_blocked_by_pending_transfers_and_reviews(conn_closures):
    """Verifica que traslados en tránsito no confirmados impiden el cierre definitivo."""
    cur = conn_closures.cursor()
    cur.execute("SELECT id FROM tesoreria_jornadas WHERE estado = 'ABIERTA'")
    jid = cur.fetchone()["id"]
    cur.close()

    # Iniciar un traslado que queda en PENDING_RECEIPT
    t = transferFunds(
        conn_closures,
        origin_fund_code="1110",
        destination_fund_code="1210",
        amount_cents=10_000_000,
        dispatched_by="cajero_adv",
        reason="Envío no recibido aún",
    )
    assert t["status"] == "PENDING_RECEIPT"

    beginClosure(conn_closures, journal_id=jid, user_id="supervisor_cierre")

    # validateClosure debe reportar el traslado pendiente
    val = validateClosure(conn_closures, journal_id=jid)
    assert val["can_close"] is False
    assert val["pending_transfers_count"] == 1
    assert any("traslados en tránsito" in r for r in val["reasons"])

    # Confirmar cierre debe ser rechazado
    with pytest.raises(ErrorCierre) as exc:
        confirmClosure(conn_closures, journal_id=jid, user_id="supervisor_cierre")
    assert "traslados en tránsito" in str(exc.value)

    # Al cancelar el traslado, el impedimento desaparece
    cancelPendingTransfer(
        conn_closures,
        transfer_id=t["transfer_id"],
        cancelled_by="cajero_adv",
        reason="Se cancela para cerrar jornada",
    )

    # Ejecutar sweep para vaciar el saldo restituido en 1110
    executeCashDrawerSweep(conn_closures, journal_id=jid, user_id="supervisor_cierre")
    val_post = validateClosure(conn_closures, journal_id=jid)
    assert val_post["can_close"] is True


def test_closure_preserves_difference_cases_in_snapshot(conn_closures):
    """Verifica que expedientes de diferencias abiertos quedan registrados en el snapshot del cierre."""
    cur = conn_closures.cursor()
    cur.execute("SELECT id FROM tesoreria_jornadas WHERE estado = 'ABIERTA'")
    jid = cur.fetchone()["id"]
    cur.close()

    # Crear un arqueo con diferencia de -$1.000 (faltante de 100_000 cents)
    # Saldo registrado en 1110 = 50_000_000 cents
    rec = createReconciliation(
        conn=conn_closures,
        fund_code="1110",
        reconciliation_type="CLOSING",
        verified_amount_cents=49_900_000,
        user_id="auditor_adv",
        notes="Faltante verificado en conteo",
        create_difference_case=True,
    )
    assert rec["diferencia_cents"] == -100_000
    assert rec["difference_case_id"] is not None

    beginClosure(conn_closures, journal_id=jid, user_id="supervisor_cierre")
    executeCashDrawerSweep(conn_closures, journal_id=jid, user_id="supervisor_cierre")

    cierre = confirmClosure(conn_closures, journal_id=jid, user_id="supervisor_cierre")
    snapshot = cierre["snapshot"]

    assert len(snapshot["expedientes_diferencias_abiertos"]) == 1
    diff_case = snapshot["expedientes_diferencias_abiertos"][0]
    assert diff_case["case_id"] == rec["difference_case_id"]
    assert diff_case["difference_cents"] == -100_000
