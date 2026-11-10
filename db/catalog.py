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


class ErrorAdminCatalogo(ErrorCatalogo):
  """Excepción base para errores de administración del catálogo."""

  pass


class ProductoNoEncontrado(ErrorAdminCatalogo):
  """El producto solicitado no existe en la base de datos."""

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

    conn = turso_serverless.connect(url, auth_token=token)
    try:
      conn.execute("PRAGMA foreign_keys = ON")
    except Exception:
      pass
    return conn
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
            (CASE WHEN costo > 0 THEN ROUND((CAST(precio_dia AS REAL) - costo) / costo, 4) ELSE 0.0 END) AS margen_ratio,
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


def eliminar_producto_inteligente_turso(
    id_producto: int,
    conn: Optional[Any] = None,
    actor: str = "sistema",
) -> Dict[str, Any]:
  """Elimina un producto aplicando la lógica de Smart Delete.

  - Consulta si `id_producto` posee ventas registradas en `ticket_items`.
  - Si NO tiene ventas: ejecuta `DELETE FROM productos WHERE id = ?` (borrado físico limpio).
  - Si SÍ tiene ventas: ejecuta `UPDATE productos SET activo = 0 WHERE id = ?` (baja lógica).
  """
  cerrar_al_terminar = False
  if conn is None:
    conn = _obtener_conexion_turso()
    cerrar_al_terminar = True

  try:
    cur_prod = conn.execute(
        """
        SELECT id, nombre, categoria, proveedor, unidad, costo, precio_dia, activo, fecha_actualizacion
        FROM productos
        WHERE id = ?;
        """,
        (id_producto,),
    )
    fila = cur_prod.fetchone()
    if not fila:
      raise ProductoNoEncontrado(
          f"No existe ningún producto con ID {id_producto}."
      )

    nombre_prod = str(fila[1]).strip()
    fecha_act = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # Consultar si existen ventas asociadas en ticket_items
    tiene_ventas = False
    cur_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ticket_items';"
    )
    if cur_table.fetchone():
      cols = {
          str(r[1]).lower()
          for r in conn.execute("PRAGMA table_info(ticket_items);").fetchall()
      }
      condiciones: List[str] = []
      params_ventas: List[Any] = []
      if "producto_id" in cols:
        condiciones.append("producto_id = ?")
        params_ventas.append(id_producto)
      if "id_producto" in cols:
        condiciones.append("id_producto = ?")
        params_ventas.append(id_producto)
      if "producto_nombre" in cols:
        if "producto_id" in cols:
          condiciones.append(
              "(producto_id IS NULL AND producto_nombre = ? COLLATE NOCASE)"
          )
          params_ventas.append(nombre_prod)
        elif not condiciones:
          condiciones.append("producto_nombre = ? COLLATE NOCASE")
          params_ventas.append(nombre_prod)

      if condiciones:
        sql_ventas = (
            f"SELECT 1 FROM ticket_items WHERE {' OR '.join(condiciones)} LIMIT 1;"
        )
        if conn.execute(sql_ventas, tuple(params_ventas)).fetchone():
          tiene_ventas = True

    tiene_auditoria = bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='productos_auditoria';"
        ).fetchone()
    )

    if tiene_ventas:
      conn.execute(
          """
          UPDATE productos
          SET activo = 0, fecha_actualizacion = ?
          WHERE id = ?;
          """,
          (fecha_act, id_producto),
      )
      if tiene_auditoria:
        import json

        snapshot_ant = {
            "id_producto": int(fila[0]),
            "nombre": nombre_prod,
            "categoria": str(fila[2] or "Otro"),
            "proveedor": str(fila[3] or "General"),
            "unidad": str(fila[4] or "Unidad"),
            "costo": int(fila[5] or 0),
            "precio_venta": int(fila[6] or 0),
            "activo": bool(fila[7]),
            "fecha_act": str(fila[8] or ""),
        }
        snapshot_nuevo = dict(snapshot_ant, activo=False, fecha_act=fecha_act)
        conn.execute(
            """
            INSERT INTO productos_auditoria (
                producto_id, accion, valores_anteriores, valores_nuevos, fecha_utc, actor
            )
            VALUES (?, 'DESACTIVAR', ?, ?, ?, ?);
            """,
            (
                id_producto,
                json.dumps(snapshot_ant, ensure_ascii=False),
                json.dumps(snapshot_nuevo, ensure_ascii=False),
                fecha_act,
                actor,
            ),
        )
      try:
        conn.commit()
      except Exception:
        pass

      return {
          "id_producto": id_producto,
          "nombre": nombre_prod,
          "status": "desactivado",
          "accion": "baja_logica",
          "eliminado_fisico": False,
          "activo": False,
          "tiene_ventas": True,
          "mensaje": (
              f"El producto '{nombre_prod}' posee ventas registradas y fue "
              "desactivado lógicamente."
          ),
      }
    else:
      if tiene_auditoria:
        conn.execute(
            "DELETE FROM productos_auditoria WHERE producto_id = ?;",
            (id_producto,),
        )
      conn.execute("DELETE FROM productos WHERE id = ?;", (id_producto,))
      try:
        conn.commit()
      except Exception:
        pass

      return {
          "id_producto": id_producto,
          "nombre": nombre_prod,
          "status": "eliminado",
          "accion": "borrado_fisico",
          "eliminado_fisico": True,
          "activo": False,
          "tiene_ventas": False,
          "mensaje": (
              f"Producto '{nombre_prod}' eliminado físicamente del catálogo."
          ),
      }
  except ProductoNoEncontrado:
    try:
      conn.rollback()
    except Exception:
      pass
    raise
  except Exception as e:
    try:
      conn.rollback()
    except Exception:
      pass
    raise ErrorAdminCatalogo(
        f"Error al eliminar el producto ID {id_producto}: {e}"
    ) from e
  finally:
    if cerrar_al_terminar:
      try:
        conn.close()
      except Exception:
        pass

