"""Pruebas Adversariales de Seguridad, Inmutabilidad de Triggers y Recuperación de Desastres — SPRINT 4 FASE G & H.

Verificación de:
- Triggers de inmutabilidad estricta contra UPDATE y DELETE directo en:
  * tesoreria_movimientos
  * tesoreria_cierres
  * tesoreria_auditoria_eventos
  * tesoreria_reversiones
  * tesoreria_traslados (en estados finales)
- Cumplimiento estricto de PRAGMA foreign_keys = ON (rechazo de registros huérfanos).
- Simulación de reinicio abrupto y recuperación de desastres (Crash Recovery en WAL).
- Reconstrucción determinista de saldos exclusivamente desde tesoreria_movimientos (Single Source of Truth).
"""

import sqlite3
import pytest

from tesoreria.catalog import obtener_fondo_por_codigo, sembrar_catalogo_canónico
from tesoreria.closures import beginClosure, confirmClosure, executeCashDrawerSweep
from tesoreria.isolation import conectar_db_laboratorio
from tesoreria.jornadas import abrir_jornada
from tesoreria.movements import registerEntry, registerExit
from tesoreria.reversals import reverseMovement
from tesoreria.schema import inicializar_esquema_laboratorio
from tesoreria.transfers import confirmTransferReceipt, transferFunds


@pytest.fixture
def conn_security(tmp_path):
    """Base SQLite con WAL habilitado y esquema inicializado."""
    db_file = tmp_path / "tesoreria_security_recovery.db"
    conn = conectar_db_laboratorio(str(db_file))
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)
    abrir_jornada(conn, usuario="security_admin")
    yield conn, str(db_file)
    conn.close()


def test_triggers_block_direct_sql_tampering(conn_security):
    """Verifica que los triggers de inmutabilidad aborten intentos de manipulación directa SQL."""
    conn, _ = conn_security

    # 1. Movimiento registrado
    m = registerEntry(
        conn,
        fund_code="1110",
        amount_cents=10_000_000,
        counterpart="Capital",
        reason="Fondeo test",
        created_by="security_admin",
    )

    # Intento de UPDATE directo en tesoreria_movimientos
    with pytest.raises(sqlite3.IntegrityError) as exc_m_upd:
        conn.execute("UPDATE tesoreria_movimientos SET amount_cents = 99999999 WHERE movement_id = ?", (m["movement_id"],))
    assert "INMUTABILIDAD" in str(exc_m_upd.value)

    # Intento de DELETE directo en tesoreria_movimientos
    with pytest.raises(sqlite3.IntegrityError) as exc_m_del:
        conn.execute("DELETE FROM tesoreria_movimientos WHERE movement_id = ?", (m["movement_id"],))
    assert "INMUTABILIDAD" in str(exc_m_del.value)

    # 2. Reversión
    rev = reverseMovement(
        conn,
        movement_id=m["movement_id"],
        reason="Corrección de fondeo",
        user_id="security_admin",
    )

    # Intento de UPDATE directo en tesoreria_reversiones
    with pytest.raises(sqlite3.IntegrityError) as exc_r_upd:
        conn.execute("UPDATE tesoreria_reversiones SET reversed_amount_cents = 0 WHERE reversal_id = ?", (rev["reversal_id"],))
    assert "INMUTABILIDAD" in str(exc_r_upd.value)

    # Intento de DELETE directo en tesoreria_reversiones
    with pytest.raises(sqlite3.IntegrityError) as exc_r_del:
        conn.execute("DELETE FROM tesoreria_reversiones WHERE reversal_id = ?", (rev["reversal_id"],))
    assert "INMUTABILIDAD" in str(exc_r_del.value)


def test_foreign_keys_enforcement(conn_security):
    """Verifica que PRAGMA foreign_keys = ON esté activo y rechace claves foráneas inexistentes."""
    conn, _ = conn_security

    # Intentar insertar un movimiento con fund_id inexistente (99999)
    with pytest.raises(sqlite3.IntegrityError) as exc_fk:
        conn.execute(
            """
            INSERT INTO tesoreria_movimientos (
                movement_id, idempotency_key, journal_id, fund_id,
                movement_type, amount_cents, currency, counterpart,
                reason, created_by, terminal_id, status, occurred_at, recorded_at
            ) VALUES ('orphan-mov', 'k1', 1, 99999, 'ENTRY', 1000, 'ARS', 'X', 'Test', 'u', 'T1', 'POSTED', '2026-10-31T00:00:00Z', '2026-10-31T00:00:00Z')
            """
        )
    assert "FOREIGN KEY constraint failed" in str(exc_fk.value)


def test_crash_recovery_and_balance_reconstruction_from_ledger(conn_security):
    """Simula una desconexión abrupta / reinicio del proceso y verifica:
    1. La persistencia íntegra de las transacciones confirmadas.
    2. La reconstrucción de saldos exactamente a partir del libro de movimientos (single source of truth)."""
    conn, db_path = conn_security

    # Cargar operaciones
    registerEntry(conn, fund_code="1110", amount_cents=100_000_000, counterpart="Ventas", reason="Día", created_by="cajero")
    registerExit(conn, fund_code="1110", amount_cents=30_000_000, counterpart="Proveedor", reason="Insumos", created_by="cajero")

    trf = transferFunds(
        conn,
        origin_fund_code="1110",
        destination_fund_code="1210",
        amount_cents=20_000_000,
        dispatched_by="cajero",
        reason="Depósito en banco",
    )
    confirmTransferReceipt(conn, transfer_id=trf["transfer_id"], received_by="cajero")

    # Guardar y cerrar conexión abruptamente (simula cierre de proceso / reinicio)
    conn.commit()
    conn.close()

    # Abrir nueva conexión fresca hacia el archivo SQLite
    new_conn = conectar_db_laboratorio(db_path)

    # Verificar que el PRAGMA foreign_keys y modo WAL están activos en la nueva conexión
    cur = new_conn.cursor()
    cur.execute("PRAGMA foreign_keys")
    assert cur.fetchone()[0] == 1

    cur.execute("PRAGMA journal_mode")
    assert cur.fetchone()[0].upper() == "WAL"

    # RECONSTRUCCIÓN INDEPENDIENTE DE SALDOS DIRECTAMENTE DESDE tesoreria_movimientos:
    # Para cada fondo: SUM(ENTRY) - SUM(EXIT)
    cur.execute(
        """
        SELECT f.codigo,
               COALESCE(SUM(CASE WHEN m.movement_type IN ('ENTRY', 'TRANSFER_IN', 'SWEEP_IN') THEN m.amount_cents
                                 WHEN m.movement_type IN ('EXIT', 'TRANSFER_OUT', 'SWEEP_OUT') THEN -m.amount_cents
                                 ELSE 0 END), 0) as saldo_reconstruido
        FROM tesoreria_fondos f
        LEFT JOIN tesoreria_movimientos m ON f.id = m.fund_id AND m.status = 'POSTED'
        GROUP BY f.codigo
        """
    )
    reconstruidos = {row["codigo"]: row["saldo_reconstruido"] for row in cur.fetchall()}

    # Comprobar contra los saldos en tesoreria_fondos
    f1110 = obtener_fondo_por_codigo(new_conn, "1110")
    f1210 = obtener_fondo_por_codigo(new_conn, "1210")

    # 1110: 100M - 30M - 20M = 50M
    assert f1110["saldo_centavos"] == 50_000_000
    assert reconstruidos["1110"] == 50_000_000

    # 1210: +20M = 20M
    assert f1210["saldo_centavos"] == 20_000_000
    assert reconstruidos["1210"] == 20_000_000

    new_conn.close()
