#!/usr/bin/env python3
"""Script de migración y normalización de categorías del catálogo — GenaroPOS V4.

Conecta a la base de datos SQLite local (`genaro-pos.db` por defecto), analiza las
categorías actuales de la tabla `productos`, sugiere el mapeo hacia las 10 categorías
canónicas (+ 'Otros' como fallback) mediante heurística de diccionario y palabras clave,
y aplica el UPDATE transaccional únicamente tras confirmación explícita en consola.
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
        ("alfajor", "galletita", "galleta", "oblea", "pepas", "bizcochito", "formis", "oreo", "mana", "traviat"),
        "Galletitas y Alfajores",
    ),
    (
        ("cerveza", "vino", "fernet", "gancia", "vodka", "gin", "sidra", "champagne", "whisky", "smirnoff", "brahma", "quilmes", "salta", "andes"),
        "Bebidas con Alcohol",
    ),
    (
        ("cigarrillo", "philip morris", "marlboro", "lucky strike", "chesterfield", "red point", "tabaco", "papelillo", "filtro", "encendedor"),
        "Cigarrillos y Tabaquería",
    ),
    (
        ("papas fritas", "chizito", "palitos", "doritos", "cheetos", "lays", "pehuamar", "krachitos", "mani", "snack", "salado"),
        "Snacks y Salados",
    ),
    (
        ("chocolate", "bon o bon", "block", "cofler", "milka", "tofi", "caramelo", "chupetin", "chicle", "beldent", "topline", "gomita", "mogul", "sugus", "flynn paff", "rocklets", "kinder", "marroc", "turron", "golosina"),
        "Golosinas y Chocolates",
    ),
    (
        ("gaseosa", "coca cola", "pepsi", "sprite", "fanta", "manaon", "manaos", "pritty", "paso de los toros", "schweppes", "agua", "aquarius", "cepita", "baggio", "ades", "speed", "monster", "red bull", "gatorade", "powerade", "jugo", "yerba", "cafe", "mate cocido", "te "),
        "Bebidas e Infusiones",
    ),
    (
        ("pan ", "mignon", "bizcocho", "tortilla", "factura", "medialuna", "jamon", "queso", "salame", "mortadela", "fiambre", "paleta", "sandwich", "pebete"),
        "Panadería y Fiambrería",
    ),
    (
        ("leche", "yogur", "manteca", "crema", "dulce de leche", "fideo", "arroz", "aceite", "azucar", "harina", "pure de tomate", "sal ", "mayonesa", "ketchup", "mostaza", "atun", "picadillo", "huevo", "almacen"),
        "Almacén y Lácteos",
    ),
    (
        ("fotocopia", "impresion", "anillado", "plastificado", "lapicera", "birome", "lapiz", "cuaderno", "carpeta", "hoja", "sobre", "cinta", "plasticola", "voligoma", "regalo", "juguete", "pila", "naipes", "libreria"),
        "Librería y Regalería",
    ),
    (
        ("jabon", "shampoo", "acondicionador", "desodorante", "papel higienico", "servilleta", "toallita", "kotex", "always", "panal", "cepillo dental", "colgate", "prestobarba", "gillette", "preservativo", "prime", "tulipan", "lavandina", "detergente", "esponja", "trapo", "higiene"),
        "Higiene y Cuidado Personal",
    ),
]


def _quitar_tildes(texto: str) -> str:
    """Convierte a minúsculas y elimina diacríticos para comparación robusta."""
    limpio = texto.strip().lower()
    normalizado = unicodedata.normalize("NFD", limpio)
    return "".join(c for c in normalizado if unicodedata.category(c) != "Mn")


def _coincide_keyword(texto_norm: str, kw: str) -> bool:
    """Verifica coincidencia de palabra clave evitando falsos positivos por subcadena corta (ej. 'gin' en 'original')."""
    kw_clean = kw.strip()
    if len(kw_clean) <= 4:
        return bool(re.search(rf"\b{re.escape(kw_clean)}\b", texto_norm))
    return kw_clean in texto_norm


def sugerir_categoria_canonica(categoria_actual: str, nombre_producto: str = "") -> str:
    """Determina la categoría canónica correspondiente usando heurística de diccionario y nombre."""
    cat_limpia = (categoria_actual or "").strip()
    if cat_limpia in CATEGORIAS_CANONICAS and cat_limpia != "Otros":
        return cat_limpia

    cat_norm = _quitar_tildes(cat_limpia)
    nom_norm = _quitar_tildes(nombre_producto or "")

    # 1. Si el nombre del producto da una señal inequívoca en rubros que antes estaban mezclados
    #    (ej. un Alfajor que estaba en 'Golosinas', o cuando la categoría es genérica 'Varios'/'Otros')
    for keywords, canonica in HEURISTICA_KEYWORDS:
        if any(_coincide_keyword(nom_norm, kw) for kw in keywords):
            # Si la categoría actual era genérica o si es un caso de separación (ej. Alfajor/Galletita)
            if cat_norm in ("", "otro", "otros", "varios", "general", "sin categoria") or canonica in (
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


def normalizar_catalogo(db_path: Path, dry_run: bool = False) -> int:
    """Ejecuta el análisis y (previa confirmación en consola) la actualización del catálogo."""
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
        print("=" * 72)
        print(" 📦 NORMALIZACIÓN DE CATÁLOGO — GENAROPOS V4")
        print(f" Base de datos: {db_path.resolve()}")
        print(f" Total de productos analizados: {total}")
        print("=" * 72)

        if total == 0:
            print("ℹ️  No hay productos registrados en la tabla 'productos'.")
            return 0

        cambios: List[Tuple[int, str, str, str]] = []
        transiciones: Counter = Counter()

        for prod_id, nombre, cat_actual in filas:
            cat_str = str(cat_actual or "").strip()
            nueva_cat = sugerir_categoria_canonica(cat_str, str(nombre or ""))
            if cat_str != nueva_cat:
                cambios.append((int(prod_id), str(nombre), cat_str or "(vacía)", nueva_cat))
                transiciones[(cat_str or "(vacía)", nueva_cat)] += 1

        if not cambios:
            print("✅ Todos los productos ya cuentan con categorías canónicas normalizadas.")
            return 0

        print(f"\n🔍 Se detectaron {len(cambios)} productos para normalizar:\n")
        print("Resumen de mapeo propuesto (Categoría Antigua -> Categoría Canónica):")
        for (antigua, nueva), cant in sorted(transiciones.items(), key=lambda x: (-x[1], x[0])):
            print(f"  • {antigua!r:25} ➔  {nueva!r:28} ({cant} prod.)")

        print("\nDetalle de los primeros 20 productos a actualizar:")
        for pid, nom, ant, nue in cambios[:20]:
            print(f"  [#{pid:4d}] {nom[:35]:35} | {ant:18} -> {nue}")
        if len(cambios) > 20:
            print(f"  ... y {len(cambios) - 20} productos más.")

        if dry_run:
            print("\nℹ️  Modo --dry-run activo: no se realizaron cambios en la base de datos.")
            return 0

        print("\n" + "-" * 72)
        respuesta = input(
            f"¿Confirmás aplicar el UPDATE de {len(cambios)} productos en '{db_path.name}'? [s/N]: "
        ).strip().lower()

        if respuesta not in ("s", "si", "sí", "y", "yes"):
            print("🚫 Operación cancelada por el usuario. No se modificó la base de datos.")
            return 0

        fecha_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("BEGIN IMMEDIATE;")
        for pid, _nom, _ant, nueva_cat in cambios:
            conn.execute(
                "UPDATE productos SET categoria = ?, fecha_actualizacion = ? WHERE id = ?;",
                (nueva_cat, fecha_utc, pid),
            )
        conn.commit()
        print(f"✅ ¡Éxito! Se normalizaron {len(cambios)} productos en '{db_path.name}'.")
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
        description="Normaliza las categorías de productos de GenaroPOS V4 hacia las 10 categorías canónicas."
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
    args = parser.parse_args()
    return normalizar_catalogo(Path(args.db), dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
