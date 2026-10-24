"""Procedimiento de aprovisionamiento seguro del catálogo sintético (Fase 14.2-B).

Este módulo define el protocolo y artefactos para aprovisionar un conjunto pequeño
de datos sintéticos en la base de desarrollo 'genaro-pos-dev'.

REGLAS DE SEGURIDAD ESTRICTAS:
1. No se ejecuta automáticamente ni realiza conexiones remotas en esta fase.
2. Modo dry-run (validación previa) que no emite escrituras.
3. Validación exhaustiva de destino mediante análisis estructurado de URL.
4. Prohibido: DROP TABLE, DELETE masivo, truncado o sobrescrituras ciegas.
5. Detección y bloqueo inmediato ante datos en conflicto o inesperados.
6. Inserción atómica y transaccional.
7. Idempotencia: distingue reintento seguro de colisión de datos.
8. Cero exposición de secretos o tokens en logs y excepciones.
"""

from datetime import datetime, timezone
import os
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse

# Estados del procedimiento
ESTADO_VALIDACION_OK = "VALIDACION_OK"
ESTADO_BLOQUEADO_PREFLIGHT = "BLOQUEADO_PREFLIGHT"
ESTADO_APROVISIONADO_EXITO = "APROVISIONADO_EXITO"
ESTADO_YA_APROVISIONADO = "YA_APROVISIONADO"
ESTADO_CONFLICTO_DATOS = "CONFLICTO_DATOS"

# Destino remoto exacto y autorizado para el laboratorio DEV
DESTINO_AUTORIZADO_SCHEME = "turso"
DESTINO_AUTORIZADO_HOST = "genaro-pos-dev-alanrod10.aws-sa-east-1.turso.io"

# Objeto interno autorizado del motor TursoDB
OBJETO_INTERNO_PERMITIDO_TIPO = "table"
OBJETO_INTERNO_PERMITIDO_NOMBRE = "__turso_internal_mvcc_meta"

# Conjunto de productos sintéticos (completamente ficticios, no copiados de producción)
PRODUCTOS_SINTETICOS: List[Dict[str, Any]] = [
    {
        "id": 9001,
        "nombre": "Alfajor Sintético Simple",
        "categoria": "Golosinas",
        "proveedor": "Distribuidora Ficticia Alfa",
        "unidad": "Unidad",
        "costo": 300,
        "precio_dia": 500,
        "precio_noche": 500,
        "activo": 1,
        "fecha_actualizacion": "2026-10-22 12:00:00",
    },
    {
        "id": 9002,
        "nombre": "Galletitas Ficticias Vainilla 150g",
        "categoria": "Almacén",
        "proveedor": "Distribuidora Ficticia Alfa",
        "unidad": "Unidad",
        "costo": 400,
        "precio_dia": 600,
        "precio_noche": 600,
        "activo": 1,
        "fecha_actualizacion": "2026-10-22 12:00:00",
    },
    {
        # Producto con nombre duplicado respecto al 9002 pero distinto proveedor, costo y precio
        "id": 9003,
        "nombre": "Galletitas Ficticias Vainilla 150g",
        "categoria": "Almacén",
        "proveedor": "Mayorista Ficticio Beta",
        "unidad": "Unidad",
        "costo": 450,
        "precio_dia": 700,
        "precio_noche": 700,
        "activo": 1,
        "fecha_actualizacion": "2026-10-22 12:00:00",
    },
    {
        # Producto con costo 0 (servicio o cortesía)
        "id": 9004,
        "nombre": "Bolsa Ficticia Biodegradable",
        "categoria": "Varios",
        "proveedor": "General",
        "unidad": "Unidad",
        "costo": 0,
        "precio_dia": 50,
        "precio_noche": 50,
        "activo": 1,
        "fecha_actualizacion": "2026-10-22 12:00:00",
    },
    {
        # Producto inactivo: NO debe ser leído en el catálogo de venta
        "id": 9005,
        "nombre": "Producto Descontinuado Ficticio",
        "categoria": "Almacén",
        "proveedor": "Proveedor Antiguo",
        "unidad": "Unidad",
        "costo": 200,
        "precio_dia": 350,
        "precio_noche": 350,
        "activo": 0,
        "fecha_actualizacion": "2026-10-22 12:00:00",
    },
]

IDS_SINTETICOS_RESERVADOS = {p["id"] for p in PRODUCTOS_SINTETICOS}


def validar_endpoint_estructurado(url: Optional[str]) -> Tuple[bool, str]:
  """Valida exhaustivamente la URL del endpoint remoto mediante análisis sintáctico formal."""
  if not url:
    return False, "Variable de URL de Turso no configurada."

  try:
    parsed = urllib.parse.urlparse(url)
  except Exception as e:
    return False, f"Error al parsear URL: {e}"

  if parsed.scheme != DESTINO_AUTORIZADO_SCHEME:
    return False, (
        f"Esquema no autorizado: '{parsed.scheme}'. Debe iniciar con"
        f" '{DESTINO_AUTORIZADO_SCHEME}://'."
    )

  if parsed.hostname != DESTINO_AUTORIZADO_HOST:
    return False, (
        f"Host remoto no autorizado: '{parsed.hostname}'. Destino esperado:"
        f" '{DESTINO_AUTORIZADO_HOST}'."
    )

  if parsed.username is not None or parsed.password is not None:
    return False, "La URL contiene credenciales embebidas (prohibido)."

  if parsed.port is not None:
    return False, f"Puerto personalizado no autorizado: {parsed.port}."

  if parsed.path not in ("", "/"):
    return False, f"Ruta en URL no autorizada: '{parsed.path}'."

  if parsed.query != "":
    return False, f"Parámetros de consulta no permitidos: '{parsed.query}'."

  if parsed.fragment != "":
    return False, f"Fragmento no permitido: '{parsed.fragment}'."

  return True, ""


def validar_preflight_aprovisionamiento() -> Tuple[bool, List[str]]:
  """Valida los controles obligatorios antes de conectar o aprovisionar."""
  bloqueos = []

  env_name = os.environ.get("ENV_NAME")
  if env_name != "genaro-pos-dev":
    bloqueos.append(
        "ENV_NAME no está configurado como 'genaro-pos-dev' (actual:"
        f" '{env_name}')."
    )

  test_approved = os.environ.get("TURSO_TEST_APPROVED")
  if test_approved != "YES":
    bloqueos.append(
        "TURSO_TEST_APPROVED no está configurado como 'YES' (actual:"
        f" '{test_approved}')."
    )

  url = os.environ.get("TURSO_DEV_DATABASE_URL")
  valido, detalle_url = validar_endpoint_estructurado(url)
  if not valido:
    bloqueos.append(detalle_url)

  token = os.environ.get("TURSO_DEV_AUTH_TOKEN")
  if not token:
    bloqueos.append("TURSO_DEV_AUTH_TOKEN no configurada en el entorno.")

  try:
    import turso_serverless
  except ImportError:
    bloqueos.append("Paquete 'turso-serverless' no disponible en el entorno.")

  return len(bloqueos) == 0, bloqueos


def inspeccionar_estado_tabla_productos(conn: Any) -> Tuple[str, str]:
  """Inspecciona la tabla 'productos' para determinar si está vacía, aprovisionada o en conflicto.

  Retorna:
      (estado, mensaje_detalle)
  """
  cursor_master = conn.execute(
      "SELECT name FROM sqlite_master WHERE type='table' AND name='productos';"
  )
  if not cursor_master.fetchone():
    return (
        "NO_EXISTE",
        "La tabla 'productos' aún no existe (requiere creación previa de DDL).",
    )

  cursor_filas = conn.execute(
      "SELECT id, nombre, categoria, proveedor, unidad, costo, precio_dia,"
      " precio_noche, activo FROM productos ORDER BY id ASC;"
  )
  filas = cursor_filas.fetchall()

  if not filas:
    return "VACIA", "La tabla 'productos' existe y está vacía."

  ids_existentes = {f[0] for f in filas}

  # Caso 1: Los IDs corresponden exactamente al set sintético
  if ids_existentes == IDS_SINTETICOS_RESERVADOS:
    # Comprobar que los datos coincidan
    coincide_todo = True
    dict_sinteticos = {p["id"]: p for p in PRODUCTOS_SINTETICOS}
    for f in filas:
      p_id = f[0]
      esperado = dict_sinteticos[p_id]
      if (
          f[1] != esperado["nombre"]
          or f[2] != esperado["categoria"]
          or f[3] != esperado["proveedor"]
          or f[4] != esperado["unidad"]
          or f[5] != esperado["costo"]
          or f[6] != esperado["precio_dia"]
          or f[7] != esperado["precio_noche"]
          or f[8] != esperado["activo"]
      ):
        coincide_todo = False
        break

    if coincide_todo:
      return (
          ESTADO_YA_APROVISIONADO,
          "La tabla ya contiene exactamente los 5 productos sintéticos del set"
          " reservado.",
      )
    else:
      return (
          ESTADO_CONFLICTO_DATOS,
          "Existen filas con los IDs sintéticos pero con datos discordantes.",
      )

  # Caso 2: Existen IDs ajenos o solapamiento parcial
  return (
      ESTADO_CONFLICTO_DATOS,
      "La tabla 'productos' contiene registros ajenos o IDs fuera del set"
      f" reservado ({ids_existentes}). Abortando por seguridad.",
  )


def aprovisionar_catalogo_sintetico(
    conn: Optional[Any] = None, dry_run: bool = False
) -> Tuple[bool, str, Dict[str, Any]]:
  """Ejecuta el protocolo de aprovisionamiento seguro del catálogo sintético.

  Args:
      conn: Conexión opcional (inyectable para pruebas locales). Si es None,
        valida preflight y conecta a Turso DEV.
      dry_run: Si es True, no ejecuta escrituras; solo valida y reporta.

  Returns:
      (exito, estado, resumen)
  """
  resumen: Dict[str, Any] = {
      "dry_run": dry_run,
      "filas_insertadas": 0,
      "estado": ESTADO_BLOQUEADO_PREFLIGHT,
      "detalle": "",
  }

  cerrar_conn = False
  if conn is None:
    aprobado, bloqueos = validar_preflight_aprovisionamiento()
    if not aprobado:
      resumen["estado"] = ESTADO_BLOQUEADO_PREFLIGHT
      resumen["detalle"] = "Bloqueado por preflight: " + "; ".join(bloqueos)
      return False, ESTADO_BLOQUEADO_PREFLIGHT, resumen

    url = os.environ["TURSO_DEV_DATABASE_URL"]
    token = os.environ["TURSO_DEV_AUTH_TOKEN"]
    import turso_serverless

    conn = turso_serverless.connect(url, auth_token=token)
    cerrar_conn = True

  try:
    estado_tabla, detalle_tabla = inspeccionar_estado_tabla_productos(conn)

    if estado_tabla == ESTADO_CONFLICTO_DATOS:
      resumen["estado"] = ESTADO_CONFLICTO_DATOS
      resumen["detalle"] = detalle_tabla
      return False, ESTADO_CONFLICTO_DATOS, resumen

    if estado_tabla == ESTADO_YA_APROVISIONADO:
      resumen["estado"] = ESTADO_YA_APROVISIONADO
      resumen["detalle"] = detalle_tabla
      return True, ESTADO_YA_APROVISIONADO, resumen

    if estado_tabla == "NO_EXISTE":
      resumen["estado"] = ESTADO_BLOQUEADO_PREFLIGHT
      resumen["detalle"] = (
          "La tabla 'productos' no existe en la base remota. Debe ejecutarse el"
          " DDL correspondiente antes del aprovisionamiento."
      )
      return False, ESTADO_BLOQUEADO_PREFLIGHT, resumen

    # Aquí estado_tabla == "VACIA"
    if dry_run:
      resumen["estado"] = ESTADO_VALIDACION_OK
      resumen["detalle"] = (
          "Validación previa exitosa: tabla 'productos' vacía y lista para"
          " aprovisionar. No se escribieron datos (dry_run=True)."
      )
      return True, ESTADO_VALIDACION_OK, resumen

    # Inserción transaccional atómica de los 5 productos sintéticos
    sql_insert = """
            INSERT INTO productos (id, nombre, categoria, proveedor, unidad, costo, precio_dia, precio_noche, activo, fecha_actualizacion)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
    for p in PRODUCTOS_SINTETICOS:
      conn.execute(
          sql_insert,
          (
              p["id"],
              p["nombre"],
              p["categoria"],
              p["proveedor"],
              p["unidad"],
              p["costo"],
              p["precio_dia"],
              p["precio_noche"],
              p["activo"],
              p["fecha_actualizacion"],
          ),
      )

    conn.commit()
    resumen["filas_insertadas"] = len(PRODUCTOS_SINTETICOS)
    resumen["estado"] = ESTADO_APROVISIONADO_EXITO
    resumen["detalle"] = (
        f"Se aprovisionaron exitosamente {len(PRODUCTOS_SINTETICOS)} productos"
        f" sintéticos (IDs {sorted(list(IDS_SINTETICOS_RESERVADOS))})."
    )
    return True, ESTADO_APROVISIONADO_EXITO, resumen

  finally:
    if cerrar_conn:
      try:
        conn.close()
      except Exception:
        pass
