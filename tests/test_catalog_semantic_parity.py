"""Pruebas unitarias de paridad semántica del catálogo de productos (Fase 14.2-E).

Verifica de forma estrictamente local y aislada (sin conexión a red):
1. Margen exacto de 50 % (costo 400, precio 600).
2. Margen no periódico / fraccional (costo 450, precio 700): evaluación del redondeo
   a 4 decimales vs fracción completa y coincidencia en presentación visual de 2 decimales.
3. Tratamiento de costo cero (costo 0, precio 50): ausencia de división por cero.
4. Margen negativo (costo superior al precio): contrato de venta a pérdida.
5. Nombres duplicados: conservación estricta de identidad por ID numérico.
6. Productos inactivos (activo = 0): exclusión garantizada del catálogo activo.
7. Catálogo vacío: preservación de columnas y compatibilidad con consumidores.
8. Formato de fecha FECHA_ACT: distinción entre igualdad textual, validez temporal
   y requerimientos reales de consumo en la interfaz.
9. Evaluación sistemática del contrato de redondeo ante múltiples precios.
"""

from datetime import datetime, timezone
import sqlite3
from typing import Any, Dict, List
import pytest

from db.catalog import (
    COLUMNAS_CATALOGO_POS,
    cargar_catalogo_turso,
    cargar_catalogo_turso_filas,
)


def _catalogo_a_registros(catalogo):
    """Convierte el catálogo a filas con Pandas o adaptador liviano."""
    try:
        return catalogo.to_dict(orient="records")
    except TypeError:
        return catalogo.to_dict()

@pytest.fixture
def conn_memoria():
  """Crea una base SQLite en memoria con el esquema canónico real de schema_v1.sql."""
  conn = sqlite3.connect(":memory:")
  with open("db/schema_v1.sql", "r", encoding="utf-8") as f:
    conn.executescript(f.read())
  yield conn
  conn.close()


# -----------------------------------------------------------------------------
# 1. Margen exacto de 50 %
# -----------------------------------------------------------------------------
def test_margen_exacto_cincuenta_porciento(conn_memoria):
  """Valida que costo 400 y precio 600 entreguen fracción 0.5 y presentación 50.00%."""
  conn_memoria.execute("""
        INSERT INTO productos (id, nombre, categoria, proveedor, unidad, costo, precio_dia, precio_noche, activo, fecha_actualizacion)
        VALUES (1001, 'Producto Margen Exacto 50', 'Almacén', 'Distribuidora A', 'Unidad', 400, 600, 600, 1, '2026-10-23 12:00:00');
    """)
  conn_memoria.commit()

  catalogo = cargar_catalogo_turso(conn_memoria)
  assert len(catalogo) == 1
  assert catalogo.empty is False

  fila = [r for _, r in catalogo.iterrows()][0]
  margen_obtenido = fila["MARGEN_%"]

  # 1.1 Valor fraccional esperado
  assert margen_obtenido == 0.5

  # 1.2 Formato en panel Admin (app.py:2253 / 2283) -> float * 100 con 2 decimales
  formato_admin = f"{margen_obtenido * 100:.2f}%"
  assert formato_admin == "50.00%"

  # 1.3 Formato en Preventistas (app.py:2897 / 2930) -> round(1) con 1 decimal
  formato_prev = f"{round(margen_obtenido * 100, 1):.1f} %"
  assert formato_prev == "50.0 %"


# -----------------------------------------------------------------------------
# 2. Margen no periódico / fraccional
# -----------------------------------------------------------------------------
def test_margen_no_periodico_redondeo_y_presentacion(conn_memoria):
  """Valida costo 450 y precio 700: compara fracción matemática vs 0.5556 y paridad visual."""
  conn_memoria.execute("""
        INSERT INTO productos (id, nombre, categoria, proveedor, unidad, costo, precio_dia, precio_noche, activo, fecha_actualizacion)
        VALUES (1002, 'Producto Margen No Periodico', 'Almacén', 'Distribuidora B', 'Unidad', 450, 700, 700, 1, '2026-10-23 12:00:00');
    """)
  conn_memoria.commit()

  catalogo = cargar_catalogo_turso(conn_memoria)
  fila = [r for _, r in catalogo.iterrows()][0]
  margen_turso = fila["MARGEN_%"]

  # Fracción matemática continua teórica: (700 - 450) / 450 = 250 / 450 = 5 / 9
  ref_fraccion = (700 - 450) / 450  # 0.5555555555555556

  # 2.1 Verificación de que Turso aplica ROUND(..., 4) produciendo 0.5556
  assert margen_turso == 0.5556

  # 2.2 Evaluación de igualdad numérica completa:
  # A nivel de flotante IEEE 754 de 64 bits, la igualdad completa falla por el redondeo a 4 dígitos
  assert margen_turso != ref_fraccion

  # La diferencia absoluta es estrictamente menor a 0.0001 (exactamente ~4.44e-5)
  delta = abs(margen_turso - ref_fraccion)
  assert delta < 0.0001
  assert round(delta, 6) == 0.000044

  # 2.3 Evaluación de igualdad con redondeo a 4 decimales:
  assert round(ref_fraccion, 4) == margen_turso

  # 2.4 Paridad visual en panel Admin (formato :.2f}%):
  # Tanto la fracción continua como 0.5556 renderizan IDÉNTICAMENTE a "55.56%"
  texto_ui_turso = f"{margen_turso * 100:.2f}%"
  texto_ui_referencia = f"{ref_fraccion * 100:.2f}%"
  assert texto_ui_turso == "55.56%"
  assert texto_ui_turso == texto_ui_referencia

  # 2.5 Paridad visual en grilla Preventistas (.round(1)):
  # Ambas renderizan IDÉNTICAMENTE a 55.6
  prev_ui_turso = round(margen_turso * 100, 1)
  prev_ui_referencia = round(ref_fraccion * 100, 1)
  assert prev_ui_turso == 55.6
  assert prev_ui_turso == prev_ui_referencia


# -----------------------------------------------------------------------------
# 3. Tratamiento de costo cero
# -----------------------------------------------------------------------------
def test_margen_costo_cero_evita_division_por_cero(conn_memoria):
  """Valida que costo 0 y precio 50 retorne margen 0.0 sin excepción de división por cero."""
  conn_memoria.execute("""
        INSERT INTO productos (id, nombre, categoria, proveedor, unidad, costo, precio_dia, precio_noche, activo, fecha_actualizacion)
        VALUES (1003, 'Bolsa Plastica Biodegradable', 'Varios', 'General', 'Unidad', 0, 50, 50, 1, '2026-10-23 12:00:00');
    """)
  conn_memoria.commit()

  catalogo = cargar_catalogo_turso(conn_memoria)
  fila = [r for _, r in catalogo.iterrows()][0]

  assert fila["COSTO"] == 0
  assert fila["PRECIO_DIA"] == 50
  assert fila["MARGEN_%"] == 0.0
  assert f"{fila['MARGEN_%'] * 100:.2f}%" == "0.00%"


# -----------------------------------------------------------------------------
# 4. Margen negativo (venta a pérdida)
# -----------------------------------------------------------------------------
def test_margen_negativo_venta_a_perdida(conn_memoria):
  """Valida que costo 600 y precio 400 satisfagan las restricciones DDL y generen margen negativo."""
  # En schema_v1.sql: CHECK (costo >= 0) y CHECK (precio_dia >= 0) permiten costo > precio
  conn_memoria.execute("""
        INSERT INTO productos (id, nombre, categoria, proveedor, unidad, costo, precio_dia, precio_noche, activo, fecha_actualizacion)
        VALUES (1004, 'Liquidacion por Vencimiento', 'Lácteos', 'Distribuidora C', 'Unidad', 600, 400, 400, 1, '2026-10-23 12:00:00');
    """)
  conn_memoria.commit()

  catalogo = cargar_catalogo_turso(conn_memoria)
  fila = [r for _, r in catalogo.iterrows()][0]

  # (400 - 600) / 600 = -200 / 600 = -1/3 = -0.333333... -> ROUND a 4 decimales: -0.3333
  assert fila["COSTO"] == 600
  assert fila["PRECIO_DIA"] == 400
  assert fila["MARGEN_%"] == -0.3333

  # Renderizado visual
  assert f"{fila['MARGEN_%'] * 100:.2f}%" == "-33.33%"
  assert round(fila["MARGEN_%"] * 100, 1) == -33.3


# -----------------------------------------------------------------------------
# 5. Nombres duplicados con IDs y proveedores distintos
# -----------------------------------------------------------------------------
def test_nombres_duplicados_conservan_identidad_independiente(conn_memoria):
  """Valida que dos productos con idéntico nombre y distintos proveedores no colisionen ni se fusionen."""
  conn_memoria.execute("""
        INSERT INTO productos (id, nombre, categoria, proveedor, unidad, costo, precio_dia, precio_noche, activo)
        VALUES (5001, 'Galletas Rellenas Chocolate', 'Almacén', 'Mayorista Central', 'Unidad', 400, 600, 600, 1),
               (5002, 'Galletas Rellenas Chocolate', 'Almacén', 'Distribuidora Regional', 'Unidad', 450, 700, 700, 1);
    """)
  conn_memoria.commit()

  catalogo = cargar_catalogo_turso(conn_memoria)
  assert len(catalogo) == 2

  filas = [r for _, r in catalogo.iterrows()]
  f1, f2 = filas[0], filas[1]

  assert f1["NOMBRE"] == f2["NOMBRE"] == "Galletas Rellenas Chocolate"
  assert f1["ID_PRODUCTO"] == 5001
  assert f2["ID_PRODUCTO"] == 5002
  assert f1["PROVEEDOR"] == "Mayorista Central"
  assert f2["PROVEEDOR"] == "Distribuidora Regional"
  assert f1["COSTO"] == 400 and f1["PRECIO_DIA"] == 600
  assert f2["COSTO"] == 450 and f2["PRECIO_DIA"] == 700
  assert f1["MARGEN_%"] == 0.5
  assert f2["MARGEN_%"] == 0.5556


# -----------------------------------------------------------------------------
# 6. Producto inactivo excluido del catálogo activo
# -----------------------------------------------------------------------------
def test_producto_inactivo_excluido_del_catalogo_activo(conn_memoria):
  """Valida que registros con activo = 0 no sean leídos por cargar_catalogo_turso."""
  conn_memoria.execute("""
        INSERT INTO productos (id, nombre, categoria, proveedor, unidad, costo, precio_dia, activo)
        VALUES (6001, 'Producto Vigente', 'Almacén', 'Prov A', 'Unidad', 100, 200, 1),
               (6002, 'Producto Descontinuado', 'Almacén', 'Prov B', 'Unidad', 150, 300, 0);
    """)
  conn_memoria.commit()

  catalogo = cargar_catalogo_turso(conn_memoria)
  assert len(catalogo) == 1

  ids = [r["ID_PRODUCTO"] for _, r in catalogo.iterrows()]
  assert 6001 in ids
  assert 6002 not in ids


# -----------------------------------------------------------------------------
# 7. Catálogo vacío: compatibilidad de contrato y consumidores
# -----------------------------------------------------------------------------
def test_catalogo_vacio_compatibilidad_contrato_y_consumidores(conn_memoria):
  """Valida que un catálogo vacío entregue las 10 columnas y soporte iterrows y to_dict sin excepciones."""
  catalogo = cargar_catalogo_turso(conn_memoria)

  # 7.1 Métricas de estado vacío
  assert len(catalogo) == 0
  assert catalogo.empty is True

  # 7.2 Conservación estricta de las 10 columnas estándar de Genaro POS
  assert list(catalogo.columns) == COLUMNAS_CATALOGO_POS

  # 7.3 Seguridad ante bucles de consumo de la interfaz
  recorridos = 0
  for _, fila in catalogo.iterrows():
    recorridos += 1
  assert recorridos == 0

  # 7.4 Seguridad ante conversión a diccionario
  assert _catalogo_a_registros(catalogo) == []

  # 7.5 Simulación de patrón de app.py:2240 (filtro por nombre en DataFrame vacío)
  coincidencias = [r for _, r in catalogo.iterrows() if r["NOMBRE"] == "Inexistente"]
  assert len(coincidencias) == 0


# -----------------------------------------------------------------------------
# 8. Formato de fecha FECHA_ACT: distinción textual vs temporal
# -----------------------------------------------------------------------------
def test_formato_fecha_actualizacion_distincion_textual_vs_temporal(conn_memoria):
  """Compara el formato ISO UTC de Turso con el formato histórico DD/MM/YYYY de Sheets."""
  timestamp_iso = "2026-10-23 12:45:00"
  conn_memoria.execute(
      """
        INSERT INTO productos (id, nombre, categoria, proveedor, unidad, costo, precio_dia, activo, fecha_actualizacion)
        VALUES (8001, 'Item Fecha Audit', 'Test', 'Prov', 'Unidad', 500, 800, 1, ?);
    """,
      (timestamp_iso,),
  )
  conn_memoria.commit()

  catalogo = cargar_catalogo_turso(conn_memoria)
  fila = [r for _, r in catalogo.iterrows()][0]
  fecha_turso = fila["FECHA_ACT"]

  # 8.1 Valor entregado por Turso
  assert fecha_turso == timestamp_iso

  # 8.2 Formato histórico generado por app.py:fecha_act_texto() en Google Sheets
  fecha_historica_sheets = "23/10/2026"

  # 8.3 Igualdad textual: FALSE (diferencia de sintaxis esperada)
  assert fecha_turso != fecha_historica_sheets

  # 8.4 Validez temporal: Ambas representan fechas válidas
  dt_turso = datetime.strptime(fecha_turso, "%Y-%m-%d %H:%M:%S")
  dt_sheets = datetime.strptime(fecha_historica_sheets, "%d/%m/%Y")

  assert dt_turso.year == dt_sheets.year == 2026
  assert dt_turso.month == dt_sheets.month == 10
  assert dt_turso.day == dt_sheets.day == 23

  # 8.5 Precisión: Turso conserva hora, minuto y segundo en UTC; Sheets solo fecha de calendario
  assert (dt_turso.hour, dt_turso.minute, dt_turso.second) == (12, 45, 0)
  assert (dt_sheets.hour, dt_sheets.minute, dt_sheets.second) == (0, 0, 0)

  # 8.6 Requerimiento real de la UI:
  # En app.py, FECHA_ACT solo se asigna al guardar productos (líneas 2301, 2401, 2952, 3004).
  # No interviene en cálculos, ordenamientos ni filtros de ventas.


# -----------------------------------------------------------------------------
# 9. Evaluación sistemática del contrato de redondeo ante múltiples precios
# -----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "costo,precio,fraccion_esperada,sql_redondeado,ui_admin_esperado,ui_prev_esperado",
    [
        (300, 500, 200 / 300, 0.6667, "66.67%", 66.7),
        (700, 1000, 300 / 700, 0.4286, "42.86%", 42.9),
        (800, 1200, 400 / 800, 0.5000, "50.00%", 50.0),
        (1000, 1333, 333 / 1000, 0.3330, "33.30%", 33.3),
        (1200, 1500, 300 / 1200, 0.2500, "25.00%", 25.0),
    ],
)
def test_evaluacion_sistematica_redondeo_y_presentacion(
    conn_memoria,
    costo,
    precio,
    fraccion_esperada,
    sql_redondeado,
    ui_admin_esperado,
    ui_prev_esperado,
):
  """Valida que para diversas combinaciones de costo y precio, el redondeo SQL a 4 decimales

  produzca paridad visual exacta con la fracción continua en la UI.
  """
  conn_memoria.execute(
      """
        INSERT INTO productos (nombre, categoria, proveedor, unidad, costo, precio_dia, activo)
        VALUES (?, 'Param', 'Prov', 'Unidad', ?, ?, 1);
    """,
      (f"Item_{costo}_{precio}", costo, precio),
  )
  conn_memoria.commit()

  catalogo = cargar_catalogo_turso(conn_memoria)
  fila = [r for _, r in catalogo.iterrows()][-1]
  margen_turso = fila["MARGEN_%"]

  # Comparación con el valor generado por la función SQL
  assert margen_turso == sql_redondeado
  assert round(fraccion_esperada, 4) == sql_redondeado

  # Formato en Admin (:.2f}%)
  assert f"{margen_turso * 100:.2f}%" == ui_admin_esperado
  assert f"{fraccion_esperada * 100:.2f}%" == ui_admin_esperado

  # Formato en Preventistas (round(1))
  assert round(margen_turso * 100, 1) == ui_prev_esperado
  assert round(fraccion_esperada * 100, 1) == ui_prev_esperado
