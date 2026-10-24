"""Pruebas unitarias para la integración del catálogo de productos con Turso (Fase 14.2-A).

Verifica de forma estrictamente local (sin red):
1. La opción desactivada conserva la ruta original por defecto (SHEETS).
2. La opción activada consulta el adaptador Turso (TURSO).
3. Un catálogo vacío se distingue de un error de acceso.
4. Los errores del adaptador no producen fallback silencioso a Sheets.
5. La conversión del resultado conserva columnas y tipos esperados por la aplicación.
6. Los identificadores no dependen de la unicidad del nombre (soporta nombres duplicados con IDs únicos).
7. Los productos inactivos (activo = 0) no se cargan al catálogo activo.
8. Importar los módulos nuevos no requiere secretos ni establece conexiones de red.
"""

import os
import sqlite3
import pytest
from db.catalog import (
    BACKEND_SHEETS,
    BACKEND_TURSO,
    COLUMNAS_CATALOGO_POS,
    ENV_CATALOGO_BACKEND,
    ErrorAccesoCatalogoTurso,
    cargar_catalogo_turso,
    cargar_catalogo_turso_filas,
    obtener_backend_catalogo,
)


@pytest.fixture
def conn_memoria():
  """Crea una base SQLite en memoria con el esquema canónico de productos."""
  conn = sqlite3.connect(":memory:")
  conn.execute("""
        CREATE TABLE productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL COLLATE NOCASE,
            categoria TEXT NOT NULL DEFAULT 'Otro',
            proveedor TEXT NOT NULL DEFAULT 'General',
            unidad TEXT NOT NULL DEFAULT 'Unidad',
            costo INTEGER NOT NULL DEFAULT 0 CHECK (costo >= 0),
            precio_dia INTEGER NOT NULL CHECK (precio_dia >= 0),
            precio_noche INTEGER NOT NULL DEFAULT 0,
            margen_ratio REAL,
            activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
            fecha_actualizacion TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
  conn.commit()
  yield conn
  conn.close()


def test_opcion_desactivada_conserva_ruta_original_sheets(monkeypatch):
  """Valida que por defecto la variable CATALOGO_BACKEND selecciona SHEETS."""
  monkeypatch.delenv(ENV_CATALOGO_BACKEND, raising=False)
  assert obtener_backend_catalogo() == BACKEND_SHEETS

  # Cualquier valor no reconocido también retorna de forma segura SHEETS
  monkeypatch.setenv(ENV_CATALOGO_BACKEND, "DESCONOCIDO")
  assert obtener_backend_catalogo() == BACKEND_SHEETS

  monkeypatch.setenv(ENV_CATALOGO_BACKEND, "sheets")
  assert obtener_backend_catalogo() == BACKEND_SHEETS


def test_opcion_activada_consulta_adaptador_turso(monkeypatch):
  """Valida que configurando CATALOGO_BACKEND=TURSO se activa el backend Turso."""
  monkeypatch.setenv(ENV_CATALOGO_BACKEND, "TURSO")
  assert obtener_backend_catalogo() == BACKEND_TURSO

  monkeypatch.setenv(ENV_CATALOGO_BACKEND, "turso")
  assert obtener_backend_catalogo() == BACKEND_TURSO


def test_catalogo_vacio_se_distingue_de_error_de_acceso(conn_memoria):
  """Valida que un catálogo vacío retorna una estructura vacía sin elevar excepción, mientras que un fallo de acceso eleva ErrorAccesoCatalogoTurso."""
  # 1. Catálogo vacío legítimo
  resultado = cargar_catalogo_turso(conn=conn_memoria)
  assert len(resultado) == 0
  assert resultado.empty is True
  for col in COLUMNAS_CATALOGO_POS:
    assert col in resultado.columns

  # 2. Error de acceso (tabla inexistente o conexión cerrada)
  conn_cerrada = sqlite3.connect(":memory:")
  conn_cerrada.close()
  with pytest.raises(ErrorAccesoCatalogoTurso) as exc_info:
    cargar_catalogo_turso(conn=conn_cerrada)
  assert "Error al consultar la tabla 'productos'" in str(exc_info.value)


def test_errores_adaptador_sin_credenciales_elevan_excepcion_controlada(
    monkeypatch,
):
  """Valida que invocar el adaptador sin conexión y sin credenciales en el entorno produce ErrorAccesoCatalogoTurso inmediato sin fallback silencioso."""
  monkeypatch.delenv("TURSO_DEV_DATABASE_URL", raising=False)
  monkeypatch.delenv("TURSO_DEV_AUTH_TOKEN", raising=False)

  with pytest.raises(ErrorAccesoCatalogoTurso) as exc_info:
    cargar_catalogo_turso(conn=None)

  assert "TURSO_DEV_DATABASE_URL" in str(exc_info.value)


def test_conversion_resultado_conserva_columnas_y_tipos(conn_memoria):
  """Valida que los registros de la tabla 'productos' se mapean a las columnas y tipos esperados por Genaro POS."""
  conn_memoria.execute("""
        INSERT INTO productos (id, nombre, categoria, proveedor, unidad, costo, precio_dia, precio_noche, margen_ratio, activo, fecha_actualizacion)
        VALUES (1, 'Gaseosa Cola 1.5L', 'Bebidas', 'Distribuidora Norte', 'Unidad', 800, 1200, 1200, 0.5, 1, '2026-10-22 12:00:00');
    """)
  conn_memoria.commit()

  filas = cargar_catalogo_turso_filas(conn=conn_memoria)
  assert len(filas) == 1
  f = filas[0]

  assert f["ID_PRODUCTO"] == 1
  assert f["NOMBRE"] == "Gaseosa Cola 1.5L"
  assert f["CATEGORIA"] == "Bebidas"
  assert f["PROVEEDOR"] == "Distribuidora Norte"
  assert f["UNIDAD"] == "Unidad"
  assert f["COSTO"] == 800
  assert f["PRECIO_DIA"] == 1200
  assert f["PRECIO_NOCHE"] == 1200
  assert f["MARGEN_%"] == 0.5
  assert f["FECHA_ACT"] == "2026-10-22 12:00:00"

  frame = cargar_catalogo_turso(conn=conn_memoria)
  assert len(frame) == 1
  assert frame.empty is False
  assert list(frame.columns) == COLUMNAS_CATALOGO_POS


def test_identificadores_no_dependen_de_unicidad_nombre(conn_memoria):
  """Valida que productos con el mismo nombre y distintos proveedores/costos conserven su ID numérico propio y no se fusionen ni sobrescriban."""
  conn_memoria.execute("""
        INSERT INTO productos (id, nombre, categoria, proveedor, unidad, costo, precio_dia, precio_noche, margen_ratio, activo)
        VALUES (101, 'Galletas Pitusas 160gr', 'Almacén', 'Mayorista Central', 'Unidad', 400, 600, 600, 0.5, 1),
               (102, 'Galletas Pitusas 160gr', 'Almacén', 'Distribuidora Sur', 'Unidad', 450, 700, 700, 0.55, 1);
    """)
  conn_memoria.commit()

  filas = cargar_catalogo_turso_filas(conn=conn_memoria)
  assert len(filas) == 2

  ids = [f["ID_PRODUCTO"] for f in filas]
  assert ids == [101, 102]
  assert filas[0]["PROVEEDOR"] == "Mayorista Central"
  assert filas[1]["PROVEEDOR"] == "Distribuidora Sur"
  assert filas[0]["PRECIO_DIA"] == 600
  assert filas[1]["PRECIO_DIA"] == 700


def test_productos_inactivos_no_se_cargan(conn_memoria):
  """Valida que los productos dados de baja lógica (activo = 0) no aparezcan en el catálogo activo."""
  conn_memoria.execute("""
        INSERT INTO productos (id, nombre, categoria, proveedor, unidad, costo, precio_dia, activo)
        VALUES (1, 'Producto Vigente', 'Almacén', 'Prov A', 'Unidad', 100, 200, 1),
               (2, 'Producto Descontinuado', 'Almacén', 'Prov B', 'Unidad', 100, 200, 0);
    """)
  conn_memoria.commit()

  filas = cargar_catalogo_turso_filas(conn=conn_memoria)
  assert len(filas) == 1
  assert filas[0]["ID_PRODUCTO"] == 1
  assert filas[0]["NOMBRE"] == "Producto Vigente"


def test_importacion_no_requiere_secretos_ni_red(monkeypatch):
  """Valida que importar db.catalog sin ninguna variable de entorno definida no arroja error ni intenta conectar a la red."""
  monkeypatch.delenv("TURSO_DEV_DATABASE_URL", raising=False)
  monkeypatch.delenv("TURSO_DEV_AUTH_TOKEN", raising=False)
  monkeypatch.delenv("ENV_NAME", raising=False)
  monkeypatch.delenv("TURSO_TEST_APPROVED", raising=False)

  import db.catalog

  assert hasattr(db.catalog, "cargar_catalogo_turso")
  assert hasattr(db.catalog, "obtener_backend_catalogo")
  assert db.catalog.obtener_backend_catalogo() == BACKEND_SHEETS


def test_contrato_integracion_sin_fallback_silencioso(monkeypatch):
  """Valida que la lógica de integración cuando CATALOGO_BACKEND='TURSO' propaga ErrorAccesoCatalogoTurso y no oculta el fallo ni vuelve a Sheets."""
  monkeypatch.setenv(ENV_CATALOGO_BACKEND, "TURSO")
  monkeypatch.delenv("TURSO_DEV_DATABASE_URL", raising=False)
  monkeypatch.delenv("TURSO_DEV_AUTH_TOKEN", raising=False)

  from db.catalog import (
      BACKEND_TURSO,
      ErrorAccesoCatalogoTurso,
      cargar_catalogo_turso,
      obtener_backend_catalogo,
  )

  backend = obtener_backend_catalogo()
  assert backend == BACKEND_TURSO

  # Si se intenta cargar el catálogo de Turso sin credenciales, debe fallar ruidosamente
  with pytest.raises(ErrorAccesoCatalogoTurso):
    cargar_catalogo_turso(conn=None)

