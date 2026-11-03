"""Biblioteca Monetaria de Precisión — GENARO TESORERÍA LABORATORIO.

Implementación de LAB-002:
- parseMoney
- addMoney
- subtractMoney
- compareMoney
- allocateMoney
- formatMoney
- assertValidMoney

REGLAS DE PRECISIÓN MONETARIA:
- La unidad canónica de persistencia y cálculo es el CENTAVO ENTERO (INTEGER).
- Prohibido el uso de redondeos implícitos de punto flotante de IEEE 754.
- Inmunidad a desbordamientos y pérdidas de centavos indivisibles (distribución Hare-Clark / mayor resto).
- Rango seguro compatible con SQLite 64-bit y JavaScript MAX_SAFE_INTEGER (+/- 9,007,199,254,740,991).
"""

from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
import re
from typing import Any, List, Sequence, Tuple, Union

# Límites de enteros seguros (64-bit integer y JS Number.MAX_SAFE_INTEGER)
MAX_SAFE_MONEY = 9_007_199_254_740_991
MIN_SAFE_MONEY = -9_007_199_254_740_991

# Expresión regular para validar formato monetario en texto
_MONEY_CLEAN_RE = re.compile(r"[^\d,\.\-]")


class ErrorMonetario(ValueError):
    """Excepción lanzada cuando ocurre una violación de integridad o cálculo monetario."""
    pass


def assertValidMoney(value: Any) -> None:
    """Valida que un valor sea un monto monetario entero en centavos válido.

    Requisitos:
    - Debe ser entero (int exacto, no bool, no float, no NaN, no None).
    - Debe estar dentro del rango seguro [-MAX_SAFE_MONEY, MAX_SAFE_MONEY].
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ErrorMonetario(
            f"Monto monetario inválido: se esperaba int entero en centavos, recibido {type(value).__name__} ({value!r})"
        )

    if value < MIN_SAFE_MONEY or value > MAX_SAFE_MONEY:
        raise ErrorMonetario(
            f"Desbordamiento monetario: {value} fuera de los límites permitidos [{MIN_SAFE_MONEY}, {MAX_SAFE_MONEY}]"
        )


def parseMoney(value: Any) -> int:
    """Convierte un valor de entrada a centavos enteros (INTEGER) de forma determinista y sin coma flotante.

    Admite:
    - Dict/objeto con clave 'centavos': extrae y valida el entero.
    - Entero: se interpreta como centavos si ya viene en formato de persistencia, o se valida directamente.
    - Cadenas de texto: ej. "100.50", "100,50", "$ 1.234,56", "1,234.56", "-45.00", "500".
    - Números decimales o flotantes: se convierten vía cadena y Decimal para evitar redondeos espurios IEEE 754.

    Retorna:
    - int con la cantidad exacta de centavos.
    """
    if value is None:
        raise ErrorMonetario("No se puede parsear un valor monetario nulo (None).")

    # Si es un diccionario con clave centavos
    if isinstance(value, dict) and "centavos" in value:
        c = value["centavos"]
        assertValidMoney(c)
        return c

    # Si es booleano (en Python bool es subclase de int, rechazar explícitamente)
    if isinstance(value, bool):
        raise ErrorMonetario("Un booleano no es un monto monetario válido.")

    # Si ya es int y se pasa directamente
    # Nota: para valores enteros explícitos como 100 centavos
    # Si viene como int puro, validamos directamente si ya es centavos
    # Si viene como str '100', representa 100 pesos = 10000 centavos.
    # Para consistencia: si se pasa str '100' -> 10000 centavos.
    # Si se pasa int directamente, en APIs financieras de persistencia suele recibirse en centavos.
    # Sin embargo, si alguien llama parseMoney(100), ¿es 100 centavos o 100 pesos?
    # Para unificar: las cadenas y floats se leen en PESOS (unidades principales) y se convierten a centavos.
    # Los dict {"centavos": n} se leen como centavos.
    # Si es int, si se llama parseMoney(100), permitimos tanto centavos como pesos?
    # Mejor: los enteros pasados como str "100" son 100.00 pesos -> 10000 centavos.
    # Para ints puros: si alguien pasa parseMoney(100), para evitar ambigüedad,
    # convertimos a str -> 100 pesos -> 10000 centavos, EXCEPTO si se especifica {"centavos": ...}
    # o si se provee la función helper cents().
    # PERO comprobemos: Si alguien hace parseMoney("100.50"), es 10050.
    # Si alguien hace parseMoney(100.50), es 10050.
    # Si alguien hace parseMoney(100), str(100) -> "100" -> 10000 centavos.
    # Si ya tiene centavos enteros y quiere validar: assertValidMoney(c).

    if isinstance(value, (int, float)):
        str_val = str(value)
    elif isinstance(value, Decimal):
        str_val = str(value)
    elif isinstance(value, str):
        str_val = value.strip()
    else:
        raise ErrorMonetario(f"Tipo no soportado para parseMoney: {type(value).__name__}")

    if not str_val:
        raise ErrorMonetario("Cadena monetaria vacía.")

    # Limpiar espacios y símbolos
    # Detectar signo negativo
    es_negativo = "-" in str_val
    texto = _MONEY_CLEAN_RE.sub("", str_val)

    if not texto:
        raise ErrorMonetario(f"Cadena no contiene caracteres numéricos válidos: {value!r}")

    # Normalizar separadores decimales y de miles:
    # Casos comunes:
    # 1.234,56 (formato es-AR) -> punto miles, coma decimal
    # 1,234.56 (formato en-US) -> coma miles, punto decimal
    # 1234,56 -> coma decimal
    # 1234.56 -> punto decimal
    # 1234 -> sin decimales

    if "," in texto and "." in texto:
        idx_coma = texto.rfind(",")
        idx_punto = texto.rfind(".")
        if idx_coma > idx_punto:
            # Formato es-AR: 1.234,56
            texto = texto.replace(".", "").replace(",", ".")
        else:
            # Formato en-US: 1,234.56
            texto = texto.replace(",", "")
    elif "," in texto:
        # Solo coma: ej 1234,56
        texto = texto.replace(",", ".")

    try:
        dec = Decimal(texto)
    except InvalidOperation:
        raise ErrorMonetario(f"Formato numérico monetario inválido: {value!r}")

    if dec.is_nan() or dec.is_infinite():
        raise ErrorMonetario(f"El valor monetario no puede ser NaN ni infinito: {value!r}")

    # Convertir a centavos multiplicando por 100 exactamente con Decimal
    dec_centavos = (dec * 100).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)
    centavos = int(dec_centavos)

    if es_negativo and centavos > 0:
        centavos = -centavos
    elif not es_negativo and centavos < 0:
        centavos = abs(centavos)

    assertValidMoney(centavos)
    return centavos


def addMoney(a: int, b: int) -> int:
    """Suma dos montos en centavos enteros con validación estricta."""
    assertValidMoney(a)
    assertValidMoney(b)
    resultado = a + b
    assertValidMoney(resultado)
    return resultado


def subtractMoney(a: int, b: int) -> int:
    """Resta b de a en centavos enteros con validación estricta."""
    assertValidMoney(a)
    assertValidMoney(b)
    resultado = a - b
    assertValidMoney(resultado)
    return resultado


def compareMoney(a: int, b: int) -> int:
    """Compara dos montos monetarios en centavos enteros.

    Retorna:
    - 1 si a > b
    - 0 si a == b
    - -1 si a < b
    """
    assertValidMoney(a)
    assertValidMoney(b)
    if a > b:
        return 1
    elif a < b:
        return -1
    return 0


def allocateMoney(total_centavos: int, ratios: Sequence[Union[int, float, Decimal]]) -> List[int]:
    """Distribuye un monto en centavos según ratios dados garantizando preservación exacta.

    Utiliza el método del mayor resto (Hamilton-Hare) para asegurar que:
    sum(asignaciones) == total_centavos
    No se pierde ni se crea un solo centavo por redondeos indivisibles.
    """
    assertValidMoney(total_centavos)

    if not ratios:
        raise ErrorMonetario("La lista de ratios no puede estar vacía.")

    # Validar ratios
    ratios_dec: List[Decimal] = []
    for r in ratios:
        if isinstance(r, bool):
            raise ErrorMonetario("Los ratios no pueden ser booleanos.")
        try:
            d = Decimal(str(r))
            if d < 0:
                raise ErrorMonetario("Los ratios no pueden ser negativos.")
            ratios_dec.append(d)
        except (InvalidOperation, ValueError):
            raise ErrorMonetario(f"Ratio inválido: {r!r}")

    suma_ratios = sum(ratios_dec)
    if suma_ratios <= 0:
        raise ErrorMonetario("La suma de los ratios debe ser mayor a cero.")

    # Cuota base entera y restos fraccionarios
    asignaciones: List[int] = []
    restos: List[Tuple[Decimal, int]] = []  # (resto, indice_original)

    # Si total es negativo, trabajar con magnitud y reasignar signo
    es_negativo = total_centavos < 0
    total_abs = abs(total_centavos)

    total_dec = Decimal(total_abs)
    suma_asignada = 0

    for i, r in enumerate(ratios_dec):
        cuota_exacta = (total_dec * r) / suma_ratios
        parte_entera = int(cuota_exacta)
        fraccion = cuota_exacta - Decimal(parte_entera)

        asignaciones.append(parte_entera)
        restos.append((fraccion, i))
        suma_asignada += parte_entera

    centavos_sobrantes = total_abs - suma_asignada

    # Ordenar por mayor resto descendente; desempate por índice menor
    restos.sort(key=lambda item: (-item[0], item[1]))

    for k in range(centavos_sobrantes):
        idx = restos[k][1]
        asignaciones[idx] += 1

    if es_negativo:
        asignaciones = [-x for x in asignaciones]

    # Verificación de invariante contable estricto
    if sum(asignaciones) != total_centavos:
        raise ErrorMonetario(
            f"Fallo en invariante de distribución: suma({asignaciones})={sum(asignaciones)} != {total_centavos}"
        )

    for a in asignaciones:
        assertValidMoney(a)

    return asignaciones


def formatMoney(centavos: int, include_symbol: bool = True, currency: str = "ARS") -> str:
    """Formatea centavos enteros a una representación legible sin usar punto flotante.

    Ejemplo:
    123456 -> "$ 1.234,56"
    -5000 -> "-$ 50,00"
    """
    assertValidMoney(centavos)

    es_negativo = centavos < 0
    c_abs = abs(centavos)

    parte_entera = c_abs // 100
    parte_decimal = c_abs % 100

    # Formatear parte entera con separador de miles '.'
    str_entera = f"{parte_entera:,}".replace(",", ".")
    str_decimal = f"{parte_decimal:02d}"

    signo = "-" if es_negativo else ""
    monto_formateado = f"{signo}{str_entera},{str_decimal}"

    if include_symbol:
        if currency == "ARS":
            if es_negativo:
                return f"-${str_entera},{str_decimal}"
            return f"${str_entera},{str_decimal}"
        return f"{monto_formateado} {currency}"

    return monto_formateado
