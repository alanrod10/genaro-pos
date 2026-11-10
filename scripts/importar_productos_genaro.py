#!/usr/bin/env python3
"""Script de importación, normalización y UPSERT de catálogo — GenaroPOS V4.

Localiza el archivo 'PRODUCTOS GENARO' (.xlsx, .csv) en la raíz del workspace,
aplica la normalización ortográfica y de categorías canónicas de V4, genera un backup
atómico previo de la base de datos de producción mediante SQLite backup API, y ejecuta
un UPSERT transaccional (INSERT nuevos / UPDATE existentes) sobre la tabla 'productos'
para alcanzar exactamente los 827 productos (817 activos y 10 inactivos) con cero regresión.
"""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import sys
from typing import Dict, List, Optional, Set, Tuple

# Asegurar que la raíz del proyecto esté en sys.path para importar módulos locales
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import pandas as pd

from scripts.normalizar_catalogo_v4 import (
    CATEGORIAS_CANONICAS,
    formatear_nombre_producto,
    sugerir_categoria_canonica,
)

DEFAULT_DB_PATH = "/home/alanrod10/production/data/genaro-pos.db"
DEFAULT_BACKUP_DIR = "/home/alanrod10/production/backups"
WORKSPACE_DIR = Path("/home/alanrod10/workspace/genaro-pos-turso-lab")


def detectar_archivo_origen(directorio: Path) -> Path:
    """Busca en el directorio raíz el archivo que empiece con 'PRODUCTOS GENARO'."""
    candidatos = list(directorio.glob("PRODUCTOS GENARO*.*")) + list(directorio.glob("PRODUCTOS*GENARO*.*"))
    candidatos = [c for c in candidatos if not c.name.startswith("~$") and c.is_file()]
    if not candidatos:
        raise FileNotFoundError(
            f"No se encontró ningún archivo con nombre 'PRODUCTOS GENARO' en '{directorio}'."
        )
    # Preferir .xlsx sobre otros formatos si hay duplicados
    candidatos.sort(key=lambda p: (0 if p.suffix.lower() == ".xlsx" else 1, p.name))
    return candidatos[0]


def cargar_datos_origen(archivo: Path) -> pd.DataFrame:
    """Lee el archivo de origen (Excel o CSV) y valida su estructura básica."""
    sufijo = archivo.suffix.lower()
    if sufijo in (".xlsx", ".xls"):
        df = pd.read_excel(archivo)
    elif sufijo in (".csv", ".tsv"):
        try:
            df = pd.read_csv(archivo, sep=None, engine="python")
        except Exception:
            df = pd.read_csv(archivo)
    else:
        raise ValueError(f"Formato no soportado para el archivo: '{archivo.name}' ({sufijo}).")

    columnas_requeridas = {"ID", "Producto", "Categoría"}
    faltantes = columnas_requeridas - set(df.columns)
    if faltantes:
        raise ValueError(f"El archivo origen carece de las columnas obligatorias: {faltantes}")

    return df


def realizar_backup_sqlite(db_path: Path, backup_dir: Path) -> Path:
    """Crea una copia de seguridad atómica usando la API de backup nativa de SQLite."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_file = backup_dir / f"genaro-pos_pre_import_827_{timestamp}.db"

    with sqlite3.connect(db_path) as src_conn, sqlite3.connect(backup_file) as dst_conn:
        src_conn.backup(dst_conn)

    return backup_file


def importar_catalogo(
    archivo_origen: Path,
    db_path: Path,
    backup_dir: Path,
    dry_run: bool = False,
    force: bool = False,
) -> int:
    """Ejecuta el proceso completo de normalización e ingesta transaccional (UPSERT)."""
    print("=" * 80)
    print(" 🚀 INGESTA, NORMALIZACIÓN Y UPSERT DE CATÁLOGO — GENAROPOS V4")
    print(f" Archivo origen : {archivo_origen}")
    print(f" Base de datos  : {db_path}")
    print("=" * 80)

    if not db_path.exists():
        print(f"❌ Error: La base de datos no existe en '{db_path}'.")
        return 1

    # 1. Cargar datos del archivo
    try:
        df = cargar_datos_origen(archivo_origen)
        print(f"\n📂 Archivo cargado exitosamente: {len(df)} filas detectadas.")
    except Exception as exc:
        print(f"❌ Error al leer el archivo origen: {exc}")
        return 1

    # 2. Conectar y analizar base de datos existente
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT id, nombre, categoria, activo FROM productos;")
        filas_db = cursor.fetchall()
        db_ids: Set[int] = {row["id"] for row in filas_db}
        total_previo_db = len(filas_db)
        print(f"📊 Productos existentes en base de datos: {total_previo_db}")

        # 3. Normalizar filas en vuelo
        filas_update: List[Tuple] = []
        filas_insert: List[Tuple] = []
        conteo_activos = 0
        conteo_inactivos = 0

        fecha_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        for _, r in df.iterrows():
            raw_id = str(r["ID"]).strip().lstrip("#")
            try:
                pid = int(raw_id)
            except ValueError:
                print(f"⚠️ Omitiendo fila con ID no numérico: {r['ID']}")
                continue

            # Normalización de nombre y categoría canónica V4
            raw_nom = str(r.get("Producto", "") or "")
            nom_normalizado = formatear_nombre_producto(raw_nom)

            raw_cat = str(r.get("Categoría", "") or "")
            cat_canonica = sugerir_categoria_canonica(raw_cat, nom_normalizado)

            # Normalización de campos auxiliares
            proveedor = str(r.get("Proveedor", "") or "").strip() or "General"
            unidad = str(r.get("Unidad", "") or "").strip() or "Unidad"

            try:
                costo = int(round(float(r.get("Costo", 0) or 0)))
            except (ValueError, TypeError):
                costo = 0
            if costo < 0:
                costo = 0

            try:
                precio_dia = int(round(float(r.get("Precio Venta", 0) or 0)))
            except (ValueError, TypeError):
                precio_dia = 0
            if precio_dia < 0:
                precio_dia = 0

            # Determinación de estado Activo/Inactivo (817 activos, 10 inactivos)
            raw_estado = str(r.get("Estado", "Activo") or "").strip().lower()
            activo = 0 if raw_estado in ("inactivo", "0", "false") else 1

            if activo == 1:
                conteo_activos += 1
            else:
                conteo_inactivos += 1

            registro = (
                pid,
                nom_normalizado,
                cat_canonica,
                proveedor,
                unidad,
                costo,
                precio_dia,
                activo,
                fecha_utc,
            )

            if pid in db_ids:
                # Registro existente -> UPDATE
                filas_update.append(registro)
            else:
                # Registro nuevo -> INSERT
                filas_insert.append(registro)

        total_procesados = len(filas_update) + len(filas_insert)
        print(f"\n📋 Análisis de Ingesta:")
        print(f"   • Total productos a procesar:  {total_procesados}")
        print(f"   • Existentes a ACTUALIZAR:     {len(filas_update)}")
        print(f"   • Nuevos a INSERTAR:           {len(filas_insert)}")
        print(f"   • Distribución de estado:      {conteo_activos} Activos / {conteo_inactivos} Inactivos")

        if filas_insert:
            print("\n✨ Muestra de nuevos productos a insertar:")
            for p in filas_insert[:6]:
                print(f"   [#{p[0]:4d}] {p[1]:40s} | {p[2]:25s} | Costo: ${p[5]:5d} | Precio: ${p[6]:5d}")
            if len(filas_insert) > 6:
                print(f"   ... y {len(filas_insert) - 6} productos nuevos adicionales.")

        if dry_run:
            print("\nℹ️  Modo --dry-run activo: No se realizaron modificaciones en la base de datos.")
            return 0

        # Confirmación interactiva si no se especificó --force
        if not force:
            print("\n" + "-" * 78)
            resp = input(
                f"¿Confirmás aplicar el UPSERT de {total_procesados} productos en '{db_path.name}'? [s/N]: "
            ).strip().lower()
            if resp not in ("s", "si", "sí", "y", "yes"):
                print("🚫 Operación cancelada por el usuario. Base de datos sin cambios.")
                return 0

        # 4. Realizar backup previo con SQLite API (.backup)
        print("\n💾 Generando backup de seguridad previo...")
        backup_creado = realizar_backup_sqlite(db_path, backup_dir)
        print(f"✅ Backup creado exitosamente en: {backup_creado}")

        # 5. Ejecutar UPSERT transaccional
        print("\n🔄 Ejecutando transacción UPSERT...")
        conn.execute("BEGIN IMMEDIATE;")

        # A. Actualizar existentes (preservando ticket_items y claves primarias)
        for p in filas_update:
            conn.execute(
                """
                UPDATE productos
                SET nombre = ?,
                    categoria = ?,
                    proveedor = ?,
                    unidad = ?,
                    costo = ?,
                    precio_dia = ?,
                    activo = ?,
                    fecha_actualizacion = ?
                WHERE id = ?;
                """,
                (p[1], p[2], p[3], p[4], p[5], p[6], p[7], p[8], p[0]),
            )

        # B. Insertar nuevos productos con su ID explícito
        for p in filas_insert:
            conn.execute(
                """
                INSERT INTO productos (
                    id,
                    nombre,
                    categoria,
                    proveedor,
                    unidad,
                    costo,
                    precio_dia,
                    precio_noche,
                    activo,
                    fecha_actualizacion
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?);
                """,
                (p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7], p[8]),
            )

        conn.commit()
        print("✅ Transacción commitada con éxito.")

        # 6. Verificación estricta post-ingesta
        cursor.execute("SELECT count(*) FROM productos;")
        total_post = cursor.fetchone()[0]

        cursor.execute("SELECT activo, count(*) FROM productos GROUP BY activo ORDER BY activo DESC;")
        estados_post = {row[0]: row[1] for row in cursor.fetchall()}

        cursor.execute("SELECT count(DISTINCT categoria) FROM productos;")
        total_cats = cursor.fetchone()[0]

        print("\n" + "=" * 80)
        print(" 🎯 VERIFICACIÓN FINAL EN PRODUCCIÓN (genaro-pos.db)")
        print("=" * 80)
        print(f" • Total de productos en la tabla: {total_post} (Esperado: 827)")
        print(f" • Productos Activos (activo = 1): {estados_post.get(1, 0)} (Esperado: 817)")
        print(f" • Productos Inactivos (activo = 0): {estados_post.get(0, 0)} (Esperado: 10)")
        print(f" • Total de categorías distintas:  {total_cats}")
        print("=" * 80)

        if total_post == 827 and estados_post.get(1, 0) == 817 and estados_post.get(0, 0) == 10:
            print("🎉 ¡Éxito absoluto! Catálogo de 827 productos normalizado e importado al 100%.")
            return 0
        else:
            print("⚠️ Advertencia: El conteo final difiere de las cantidades esperadas.")
            return 1

    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"❌ Error durante el proceso de importación: {exc}")
        return 1
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Importación, normalización y UPSERT de productos para GenaroPOS V4."
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=None,
        help="Ruta al archivo origen (por defecto: detecta 'PRODUCTOS GENARO' en el workspace).",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(DEFAULT_DB_PATH),
        help=f"Ruta a la base de datos de producción (por defecto: {DEFAULT_DB_PATH}).",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=Path(DEFAULT_BACKUP_DIR),
        help=f"Directorio de backups (por defecto: {DEFAULT_BACKUP_DIR}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simula la importación y muestra el resumen sin modificar la base de datos.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Aplica el UPSERT directamente sin confirmación interactiva en consola.",
    )

    args = parser.parse_args()

    archivo = args.file
    if archivo is None:
        try:
            archivo = detectar_archivo_origen(WORKSPACE_DIR)
        except Exception as exc:
            print(f"❌ {exc}")
            return 1

    return importar_catalogo(
        archivo_origen=archivo,
        db_path=args.db,
        backup_dir=args.backup_dir,
        dry_run=args.dry_run,
        force=args.force,
    )


if __name__ == "__main__":
    sys.exit(main())
