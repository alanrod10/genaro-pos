"""Adaptador de persistencia para el catálogo de productos (Fase 14.2-A).

Proporciona una interfaz acotada y segura para la lectura del catálogo de productos
desde Turso / SQLite, compatible con las columnas, tipos e índices que requiere Genaro POS.
"""

from datetime import datetime, timezone
import os
from typing import Any, Dict, Iterator, List, Optional, Tuple

try:
  import pandas as pd
except ImportError:
  pd = None

# Columnas estándar del catálogo de productos esperadas por los consumidores de Genaro POS
COLUMNAS_CATALOGO_POS = [
    "ID_PRODUCTO",
    "NOMBRE",
    "CATEGORIA",
    "PROVEEDOR",
    "UNIDAD",
    "COSTO",
    "MARGEN_%",
    "PRECIO_DIA",
    "PRECIO_NOCHE",
    "FECHA_ACT",
]

# Variable de configuración que gobierna la fuente de datos del catálogo
ENV_CATALOGO_BACKEND = "CATALOGO_BACKEND"
BACKEND_SHEETS = "SHEETS"
BACKEND_TURSO = "TURSO"


class ErrorCatalogo(Exception):
  """Excepción base para operaciones sobre el catálogo de productos."""

  pass


class ErrorAccesoCatalogoTurso(ErrorCatalogo):
  """Error al conectar o consultar el catálogo de productos en Turso."""

  pass


class CatalogoFrameLiviano:
  """Representación tabular liviana compatible con columnas e iterrows para entornos sin pandas."""

  def __init__(
      self, filas: List[Dict[str, Any]], columnas: Optional[List[str]] = None
  ):
    self.columns = list(columnas or COLUMNAS_CATALOGO_POS)
    self._filas = list(filas)
    self.empty = len(self._filas) == 0

  def __len__(self) -> int:
    return len(self._filas)

  def __getitem__(self, col: str) -> List[Any]:
    return [f.get(col) for f in self._filas]

  def iterrows(self) -> Iterator[Tuple[int, Dict[str, Any]]]:
    for idx, fila in enumerate(self._filas):
      yield idx, fila

  def to_dict(self) -> List[Dict[str, Any]]:
    return [dict(f) for f in self._filas]


def obtener_backend_catalogo() -> str:
  """Retorna el backend configurado para el catálogo.

  Valor predeterminado seguro: 'SHEETS'.
  Para activar Turso en desarrollo: export CATALOGO_BACKEND=TURSO
  """
  backend = os.environ.get(ENV_CATALOGO_BACKEND, BACKEND_SHEETS).strip().upper()
  if backend in (BACKEND_SHEETS, BACKEND_TURSO):
    return backend
  return BACKEND_SHEETS


def _obtener_conexion_turso() -> Any:
  """Crea una conexión con Turso DEV validando la presencia de credenciales."""
  url = os.environ.get("TURSO_DEV_DATABASE_URL")
  token = os.environ.get("TURSO_DEV_AUTH_TOKEN")

  if not url:
    raise ErrorAccesoCatalogoTurso(
        "Variable de entorno 'TURSO_DEV_DATABASE_URL' no configurada."
    )
  if not token:
    raise ErrorAccesoCatalogoTurso(
        "Variable de entorno 'TURSO_DEV_AUTH_TOKEN' no configurada."
    )

  try:
    import turso_serverless

    return turso_serverless.connect(url, auth_token=token)
  except Exception as e:
    raise ErrorAccesoCatalogoTurso(
        f"Fallo al conectar con el servidor Turso: {e}"
    ) from e


def cargar_catalogo_turso_filas(
    conn: Optional[Any] = None,
) -> List[Dict[str, Any]]:
  """Consulta la tabla 'productos' en Turso y retorna las filas normalizadas como diccionarios.

  Reglas de negocio:
  - Solo incluye productos con activo == 1.
  - Orden ascendente por id.
  - No deduplica por nombre: cada producto conserva su id numérico único.
  """
  cerrar_al_terminar = False
  if conn is None:
    conn = _obtener_conexion_turso()
    cerrar_al_terminar = True

  try:
    cursor = conn.execute(
        """
        SELECT 
            id,
            nombre,
            categoria,
            proveedor,
            unidad,
            costo,
            precio_dia,
            precio_noche,
            margen_ratio,
            fecha_actualizacion
        FROM productos
        WHERE activo = 1
        ORDER BY id ASC;
        """
    )
    filas = cursor.fetchall()
  except Exception as e:
    raise ErrorAccesoCatalogoTurso(
        f"Error al consultar la tabla 'productos' en Turso: {e}"
    ) from e
  finally:
    if cerrar_al_terminar:
      try:
        conn.close()
      except Exception:
        pass

  datos: List[Dict[str, Any]] = []
  for fila in filas:
    p_id = int(fila[0])
    p_nom = str(fila[1]).strip()
    p_cat = str(fila[2]).strip() if fila[2] is not None else "Otro"
    p_prov = str(fila[3]).strip() if fila[3] is not None else "General"
    p_uni = str(fila[4]).strip() if fila[4] is not None else "Unidad"
    p_costo = int(fila[5]) if fila[5] is not None else 0
    p_dia = int(fila[6]) if fila[6] is not None else 0
    p_noche = int(fila[7]) if fila[7] is not None else 0

    if fila[8] is not None:
      p_margen = float(fila[8])
    else:
      p_margen = (
          round((p_dia - p_costo) / p_costo, 4) if p_costo > 0 else 0.0
      )

    p_fecha = str(fila[9]) if fila[9] is not None else ""

    datos.append({
        "ID_PRODUCTO": p_id,
        "NOMBRE": p_nom,
        "CATEGORIA": p_cat,
        "PROVEEDOR": p_prov,
        "UNIDAD": p_uni,
        "COSTO": p_costo,
        "MARGEN_%": p_margen,
        "PRECIO_DIA": p_dia,
        "PRECIO_NOCHE": p_noche,
        "FECHA_ACT": p_fecha,
    })

  return datos


def cargar_catalogo_turso(conn: Optional[Any] = None) -> Any:
  """Obtiene el catálogo de productos activos desde Turso o conexión SQLite compatible.

  Args:
      conn: Conexión opcional. Si se omite, se conecta mediante credenciales
        TURSO_DEV_*.

  Returns:
      pandas.DataFrame (si pandas está disponible) o CatalogoFrameLiviano
      con las columnas estándar de Genaro POS.

  Raises:
      ErrorAccesoCatalogoTurso: Si ocurre un error de conexión, autenticación o
      SQL.
  """
  datos = cargar_catalogo_turso_filas(conn=conn)

  if pd is not None:
    if not datos:
      return pd.DataFrame(columns=COLUMNAS_CATALOGO_POS)
    df = pd.DataFrame(datos)
    df["ID_PRODUCTO"] = df["ID_PRODUCTO"].astype(int)
    df["COSTO"] = pd.to_numeric(df["COSTO"], errors="coerce").fillna(0)
    df["PRECIO_DIA"] = pd.to_numeric(
        df["PRECIO_DIA"], errors="coerce"
    ).fillna(0)
    df["PRECIO_NOCHE"] = pd.to_numeric(
        df["PRECIO_NOCHE"], errors="coerce"
    ).fillna(0)
    df["MARGEN_%"] = pd.to_numeric(
        df["MARGEN_%"], errors="coerce"
    ).fillna(0.0)
    return df

  return CatalogoFrameLiviano(datos, columnas=COLUMNAS_CATALOGO_POS)
