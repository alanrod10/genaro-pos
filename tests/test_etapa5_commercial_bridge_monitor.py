"""Pruebas de Integración y Seguridad para Etapa 5 — Commercial Bridge Monitor y Worker Híbrido.

Verifica:
1. Diferencias de permisos entre Cajero y Administrador / Socios (Control de Acceso RBAC).
2. Métricas y actualización de estados del Monitor (/status, /events).
3. Búsqueda y filtros avanzados de eventos comerciales.
4. Reprocesamiento manual auditable de eventos fallidos o en DEAD_LETTER (reprocessed_by, reprocessed_at).
5. Recuperación de eventos huérfanos tras caídas del backend (recover_orphaned_processing_events).
6. Prevención de movimientos duplicados ante reintentos o reprocesamientos repetidos.
7. Consistencia estricta entre Outbox comercial y saldos del libro mayor de Tesorería.
8. Worker híbrido en segundo plano (CommercialBridgeBackgroundWorker, ciclo periódico y trigger inmediato).
"""

from datetime import datetime, timezone
import json
import sqlite3
import time
import pytest
from tesoreria.catalog import CODIGOS_CANONICOS
from fastapi import status
from fastapi.testclient import TestClient

from api.auth import AuthenticatedUser
from api.dependencies import get_current_user, get_db_connection
from api.main import app
from api.routers.commercial_bridge import get_tesoreria_connection
from db.commercial_outbox_emitter import enqueue_commercial_event
from db.migrations_runner import ejecutar_migraciones
from tesoreria.catalog import sembrar_catalogo_canónico
from tesoreria.commercial_bridge_worker import (
    CommercialBridgeBackgroundWorker,
    get_commercial_bridge_status,
    get_simplified_sync_status,
    list_commercial_events_detailed,
    process_pending_commercial_events,
    recover_orphaned_processing_events,
    reprocess_outbox_event,
    reconcile_treasury_ledger_balances,
)
from tesoreria.isolation import conectar_db_laboratorio
from tesoreria.jornadas import abrir_jornada
from tesoreria.movements import registerEntry
from tesoreria.schema import inicializar_esquema_laboratorio


@pytest.fixture
def env_etapa5():
    """Configura base comercial SQLite en memoria y base de Tesorería aislada."""
    comm_conn = sqlite3.connect(":memory:", check_same_thread=False)
    comm_conn.row_factory = sqlite3.Row
    comm_conn.execute("PRAGMA foreign_keys = ON;")

    # Tabla base para migraciones
    comm_conn.execute(
        """
        CREATE TABLE productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL COLLATE NOCASE,
            categoria TEXT NOT NULL DEFAULT 'Otro',
            proveedor TEXT NOT NULL DEFAULT 'General',
            unidad TEXT NOT NULL DEFAULT 'Unidad',
            costo INTEGER NOT NULL DEFAULT 0 CHECK (costo >= 0),
            precio_dia INTEGER NOT NULL CHECK (precio_dia >= 0),
            precio_noche INTEGER NOT NULL DEFAULT 0,
            activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
            fecha_actualizacion TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    ejecutar_migraciones(comm_conn)

    # Base de Tesorería aislada
    tes_conn = conectar_db_laboratorio(":memory:")
    inicializar_esquema_laboratorio(tes_conn)
    sembrar_catalogo_canónico(tes_conn, usuario="admin_lab")
    abrir_jornada(tes_conn, usuario="admin_lab", observaciones="Jornada Etapa 5")

    # Inyectar dependencias en FastAPI
    app.dependency_overrides[get_db_connection] = lambda: comm_conn
    app.dependency_overrides[get_tesoreria_connection] = lambda: tes_conn

    client = TestClient(app)

    yield comm_conn, tes_conn, client

    # Restaurar
    app.dependency_overrides.clear()
    comm_conn.close()
    tes_conn.close()


# ============================================================================
# 1. PRUEBAS DE PERMISOS Y CONTROL DE ACCESO (CAJERO VS ADMINISTRADOR/SOCIO)
# ============================================================================

def test_permisos_cajero_solo_accede_a_sync_status_simplificado(env_etapa5):
    """Verifica que un usuario Cajero solo pueda ver el estado simplificado y no detalles técnicos."""
    comm_conn, tes_conn, client = env_etapa5

    # Usuario con rol cajero
    usuario_cajero = AuthenticatedUser(
        username="cajero_turno_manana",
        display_name="Cajero 1",
        role="cajero",
    )
    app.dependency_overrides[get_current_user] = lambda: usuario_cajero

    # 1. sync-status debe responder HTTP 200 con formato simplificado
    res_sync = client.get("/api/tesoreria/commercial-bridge/sync-status")
    assert res_sync.status_code == status.HTTP_200_OK
    data_sync = res_sync.json()
    assert "sincronizadas" in data_sync
    assert "pendientes" in data_sync
    assert "requiere_atencion" in data_sync
    assert "mensaje_cajero" in data_sync
    # No debe filtrar información sensible como hashes, balances de fondos o IDs de movimiento
    assert "payload_canonical_hash" not in data_sync
    assert "saldo_centavos" not in data_sync

    # 2. Endpoints protegidos deben responder HTTP 403 Forbidden
    res_status = client.get("/api/tesoreria/commercial-bridge/status")
    assert res_status.status_code == status.HTTP_403_FORBIDDEN
    assert "Administrador o Socio" in res_status.json()["detail"]

    res_events = client.get("/api/tesoreria/commercial-bridge/events")
    assert res_events.status_code == status.HTTP_403_FORBIDDEN

    res_reproc = client.post("/api/tesoreria/commercial-bridge/reprocess/fake-uuid")
    assert res_reproc.status_code == status.HTTP_403_FORBIDDEN

    res_drain = client.post("/api/tesoreria/commercial-bridge/trigger-drain")
    assert res_drain.status_code == status.HTTP_403_FORBIDDEN


def test_permisos_administrador_y_socio_acceso_total(env_etapa5):
    """Verifica que Alan (socio) o Yilda (administrador) tengan acceso completo al monitor."""
    comm_conn, tes_conn, client = env_etapa5

    usuario_admin = AuthenticatedUser(
        username="alan",
        display_name="Alan",
        role="socio",
    )
    app.dependency_overrides[get_current_user] = lambda: usuario_admin

    res_status = client.get("/api/tesoreria/commercial-bridge/status")
    assert res_status.status_code == status.HTTP_200_OK
    assert "total" in res_status.json()

    res_events = client.get("/api/tesoreria/commercial-bridge/events")
    assert res_events.status_code == status.HTTP_200_OK
    assert "events" in res_events.json()

    res_drain = client.post("/api/tesoreria/commercial-bridge/trigger-drain")
    assert res_drain.status_code == status.HTTP_200_OK


# ============================================================================
# 2. PRUEBAS DEL MONITOR: MÉTRICAS, ESTADOS, FILTROS Y BÚSQUEDA
# ============================================================================

def test_monitor_metricas_filtros_y_busqueda(env_etapa5):
    """Verifica que los conteos de estados y los filtros de búsqueda operen correctamente."""
    comm_conn, tes_conn, client = env_etapa5

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        username="yilda", display_name="Yilda", role="administrador"
    )

    # 1. Emitir 3 eventos comerciales
    enqueue_commercial_event(
        conn=comm_conn,
        event_type="SALE_CONFIRMED",
        entity_type="TICKET",
        entity_id="TKT-101",
        action_type="ENTRY",
        idempotency_key="IDEM-TKT-101",
        payload={"ticket_id": "TKT-101", "monto_efectivo_cents": 200000},
    )
    enqueue_commercial_event(
        conn=comm_conn,
        event_type="EXPENSE_CONFIRMED",
        entity_type="EXPENSE",
        entity_id="EXP-202",
        action_type="EXIT",
        idempotency_key="IDEM-EXP-202",
        payload={"gasto_id": "EXP-202", "monto_cents": 50000, "origen_fondos": "1110"},
    )
    enqueue_commercial_event(
        conn=comm_conn,
        event_type="SERVICE_CONFIRMED",
        entity_type="SERVICE_CHARGE",
        entity_id="SRV-303",
        action_type="ENTRY",
        idempotency_key="IDEM-SRV-303",
        payload={"servicio": "PREPAY", "pago_efectivo_cents": 80000, "monto_total_cents": 80000},
    )
    comm_conn.commit()

    # Verificar métricas iniciales (3 PENDING)
    res_status = client.get("/api/tesoreria/commercial-bridge/status").json()
    assert res_status["total"] == 3
    assert res_status["pending"] == 3
    assert res_status["published"] == 0

    # 2. Drenar eventos
    process_pending_commercial_events(comm_conn, tes_conn)

    res_status2 = client.get("/api/tesoreria/commercial-bridge/status").json()
    assert res_status2["total"] == 3
    assert res_status2["published"] == 3
    assert res_status2["pending"] == 0

    # 3. Filtro por tipo de evento
    res_filtro_sale = client.get("/api/tesoreria/commercial-bridge/events?event_type=SALE").json()
    assert res_filtro_sale["total"] == 1
    assert res_filtro_sale["events"][0]["entity_id"] == "TKT-101"

    # 4. Búsqueda por entity_id
    res_busq = client.get("/api/tesoreria/commercial-bridge/events?search=EXP-202").json()
    assert res_busq["total"] == 1
    assert res_busq["events"][0]["event_type"] == "EXPENSE_CONFIRMED"


# ============================================================================
# 3. RECUPERACIÓN TRAS REINICIO DEL BACKEND (ORPHANED PROCESSING EVENTS)
# ============================================================================

def test_recuperacion_eventos_huerfanos_en_processing(env_etapa5):
    """Simula una caída del servidor dejando un evento en PROCESSING y certifica su rescate."""
    comm_conn, tes_conn, client = env_etapa5

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        username="alan", display_name="Alan", role="socio"
    )

    enqueue_commercial_event(
        conn=comm_conn,
        event_type="SALE_CONFIRMED",
        entity_type="TICKET",
        entity_id="TKT-CRASH-RESTART",
        action_type="ENTRY",
        idempotency_key="IDEM-CRASH-RESTART",
        payload={"ticket_id": "TKT-CRASH-RESTART", "monto_efectivo_cents": 150000},
    )
    comm_conn.commit()

    # Simular que el worker lo tomó y el proceso murió abruptamente hace 1 minuto
    hace_un_minuto = datetime.fromtimestamp(time.time() - 60, timezone.utc).isoformat()
    comm_conn.execute(
        """
        UPDATE commercial_events_outbox
        SET status = 'PROCESSING',
            retry_count = 1,
            updated_at = ?
        WHERE entity_id = 'TKT-CRASH-RESTART';
        """,
        (hace_un_minuto,),
    )
    comm_conn.commit()

    # El endpoint trigger-drain ejecuta recover_orphaned_processing_events y procesa
    res_drain = client.post("/api/tesoreria/commercial-bridge/trigger-drain")
    assert res_drain.status_code == status.HTTP_200_OK

    row = comm_conn.execute(
        "SELECT status, tesoreria_movement_id FROM commercial_events_outbox WHERE entity_id = 'TKT-CRASH-RESTART';"
    ).fetchone()
    assert row["status"] == "PUBLISHED"
    assert row["tesoreria_movement_id"] is not None

    # El saldo en 1110 debe estar acreditado exactamente
    assert tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1110';").fetchone()[0] == 150000


# ============================================================================
# 4. REPROCESAMIENTO MANUAL AUDITABLE DE EVENTOS EN DEAD_LETTER
# ============================================================================

def test_reprocesamiento_manual_auditable_de_dead_letter(env_etapa5):
    """Verifica que un Administrador pueda reprocesar un DEAD_LETTER registrando auditoría."""
    comm_conn, tes_conn, client = env_etapa5

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        username="alan", display_name="Alan", role="socio"
    )

    # Emitir un gasto sin saldo que agotará reintentos y pasará a DEAD_LETTER
    enqueue_commercial_event(
        conn=comm_conn,
        event_type="EXPENSE_CONFIRMED",
        entity_type="EXPENSE",
        entity_id="EXP-SIN-SALDO",
        action_type="EXIT",
        idempotency_key="IDEM-EXP-DEAD",
        payload={"gasto_id": "EXP-SIN-SALDO", "monto_cents": 300000, "origen_fondos": "1110"},
    )
    comm_conn.commit()

    # Forzar a DEAD_LETTER simulando agotamiento de 5 reintentos
    comm_conn.execute(
        """
        UPDATE commercial_events_outbox
        SET status = 'DEAD_LETTER',
            retry_count = 5,
            error_message = 'Saldo insuficiente en fondo 1110'
        WHERE entity_id = 'EXP-SIN-SALDO';
        """
    )
    comm_conn.commit()

    cur = comm_conn.execute("SELECT event_id FROM commercial_events_outbox WHERE entity_id = 'EXP-SIN-SALDO';")
    event_id = cur.fetchone()["event_id"]

    # Fondeamos 1110 con saldo suficiente para que el reprocesamiento tenga éxito
    registerEntry(conn=tes_conn, fund_code="1110", amount_cents=500000, counterpart="FONDEO", reason="Base", created_by="admin")

    # Reprocesar mediante API
    res_reproc = client.post(f"/api/tesoreria/commercial-bridge/reprocess/{event_id}")
    assert res_reproc.status_code == status.HTTP_200_OK
    data = res_reproc.json()
    assert data["event_id"] == event_id
    assert data["status"] == "PUBLISHED"
    assert data["authorized_by"] == "alan"
    assert data["reprocessed_at"] is not None

    # Verificar que en la base quedó asentada la auditoría de reprocesamiento
    cur_check = comm_conn.execute(
        "SELECT status, reprocessed_by, reprocessed_at, tesoreria_movement_id FROM commercial_events_outbox WHERE event_id = ?;",
        (event_id,),
    )
    row_check = cur_check.fetchone()
    assert row_check["status"] == "PUBLISHED"
    assert row_check["reprocessed_by"] == "alan"
    assert row_check["reprocessed_at"] is not None
    assert row_check["tesoreria_movement_id"] is not None


# ============================================================================
# 5. RECONCILIACIÓN MATEMÁTICA Y CONSISTENCIA CON LIBRO MAYOR
# ============================================================================

def test_consistencia_outbox_y_libro_mayor(env_etapa5):
    """Certifica que los eventos confirmados coincidan exactamente con los asientos del libro mayor."""
    comm_conn, tes_conn, client = env_etapa5

    # 1. Venta efectivo $3.000
    enqueue_commercial_event(
        conn=comm_conn,
        event_type="SALE_CONFIRMED",
        entity_type="TICKET",
        entity_id="TKT-M-1",
        action_type="ENTRY",
        idempotency_key="IDEM-M-1",
        payload={"ticket_id": "TKT-M-1", "monto_efectivo_cents": 300000},
    )
    # 2. Venta digital $2.000
    enqueue_commercial_event(
        conn=comm_conn,
        event_type="SALE_CONFIRMED",
        entity_type="TICKET",
        entity_id="TKT-M-2",
        action_type="ENTRY",
        idempotency_key="IDEM-M-2",
        payload={"ticket_id": "TKT-M-2", "monto_transferencia_cents": 200000},
    )
    comm_conn.commit()

    # Drenar
    stats = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats["published"] == 2

    # Verificar reconciliación matemática
    rec = reconcile_treasury_ledger_balances(tes_conn)
    assert rec["cuadre_exacto"] is True
    assert rec["fondos_auditados"] == len(CODIGOS_CANONICOS)  # catálogo canónico vigente (incluye 1160)
    assert rec["saldos_reconstruidos"]["1110"] == 300000
    assert rec["saldos_reconstruidos"]["1210"] == 200000

    # Segunda pasada no altera ningún saldo
    stats_segunda = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats_segunda["published"] == 0

    rec2 = reconcile_treasury_ledger_balances(tes_conn)
    assert rec2["cuadre_exacto"] is True
    assert rec2["saldos_reconstruidos"]["1110"] == 300000
    assert rec2["saldos_reconstruidos"]["1210"] == 200000


# ============================================================================
# 6. WORKER HÍBRIDO EN SEGUNDO PLANO (CICLO PERIÓDICO Y TRIGGER INMEDIATO)
# ============================================================================

def test_commercial_bridge_background_worker_hibrido(tmp_path):
    """Verifica que el CommercialBridgeBackgroundWorker ejecute drenajes automáticos y por trigger."""
    comm_db_path = str(tmp_path / "comm_lab.db")
    tes_db_path = str(tmp_path / "tes_lab.db")

    init_comm = sqlite3.connect(comm_db_path)
    init_comm.execute("PRAGMA journal_mode = WAL;")
    init_comm.execute(
        """
        CREATE TABLE productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            precio_dia INTEGER NOT NULL CHECK (precio_dia >= 0),
            activo INTEGER NOT NULL DEFAULT 1
        );
        """
    )
    ejecutar_migraciones(init_comm)
    init_comm.close()

    init_tes = conectar_db_laboratorio(tes_db_path)
    inicializar_esquema_laboratorio(init_tes)
    sembrar_catalogo_canónico(init_tes)
    abrir_jornada(init_tes, usuario="admin")
    init_tes.close()

    worker = CommercialBridgeBackgroundWorker(
        comm_conn_factory=lambda: sqlite3.connect(comm_db_path, check_same_thread=False, timeout=10.0),
        tes_conn_factory=lambda: conectar_db_laboratorio(tes_db_path),
        interval_seconds=1.0,
        close_connections_on_drain=True,
    )

    try:
        worker.start()

        # Emitir un evento desde el hilo principal con su propia conexión
        main_comm = sqlite3.connect(comm_db_path, timeout=10.0)
        main_comm.row_factory = sqlite3.Row
        enqueue_commercial_event(
            conn=main_comm,
            event_type="SALE_CONFIRMED",
            entity_type="TICKET",
            entity_id="TKT-WORKER-ASYNC",
            action_type="ENTRY",
            idempotency_key="IDEM-WORKER-ASYNC",
            payload={"ticket_id": "TKT-WORKER-ASYNC", "monto_efectivo_cents": 100000},
        )
        main_comm.commit()

        # Despertar de inmediato con trigger
        worker.trigger_immediate_drain()
        time.sleep(0.4)

        cur = main_comm.execute(
            "SELECT status, tesoreria_movement_id FROM commercial_events_outbox WHERE entity_id = 'TKT-WORKER-ASYNC';"
        )
        row = cur.fetchone()
        assert row["status"] == "PUBLISHED"
        assert row["tesoreria_movement_id"] is not None
        main_comm.close()

    finally:
        worker.stop()
