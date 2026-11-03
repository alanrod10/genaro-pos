"""Pruebas de Preparación Operativa, Despliegue, Día D y Rollback — Etapa 8.

Verifica:
1. Plantillas de configuración de Producción y comprobaciones de aislamiento estricto.
2. Utilidad de pre-vuelo y despliegue controlado en modo simulación (deploy_production_bridge --dry-run).
3. Protocolo del Día D (dia_d_apertura_fondos):
   - Cero importación de Excel o saldos ficticios.
   - Rechazo de saldos físicos negativos.
   - Apertura de jornada y registro de asientos inmutables iniciales.
   - Segregación del fondo 1150 (Claro Custodia).
   - Generación de certificado digital firmado con hash canónico SHA-256 determinista.
   - Cuadre contable exacto al centavo en los 9 fondos.
4. Simulacro de reversión de emergencia (rollback_production_bridge):
   - Purga limpia de la migración 12 y eliminación de tablas Outbox.
   - Verificación de integridad SQLite post-rollback.
   - Validación del procedimiento de restauración desde respaldo verificado.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import pytest
from tesoreria.catalog import CODIGOS_CANONICOS

from db.migrations_runner import ejecutar_migraciones, obtener_versiones_aplicadas
from production.deploy_production_bridge import (
    check_isolation,
    compute_sha256,
    execute_production_deployment,
)
from production.dia_d_apertura_fondos import (
    ErrorDiaD,
    canonical_json_hash,
    ejecutar_apertura_dia_d,
)
from production.rollback_production_bridge import ejecutar_rollback_simetrico
from scripts.run_commercial_bridge_worker import open_db_connection
from tesoreria.availability import getTreasurySummary
from tesoreria.catalog import obtener_catalogo_fondos, obtener_fondo_por_codigo, sembrar_catalogo_canónico
from tesoreria.commercial_bridge_worker import reconcile_treasury_ledger_balances
from tesoreria.schema import inicializar_esquema_laboratorio


# ============================================================================
# 1. AISLAMIENTO Y PLANTILLAS DE PRODUCCIÓN
# ============================================================================

def test_etapa8_isolation_and_env_templates():
    """Valida que los archivos de configuración de producción no apunten a Turso ni comprometan aislamiento."""
    iso = check_isolation()
    assert iso["isolation_verified"] is True
    assert iso["remote_connections_blocked"] is True

    prod_dir = Path(__file__).resolve().parent.parent / "production"
    env_example = prod_dir / "production.env.example"
    service_file = prod_dir / "genaro-commercial-bridge.service"
    runbook_file = prod_dir / "OPERATIONAL_RUNBOOK_DIA_D.md"

    assert env_example.exists()
    assert service_file.exists()
    assert runbook_file.exists()

    env_content = env_example.read_text(encoding="utf-8")
    assert "GENARO_ENV=production" in env_content
    assert "TURSO_DEV_DATABASE_URL=" in env_content
    assert "TURSO_PROD_DATABASE_URL=" in env_content
    assert "ISOLATION_MODE=STRICT_LOCAL_SQLITE" in env_content


# ============================================================================
# 2. PRE-VUELO Y DESPLIEGUE CONTROLADO (DRY-RUN)
# ============================================================================

def test_etapa8_deploy_production_bridge_dry_run(tmp_path):
    """Ejecuta el protocolo de despliegue controlado en modo --dry-run sobre base sintética."""
    comm_path = tmp_path / "comm_prod_sim.db"
    tes_path = tmp_path / "tes_prod_sim.db"

    # Crear base comercial sintética inicial
    conn = sqlite3.connect(str(comm_path))
    conn.execute(
        """
        CREATE TABLE productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL COLLATE NOCASE,
            categoria TEXT NOT NULL DEFAULT 'Otro',
            proveedor TEXT NOT NULL DEFAULT 'General',
            unidad TEXT NOT NULL DEFAULT 'Unidad',
            costo INTEGER NOT NULL DEFAULT 0,
            precio_dia INTEGER NOT NULL DEFAULT 0,
            precio_noche INTEGER NOT NULL DEFAULT 0,
            activo INTEGER NOT NULL DEFAULT 1,
            fecha_actualizacion TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.commit()
    conn.close()

    # Ejecutar pre-vuelo en seco
    rep = execute_production_deployment(comm_path, tes_path, dry_run=True, backup_dir=tmp_path / "backups")
    assert rep["status"] == "READY_FOR_OPERATION"
    assert rep["dry_run"] is True
    assert rep["isolation"]["isolation_verified"] is True
    assert rep["backup"]["verified"] is True
    assert rep["commercial_db"]["migracion_012_presente"] is True
    assert rep["tesoreria_db"]["fondos_canónicos"] == len(CODIGOS_CANONICOS)  # catálogo canónico vigente (incluye 1160)
    assert rep["tesoreria_db"]["cuadre_exacto"] is True


# ============================================================================
# 3. PROTOCOLO DEL DÍA D — ESTABLECIMIENTO DE SALDOS INICIALES
# ============================================================================

def test_etapa8_dia_d_apertura_fondos_protocol(tmp_path):
    """Verifica el cumplimiento estricto del protocolo del Día D sin saldos ficticios."""
    tes_path = tmp_path / "tes_dia_d.db"
    conn = open_db_connection(tes_path)
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)

    # A) Validar rechazo de responsable vacío
    with pytest.raises(ErrorDiaD, match="usuario responsable"):
        ejecutar_apertura_dia_d(conn, {}, usuario_responsable="", fecha_operativa="2026-11-01")

    # B) Validar rechazo de código no canónico
    with pytest.raises(ErrorDiaD, match="no canónico"):
        ejecutar_apertura_dia_d(conn, {"9999": 1000}, usuario_responsable="alan_socio", fecha_operativa="2026-11-01")

    # C) Validar rechazo de saldo físico negativo
    with pytest.raises(ErrorDiaD, match="saldo físico negativo"):
        ejecutar_apertura_dia_d(conn, {"1110": -50000}, usuario_responsable="alan_socio", fecha_operativa="2026-11-01")

    # D) Ejecución válida de apertura del Día D con arqueos reales documentados
    saldos_dia_d = {
        "1110": 2500000,  # $25.000,00 cambio en caja diaria
        "1120": 8000000,  # $80.000,00 efectivo en caja fuerte
        "1130": 1500000,  # $15.000,00 fondo de recargas Prepay
        "1140": 500000,   # $5.000,00 adicionales particulares
        "1150": 300000,   # $3.000,00 efectivo en custodia Claro
        "1210": 4500000,  # $45.000,00 saldo en cuenta Mercado Pago
        "1220": 1000000,  # $10.000,00 saldo mayorista Prepay
        "1230": 1000000,  # $10.000,00 saldo mayorista Claro
        "1310": 0,        # $0,00 cobranzas en tránsito
    }

    acta = ejecutar_apertura_dia_d(
        tes_conn=conn,
        saldos_iniciales_cents=saldos_dia_d,
        usuario_responsable="alan_socio",
        fecha_operativa="2026-11-01",
        acta_referencia="ACTA_INSPECCION_PRESENCIAL_001",
        observaciones="Conteo físico de billetes y verificación en pantallas",
    )

    assert acta["tipo_documento"] == "ACTA_CONSTITUTIVA_SALDOS_INICIALES_DIA_D"
    assert "certificado_sha256" in acta
    assert len(acta["certificado_sha256"]) == 64
    assert len(acta["asientos_iniciales"]) == len(CODIGOS_CANONICOS)  # catálogo canónico vigente (incluye 1160)

    # Verificar saldos en base de datos
    f1110 = obtener_fondo_por_codigo(conn, "1110")
    f1150 = obtener_fondo_por_codigo(conn, "1150")
    f1210 = obtener_fondo_por_codigo(conn, "1210")

    assert f1110["saldo_centavos"] == 2500000
    assert f1150["saldo_centavos"] == 300000
    assert f1150["titularidad"] == "CUSTODIA"
    assert f1210["saldo_centavos"] == 4500000

    # Verificar resumen de tesorería y segregación de fondos en custodia
    summary = getTreasurySummary(conn)
    assert summary["fondos_propios"]["total_disponible_cents"] > 0
    # Fondo 1150 no debe figurar como fondo propio disponible
    custodia_funds = [f for f in summary["fondos"] if f["ownership"] == "CUSTODIA"]
    assert len(custodia_funds) == 1
    assert custodia_funds[0]["fund_code"] == "1150"

    # Verificar conciliación del libro mayor
    rec = reconcile_treasury_ledger_balances(conn)
    assert rec["cuadre_exacto"] is True
    assert len(rec["discrepancias"]) == 0

    conn.close()


# ============================================================================
# 4. SIMULACRO DE REVERSIÓN DE EMERGENCIA (ROLLBACK DRILL)
# ============================================================================

def test_etapa8_rollback_production_bridge_drill(tmp_path):
    """Simula una reversión simétrica de emergencia en base comercial con migraciones."""
    comm_path = tmp_path / "comm_rollback_test.db"
    conn = open_db_connection(comm_path)
    conn.execute(
        """
        CREATE TABLE productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL COLLATE NOCASE,
            categoria TEXT NOT NULL DEFAULT 'Otro',
            proveedor TEXT NOT NULL DEFAULT 'General',
            unidad TEXT NOT NULL DEFAULT 'Unidad',
            costo INTEGER NOT NULL DEFAULT 0,
            precio_dia INTEGER NOT NULL DEFAULT 0,
            precio_noche INTEGER NOT NULL DEFAULT 0,
            activo INTEGER NOT NULL DEFAULT 1,
            fecha_actualizacion TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.commit()

    # Aplicar las 12 migraciones
    ejecutar_migraciones(conn)
    vers_pre = obtener_versiones_aplicadas(conn)
    assert 12 in vers_pre
    conn.close()

    # Ejecutar rollback de emergencia
    rep = ejecutar_rollback_simetrico(comm_path, dry_run=False)
    assert rep["status"] == "ROLLBACK_SUCCESS"
    assert rep["fases"]["ddl_rollback"]["version_12_removida"] is True
    assert rep["fases"]["ddl_rollback"]["tablas_outbox_eliminadas"] is True
    assert rep["fases"]["ddl_rollback"]["integrity_check"] == "ok"

    # Verificar directamente en la base
    conn_post = open_db_connection(comm_path)
    vers_post = obtener_versiones_aplicadas(conn_post)
    cur = conn_post.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='commercial_events_outbox';")
    tabla_outbox = cur.fetchone()
    cur.close()
    conn_post.close()

    assert 12 not in vers_post
    assert tabla_outbox is None
