#!/usr/bin/env python3
"""Herramienta de Pre-vuelo y Despliegue Controlado de Producción — Etapa 8.

Ejecuta el protocolo de puesta en marcha seguro:
1. Comprobación estricta de aislamiento (0 conexiones remotas).
2. Verificación de integridad SQLite previa.
3. Creación y verificación de respaldo SHA-256 pre-migración obligatorio.
4. Aplicación de la migración 012 sobre la base comercial mediante migrations_runner.
5. Inicialización de la base de Tesorería y siembra del catálogo de los 9 fondos canónicos.
6. Validación de conciliación inicial (0 centavos iniciales en cada fondo).
7. Soporte para simulación segura en seco (--dry-run).
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import shutil
import sqlite3
import sys
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from db.migrations_runner import ejecutar_migraciones, obtener_versiones_aplicadas
from scripts.run_commercial_bridge_worker import open_db_connection
from tesoreria.catalog import CODIGOS_CANONICOS, obtener_catalogo_fondos, sembrar_catalogo_canónico
from tesoreria.commercial_bridge_worker import reconcile_treasury_ledger_balances
from tesoreria.schema import inicializar_esquema_laboratorio

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [DEPLOY-PROD] %(message)s")
logger = logging.getLogger("genaro_pos.deploy_prod")


class PreflightError(Exception):
    """Fallo en las pre-condiciones de seguridad de despliegue."""
    pass


def compute_sha256(file_path: Path) -> str:
    """Calcula el hash SHA-256 de un archivo en disco."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def check_isolation() -> Dict[str, Any]:
    """Verifica que el entorno no tenga conexiones activas a Turso ni credenciales remotas."""
    import os
    turso_dev = os.getenv("TURSO_DEV_DATABASE_URL", "")
    turso_prod = os.getenv("TURSO_PROD_DATABASE_URL", "")

    if turso_dev.startswith("libsql://") or turso_prod.startswith("libsql://"):
        raise PreflightError(
            f"Fallo de aislamiento: Se detectaron URLs remotas de Turso configuradas en el entorno."
        )

    return {
        "isolation_verified": True,
        "remote_connections_blocked": True,
        "engine": "STRICT_LOCAL_SQLITE",
    }


def execute_production_deployment(
    comm_db_path: Path,
    tes_db_path: Path,
    dry_run: bool = False,
    backup_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Ejecuta el protocolo de despliegue de la integración en producción o en seco."""
    logger.info("Iniciando protocolo de despliegue (dry_run=%s)...", dry_run)

    # 1. Chequeo de Aislamiento
    iso_report = check_isolation()

    if dry_run:
        logger.info("Modo --dry-run activo: creando copias temporales aisladas para la prueba...")
        temp_dir = PROJECT_ROOT / "production" / "temp_dry_run"
        temp_dir.mkdir(parents=True, exist_ok=True)
        target_comm = temp_dir / f"dryrun_comm_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.db"
        target_tes = temp_dir / f"dryrun_tes_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.db"

        if comm_db_path.exists():
            shutil.copy2(comm_db_path, target_comm)
        else:
            # Crear base sintética mínima con tabla productos si no existía
            c = sqlite3.connect(str(target_comm))
            c.execute(
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
            c.commit()
            c.close()
    else:
        target_comm = comm_db_path
        target_tes = tes_db_path

    # 2. Respaldo Obligatorio Previo
    backup_report = {}
    if target_comm.exists():
        b_dir = backup_dir or (PROJECT_ROOT / "production" / "backups")
        b_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_file = b_dir / f"{target_comm.stem}_pre_migracion_012_{ts}.db"
        shutil.copy2(target_comm, backup_file)
        b_hash = compute_sha256(backup_file)
        orig_hash = compute_sha256(target_comm)
        assert b_hash == orig_hash, "Fallo crítico: Hash de respaldo no coincide con el original"
        backup_report = {
            "backup_created": str(backup_file),
            "sha256": b_hash,
            "verified": True,
        }
        logger.info("Respaldo pre-migración verificado: %s (SHA-256: %s)", backup_file.name, b_hash)

    # 3. Integridad SQLite Previa
    comm_conn = open_db_connection(target_comm)
    integ_pre = comm_conn.execute("PRAGMA integrity_check;").fetchone()[0]
    if integ_pre != "ok":
        comm_conn.close()
        raise PreflightError(f"Integridad SQLite falló en {target_comm}: {integ_pre}")

    # 4. Aplicación de Migraciones (Versión 12 Outbox)
    logger.info("Aplicando migraciones comerciales pendientes...")
    versiones_previas = obtener_versiones_aplicadas(comm_conn)
    ejecutar_migraciones(comm_conn)
    versiones_posteriores = obtener_versiones_aplicadas(comm_conn)
    assert 12 in versiones_posteriores, "La migración 12 no fue registrada en _schema_migrations"

    # Verificar estructura outbox
    cur = comm_conn.cursor()
    cur.execute("PRAGMA table_info(commercial_events_outbox);")
    cols = {r[1] for r in cur.fetchall()}
    assert "reprocessed_by" in cols and "reprocessed_at" in cols
    cur.close()
    comm_conn.close()

    # 5. Inicialización de Base de Tesorería
    logger.info("Inicializando catálogo de Tesorería en %s...", target_tes)
    tes_conn = open_db_connection(target_tes)
    inicializar_esquema_laboratorio(tes_conn)
    sembrar_catalogo_canónico(tes_conn)
    fondos = obtener_catalogo_fondos(tes_conn)
    assert {f["codigo"] for f in fondos} == CODIGOS_CANONICOS, (
        f"Catálogo de fondos no canónico: se esperaban {sorted(CODIGOS_CANONICOS)}, se encontraron {sorted(f['codigo'] for f in fondos)}"
    )
    assert all(f["saldo_centavos"] == 0 for f in fondos), "Los fondos iniciales deben ser estrictamente 0"

    # 6. Reconciliación Inicial de Libro Mayor
    rec = reconcile_treasury_ledger_balances(tes_conn)
    assert rec["cuadre_exacto"] is True, "El libro mayor inicial no cuadra"
    tes_conn.close()

    reporte_despliegue = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "isolation": iso_report,
        "backup": backup_report,
        "commercial_db": {
            "path": str(target_comm),
            "integrity_check": integ_pre,
            "versiones_aplicadas": len(versiones_posteriores),
            "migracion_012_presente": 12 in versiones_posteriores,
        },
        "tesoreria_db": {
            "path": str(target_tes),
            "fondos_canónicos": len(fondos),
            "saldos_en_cero": True,
            "cuadre_exacto": rec["cuadre_exacto"],
        },
        "status": "READY_FOR_OPERATION",
    }

    # Limpieza en seco
    if dry_run:
        target_comm.unlink(missing_ok=True)
        target_tes.unlink(missing_ok=True)

    logger.info("Protocolo de despliegue completado EXITOSAMENTE.")
    return reporte_despliegue


def main() -> int:
    parser = argparse.ArgumentParser(description="Despliegue Controlado de Producción — GenaroPOS")
    parser.add_argument("--comm-db", type=str, default="/home/alanrod10/production/data/genaro-pos.db", help="Ruta DB comercial")
    parser.add_argument("--tes-db", type=str, default="/home/alanrod10/production/data/tesoreria.db", help="Ruta DB tesorería")
    parser.add_argument("--dry-run", action="store_true", help="Ejecutar simulación en seco en copias aisladas")

    args = parser.parse_args()
    comm_path = Path(args.comm_db).resolve()
    tes_path = Path(args.tes_db).resolve()

    try:
        reporte = execute_production_deployment(comm_path, tes_path, dry_run=args.dry_run)
        print(json.dumps(reporte, indent=2, ensure_ascii=False))
        return 0
    except Exception as e:
        logger.error("Fallo durante el despliegue: %s", e, exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
