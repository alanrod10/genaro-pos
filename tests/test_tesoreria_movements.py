"""Pruebas de Entradas y Salidas — LAB-005 (FLOW-021 a FLOW-028).

Verificación de operaciones monetarias de laboratorio:
- FLOW-021: registerEntry con datos válidos incrementa saldo y genera movimiento POSTED.
- FLOW-022: registerExit con saldo suficiente decrementa saldo y genera movimiento POSTED.
- FLOW-023: Rechazo de importe <= 0.
- FLOW-024: Rechazo si no hay jornada abierta.
- FLOW-025: Rechazo si fondo no está habilitado (ACTIVO).
- FLOW-026: Rechazo de egreso por saldo disponible insuficiente.
- FLOW-027: Rechazo sin motivo o contrapartida económica obligatoria.
- FLOW-028: Idempotencia estricta bajo reintentos.
- Inmutabilidad y Rollback ante errores.
"""

import sqlite3
import pytest

from tesoreria.isolation import conectar_db_laboratorio
from tesoreria.schema import inicializar_esquema_laboratorio
from tesoreria.catalog import sembrar_catalogo_canónico, actualizar_estado_fondo, obtener_fondo_por_codigo
from tesoreria.jornadas import abrir_jornada
from tesoreria.movements import (
    ErrorMovimiento,
    ErrorSaldoInsuficiente,
    listar_movimientos,
    registerEntry,
    registerExit,
)
from tesoreria.idempotency import ErrorIdempotencia


@pytest.fixture
def conn_lab(tmp_path):
    """Crea una base SQLite de laboratorio con catálogo y jornada abierta."""
    db_file = tmp_path / "tesoreria_movs_test.db"
    conn = conectar_db_laboratorio(str(db_file))
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)
    abrir_jornada(conn, usuario="alan", observaciones="Jornada para pruebas LAB-005")
    yield conn
    conn.close()


def test_flow_021_register_entry_valido(conn_lab):
    """FLOW-021: registerEntry incrementa saldo y genera movimiento POSTED."""
    fondo_inicial = obtener_fondo_por_codigo(conn_lab, "1110")
    assert fondo_inicial["saldo_centavos"] == 0

    mov = registerEntry(
        conn=conn_lab,
        fund_code="1110",
        amount_cents=100000_00,  # $100.000,00
        counterpart="Aporte de capital inicial laboratorio",
        reason="Dotación de efectivo en caja principal",
        created_by="alan",
        terminal_id="TERM-01",
        idempotency_key="idemp_entry_001",
    )

    assert mov["status"] == "POSTED"
    assert mov["movement_type"] == "ENTRY"
    assert mov["amount_cents"] == 100000_00
    assert mov["saldo_posterior_centavos"] == 100000_00

    fondo_actual = obtener_fondo_por_codigo(conn_lab, "1110")
    assert fondo_actual["saldo_centavos"] == 100000_00

    # Verificar que aparece en el listado de movimientos
    movs = listar_movimientos(conn_lab, codigo_fondo="1110")
    assert len(movs) == 1
    assert movs[0]["movement_id"] == mov["movement_id"]


def test_flow_022_register_exit_valido(conn_lab):
    """FLOW-022: registerExit con saldo suficiente decrementa saldo."""
    registerEntry(
        conn=conn_lab,
        fund_code="1110",
        amount_cents=50000_00,
        counterpart="Fondo operativo",
        reason="Dotación",
        created_by="alan",
    )

    mov_exit = registerExit(
        conn=conn_lab,
        fund_code="1110",
        amount_cents=20000_00,
        counterpart="Pago proveedor librería",
        reason="Compra de resmas y formularios",
        created_by="alan",
        terminal_id="TERM-01",
        idempotency_key="idemp_exit_001",
    )

    assert mov_exit["status"] == "POSTED"
    assert mov_exit["movement_type"] == "EXIT"
    assert mov_exit["amount_cents"] == 20000_00
    assert mov_exit["saldo_posterior_centavos"] == 30000_00

    fondo_actual = obtener_fondo_por_codigo(conn_lab, "1110")
    assert fondo_actual["saldo_centavos"] == 30000_00


def test_flow_023_rechazo_importe_invalido(conn_lab):
    """FLOW-023: Rechazo de importe cero o negativo."""
    with pytest.raises(ErrorMovimiento, match="estrictamente positivo"):
        registerEntry(
            conn=conn_lab,
            fund_code="1110",
            amount_cents=0,
            counterpart="Aporte",
            reason="Prueba cero",
            created_by="alan",
        )

    with pytest.raises(ErrorMovimiento, match="estrictamente positivo"):
        registerExit(
            conn=conn_lab,
            fund_code="1110",
            amount_cents=-5000,
            counterpart="Retiro",
            reason="Prueba negativo",
            created_by="alan",
        )


def test_flow_024_rechazo_sin_jornada_abierta(tmp_path):
    """FLOW-024: Rechazo si no hay jornada abierta."""
    db_file = tmp_path / "sin_jornada.db"
    conn = conectar_db_laboratorio(str(db_file))
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)

    with pytest.raises(ErrorMovimiento, match="Se requiere una jornada de tesorería ABIERTA"):
        registerEntry(
            conn=conn,
            fund_code="1110",
            amount_cents=1000,
            counterpart="Aporte",
            reason="Sin jornada",
            created_by="alan",
        )
    conn.close()


def test_flow_025_rechazo_fondo_no_activo(conn_lab):
    """FLOW-025: Rechazo si el fondo no está ACTIVO."""
    actualizar_estado_fondo(conn_lab, codigo="1120", nuevo_estado="BLOQUEADO", usuario="alan")

    with pytest.raises(ErrorMovimiento, match="no está habilitado"):
        registerEntry(
            conn=conn_lab,
            fund_code="1120",
            amount_cents=5000,
            counterpart="Aporte",
            reason="Fondo bloqueado",
            created_by="alan",
        )


def test_flow_026_rechazo_saldo_insuficiente(conn_lab):
    """FLOW-026: Rechazo de egreso cuando el saldo disponible es insuficiente."""
    # Fondo 1110 tiene 0 centavos
    with pytest.raises(ErrorSaldoInsuficiente, match="Saldo disponible insuficiente"):
        registerExit(
            conn=conn_lab,
            fund_code="1110",
            amount_cents=10000,
            counterpart="Pago servicios",
            reason="Egreso sin saldo",
            created_by="alan",
        )

    # Verificar que el saldo sigue intacto en 0
    fondo = obtener_fondo_por_codigo(conn_lab, "1110")
    assert fondo["saldo_centavos"] == 0


def test_flow_027_rechazo_sin_motivo_o_contrapartida(conn_lab):
    """FLOW-027: Rechazo sin motivo o sin contrapartida económica justificada."""
    with pytest.raises(ErrorMovimiento, match="El motivo del movimiento es obligatorio"):
        registerEntry(
            conn=conn_lab,
            fund_code="1110",
            amount_cents=1000,
            counterpart="Aporte",
            reason="",
            created_by="alan",
        )

    with pytest.raises(ErrorMovimiento, match="La contrapartida económica es obligatoria"):
        registerEntry(
            conn=conn_lab,
            fund_code="1110",
            amount_cents=1000,
            counterpart="   ",
            reason="Motivo valido",
            created_by="alan",
        )


def test_flow_028_idempotencia_estricta(conn_lab):
    """FLOW-028: Doble envío con misma clave no duplica movimiento ni saldo."""
    k = "idemp_test_unique_key_123"

    # Primer envío
    res1 = registerEntry(
        conn=conn_lab,
        fund_code="1110",
        amount_cents=45000_00,
        counterpart="Aporte socios",
        reason="Inyección inicial",
        created_by="alan",
        idempotency_key=k,
    )

    # Reintento con mismos parámetros
    res2 = registerEntry(
        conn=conn_lab,
        fund_code="1110",
        amount_cents=45000_00,
        counterpart="Aporte socios",
        reason="Inyección inicial",
        created_by="alan",
        idempotency_key=k,
    )

    assert res1["movement_id"] == res2["movement_id"]
    fondo = obtener_fondo_por_codigo(conn_lab, "1110")
    assert fondo["saldo_centavos"] == 45000_00  # NO se duplicó a 90.000!

    movs = listar_movimientos(conn_lab, codigo_fondo="1110")
    assert len(movs) == 1

    # Conflicto con distinta cantidad
    with pytest.raises(ErrorIdempotencia, match="Conflicto de Idempotencia"):
        registerEntry(
            conn=conn_lab,
            fund_code="1110",
            amount_cents=99999_00,
            counterpart="Aporte socios",
            reason="Inyección inicial",
            created_by="alan",
            idempotency_key=k,
        )


def test_inmutabilidad_triggers_movimientos(conn_lab):
    """Verifica que los triggers de SQLite prohíban UPDATE o DELETE en movimientos confirmados."""
    mov = registerEntry(
        conn=conn_lab,
        fund_code="1110",
        amount_cents=1000_00,
        counterpart="Prueba",
        reason="Inmutabilidad",
        created_by="alan",
    )

    cursor = conn_lab.cursor()
    # Intento de UPDATE
    with pytest.raises(sqlite3.IntegrityError, match="No está permitido modificar movimientos confirmados"):
        cursor.execute(
            "UPDATE tesoreria_movimientos SET amount_cents = 999999 WHERE movement_id = ?",
            (mov["movement_id"],),
        )

    # Intento de DELETE
    with pytest.raises(sqlite3.IntegrityError, match="No está permitido eliminar movimientos de tesorería"):
        cursor.execute(
            "DELETE FROM tesoreria_movimientos WHERE movement_id = ?",
            (mov["movement_id"],),
        )
    cursor.close()
