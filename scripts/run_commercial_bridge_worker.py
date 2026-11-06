#!/usr/bin/env python3
"""Servicio Continuo y Runner Autónomo del Commercial Bridge Worker.

Este ejecutable provee la interfaz de servicio para la sincronización continua
entre el sistema comercial GenaroPOS y Genaro Tesorería.

Capacidades:
1. Inicio continuo con ciclo configurable y drenado por lotes.
2. Modo ejecución única (--once) y modo diagnóstico/salud (--health-check).
3. Manejo de señales UNIX (SIGINT, SIGTERM) con apagado ordenado (graceful shutdown).
4. Prevención de concurrencia mediante archivo PID bloqueante.
5. Recuperación automática de eventos en PROCESSING huérfanos al iniciar.
6. Registro de logs estructurado y detallado.
"""

import argparse
import atexit
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import signal
import sqlite3
import sys
import time
from typing import Dict, Optional

# Añadir el directorio raíz del proyecto al path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tesoreria.commercial_bridge_worker import (
    get_commercial_bridge_status,
    get_simplified_sync_status,
    process_pending_commercial_events,
    reconcile_treasury_ledger_balances,
    recover_orphaned_processing_events,
)
from tesoreria.isolation import conectar_db_laboratorio

logger = logging.getLogger("genaro_pos.service.commercial_bridge")


class ServiceLockError(Exception):
    """Excepción cuando otra instancia del worker ya está en ejecución."""
    pass


class PidLockManager:
    """Administrador de archivo de bloqueo PID para garantizar instancia única."""

    def __init__(self, pid_file: Path):
        self.pid_file = pid_file
        self._acquired = False

    def acquire(self) -> None:
        if self.pid_file.exists():
            try:
                with open(self.pid_file, "r") as f:
                    old_pid = int(f.read().strip())
                # Comprobar si el proceso anterior sigue con vida
                os.kill(old_pid, 0)
                raise ServiceLockError(
                    f"Existe otra instancia del worker activa con PID {old_pid}. "
                    f"Archivo de bloqueo: {self.pid_file}"
                )
            except (ValueError, OSError):
                # El archivo de bloqueo era huérfano (proceso anterior terminó)
                logger.warning("Eliminando archivo de bloqueo huérfano: %s", self.pid_file)
                try:
                    self.pid_file.unlink(missing_ok=True)
                except Exception as ex:
                    logger.warning("No se pudo eliminar archivo de bloqueo huérfano %s: %s", self.pid_file, ex)

        try:
            self.pid_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.pid_file, "w") as f:
                f.write(str(os.getpid()))
            self._acquired = True
            atexit.register(self.release)
        except Exception as e:
            raise ServiceLockError(f"No se pudo crear el archivo de bloqueo {self.pid_file}: {e}")

    def release(self) -> None:
        if self._acquired and self.pid_file.exists():
            try:
                with open(self.pid_file, "r") as f:
                    current_content = f.read().strip()
                if current_content == str(os.getpid()):
                    self.pid_file.unlink(missing_ok=True)
                    self._acquired = False
            except Exception as ex:
                logger.warning("No se pudo liberar archivo de bloqueo %s: %s", self.pid_file, ex)


def open_db_connection(db_path: Path) -> sqlite3.Connection:
    """Abre conexión SQLite local con WAL y foreign keys."""
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        conn.execute("PRAGMA journal_mode = WAL;")
    except Exception as ex:
        logger.warning("No se pudo activar modo WAL en %s: %s", db_path, ex)
    return conn


def run_health_check(comm_path: Path, tes_path: Path) -> int:
    """Ejecuta diagnóstico de salud integral del puente y emite reporte JSON."""
    report: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "UNKNOWN",
        "comm_db": str(comm_path),
        "tes_db": str(tes_path),
        "checks": {},
    }

    if not comm_path.exists():
        report["status"] = "ERROR"
        report["error"] = f"Base comercial no encontrada: {comm_path}"
        print(json.dumps(report, indent=2))
        return 1

    if not tes_path.exists():
        report["status"] = "ERROR"
        report["error"] = f"Base de tesorería no encontrada: {tes_path}"
        print(json.dumps(report, indent=2))
        return 1

    try:
        comm_conn = open_db_connection(comm_path)
        tes_conn = open_db_connection(tes_path)

        # 1. Integridad SQLite
        comm_integ = comm_conn.execute("PRAGMA integrity_check;").fetchone()[0]
        tes_integ = tes_conn.execute("PRAGMA integrity_check;").fetchone()[0]
        report["checks"]["commercial_integrity"] = comm_integ
        report["checks"]["tesoreria_integrity"] = tes_integ

        # 2. Estado del puente
        bridge_status = get_commercial_bridge_status(comm_conn)
        sync_status = get_simplified_sync_status(comm_conn)
        report["checks"]["bridge_metrics"] = bridge_status
        report["checks"]["sync_summary"] = sync_status

        # 3. Conciliación de libro mayor
        reconciliation = reconcile_treasury_ledger_balances(tes_conn)
        report["checks"]["ledger_reconciliation"] = {
            "cuadre_exacto": reconciliation["cuadre_exacto"],
            "fondos_auditados": reconciliation["fondos_auditados"],
            "discrepancias": reconciliation["discrepancias"],
        }

        # Determinación de estado global
        if comm_integ == "ok" and tes_integ == "ok" and reconciliation["cuadre_exacto"]:
            if bridge_status["dead_letter"] > 0:
                report["status"] = "DEGRADED"
                report["warning"] = f"Hay {bridge_status['dead_letter']} eventos en DEAD_LETTER que requieren revisión de Socios."
                ret_code = 2
            elif bridge_status["failed"] > 0:
                report["status"] = "WARNING"
                report["warning"] = f"Hay {bridge_status['failed']} eventos en reintento (FAILED)."
                ret_code = 0
            else:
                report["status"] = "HEALTHY"
                ret_code = 0
        else:
            report["status"] = "CRITICAL"
            ret_code = 1

        comm_conn.close()
        tes_conn.close()
        print(json.dumps(report, indent=2))
        return ret_code

    except Exception as e:
        report["status"] = "FATAL"
        report["error"] = str(e)
        print(json.dumps(report, indent=2))
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Servicio Autónomo del Commercial Bridge Worker.")
    parser.add_argument("--interval", type=float, default=5.0, help="Intervalo en segundos entre ciclos (def: 5.0)")
    parser.add_argument("--batch-size", type=int, default=50, help="Tamaño de lote por ciclo (def: 50)")
    parser.add_argument("--db-comm", type=str, default=str(PROJECT_ROOT / "db" / "genaro-pos-dev-lab.db"), help="Ruta DB comercial")
    parser.add_argument("--db-tes", type=str, default=str(PROJECT_ROOT / "db" / "tesoreria_lab.db"), help="Ruta DB tesorería")
    parser.add_argument("--pid-file", type=str, default="/tmp/genaro_commercial_bridge.pid", help="Ruta del archivo PID")
    parser.add_argument("--once", action="store_true", help="Ejecutar un solo ciclo y terminar")
    parser.add_argument("--health-check", action="store_true", help="Ejecutar diagnóstico de salud y salir")
    parser.add_argument("--recover-only", action="store_true", help="Recuperar eventos huérfanos y salir")
    parser.add_argument("--verbose", action="store_true", help="Habilitar salida DEBUG")

    args = parser.parse_args()

    # Configuración de Logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    comm_path = Path(args.db_comm).resolve()
    tes_path = Path(args.db_tes).resolve()
    pid_path = Path(args.pid_file).resolve()

    if args.health_check:
        return run_health_check(comm_path, tes_path)

    # Modo recuperación exclusiva
    if args.recover_only:
        logger.info("Modo --recover-only iniciado.")
        comm_conn = open_db_connection(comm_path)
        rec = recover_orphaned_processing_events(comm_conn, timeout_seconds=0)
        comm_conn.close()
        logger.info("Eventos huérfanos recuperados: %d", rec)
        return 0

    # Adquisición de PID lock para ejecución de drenaje
    lock_mgr = PidLockManager(pid_path)
    try:
        lock_mgr.acquire()
        logger.info("Bloqueo de proceso adquirido (PID: %d, archivo: %s)", os.getpid(), pid_path)
    except ServiceLockError as e:
        logger.error("Fallo de bloqueo: %s", e)
        return 1

    # Manejo de señales para graceful shutdown
    stop_requested = False

    def handle_signal(sig, frame):
        nonlocal stop_requested
        sig_name = signal.Signals(sig).name
        logger.info("Señal %s recibida. Solicitando apagado ordenado del worker...", sig_name)
        stop_requested = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Inicializar conexiones
    comm_conn = open_db_connection(comm_path)
    tes_conn = open_db_connection(tes_path)

    try:
        # 1. Recuperación automática de eventos huérfanos de ejecuciones previas
        logger.info("Comprobando eventos huérfanos tras posibles reinicios...")
        rec = recover_orphaned_processing_events(comm_conn, timeout_seconds=30)
        if rec > 0:
            logger.warning("Se recuperaron %d eventos que habían quedado en PROCESSING.", rec)

        if args.once:
            logger.info("Ejecutando ciclo único (--once)...")
            stats = process_pending_commercial_events(comm_conn, tes_conn, batch_size=args.batch_size)
            logger.info(
                "Ciclo único completado: publicados=%d, fallidos=%d, dead_letter=%d, omitidos=%d",
                stats["published"], stats["failed"], stats["dead_letter"], stats["skipped"]
            )
            return 0

        logger.info(
            "Commercial Bridge Worker en ejecución continua (intervalo: %ss, lote: %d)...",
            args.interval, args.batch_size
        )

        while not stop_requested:
            try:
                # Comprobar eventos huérfanos periódicamente
                recover_orphaned_processing_events(comm_conn, timeout_seconds=60)
                # Drenar lote
                stats = process_pending_commercial_events(comm_conn, tes_conn, batch_size=args.batch_size)
                total_procesados = stats["published"] + stats["failed"] + stats["dead_letter"]
                if total_procesados > 0:
                    logger.info(
                        "Lote procesado: %d eventos (publicados: %d, fallidos: %d, dead_letter: %d)",
                        total_procesados, stats["published"], stats["failed"], stats["dead_letter"]
                    )
            except Exception as e:
                logger.error("Error inesperado en ciclo de drenado: %s", e, exc_info=True)

            # Espera activa comprobando la bandera de parada cada 0.2s
            slept = 0.0
            while slept < args.interval and not stop_requested:
                time.sleep(0.2)
                slept += 0.2

        logger.info("Apagado ordenado finalizado con éxito.")
        return 0

    finally:
        try:
            comm_conn.close()
        except Exception as ex:
            logger.warning("Error cerrando comm_conn: %s", ex)
        try:
            tes_conn.close()
        except Exception as ex:
            logger.warning("Error cerrando tes_conn: %s", ex)
        lock_mgr.release()


if __name__ == "__main__":
    sys.exit(main())
