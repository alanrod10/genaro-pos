"""Módulo de validación y migración controlada del catálogo real (Fase 14.2-G).

Define el contrato formal de importación CSV hacia la tabla 'productos'
en Turso / SQLite, con garantías rigurosas de seguridad y determinismo:

1. Contrato explícito:
   - Columnas OBLIGATORIAS: ID_PRODUCTO, NOMBRE, COSTO, PRECIO_DIA.
   - Columnas OPCIONALES con valores predeterminados explícitos:
     CATEGORIA ('Otro'), PROVEEDOR ('General'), UNIDAD ('Unidad'),
     MARGEN_% (valor derivado informativo), FECHA_ACT (timestamp UTC actual), ACTIVO (1).
   - PRECIO_NOCHE es obsoleta: su encabezado se tolera por compatibilidad, pero la
     columna se ignora por completo (no se lee, valida ni inserta).
   - Columnas desconocidas: Rechazo estricto inmediato (no se admiten encabezados ajenos).

2. Política estricta de IDs:
   - ID_PRODUCTO debe ser un número entero positivo > 0.
   - Prohibida la autogeneración durante la migración histórica.
   - Detección y bloqueo ante IDs duplicados en el archivo.
   - Detección y bloqueo ante colisiones con el rango sintético reservado (9001..9005).
   - Detección y bloqueo ante colisiones con registros preexistentes en la base destino.

3. Validación monetaria:
   - Solo pesos enteros (INTEGER >= 0).
   - Rechazo de centavos reales (ej. ,50 o .50) y valores negativos o no numéricos.

4. Normalización determinista de fechas:
   - DD/MM/YYYY: Se almacena fijando medianoche (YYYY-MM-DD 00:00:00) sin desplazar días.
   - YYYY-MM-DD HH:MM:SS: Se almacena tal cual sin alterar la hora registrada.
   - Timestamps con Z o huso explícito: Se convierten deterministamente a UTC.
   - Fechas imposibles (ej. 31/02) o ilegibles: Rechazo como error de fila.

5. Control de margen:
   - Representado como fracción decimal (0.5 = 50 %).
   - No se escribe en la BD; prevalece la columna virtual GENERATED ALWAYS de SQL.
   - Discrepancia > 0.05 (5 puntos porcentuales) emite advertencia informativa.

6. Modo de ejecución e integridad:
   - Modo dry_run=True por defecto (cero escrituras).
   - Cero conexiones remotas o acceso a la red.
   - Checksum SHA-256 del archivo y de los registros normalizados.
"""

import csv
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from db.normalization import parse_moneda_pesos

# Columnas OBLIGATORIAS: deben figurar indefectiblemente en los encabezados del CSV
COLUMNAS_OBLIGATORIAS = [
    "ID_PRODUCTO",
    "NOMBRE",
    "COSTO",
    "PRECIO_DIA",
]

# Columnas OPCIONALES reconocidas y sus valores predeterminados explícitos
VALORES_PREDETERMINADOS_OPCIONALES = {
    "CATEGORIA": "Otro",
    "PROVEEDOR": "General",
    "UNIDAD": "Unidad",
    "MARGEN_%": None,      # Valor derivado informativo; no sobreescribe cálculo SQL
    "FECHA_ACT": None,     # Informativo; si está ausente se asigna timestamp actual UTC
    "ACTIVO": 1,           # Estado operativo; si está ausente se asigna 1 (activo)
}

COLUMNAS_OPCIONALES = list(VALORES_PREDETERMINADOS_OPCIONALES.keys())

# Campo legado admitido solo para ignorarlo. Nunca se indexa ni se interpreta.
COLUMNAS_OBSOLETAS_IGNORADAS = {"PRECIO_NOCHE"}

# Conjunto total de columnas admitidas por el contrato CSV
COLUMNAS_PERMITIDAS = set(COLUMNAS_OBLIGATORIAS) | set(COLUMNAS_OPCIONALES)

# Conjunto de IDs sintéticos reservados en el entorno de desarrollo (Fases 14.2-B y 14.2-C)
IDS_SINTETICOS_RESERVADOS = {9001, 9002, 9003, 9004, 9005}

# Umbral de discrepancia de margen (5 puntos porcentuales = 0.05 en escala fraccional)
UMBRAL_DISCREPANCIA_MARGEN = 0.05

# Estados del procedimiento
ESTADO_VALIDACION_OK = "VALIDACION_OK"
ESTADO_VALIDACION_FALLIDA = "VALIDACION_FALLIDA"
ESTADO_MIGRACION_EXITO = "MIGRACION_EXITO"
ESTADO_CONFLICTO_DATOS = "CONFLICTO_DATOS"
ESTADO_COLISION_SINTETICOS = "COLISION_SINTETICOS"
ESTADO_ARCHIVO_INVALIDO = "ARCHIVO_INVALIDO"


def calcular_sha256_archivo(path: str) -> str:
  """Calcula el hash SHA-256 del contenido binario de un archivo."""
  sha256 = hashlib.sha256()
  with open(path, "rb") as f:
    while chunk := f.read(65536):
      sha256.update(chunk)
  return sha256.hexdigest()


def calcular_sha256_registros(registros: List[Dict[str, Any]]) -> str:
  """Calcula un hash SHA-256 determinista a partir de los registros normalizados."""
  contenido_canonico = json.dumps(
      registros, sort_keys=True, ensure_ascii=False
  )
  return hashlib.sha256(contenido_canonico.encode("utf-8")).hexdigest()


def parse_booleano_activo(valor: Any) -> Tuple[int, bool]:
  """Parsea valores de estado activo a entero 1 o 0 con validación estricta.

  Valores admitidos para activo (1):
  - 1, "1", "true", "t", "si", "sí", "activo", "vigente", "" (o None)
  Valores admitidos para inactivo (0):
  - 0, "0", "false", "f", "no", "inactivo", "baja", "descontinuado"
  Cualquier otro valor retorna (0, False).
  """
  if valor is None:
    return 1, True

  s = str(valor).strip().lower()
  if s in ("", "1", "true", "t", "si", "sí", "activo", "vigente"):
    return 1, True
  if s in ("0", "false", "f", "no", "inactivo", "baja", "descontinuado"):
    return 0, True

  return 0, False


def normalizar_fecha_iso(valor: Any) -> Tuple[Optional[str], Optional[str]]:
  """Parsea y normaliza cadenas de fecha al formato canónico 'YYYY-MM-DD HH:MM:SS'.

  Reglas deterministas:
  1. Si es None o vacío: genera timestamp UTC actual.
  2. Timestamp con offset o 'Z' (ISO con huso horario): se convierte deterministamente a UTC.
  3. Timestamp local/sin zona ('YYYY-MM-DD HH:MM:SS'): se almacena tal cual, preservando la hora registrada.
  4. Fecha de calendario regional ('DD/MM/YYYY'): se almacena fijando hora en 00:00:00 (YYYY-MM-DD 00:00:00).
  5. Fecha de calendario ISO ('YYYY-MM-DD'): se almacena fijando hora en 00:00:00 (YYYY-MM-DD 00:00:00).
  6. Fechas imposibles (ej. 31/02) o ilegibles: retorna (None, error_str).
  """
  if valor is None:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), None

  s = str(valor).strip()
  if not s:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), None

  # 1. Intentar ISO con offset explícito o sufijo 'Z'
  if "T" in s or "Z" in s or ("+" in s or (s.count("-") >= 3)):
    try:
      dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
      if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), None
      else:
        return dt.strftime("%Y-%m-%d %H:%M:%S"), None
    except ValueError:
      pass

  # 2. Formato ISO estándar sin huso: 'YYYY-MM-DD HH:MM:SS'
  try:
    dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    return dt.strftime("%Y-%m-%d %H:%M:%S"), None
  except ValueError:
    pass

  # 3. Formato ISO solo fecha: 'YYYY-MM-DD'
  try:
    dt = datetime.strptime(s, "%Y-%m-%d")
    return dt.strftime("%Y-%m-%d 00:00:00"), None
  except ValueError:
    pass

  # 4. Formato regional Sheets con hora: 'DD/MM/YYYY HH:MM:SS'
  try:
    dt = datetime.strptime(s, "%d/%m/%Y %H:%M:%S")
    return dt.strftime("%Y-%m-%d %H:%M:%S"), None
  except ValueError:
    pass

  # 5. Formato regional Sheets solo fecha: 'DD/MM/YYYY'
  try:
    dt = datetime.strptime(s, "%d/%m/%Y")
    return dt.strftime("%Y-%m-%d 00:00:00"), None
  except ValueError:
    pass

  return (
      None,
      f"Formato de fecha no reconocido o fecha de calendario inválida: '{s}'",
  )


class ImportadorCatalogo:
  """Gestor de inspección, validación y carga del catálogo de productos."""

  def __init__(
      self,
      ids_prohibidos: Optional[Set[int]] = None,
      permitir_colision_sinteticos: bool = False,
  ):
    self.ids_prohibidos = (
        set(ids_prohibidos)
        if ids_prohibidos is not None
        else set(IDS_SINTETICOS_RESERVADOS)
    )
    self.permitir_colision_sinteticos = permitir_colision_sinteticos

  def validar_archivo_csv(
      self, csv_path: str
  ) -> Tuple[bool, Dict[str, Any], List[Dict[str, Any]]]:
    """Lee y valida exhaustivamente un archivo CSV de catálogo bajo el contrato formal.

    Retorna: (es_valido, resumen_metricas, registros_normalizados)
    """
    resumen: Dict[str, Any] = {
        "archivo_path": csv_path,
        "archivo_sha256": "",
        "registros_sha256": "",
        "total_filas_leidas": 0,
        "filas_validas": 0,
        "filas_invalidas": 0,
        "filas_vacias": 0,
        "ids_duplicados_en_csv": [],
        "nombres_duplicados": [],
        "colisiones_con_sinteticos": [],
        "errores": [],
        "advertencias": [],
        "estado": ESTADO_VALIDACION_FALLIDA,
    }

    if not os.path.exists(csv_path):
      resumen["errores"].append({
          "fila": 0,
          "campo": "archivo",
          "valor": csv_path,
          "motivo": "El archivo especificado no existe.",
      })
      resumen["estado"] = ESTADO_ARCHIVO_INVALIDO
      return False, resumen, []

    if not os.path.isfile(csv_path):
      resumen["errores"].append({
          "fila": 0,
          "campo": "archivo",
          "valor": csv_path,
          "motivo": "La ruta indicada no es un archivo regular.",
      })
      resumen["estado"] = ESTADO_ARCHIVO_INVALIDO
      return False, resumen, []

    try:
      resumen["archivo_sha256"] = calcular_sha256_archivo(csv_path)
    except Exception as e:
      resumen["errores"].append({
          "fila": 0,
          "campo": "archivo",
          "valor": "",
          "motivo": f"Error al calcular checksum del archivo: {e}",
      })
      return False, resumen, []

    registros: List[Dict[str, Any]] = []
    ids_vistos: Dict[int, int] = {}  # id -> primera_fila
    nombres_vistos: Dict[str, List[int]] = {}  # nombre_normalizado -> [ids]

    try:
      with open(csv_path, "r", encoding="utf-8-sig") as f:
        lector = csv.reader(f)
        try:
          encabezados_raw = next(lector)
        except StopIteration:
          resumen["errores"].append({
              "fila": 1,
              "campo": "encabezados",
              "valor": "",
              "motivo": "El archivo CSV está completamente vacío.",
          })
          return False, resumen, []

        encabezados = [c.strip().upper() for c in encabezados_raw]
        # Aceptar el encabezado obsoleto solo para compatibilidad con CSV antiguos;
        # se excluye del mapa y no se accede nunca a sus celdas.
        encabezados_procesables = [
            c for c in encabezados if c not in COLUMNAS_OBSOLETAS_IGNORADAS
        ]

        # 1. Validar presencia de columnas obligatorias
        faltantes = [c for c in COLUMNAS_OBLIGATORIAS if c not in encabezados_procesables]
        if faltantes:
          resumen["errores"].append({
              "fila": 1,
              "campo": "encabezados",
              "valor": str(encabezados),
              "motivo": f"Faltan columnas obligatorias requeridas: {faltantes}",
          })
          return False, resumen, []

        # 2. Validar columnas inesperadas (garantía de integridad del contrato)
        inesperadas = [c for c in encabezados_procesables if c not in COLUMNAS_PERMITIDAS]
        if inesperadas:
          resumen["errores"].append({
              "fila": 1,
              "campo": "encabezados",
              "valor": str(inesperadas),
              "motivo": (
                  f"El archivo contiene columnas desconocidas no permitidas: {inesperadas}. "
                  f"Columnas admitidas: obligatorias {COLUMNAS_OBLIGATORIAS}, opcionales {COLUMNAS_OPCIONALES}."
              ),
          })
          return False, resumen, []

        indice_map = {
            col: idx for idx, col in enumerate(encabezados)
            if col not in COLUMNAS_OBSOLETAS_IGNORADAS
        }

        # 3. Iterar y validar cada fila de datos
        num_fila = 1
        for fila in lector:
          num_fila += 1
          resumen["total_filas_leidas"] += 1

          # Detección de filas vacías
          if not fila or all(not str(c).strip() for c in fila):
            resumen["filas_vacias"] += 1
            continue

          if len(fila) != len(encabezados):
            resumen["errores"].append({
                "fila": num_fila,
                "campo": "fila",
                "valor": f"{len(fila)} columnas",
                "motivo": (
                    f"Número de campos discordante con encabezado ({len(encabezados)} esperados)."
                ),
            })
            resumen["filas_invalidas"] += 1
            continue

          def get_val(campo: str, default: Any = "") -> str:
            idx = indice_map.get(campo)
            if idx is None:
              return str(default) if default is not None else ""
            raw_value = fila[idx]
            v = "" if raw_value is None else str(raw_value).strip()
            # CSV representa la mayoría de los nulos con celdas vacías; admitir
            # también marcadores textuales comunes antes de aplicar defaults.
            if (not v or v.casefold() in {"null", "none"}) and default is not None:
              return str(default)
            return v

          fila_valida = True

          # 3.1 ID_PRODUCTO (Obligatorio, entero > 0, único, no colisionante con sintéticos)
          raw_id = get_val("ID_PRODUCTO")
          if not raw_id:
            resumen["errores"].append({
                "fila": num_fila,
                "campo": "ID_PRODUCTO",
                "valor": "",
                "motivo": "ID_PRODUCTO es obligatorio y no puede estar vacío.",
            })
            fila_valida = False
            prod_id = 0
          else:
            try:
              prod_id = int(raw_id)
              if prod_id <= 0:
                resumen["errores"].append({
                    "fila": num_fila,
                    "campo": "ID_PRODUCTO",
                    "valor": raw_id,
                    "motivo": "ID_PRODUCTO debe ser un entero positivo > 0.",
                })
                fila_valida = False
              else:
                if prod_id in ids_vistos:
                  resumen["ids_duplicados_en_csv"].append(prod_id)
                  resumen["errores"].append({
                      "fila": num_fila,
                      "campo": "ID_PRODUCTO",
                      "valor": str(prod_id),
                      "motivo": (
                          f"ID duplicado en el archivo (visto antes en fila {ids_vistos[prod_id]})."
                      ),
                  })
                  fila_valida = False
                else:
                  ids_vistos[prod_id] = num_fila

                if (
                    not self.permitir_colision_sinteticos
                    and prod_id in self.ids_prohibidos
                ):
                  resumen["colisiones_con_sinteticos"].append(prod_id)
                  resumen["errores"].append({
                      "fila": num_fila,
                      "campo": "ID_PRODUCTO",
                      "valor": str(prod_id),
                      "motivo": (
                          f"ID {prod_id} colisiona con el rango sintético reservado de pruebas (9001..9005)."
                      ),
                  })
                  fila_valida = False
            except ValueError:
              resumen["errores"].append({
                  "fila": num_fila,
                  "campo": "ID_PRODUCTO",
                  "valor": raw_id,
                  "motivo": "ID_PRODUCTO debe ser un número entero válido.",
              })
              fila_valida = False
              prod_id = 0

          # 3.2 NOMBRE (Obligatorio, no vacío)
          nombre = get_val("NOMBRE")
          if not nombre:
            resumen["errores"].append({
                "fila": num_fila,
                "campo": "NOMBRE",
                "valor": "",
                "motivo": "NOMBRE de producto es obligatorio.",
            })
            fila_valida = False
          else:
            nom_key = nombre.casefold()
            if nom_key in nombres_vistos:
              if nombre not in resumen["nombres_duplicados"]:
                resumen["nombres_duplicados"].append(nombre)
              nombres_vistos[nom_key].append(prod_id)
            else:
              nombres_vistos[nom_key] = [prod_id]

          # 3.3 CATEGORIA, PROVEEDOR, UNIDAD (Opcionales con defaults explícitos)
          categoria = get_val("CATEGORIA", VALORES_PREDETERMINADOS_OPCIONALES["CATEGORIA"])
          proveedor = get_val("PROVEEDOR", VALORES_PREDETERMINADOS_OPCIONALES["PROVEEDOR"])
          unidad = get_val("UNIDAD", VALORES_PREDETERMINADOS_OPCIONALES["UNIDAD"])

          # 3.4 COSTO (Obligatorio, pesos enteros >= 0)
          raw_costo = get_val("COSTO")
          costo, es_costo_valido = parse_moneda_pesos(raw_costo)
          if not es_costo_valido or costo < 0:
            resumen["errores"].append({
                "fila": num_fila,
                "campo": "COSTO",
                "valor": raw_costo,
                "motivo": (
                    "COSTO debe ser un número entero no negativo sin centavos fraccionarios."
                ),
            })
            fila_valida = False

          # 3.5 PRECIO_DIA (Obligatorio, pesos enteros >= 0)
          raw_precio_dia = get_val("PRECIO_DIA")
          precio_dia, es_precio_valido = parse_moneda_pesos(raw_precio_dia)
          if not es_precio_valido or precio_dia < 0:
            resumen["errores"].append({
                "fila": num_fila,
                "campo": "PRECIO_DIA",
                "valor": raw_precio_dia,
                "motivo": (
                    "PRECIO_DIA debe ser un número entero no negativo sin centavos fraccionarios."
                ),
            })
            fila_valida = False

          # 3.7 MARGEN_% (Opcional derivado: auditoría de discrepancia sin sobreescritura SQL)
          raw_margen = get_val("MARGEN_%", "")
          margen_csv = None
          if raw_margen:
            try:
              m_str = raw_margen.replace("%", "").replace(",", ".").strip()
              m_val = float(m_str)
              # Solo convertir cuando el CSV declara explícitamente el sufijo "%".
              # Un ratio decimal puede superar 1.0; la magnitud por sí sola no cambia la escala.
              if raw_margen.strip().endswith("%"):
                m_val_fraccion = m_val / 100.0
                resumen["advertencias"].append(
                    f"Fila {num_fila} ({nombre}): MARGEN_% incluye el sufijo explícito '%'; se interpretó como escala porcentual y se convirtió a fracción decimal."
                )
              else:
                m_val_fraccion = m_val
              margen_csv = m_val_fraccion
            except ValueError:
              resumen["advertencias"].append(
                  f"Fila {num_fila}: Formato de MARGEN_% no numérico ('{raw_margen}'). Se ignorará y se calculará en base de datos."
              )

          margen_esperado = (
              round((precio_dia - costo) / costo, 4) if costo > 0 else 0.0
          )
          if margen_csv is not None:
            diferencia = abs(margen_csv - margen_esperado)
            if diferencia > UMBRAL_DISCREPANCIA_MARGEN:
              resumen["advertencias"].append(
                  f"Fila {num_fila} ({nombre}): MARGEN_% en archivo ({margen_csv:.4f}) difiere por más de {UMBRAL_DISCREPANCIA_MARGEN} del cálculo SQL ({margen_esperado:.4f}). Prevalecerá la fórmula del motor SQL."
              )

          # 3.8 ACTIVO (Opcional, default = 1)
          raw_activo = get_val("ACTIVO", "1")
          activo, activo_valido = parse_booleano_activo(raw_activo)
          if not activo_valido:
            resumen["errores"].append({
                "fila": num_fila,
                "campo": "ACTIVO",
                "valor": raw_activo,
                "motivo": (
                    "Valor de ACTIVO no reconocido. Valores admitidos: 1, 0, true, false, si, no, activo, inactivo."
                ),
            })
            fila_valida = False

          # 3.9 FECHA_ACT (Opcional, default = now UTC)
          raw_fecha = get_val("FECHA_ACT", "")
          fecha_iso, err_fecha = normalizar_fecha_iso(raw_fecha if raw_fecha else None)
          if err_fecha:
            resumen["errores"].append({
                "fila": num_fila,
                "campo": "FECHA_ACT",
                "valor": raw_fecha,
                "motivo": err_fecha,
            })
            fila_valida = False

          if fila_valida:
            resumen["filas_validas"] += 1
            registros.append({
                "id": prod_id,
                "nombre": nombre,
                "categoria": categoria,
                "proveedor": proveedor,
                "unidad": unidad,
                "costo": costo,
                "precio_dia": precio_dia,
                "activo": activo,
                "fecha_actualizacion": fecha_iso,
            })
          else:
            resumen["filas_invalidas"] += 1

    except Exception as e:
      resumen["errores"].append({
          "fila": 0,
          "campo": "lectura",
          "valor": "",
          "motivo": f"Fallo al procesar el archivo CSV: {e}",
      })
      return False, resumen, []

    if resumen["errores"]:
      resumen["estado"] = ESTADO_VALIDACION_FALLIDA
      if resumen["colisiones_con_sinteticos"]:
        resumen["estado"] = ESTADO_COLISION_SINTETICOS
      return False, resumen, registros

    resumen["registros_sha256"] = calcular_sha256_registros(registros)
    resumen["estado"] = ESTADO_VALIDACION_OK
    return True, resumen, registros

  def previsualizar_operaciones(
      self, registros: List[Dict[str, Any]], limite_muestras: int = 5
  ) -> List[Dict[str, Any]]:
    """Genera una muestra estructurada de las operaciones INSERT que se ejecutarían."""
    muestras = []
    for r in registros[:limite_muestras]:
      muestras.append({
          "operacion": "INSERT",
          "tabla": "productos",
          "id": r["id"],
          "nombre": r["nombre"],
          "proveedor": r["proveedor"],
          "costo": r["costo"],
          "precio_dia": r["precio_dia"],
          "activo": r["activo"],
          "fecha_actualizacion": r["fecha_actualizacion"],
          "margen_ratio_calculado_por_sql": (
              round((r["precio_dia"] - r["costo"]) / r["costo"], 4)
              if r["costo"] > 0
              else 0.0
          ),
      })
    return muestras

  def migrar_catalogo(
      self,
      csv_path: str,
      conn: Optional[Any] = None,
      dry_run: bool = True,
  ) -> Tuple[bool, Dict[str, Any]]:
    """Ejecuta el protocolo de validación y migración controlada.

    Args:
        csv_path: Ruta del archivo CSV.
        conn: Conexión SQLite/Turso local inyectable. NUNCA se conecta
          automáticamente a la red.
        dry_run: Si es True, no ejecuta escrituras; valida y retorna preview.

    Returns:
        (exito, resumen_detallado)
    """
    es_valido, resumen, registros = self.validar_archivo_csv(csv_path)

    resumen["dry_run"] = dry_run
    resumen["filas_insertadas"] = 0
    resumen["preview_operaciones"] = self.previsualizar_operaciones(registros)

    if not es_valido:
      return False, resumen

    if dry_run:
      return True, resumen

    if conn is None:
      resumen["errores"].append({
          "fila": 0,
          "campo": "conexion",
          "valor": "None",
          "motivo": (
              "No se especificó una conexión a base de datos. Escrituras remotas no permitidas en esta fase."
          ),
      })
      resumen["estado"] = ESTADO_VALIDACION_FALLIDA
      return False, resumen

    cursor = conn.execute("SELECT id FROM productos;")
    ids_existentes = {fila[0] for fila in cursor.fetchall()}

    ids_nuevos = {r["id"] for r in registros}
    colisiones_destino = ids_existentes.intersection(ids_nuevos)
    if colisiones_destino:
      resumen["errores"].append({
          "fila": 0,
          "campo": "destino",
          "valor": str(sorted(list(colisiones_destino))),
          "motivo": (
              f"La base de destino ya contiene {len(colisiones_destino)} IDs que colisionan con el archivo. Se aborta la migración por seguridad."
          ),
      })
      resumen["estado"] = ESTADO_CONFLICTO_DATOS
      return False, resumen

    sql_insert = """
            INSERT INTO productos (id, nombre, categoria, proveedor, unidad, costo, precio_dia, activo, fecha_actualizacion)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
    try:
      with conn:
        for r in registros:
          conn.execute(
              sql_insert,
              (
                  r["id"],
                  r["nombre"],
                  r["categoria"],
                  r["proveedor"],
                  r["unidad"],
                  r["costo"],
                  r["precio_dia"],
                  r["activo"],
                  r["fecha_actualizacion"],
              ),
          )
      resumen["filas_insertadas"] = len(registros)
      resumen["estado"] = ESTADO_MIGRACION_EXITO
      return True, resumen
    except Exception as e:
      resumen["errores"].append({
          "fila": 0,
          "campo": "transaccion",
          "valor": "",
          "motivo": f"Fallo durante la inserción transaccional: {e}",
      })
      resumen["estado"] = ESTADO_VALIDACION_FALLIDA
      return False, resumen
