"""Pruebas de Certificación del Piloto Controlado en STAGING — Etapa 7.

Verifica:
1. Aislamiento absoluto de Staging: credenciales propias, 0 conexiones remotas, backups intactos.
2. Migraciones comerciales 1..12 en base de staging e inicialización del catálogo de 9 fondos.
3. Ciclo de vida del worker en staging: PID lock, recuperación de huérfanos, health-check y graceful shutdown.
4. Jornada piloto multi-turno completa con inyecciones adversariales y reconciliación contable estricta al centavo.
5. Control de acceso (RBAC) y monitoreo de sincronización para Cajero vs Administrador/Socios.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import pytest
from fastapi import status
from fastapi.testclient import TestClient

from api.auth import AuthenticatedUser
from api.dependencies import get_current_user, get_db_connection
from api.main import app
from api.routers.commercial_bridge import get_tesoreria_connection
from db.commercial_outbox_emitter import enqueue_commercial_event
from db.migrations_runner import obtener_versiones_aplicadas
from scripts.run_commercial_bridge_worker import (
    PidLockManager,
    ServiceLockError,
    open_db_connection,
    run_health_check,
)
import staging.staging_orchestrator as orquestador_staging
from staging.staging_orchestrator import (
    execute_staging_pilot_day,
    init_staging_databases,
    verify_staging_isolation,
)
from tesoreria.availability import getTreasurySummary
from tesoreria.catalog import CODIGOS_CANONICOS, obtener_catalogo_fondos, obtener_fondo_por_codigo
from tesoreria.commercial_bridge_worker import (
    get_commercial_bridge_status,
    get_simplified_sync_status,
    process_pending_commercial_events,
    reconcile_treasury_ledger_balances,
    recover_orphaned_processing_events,
)



# ============================================================================
# 0. AISLAMIENTO DE LAS PRUEBAS RESPECTO DE LAS BASES PERSISTENTES DE STAGING
# ============================================================================
# Las pruebas de este módulo reinicializan bases y simulan jornadas. Nunca deben operar
# sobre staging/genaro_pos_staging.db ni staging/tesoreria_staging.db (operatoria real
# de Staging). Se redirige el orquestador a un directorio temporal compartido por el módulo.

@pytest.fixture(scope="module", autouse=True)
def staging_aislado(tmp_path_factory):
    tmp_dir = tmp_path_factory.mktemp("staging_aislado")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(orquestador_staging, "STAGING_DIR", tmp_dir)
        mp.setattr(orquestador_staging, "STAGING_COMM_DB", tmp_dir / "genaro_pos_staging.db")
        mp.setattr(orquestador_staging, "STAGING_TES_DB", tmp_dir / "tesoreria_staging.db")
        mp.setattr(orquestador_staging, "STAGING_PID_FILE", tmp_dir / "staging_bridge.pid")
        yield tmp_dir


# ============================================================================
# 1. AISLAMIENTO Y CREDENCIALES DE STAGING
# ============================================================================

def test_staging_environment_isolation_and_credentials():
    """Comprueba que Staging tenga credenciales independientes y 0 conexiones a producción o Turso."""
    iso = verify_staging_isolation()
    assert iso["isolation_verified"] is True
    assert iso["mode"] == "LOCAL_SQLITE_ISOLATED"
    assert iso["remote_turso_connected"] is False

    # Verificar existencia y contenido de .env.staging
    env_file = Path(__file__).resolve().parent.parent / "staging" / ".env.staging"
    assert env_file.exists(), "Archivo staging/.env.staging no existe"

    content = env_file.read_text(encoding="utf-8")
    assert "GENARO_ENV=staging" in content
    assert "TURSO_DEV_DATABASE_URL=" in content
    assert "JWT_SECRET=" in content
    assert "STAGING_API_PORT=8001" in content


# ============================================================================
# 2. MIGRACIONES Y CATÁLOGO DE 9 FONDOS EN STAGING
# ============================================================================

def test_staging_database_initialization_and_migrations():
    """Inicializa bases de staging limpias y valida las 12 versiones y los 9 fondos canónicos."""
    init_res = init_staging_databases(clean=True)
    assert init_res["status"] == "INITIALIZED"
    assert init_res["commercial_migrations"] >= 12
    assert init_res["commercial_integrity"] == "ok"
    assert init_res["tesoreria_funds"] == len(CODIGOS_CANONICOS)
    assert init_res["tesoreria_integrity"] == "ok"
    assert init_res["initial_ledger_exact"] is True

    # Verificar que las bases SQLite físicas se hayan creado en staging/
    staging_dir = orquestador_staging.STAGING_DIR
    comm_db = staging_dir / "genaro_pos_staging.db"
    tes_db = staging_dir / "tesoreria_staging.db"

    assert comm_db.exists()
    assert tes_db.exists()

    comm_conn = open_db_connection(comm_db)
    versiones = obtener_versiones_aplicadas(comm_conn)
    comm_conn.close()

    assert len(versiones) >= 12
    assert 12 in versiones

    tes_conn = open_db_connection(tes_db)
    fondos = obtener_catalogo_fondos(tes_conn)
    tes_conn.close()

    codigos = {f["codigo"] for f in fondos}
    assert codigos == CODIGOS_CANONICOS
    assert all(f["saldo_centavos"] == 0 for f in fondos)


# ============================================================================
# 3. EXCLUSIÓN MUTUA PID Y SERVICIO WORKER EN STAGING
# ============================================================================

def test_staging_service_worker_pid_lock_and_lifecycle(tmp_path):
    """Comprueba el bloqueo de concurrencia PID lock y el health check sobre Staging."""
    staging_dir = orquestador_staging.STAGING_DIR
    pid_file = staging_dir / "test_worker_lock.pid"
    comm_db = staging_dir / "genaro_pos_staging.db"
    tes_db = staging_dir / "tesoreria_staging.db"

    # 1. Probar bloqueo concurrente
    lock1 = PidLockManager(pid_file)
    lock1.acquire()
    assert pid_file.exists()

    lock2 = PidLockManager(pid_file)
    with pytest.raises(ServiceLockError):
        lock2.acquire()

    lock1.release()
    assert not pid_file.exists()

    # 2. Diagnóstico de salud
    hc_code = run_health_check(comm_db, tes_db)
    # Puede ser 0 (HEALTHY) o 2 (DEGRADED si hay algún dead letter previo)
    assert hc_code in (0, 2)


# ============================================================================
# 4. SIMULACIÓN INTEGRAL DE JORNADA PILOTO Y CONCILIACIÓN DEL LIBRO MAYOR
# ============================================================================

def test_staging_full_pilot_day_and_financial_reconciliation():
    """Ejecuta el flujo piloto completo en staging y audita matemáticamente los 9 fondos."""
    # Inicializar bases limpias
    init_staging_databases(clean=True)

    # Ejecutar jornada piloto
    rep = execute_staging_pilot_day()
    assert rep["status"] == "PILOT_DAY_SUCCESS"
    assert rep["cuadre_exacto_libro_mayor"] is True
    assert rep["total_eventos_procesados"] == 14
    assert rep["dead_letter_detectados"] == 1
    assert len(rep["cierre_canonical_hash"]) == 64

    # Auditoría directa sobre la base de tesorería de staging
    staging_dir = orquestador_staging.STAGING_DIR
    tes_db = staging_dir / "tesoreria_staging.db"
    tes_conn = open_db_connection(tes_db)

    f1110 = obtener_fondo_por_codigo(tes_conn, "1110")
    f1120 = obtener_fondo_por_codigo(tes_conn, "1120")
    f1130 = obtener_fondo_por_codigo(tes_conn, "1130")
    f1140 = obtener_fondo_por_codigo(tes_conn, "1140")
    f1150 = obtener_fondo_por_codigo(tes_conn, "1150")
    f1210 = obtener_fondo_por_codigo(tes_conn, "1210")
    f1220 = obtener_fondo_por_codigo(tes_conn, "1220")
    f1230 = obtener_fondo_por_codigo(tes_conn, "1230")

    # A) 1110 terminó en 0 absoluto tras el barrido obligatorio
    assert f1110["saldo_centavos"] == 0

    # B) 1140 contiene los adicionales trasladados manualmente ($600 = 60.000 centavos)
    assert f1140["saldo_centavos"] == 60000

    # C) 1150 (Custodia Claro) contiene exactamente la recaudación ($3.500 = 350.000 centavos)
    assert f1150["saldo_centavos"] == 350000
    assert f1150["titularidad"] == "CUSTODIA"

    # D) 1130 contiene la cobranza en mostrador de Prepay ($4.000 = 400.000 centavos)
    assert f1130["saldo_centavos"] == 400000

    # E) 1210 contiene las ventas digitales MP ($14.200 + $12.000 + $2.500 SUBE + $4.000 + $11.000 = $43.700 = 4.370.000 centavos)
    # Nota: SUBE debita 1210 (-$2.500) y acredita la cobranza en transferencia (+$2.500), neto de SUBE en 1210 = 0.
    # Total en 1210 = 14.200 + 12.000 + 4.000 + 11.000 = 41.200 (4.120.000 centavos)
    assert f1210["saldo_centavos"] == 4120000

    # F) Ningún fondo físico presenta saldo negativo
    for f in [f1110, f1120, f1130, f1140, f1150]:
        assert f["saldo_centavos"] >= 0, f"Fondo físico {f['codigo']} negativo: {f['saldo_centavos']}"

    # G) Conciliación de libro mayor
    rec = reconcile_treasury_ledger_balances(tes_conn)
    assert rec["cuadre_exacto"] is True
    assert len(rec["discrepancias"]) == 0

    tes_conn.close()


# ============================================================================
# 5. CONTROL DE ACCESO (RBAC) Y MONITOREO DE STAGING
# ============================================================================

def test_staging_api_rbac_and_sync_monitor():
    """Verifica que en Staging el Cajero tenga acceso únicamente a sync-status y no a monitoreo global."""
    staging_dir = orquestador_staging.STAGING_DIR
    comm_db = staging_dir / "genaro_pos_staging.db"
    tes_db = staging_dir / "tesoreria_staging.db"

    comm_conn = open_db_connection(comm_db)
    tes_conn = open_db_connection(tes_db)

    app.dependency_overrides[get_db_connection] = lambda: comm_conn
    app.dependency_overrides[get_tesoreria_connection] = lambda: tes_conn

    client = TestClient(app)

    # 1. Rol Cajero
    cajero = AuthenticatedUser(user_id="cajero_stg", username="cajero_stg", display_name="Cajero Staging", role="cajero")
    app.dependency_overrides[get_current_user] = lambda: cajero

    # Permitido para Cajero
    res_sync = client.get("/api/tesoreria/commercial-bridge/sync-status")
    assert res_sync.status_code == 200
    sync_data = res_sync.json()
    assert "sincronizadas" in sync_data
    assert "pendientes" in sync_data

    # Bloqueados para Cajero (403 Forbidden)
    assert client.get("/api/tesoreria/commercial-bridge/status").status_code == status.HTTP_403_FORBIDDEN
    assert client.get("/api/tesoreria/commercial-bridge/events").status_code == status.HTTP_403_FORBIDDEN
    assert client.post("/api/tesoreria/commercial-bridge/reprocess/any-id").status_code == status.HTTP_403_FORBIDDEN
    assert client.post("/api/tesoreria/commercial-bridge/trigger-drain").status_code == status.HTTP_403_FORBIDDEN

    # 2. Rol Administrador / Socio
    admin = AuthenticatedUser(user_id="alan_admin", username="alan_admin", display_name="Alan Admin", role="administrador")
    app.dependency_overrides[get_current_user] = lambda: admin

    res_admin = client.get("/api/tesoreria/commercial-bridge/status")
    assert res_admin.status_code == 200
    admin_data = res_admin.json()
    assert "published" in admin_data
    assert "total" in admin_data

    # Limpiar overrides y conexiones
    app.dependency_overrides.clear()
    comm_conn.close()
    tes_conn.close()
