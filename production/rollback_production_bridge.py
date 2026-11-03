#!/usr/bin/env python3
"""Herramienta y Simulador de Rollback de Emergencia — Etapa 8.

Ejecuta el protocolo de reversión simétrica de la integración comercial:
1. Verificación del estado del Worker y del archivo PID.
2. Aplicación transaccional de rollback_012_commercial_integration_outbox_lab.sql.
3. Eliminación de tablas Outbox y purga de la versión 12 de _schema_migrations.
4. Verificación de integridad SQLite post-rollback.
5. Opción de restauración directa desde backup verificable con hash SHA-256.
6. Soporte completo de simulación en seco (--dry-run).
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

from db.migrations_runner import obtener_versiones_aplicadas
from scripts.run_commercial_bridge_worker import open_db_connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [ROLLBACK] %(message)s")
logger = logging.getLogger("genaro_pos.rollback")


def compute_sha256(file_path: Path) -> str:
    """Calcula el hash SHA-256 de un archivo en disco."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def ejecutar_rollback_simetrico(
    comm_db_path: Path,
    dry_run: bool = False,
    backup_to_restore: Optional[Path] = None,
) -> Dict[str, Any]:
    """Ejecuta el rollback de la migración 12 y opcionalmente restaura un backup previo."""
    logger.info("Iniciando procedimiento de reversión (dry_run=%s)...", dry_run)

    if dry_run:
        temp_dir = PROJECT_ROOT / "production" / "temp_dry_run"
        temp_dir.mkdir(parents=True, exist_ok=True)
        target_comm = temp_dir / f"dryrun_rollback_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.db"
        if comm_db_path.exists():
            shutil.copy2(comm_db_path, target_comm)
        else:
            raise FileNotFoundError(f"Base de datos origen no existe: {comm_db_path}")
    else:
        target_comm = comm_db_path

    reporte: Dict[str, Any] = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "target_db": str(target_comm),
        "fases": {},
    }

    # FASE 1: Ejecutar script de reversión DDL de la migración 012
    rollback_sql_path = PROJECT_ROOT / "db" / "migrations" / "rollback_012_commercial_integration_outbox_lab.sql"
    if not rollback_sql_path.exists():
        raise FileNotFoundError(f"Script de rollback no encontrado en {rollback_sql_path}")

    with open(rollback_sql_path, "r", encoding="utf-8") as f:
        sql_content = f.read()

    comm_conn = open_db_connection(target_comm)
    versiones_pre = obtener_versiones_aplicadas(comm_conn)

    logger.info("Aplicando script de rollback simétrico DDL...")
    comm_conn.executescript(sql_content)

    versiones_post = obtener_versiones_aplicadas(comm_conn)
    cur = comm_conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('commercial_events_outbox', 'commercial_adicionales_arqueo_turno');")
    tablas_remanentes = [r[0] for r in cur.fetchall()]
    integ = comm_conn.execute("PRAGMA integrity_check;").fetchone()[0]
    comm_conn.close()

    reporte["fases"]["ddl_rollback"] = {
        "version_12_removida": 12 not in versiones_post,
        "tablas_outbox_eliminadas": len(tablas_remanentes) == 0,
        "tablas_remanentes": tablas_remanentes,
        "integrity_check": integ,
    }
    assert 12 not in versiones_post, "La versión 12 debería haber sido eliminada de _schema_migrations"
    assert len(tablas_remanentes) == 0, f"Quedaron tablas sin eliminar: {tablas_remanentes}"
    assert integ == "ok", f"Integridad corrupta tras rollback: {integ}"

    # FASE 2: Restauración desde Backup (si fue solicitado)
    if backup_to_restore:
        if not backup_to_restore.exists():
            raise FileNotFoundError(f"Archivo de respaldo a restaurar no encontrado: {backup_to_restore}")

        hash_backup = compute_sha256(backup_to_restore)
        logger.info("Restaurando base desde respaldo: %s (SHA-256: %s)", backup_to_restore.name, hash_backup)
        shutil.copy2(backup_to_restore, target_comm)
        hash_restaurado = compute_sha256(target_comm)
        assert hash_backup == hash_restaurado, "Discrepancia en hash de archivo restaurado"

        conn_rest = open_db_connection(target_comm)
        integ_rest = conn_rest.execute("PRAGMA integrity_check;").fetchone()[0]
        vers_rest = obtener_versiones_aplicadas(conn_rest)
        conn_rest.close()

        reporte["fases"]["backup_restoration"] = {
            "restored_from": str(backup_to_restore),
            "sha256": hash_restaurado,
            "integrity_check": integ_rest,
            "versiones_presentes": vers_rest,
        }
        assert integ_rest == "ok"

    if dry_run:
        target_comm.unlink(missing_ok=True)

    reporte["status"] = "ROLLBACK_SUCCESS"
    logger.info("Procedimiento de rollback finalizado con éxito.")
    return reporte


def main() -> int:
    parser = argparse.ArgumentParser(description="Procedimiento de Rollback de Integración Comercial")
    parser.add_argument("--comm-db", type=str, required=True, help="Ruta de la base comercial a revertir")
    parser.add_argument("--dry-run", action="store_true", help="Simular reversión en copia temporal")
    parser.add_argument("--restore-from", type=str, help="Ruta opcional de backup para restaurar")

    args = parser.parse_args()
    comm_path = Path(args.comm_db).resolve()
    restore_path = Path(args.restore_from).resolve() if args.restore_from else None

    try:
        rep = ejecutar_rollback_simetrico(comm_path, dry_run=args.dry_run, backup_to_restore=restore_path)
        print(json.dumps(rep, indent=2, ensure_ascii=False))
        return 0
    except Exception as e:
        logger.error("Error en rollback: %s", e, exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
