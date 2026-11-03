"""Ejecutor de Migraciones Estructurales para Base de Datos de Tesorería — GenaroPOS.

Asegura:
1. Versionado secuencial registrado en _tesoreria_schema_migrations.
2. Reconstrucción segura de tablas SQLite (12-step procedure) para ampliación de CHECK constraints.
3. Transaccionalidad atómica por migración con rollback ante cualquier fallo.
4. Preservación absoluta de identificadores, saldos y relaciones foráneas.
5. Idempotencia: ejecuciones repetidas no alteran el estado ni duplican datos.
"""

from datetime import datetime
import logging
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("genaro_pos.tesoreria_migrations")

RUTA_MIGRACIONES_TESORERIA = Path(__file__).resolve().parent / "migrations"


class ErrorMigracionesTesoreria(Exception):
    """Excepción base para fallos del sistema de migraciones de Tesorería."""
    pass


class ErrorEjecucionMigracionTesoreria(ErrorMigracionesTesoreria):
    """Fallo durante la ejecución de una migración de Tesorería."""
    def __init__(self, version: int, nombre_archivo: str, mensaje_original: str):
        super().__init__(
            f"Fallo crítico al aplicar migración de Tesorería {version:03d} ({nombre_archivo}): {mensaje_original}"
        )
        self.version = version
        self.nombre_archivo = nombre_archivo
        self.mensaje_original = mensaje_original


def _asegurar_tabla_schema_migrations(conn: sqlite3.Connection) -> None:
    """Crea la tabla de control de versiones de tesorería si no existe."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS _tesoreria_schema_migrations (
            version INTEGER PRIMARY KEY,
            nombre TEXT NOT NULL,
            aplicado_en TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )


def obtener_versiones_aplicadas(conn: sqlite3.Connection) -> Dict[int, str]:
    """Retorna un diccionario de {version: nombre} de las migraciones de tesorería aplicadas."""
    _asegurar_tabla_schema_migrations(conn)
    cursor = conn.execute(
        "SELECT version, nombre FROM _tesoreria_schema_migrations ORDER BY version ASC;"
    )
    return {int(row[0]): str(row[1]) for row in cursor.fetchall()}


def listar_archivos_migracion(directorio: Optional[Path] = None) -> List[Tuple[int, str, Path]]:
    """Descubre y ordena los archivos de migración de tesorería (NNN_nombre.sql)."""
    dir_path = Path(directorio) if directorio else RUTA_MIGRACIONES_TESORERIA
    if not dir_path.exists():
        raise ErrorMigracionesTesoreria(f"Directorio de migraciones de tesorería '{dir_path}' no existe.")

    migraciones = []
    patron = re.compile(r"^(\d+)_([a-zA-Z0-9_-]+)\.sql$")
    for f in dir_path.glob("*.sql"):
        match = patron.match(f.name)
        if match:
            version = int(match.group(1))
            nombre = match.group(2)
            migraciones.append((version, nombre, f))

    migraciones.sort(key=lambda x: x[0])
    return migraciones


def migrar_tabla_fondos_si_requiere_1160(conn: sqlite3.Connection) -> bool:
    """Verifica si tesoreria_fondos tiene la restricción CHECK de 9 fondos y la reconstruye a 10 fondos (1160).
    
    Implementa el procedimiento oficial de SQLite de 12 pasos preservando datos, IDs y relaciones FK.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='tesoreria_fondos';")
    row = cursor.fetchone()
    if not row or not row[0]:
        return False

    sql_def = row[0]
    # Si '1160' ya está presente en el CHECK, no requiere reconstrucción
    if "'1160'" in sql_def or '"1160"' in sql_def:
        return False

    logger.info("Detectada restricción CHECK de 9 fondos en tesoreria_fondos. Iniciando reconstrucción a 10 fondos...")

    # Deshabilitar claves foráneas antes de modificar estructura
    if conn.in_transaction:
        conn.commit()

    conn.execute("PRAGMA foreign_keys = OFF;")
    try:
        if not conn.in_transaction:
            conn.execute("BEGIN TRANSACTION;")

        # 1. Crear tabla temporal con el CHECK actualizado
        conn.execute(
            """
            CREATE TABLE tesoreria_fondos_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo TEXT NOT NULL UNIQUE CHECK(codigo IN ('1110', '1120', '1130', '1140', '1150', '1160', '1210', '1220', '1230', '1310')),
                nombre TEXT NOT NULL CHECK(length(trim(nombre)) > 0),
                naturaleza TEXT NOT NULL CHECK(naturaleza IN ('EFECTIVO', 'FONDO_FIJO', 'ADICIONALES_PARTICULARES', 'RECAUDACION_TERCEROS', 'CUENTA_BANCARIA', 'BILLETERA_VIRTUAL', 'TRANSITO', 'TRANSITO_PREVENTISTAS')),
                titularidad TEXT NOT NULL CHECK(titularidad IN ('PROPIA', 'CUSTODIA', 'TRANSITO')),
                moneda TEXT NOT NULL DEFAULT 'ARS' CHECK(moneda = 'ARS'),
                estado TEXT NOT NULL DEFAULT 'ACTIVO' CHECK(estado IN ('ACTIVO', 'INACTIVO', 'BLOQUEADO')),
                saldo_centavos INTEGER NOT NULL DEFAULT 0 CHECK(typeof(saldo_centavos) = 'integer'),
                permite_sobregiro INTEGER NOT NULL DEFAULT 0 CHECK(permite_sobregiro IN (0, 1)),
                version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                creado_por TEXT NOT NULL DEFAULT 'SISTEMA_LAB',
                actualizado_por TEXT NOT NULL DEFAULT 'SISTEMA_LAB'
            );
            """
        )

        # 2. Copiar exactamente todos los registros existentes preservando IDs y saldos
        conn.execute(
            """
            INSERT INTO tesoreria_fondos_v2 (
                id, codigo, nombre, naturaleza, titularidad, moneda,
                estado, saldo_centavos, permite_sobregiro, version,
                created_at, updated_at, creado_por, actualizado_por
            )
            SELECT
                id, codigo, nombre, naturaleza, titularidad, moneda,
                estado, saldo_centavos, permite_sobregiro, version,
                created_at, updated_at, creado_por, actualizado_por
            FROM tesoreria_fondos;
            """
        )

        # 3. Eliminar tabla antigua y renombrar
        conn.execute("DROP TABLE tesoreria_fondos;")
        conn.execute("ALTER TABLE tesoreria_fondos_v2 RENAME TO tesoreria_fondos;")

        # 4. Recrear índices
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tesoreria_fondos_codigo ON tesoreria_fondos(codigo);")

        # 5. Recrear triggers de integridad e inmutabilidad (destruidos por DROP TABLE)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tesoreria_fondos_auditoria (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fondo_id INTEGER NOT NULL,
                codigo TEXT NOT NULL,
                accion TEXT NOT NULL CHECK(accion IN ('INSERT', 'UPDATE', 'DELETE_ATTEMPT', 'BLOQUEO')),
                usuario TEXT NOT NULL,
                valores_previos TEXT,
                valores_nuevos TEXT,
                motivo TEXT,
                timestamp_utc TEXT NOT NULL,
                FOREIGN KEY (fondo_id) REFERENCES tesoreria_fondos(id) ON DELETE RESTRICT
            );
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_proteger_borrado_fondos
            BEFORE DELETE ON tesoreria_fondos
            BEGIN
                SELECT RAISE(ABORT, 'VIOLACIÓN DE INTEGRIDAD: No está permitido eliminar fondos del catálogo de tesorería.');
            END;
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_audit_insert_fondo
            AFTER INSERT ON tesoreria_fondos
            BEGIN
                INSERT INTO tesoreria_fondos_auditoria (
                    fondo_id, codigo, accion, usuario, valores_previos, valores_nuevos, motivo, timestamp_utc
                ) VALUES (
                    NEW.id,
                    NEW.codigo,
                    'INSERT',
                    NEW.creado_por,
                    NULL,
                    json_object(
                        'codigo', NEW.codigo,
                        'nombre', NEW.nombre,
                        'naturaleza', NEW.naturaleza,
                        'titularidad', NEW.titularidad,
                        'estado', NEW.estado,
                        'saldo_centavos', NEW.saldo_centavos
                    ),
                    'Alta inicial en catálogo',
                    NEW.created_at
                );
            END;
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_audit_update_fondo
            AFTER UPDATE ON tesoreria_fondos
            BEGIN
                INSERT INTO tesoreria_fondos_auditoria (
                    fondo_id, codigo, accion, usuario, valores_previos, valores_nuevos, motivo, timestamp_utc
                ) VALUES (
                    NEW.id,
                    NEW.codigo,
                    'UPDATE',
                    NEW.actualizado_por,
                    json_object(
                        'codigo', OLD.codigo,
                        'nombre', OLD.nombre,
                        'naturaleza', OLD.naturaleza,
                        'titularidad', OLD.titularidad,
                        'estado', OLD.estado,
                        'saldo_centavos', OLD.saldo_centavos,
                        'version', OLD.version
                    ),
                    json_object(
                        'codigo', NEW.codigo,
                        'nombre', NEW.nombre,
                        'naturaleza', NEW.naturaleza,
                        'titularidad', NEW.titularidad,
                        'estado', NEW.estado,
                        'saldo_centavos', NEW.saldo_centavos,
                        'version', NEW.version
                    ),
                    'Modificación de atributos',
                    NEW.updated_at
                );
            END;
            """
        )

        # 6. Validar integridad referencial completa
        fk_violations = conn.execute("PRAGMA foreign_key_check;").fetchall()
        if fk_violations:
            raise ErrorMigracionesTesoreria(
                f"Violación de claves foráneas al reconstruir tesoreria_fondos: {fk_violations}"
            )

        if conn.in_transaction:
            conn.commit()
        logger.info("Tabla tesoreria_fondos reconstruida con éxito a 10 fondos.")
        return True
    except Exception as e:
        if conn.in_transaction:
            conn.rollback()
        raise ErrorMigracionesTesoreria(f"Fallo al reconstruir tesoreria_fondos: {e}") from e
    finally:
        conn.execute("PRAGMA foreign_keys = ON;")


def ejecutar_migraciones_tesoreria(
    conn: sqlite3.Connection,
    directorio: Optional[Path] = None,
) -> List[int]:
    """Aplica de forma secuencial y transaccional todas las migraciones pendientes de Tesorería."""
    _asegurar_tabla_schema_migrations(conn)
    versiones_aplicadas = obtener_versiones_aplicadas(conn)
    archivos = listar_archivos_migracion(directorio)

    aplicadas_ahora: List[int] = []

    for version, nombre, path_archivo in archivos:
        if version in versiones_aplicadas:
            continue

        # Hook especial para la versión 3 (o cualquier versión que requiera el CHECK de 1160)
        if version == 3:
            migrar_tabla_fondos_si_requiere_1160(conn)

        try:
            with open(path_archivo, "r", encoding="utf-8") as f:
                contenido_sql = f.read()

            if conn.in_transaction:
                conn.commit()

            conn.executescript(contenido_sql)
            conn.execute(
                "INSERT INTO _tesoreria_schema_migrations (version, nombre) VALUES (?, ?);",
                (version, nombre),
            )
            conn.commit()
            aplicadas_ahora.append(version)
            logger.info("Migración de tesorería %03d (%s) aplicada con éxito.", version, nombre)
        except Exception as e:
            if conn.in_transaction:
                conn.rollback()
            logger.error("Error crítico en migración de tesorería %03d: %s", version, e)
            raise ErrorEjecucionMigracionTesoreria(version, path_archivo.name, str(e)) from e

    return aplicadas_ahora
