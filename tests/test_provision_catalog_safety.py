"""Pruebas unitarias locales para el aprovisionamiento seguro del catálogo sintético (Fase 14.2-B).

Verifica sin red:
1. El modo dry-run (validación previa) no escribe datos.
2. La falta de aprobación explícita bloquea el procedimiento.
3. Un entorno distinto de 'genaro-pos-dev' produce rechazo inmediato.
4. Destinos remotos no verificables o maliciosos son rechazados.
5. IDs o datos en conflicto no provocan limpieza ni sobrescrituras silenciosas.
6. El reintento sobre datos idénticos es seguro e idempotente.
7. La carga sintética representa fielmente los casos de prueba (nombres duplicados, activo/inactivo, costo 0).
8. Cero exposición de secretos o tokens en mensajes de error o logs.
"""

import os
import sqlite3
import pytest
from db.catalog import cargar_catalogo_turso
from db.provision_catalog import (
    DESTINO_AUTORIZADO_HOST,
    DESTINO_AUTORIZADO_SCHEME,
    ESTADO_APROVISIONADO_EXITO,
    ESTADO_BLOQUEADO_PREFLIGHT,
    ESTADO_CONFLICTO_DATOS,
    ESTADO_VALIDACION_OK,
    ESTADO_YA_APROVISIONADO,
    PRODUCTOS_SINTETICOS,
    aprovisionar_catalogo_sintetico,
    inspeccionar_estado_tabla_productos,
    validar_endpoint_estructurado,
    validar_preflight_aprovisionamiento,
)


@pytest.fixture
def conn_productos():
  """Crea una base SQLite en memoria con la tabla productos de db/schema_v1.sql."""
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
            margen_ratio REAL GENERATED ALWAYS AS (
                CASE WHEN costo > 0 THEN ROUND((CAST(precio_dia AS REAL) - costo) / costo, 4) ELSE 0.0 END
            ) VIRTUAL,
            activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
            fecha_actualizacion TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
  conn.commit()
  yield conn
  conn.close()


def test_dry_run_valida_sin_escribir(conn_productos):
  """Valida que en modo dry_run=True no se inserta ninguna fila en la base de datos."""
  exito, estado, resumen = aprovisionar_catalogo_sintetico(
      conn=conn_productos, dry_run=True
  )

  assert exito is True
  assert estado == ESTADO_VALIDACION_OK
  assert resumen["dry_run"] is True
  assert resumen["filas_insertadas"] == 0

  cursor = conn_productos.execute("SELECT COUNT(*) FROM productos;")
  assert cursor.fetchone()[0] == 0


def test_falta_de_aprobacion_bloquea_ejecucion(monkeypatch):
  """Valida que TURSO_TEST_APPROVED != 'YES' detiene el procedimiento antes de conectar."""
  monkeypatch.setenv("ENV_NAME", "genaro-pos-dev")
  monkeypatch.delenv("TURSO_TEST_APPROVED", raising=False)
  monkeypatch.setenv(
      "TURSO_DEV_DATABASE_URL",
      f"{DESTINO_AUTORIZADO_SCHEME}://{DESTINO_AUTORIZADO_HOST}",
  )
  monkeypatch.setenv("TURSO_DEV_AUTH_TOKEN", "fake_token")

  aprobado, bloqueos = validar_preflight_aprovisionamiento()
  assert aprobado is False
  assert any("TURSO_TEST_APPROVED" in b for b in bloqueos)

  exito, estado, resumen = aprovisionar_catalogo_sintetico(conn=None)
  assert exito is False
  assert estado == ESTADO_BLOQUEADO_PREFLIGHT


def test_entorno_distinto_de_desarrollo_produce_rechazo(monkeypatch):
  """Valida que un entorno distinto de 'genaro-pos-dev' es rechazado sin excepción."""
  monkeypatch.setenv("ENV_NAME", "produccion")
  monkeypatch.setenv("TURSO_TEST_APPROVED", "YES")

  aprobado, bloqueos = validar_preflight_aprovisionamiento()
  assert aprobado is False
  assert any("ENV_NAME no está configurado" in b for b in bloqueos)


def test_destino_no_verificable_o_falso_se_rechaza():
  """Valida el análisis sintáctico de URLs contra endpoints no autorizados."""
  urls_invalidas = [
      "https://genaro-pos-dev-alanrod10.aws-sa-east-1.turso.io",
      f"{DESTINO_AUTORIZADO_SCHEME}://otro-host.turso.io",
      f"{DESTINO_AUTORIZADO_SCHEME}://usuario:secreto@{DESTINO_AUTORIZADO_HOST}",
      f"{DESTINO_AUTORIZADO_SCHEME}://{DESTINO_AUTORIZADO_HOST}:8080",
      f"{DESTINO_AUTORIZADO_SCHEME}://{DESTINO_AUTORIZADO_HOST}/v1/query",
      f"{DESTINO_AUTORIZADO_SCHEME}://{DESTINO_AUTORIZADO_HOST}?admin=1",
      f"{DESTINO_AUTORIZADO_SCHEME}://{DESTINO_AUTORIZADO_HOST}#seccion",
  ]
  for url in urls_invalidas:
    valido, detalle = validar_endpoint_estructurado(url)
    assert valido is False
    assert len(detalle) > 0


def test_datos_en_conflicto_no_provocan_limpieza_ni_sobrescritura(
    conn_productos,
):
  """Valida que ante filas existentes ajenas al set sintético se aborta sin borrar ni alterar los datos."""
  conn_productos.execute("""
        INSERT INTO productos (id, nombre, categoria, proveedor, unidad, costo, precio_dia, activo)
        VALUES (101, 'Producto Preexistente', 'Almacén', 'Proveedor Real', 'Unidad', 100, 200, 1);
    """)
  conn_productos.commit()

  exito, estado, resumen = aprovisionar_catalogo_sintetico(
      conn=conn_productos, dry_run=False
  )

  assert exito is False
  assert estado == ESTADO_CONFLICTO_DATOS
  assert "registros ajenos o IDs fuera del set" in resumen["detalle"]

  # Garantía inquebrantable: el producto preexistente permanece intacto
  cursor = conn_productos.execute("SELECT id, nombre FROM productos;")
  filas = cursor.fetchall()
  assert len(filas) == 1
  assert filas[0][0] == 101
  assert filas[0][1] == "Producto Preexistente"


def test_reintento_idempotente_seguro(conn_productos):
  """Valida que una segunda ejecución sobre el catálogo ya aprovisionado reporta YA_APROVISIONADO sin duplicar filas."""
  # Primer aprovisionamiento
  exito1, estado1, resumen1 = aprovisionar_catalogo_sintetico(
      conn=conn_productos, dry_run=False
  )
  assert exito1 is True
  assert estado1 == ESTADO_APROVISIONADO_EXITO
  assert resumen1["filas_insertadas"] == len(PRODUCTOS_SINTETICOS)

  # Reintento (idempotencia)
  exito2, estado2, resumen2 = aprovisionar_catalogo_sintetico(
      conn=conn_productos, dry_run=False
  )
  assert exito2 is True
  assert estado2 == ESTADO_YA_APROVISIONADO
  assert resumen2["filas_insertadas"] == 0

  # Conteo se mantiene en exactamente 5
  cursor = conn_productos.execute("SELECT COUNT(*) FROM productos;")
  assert cursor.fetchone()[0] == len(PRODUCTOS_SINTETICOS)


def test_carga_sintetica_representa_casos_de_prueba(conn_productos):
  """Valida que el catálogo sintético aprovisionado se integra perfectamente con cargar_catalogo_turso."""
  exito, estado, _ = aprovisionar_catalogo_sintetico(
      conn=conn_productos, dry_run=False
  )
  assert exito is True

  # Consulta a través del adaptador de persistencia
  frame = cargar_catalogo_turso(conn=conn_productos)

  # 1. El producto inactivo (ID 9005) debe quedar excluido
  # Quedan exactamente 4 productos activos
  assert len(frame) == 4

  ids = [row["ID_PRODUCTO"] for _, row in frame.iterrows()]
  assert 9005 not in ids
  assert ids == [9001, 9002, 9003, 9004]

  # 2. Dos productos con el mismo nombre y distintos IDs
  p9002 = next(r for _, r in frame.iterrows() if r["ID_PRODUCTO"] == 9002)
  p9003 = next(r for _, r in frame.iterrows() if r["ID_PRODUCTO"] == 9003)

  assert (
      p9002["NOMBRE"]
      == p9003["NOMBRE"]
      == "Galletitas Ficticias Vainilla 150g"
  )
  assert p9002["PROVEEDOR"] != p9003["PROVEEDOR"]
  assert p9002["PRECIO_DIA"] == 600
  assert p9003["PRECIO_DIA"] == 700

  # 3. Producto con costo 0
  p9004 = next(r for _, r in frame.iterrows() if r["ID_PRODUCTO"] == 9004)
  assert p9004["COSTO"] == 0
  assert p9004["PRECIO_DIA"] == 50
  assert p9004["MARGEN_%"] == 0.0


def test_cero_exposicion_de_secretos_en_errores_o_logs(monkeypatch):
  """Valida que bajo ninguna circunstancia el valor del token o secreto aparece en mensajes de error o logs."""
  token_secreto_super_confidencial = "TURSO_SECRET_TOKEN_XYZ_9999"
  monkeypatch.setenv("ENV_NAME", "genaro-pos-dev")
  monkeypatch.setenv("TURSO_TEST_APPROVED", "NO")  # Provocar bloqueo
  monkeypatch.setenv(
      "TURSO_DEV_DATABASE_URL",
      f"{DESTINO_AUTORIZADO_SCHEME}://{DESTINO_AUTORIZADO_HOST}",
  )
  monkeypatch.setenv("TURSO_DEV_AUTH_TOKEN", token_secreto_super_confidencial)

  aprobado, bloqueos = validar_preflight_aprovisionamiento()
  assert aprobado is False

  bloqueos_str = " ".join(bloqueos)
  assert token_secreto_super_confidencial not in bloqueos_str

  exito, estado, resumen = aprovisionar_catalogo_sintetico(conn=None)
  assert exito is False
  resumen_str = str(resumen)
  assert token_secreto_super_confidencial not in resumen_str


def test_columna_generada_virtual_margen_ratio(conn_productos):
  """Valida la columna generada VIRTUAL para margen_ratio con costo > 0 y costo == 0."""
  conn_productos.execute("""
        INSERT INTO productos (id, nombre, categoria, proveedor, unidad, costo, precio_dia, activo)
        VALUES (8001, 'Item Con Costo', 'Test', 'Prov', 'Unidad', 400, 600, 1);
    """)
  conn_productos.execute("""
        INSERT INTO productos (id, nombre, categoria, proveedor, unidad, costo, precio_dia, activo)
        VALUES (8002, 'Item Sin Costo', 'Test', 'Prov', 'Unidad', 0, 50, 1);
    """)
  conn_productos.commit()

  cursor = conn_productos.execute(
      "SELECT id, costo, precio_dia, margen_ratio FROM productos WHERE id IN"
      " (8001, 8002) ORDER BY id ASC;"
  )
  filas = cursor.fetchall()

  assert len(filas) == 2
  # Item 8001: (600 - 400) / 400 = 0.5
  assert filas[0][0] == 8001
  assert filas[0][1] == 400
  assert filas[0][2] == 600
  assert filas[0][3] == 0.5

  # Item 8002: costo == 0 -> margen_ratio == 0.0 (evita division por cero)
  assert filas[1][0] == 8002
  assert filas[1][1] == 0
  assert filas[1][2] == 50
  assert filas[1][3] == 0.0

