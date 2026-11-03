"""Ejecutor seguro y transaccional de migraciones para GenaroPOS.

Asegura:
1. Procesamiento estrictamente secuencial y ordenado por número de versión.
2. Omitir versiones previamente registradas en _schema_migrations.
3. Transaccionalidad atómica por migración con rollback inmediato ante fallos.
4. Diagnóstico explícito ante inconsistencias de esquema (ej. columna costo_unitario
   preexistente sin registro de versión 9).
5. No reejecución accidental de operaciones DDL no idempotentes (ALTER TABLE ADD COLUMN).
"""

from datetime import datetime
import logging
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("genaro_pos.migrations")

RUTA_MIGRACIONES_DEFAULT = Path(__file__).resolve().parent / "migrations"


class ErrorMigraciones(Exception):
    """Excepción base para fallos del sistema de migraciones."""
    pass


class ErrorInconsistenciaEsquema(ErrorMigraciones):
    """Se detectó una discrepancia entre las tablas reales y _schema_migrations."""
    pass


class ErrorEjecucionMigracion(ErrorMigraciones):
    """Fallo durante la ejecución transaccional de una migración."""
    def __init__(self, version: int, nombre_archivo: str, mensaje_original: str):
        super().__init__(
            f"Fallo crítico al aplicar la migración {version:03d} ({nombre_archivo}): {mensaje_original}"
        )
        self.version = version
        self.nombre_archivo = nombre_archivo
        self.mensaje_original = mensaje_original


def _asegurar_tabla_schema_migrations(conn: Any) -> None:
    """Garantiza la existencia de la tabla de control de versiones."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS _schema_migrations (
            version INTEGER PRIMARY KEY,
            nombre TEXT NOT NULL,
            aplicado_en TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )


def obtener_versiones_aplicadas(conn: Any) -> Dict[int, str]:
    """Retorna un diccionario de {version: nombre} de las migraciones aplicadas."""
    _asegurar_tabla_schema_migrations(conn)
    cursor = conn.execute(
        "SELECT version, nombre FROM _schema_migrations ORDER BY version ASC;"
    )
    return {int(row[0]): str(row[1]) for row in cursor.fetchall()}


def listar_archivos_migracion(directorio: Optional[Path] = None) -> List[Tuple[int, str, Path]]:
    """Descubre y ordena los archivos de migración (formato: NNN_nombre.sql)."""
    dir_path = Path(directorio) if directorio else RUTA_MIGRACIONES_DEFAULT
    if not dir_path.exists():
        raise ErrorMigraciones(f"El directorio de migraciones '{dir_path}' no existe.")

    migraciones = []
    patron = re.compile(r"^(\d+)_([a-zA-Z0-9_-]+)\.sql$")
    for f in dir_path.glob("*.sql"):
        match = patron.match(f.name)
        if match:
            version = int(match.group(1))
            nombre = match.group(2)
            migraciones.append((version, nombre, f))

    # Orden estrictamente ascendente por número de versión
    migraciones.sort(key=lambda x: x[0])
    return migraciones


def _verificar_consistencia_previa(conn: Any, version: int, path_archivo: Path, versiones_aplicadas: Set[int]) -> None:
    """Verifica inconsistencias conocidas entre la base de datos y la tabla de versiones antes de ejecutar.
    
    Caso crítico de la migración 009: Si costo_unitario ya existe en ticket_items pero la versión 9
    no está registrada en _schema_migrations, se aborta con error explícito para no causar
    un fallo opaco de 'duplicate column name' ni alterar datos a ciegas.
    """
    if version == 9 and 9 not in versiones_aplicadas:
        cursor = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ticket_items';"
        )
        if cursor.fetchone() is not None:
            cols = [
                row[1] for row in conn.execute("PRAGMA table_info(ticket_items);").fetchall()
            ]
            if "costo_unitario" in cols:
                raise ErrorInconsistenciaEsquema(
                    "Inconsistencia crítica de esquema: la columna 'costo_unitario' ya existe en 'ticket_items', "
                    "pero la versión 9 no figura registrada en _schema_migrations. "
                    "Se detiene la ejecución para prevenir alteraciones destructivas o reintentos fallidos."
                )

    if version == 10 and 10 not in versiones_aplicadas:
        problemas = []

        # 1. Comprobar columna idempotency_key en cargas_servicios
        cur = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cargas_servicios';")
        if cur.fetchone() is not None:
            cols_cargas = [row[1] for row in conn.execute("PRAGMA table_info(cargas_servicios);").fetchall()]
            if "idempotency_key" in cols_cargas:
                problemas.append("columna 'idempotency_key' ya existe en 'cargas_servicios'")

        # 2. Comprobar columna idempotency_key en gastos_retiros
        cur = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='gastos_retiros';")
        if cur.fetchone() is not None:
            cols_gastos = [row[1] for row in conn.execute("PRAGMA table_info(gastos_retiros);").fetchall()]
            if "idempotency_key" in cols_gastos:
                problemas.append("columna 'idempotency_key' ya existe en 'gastos_retiros'")

        # 3. Comprobar índices asociados preexistentes
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name IN ('uq_cargas_idempotency_key', 'uq_gastos_idempotency_key');"
        )
        indices = [row[0] for row in cur.fetchall()]
        for idx in indices:
            problemas.append(f"índice '{idx}' ya existe")

        if problemas:
            detalles = "; ".join(problemas)
            raise ErrorInconsistenciaEsquema(
                f"Inconsistencia crítica de esquema: la versión 10 no figura registrada en _schema_migrations, "
                f"pero se detectaron estructuras preexistentes ({detalles}). "
                "Se detiene la ejecución para prevenir alteraciones destructivas o fallos de columna duplicada. "
                "Requiere revisión manual del esquema."
            )

    if version == 11 and 11 not in versiones_aplicadas:
        cur = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='auth_scram_challenges';"
        )
        if cur.fetchone() is not None:
            cols_scram = {
                row[1]
                for row in conn.execute("PRAGMA table_info(auth_scram_challenges);").fetchall()
            }
            requeridas_scram = {"jti", "exp", "used", "creado_en_utc"}
            faltantes_scram = sorted(requeridas_scram - cols_scram)
            if faltantes_scram:
                raise ErrorInconsistenciaEsquema(
                    f"Inconsistencia crítica de esquema: la tabla 'auth_scram_challenges' existe sin la versión 11 "
                    f"en _schema_migrations y carece de las columnas requeridas: {faltantes_scram}."
                )

    if version == 12 and 12 not in versiones_aplicadas:
        cur = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='commercial_events_outbox';"
        )
        if cur.fetchone() is not None:
            cols_outbox = {
                row[1]
                for row in conn.execute("PRAGMA table_info(commercial_events_outbox);").fetchall()
            }
            requeridas_outbox = {
                "sequence_id",
                "event_id",
                "event_type",
                "entity_type",
                "entity_id",
                "action_type",
                "idempotency_key",
                "payload_canonical_hash",
                "payload",
                "status",
            }
            faltantes_outbox = sorted(requeridas_outbox - cols_outbox)
            if faltantes_outbox:
                raise ErrorInconsistenciaEsquema(
                    f"Inconsistencia crítica de esquema: la tabla 'commercial_events_outbox' existe sin la versión 12 "
                    f"en _schema_migrations y carece de las columnas requeridas: {faltantes_outbox}."
                )


def ejecutar_migraciones(
    conn: Any,
    directorio: Optional[Path] = None,
    hasta_version: Optional[int] = None,
) -> List[int]:
    """Aplica secuencialmente todas las migraciones pendientes en orden estricto.
    
    Retorna la lista de versiones aplicadas en esta ejecución.
    Si una migración ya está aplicada, se omite de forma idempotente.
    Si una migración falla, se detiene el proceso y se emite diagnóstico claro.
    """
    _asegurar_tabla_schema_migrations(conn)
    versiones_aplicadas_dict = obtener_versiones_aplicadas(conn)
    versiones_aplicadas_set = set(versiones_aplicadas_dict.keys())

    archivos = listar_archivos_migracion(directorio)
    versiones_nuevas: List[int] = []

    for version, nombre_corto, archivo_path in archivos:
        if hasta_version is not None and version > hasta_version:
            break

        if version in versiones_aplicadas_set:
            logger.debug(
                "Migración %03d ya aplicada (%s). Omitiendo.",
                version,
                versiones_aplicadas_dict[version],
            )
            continue

        # Validaciones de consistencia previa
        _verificar_consistencia_previa(conn, version, archivo_path, versiones_aplicadas_set)

        logger.info("Aplicando migración %03d desde %s...", version, archivo_path.name)
        contenido_sql = archivo_path.read_text(encoding="utf-8")

        # Ejecución transaccional atómica
        try:
            # Si el script contiene BEGIN y COMMIT, executescript ejecuta el lote completo.
            conn.executescript(contenido_sql)
        except Exception as exc:
            msg = str(exc)
            # Revertir cualquier transacción abierta pendiente de commit
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                pass
            logger.error("Error en migración %03d (%s): %s", version, archivo_path.name, msg)
            raise ErrorEjecucionMigracion(version, archivo_path.name, msg) from exc

        # Verificación post-aplicación: la versión debe haber quedado registrada
        versiones_post = obtener_versiones_aplicadas(conn)
        if version not in versiones_post:
            raise ErrorMigraciones(
                f"La migración {version:03d} ({archivo_path.name}) se ejecutó pero no registró su versión "
                f"en la tabla _schema_migrations."
            )

        versiones_aplicadas_set.add(version)
        versiones_nuevas.append(version)
        logger.info("✓ Migración %03d aplicada y registrada exitosamente.", version)

    return versiones_nuevas
