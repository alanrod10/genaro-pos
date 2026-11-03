#!/usr/bin/env python3
"""Script de gestión y supervisión del Entorno Operativo de Pruebas (GenaroPOS).

Permite iniciar, verificar el estado y detener el entorno completo (FastAPI + Vite)
de forma aislada, sin sudo y con apagado ordenado.
"""

import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request
import json

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STAGING_DIR = PROJECT_ROOT / "staging"
ENV_TEST_PATH = PROJECT_ROOT / ".env.testing_operativo"
BACKEND_PID_FILE = STAGING_DIR / "backend_test.pid"
FRONTEND_PID_FILE = STAGING_DIR / "frontend_test.pid"
BACKEND_LOG_FILE = STAGING_DIR / "backend_test.log"
FRONTEND_LOG_FILE = STAGING_DIR / "frontend_test.log"
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"


def is_process_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def get_stored_pid(pid_file: Path) -> int | None:
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            if is_process_running(pid):
                return pid
        except Exception:
            pass
    return None


def stop_env():
    print("[ENTORNO PRUEBAS] Deteniendo servicios...")
    stopped = 0
    for name, pid_file in [("Backend FastAPI", BACKEND_PID_FILE), ("Frontend Vite", FRONTEND_PID_FILE)]:
        pid = get_stored_pid(pid_file)
        if pid:
            print(f"  Deteniendo {name} (PID: {pid})...")
            try:
                try:
                    pgid = os.getpgid(pid)
                    os.killpg(pgid, signal.SIGTERM)
                except Exception:
                    os.kill(pid, signal.SIGTERM)
                for _ in range(15):
                    if not is_process_running(pid):
                        break
                    time.sleep(0.2)
                if is_process_running(pid):
                    try:
                        pgid = os.getpgid(pid)
                        os.killpg(pgid, signal.SIGKILL)
                    except Exception:
                        os.kill(pid, signal.SIGKILL)
                stopped += 1
            except Exception as e:
                print(f"  Error al detener {name}: {e}")
        if pid_file.exists():
            pid_file.unlink(missing_ok=True)
    
    # También verificar si quedó algún proceso uvicorn / vite colgado en estos puertos
    print(f"[ENTORNO PRUEBAS] Servicios detenidos ({stopped} procesos finalizados).")


def start_env():
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    b_pid = get_stored_pid(BACKEND_PID_FILE)
    f_pid = get_stored_pid(FRONTEND_PID_FILE)

    if b_pid or f_pid:
        print(f"[ENTORNO PRUEBAS] Hay servicios ya activos (Backend PID: {b_pid}, Frontend PID: {f_pid}).")
        print("Ejecute 'python scripts/manage_test_env.py stop' antes de reiniciar.")
        return

    # Cargar variables de entorno operativo
    env = os.environ.copy()
    if ENV_TEST_PATH.exists():
        for line in ENV_TEST_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()

    # 1. Iniciar Backend FastAPI
    print("[ENTORNO PRUEBAS] Iniciando Backend FastAPI en http://127.0.0.1:8000...")
    backend_log = open(BACKEND_LOG_FILE, "a", encoding="utf-8")
    backend_proc = subprocess.Popen(
        [
            str(VENV_PYTHON),
            "-m",
            "uvicorn",
            "api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
            "--log-level",
            "info",
        ],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=backend_log,
        stderr=backend_log,
        preexec_fn=os.setsid,
    )
    BACKEND_PID_FILE.write_text(str(backend_proc.pid))

    # Esperar que el backend responda /health
    backend_ready = False
    for _ in range(30):
        time.sleep(0.3)
        try:
            req = urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=1.0)
            if req.status == 200:
                backend_ready = True
                break
        except Exception:
            pass

    if not backend_ready:
        print("  ⚠️ Advertencia: Backend FastAPI demoró en responder. Revisar logs en:", BACKEND_LOG_FILE)
    else:
        print("  ✓ Backend FastAPI iniciado y respondiendo en http://127.0.0.1:8000")

    # 2. Iniciar Frontend Vite
    print("[ENTORNO PRUEBAS] Iniciando Frontend Vite en http://0.0.0.0:5173...")
    frontend_log = open(FRONTEND_LOG_FILE, "a", encoding="utf-8")
    frontend_proc = subprocess.Popen(
        ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"],
        cwd=str(PROJECT_ROOT / "frontend"),
        env=env,
        stdout=frontend_log,
        stderr=frontend_log,
        preexec_fn=os.setsid,
    )
    FRONTEND_PID_FILE.write_text(str(frontend_proc.pid))

    frontend_ready = False
    for _ in range(30):
        time.sleep(0.3)
        try:
            req = urllib.request.urlopen("http://127.0.0.1:5173", timeout=1.0)
            if req.status == 200:
                frontend_ready = True
                break
        except Exception:
            pass

    if not frontend_ready:
        print("  ⚠️ Advertencia: Frontend Vite demoró en responder. Revisar logs en:", FRONTEND_LOG_FILE)
    else:
        print("  ✓ Frontend Vite iniciado y respondiendo en http://0.0.0.0:5173")

    status_env()


def status_env():
    print("\n=== ESTADO DEL ENTORNO OPERATIVO DE PRUEBAS ===")
    b_pid = get_stored_pid(BACKEND_PID_FILE)
    f_pid = get_stored_pid(FRONTEND_PID_FILE)

    b_ok = False
    f_ok = False

    if b_pid:
        try:
            req = urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=1.0)
            data = json.loads(req.read().decode())
            print(f"Backend FastAPI: ACTIVO (PID: {b_pid}, HTTP 200)")
            print(f"  Detalle Health: status={data.get('status')}, env={data.get('env')}")
            b_ok = True
        except Exception as e:
            print(f"Backend FastAPI: PID {b_pid} ACTIVO pero no responde HTTP ({e})")
    else:
        print("Backend FastAPI: DETENIDO")

    if f_pid:
        try:
            req = urllib.request.urlopen("http://127.0.0.1:5173", timeout=1.0)
            print(f"Frontend Vite:   ACTIVO (PID: {f_pid}, HTTP 200)")
            f_ok = True
        except Exception as e:
            print(f"Frontend Vite:   PID {f_pid} ACTIVO pero no responde HTTP ({e})")
    else:
        print("Frontend Vite:   DETENIDO")

    print("\n--- Puntos de Acceso ---")
    print("  Navegador local:      http://localhost:5173")
    print("  Navegador LAN / WSL:  http://172.20.233.143:5173")
    print("  API Docs (FastAPI):   http://127.0.0.1:8000/docs")
    print("\n--- Credenciales de Prueba ---")
    print("  Usuario Alan:  alan  /  alan2026  (Rol: Socio)")
    print("  Usuario Yilda: yilda /  yilda2026 (Rol: Administrador)")
    print("\n--- Bases de Datos Aisladas de Prueba ---")
    print("  Comercial: staging/genaro_pos_staging.db")
    print("  Tesorería: staging/tesoreria_staging.db")
    print("  Producción intacta: /home/alanrod10/production/data/genaro-pos.db (NO TOCADA)")
    print("================================================\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gestor del Entorno de Pruebas GenaroPOS")
    parser.add_argument("action", choices=["start", "stop", "status", "restart"], help="Acción a realizar")
    args = parser.parse_args()

    if args.action == "start":
        start_env()
    elif args.action == "stop":
        stop_env()
    elif args.action == "restart":
        stop_env()
        time.sleep(1)
        start_env()
    elif args.action == "status":
        status_env()
