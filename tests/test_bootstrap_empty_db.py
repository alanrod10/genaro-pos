"""Pruebas de validación del bootstrap de base vacía y compatibilidad de migraciones (001-011).

Verifica:
1. Inicialización desde base de datos completamente nueva y vacía (sin preexistencias).
2. Compatibilidad hacia atrás con base de datos preexistente que ya contiene el catálogo.
3. Idempotencia del runner de migraciones ante ejecuciones consecutivas.
4. Readiness final del esquema ('PREPARADO', listo: True, 11 migraciones aplicadas).
5. Comportamiento fail-closed y rollback atómico si una migración no puede completarse.
"""

from pathlib import Path
import sqlite3
import pytest

from db.migrations_runner import (
    ErrorEjecucionMigracion,
    ejecutar_migraciones,
    obtener_versiones_aplicadas,
    listar_archivos_migracion,
)
from db.readiness import comprobar_preparacion_esquema


def test_inicializacion_desde_base_vacia():
    """Una base SQLite nueva y vacía ejecuta las 11 migraciones y alcanza estado PREPARADO."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")

    # Verificar que inicialmente no existen tablas de usuario
    cur = conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';"
    )
    assert cur.fetchone()[0] == 0, "La base inicial debe estar completamente vacía"

    # Ejecutar pipeline completo
    versiones_aplicadas = ejecutar_migraciones(conn)
    assert len(versiones_aplicadas) >= 12
    assert all(i in versiones_aplicadas for i in range(1, 13))

    # Comprobar readiness
    readiness = comprobar_preparacion_esquema(conn)
    assert readiness["status"] == "PREPARADO"
    assert readiness["listo"] is True
    assert readiness["tablas_faltantes"] == []
    assert readiness["inconsistencias"] == []
    assert "productos" in readiness["tablas_existentes"]

    # Comprobar inserción canónica en la tabla productos generada
    conn.execute(
        """
        INSERT INTO productos (nombre, categoria, proveedor, costo, precio_dia, precio_noche, activo)
        VALUES ('Alfajor Test', 'Golosinas', 'Distribuidora', 200, 350, 400, 1);
        """
    )
    conn.commit()

    cur = conn.execute("SELECT id, nombre, margen_ratio FROM productos WHERE nombre = 'Alfajor Test';")
    row = cur.fetchone()
    assert row is not None
    assert row[1] == "Alfajor Test"
    # Margen virtual generado: (350 - 200) / 200 = 0.75
    assert row[2] == 0.75

    conn.close()


def test_compatibilidad_con_base_catalogo_preexistente():
    """Una base que ya posee la tabla productos y registros no sufre alteraciones ni pérdida de datos."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")

    # Crear catálogo previo (simulando estado legacy)
    conn.execute(
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
    conn.execute(
        "INSERT INTO productos (id, nombre, costo, precio_dia) VALUES (101, 'Producto Preexistente', 500, 1000);"
    )
    conn.commit()

    # Ejecutar pipeline
    versiones_nuevas = ejecutar_migraciones(conn)
    assert 1 in versiones_nuevas

    # Verificar que el registro preexistente está intacto
    cur = conn.execute("SELECT id, nombre, costo, precio_dia FROM productos WHERE id = 101;")
    row = cur.fetchone()
    assert row == (101, "Producto Preexistente", 500, 1000)

    # El registro de migraciones contiene todas las versiones
    versiones = obtener_versiones_aplicadas(conn)
    assert len(versiones) >= 12
    assert all(i in versiones for i in range(1, 13))

    conn.close()


def test_ejecucion_idempotente_del_runner():
    """Reejecutar el runner sobre una base ya migrada no aplica sentencias ni causa errores."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")

    # Primera ejecución
    primera_vez = ejecutar_migraciones(conn)
    assert len(primera_vez) >= 12

    # Segunda ejecución: debe detectar que todo está aplicado y retornar lista vacía
    segunda_vez = ejecutar_migraciones(conn)
    assert segunda_vez == []

    # Tercera ejecución: sigue intacto
    tercera_vez = ejecutar_migraciones(conn)
    assert tercera_vez == []

    conn.close()


def test_readiness_final_del_esquema():
    """El reporte de readiness confirma preparación total y ausencia de inconsistencias."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")

    ejecutar_migraciones(conn)
    resultado = comprobar_preparacion_esquema(conn)

    assert resultado["status"] == "PREPARADO"
    assert resultado["listo"] is True
    assert resultado["migraciones_pendientes"] == []
    assert resultado["columnas_faltantes"] == []
    assert resultado["indices_faltantes"] == []
    assert resultado["indices_incorrectos"] == []
    assert len(resultado["tablas_existentes"]) >= 15

    conn.close()


def test_fallo_seguro_con_rollback_si_migracion_falla(tmp_path):
    """Si una migración falla por sintaxis o violación, se aborta y no se registra la versión fallida."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")

    # Aplicar hasta la versión 2 normalmente
    ejecutar_migraciones(conn, hasta_version=2)
    versiones_previas = obtener_versiones_aplicadas(conn)
    assert set(versiones_previas.keys()) == {1, 2}

    # Crear directorio con una migración inválida como 003
    dir_test = tmp_path / "migrations_corruptas"
    dir_test.mkdir()

    # Copiar migración 001 y 002 legítimas
    from db.migrations_runner import RUTA_MIGRACIONES_DEFAULT
    dir_origen = RUTA_MIGRACIONES_DEFAULT
    (dir_test / "001_baseline_catalogo_existente.sql").write_text((dir_origen / "001_baseline_catalogo_existente.sql").read_text())
    (dir_test / "002_cargas_servicios.sql").write_text((dir_origen / "002_cargas_servicios.sql").read_text())

    # Crear 003 corrupta con error de sintaxis SQL
    (dir_test / "003_corrupta.sql").write_text(
        "BEGIN;\nCREATE TABLE tabla_invalida (id INT);\nESTA_INSTRUCCION_ES_INVALIDA_SYNTAX_ERROR;\nCOMMIT;"
    )

    with pytest.raises(ErrorEjecucionMigracion) as excinfo:
        ejecutar_migraciones(conn, directorio=dir_test)

    assert excinfo.value.version == 3

    # Comprobar que la versión 3 NO quedó registrada en _schema_migrations
    versiones_post = obtener_versiones_aplicadas(conn)
    assert 3 not in versiones_post
    assert set(versiones_post.keys()) == {1, 2}

    # Comprobar que la tabla no quedó creada (rollback atómico)
    cur = conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='tabla_invalida';"
    )
    assert cur.fetchone()[0] == 0

    conn.close()
