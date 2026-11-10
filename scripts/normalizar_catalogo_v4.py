#!/usr/bin/env python3
"""Script de migración, normalización de categorías y corrección ortográfica — GenaroPOS V4.

Conecta a la base de datos SQLite local (`genaro-pos.db` por defecto), analiza las
categorías actuales y los nombres de la tabla `productos`, aplica corrección ortográfica
(Title Case, tildes de drugstore y estandarización de unidades de medida al final),
mapea las categorías a las 10 categorías canónicas (+ 'Otros' como fallback) y ejecuta
el UPDATE transaccional tras confirmación en consola o de forma directa con `--force`.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import sqlite3
import sys
from typing import Dict, List, Tuple
import unicodedata

DEFAULT_DB_PATH = "genaro-pos.db"

CATEGORIAS_CANONICAS: Tuple[str, ...] = (
    "Golosinas y Chocolates",
    "Galletitas y Alfajores",
    "Snacks y Salados",
    "Bebidas e Infusiones",
    "Bebidas con Alcohol",
    "Cigarrillos y Tabaquería",
    "Panadería y Fiambrería",
    "Almacén y Lácteos",
    "Librería y Regalería",
    "Higiene y Cuidado Personal",
    "Otros",
)

# Diccionario de reemplazos ortográficos exactos (clave en minúsculas sin tilde -> forma canónica con tilde)
DICCIONARIO_ORTOGRAFICO: Dict[str, str] = {
    # Requeridos explícitamente
    "mani": "Maní",
    "cafe": "Café",
    "clasico": "Clásico",
    "clasica": "Clásica",
    "clasicos": "Clásicos",
    "clasicas": "Clásicas",
    "limon": "Limón",
    "jamon": "Jamón",
    "azucar": "Azúcar",
    "pure": "Puré",
    "plastico": "Plástico",
    "plastica": "Plástica",
    "plasticos": "Plásticos",
    "plasticas": "Plásticas",
    "te": "Té",
    "maiz": "Maíz",
    "comun": "Común",
    "salame": "Salame",
    "sandwich": "Sándwich",
    "sandwiches": "Sándwiches",
    "sanguche": "Sándwich",
    "sanguches": "Sándwiches",
    "lacteo": "Lácteo",
    "lacteos": "Lácteos",
    "rapido": "Rápido",
    "rapida": "Rápida",
    "magico": "Mágico",
    "magica": "Mágica",
    "bombom": "Bombón",
    "bombon": "Bombón",
    "bombones": "Bombones",
    "alfajor": "Alfajor",
    # Términos frecuentes adicionales del catálogo de drugstore
    "jabon": "Jabón",
    "jabo": "Jabón",
    "algodon": "Algodón",
    "atun": "Atún",
    "turron": "Turrón",
    "chupetin": "Chupetín",
    "budin": "Budín",
    "sifon": "Sifón",
    "carbon": "Carbón",
    "anana": "Ananá",
    "instantaneo": "Instantáneo",
    "instantanea": "Instantánea",
    "liquido": "Líquido",
    "liquida": "Líquida",
    "acido": "Ácido",
    "acida": "Ácida",
    "acidos": "Ácidos",
    "acidas": "Ácidas",
    "impresion": "Impresión",
    "impresiones": "Impresiones",
    "lapiz": "Lápiz",
    "lapices": "Lápices",
    "boligrafo": "Bolígrafo",
    "fotografico": "Fotográfico",
    "fotografica": "Fotográfica",
    "pizarron": "Pizarrón",
    "exito": "Éxito",
    "lampara": "Lámpara",
    "tramite": "Trámite",
    "hidratacion": "Hidratación",
    "restauracion": "Restauración",
    "diversion": "Diversión",
    "vocacion": "Vocación",
    "rustico": "Rústico",
    "rustica": "Rústica",
    "rusticos": "Rústicos",
    "rusticas": "Rústicas",
    "celofan": "Celofán",
    "legitimo": "Legítimo",
    "colon": "Colón",
    "pajaro": "Pájaro",
    "guaymallen": "Guaymallén",
    "tatin": "Tatín",
    "aguila": "Águila",
    "danica": "Dánica",
    "ayudin": "Ayudín",
    "querubin": "Querubín",
    "arlistan": "Arlistán",
    "velez": "Vélez",
    "maria": "María",
    "julian": "Julián",
    "tia": "Tía",
    "mana": "Maná",
    "dia": "Día",
    "rio": "Río",
    "cosquin": "Cosquín",
    "bahia": "Bahía",
}

ACRONYMS_PRESERVE: Dict[str, str] = {
    "a4": "A4",
    "sube": "SUBE",
    "led": "LED",
    "b&n": "B&N",
    "b/n": "B/N",
    "byn": "ByN",
    "3d": "3D",
    "h2oh": "H2Oh",
    "km": "KM",
    "sys": "SyS",
    "pp": "PP",
    "jl": "JL",
    "bc": "BC",
    "pvc": "PVC",
    "drf": "DRF",
    "ii": "II",
    "iii": "III",
}

MAPA_UNIDADES: Dict[str, str] = {
    "ml": "ml",
    "cc": "cc",
    "g": "g",
    "gr": "g",
    "grs": "g",
    "kg": "kg",
    "kgr": "kg",
    "kgrs": "kg",
    "l": "l",
    "lt": "l",
    "lts": "l",
    "mg": "mg",
    "mm": "mm",
}

# Diccionario de mapeo directo de categorías antiguas normalizadas (sin tildes, minúsculas)
MAPA_CATEGORIAS_ANTIGUAS: Dict[str, str] = {
    # 1. Golosinas y Chocolates
    "golosina": "Golosinas y Chocolates",
    "golosinas": "Golosinas y Chocolates",
    "chocolate": "Golosinas y Chocolates",
    "chocolates": "Golosinas y Chocolates",
    "caramelos": "Golosinas y Chocolates",
    "chicles": "Golosinas y Chocolates",
    "confiteria": "Golosinas y Chocolates",
    "dulces": "Golosinas y Chocolates",
    "golosinas y chocolates": "Golosinas y Chocolates",
    # 2. Galletitas y Alfajores
    "galletita": "Galletitas y Alfajores",
    "galletitas": "Galletitas y Alfajores",
    "galletas": "Galletitas y Alfajores",
    "alfajor": "Galletitas y Alfajores",
    "alfajores": "Galletitas y Alfajores",
    "obleas": "Galletitas y Alfajores",
    "budines": "Galletitas y Alfajores",
    "galletitas y alfajores": "Galletitas y Alfajores",
    # 3. Snacks y Salados
    "snack": "Snacks y Salados",
    "snacks": "Snacks y Salados",
    "salados": "Snacks y Salados",
    "copetin": "Snacks y Salados",
    "papas fritas": "Snacks y Salados",
    "mani": "Snacks y Salados",
    "snacks y salados": "Snacks y Salados",
    # 4. Bebidas e Infusiones
    "bebida": "Bebidas e Infusiones",
    "bebidas": "Bebidas e Infusiones",
    "bebidas sin alcohol": "Bebidas e Infusiones",
    "gaseosa": "Bebidas e Infusiones",
    "gaseosas": "Bebidas e Infusiones",
    "aguas": "Bebidas e Infusiones",
    "jugos": "Bebidas e Infusiones",
    "energizantes": "Bebidas e Infusiones",
    "infusiones": "Bebidas e Infusiones",
    "yerba": "Bebidas e Infusiones",
    "cafe": "Bebidas e Infusiones",
    "te": "Bebidas e Infusiones",
    "bebidas e infusiones": "Bebidas e Infusiones",
    # 5. Bebidas con Alcohol
    "alcohol": "Bebidas con Alcohol",
    "bebidas con alcohol": "Bebidas con Alcohol",
    "bebidas alcoholicas": "Bebidas con Alcohol",
    "cerveza": "Bebidas con Alcohol",
    "cervezas": "Bebidas con Alcohol",
    "vino": "Bebidas con Alcohol",
    "vinos": "Bebidas con Alcohol",
    "aperitivos": "Bebidas con Alcohol",
    "licores": "Bebidas con Alcohol",
    # 6. Cigarrillos y Tabaquería
    "cigarrillo": "Cigarrillos y Tabaquería",
    "cigarrillos": "Cigarrillos y Tabaquería",
    "tabaco": "Cigarrillos y Tabaquería",
    "tabaqueria": "Cigarrillos y Tabaquería",
    "encendedores": "Cigarrillos y Tabaquería",
    "cigarrillos y tabaqueria": "Cigarrillos y Tabaquería",
    # 7. Panadería y Fiambrería
    "panaderia": "Panadería y Fiambrería",
    "fiambreria": "Panadería y Fiambrería",
    "fiambres": "Panadería y Fiambrería",
    "fiambre": "Panadería y Fiambrería",
    "panificados": "Panadería y Fiambrería",
    "sandwiches": "Panadería y Fiambrería",
    "panaderia y fiambreria": "Panadería y Fiambrería",
    # 8. Almacén y Lácteos
    "almacen": "Almacén y Lácteos",
    "comestibles": "Almacén y Lácteos",
    "lacteo": "Almacén y Lácteos",
    "lacteos": "Almacén y Lácteos",
    "heladera": "Almacén y Lácteos",
    "helados": "Almacén y Lácteos",
    "congelados": "Almacén y Lácteos",
    "almacen y lacteos": "Almacén y Lácteos",
    # 9. Librería y Regalería
    "libreria": "Librería y Regalería",
    "regaleria": "Librería y Regalería",
    "utiles": "Librería y Regalería",
    "papeleria": "Librería y Regalería",
    "impresiones": "Librería y Regalería",
    "fotocopias": "Librería y Regalería",
    "servicios": "Librería y Regalería",
    "jugueteria": "Librería y Regalería",
    "pilas": "Librería y Regalería",
    "libreria y regaleria": "Librería y Regalería",
    # 10. Higiene y Cuidado Personal
    "higiene": "Higiene y Cuidado Personal",
    "cuidado personal": "Higiene y Cuidado Personal",
    "perfumeria": "Higiene y Cuidado Personal",
    "limpieza": "Higiene y Cuidado Personal",
    "farmacia": "Higiene y Cuidado Personal",
    "pastillas": "Higiene y Cuidado Personal",
    "higiene y cuidado personal": "Higiene y Cuidado Personal",
    # 11. Fallback Otros
    "otro": "Otros",
    "otros": "Otros",
    "varios": "Otros",
    "general": "Otros",
    "sin categoria": "Otros",
}

# Reglas heurísticas por palabras clave (evaluadas en orden de especificidad)
HEURISTICA_KEYWORDS: List[Tuple[Tuple[str, ...], str]] = [
    (
        (
            "vinagre", "aceite", "aceituna", "arroz", "arveja", "avena", "azucar",
            "bicarbonato", "caballa", "caldo", "choclo", "fideo", "harina", "huevo",
            "jardinera", "ketchup", "leche", "lenteja", "maiz", "manteca", "mayonesa",
            "mermelada", "miel", "mostaza", "picadillo", "paté", "pate", "polenta",
            "pure", "sal ", "salsa", "sopa", "tapa para empanada", "tapas para empanadas",
            "tomate", "yogur", "yogurt", "atun", "cereal", "almacen",
        ),
        "Almacén y Lácteos",
    ),
    (
        (
            "alcohol puro", "alcohol etilico", "alcohol en gel", "jabon", "jabo ",
            "shampoo", "acondicionador", "desodorante", "antitranspirante",
            "papel higienico", "servilleta", "rollo de cocina", "toallita", "kotex",
            "always", "calipso", "doncella", "ladysoft", "panal", "pañal", "algodon",
            "cepillo dental", "colgate", "odol", "kolynos", "afeitadora", "prestobarba",
            "gillette", "preservativo", "prime", "tulipan", "lavandina", "lavavajillas",
            "detergente", "limpiador", "lustra muebles", "suavizante", "insecticida",
            "repelente", "tableta raid", "fuyi", "selton", "esponja", "trapo",
            "guantes virulana", "gel lord", "actron", "alikal", "amoxicilina",
            "aspirineta", "bayaspirina", "bio-grip", "buscapina", "cafiaspirina",
            "cefalexina", "ibuprofeno", "keterolac", "loperamida", "loratadina",
            "migral", "tafirol", "uvasal", "sertal", "paracetamol", "diclofenac",
            "higiene",
        ),
        "Higiene y Cuidado Personal",
    ),
    (
        (
            "alfajor", "galletita", "galleta", "oblea", "pepas", "bizcochito",
            "bizcochos", "formis", "oreo", "mana", "traviata", "chocman",
            "merengadas", "mellizas", "criollitas", "pitusas", "sonrisas",
            "surtidas", "vainillas", "magdalena", "mini donuts", "budin",
        ),
        "Galletitas y Alfajores",
    ),
    (
        (
            "cerveza", "vino", "fernet", "fernuco", "gancia", "vodka", "gin",
            "sidra", "champagne", "whisky", "smirnoff", "brahma", "quilmes",
            "salta", "andes origen", "amstel", "budweiser", "corona", "heineken",
            "imperial", "miller", "schneider", "stella artois", "anana fizz",
            "canciller", "viñas de balbo", "tres plumas", "dr lemon",
        ),
        "Bebidas con Alcohol",
    ),
    (
        (
            "cigarrillo", "philip morris", "marlboro", "lucky strike", "chesterfield",
            "red point", "hills", "master", "dolchester", "camel", "parliament",
            "tabaco", "papelillo", "filtro", "encendedor", "hoja de coca", "bica",
        ),
        "Cigarrillos y Tabaquería",
    ),
    (
        (
            "papas fritas", "chizito", "palitos", "doritos", "cheetos", "3d queso",
            "lays", "pehuamar", "krachitos", "kesitas", "mani", "snack", "salado",
            "tutuca", "puflitos", "garrapiñada",
        ),
        "Snacks y Salados",
    ),
    (
        (
            "chocolate", "bon o bon", "block", "cofler", "milka", "tofi", "caramelo",
            "chupetin", "chicle", "bazooka", "beldent", "bubbaloo", "topline",
            "gomita", "mogul", "yummy", "sugus", "flynn paff", "rocklets",
            "turrocklets", "kinder", "marroc", "turron", "mantecol", "bombon",
            "bombom", "menthoplus", "halls", "pastilla", "drf", "malvavisco",
            "bulldog regaliz", "pico dulce", "golosina",
        ),
        "Golosinas y Chocolates",
    ),
    (
        (
            "gaseosa", "coca cola", "coca", "pepsi", "sprite", "fanta", "manaos",
            "pritty", "paso de los toros", "schweppes", "7up", "seven up", "mirinda",
            "torasso", "talca", "brio", "361", "agua", "soda", "h2oh", "aquarius",
            "cepita", "baggio", "ades", "chocolatada", "cacao", "speed", "monster",
            "red bull", "gatorade", "powerade", "suerox", "bio balance", "marinaro",
            "jugo", "yerba", "cafe", "mate cocido", "te",
        ),
        "Bebidas e Infusiones",
    ),
    (
        (
            "pan ", "mignon", "tortilla", "factura", "medialuna", "jamon", "queso",
            "salame", "mortadela", "fiambre", "paleta", "sandwich", "pebete",
        ),
        "Panadería y Fiambrería",
    ),
    (
        (
            "fotocopia", "impresion", "anillado", "plastificado", "lapicera",
            "birome", "lapiz", "lapices", "cuaderno", "libreta", "carpeta", "hoja",
            "aro", "borrador", "cartulina", "tiza", "goma", "regla", "mapa",
            "marcador", "resaltador", "sobre", "cinta", "plasticola", "voligoma",
            "pegamento", "tijera", "sacapuntas", "corrector", "afiche", "papel",
            "regalo", "juguete", "bombucha", "globo", "pila", "naipes", "libreria",
        ),
        "Librería y Regalería",
    ),
]


def _quitar_tildes(texto: str) -> str:
    """Convierte a minúsculas y elimina diacríticos para comparación robusta."""
    limpio = texto.strip().lower()
    normalizado = unicodedata.normalize("NFD", limpio)
    return "".join(c for c in normalizado if unicodedata.category(c) != "Mn")


def _coincide_keyword(texto_norm: str, kw: str) -> bool:
    """Verifica coincidencia de palabra clave evitando falsos positivos por subcadena corta."""
    kw_clean = kw.strip()
    if len(kw_clean) <= 4:
        return bool(re.search(rf"\b{re.escape(kw_clean)}\b", texto_norm))
    return kw_clean in texto_norm


def _formatear_subpalabra(sub: str) -> str:
    """Capitaliza y aplica corrección ortográfica a una palabra individual."""
    if not sub:
        return sub

    # Preservar prefijo/sufijo de puntuación como paréntesis
    m = re.match(r"^([^A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]*)([A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9.,&'-]+)([^A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]*)$", sub)
    if not m:
        return sub
    prefijo, nucleo, sufijo = m.groups()

    nucleo_lower = nucleo.lower()
    nucleo_sin_tilde = _quitar_tildes(nucleo)

    # 1. Multiplicadores tipo x1, x12, x12u, x20
    if re.match(r"^x\d+[a-z]*$", nucleo_lower):
        return f"{prefijo}{nucleo_lower}{sufijo}"

    # 2. Cantidades con unidad pegada (ej. 500ml, 70gr, 1,5lts, 50x20gr)
    m_unit = re.match(r"^(\d+(?:[.,x]\d+)?)(ml|cc|g|gr|grs|kg|kgr|kgrs|l|lt|lts|mg|mm|u)\.?$", nucleo_lower)
    if m_unit:
        num_part, unit_part = m_unit.groups()
        unit_canon = MAPA_UNIDADES.get(unit_part, unit_part)
        return f"{prefijo}{num_part}{unit_canon}{sufijo}"

    # 3. Acrónimos preservados
    if nucleo_lower in ACRONYMS_PRESERVE:
        return f"{prefijo}{ACRONYMS_PRESERVE[nucleo_lower]}{sufijo}"

    # 4. Diccionario ortográfico exacto (tildes de drugstore)
    if nucleo_sin_tilde in DICCIONARIO_ORTOGRAFICO:
        return f"{prefijo}{DICCIONARIO_ORTOGRAFICO[nucleo_sin_tilde]}{sufijo}"

    # 5. Si la palabra ya tiene tilde en el original, preservar la tilde y aplicar Title Case
    if any(c in nucleo_lower for c in "áéíóúüñ"):
        capitalizada = nucleo_lower[0].upper() + nucleo_lower[1:]
        return f"{prefijo}{capitalizada}{sufijo}"

    # 6. Title Case estándar (primera letra en mayúscula)
    capitalizada = nucleo_lower[0].upper() + nucleo_lower[1:]
    return f"{prefijo}{capitalizada}{sufijo}"


def formatear_nombre_producto(nombre: str) -> str:
    """Aplica Title Case, corrección de tildes y normalización de unidades de medida."""
    texto = re.sub(r"\s+", " ", (nombre or "").strip())
    if not texto:
        return ""

    # 1. Unir unidades separadas por espacio al número precedente (ej. "500 ML" -> "500ml", "70 G" -> "70g", "3 lts" -> "3l")
    def _reemplazar_unidad_espaciada(match: re.Match) -> str:
        numero = match.group(1)
        unidad_raw = match.group(2).lower().rstrip(".")
        unidad_canon = MAPA_UNIDADES.get(unidad_raw, unidad_raw)
        return f"{numero}{unidad_canon}"

    texto = re.sub(
        r"\b(\d+(?:[.,]\d+)?)\s+(ml|cc|g|gr|grs|kg|kgr|kgrs|l|lt|lts|mg|mm)\.?\b",
        _reemplazar_unidad_espaciada,
        texto,
        flags=re.IGNORECASE,
    )

    # 2. Procesar token por token (soportando palabras separadas por '/' o '-')
    palabras = texto.split(" ")
    palabras_formateadas: List[str] = []

    for palabra in palabras:
        if "/" in palabra and palabra.lower() not in ("c/elast.", "s/alcohol", "c/alcohol", "b/n"):
            partes = [_formatear_subpalabra(p) for p in palabra.split("/")]
            palabras_formateadas.append("/".join(partes))
        elif "-" in palabra:
            partes = [_formatear_subpalabra(p) for p in palabra.split("-")]
            palabras_formateadas.append("-".join(partes))
        else:
            palabras_formateadas.append(_formatear_subpalabra(palabra))

    resultado = " ".join(palabras_formateadas)

    # 3. Asegurar que cualquier unidad final quede en minúscula estándar pegada al número
    resultado = re.sub(
        r"(\d+(?:[.,]\d+)?)\s*(ml|cc|g|gr|grs|kg|kgr|kgrs|l|lt|lts|mg|mm)\.?$",
        lambda m: f"{m.group(1)}{MAPA_UNIDADES.get(m.group(2).lower(), m.group(2).lower())}",
        resultado,
        flags=re.IGNORECASE,
    )

    return resultado


PREFIJOS_TIPO_PRODUCTO: List[Tuple[Tuple[str, ...], str]] = [
    (
        (
            "alfajor", "galleta", "galletita", "mini galletita", "mini donuts",
            "oblea", "vainilla", "magdalena", "budin", "surtidas", "chocman",
            "traviata", "cerealitas", "bizcochos de grasa", "bizcochos don satur",
        ),
        "Galletitas y Alfajores",
    ),
    (
        (
            "chocolate", "turron", "turrocklets", "caramelo", "chicle", "chupetin",
            "gomita", "bombon", "bombom", "bon o bon", "mantecol", "menthoplus",
            "topline", "malvavisco", "bulldog", "latita de chicle", "botellita",
        ),
        "Golosinas y Chocolates",
    ),
    (
        (
            "papas fritas", "mani", "lays", "cheetos", "3d", "doritos", "chizito",
            "palitos", "kesitas", "tutuca", "puflitos", "garrapiñada",
        ),
        "Snacks y Salados",
    ),
    (
        (
            "cerveza", "vino", "viñas", "vodka", "fernet", "fernuco", "gancia",
            "anana fizz", "canciller", "tres plumas", "brahma", "budweiser",
            "quilmes", "stella artois", "amstel", "andes", "salta", "heineken",
            "imperial", "miller", "corona", "schneider", "dr lemon",
        ),
        "Bebidas con Alcohol",
    ),
    (
        (
            "agua", "soda", "gaseosa", "jugo", "coca", "sprite", "fanta", "pepsi",
            "manaos", "torasso", "talca", "brio", "361", "aquarius", "cepita",
            "baggio", "ades", "chocolatada", "cacao", "speed", "gatorade",
            "powerade", "suerox", "h2oh", "bio balance", "marinaro", "yerba",
            "cafe", "mate cocido", "te ",
        ),
        "Bebidas e Infusiones",
    ),
    (
        (
            "cigarrillo", "hills", "philip", "marlboro", "lucky", "red point",
            "chesterfield", "master", "dolchester", "camel", "tabaco",
            "encendedor", "hoja de coca", "bica",
        ),
        "Cigarrillos y Tabaquería",
    ),
    (
        (
            "mignon", "pan ", "jamon", "queso", "salame", "mortadela", "fiambre",
            "paleta", "sandwich", "pebete", "bizcochos rombo", "tortilla",
            "factura", "medialuna",
        ),
        "Panadería y Fiambrería",
    ),
    (
        (
            "aceite", "aceituna", "arroz", "arveja", "atun", "avena", "azucar",
            "bicarbonato", "caballa", "caldo", "cereal", "choclo", "fideo",
            "gelatina", "harina", "huevo", "jardinera", "ketchup", "leche",
            "lenteja", "maiz", "manteca", "mayonesa", "mermelada", "miel",
            "mostaza", "picadillo", "pate", "polenta", "pure", "sal ", "salsa",
            "sopa", "tapas para empanadas", "tomate", "vinagre", "yogur",
        ),
        "Almacén y Lácteos",
    ),
    (
        (
            "acondicionador", "actron", "afeitadora", "alcohol puro", "algodon",
            "alikal", "amoxicilina", "antitranspirante", "aspirineta",
            "bayaspirina", "bio-grip", "buscapina", "cafiaspirina", "cefalexina",
            "cepillo", "colgate", "desodorante", "esponja", "gel lord", "guantes",
            "ibuprofeno", "insecticida", "jabon", "jabo ", "keterolac",
            "lavandina", "lavavajillas", "limpiador", "loperamida", "loratadina",
            "lustra muebles", "migral", "odol", "papel higienico", "pañal",
            "preservativo", "prestobarba", "repelente", "rollo de cocina",
            "servilleta", "shampoo", "suavizante", "tableta raid",
            "tableta repelente", "tafirol", "toallita", "trapo", "uvasal",
        ),
        "Higiene y Cuidado Personal",
    ),
    (
        (
            "aro", "borrador", "caja de tiza", "carpeta", "cartulina", "cinta",
            "corrector", "cuaderno", "fotocopia", "goma", "hoja", "impresion",
            "juego de regla", "lapicera", "lapices", "lapiz", "libreta", "mapa",
            "marcador", "papel", "pegamento", "pila", "plasticola", "regla",
            "resaltador", "sacapuntas", "sobre", "tarjeta", "tijera", "tiza",
            "tramite", "voligoma", "bombucha",
        ),
        "Librería y Regalería",
    ),
]


def sugerir_categoria_canonica(categoria_actual: str, nombre_producto: str = "") -> str:
    """Determina la categoría canónica correspondiente usando heurística de prefijo, diccionario y nombre."""
    cat_limpia = (categoria_actual or "").strip()
    cat_norm = _quitar_tildes(cat_limpia)
    nom_norm = _quitar_tildes(nombre_producto or "")

    # 0. Clasificación prioritaria por el [TIPO] al inicio del nombre del producto
    for prefijos, canonica in PREFIJOS_TIPO_PRODUCTO:
        if any(nom_norm.startswith(pref) for pref in prefijos):
            return canonica

    if cat_limpia in CATEGORIAS_CANONICAS and cat_limpia != "Otros":
        return cat_limpia

    # 1. Si el nombre del producto da una señal inequívoca
    for keywords, canonica in HEURISTICA_KEYWORDS:
        if any(_coincide_keyword(nom_norm, kw) for kw in keywords):
            if cat_norm in ("", "otro", "otros", "varios", "general", "sin categoria") or canonica in (
                "Almacén y Lácteos",
                "Higiene y Cuidado Personal",
                "Galletitas y Alfajores",
                "Bebidas con Alcohol",
                "Cigarrillos y Tabaquería",
                "Snacks y Salados",
            ):
                return canonica

    # 2. Mapeo directo por diccionario de categoría antigua
    if cat_norm in MAPA_CATEGORIAS_ANTIGUAS:
        mapeada = MAPA_CATEGORIAS_ANTIGUAS[cat_norm]
        if mapeada != "Otros":
            return mapeada

    # 3. Coincidencia parcial sobre el texto de la categoría antigua o el nombre del producto
    texto_combinado = f"{cat_norm} {nom_norm}"
    for keywords, canonica in HEURISTICA_KEYWORDS:
        if any(_coincide_keyword(texto_combinado, kw) for kw in keywords):
            return canonica

    return "Otros"


def normalizar_catalogo(db_path: Path, dry_run: bool = False, force: bool = False) -> int:
    """Ejecuta el análisis y actualización ortográfica y de categorías del catálogo."""
    if not db_path.exists():
        print(f"❌ Error: No se encontró el archivo de base de datos en: {db_path}")
        return 1

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='productos';"
        )
        if not cur.fetchone():
            print(f"❌ Error: La base de datos '{db_path}' no contiene la tabla 'productos'.")
            return 1

        filas = conn.execute(
            "SELECT id, nombre, categoria FROM productos ORDER BY id ASC;"
        ).fetchall()

        total = len(filas)
        print("=" * 78)
        print(" 📦 NORMALIZACIÓN ORTOGRÁFICA Y DE CATEGORÍAS — GENAROPOS V4")
        print(f" Base de datos: {db_path.resolve()}")
        print(f" Total de productos analizados: {total}")
        print("=" * 78)

        if total == 0:
            print("ℹ️  No hay productos registrados en la tabla 'productos'.")
            return 0

        cambios: List[Tuple[int, str, str, str, str]] = []
        cambios_ortograficos: List[Tuple[int, str, str]] = []
        transiciones_cat: Counter = Counter()

        for prod_id, nombre_actual, cat_actual in filas:
            nom_str = str(nombre_actual or "").strip()
            cat_str = str(cat_actual or "").strip()

            nuevo_nom = formatear_nombre_producto(nom_str)
            nueva_cat = sugerir_categoria_canonica(cat_str, nuevo_nom)

            if nom_str != nuevo_nom:
                cambios_ortograficos.append((int(prod_id), nom_str, nuevo_nom))

            if cat_str != nueva_cat:
                transiciones_cat[(cat_str or "(vacía)", nueva_cat)] += 1

            if nom_str != nuevo_nom or cat_str != nueva_cat:
                cambios.append((int(prod_id), nom_str, nuevo_nom, cat_str or "(vacía)", nueva_cat))

        if not cambios:
            print("✅ Todos los productos ya cuentan con nombres y categorías normalizados.")
            return 0

        print(f"\n🔍 Total de productos a actualizar: {len(cambios)}")
        print(f"   • Con corrección ortográfica / formato de nombre: {len(cambios_ortograficos)}")
        print(f"   • Con reclasificación de categoría canónica:     {sum(transiciones_cat.values())}\n")

        if transiciones_cat:
            print("Resumen de mapeo de categorías (Categoría Antigua -> Categoría Canónica):")
            for (antigua, nueva), cant in sorted(transiciones_cat.items(), key=lambda x: (-x[1], x[0])):
                print(f"  • {antigua!r:25} ➔  {nueva!r:28} ({cant} prod.)")

        if cambios_ortograficos:
            print("\nMuestra de productos corregidos ortográficamente (Antes -> Después):")
            for pid, ant_nom, nue_nom in cambios_ortograficos[:30]:
                print(f"  [#{pid:4d}] {ant_nom:42} ➔  {nue_nom}")
            if len(cambios_ortograficos) > 30:
                print(f"  ... y {len(cambios_ortograficos) - 30} nombres corregidos más.")

        if dry_run:
            print("\nℹ️  Modo --dry-run activo: no se realizaron cambios en la base de datos.")
            return 0

        if not force:
            print("\n" + "-" * 78)
            respuesta = input(
                f"¿Confirmás aplicar el UPDATE de {len(cambios)} productos en '{db_path.name}'? [s/N]: "
            ).strip().lower()

            if respuesta not in ("s", "si", "sí", "y", "yes"):
                print("🚫 Operación cancelada por el usuario. No se modificó la base de datos.")
                return 0

        fecha_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("BEGIN IMMEDIATE;")
        for pid, _ant_nom, nuevo_nom, _ant_cat, nueva_cat in cambios:
            conn.execute(
                "UPDATE productos SET nombre = ?, categoria = ?, fecha_actualizacion = ? WHERE id = ?;",
                (nuevo_nom, nueva_cat, fecha_utc, pid),
            )
        conn.commit()
        print(
            f"\n✅ ¡Éxito! Se actualizaron {len(cambios)} productos "
            f"({len(cambios_ortograficos)} nombres perfeccionados) en '{db_path.name}'."
        )
        return 0

    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"❌ Error durante la normalización: {exc}")
        return 1
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Normaliza ortografía de nombres y categorías de productos de GenaroPOS V4."
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("COMMERCIAL_DB_PATH", DEFAULT_DB_PATH),
        help="Ruta al archivo SQLite de base de datos (por defecto: genaro-pos.db).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Solo muestra las sugerencias en consola sin realizar cambios.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ejecuta el UPDATE directamente sin solicitar confirmación interactiva.",
    )
    args = parser.parse_args()
    return normalizar_catalogo(Path(args.db), dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
