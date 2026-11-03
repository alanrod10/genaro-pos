#!/usr/bin/env python3
"""Herramienta de Simulación y Ensayo General de Migración (DRY-RUN) — Etapa 6.

Ejecuta en un entorno aislado de laboratorio:
1. Verificación del estado inicial de bases y migraciones (versiones 1 a 12).
2. Verificación de integridad de tablas, claves foráneas e índices.
3. Validación del catálogo canónico de 9 fondos y sus naturalezas/titularidades.
4. Ensayo del procedimiento simétrico de reversión (Rollback de migración 12) y re-aplicación.
5. Verificación del estado inicial de Outbox y conciliación contable sin movimientos previos.
6. Emisión de informe estructurado de certificación de pre-condiciones.
"""

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3
import sys
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from db.migrations_runner import ejecutar_migraciones, obtener_versiones_aplicadas
from tesoreria.catalog import FONDOS_CANONICOS, obtener_catalogo_fondos, sembrar_catalogo_canónico
from tesoreria.commercial_bridge_worker import reconcile_treasury_ledger_balances
from tesoreria.schema import inicializar_esquema_laboratorio

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("genaro_pos.dry_run")


def ejecutar_dry_run_migracion(comm_db_path: Path, tes_db_path: Path) -> Dict[str, Any]:
    """Ejecuta el ensayo general de migración en archivos SQLite dedicados."""
    logger.info("Iniciando DRY-RUN de migración en entorno aislado...")

    # Limpiar archivos previos si existen para garantizar prueba limpia
    comm_db_path.unlink(missing_ok=True)
    tes_db_path.unlink(missing_ok=True)

    reporte: Dict[str, Any] = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "resultado": "EXITOSO",
        "etapas": {},
        "advertencias": [],
        "errores": [],
    }

    # 1. Base Comercial y Migraciones 1 a 12
    logger.info("Fase 1: Aplicación secuencial de migraciones comerciales 1 a 12...")
    comm_conn = sqlite3.connect(str(comm_db_path))
    comm_conn.row_factory = sqlite3.Row
    comm_conn.execute("PRAGMA foreign_keys = ON;")

    # Tabla base requerida por el esquema existente
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
    comm_conn.commit()

    # Ejecutar migraciones 1..12
    ejecutar_migraciones(comm_conn)
    versiones = obtener_versiones_aplicadas(comm_conn)

    reporte["etapas"]["migraciones_aplicadas"] = {
        "total": len(versiones),
        "versiones": versiones,
    }
    assert 12 in versiones, "Migración 12 no registrada"
    assert len(versiones) >= 12, f"Se esperaban al menos 12 migraciones, se encontraron {len(versiones)}"

    # 2. Comprobación de integridad y tablas creadas
    logger.info("Fase 2: Verificación de integridad estructural e índices...")
    fk_check = comm_conn.execute("PRAGMA foreign_key_check;").fetchall()
    integ_check = comm_conn.execute("PRAGMA integrity_check;").fetchone()[0]

    cur = comm_conn.cursor()
    cur.execute("PRAGMA table_info(commercial_events_outbox);")
    cols_outbox = {c[1]: c[2] for c in cur.fetchall()}

    cur.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='commercial_events_outbox';")
    indices_outbox = [row[0] for row in cur.fetchall()]

    reporte["etapas"]["estructura_outbox"] = {
        "columnas_totales": len(cols_outbox),
        "tiene_reprocessed_by": "reprocessed_by" in cols_outbox,
        "tiene_reprocessed_at": "reprocessed_at" in cols_outbox,
        "indices": indices_outbox,
        "foreign_keys_ok": len(fk_check) == 0,
        "integrity_check": integ_check,
    }
    assert "reprocessed_by" in cols_outbox and "reprocessed_at" in cols_outbox
    assert integ_check == "ok"

    # 3. Ensayo de Rollback Simétrico de Migración 12 y Re-aplicación
    logger.info("Fase 3: Ensayo de procedimiento de Rollback simétrico de la migración 12...")
    rollback_path = PROJECT_ROOT / "db" / "migrations" / "rollback_012_commercial_integration_outbox_lab.sql"
    assert rollback_path.exists(), "Archivo de rollback 012 no encontrado"

    with open(rollback_path, "r", encoding="utf-8") as f:
        rollback_sql = f.read()

    comm_conn.executescript(rollback_sql)
    versiones_post_rollback = obtener_versiones_aplicadas(comm_conn)

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='commercial_events_outbox';")
    tabla_existe_post_rollback = cur.fetchone() is not None

    reporte["etapas"]["rollback_simetrico"] = {
        "version_12_removida": 12 not in versiones_post_rollback,
        "tabla_outbox_eliminada": not tabla_existe_post_rollback,
    }
    assert 12 not in versiones_post_rollback, "La versión 12 debería haber sido removida"
    assert not tabla_existe_post_rollback, "La tabla outbox debería haber sido eliminada"

    # Re-aplicar para dejar la base en estado operativo
    logger.info("Re-aplicando migración 12 tras rollback exitoso...")
    ejecutar_migraciones(comm_conn)
    versiones_reaplicadas = obtener_versiones_aplicadas(comm_conn)
    assert 12 in versiones_reaplicadas
    reporte["etapas"]["re_aplicacion_exitosa"] = True

    # 4. Esquema y Catálogo de Tesorería Aislada
    logger.info("Fase 4: Verificación de base de Tesorería y Catálogo Canónico...")
    tes_conn = sqlite3.connect(str(tes_db_path))
    tes_conn.row_factory = sqlite3.Row
    tes_conn.execute("PRAGMA foreign_keys = ON;")

    inicializar_esquema_laboratorio(tes_conn)
    sembrar_catalogo_canónico(tes_conn)

    fondos = obtener_catalogo_fondos(tes_conn)
    codigos_en_base = {f["codigo"] for f in fondos}
    codigos_esperados = {f["codigo"] for f in FONDOS_CANONICOS}

    reporte["etapas"]["catalogo_tesoreria"] = {
        "fondos_totales": len(fondos),
        "codigos": sorted(list(codigos_en_base)),
        "todos_saldos_en_cero": all(f["saldo_centavos"] == 0 for f in fondos),
        "todos_activos": all(f["estado"] == "ACTIVO" for f in fondos),
    }
    assert codigos_en_base == codigos_esperados, "Discrepancia en los 9 códigos canónicos"
    assert all(f["saldo_centavos"] == 0 for f in fondos), "Saldos iniciales deben ser 0"

    # 5. Conciliación Inicial de Libro Mayor
    logger.info("Fase 5: Conciliación matemática inicial de los 9 fondos...")
    reconciliacion_inicial = reconcile_treasury_ledger_balances(tes_conn)
    reporte["etapas"]["conciliacion_inicial"] = {
        "cuadre_exacto": reconciliacion_inicial["cuadre_exacto"],
        "fondos_auditados": reconciliacion_inicial["fondos_auditados"],
        "discrepancias": reconciliacion_inicial["discrepancias"],
    }
    assert reconciliacion_inicial["cuadre_exacto"], "El cuadre inicial debe ser exacto"
    assert len(reconciliacion_inicial["discrepancias"]) == 0

    comm_conn.close()
    tes_conn.close()

    logger.info("DRY-RUN completado exitosamente sin fallos.")
    return reporte


def main() -> int:
    comm_path = PROJECT_ROOT / "db" / "dry_run_comm_test.db"
    tes_path = PROJECT_ROOT / "db" / "dry_run_tes_test.db"

    try:
        reporte = ejecutar_dry_run_migracion(comm_path, tes_path)
        print(json.dumps(reporte, indent=2, ensure_ascii=False))
        return 0
    finally:
        comm_path.unlink(missing_ok=True)
        tes_path.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
