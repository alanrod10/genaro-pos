"""Módulo de Aislamiento y Seguridad — GENARO TESORERÍA LABORATORIO.

Implementación de DEV-001 y LAB-001:
- Garantiza que el laboratorio funcione exclusivamente en una base SQLite local independiente.
- Bloquea cualquier intento de conexión remota a Turso o servicios cloud (Fail-Closed).
- Verifica integridad de pragmas (foreign_keys = ON, journal_mode = WAL).
- Impide colisiones o modificaciones accidentales a bases comerciales o tablas comerciales.
"""

import os
from pathlib import Path
import sqlite3
from typing import Optional, Set

# Tablas comerciales protegidas. Bajo ninguna circunstancia deben existir ni crearse en el laboratorio.
TABLAS_COMERCIALES_PROHIBIDAS: Set[str] = {
    "ventas",
    "ventas_items",
    "productos",
    "productos_audit",
    "servicios",
    "gastos",
    "cierres_caja",
    "preventistas",
    "pedidos_preventistas",
    "outbox_events",
    "historico_ventas",
    "historico_items",
}

# Prefijos de esquemas remotos prohibidos
ESQUEMAS_REMOTOS_PROHIBIDOS = (
    "libsql://",
    "https://",
    "http://",
    "wss://",
    "ws://",
    "turso://",
    "postgres://",
    "postgresql://",
    "mysql://",
)

# Nombres de bases de datos comerciales protegidas contra sobreescritura
BASES_COMERCIALES_CONOCIDAS = {
    "genaro-pos-dev-pre-migraciones-20261025.db",
    "genaro-pos-dev-pre-migraciones-20261025-verificable.db",
    "respaldo_verificable.db",
    "produccion.db",
    "staging.db",
}


class ErrorViolacionAislamiento(Exception):
    """Excepción lanzada cuando una operación viola el aislamiento estricto del laboratorio."""
    pass


def verificar_cadena_conexion_aislada(url_o_ruta: str) -> None:
    """Verifica que la cadena de conexión o ruta no sea remota ni apunte a producción.

    Lanza ErrorViolacionAislamiento si se detecta cualquier indicio de conexión remota o comercial.
    """
    if not isinstance(url_o_ruta, str):
        raise ErrorViolacionAislamiento(f"Tipo de ruta o conexión inválido: {type(url_o_ruta)}")

    cadena_limpia = url_o_ruta.strip().lower()

    for prefijo in ESQUEMAS_REMOTOS_PROHIBIDOS:
        if cadena_limpia.startswith(prefijo):
            raise ErrorViolacionAislamiento(
                f"VIOLACIÓN DE AISLAMIENTO: Esquema remoto '{prefijo}' prohibido en Laboratorio Tesorería. "
                "El laboratorio solo puede operar con archivos SQLite locales."
            )

    nombre_archivo = Path(url_o_ruta).name
    if nombre_archivo in BASES_COMERCIALES_CONOCIDAS:
        raise ErrorViolacionAislamiento(
            f"VIOLACIÓN DE AISLAMIENTO: No se permite operar sobre la base comercial protegida '{nombre_archivo}'."
        )


def obtener_ruta_db_laboratorio(ruta_custom: Optional[str] = None) -> str:
    """Retorna la ruta absoluta del archivo SQLite exclusivo para el laboratorio de tesorería.

    Precedencia:
    1. ruta_custom si fue provista (útil para pruebas unitarias con :memory: o tmp_path).
    2. Variable de entorno GENARO_TESORERIA_DB_PATH.
    3. Ruta por defecto: `<PROJECT_ROOT>/db/tesoreria_lab.db`.
    """
    if ruta_custom:
        verificar_cadena_conexion_aislada(ruta_custom)
        if ruta_custom == ":memory:":
            return ":memory:"
        return str(Path(ruta_custom).resolve())

    env_path = os.getenv("GENARO_TESORERIA_DB_PATH") or os.getenv("TESORERIA_DB_PATH")
    if env_path:
        verificar_cadena_conexion_aislada(env_path)
        return str(Path(env_path).resolve())

    # Directorio base del proyecto
    raiz_proyecto = Path(__file__).resolve().parent.parent
    ruta_default = raiz_proyecto / "db" / "tesoreria_lab.db"
    verificar_cadena_conexion_aislada(str(ruta_default))
    return str(ruta_default)


def verificar_tablas_prohibidas(conn: sqlite3.Connection) -> None:
    """Verifica que la base de datos conectada no contenga tablas del sistema comercial."""
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tablas_existentes = {row[0].lower() for row in cursor.fetchall()}
    cursor.close()

    colision = tablas_existentes.intersection(TABLAS_COMERCIALES_PROHIBIDAS)
    if colision:
        raise ErrorViolacionAislamiento(
            f"VIOLACIÓN DE AISLAMIENTO: La base contiene tablas comerciales prohibidas: {sorted(list(colision))}. "
            "El laboratorio de tesorería debe estar completamente desacoplado de las operaciones comerciales."
        )


def conectar_db_laboratorio(ruta_custom: Optional[str] = None) -> sqlite3.Connection:
    """Crea y configura una conexión SQLite aislada para Tesorería Laboratorio.

    Aplica:
    - Verificación fail-closed anti-conexión remota.
    - PRAGMA foreign_keys = ON;
    - PRAGMA journal_mode = WAL (en bases basadas en archivo).
    - Verificación de ausencia de tablas comerciales prohibidas.
    """
    ruta_db = obtener_ruta_db_laboratorio(ruta_custom)
    verificar_cadena_conexion_aislada(ruta_db)

    # Si es archivo en disco, asegurar que exista el directorio padre
    if ruta_db != ":memory:":
        path_obj = Path(ruta_db)
        path_obj.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(ruta_db, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    # Activar restricciones de integridad referencial
    conn.execute("PRAGMA foreign_keys = ON;")

    # Activar WAL mode para resiliencia y concurrencia local (si no es :memory:)
    if ruta_db != ":memory:":
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode = WAL;")
        cursor.close()

    # Validar que no estemos en una base comercial preexistente
    verificar_tablas_prohibidas(conn)

    return conn
