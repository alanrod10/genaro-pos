"""Funciones auxiliares de sanitización, parseo monetario y normalización temporal

para Genaro POS.
"""

from datetime import datetime, timezone
import re
from typing import Tuple
from zoneinfo import ZoneInfo

ZONA_ARG = ZoneInfo("America/Argentina/Jujuy")


def parse_moneda_pesos(valor: str | int | float) -> Tuple[int, bool]:
  """Convierte strings o números monetarios localizados a entero en pesos exactos.

  Retorna: (monto_entero, es_valido_o_cuarentena) Si hay centavos reales
  distintos de cero o formato ilegible, retorna (0, False).
  """
  if valor is None:
    return 0, False

  if isinstance(valor, (int, float)):
    if isinstance(valor, float) and not valor.is_integer():
      # Fraccional real
      return int(round(valor)), False
    return int(round(valor)), True

  s = str(valor).strip()
  if not s:
    return 0, False

  # Remover símbolo $, espacios y signos
  s_clean = s.replace("$", "").replace(" ", "")

  # Detección explícita de centavos decimales al final:
  # Patrones tipo '.50' o ',50'
  match_centavos = re.search(r"[.,](\d{1,2})$", s_clean)
  if match_centavos:
    centavos_str = match_centavos.group(1)
    # Si son centavos reales distintos de '0' o '00', se invalida a cuarentena
    if int(centavos_str) != 0:
      return 0, False
    # Si son '.00' o ',0', se remueven los decimales
    s_clean = s_clean[: -len(match_centavos.group(0))]

  # Remover separadores de miles restantes
  s_num = s_clean.replace(".", "").replace(",", "")

  try:
    monto = int(s_num)
    return monto, True
  except ValueError:
    return 0, False


def utc_to_argentina(dt_utc_str: str) -> datetime:
  """Convierte fecha ISO-8601 UTC string a datetime con zona horaria Argentina/Jujuy."""
  dt = datetime.strptime(dt_utc_str, "%Y-%m-%d %H:%M:%S").replace(
      tzinfo=timezone.utc
  )
  return dt.astimezone(ZONA_ARG)


def argentina_to_utc_str(dt_local: datetime) -> str:
  """Convierte datetime local de Argentina a string ISO-8601 UTC."""
  dt_utc = dt_local.astimezone(timezone.utc)
  return dt_utc.strftime("%Y-%m-%d %H:%M:%S")


def calcular_reparto_prepay(
    monto_carga: int,
    monto_adicional: int,
    pago_efvo: int,
    pago_transf: int,
    politica: str = "VISOR",
) -> Tuple[int, int]:
  """Calcula la asignación del capital de recarga a Efectivo y Transferencia.

  politica 'VISOR': Prioriza transferencia al capital:
      cap_transf = min(pago_transf, monto_carga)
      cap_efvo = monto_carga - cap_transf
  politica 'CAJA_PROPORCIONAL': Prorratea proporcionalmente:
      cap_efvo = round(monto_carga * pago_efvo / total_cobrado)
      cap_transf = monto_carga - cap_efvo
  """
  total_cobrado = monto_carga + monto_adicional
  if total_cobrado <= 0:
    return 0, 0

  if politica == "VISOR":
    cap_transf = min(pago_transf, monto_carga)
    cap_efvo = monto_carga - cap_transf
    return cap_efvo, cap_transf
  elif politica == "CAJA_PROPORCIONAL":
    cap_efvo = int(round(monto_carga * (pago_efvo / total_cobrado)))
    cap_transf = monto_carga - cap_efvo
    return cap_efvo, cap_transf
  else:
    raise ValueError(f"Política desconocida: {politica}")
