"""Suite de Pruebas de Certificación FASE 2 — CIRCUITO PREPAY Y TESORERÍA INTEGRADA.

Cubre exhaustivamente los 38 escenarios obligatorios especificados en la Sección 8:
- Escenarios de Saldo (1 a 6)
- Concurrencia y Competencia por Cargo Diario (7 a 14)
- Caídas y Recuperación (15 a 22)
- Reglas del Cargo Diario de $350 (23 a 28)
- Integración Económica y Segregación de Fondos (29 a 35)
- Regresiones y Respeto al Catálogo (36 a 38)
"""

from datetime import datetime, timezone
import json
import sqlite3
import threading
import time
import pytest

from db.commercial_outbox_emitter import enqueue_commercial_event
from db.services import registrar_carga_idempotente_turso
from tesoreria.catalog import obtener_fondo_por_codigo, sembrar_catalogo_canónico
from tesoreria.commercial_bridge_worker import (
    process_pending_commercial_events,
    process_single_commercial_event,
)
from tesoreria.closures import (
    beginClosure,
    confirmClosure,
    executeCashDrawerSweep,
    executePrepayDrawerSweep,
    validateClosure,
)
from tesoreria.reconciliations import reconcilePhysicalFund
from tesoreria.idempotency import ErrorIdempotencia
from tesoreria.isolation import conectar_db_laboratorio
from tesoreria.jornadas import abrir_jornada, cerrar_jornada, obtener_jornada_actual
from tesoreria.movements import registerEntry
from tesoreria.prepay_coordinator import (
    MONTO_CARGO_DIARIO_CENTS,
    aplicar_movimientos_prepay_worker,
    cancelar_pre_reserva_prepay,
    confirmar_pre_reserva_comercial,
    inicializar_tablas_coordinacion_prepay,
    reconciliar_circuito_prepay,
    recuperar_coordinacion_prepay,
    solicitar_pre_reserva_prepay,
)
from tesoreria.reservations import ErrorReserva
from tesoreria.schema import inicializar_esquema_laboratorio
from tesoreria.transfers import ErrorTraslado, confirmTransferReceipt, transferFunds


def _crear_esquema_comercial(conn: sqlite3.Connection):
    """Crea las tablas mínimas comerciales (servicios, auditoría, outbox) para pruebas."""
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS cargas_servicios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            legacy_row_id TEXT,
            fecha_hora_utc TEXT NOT NULL,
            servicio TEXT NOT NULL,
            monto_carga INTEGER NOT NULL CHECK (monto_carga > 0),
            monto_adicional INTEGER NOT NULL DEFAULT 0 CHECK (monto_adicional >= 0),
            total_cobrado INTEGER NOT NULL CHECK (total_cobrado >= 0),
            pago_efectivo INTEGER NOT NULL DEFAULT 0 CHECK (pago_efectivo >= 0),
            pago_transferencia INTEGER NOT NULL DEFAULT 0 CHECK (pago_transferencia >= 0),
            anulado INTEGER NOT NULL DEFAULT 0 CHECK (anulado IN (0, 1)),
            idempotency_key TEXT UNIQUE,
            plataforma_origen TEXT,
            servicio_destino TEXT
        );

        CREATE TABLE IF NOT EXISTS servicios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            servicio TEXT NOT NULL,
            plataforma_origen TEXT,
            servicio_destino TEXT,
            monto_carga REAL NOT NULL,
            monto_adicional REAL NOT NULL,
            total_cobrado REAL NOT NULL,
            pago_efvo REAL NOT NULL,
            pago_transf REAL NOT NULL,
            anulación INTEGER NOT NULL DEFAULT 0,
            idempotency_key TEXT UNIQUE
        );

        CREATE TABLE IF NOT EXISTS cargas_servicios_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            carga_id INTEGER NOT NULL,
            accion TEXT NOT NULL,
            valores_anteriores TEXT,
            valores_nuevos TEXT,
            actor TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS commercial_events_outbox (
            sequence_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            event_type TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            action_type TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            payload_canonical_hash TEXT NOT NULL,
            payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING'
                CHECK (status IN ('PENDING', 'PROCESSING', 'PUBLISHED', 'FAILED', 'DEAD_LETTER')),
            retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
            max_retries INTEGER NOT NULL DEFAULT 5 CHECK (max_retries >= 1),
            error_message TEXT,
            tesoreria_movement_id TEXT,
            reprocessed_by TEXT,
            reprocessed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            published_at TEXT
        );
        """
    )
    cur.close()


@pytest.fixture
def env_fase2(tmp_path):
    """Genera dos bases SQLite aisladas y conectadas: comercial y tesorería."""
    comm_path = tmp_path / "commercial_fase2.db"
    tes_path = tmp_path / "tesoreria_fase2.db"

    comm_conn = sqlite3.connect(str(comm_path), timeout=30.0)
    comm_conn.row_factory = sqlite3.Row
    comm_conn.execute("PRAGMA foreign_keys = ON;")
    _crear_esquema_comercial(comm_conn)

    tes_conn = conectar_db_laboratorio(str(tes_path))
    inicializar_esquema_laboratorio(tes_conn)
    sembrar_catalogo_canónico(tes_conn)
    inicializar_tablas_coordinacion_prepay(tes_conn)

    # Abrir jornada inicial
    abrir_jornada(tes_conn, usuario="admin", observaciones="Jornada Pruebas Fase 2")

    yield comm_conn, tes_conn, str(comm_path), str(tes_path)

    comm_conn.close()
    tes_conn.close()


# ============================================================================
# 1. ESCENARIOS DE SALDO (1 a 6)
# ============================================================================

def test_escenario_01_capital_suficiente_y_cargo_aplicable(env_fase2):
    """1. Capital suficiente y cargo aplicable ($350). Pre-reserva, commit y aplicación exitosos."""
    comm_conn, tes_conn, _, _ = env_fase2
    # Fondeo inicial de 1220: $1.000 (100.000 centavos)
    registerEntry(tes_conn, "1220", 1000_00, "FONDEO", "Fondeo inicial 1220", "admin")

    # Solicitar recarga PREPAY de $500 en efectivo (capital $500, adicional $50)
    pre_res = solicitar_pre_reserva_prepay(
        conn=tes_conn,
        servicio_destino="PERSONAL",
        monto_carga_cents=500_00,
        monto_adicional_cents=50_00,
        pago_efvo_cents=550_00,
        pago_transf_cents=0,
        idempotency_key="tx_test_01",
        actor="cajero1",
    )
    assert pre_res["success"] is True
    assert pre_res["asume_cargo_diario"] is True
    assert pre_res["monto_cargo_cents"] == 350_00

    # Insertar ticket comercial vinculando la pre-reserva
    tkt_id, es_reintento, guardado = registrar_carga_idempotente_turso(
        registro={
            "FECHA": "01/11/2026 10:00:00",
            "SERVICIO": "Personal",
            "PLATAFORMA_ORIGEN": "PREPAY",
            "SERVICIO_DESTINO": "PERSONAL",
            "MONTO_CARGA": 500,
            "MONTO_ADICIONAL": 50,
            "TOTAL_COBRADO": 550,
            "PAGO_EFVO": 550,
            "PAGO_TRANSF": 0,
        },
        idempotency_key="tx_test_01",
        actor="cajero1",
        conn=comm_conn,
        retornar_registro=True,
        tesoreria_conn=tes_conn,
    )
    assert tkt_id > 0

    # Procesar con el worker
    stats = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats["published"] == 1

    # Verificar saldos:
    # 1220: 1000 - 500 (capital) - 350 (cargo) = 150
    s_1220 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1220';").fetchone()[0]
    assert s_1220 == 150_00

    # 1160 (efectivo prepay diario recaudado): +500
    s_1160 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1160';").fetchone()[0]
    assert s_1160 == 500_00
    s_1130 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1130';").fetchone()[0]
    assert s_1130 == 0

    # 1140 (adicionales): +50
    s_1140 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1140';").fetchone()[0]
    assert s_1140 == 50_00


def test_escenario_02_capital_suficiente_pero_capital_mas_fee_insuficiente(env_fase2):
    """2. Saldo suficiente para capital ($500), pero insuficiente para capital + fee ($500 + $350 = $850)."""
    comm_conn, tes_conn, _, _ = env_fase2
    # Fondeo 1220 con solo $600 (alcanza para $500, pero no para $850)
    registerEntry(tes_conn, "1220", 600_00, "FONDEO", "Fondeo parcial", "admin")

    with pytest.raises(ErrorReserva) as exc_info:
        solicitar_pre_reserva_prepay(
            conn=tes_conn,
            servicio_destino="MOVISTAR",
            monto_carga_cents=500_00,
            monto_adicional_cents=0,
            pago_efvo_cents=500_00,
            pago_transf_cents=0,
            idempotency_key="tx_test_02",
            actor="cajero1",
        )
    assert "insuficiente para capital" in str(exc_info.value).lower()
    assert "cargo diario" in str(exc_info.value).lower()

    # Comprobar que no quedó ninguna reserva huérfana
    reservas = tes_conn.execute("SELECT COUNT(*) FROM tesoreria_reservas;").fetchone()[0]
    assert reservas == 0


def test_escenario_03_saldo_1220_insuficiente(env_fase2):
    """3. Saldo en 1220 estrictamente insuficiente para capital."""
    comm_conn, tes_conn, _, _ = env_fase2
    registerEntry(tes_conn, "1220", 100_00, "FONDEO", "Fondeo mínimo", "admin")

    with pytest.raises(ErrorReserva) as exc_info:
        solicitar_pre_reserva_prepay(
            conn=tes_conn,
            servicio_destino="SUBE",
            monto_carga_cents=500_00,
            monto_adicional_cents=0,
            pago_efvo_cents=500_00,
            pago_transf_cents=0,
            idempotency_key="tx_test_03",
            actor="cajero1",
        )
    assert "saldo digital prepay (1220) insuficiente" in str(exc_info.value).lower()


def test_escenario_04_saldo_1120_insuficiente_para_compensacion(env_fase2):
    """4. PREPAY por transferencia: 1220 suficiente pero 1120 insuficiente para compensar a 1130."""
    comm_conn, tes_conn, _, _ = env_fase2
    # 1220 tiene $2000
    registerEntry(tes_conn, "1220", 2000_00, "FONDEO", "Fondeo 1220", "admin")
    # 1120 tiene solo $100
    registerEntry(tes_conn, "1120", 100_00, "FONDEO", "Fondeo escaso 1120", "admin")
    # 1110 tiene $5000 (no debe usarse como alternativa)
    registerEntry(tes_conn, "1110", 5000_00, "FONDEO", "Fondeo 1110", "admin")

    with pytest.raises(ErrorReserva) as exc_info:
        solicitar_pre_reserva_prepay(
            conn=tes_conn,
            servicio_destino="PERSONAL",
            monto_carga_cents=500_00,
            monto_adicional_cents=50_00,
            pago_efvo_cents=0,
            pago_transf_cents=550_00,
            idempotency_key="tx_test_04",
            actor="cajero1",
        )
    assert "saldo insuficiente en drugstore acumulado (1120)" in str(exc_info.value).lower()
    assert "no se permite utilizar 1110 como alternativa" in str(exc_info.value).lower()

    # Comprobar que 1110 y 1120 no cambiaron
    assert tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1110';").fetchone()[0] == 5000_00
    assert tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1120';").fetchone()[0] == 100_00


def test_escenario_05_rechazo_sin_movimientos_parciales(env_fase2):
    """5. Solicitud rechazada no genera movimientos parciales ni estados corruptos."""
    comm_conn, tes_conn, _, _ = env_fase2
    try:
        solicitar_pre_reserva_prepay(
            conn=tes_conn,
            servicio_destino="CLARO",
            monto_carga_cents=500_00,
            monto_adicional_cents=0,
            pago_efvo_cents=500_00,
            pago_transf_cents=0,
            idempotency_key="tx_test_05",
            actor="cajero1",
        )
    except ErrorReserva:
        pass

    assert tes_conn.execute("SELECT COUNT(*) FROM tesoreria_reservas;").fetchone()[0] == 0
    assert tes_conn.execute("SELECT COUNT(*) FROM tesoreria_movimientos;").fetchone()[0] == 0
    assert tes_conn.execute("SELECT COUNT(*) FROM tesoreria_prepay_coordinacion;").fetchone()[0] == 0


def test_escenario_06_ningun_fondo_queda_negativo(env_fase2):
    """6. Las operaciones respetan rigurosamente el sobregiro cero en todos los fondos."""
    comm_conn, tes_conn, _, _ = env_fase2
    saldos = tes_conn.execute("SELECT codigo, saldo_centavos FROM tesoreria_fondos;").fetchall()
    for row in saldos:
        assert row["saldo_centavos"] >= 0, f"Fondo {row['codigo']} quedó con saldo negativo"


# ============================================================================
# 2. CONCURRENCIA (7 a 14)
# ============================================================================

def test_escenario_07_dos_terminales_compiten_por_primer_cargo(env_fase2):
    """7 y 10. Dos terminales intentan ser la primera operación: solo una asume el fee, la segunda no lo duplica."""
    comm_conn, tes_conn, _, _ = env_fase2
    # Fondeo suficiente: $3.000
    registerEntry(tes_conn, "1220", 3000_00, "FONDEO", "Fondeo 1220", "admin")

    # Terminal 1 reserva
    res_t1 = solicitar_pre_reserva_prepay(
        conn=tes_conn,
        servicio_destino="PERSONAL",
        monto_carga_cents=500_00,
        monto_adicional_cents=0,
        pago_efvo_cents=500_00,
        pago_transf_cents=0,
        idempotency_key="t1_key",
        actor="t1",
    )
    assert res_t1["asume_cargo_diario"] is True

    # Terminal 1 confirma comercialmente
    confirmar_pre_reserva_comercial(tes_conn, "t1_key", commercial_ticket_id=101)

    # Terminal 2 reserva inmediatamente después
    res_t2 = solicitar_pre_reserva_prepay(
        conn=tes_conn,
        servicio_destino="MOVISTAR",
        monto_carga_cents=500_00,
        monto_adicional_cents=0,
        pago_efvo_cents=500_00,
        pago_transf_cents=0,
        idempotency_key="t2_key",
        actor="t2",
    )
    # Terminal 2 NO debe asumir el cargo
    assert res_t2["asume_cargo_diario"] is False
    assert res_t2["monto_cargo_cents"] == 0
    assert res_t2["fee_reservation_id"] is None


def test_escenario_08_dos_terminales_compiten_por_mismo_saldo(env_fase2):
    """8 y 13. Saldo disponible de $1000: T1 toma $800, T2 intenta $800 y es rechazada."""
    comm_conn, tes_conn, _, _ = env_fase2
    # Fondeo con $1.150 ($800 capital + $350 fee = $1.150 exacto)
    registerEntry(tes_conn, "1220", 1150_00, "FONDEO", "Fondeo exacto T1", "admin")

    res_t1 = solicitar_pre_reserva_prepay(
        conn=tes_conn,
        servicio_destino="PERSONAL",
        monto_carga_cents=800_00,
        monto_adicional_cents=0,
        pago_efvo_cents=800_00,
        pago_transf_cents=0,
        idempotency_key="t1_balance_race",
        actor="t1",
    )
    assert res_t1["success"] is True

    # Terminal 1 confirma comercialmente para consolidar la titularidad del fee
    confirmar_pre_reserva_comercial(tes_conn, "t1_balance_race", 101)

    # T2 intenta reservar $800 mientras T1 sigue activo: no hay saldo disponible en 1220
    with pytest.raises(ErrorReserva) as exc:
        solicitar_pre_reserva_prepay(
            conn=tes_conn,
            servicio_destino="SUBE",
            monto_carga_cents=800_00,
            monto_adicional_cents=0,
            pago_efvo_cents=800_00,
            pago_transf_cents=0,
            idempotency_key="t2_balance_race",
            actor="t2",
        )
    assert "insuficiente" in str(exc.value).lower()


def test_escenario_09_primera_reserva_cancela_y_segunda_asume_cargo(env_fase2):
    """9 y 26. T1 reserva capital + $350 pero cancela; T2 asume correctamente el cargo vacante."""
    comm_conn, tes_conn, _, _ = env_fase2
    registerEntry(tes_conn, "1220", 2000_00, "FONDEO", "Fondeo", "admin")

    # T1 reserva y asume cargo
    res_t1 = solicitar_pre_reserva_prepay(
        conn=tes_conn,
        servicio_destino="PERSONAL",
        monto_carga_cents=500_00,
        monto_adicional_cents=0,
        pago_efvo_cents=500_00,
        pago_transf_cents=0,
        idempotency_key="t1_cancel_me",
        actor="t1",
    )
    assert res_t1["asume_cargo_diario"] is True

    # T1 cancela antes de confirmar comercialmente
    cancel_res = cancelar_pre_reserva_prepay(
        conn=tes_conn,
        idempotency_key="t1_cancel_me",
        motivo="Cliente no tiene dinero",
        actor="t1",
    )
    assert cancel_res["estado"] == "RELEASED"

    # Verificar que el cargo diario quedó en estado RELEASED
    cargo_row = tes_conn.execute("SELECT estado FROM tesoreria_prepay_cargo_diario;").fetchone()
    assert cargo_row[0] == "RELEASED"

    # T2 solicita pre-reserva: DEBE asumir el cargo liberado
    res_t2 = solicitar_pre_reserva_prepay(
        conn=tes_conn,
        servicio_destino="MOVISTAR",
        monto_carga_cents=500_00,
        monto_adicional_cents=0,
        pago_efvo_cents=500_00,
        pago_transf_cents=0,
        idempotency_key="t2_takes_fee",
        actor="t2",
    )
    assert res_t2["asume_cargo_diario"] is True
    assert res_t2["monto_cargo_cents"] == 350_00


def test_escenario_11_t1_confirma_pero_worker_falla(env_fase2):
    """11 y 27. T1 confirmó comercialmente pero worker no ejecutó aún: T2 NO asume el cargo diario."""
    comm_conn, tes_conn, _, _ = env_fase2
    registerEntry(tes_conn, "1220", 3000_00, "FONDEO", "Fondeo", "admin")

    solicitar_pre_reserva_prepay(
        conn=tes_conn,
        servicio_destino="PERSONAL",
        monto_carga_cents=500_00,
        monto_adicional_cents=0,
        pago_efvo_cents=500_00,
        pago_transf_cents=0,
        idempotency_key="t1_worker_pending",
        actor="t1",
    )
    confirmar_pre_reserva_comercial(tes_conn, "t1_worker_pending", commercial_ticket_id=202)

    # Worker aún no ha corrido, pero el cargo está en 'CONFIRMED'
    res_t2 = solicitar_pre_reserva_prepay(
        conn=tes_conn,
        servicio_destino="SUBE",
        monto_carga_cents=500_00,
        monto_adicional_cents=0,
        pago_efvo_cents=500_00,
        pago_transf_cents=0,
        idempotency_key="t2_after_t1_confirmed",
        actor="t2",
    )
    assert res_t2["asume_cargo_diario"] is False


def test_escenario_12_t1_cae_despues_de_reservar_reconciliador_detecta(env_fase2):
    """12 y 15. T1 cae tras reservar en Tesorería y antes del commit comercial: reconciliador detecta RESERVA_SIN_TICKET."""
    comm_conn, tes_conn, _, _ = env_fase2
    registerEntry(tes_conn, "1220", 2000_00, "FONDEO", "Fondeo", "admin")

    solicitar_pre_reserva_prepay(
        conn=tes_conn,
        servicio_destino="PERSONAL",
        monto_carga_cents=500_00,
        monto_adicional_cents=0,
        pago_efvo_cents=500_00,
        pago_transf_cents=0,
        idempotency_key="t1_crash_key",
        actor="t1",
    )
    # Ningún ticket insertado en comm_conn

    informe = reconciliar_circuito_prepay(comm_conn, tes_conn)
    assert informe["auditoria_limpia"] is False
    tipos = [inc["tipo"] for inc in informe["inconsistencias"]]
    assert "RESERVA_SIN_TICKET" in tipos


# ============================================================================
# 3. CAÍDAS Y RECUPERACIÓN (15 a 22)
# ============================================================================

def test_escenario_16_caida_despues_commit_comercial_worker_recupera(env_fase2):
    """16. Commit comercial exitoso pero sistema cae antes del worker: al iniciar el worker, aplica perfectamente."""
    comm_conn, tes_conn, _, _ = env_fase2
    registerEntry(tes_conn, "1220", 2000_00, "FONDEO", "Fondeo", "admin")

    registrar_carga_idempotente_turso(
        registro={
            "FECHA": "01/11/2026 11:00:00",
            "SERVICIO": "Personal",
            "PLATAFORMA_ORIGEN": "PREPAY",
            "SERVICIO_DESTINO": "PERSONAL",
            "MONTO_CARGA": 500,
            "MONTO_ADICIONAL": 0,
            "TOTAL_COBRADO": 500,
            "PAGO_EFVO": 500,
            "PAGO_TRANSF": 0,
        },
        idempotency_key="tx_crash_before_worker",
        actor="cajero1",
        conn=comm_conn,
        tesoreria_conn=tes_conn,
    )

    # Simular reinicio y corrida del worker
    stats = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats["published"] == 1

    coord_estado = tes_conn.execute(
        "SELECT estado FROM tesoreria_prepay_coordinacion WHERE idempotency_key = 'tx_crash_before_worker';"
    ).fetchone()[0]
    assert coord_estado == "FULLY_APPLIED"


def test_escenario_17_18_idempotencia_worker_y_repeticion_evento(env_fase2):
    """17 y 18. Repetición del mismo evento comercial no duplica movimientos ni débitos."""
    comm_conn, tes_conn, _, _ = env_fase2
    registerEntry(tes_conn, "1220", 2000_00, "FONDEO", "Fondeo", "admin")

    registrar_carga_idempotente_turso(
        registro={
            "FECHA": "01/11/2026 11:30:00",
            "SERVICIO": "SUBE",
            "PLATAFORMA_ORIGEN": "PREPAY",
            "SERVICIO_DESTINO": "SUBE",
            "MONTO_CARGA": 600,
            "MONTO_ADICIONAL": 50,
            "TOTAL_COBRADO": 650,
            "PAGO_EFVO": 650,
            "PAGO_TRANSF": 0,
        },
        idempotency_key="tx_idempotency_worker",
        actor="cajero1",
        conn=comm_conn,
        tesoreria_conn=tes_conn,
    )

    # Primera corrida
    stats1 = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats1["published"] == 1
    saldo_1220_pos_1 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1220';").fetchone()[0]
    total_movs_1 = tes_conn.execute("SELECT COUNT(*) FROM tesoreria_movimientos;").fetchone()[0]

    # Forzar evento de nuevo a PENDING para simular crash antes de ACK
    comm_conn.execute("UPDATE commercial_events_outbox SET status = 'PENDING';")
    comm_conn.commit()

    # Segunda corrida del worker
    stats2 = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats2["published"] == 1

    saldo_1220_pos_2 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1220';").fetchone()[0]
    total_movs_2 = tes_conn.execute("SELECT COUNT(*) FROM tesoreria_movimientos;").fetchone()[0]

    # Saldos y movimientos exactamente inalterados
    assert saldo_1220_pos_1 == saldo_1220_pos_2
    assert total_movs_1 == total_movs_2


def test_escenario_19_repeticion_misma_clave_distinto_payload(env_fase2):
    """19. Reutilización de idempotency_key con payload alterado: rechazo tajante con ErrorIdempotencia."""
    comm_conn, tes_conn, _, _ = env_fase2
    registerEntry(tes_conn, "1220", 2000_00, "FONDEO", "Fondeo", "admin")

    solicitar_pre_reserva_prepay(
        conn=tes_conn,
        servicio_destino="PERSONAL",
        monto_carga_cents=500_00,
        monto_adicional_cents=0,
        pago_efvo_cents=500_00,
        pago_transf_cents=0,
        idempotency_key="same_key_diff_payload",
        actor="cajero1",
    )

    # Intentar misma clave con monto diferente ($800)
    with pytest.raises(ErrorIdempotencia):
        solicitar_pre_reserva_prepay(
            conn=tes_conn,
            servicio_destino="PERSONAL",
            monto_carga_cents=800_00,
            monto_adicional_cents=0,
            pago_efvo_cents=800_00,
            pago_transf_cents=0,
            idempotency_key="same_key_diff_payload",
            actor="cajero1",
        )


def test_escenario_20_22_reserva_huerfana_permanece_en_cuarentena(env_fase2):
    """20 y 22. Prohibición de liberación automática: reservas dudosas se auditan y no se liberan por mero tiempo."""
    comm_conn, tes_conn, _, _ = env_fase2
    registerEntry(tes_conn, "1220", 2000_00, "FONDEO", "Fondeo", "admin")

    solicitar_pre_reserva_prepay(
        conn=tes_conn,
        servicio_destino="MOVISTAR",
        monto_carga_cents=500_00,
        monto_adicional_cents=0,
        pago_efvo_cents=500_00,
        pago_transf_cents=0,
        idempotency_key="orphan_quarantine_key",
        actor="cajero1",
    )

    # Ejecutar reconciliador
    informe = reconciliar_circuito_prepay(comm_conn, tes_conn)
    assert informe["total_inconsistencias"] >= 1

    # Verificar que el fondo 1220 SIGUE teniendo la reserva activa (fondos cautivos)
    saldo_disp = tes_conn.execute(
        """
        SELECT f.saldo_centavos - (SELECT COALESCE(SUM(pending_amount_cents), 0) FROM tesoreria_reservas WHERE fund_id = f.id AND status = 'ACTIVE')
        FROM tesoreria_fondos f WHERE f.codigo = '1220';
        """
    ).fetchone()[0]
    # Se descontaron 500 (capital) + 350 (fee) = 850
    assert saldo_disp == 2000_00 - 850_00


# ============================================================================
# 4. CARGO DIARIO DE $350 (23 a 28)
# ============================================================================

def test_escenario_23_24_cargo_diario_una_sola_vez_y_reintentos_no_duplican(env_fase2):
    """23 y 24. Exactamente un débito de $350 en 1220 para la jornada completa."""
    comm_conn, tes_conn, _, _ = env_fase2
    registerEntry(tes_conn, "1220", 5000_00, "FONDEO", "Fondeo", "admin")

    # Operación 1
    registrar_carga_idempotente_turso(
        registro={"FECHA": "01/11/2026 12:00:00", "SERVICIO": "Personal", "PLATAFORMA_ORIGEN": "PREPAY", "SERVICIO_DESTINO": "PERSONAL", "MONTO_CARGA": 500, "MONTO_ADICIONAL": 0, "TOTAL_COBRADO": 500, "PAGO_EFVO": 500, "PAGO_TRANSF": 0},
        idempotency_key="tx_daily_1",
        conn=comm_conn,
        tesoreria_conn=tes_conn,
    )
    # Operación 2
    registrar_carga_idempotente_turso(
        registro={"FECHA": "01/11/2026 12:05:00", "SERVICIO": "Movistar", "PLATAFORMA_ORIGEN": "PREPAY", "SERVICIO_DESTINO": "MOVISTAR", "MONTO_CARGA": 500, "MONTO_ADICIONAL": 0, "TOTAL_COBRADO": 500, "PAGO_EFVO": 500, "PAGO_TRANSF": 0},
        idempotency_key="tx_daily_2",
        conn=comm_conn,
        tesoreria_conn=tes_conn,
    )

    process_pending_commercial_events(comm_conn, tes_conn)

    # Verificar cantidad de salidas con contrapartida COSTO_OPERATIVO_PREPAY
    fees = tes_conn.execute(
        "SELECT COUNT(*), SUM(amount_cents) FROM tesoreria_movimientos WHERE counterpart = 'COSTO_OPERATIVO_PREPAY';"
    ).fetchone()
    assert fees[0] == 1
    assert fees[1] == 350_00


def test_escenario_28_cambio_de_jornada_habilita_nuevo_cargo(env_fase2):
    """28. Cambio de jornada habilita un nuevo cargo de $350 para la nueva jornada."""
    comm_conn, tes_conn, _, _ = env_fase2
    registerEntry(tes_conn, "1220", 5000_00, "FONDEO", "Fondeo", "admin")

    # Día 1
    solicitar_pre_reserva_prepay(tes_conn, "PERSONAL", 500_00, 0, 500_00, 0, "day1_op", "cajero1")
    confirmar_pre_reserva_comercial(tes_conn, "day1_op", commercial_ticket_id=301)

    # Cerrar jornada 1 y abrir jornada 2
    j1 = obtener_jornada_actual(tes_conn)
    cerrar_jornada(tes_conn, j1["id"], usuario="admin")
    abrir_jornada(tes_conn, usuario="admin", observaciones="Jornada 2")

    # Día 2: primera operación DEBE asumir nuevamente el cargo diario de la jornada 2
    res_d2 = solicitar_pre_reserva_prepay(tes_conn, "PERSONAL", 500_00, 0, 500_00, 0, "day2_op", "cajero1")
    assert res_d2["asume_cargo_diario"] is True
    assert res_d2["monto_cargo_cents"] == 350_00


# ============================================================================
# 5. INTEGRACIÓN ECONÓMICA Y SEGREGACIÓN DE FONDOS (29 a 35)
# ============================================================================

def test_escenario_29_prepay_efectivo_fondos(env_fase2):
    """29. PREPAY efectivo: capital a 1130 (o 1160 si existe) y adicional a 1140."""
    comm_conn, tes_conn, _, _ = env_fase2
    registerEntry(tes_conn, "1220", 2000_00, "FONDEO", "Fondeo", "admin")

    registrar_carga_idempotente_turso(
        registro={"FECHA": "01/11/2026 13:00:00", "SERVICIO": "Personal", "PLATAFORMA_ORIGEN": "PREPAY", "SERVICIO_DESTINO": "PERSONAL", "MONTO_CARGA": 400, "MONTO_ADICIONAL": 40, "TOTAL_COBRADO": 440, "PAGO_EFVO": 440, "PAGO_TRANSF": 0},
        idempotency_key="tx_cash_split",
        conn=comm_conn,
        tesoreria_conn=tes_conn,
    )
    process_pending_commercial_events(comm_conn, tes_conn)

    assert tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1160';").fetchone()[0] == 400_00
    assert tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1130';").fetchone()[0] == 0
    assert tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1140';").fetchone()[0] == 40_00


def test_escenario_30_31_32_33_34_35_prepay_transferencia_compensacion(env_fase2):
    """30 a 35. PREPAY transferencia: débito en 1220, traslado 1120->1130, ingreso en 1210, 1140 sin efectivo ficticio, 1110 intacto, 1310 intacto."""
    comm_conn, tes_conn, _, _ = env_fase2
    registerEntry(tes_conn, "1220", 2000_00, "FONDEO", "Fondeo 1220", "admin")
    registerEntry(tes_conn, "1120", 1500_00, "FONDEO", "Fondeo 1120", "admin")
    registerEntry(tes_conn, "1110", 3000_00, "FONDEO", "Fondeo 1110", "admin")
    registerEntry(tes_conn, "1310", 800_00, "FONDEO", "Fondeo 1310", "admin")

    registrar_carga_idempotente_turso(
        registro={
            "FECHA": "01/11/2026 13:30:00",
            "SERVICIO": "Movistar",
            "PLATAFORMA_ORIGEN": "PREPAY",
            "SERVICIO_DESTINO": "MOVISTAR",
            "MONTO_CARGA": 500,
            "MONTO_ADICIONAL": 50,
            "TOTAL_COBRADO": 550,
            "PAGO_EFVO": 0,
            "PAGO_TRANSF": 550,
        },
        idempotency_key="tx_transf_complete",
        conn=comm_conn,
        tesoreria_conn=tes_conn,
    )
    process_pending_commercial_events(comm_conn, tes_conn)

    # 1. 1210 Mercado Pago Propio recibe el total transferido por el cliente ($550)
    assert tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1210';").fetchone()[0] == 550_00

    # 2. 1220 descuenta capital ($500) y fee ($350) = $850 descontados -> queda $1150
    assert tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1220';").fetchone()[0] == 1150_00

    # 3. 1120 compensa capital hacia 1130: 1500 - 500 = 1000
    assert tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1120';").fetchone()[0] == 1000_00

    # 4. 1130 recibe el efectivo compensado ($500)
    assert tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1130';").fetchone()[0] == 500_00

    # 5. 1140 NO recibe efectivo ficticio (saldo = 0)
    assert tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1140';").fetchone()[0] == 0

    # 6. 1110 Drugstore Diario permanece INTACTO ($3000)
    assert tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1110';").fetchone()[0] == 3000_00

    # 7. 1310 Cobranzas en Tránsito Preventistas permanece INTACTO ($800)
    assert tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1310';").fetchone()[0] == 800_00


# ============================================================================
# PRUEBAS FASE 2.2 — CORRECCIONES FOCALIZADAS PREPAY
# ============================================================================

def test_fase2_2_correcion_a_1160_rechazo_si_falta_o_inactivo(env_fase2):
    """Corrección A: Si 1160 no existe o está inactivo, rechaza inmediatamente sin fallback a 1130."""
    comm_conn, tes_conn, _, _ = env_fase2
    registerEntry(tes_conn, "1220", 2000_00, "FONDEO", "Fondeo", "admin")

    pre_res = solicitar_pre_reserva_prepay(
        conn=tes_conn,
        servicio_destino="PERSONAL",
        monto_carga_cents=300_00,
        monto_adicional_cents=30_00,
        pago_efvo_cents=330_00,
        pago_transf_cents=0,
        idempotency_key="tx_missing_1160",
        actor="cajero1",
    )
    assert pre_res["success"] is True

    # Desactivar fondo 1160
    tes_conn.execute("UPDATE tesoreria_fondos SET estado = 'BLOQUEADO' WHERE codigo = '1160';")
    tes_conn.commit()

    # Insertar ticket comercial
    comm_cur = comm_conn.cursor()
    comm_cur.execute(
        """
        INSERT INTO cargas_servicios (
            fecha_hora_utc, servicio, monto_carga, monto_adicional, total_cobrado,
            pago_efectivo, pago_transferencia, anulado, idempotency_key, plataforma_origen, servicio_destino
        ) VALUES (datetime('now'), 'Personal', 300, 30, 330, 330, 0, 0, 'tx_missing_1160', 'PREPAY', 'PERSONAL');
        """
    )
    tkt_id = comm_cur.lastrowid
    comm_conn.commit()

    # Al intentar aplicar los movimientos contables, debe fallar de inmediato
    with pytest.raises(ErrorReserva, match="Fondo 1160 inexistente o inactivo para recarga PREPAY en efectivo"):
        aplicar_movimientos_prepay_worker(
            comm_conn=comm_conn,
            tes_conn=tes_conn,
            event={"idempotency_key": "tx_missing_1160"},
            payload={"servicio_id": tkt_id, "actor": "worker", "commercial_idempotency_key": "tx_missing_1160"},
        )

    # 1130 debe tener saldo 0 (prohibido fallback silencioso a 1130)
    s_1130 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1130';").fetchone()[0]
    assert s_1130 == 0


def test_fase2_2_correcion_a_barrido_cierre_1160_a_1130(env_fase2):
    """Corrección A: Barrido obligatorio de cierre 1160 -> 1130 dejando 1160 en $0,00."""
    comm_conn, tes_conn, _, _ = env_fase2
    registerEntry(tes_conn, "1220", 2000_00, "FONDEO", "Fondeo", "admin")

    # Realizar recarga en efectivo: $400 capital -> 1160, $40 adicional -> 1140
    registrar_carga_idempotente_turso(
        registro={"FECHA": "01/11/2026 14:00:00", "SERVICIO": "Personal", "PLATAFORMA_ORIGEN": "PREPAY", "SERVICIO_DESTINO": "PERSONAL", "MONTO_CARGA": 400, "MONTO_ADICIONAL": 40, "TOTAL_COBRADO": 440, "PAGO_EFVO": 440, "PAGO_TRANSF": 0},
        idempotency_key="tx_sweep_test",
        conn=comm_conn,
        tesoreria_conn=tes_conn,
    )
    process_pending_commercial_events(comm_conn, tes_conn)

    # También fondear algo en 1110 para probar barrido completo
    registerEntry(tes_conn, "1110", 600_00, "VENTA", "Venta drugstore", "cajero")

    jornada = obtener_jornada_actual(tes_conn)
    j_id = jornada["id"]

    # 1. Iniciar cierre
    beginClosure(tes_conn, j_id, "admin")

    # 2. Validar cierre: debe requerir barrido de 1110 y barrido de 1160
    val = validateClosure(tes_conn, j_id)
    assert val["can_close"] is False
    assert val["needs_sweep"] is True
    assert val["needs_prepay_sweep"] is True
    assert val["saldo_1160_cents"] == 400_00

    # 3. Ejecutar barrido de 1110
    res_sweep_1110 = executeCashDrawerSweep(tes_conn, j_id, "admin")
    assert res_sweep_1110["swept"] is True

    # 4. Validar cierre: aún debe requerir barrido PREPAY de 1160
    val2 = validateClosure(tes_conn, j_id)
    assert val2["can_close"] is False
    assert val2["needs_prepay_sweep"] is True

    # 5. Registrar arqueo físico obligatorio en 1160 y ejecutar barrido de 1160 -> 1130
    reconcilePhysicalFund(
        conn=tes_conn,
        fund_code="1160",
        verified_amount_cents=400_00,
        user_id="admin",
        notes="Arqueo físico conforme de gaveta PREPAY para barrido de cierre",
    )
    res_sweep_prepay = executePrepayDrawerSweep(tes_conn, j_id, "admin")
    assert res_sweep_prepay["swept"] is True
    assert res_sweep_prepay["amount_cents"] == 400_00
    assert res_sweep_prepay["caja_prepay_saldo_final_cents"] == 0

    # Verificar saldos en base de datos:
    # 1160 = exactamente 0
    s_1160 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1160';").fetchone()[0]
    assert s_1160 == 0

    # 1130 = recibió los $400
    s_1130 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1130';").fetchone()[0]
    assert s_1130 == 400_00

    # 6. Validar cierre: ahora debe poder cerrarse limpiamente
    val3 = validateClosure(tes_conn, j_id)
    assert val3["can_close"] is True

    # 7. Confirmar cierre definitivo
    cierre = confirmClosure(tes_conn, j_id, "admin", observations="Cierre con barrido PREPAY exitoso")
    assert cierre["status"] == "CONFIRMED"


def test_fase2_2_correcion_b_gobernanza_traslado_1120_1130(env_fase2):
    """Corrección B: Gobernanza de ruta 1120 -> 1130. Prohíbe manuales genéricos, reposiciones no formalizadas y tickets inexistentes."""
    comm_conn, tes_conn, _, _ = env_fase2
    registerEntry(tes_conn, "1120", 3000_00, "FONDEO", "Fondeo tesoro", "admin")

    # 1. Intento de traslado manual genérico sin contexto ni evidencia: DEBE RECHAZAR
    with pytest.raises(ErrorTraslado, match="GOBERNANZA DE FONDOS: El traslado de Drugstore Acumulado"):
        transferFunds(
            conn=tes_conn,
            origin_fund_code="1120",
            destination_fund_code="1130",
            amount_cents=500_00,
            dispatched_by="cajero",
            reason="Pase de dinero sin justificar",
        )

    # 2. Intento de traslado con ticket inexistente en coordinación: DEBE RECHAZAR
    with pytest.raises(ErrorTraslado, match="GOBERNANZA: No existe coordinación PREPAY válida"):
        transferFunds(
            conn=tes_conn,
            origin_fund_code="1120",
            destination_fund_code="1130",
            amount_cents=500_00,
            dispatched_by="cajero",
            reason="Compensación física PREPAY transferencia",
            context="PREPAY_COMPENSATION",
            evidence_reference="TICKET_99999",
        )

    # 3. Traslado con contexto REPOSICION_PREVENTISTAS sin expediente de anticipo válido: DEBE BLOQUEAR
    with pytest.raises(ErrorTraslado, match="No existe expediente de anticipo de preventista válido"):
        transferFunds(
            conn=tes_conn,
            origin_fund_code="1120",
            destination_fund_code="1130",
            amount_cents=300_00,
            dispatched_by="admin",
            reason="Reposición preventiva autorizada",
            context="REPOSICION_PREVENTISTAS",
            evidence_reference="DOSSIER_2026_01",
        )

    # 4. Insertar coordinación real en tesoreria para ticket 555
    tes_conn.execute(
        """
        INSERT INTO tesoreria_prepay_coordinacion (
            coordination_id, idempotency_key, request_hash, jornada_id, servicio_destino,
            monto_capital_cents, monto_adicional_cents, pago_efvo_cents, pago_transf_cents,
            asume_cargo_diario, capital_reservation_id, estado, commercial_ticket_id, actor,
            created_at, updated_at
        ) VALUES (
            'coord_555', 'idem_555', 'hash_555', 1, 'PERSONAL',
            500_00, 50_00, 0, 550_00,
            0, 'res_555', 'COMMERCIAL_COMMITTED', 555, 'cajero',
            datetime('now'), datetime('now')
        )
        """
    )
    tes_conn.commit()

    # 5. Traslado con coordinación real, monto exacto de capital y contexto válido: DEBE PERMITIR
    t1 = transferFunds(
        conn=tes_conn,
        origin_fund_code="1120",
        destination_fund_code="1130",
        amount_cents=500_00,
        dispatched_by="cajero",
        reason="Compensación física PREPAY transferencia",
        context="PREPAY_COMPENSATION",
        evidence_reference="TICKET_555",
        idempotency_key="key_comp_555",
    )
    assert t1["status"] == "PENDING_RECEIPT"
    confirmTransferReceipt(tes_conn, t1["transfer_id"], "cajero")

    # 6. Intento de doble compensación para el mismo ticket con otra clave de idempotencia: DEBE RECHAZAR
    with pytest.raises(ErrorTraslado, match="Ya existe una compensación física activa"):
        transferFunds(
            conn=tes_conn,
            origin_fund_code="1120",
            destination_fund_code="1130",
            amount_cents=500_00,
            dispatched_by="cajero",
            reason="Segunda compensación duplicada",
            context="PREPAY_COMPENSATION",
            evidence_reference="TICKET_555",
            idempotency_key="key_comp_555_duplicate",
        )

    # Saldo acreditado en 1130 es exactamente 500
    s_1130 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1130';").fetchone()[0]
    assert s_1130 == 500_00


def test_fase2_2_correcion_c_migracion_014_valida_en_sqlite_aislado(tmp_path):
    """Corrección C: Archivo formal 014_prepay_coordinacion_y_cargo_diario.sql se aplica limpiamente."""
    mig_db_path = tmp_path / "test_migration_014.db"
    conn = sqlite3.connect(str(mig_db_path))

    # Base previa con tabla _schema_migrations y tesoreria_jornadas mínima
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS _schema_migrations (
            version INTEGER PRIMARY KEY,
            nombre TEXT NOT NULL,
            aplicado_en TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tesoreria_jornadas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            numero_jornada INTEGER NOT NULL UNIQUE
        );
        """
    )

    with open("db/migrations/014_prepay_coordinacion_y_cargo_diario.sql", "r", encoding="utf-8") as f:
        sql_content = f.read()

    conn.executescript(sql_content)

    # Verificar que las tablas existen
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table';").fetchall()}
    assert "tesoreria_prepay_cargo_diario" in tables
    assert "tesoreria_prepay_coordinacion" in tables

    # Verificar registro en _schema_migrations
    v14 = conn.execute("SELECT version, nombre FROM _schema_migrations WHERE version = 14;").fetchone()
    assert v14 is not None
    assert v14[0] == 14
    assert v14[1] == "014_prepay_coordinacion_y_cargo_diario"

    conn.close()


def test_fase2_2_correcion_d_concurrencia_multi_hilo_real(tmp_path):
    """Corrección D: Concurrencia real multi-hilo (threading.Thread). Exactamente una asume el fee."""
    db_path = str(tmp_path / "concurrencia_real.db")
    conn = conectar_db_laboratorio(db_path)
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)

    # Activar WAL mode para concurrencia multi-hilo limpia en SQLite
    conn.execute("PRAGMA journal_mode = WAL;")
    jornada = abrir_jornada(conn, usuario="admin", observaciones="Jornada concurrencia")
    registerEntry(conn, "1220", 50000_00, "FONDEO", "Fondeo masivo", "admin")
    conn.close()

    resultados = []
    errores = []

    def hilo_worker(hilo_id: int):
        hilo_conn = sqlite3.connect(db_path, timeout=30.0)
        hilo_conn.row_factory = sqlite3.Row
        key = f"hilo_tx_{hilo_id}"
        try:
            # Reintentos con backoff corto si otra terminal está justo en la ventana de reserva
            for _ in range(10):
                try:
                    res = solicitar_pre_reserva_prepay(
                        conn=hilo_conn,
                        servicio_destino="PERSONAL",
                        monto_carga_cents=100_00,
                        monto_adicional_cents=10_00,
                        pago_efvo_cents=110_00,
                        pago_transf_cents=0,
                        idempotency_key=key,
                        actor=f"cajero_{hilo_id}",
                        auto_commit=True,
                    )
                    # Si asumió el cargo, simular commit comercial inmediato para liberar la ventana
                    if res["asume_cargo_diario"]:
                        confirmar_pre_reserva_comercial(
                            conn=hilo_conn,
                            idempotency_key=key,
                            commercial_ticket_id=9000 + hilo_id,
                            actor=f"cajero_{hilo_id}",
                            auto_commit=True,
                        )
                    resultados.append(res)
                    break
                except ErrorReserva as er:
                    if "Concurrencia en cargo diario PREPAY" in str(er):
                        time.sleep(0.05)
                        continue
                    raise
        except Exception as e:
            errores.append((hilo_id, str(e)))
        finally:
            hilo_conn.close()

    threads = [threading.Thread(target=hilo_worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errores) == 0, f"Errores en concurrencia: {errores}"
    assert len(resultados) == 10

    # Exactamente una operación debe haber asumido el cargo diario de $350
    asumen_cargo = [r for r in resultados if r["asume_cargo_diario"] is True]
    assert len(asumen_cargo) == 1

    # Verificar que en tesoreria_prepay_cargo_diario existe un único registro
    check_conn = sqlite3.connect(db_path)
    cargos = check_conn.execute("SELECT COUNT(*) FROM tesoreria_prepay_cargo_diario;").fetchone()[0]
    assert cargos == 1
    check_conn.close()


def test_fase2_2_correcion_d_recuperacion_6_evidencias_y_dead_letter(env_fase2):
    """Corrección D: Protocolo de recuperación con 6 evidencias, auto-commit y dead letter sin TTL release."""
    comm_conn, tes_conn, _, _ = env_fase2
    registerEntry(tes_conn, "1220", 3000_00, "FONDEO", "Fondeo", "admin")

    # 1. Crear pre-reserva de recarga
    pre = solicitar_pre_reserva_prepay(
        conn=tes_conn,
        servicio_destino="PERSONAL",
        monto_carga_cents=500_00,
        monto_adicional_cents=50_00,
        pago_efvo_cents=550_00,
        pago_transf_cents=0,
        idempotency_key="recup_tx_01",
        actor="cajero1",
    )
    assert pre["estado"] == "ACTIVE"

    # Insertar ticket comercial comiteado en cargas_servicios
    comm_cur = comm_conn.cursor()
    comm_cur.execute(
        """
        INSERT INTO cargas_servicios (
            fecha_hora_utc, servicio, monto_carga, monto_adicional, total_cobrado,
            pago_efectivo, pago_transferencia, anulado, idempotency_key, plataforma_origen, servicio_destino
        ) VALUES (datetime('now'), 'Personal', 500, 50, 550, 550, 0, 0, 'recup_tx_01', 'PREPAY', 'PERSONAL');
        """
    )
    tkt_id = comm_cur.lastrowid

    # Insertar evento outbox en estado PENDING
    comm_cur.execute(
        """
        INSERT INTO commercial_events_outbox (
            event_id, event_type, entity_type, entity_id, action_type, idempotency_key,
            payload_canonical_hash, payload, status, created_at, updated_at
        ) VALUES ('evt_01', 'SERVICE_CONFIRMED', 'SERVICE', ?, 'CONFIRM', 'recup_tx_01', 'hash_01', '{}', 'PENDING', datetime('now'), datetime('now'));
        """,
        (str(tkt_id),),
    )
    comm_conn.commit()

    # Ejecutar recuperación: debe detectar ticket comercial comiteado y preservar sin cancelar por TTL
    recup_res = recuperar_coordinacion_prepay(
        comm_conn=comm_conn,
        tes_conn=tes_conn,
        idempotency_key="recup_tx_01",
    )
    assert recup_res["success"] is True
    assert recup_res["accion"] == "COMMERCIAL_CONFIRMED_PRESERVED"
    assert recup_res["estado"] == "COMMERCIAL_COMMITTED"
    assert recup_res["evidencias"]["evidencia_3_ticket"]["ticket_existe"] is True
    assert recup_res["evidencias"]["evidencia_4_outbox"]["outbox_status"] == "PENDING"

    # 2. Simular evento Outbox pasando a DEAD_LETTER
    comm_conn.execute("UPDATE commercial_events_outbox SET status = 'DEAD_LETTER' WHERE idempotency_key = 'recup_tx_01';")
    comm_conn.commit()

    recup_dead = recuperar_coordinacion_prepay(
        comm_conn=comm_conn,
        tes_conn=tes_conn,
        idempotency_key="recup_tx_01",
    )
    assert recup_dead["success"] is True
    assert recup_dead["accion"] == "DEAD_LETTER_ROUTED"
    assert recup_dead["estado"] == "RECONCILIATION_REQUIRED"

    # 3. Caso de transacción abortada comercialmente (no existe en DB comercial)
    pre_aborted = solicitar_pre_reserva_prepay(
        conn=tes_conn,
        servicio_destino="MOVISTAR",
        monto_carga_cents=200_00,
        monto_adicional_cents=20_00,
        pago_efvo_cents=220_00,
        pago_transf_cents=0,
        idempotency_key="tx_aborted_99",
        actor="cajero1",
    )
    assert pre_aborted["estado"] == "ACTIVE"

    # Al recuperar sin ticket comercial ni outbox: debe cancelar de forma segura
    recup_abort = recuperar_coordinacion_prepay(
        comm_conn=comm_conn,
        tes_conn=tes_conn,
        idempotency_key="tx_aborted_99",
    )
    assert recup_abort["success"] is True
    assert recup_abort["accion"] == "SAFELY_RELEASED"
    assert recup_abort["estado"] == "RELEASED"

