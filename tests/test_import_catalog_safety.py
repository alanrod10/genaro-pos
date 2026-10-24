"""Pruebas unitarias locales para el importador del catálogo real (Fase 14.2-G).

Verifica sin red y mediante fixtures sintéticos:
1. Catálogo válido: validación exitosa, checksums deterministas y preview de operaciones.
2. Contrato flexible y determinista:
   - Archivo con solo columnas obligatorias (ID_PRODUCTO, NOMBRE, COSTO, PRECIO_DIA).
   - Asignación correcta de valores predeterminados para columnas opcionales.
3. Detección y rechazo de IDs duplicados en el mismo archivo CSV.
4. Admisión legítima de nombres de producto duplicados con IDs independientes.
5. Validación monetaria regional estricta (1234, 1.234, $ 1.234, 1.234,00).
6. Rechazo de centavos reales (1.234,50), importes negativos y valores no numéricos.
7. Detección de campos obligatorios ausentes (NOMBRE, ID_PRODUCTO, PRECIO_DIA).
8. Preservación del estado de productos inactivos (ACTIVO=0).
9. Tolerancia a filas vacías sin falsos positivos de error.
10. Normalización determinista de fechas (regional Sheets DD/MM/YYYY, ISO, ISO con Z / offset).
11. Rechazo de fechas imposibles (31/02/2026) o cadenas ilegibles.
12. Control y auditoría de discrepancia de margen (> 0.05) sin sobreescribir la fórmula SQL.
13. Rechazo de archivos con encabezados obligatorios faltantes o columnas desconocidas.
14. Protección contra colisiones con el rango sintético reservado (9001..9005).
15. Detección de colisiones contra datos preexistentes en la base destino.
16. Garantía de aislamiento: dry_run=False sin conexión inyectada no conecta a la red.
17. Sensibilidad y determinismo de los checksums SHA-256 ante alteraciones mínimas.
"""

import os
import re
import inspect
import sqlite3
import tempfile
import pytest

from db.catalog import cargar_catalogo_turso
from db.import_catalog import (
    COLUMNAS_OBLIGATORIAS,
    COLUMNAS_OPCIONALES,
    COLUMNAS_PERMITIDAS,
    ESTADO_COLISION_SINTETICOS,
    ESTADO_CONFLICTO_DATOS,
    ESTADO_MIGRACION_EXITO,
    ESTADO_VALIDACION_FALLIDA,
    ESTADO_VALIDACION_OK,
    IDS_SINTETICOS_RESERVADOS,
    UMBRAL_DISCREPANCIA_MARGEN,
    ImportadorCatalogo,
    calcular_sha256_archivo,
    calcular_sha256_registros,
    normalizar_fecha_iso,
    parse_booleano_activo,
)


def _catalogo_a_registros(catalogo):
    """Convierte el catálogo a filas con Pandas o adaptador liviano."""
    try:
        return catalogo.to_dict(orient="records")
    except TypeError:
        return catalogo.to_dict()

@pytest.fixture
def conn_productos():
  """Crea una base SQLite en memoria con el esquema canónico de schema_v1.sql."""
  conn = sqlite3.connect(":memory:")
  with open("db/schema_v1.sql", "r", encoding="utf-8") as f:
    conn.executescript(f.read())
  yield conn
  conn.close()


def crear_csv_temporal(contenido: str) -> str:
  """Crea un archivo CSV temporal y retorna su ruta."""
  fd, path = tempfile.mkstemp(suffix=".csv", prefix="genaro_cat_")
  with os.fdopen(fd, "w", encoding="utf-8") as f:
    f.write(contenido)
  return path



# -----------------------------------------------------------------------------
# Contrato de Fase 14.2-K: defaults explícitos y PRECIO_NOCHE obsoleto
# -----------------------------------------------------------------------------
def test_campos_opcionales_vacios_y_precio_noche_ignorado_csv_inmutable():
  """No interpreta PRECIO_NOCHE y normaliza campos vacíos sin alterar el CSV."""
  csv_content = (
      "ID_PRODUCTO,NOMBRE,CATEGORIA,PROVEEDOR,UNIDAD,COSTO,PRECIO_DIA,"
      "PRECIO_NOCHE,ACTIVO,FECHA_ACT\n"
      "9511,Proveedor vacío,Bebidas, ,Unidad,100,200,NO_LEER,1,23/10/2026\n"
      "9512,Tres opcionales vacíos,   ,   ,   ,150,300,-99,1,2026-10-23\n"
      "9513,Tres opcionales nulos,NULL,None,null,175,350,NO_VALIDAR,1,2026-10-23\n"
  )
  path = crear_csv_temporal(csv_content)
  try:
    with open(path, "rb") as f:
      source_before = f.read()
    importador = ImportadorCatalogo()
    exito, resumen, registros = importador.validar_archivo_csv(path)
    assert exito is True
    assert resumen["filas_validas"] == 3
    assert [r["proveedor"] for r in registros] == ["General", "General", "General"]
    assert [r["categoria"] for r in registros] == ["Bebidas", "Otro", "Otro"]
    assert [r["unidad"] for r in registros] == ["Unidad", "Unidad", "Unidad"]
    assert [r["activo"] for r in registros] == [1, 1, 1]
    assert all("precio_noche" not in r for r in registros)
    assert all("margen_ratio" not in r and "MARGEN_%" not in r for r in registros)
    assert registros[0]["fecha_actualizacion"] == "2026-10-23 00:00:00"
    assert registros[1]["fecha_actualizacion"] == "2026-10-23 00:00:00"
    preview = importador.previsualizar_operaciones(registros)
    assert all("precio_noche" not in op and "PRECIO_NOCHE" not in op for op in preview)
    with open(path, "rb") as f:
      assert f.read() == source_before
  finally:
    if os.path.exists(path):
      os.remove(path)


def test_insert_omite_precio_noche_y_margen_generado_sin_ejecutar_sql():
  """La instrucción de inserción omite PRECIO_NOCHE y la columna generada de margen."""
  source = inspect.getsource(ImportadorCatalogo.migrar_catalogo)
  match = re.search(r'sql_insert\s*=\s*"""(.*?)"""', source, re.DOTALL)
  assert match is not None
  sql = match.group(1).lower()
  columnas = re.search(r"insert\s+into\s+productos\s*\((.*?)\)\s*values", sql, re.DOTALL)
  assert columnas is not None
  columnas_insertadas = {c.strip() for c in columnas.group(1).split(",")}
  assert "precio_noche" not in columnas_insertadas
  assert "margen_ratio" not in columnas_insertadas
  assert "margen_%" not in columnas_insertadas


# -----------------------------------------------------------------------------
# 1. Catálogo válido completo: validación, checksums y migración en base local
# -----------------------------------------------------------------------------
def test_catalogo_valido_dry_run_y_migracion_local(conn_productos):
  """Valida un archivo CSV completamente válido en modo dry_run y luego con escritura local."""
  csv_content = """ID_PRODUCTO,NOMBRE,CATEGORIA,PROVEEDOR,UNIDAD,COSTO,MARGEN_%,PRECIO_DIA,PRECIO_NOCHE,FECHA_ACT
101,Gaseosa Cola 1.5L,Bebidas,Distribuidora Norte,Unidad,800,0.5,1200,1200,2026-10-23 12:00:00
102,Yerba Mate 500g,Almacén,Distribuidora Sur,Unidad,1000,0.5,1500,1500,23/10/2026
"""
  path = crear_csv_temporal(csv_content)
  try:
    importador = ImportadorCatalogo()

    # 1. Modo dry_run (validación pura)
    exito, res = importador.migrar_catalogo(path, conn=None, dry_run=True)
    assert exito is True
    assert res["estado"] == ESTADO_VALIDACION_OK
    assert res["total_filas_leidas"] == 2
    assert res["filas_validas"] == 2
    assert res["filas_invalidas"] == 0
    assert len(res["archivo_sha256"]) == 64
    assert len(res["registros_sha256"]) == 64
    assert len(res["preview_operaciones"]) == 2
    assert res["filas_insertadas"] == 0

    # 2. Modo escritura en SQLite local
    exito_write, res_write = importador.migrar_catalogo(
        path, conn=conn_productos, dry_run=False
    )
    assert exito_write is True
    assert res_write["estado"] == ESTADO_MIGRACION_EXITO
    assert res_write["filas_insertadas"] == 2

    # Verificar lectura a través del adaptador oficial de Genaro POS
    catalogo = cargar_catalogo_turso(conn_productos)
    assert len(catalogo) == 2
    ids = [r["ID_PRODUCTO"] for _, r in catalogo.iterrows()]
    assert ids == [101, 102]
    filas = [r for _, r in catalogo.iterrows()]
    assert filas[0]["MARGEN_%"] == 0.5  # (1200-800)/800 = 0.5
    assert filas[1]["MARGEN_%"] == 0.5  # (1500-1000)/1000 = 0.5
  finally:
    if os.path.exists(path):
      os.remove(path)


# -----------------------------------------------------------------------------
# 2. Contrato formal: solo columnas obligatorias y defaults para opcionales
# -----------------------------------------------------------------------------
def test_contrato_csv_minimo_solo_columnas_obligatorias(conn_productos):
  """Valida que un CSV que contiene únicamente las 4 columnas obligatorias sea válido y aplique defaults."""
  csv_content = """ID_PRODUCTO,NOMBRE,COSTO,PRECIO_DIA
201,Producto Basico 1,500,800
202,Producto Basico 2,1000,1500
"""
  path = crear_csv_temporal(csv_content)
  try:
    importador = ImportadorCatalogo()
    exito, res, registros = importador.validar_archivo_csv(path)
    assert exito is True
    assert res["filas_validas"] == 2

    # Verificar aplicación de defaults en registros normalizados
    r1, r2 = registros[0], registros[1]
    assert r1["categoria"] == "Otro"
    assert r1["proveedor"] == "General"
    assert r1["unidad"] == "Unidad"
    assert "precio_noche" not in r1  # Campo obsoleto ignorado completamente
    assert r1["activo"] == 1
    assert r1["fecha_actualizacion"] is not None

    # Inserción en base local
    exito_mig, _ = importador.migrar_catalogo(
        path, conn=conn_productos, dry_run=False
    )
    assert exito_mig is True
    catalogo = cargar_catalogo_turso(conn_productos)
    assert len(catalogo) == 2
    f1 = _catalogo_a_registros(catalogo)[0]
    assert f1["CATEGORIA"] == "Otro"
    assert f1["PROVEEDOR"] == "General"
    assert f1["PRECIO_NOCHE"] == 0  # DEFAULT del esquema; el importador no inserta PRECIO_NOCHE
  finally:
    if os.path.exists(path):
      os.remove(path)


# -----------------------------------------------------------------------------
# 3. Detección y rechazo de IDs duplicados en el CSV
# -----------------------------------------------------------------------------
def test_rechazo_ids_duplicados_en_csv():
  """Valida que registros con el mismo ID_PRODUCTO en el archivo sean detectados y rechazados."""
  csv_content = """ID_PRODUCTO,NOMBRE,COSTO,PRECIO_DIA
201,Alfajor Simple,300,450
201,Alfajor Triple,500,700
"""
  path = crear_csv_temporal(csv_content)
  try:
    importador = ImportadorCatalogo()
    exito, res, _ = importador.validar_archivo_csv(path)
    assert exito is False
    assert res["estado"] == ESTADO_VALIDACION_FALLIDA
    assert 201 in res["ids_duplicados_en_csv"]
    assert any("ID duplicado" in err["motivo"] for err in res["errores"])
  finally:
    if os.path.exists(path):
      os.remove(path)


# -----------------------------------------------------------------------------
# 4. Admisión de nombres duplicados con IDs independientes
# -----------------------------------------------------------------------------
def test_admision_nombres_duplicados_con_distintos_ids(conn_productos):
  """Valida que productos con el mismo nombre y proveedores distintos no sean tratados como colisión."""
  csv_content = """ID_PRODUCTO,NOMBRE,PROVEEDOR,COSTO,PRECIO_DIA
301,Galletitas Vainilla 150g,Distribuidora Alfa,400,600
302,Galletitas Vainilla 150g,Mayorista Beta,450,700
"""
  path = crear_csv_temporal(csv_content)
  try:
    importador = ImportadorCatalogo()
    exito, res, _ = importador.validar_archivo_csv(path)
    assert exito is True
    assert res["filas_validas"] == 2
    assert "Galletitas Vainilla 150g" in res["nombres_duplicados"]

    exito_mig, _ = importador.migrar_catalogo(
        path, conn=conn_productos, dry_run=False
    )
    assert exito_mig is True
    catalogo = cargar_catalogo_turso(conn_productos)
    assert len(catalogo) == 2
    assert [r["PROVEEDOR"] for _, r in catalogo.iterrows()] == [
        "Distribuidora Alfa",
        "Mayorista Beta",
    ]
  finally:
    if os.path.exists(path):
      os.remove(path)


# -----------------------------------------------------------------------------
# 5. Validación monetaria regional estricta y rechazo de centavos reales
# -----------------------------------------------------------------------------
def test_validacion_monetaria_regional_formatos():
  """Valida formatos regionales válidos (1234, 1.234, $ 1.234, 1.234,00) y rechazo de centavos reales."""
  # Archivo con formatos válidos permitidos
  csv_valid = '''ID_PRODUCTO,NOMBRE,COSTO,PRECIO_DIA
401,Item 1,1234,1234
402,Item 2,1.234,"$ 1.234"
403,Item 3,"1.234,00","1234,00"
'''
  path_ok = crear_csv_temporal(csv_valid)

  # Archivo con centavos reales o texto no numérico
  csv_invalid = '''ID_PRODUCTO,NOMBRE,COSTO,PRECIO_DIA
411,Item Invalido 1,GRATIS,500
412,Item Invalido 2,300,"1.234,50"
413,Item Invalido 3,300,1234.50
414,Item Invalido 4,-100,500
'''
  path_err = crear_csv_temporal(csv_invalid)

  try:
    importador = ImportadorCatalogo()

    # Probar formatos válidos
    exito_ok, res_ok, registros_ok = importador.validar_archivo_csv(path_ok)
    assert exito_ok is True
    assert res_ok["filas_validas"] == 3
    for r in registros_ok:
      assert r["costo"] == 1234
      assert r["precio_dia"] == 1234

    # Probar formatos inválidos
    exito_err, res_err, _ = importador.validar_archivo_csv(path_err)
    assert exito_err is False
    assert res_err["filas_invalidas"] == 4
    campos_err = {e["campo"] for e in res_err["errores"]}
    assert "COSTO" in campos_err
    assert "PRECIO_DIA" in campos_err
  finally:
    for p in (path_ok, path_err):
      if os.path.exists(p):
        os.remove(p)


# -----------------------------------------------------------------------------
# 6. Detección de campos obligatorios ausentes o IDs no positivos
# -----------------------------------------------------------------------------
def test_rechazo_campos_obligatorios_ausentes_o_ids_invalidos():
  """Valida que ausencia de NOMBRE, ID_PRODUCTO o PRECIO_DIA o IDs <= 0 causen error de fila."""
  csv_content = """ID_PRODUCTO,NOMBRE,COSTO,PRECIO_DIA
,Sin ID,100,200
0,ID Cero,100,200
-5,ID Negativo,100,200
502,,100,200
503,Sin Precio,100,
504.5,ID Decimal,100,200
"""
  path = crear_csv_temporal(csv_content)
  try:
    importador = ImportadorCatalogo()
    exito, res, _ = importador.validar_archivo_csv(path)
    assert exito is False
    assert res["filas_invalidas"] == 6
    assert any(
        e["campo"] == "ID_PRODUCTO" and "entero válido" in e["motivo"]
        for e in res["errores"]
    )
  finally:
    if os.path.exists(path):
      os.remove(path)


# -----------------------------------------------------------------------------
# 7. Preservación del estado de productos inactivos
# -----------------------------------------------------------------------------
def test_importacion_productos_inactivos(conn_productos):
  """Valida que un producto con ACTIVO=0 se inserte como inactivo y no aparezca en el catálogo activo."""
  csv_content = """ID_PRODUCTO,NOMBRE,COSTO,PRECIO_DIA,ACTIVO
601,Producto Vigente,100,200,1
602,Producto Baja,150,300,0
603,Producto Texto Descontinuado,100,200,descontinuado
"""
  path = crear_csv_temporal(csv_content)
  try:
    importador = ImportadorCatalogo()
    exito, res = importador.migrar_catalogo(
        path, conn=conn_productos, dry_run=False
    )
    assert exito is True

    cursor = conn_productos.execute(
        "SELECT id, activo FROM productos ORDER BY id ASC;"
    )
    filas = cursor.fetchall()
    assert filas == [(601, 1), (602, 0), (603, 0)]

    catalogo = cargar_catalogo_turso(conn_productos)
    assert len(catalogo) == 1
    assert _catalogo_a_registros(catalogo)[0]["ID_PRODUCTO"] == 601
  finally:
    if os.path.exists(path):
      os.remove(path)


# -----------------------------------------------------------------------------
# 8. Tolerancia a filas vacías
# -----------------------------------------------------------------------------
def test_tolerancia_a_filas_vacias():
  """Valida que líneas en blanco o con solo comas intermedias sean ignoradas sin generar error."""
  csv_content = """ID_PRODUCTO,NOMBRE,COSTO,PRECIO_DIA
701,Producto A,100,200
,,,
   
702,Producto B,100,200
"""
  path = crear_csv_temporal(csv_content)
  try:
    importador = ImportadorCatalogo()
    exito, res, _ = importador.validar_archivo_csv(path)
    assert exito is True
    assert res["filas_validas"] == 2
    assert res["filas_vacias"] == 2
    assert res["filas_invalidas"] == 0
  finally:
    if os.path.exists(path):
      os.remove(path)


# -----------------------------------------------------------------------------
# 9. Normalización determinista de fechas y rechazo de fechas imposibles
# -----------------------------------------------------------------------------
def test_normalizacion_determinista_de_fechas():
  """Valida fechas regionales, ISO, ISO con huso horario y rechazo de fechas imposibles."""
  # 1. Regional Sheets DD/MM/YYYY -> fija 00:00:00 sin desplazar día
  f1, err1 = normalizar_fecha_iso("23/10/2026")
  assert err1 is None
  assert f1 == "2026-10-23 00:00:00"

  # 2. Regional Sheets con hora DD/MM/YYYY HH:MM:SS
  f2, err2 = normalizar_fecha_iso("23/10/2026 14:30:00")
  assert err2 is None
  assert f2 == "2026-10-23 14:30:00"

  # 3. ISO estándar sin zona YYYY-MM-DD HH:MM:SS
  f3, err3 = normalizar_fecha_iso("2026-10-23 14:30:00")
  assert err3 is None
  assert f3 == "2026-10-23 14:30:00"

  # 4. ISO con sufijo Z (UTC explícito)
  f4, err4 = normalizar_fecha_iso("2026-10-23T14:30:00Z")
  assert err4 is None
  assert f4 == "2026-10-23 14:30:00"

  # 5. ISO con offset explícito (-03:00 Argentina -> UTC +3 horas)
  f5, err5 = normalizar_fecha_iso("2026-10-23T11:30:00-03:00")
  assert err5 is None
  assert f5 == "2026-10-23 14:30:00"

  # 6. Fechas imposibles o formato no reconocido
  f6, err6 = normalizar_fecha_iso("31/02/2026")
  assert f6 is None
  assert "fecha de calendario inválida" in err6

  f7, err7 = normalizar_fecha_iso("fecha_corrupta")
  assert f7 is None
  assert "Formato de fecha no reconocido" in err7

  # 7. Una fecha ambigua se interpreta siempre como DD/MM/YYYY, nunca MM/DD/YYYY.
  f8, err8 = normalizar_fecha_iso("03/04/2026")
  assert err8 is None
  assert f8 == "2026-04-03 00:00:00"
  f9, err9 = normalizar_fecha_iso("10/23/2026")
  assert f9 is None
  assert err9 is not None


# -----------------------------------------------------------------------------
# 10. Control de discrepancia de margen y no sobreescritura SQL
# -----------------------------------------------------------------------------
def test_control_discrepancia_de_margen(conn_productos):
  """Valida que diferencias en MARGEN_% > 0.05 emitan advertencias informativas sin alterar el cálculo SQL."""
  # Producto 801: Costo 400, Precio 600 -> Margen SQL: 0.5 (50%)
  # Archivo reporta margen de 0.8 (discrepancia 0.30 > 0.05)
  csv_content = """ID_PRODUCTO,NOMBRE,COSTO,PRECIO_DIA,MARGEN_%
801,Alfajor Discrepante,400,600,0.80
802,Alfajor Escala Porcentual,400,600,50%
"""
  path = crear_csv_temporal(csv_content)
  try:
    importador = ImportadorCatalogo()
    exito, res, registros = importador.validar_archivo_csv(path)
    assert exito is True
    assert len(res["advertencias"]) >= 2
    assert any("difiere por más de 0.05" in adv for adv in res["advertencias"])
    assert any("escala porcentual" in adv for adv in res["advertencias"])

    # Ejecutar migración local para constatar que prevalece SQL
    exito_mig, _ = importador.migrar_catalogo(
        path, conn=conn_productos, dry_run=False
    )
    assert exito_mig is True
    catalogo = cargar_catalogo_turso(conn_productos)
    filas = _catalogo_a_registros(catalogo)
    assert filas[0]["MARGEN_%"] == 0.5  # Cálculo exacto del motor SQL
    assert filas[1]["MARGEN_%"] == 0.5
  finally:
    if os.path.exists(path):
      os.remove(path)


# -----------------------------------------------------------------------------
# 11. Rechazo de archivos con encabezados incorrectos o desconocidos
# -----------------------------------------------------------------------------
def test_rechazo_encabezados_incorrectos_o_desconocidos():
  """Valida que columnas faltantes o no autorizadas provoquen el rechazo del archivo."""
  # 1. Falta columna obligatoria PRECIO_DIA
  csv_falta = (
      "ID_PRODUCTO,NOMBRE,COSTO\n1,Producto Incompleto,100\n"
  )
  path1 = crear_csv_temporal(csv_falta)

  # 2. Columna desconocida no permitida
  csv_extra = """ID_PRODUCTO,NOMBRE,COSTO,PRECIO_DIA,COLUMNA_INVENTADA
1,Producto Valido,100,200,extra
"""
  path2 = crear_csv_temporal(csv_extra)

  try:
    importador = ImportadorCatalogo()
    exito1, res1, _ = importador.validar_archivo_csv(path1)
    assert exito1 is False
    assert any("Faltan columnas obligatorias" in e["motivo"] for e in res1["errores"])

    exito2, res2, _ = importador.validar_archivo_csv(path2)
    assert exito2 is False
    assert any("columnas desconocidas no permitidas" in e["motivo"] for e in res2["errores"])
  finally:
    for p in (path1, path2):
      if os.path.exists(p):
        os.remove(p)


# -----------------------------------------------------------------------------
# 12. Protección contra colisiones con el rango sintético (9001..9005)
# -----------------------------------------------------------------------------
def test_deteccion_colision_con_rango_sintetico():
  """Valida que si el CSV contiene un ID reservado del laboratorio DEV, se bloquee de inmediato."""
  csv_content = """ID_PRODUCTO,NOMBRE,COSTO,PRECIO_DIA
9002,Conflicto con Sintetico,400,600
"""
  path = crear_csv_temporal(csv_content)
  try:
    importador = ImportadorCatalogo()
    exito, res, _ = importador.validar_archivo_csv(path)
    assert exito is False
    assert res["estado"] == ESTADO_COLISION_SINTETICOS
    assert 9002 in res["colisiones_con_sinteticos"]
  finally:
    if os.path.exists(path):
      os.remove(path)


# -----------------------------------------------------------------------------
# 13. Detección de colisiones contra datos existentes en la base destino
# -----------------------------------------------------------------------------
def test_conflicto_con_datos_preexistentes_en_destino(conn_productos):
  """Valida que si la tabla destino ya contiene registros que coinciden en ID, la migración se aborte."""
  conn_productos.execute("""
        INSERT INTO productos (id, nombre, categoria, proveedor, unidad, costo, precio_dia, precio_noche, activo)
        VALUES (801, 'Producto Preexistente', 'Almacén', 'Prov Antiguo', 'Unidad', 500, 800, 800, 1);
    """)
  conn_productos.commit()

  csv_content = """ID_PRODUCTO,NOMBRE,COSTO,PRECIO_DIA
801,Intento Sobrescritura,600,900
"""
  path = crear_csv_temporal(csv_content)
  try:
    importador = ImportadorCatalogo()
    exito, res = importador.migrar_catalogo(
        path, conn=conn_productos, dry_run=False
    )
    assert exito is False
    assert res["estado"] == ESTADO_CONFLICTO_DATOS
    assert "colisionan con el archivo" in res["errores"][0]["motivo"]

    # Garantizar que el registro preexistente quedó intacto
    cursor = conn_productos.execute(
        "SELECT nombre, costo FROM productos WHERE id = 801;"
    )
    fila = cursor.fetchone()
    assert fila == ("Producto Preexistente", 500)
  finally:
    if os.path.exists(path):
      os.remove(path)


# -----------------------------------------------------------------------------
# 14. Garantía de aislamiento: dry_run=False sin conexión inyectada
# -----------------------------------------------------------------------------
def test_garantia_aislamiento_sin_red():
  """Valida que invocar migrar_catalogo con dry_run=False y conn=None aborte ruidosamente sin red."""
  csv_content = """ID_PRODUCTO,NOMBRE,COSTO,PRECIO_DIA
850,Producto Aislado,100,200
"""
  path = crear_csv_temporal(csv_content)
  try:
    importador = ImportadorCatalogo()
    exito, res = importador.migrar_catalogo(path, conn=None, dry_run=False)
    assert exito is False
    assert res["estado"] == ESTADO_VALIDACION_FALLIDA
    assert any("Escrituras remotas no permitidas" in e["motivo"] for e in res["errores"])
  finally:
    if os.path.exists(path):
      os.remove(path)


# -----------------------------------------------------------------------------
# 15. Sensibilidad y determinismo de checksums SHA-256
# -----------------------------------------------------------------------------
def test_checksums_sensibilidad_y_determinismo():
  """Valida que alteraciones mínimas cambien el hash de archivo y que datos idénticos generen el mismo hash de registros."""
  csv_orig = "ID_PRODUCTO,NOMBRE,COSTO,PRECIO_DIA\n1,Prod A,100,200\n"
  csv_alterado = "ID_PRODUCTO,NOMBRE,COSTO,PRECIO_DIA\n1,Prod B,100,200\n"

  path1 = crear_csv_temporal(csv_orig)
  path2 = crear_csv_temporal(csv_alterado)

  try:
    importador = ImportadorCatalogo()
    ok1, res1, reg1 = importador.validar_archivo_csv(path1)
    ok2, res2, reg2 = importador.validar_archivo_csv(path2)

    assert ok1 is True and ok2 is True
    # Hashes de archivo distintos
    assert res1["archivo_sha256"] != res2["archivo_sha256"]
    # Hashes de registros normalizados distintos
    assert res1["registros_sha256"] != res2["registros_sha256"]

    # Mismo contenido produce mismo hash
    hash_reg_repetido = calcular_sha256_registros(reg1)
    assert hash_reg_repetido == res1["registros_sha256"]
  finally:
    for p in (path1, path2):
      if os.path.exists(p):
        os.remove(p)

def test_auditoria_margen_ratio_decimal_sin_inferencia_implicita():
  """Ratios >1 se comparan directamente; solo se escala si hay un % explícito."""
  csv_content = """ID_PRODUCTO,NOMBRE,COSTO,PRECIO_DIA,MARGEN_%
9611,Ratio fraccional,400,600,0.5
9612,Ratio mayor uno,1000,2029,1.0290
9613,Discrepancia real,400,600,0.8
9614,Porcentaje explicito,400,600,50%
"""
  path = crear_csv_temporal(csv_content)
  try:
    importador = ImportadorCatalogo()
    exito, resumen, registros = importador.validar_archivo_csv(path)

    assert exito is True
    assert resumen["filas_validas"] == 4
    assert len(registros) == 4

    advertencias = resumen["advertencias"]
    diferencias_reales = [
        adv for adv in advertencias if "difiere por más de" in adv
    ]
    assert len(diferencias_reales) == 1
    assert "Discrepancia real" in diferencias_reales[0]
    assert not any("Ratio fraccional" in adv for adv in advertencias)
    assert not any("Ratio mayor uno" in adv for adv in advertencias)

    escalas_explicitas = [
        adv for adv in advertencias if "sufijo explícito" in adv
    ]
    assert len(escalas_explicitas) == 1
    assert "Porcentaje explicito" in escalas_explicitas[0]
    assert not any(
        "difiere por más de" in adv and "Porcentaje explicito" in adv
        for adv in advertencias
    )
  finally:
    if os.path.exists(path):
      os.remove(path)
