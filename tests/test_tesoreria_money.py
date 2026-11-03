"""Pruebas de la Biblioteca Monetaria — MONEY-001 a MONEY-012.

Verificación rigurosa de LAB-002:
- Manejo estricto de centavos enteros (INTEGER).
- Inmunidad total a errores de punto flotante de IEEE 754.
- Distribución exacta sin pérdida de centavos.
"""

from decimal import Decimal
import pytest
from tesoreria.money import (
    ErrorMonetario,
    addMoney,
    allocateMoney,
    assertValidMoney,
    compareMoney,
    formatMoney,
    parseMoney,
    subtractMoney,
    MAX_SAFE_MONEY,
    MIN_SAFE_MONEY,
)


def test_money_001_parse_money_cadenas_y_decimales():
    """MONEY-001: parseMoney convierte correctamente cadenas enteras y decimales sin coma flotante."""
    assert parseMoney("100") == 10000
    assert parseMoney("100.50") == 10050
    assert parseMoney("100,50") == 10050
    assert parseMoney("1234.56") == 123456
    assert parseMoney("1.234,56") == 123456
    assert parseMoney("$ 1.234,56") == 123456
    assert parseMoney("0.05") == 5
    assert parseMoney("0,01") == 1
    assert parseMoney(100.50) == 10050
    assert parseMoney(Decimal("100.50")) == 10050
    assert parseMoney({"centavos": 50025}) == 50025


def test_money_002_parse_money_rechazo_valores_invalidos():
    """MONEY-002: parseMoney rechaza strings malformados, valores no numéricos, infinitos o NaN."""
    with pytest.raises(ErrorMonetario):
        parseMoney(None)

    with pytest.raises(ErrorMonetario):
        parseMoney("")

    with pytest.raises(ErrorMonetario):
        parseMoney("abc")

    with pytest.raises(ErrorMonetario):
        parseMoney("12.34.56.78")

    with pytest.raises(ErrorMonetario):
        parseMoney(float("nan"))

    with pytest.raises(ErrorMonetario):
        parseMoney(float("inf"))

    with pytest.raises(ErrorMonetario):
        parseMoney(True)


def test_money_003_assert_valid_money():
    """MONEY-003: assertValidMoney valida que el monto sea un número entero (INTEGER) seguro."""
    assertValidMoney(0)
    assertValidMoney(10000)
    assertValidMoney(-500)
    assertValidMoney(MAX_SAFE_MONEY)
    assertValidMoney(MIN_SAFE_MONEY)

    # Debe rechazar tipos que no sean int puro
    with pytest.raises(ErrorMonetario):
        assertValidMoney(10.5)

    with pytest.raises(ErrorMonetario):
        assertValidMoney("100")

    with pytest.raises(ErrorMonetario):
        assertValidMoney(False)

    with pytest.raises(ErrorMonetario):
        assertValidMoney(True)

    with pytest.raises(ErrorMonetario):
        assertValidMoney(MAX_SAFE_MONEY + 1)

    with pytest.raises(ErrorMonetario):
        assertValidMoney(MIN_SAFE_MONEY - 1)


def test_money_004_add_money():
    """MONEY-004: addMoney suma exactamente centavos enteros sin desbordamiento ni imprecisión."""
    assert addMoney(10050, 20025) == 30075
    assert addMoney(0, 500) == 500
    assert addMoney(-100, 100) == 0
    assert addMoney(-100, -200) == -300

    with pytest.raises(ErrorMonetario):
        addMoney(MAX_SAFE_MONEY, 1)


def test_money_005_subtract_money():
    """MONEY-005: subtractMoney resta exactamente centavos enteros."""
    assert subtractMoney(30075, 10050) == 20025
    assert subtractMoney(10000, 10000) == 0
    assert subtractMoney(500, 1000) == -500
    assert subtractMoney(-200, 300) == -500

    with pytest.raises(ErrorMonetario):
        subtractMoney(MIN_SAFE_MONEY, 1)


def test_money_006_compare_money():
    """MONEY-006: compareMoney compara montos retornando -1, 0, 1 correctamente."""
    assert compareMoney(10000, 20000) == -1
    assert compareMoney(20000, 10000) == 1
    assert compareMoney(15000, 15000) == 0
    assert compareMoney(-500, 0) == -1
    assert compareMoney(0, -500) == 1


def test_money_007_allocate_money_equitativo_con_resto():
    """MONEY-007: allocateMoney distribuye montos indivisibles preservando la suma exacta."""
    # $100.00 (10000 centavos) entre 3 partes iguales
    # 10000 // 3 = 3333 centavos con 1 centavo de resto
    resultado = allocateMoney(10000, [1, 1, 1])
    assert resultado == [3334, 3333, 3333]
    assert sum(resultado) == 10000


def test_money_008_allocate_money_ratios_arbitrarios():
    """MONEY-008: allocateMoney distribuye montos según ratios arbitrarios sin perder centavos."""
    # 10001 centavos con ratios [1, 2, 1] (suma de partes = 4)
    # 10001 * 1/4 = 2500.25 (resto 0.25)
    # 10001 * 2/4 = 5000.50 (resto 0.50 -> recibe el centavo sobrante)
    # 10001 * 1/4 = 2500.25 (resto 0.25)
    resultado = allocateMoney(10001, [1, 2, 1])
    assert sum(resultado) == 10001
    assert resultado == [2500, 5001, 2500]

    # Distribución en partes con decimales de peso
    resultado2 = allocateMoney(7000, [0.33, 0.67])
    assert sum(resultado2) == 7000


def test_money_009_format_money():
    """MONEY-009: formatMoney formatea correctamente centavos a representación contable."""
    assert formatMoney(123456) == "$1.234,56"
    assert formatMoney(0) == "$0,00"
    assert formatMoney(5) == "$0,05"
    assert formatMoney(50) == "$0,50"
    assert formatMoney(100000000) == "$1.000.000,00"
    assert formatMoney(-5000) == "-$50,00"
    assert formatMoney(123456, include_symbol=False) == "1.234,56"


def test_money_010_inmunidad_flotante_ieee754():
    """MONEY-010: Inmunidad total contra el error clásico 0.1 + 0.2 != 0.3 de IEEE 754."""
    # En IEEE 754: 0.1 + 0.2 = 0.30000000000000004
    # En nuestra biblioteca de centavos enteros:
    monto_a = parseMoney("0.10")  # 10 centavos
    monto_b = parseMoney("0.20")  # 20 centavos
    monto_c = parseMoney("0.30")  # 30 centavos

    assert addMoney(monto_a, monto_b) == monto_c
    assert compareMoney(addMoney(monto_a, monto_b), monto_c) == 0

    # Caso típico de redondeo de punto flotante: 1.14 * 100 = 113.99999999999999
    assert parseMoney("1.14") == 114
    assert parseMoney(1.14) == 114


def test_money_011_manejo_negativos_y_cero():
    """MONEY-011: Comportamiento correcto y determinista con ceros y montos negativos."""
    assert addMoney(0, 0) == 0
    assert subtractMoney(0, 0) == 0
    assert compareMoney(0, 0) == 0
    assert formatMoney(0) == "$0,00"

    neg_500 = parseMoney("-5.00")
    assert neg_500 == -500
    assert addMoney(neg_500, 500) == 0
    assert subtractMoney(0, neg_500) == 500


def test_money_012_centavos_grandes_seguros():
    """MONEY-012: Operaciones con centavos de magnitud muy alta dentro de límites seguros."""
    # 5 billones de pesos = 500 billones de centavos
    gran_monto_1 = 500_000_000_000_00
    gran_monto_2 = 250_000_000_000_00

    suma = addMoney(gran_monto_1, gran_monto_2)
    assert suma == 750_000_000_000_00
    assert subtractMoney(suma, gran_monto_2) == gran_monto_1
    assert compareMoney(gran_monto_1, gran_monto_2) == 1
