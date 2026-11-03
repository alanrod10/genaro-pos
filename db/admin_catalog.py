"""Adaptador de persistencia para administración y edición de catálogo (Fase 14.3).

Permite altas, modificaciones puntuales por ID y cambios de estado (activo/inactivo),
garantizando transacciones atómicas, validaciones de esquema, auditoría de cambios
y compatibilidad histórica con la base de datos (SQLite / Turso).
"""

from datetime import datetime, timezone
import json
import logging
import os
from typing import Any, Dict, List, Optional
from db.catalog import _obtener_conexion_turso
from db.readiness import ErrorEsquemaNoPreparado

logger = logging.getLogger(__name__)


class ErrorAdminCatalogo(Exception):
    """Excepción base para errores de administración del catálogo."""
    pass


class ProductoNoEncontrado(ErrorAdminCatalogo):
    """El producto solicitado no existe en la base de datos."""
    pass


class ErrorValidacionProducto(ErrorAdminCatalogo):
    """Los datos del producto violan las restricciones de negocio."""
    pass


class ErrorDuplicadoProducto(ErrorAdminCatalogo):
    """Ya existe un producto con el mismo nombre en la base de datos."""
    pass


def _fecha_actual_utc() -> str:
    """Retorna la fecha y hora UTC en formato ISO 8601."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _verificar_tabla_auditoria(conn: Any) -> None:
    """Verifica que la tabla de auditoría exista en la base de datos sin ejecutar DDL."""
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='productos_auditoria';"
    )
    if not cur.fetchone():
        raise ErrorEsquemaNoPreparado(
            "La tabla 'productos_auditoria' no existe. Se requiere aplicar la migración 004 antes de operar."
        )


def _asegurar_tabla_auditoria(conn: Any) -> None:
    """Función de compatibilidad: verifica la existencia de auditoría sin ejecutar DDL."""
    _verificar_tabla_auditoria(conn)


def _tiene_columna_margen_ratio(conn: Any) -> bool:
    """Verifica si la tabla productos incluye una columna física escribible margen_ratio (hidden == 0)."""
    try:
        cur = conn.execute("PRAGMA table_xinfo(productos);")
        filas = cur.fetchall()
        if filas:
            return any(
                str(r[1]) == "margen_ratio" and (len(r) <= 6 or int(r[6] or 0) == 0)
                for r in filas
            )
    except Exception:
        pass
    try:
        cur = conn.execute("PRAGMA table_info(productos);")
        return any(str(r[1]) == "margen_ratio" for r in cur.fetchall())
    except Exception:
        return False


def _mapear_fila_producto(fila: Any) -> Dict[str, Any]:
    """Mapea una fila SQL de productos al formato canónico unificado de la aplicación."""
    p_id = int(fila[0])
    p_nom = str(fila[1]).strip()
    p_cat = str(fila[2]).strip() if fila[2] is not None else "Otro"
    p_prov = str(fila[3]).strip() if fila[3] is not None else "General"
    p_uni = str(fila[4]).strip() if fila[4] is not None else "Unidad"
    p_costo = int(fila[5]) if fila[5] is not None else 0
    p_dia = int(fila[6]) if fila[6] is not None else 0
    p_activo = bool(fila[7]) if fila[7] is not None else True
    p_fecha = str(fila[8]) if fila[8] is not None else ""

    p_margen = (
        round((p_dia - p_costo) / p_costo, 4) if p_costo > 0 else 0.0
    )

    return {
        "ID_PRODUCTO": p_id,
        "id_producto": p_id,
        "NOMBRE": p_nom,
        "nombre": p_nom,
        "CATEGORIA": p_cat,
        "categoria": p_cat,
        "PROVEEDOR": p_prov,
        "proveedor": p_prov,
        "UNIDAD": p_uni,
        "unidad": p_uni,
        "COSTO": p_costo,
        "costo": p_costo,
        "PRECIO_DIA": p_dia,
        "PRECIO_NOCHE": p_dia,
        "precio_venta": p_dia,
        "precio_dia": p_dia,
        "precio_noche": p_dia,
        "MARGEN_%": p_margen,
        "margen_porcentaje": p_margen,
        "ACTIVO": p_activo,
        "activo": p_activo,
        "FECHA_ACT": p_fecha,
        "fecha_act": p_fecha,
    }


def obtener_producto_por_id_turso(
    id_producto: int, conn: Optional[Any] = None
) -> Optional[Dict[str, Any]]:
    """Consulta un producto específico por su ID."""
    cerrar = False
    if conn is None:
        conn = _obtener_conexion_turso()
        cerrar = True

    try:
        cur = conn.execute(
            """
            SELECT id, nombre, categoria, proveedor, unidad, costo, precio_dia, activo, fecha_actualizacion
            FROM productos
            WHERE id = ?;
            """,
            (id_producto,),
        )
        fila = cur.fetchone()
        if not fila:
            return None
        return _mapear_fila_producto(fila)
    finally:
        if cerrar:
            try:
                conn.close()
            except Exception:
                pass


def listar_productos_admin_turso(
    conn: Optional[Any] = None,
    solo_activos: Optional[bool] = None,
    buscar: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Lista productos para administración con soporte para filtros de búsqueda y estado."""
    cerrar = False
    if conn is None:
        conn = _obtener_conexion_turso()
        cerrar = True

    try:
        query = """
            SELECT id, nombre, categoria, proveedor, unidad, costo, precio_dia, activo, fecha_actualizacion
            FROM productos
            WHERE 1=1
        """
        params: List[Any] = []

        if solo_activos is not None:
            query += " AND activo = ?"
            params.append(1 if solo_activos else 0)

        if buscar and buscar.strip():
            query += " AND (nombre LIKE ? OR proveedor LIKE ? OR categoria LIKE ?)"
            patron = f"%{buscar.strip()}%"
            params.extend([patron, patron, patron])

        query += " ORDER BY id ASC;"

        cur = conn.execute(query, tuple(params))
        filas = cur.fetchall()
        return [_mapear_fila_producto(f) for f in filas]
    finally:
        if cerrar:
            try:
                conn.close()
            except Exception:
                pass


def crear_producto_turso(
    datos: Dict[str, Any],
    conn: Optional[Any] = None,
    actor: str = "sistema",
) -> Dict[str, Any]:
    """Crea un nuevo producto en la base de datos de forma transaccional y audita la acción.

    El precio único de venta se persiste en `precio_dia` y se replica en `precio_noche`
    para satisfacer restricciones de esquema legado.
    """
    nombre = str(datos.get("nombre") or datos.get("NOMBRE") or "").strip()
    if not nombre:
        raise ErrorValidacionProducto("El nombre del producto es obligatorio.")

    categoria = str(datos.get("categoria") or datos.get("CATEGORIA") or "Otro").strip()
    proveedor = str(datos.get("proveedor") or datos.get("PROVEEDOR") or "General").strip()
    unidad = str(datos.get("unidad") or datos.get("UNIDAD") or "Unidad").strip()

    try:
        costo = int(datos.get("costo") if datos.get("costo") is not None else datos.get("COSTO", 0))
        if costo < 0:
            raise ValueError
    except (ValueError, TypeError):
        raise ErrorValidacionProducto("El costo debe ser un número entero mayor o igual a cero.")

    raw_precio = datos.get("precio_venta")
    if raw_precio is None:
        raw_precio = datos.get("PRECIO_DIA", datos.get("precio_dia"))

    try:
        precio_venta = int(raw_precio)
        if precio_venta <= 0:
            raise ValueError
    except (ValueError, TypeError):
        raise ErrorValidacionProducto("El precio de venta debe ser un número entero mayor a cero.")

    activo_int = 1 if bool(datos.get("activo", True)) else 0
    fecha_act = _fecha_actual_utc()

    cerrar = False
    if conn is None:
        conn = _obtener_conexion_turso()
        cerrar = True

    try:
        _asegurar_tabla_auditoria(conn)

        # Verificar unicidad de nombre (COLLATE NOCASE)
        cur_check = conn.execute(
            "SELECT id FROM productos WHERE nombre = ? COLLATE NOCASE;", (nombre,)
        )
        if cur_check.fetchone():
            raise ErrorDuplicadoProducto(f"Ya existe un producto con el nombre '{nombre}'.")

        # Inserción parametrizada incluyendo margen_ratio cuando la columna existe
        margen_ratio = round((precio_venta - costo) / costo, 4) if costo > 0 else 0.0
        if _tiene_columna_margen_ratio(conn):
            cur = conn.execute(
                """
                INSERT INTO productos (
                    nombre, categoria, proveedor, unidad, costo,
                    precio_dia, precio_noche, margen_ratio, activo, fecha_actualizacion
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    nombre,
                    categoria,
                    proveedor,
                    unidad,
                    costo,
                    precio_venta,
                    precio_venta,  # precio_noche alineado
                    margen_ratio,
                    activo_int,
                    fecha_act,
                ),
            )
        else:
            cur = conn.execute(
                """
                INSERT INTO productos (
                    nombre, categoria, proveedor, unidad, costo,
                    precio_dia, precio_noche, activo, fecha_actualizacion
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    nombre,
                    categoria,
                    proveedor,
                    unidad,
                    costo,
                    precio_venta,
                    precio_venta,  # precio_noche alineado
                    activo_int,
                    fecha_act,
                ),
            )
        nuevo_id = cur.lastrowid

        producto_creado = obtener_producto_por_id_turso(nuevo_id, conn=conn)
        if not producto_creado:
            raise ErrorAdminCatalogo("Fallo al recuperar el producto recién creado.")

        # Registro de auditoría
        conn.execute(
            """
            INSERT INTO productos_auditoria (
                producto_id, accion, valores_anteriores, valores_nuevos, fecha_utc, actor
            )
            VALUES (?, 'CREAR', NULL, ?, ?, ?);
            """,
            (
                nuevo_id,
                json.dumps(producto_creado, ensure_ascii=False),
                fecha_act,
                actor,
            ),
        )

        # Commit si no estamos en autocommit
        try:
            conn.commit()
        except Exception:
            pass

        return producto_creado

    except (ErrorValidacionProducto, ErrorDuplicadoProducto, ErrorEsquemaNoPreparado):
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
        raise ErrorAdminCatalogo(f"Error al insertar el producto en la base de datos: {e}") from e
    finally:
        if cerrar:
            try:
                conn.close()
            except Exception:
                pass


def editar_producto_turso(
    id_producto: int,
    cambios: Dict[str, Any],
    conn: Optional[Any] = None,
    actor: str = "sistema",
) -> Dict[str, Any]:
    """Modifica campos específicos de un producto existente por su ID.

    Nunca sobrescribe la tabla completa; aplica actualizaciones granulares
    y registra un evento de auditoría con el snapshot anterior y el posterior.
    """
    cerrar = False
    if conn is None:
        conn = _obtener_conexion_turso()
        cerrar = True

    try:
        _asegurar_tabla_auditoria(conn)

        anterior = obtener_producto_por_id_turso(id_producto, conn=conn)
        if not anterior:
            raise ProductoNoEncontrado(f"No existe ningún producto con ID {id_producto}.")

        sets: List[str] = []
        params: List[Any] = []

        # 1. Nombre
        if "nombre" in cambios or "NOMBRE" in cambios:
            nuevo_nom = str(cambios.get("nombre") or cambios.get("NOMBRE") or "").strip()
            if not nuevo_nom:
                raise ErrorValidacionProducto("El nombre del producto no puede estar vacío.")
            # Comprobar colisión con otro producto distinto
            cur_check = conn.execute(
                "SELECT id FROM productos WHERE nombre = ? COLLATE NOCASE AND id != ?;",
                (nuevo_nom, id_producto),
            )
            if cur_check.fetchone():
                raise ErrorDuplicadoProducto(
                    f"Ya existe otro producto con el nombre '{nuevo_nom}'."
                )
            sets.append("nombre = ?")
            params.append(nuevo_nom)

        # 2. Categoría
        if "categoria" in cambios or "CATEGORIA" in cambios:
            nueva_cat = str(cambios.get("categoria") or cambios.get("CATEGORIA") or "Otro").strip()
            sets.append("categoria = ?")
            params.append(nueva_cat)

        # 3. Proveedor
        if "proveedor" in cambios or "PROVEEDOR" in cambios:
            nuevo_prov = str(cambios.get("proveedor") or cambios.get("PROVEEDOR") or "General").strip()
            sets.append("proveedor = ?")
            params.append(nuevo_prov)

        # 4. Unidad
        if "unidad" in cambios or "UNIDAD" in cambios:
            nueva_uni = str(cambios.get("unidad") or cambios.get("UNIDAD") or "Unidad").strip()
            sets.append("unidad = ?")
            params.append(nueva_uni)

        # 5. Costo
        costo_efectivo = int(anterior.get("costo", 0))
        actualizo_costo_o_precio = False
        if "costo" in cambios or "COSTO" in cambios:
            try:
                nuevo_costo = int(
                    cambios.get("costo") if cambios.get("costo") is not None else cambios.get("COSTO", 0)
                )
                if nuevo_costo < 0:
                    raise ValueError
                sets.append("costo = ?")
                params.append(nuevo_costo)
                costo_efectivo = nuevo_costo
                actualizo_costo_o_precio = True
            except (ValueError, TypeError):
                raise ErrorValidacionProducto("El costo debe ser un entero no negativo.")

        # 6. Precio de venta
        precio_efectivo = int(anterior.get("precio_venta", anterior.get("precio_dia", 0)))
        raw_p = cambios.get("precio_venta")
        if raw_p is None:
            raw_p = cambios.get("PRECIO_DIA", cambios.get("precio_dia"))

        if raw_p is not None:
            try:
                nuevo_precio = int(raw_p)
                if nuevo_precio <= 0:
                    raise ValueError
                sets.append("precio_dia = ?")
                params.append(nuevo_precio)
                sets.append("precio_noche = ?")
                params.append(nuevo_precio)
                precio_efectivo = nuevo_precio
                actualizo_costo_o_precio = True
            except (ValueError, TypeError):
                raise ErrorValidacionProducto("El precio de venta debe ser un entero positivo.")

        if actualizo_costo_o_precio and _tiene_columna_margen_ratio(conn):
            nuevo_margen_ratio = (
                round((precio_efectivo - costo_efectivo) / costo_efectivo, 4)
                if costo_efectivo > 0
                else 0.0
            )
            sets.append("margen_ratio = ?")
            params.append(nuevo_margen_ratio)

        if not sets:
            # Sin cambios solicitados
            return anterior

        fecha_act = _fecha_actual_utc()
        sets.append("fecha_actualizacion = ?")
        params.append(fecha_act)

        # Clausula WHERE
        params.append(id_producto)
        sql = f"UPDATE productos SET {', '.join(sets)} WHERE id = ?;"

        conn.execute(sql, tuple(params))

        posterior = obtener_producto_por_id_turso(id_producto, conn=conn)
        if not posterior:
            raise ErrorAdminCatalogo("Fallo al recuperar el producto actualizado.")

        # Auditoría
        conn.execute(
            """
            INSERT INTO productos_auditoria (
                producto_id, accion, valores_anteriores, valores_nuevos, fecha_utc, actor
            )
            VALUES (?, 'EDITAR', ?, ?, ?, ?);
            """,
            (
                id_producto,
                json.dumps(anterior, ensure_ascii=False),
                json.dumps(posterior, ensure_ascii=False),
                fecha_act,
                actor,
            ),
        )

        try:
            conn.commit()
        except Exception:
            pass

        return posterior

    except (ProductoNoEncontrado, ErrorValidacionProducto, ErrorDuplicadoProducto, ErrorEsquemaNoPreparado):
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
        raise ErrorAdminCatalogo(f"Error al editar el producto ID {id_producto}: {e}") from e
    finally:
        if cerrar:
            try:
                conn.close()
            except Exception:
                pass


def cambiar_estado_producto_turso(
    id_producto: int,
    activo: bool,
    conn: Optional[Any] = None,
    actor: str = "sistema",
) -> Dict[str, Any]:
    """Activa o desactiva lógicamente un producto sin eliminarlo físicamente."""
    cerrar = False
    if conn is None:
        conn = _obtener_conexion_turso()
        cerrar = True

    try:
        _asegurar_tabla_auditoria(conn)

        anterior = obtener_producto_por_id_turso(id_producto, conn=conn)
        if not anterior:
            raise ProductoNoEncontrado(f"No existe ningún producto con ID {id_producto}.")

        activo_int = 1 if activo else 0
        fecha_act = _fecha_actual_utc()
        accion = "ACTIVAR" if activo else "DESACTIVAR"

        conn.execute(
            """
            UPDATE productos
            SET activo = ?, fecha_actualizacion = ?
            WHERE id = ?;
            """,
            (activo_int, fecha_act, id_producto),
        )

        posterior = obtener_producto_por_id_turso(id_producto, conn=conn)
        if not posterior:
            raise ErrorAdminCatalogo("Fallo al recuperar el producto tras el cambio de estado.")

        conn.execute(
            """
            INSERT INTO productos_auditoria (
                producto_id, accion, valores_anteriores, valores_nuevos, fecha_utc, actor
            )
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            (
                id_producto,
                accion,
                json.dumps(anterior, ensure_ascii=False),
                json.dumps(posterior, ensure_ascii=False),
                fecha_act,
                actor,
            ),
        )

        try:
            conn.commit()
        except Exception:
            pass

        return posterior

    except (ProductoNoEncontrado, ErrorEsquemaNoPreparado):
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
            f"Error al cambiar estado del producto ID {id_producto}: {e}"
        ) from e
    finally:
        if cerrar:
            try:
                conn.close()
            except Exception:
                pass


def obtener_historial_auditoria_turso(
    id_producto: Optional[int] = None,
    conn: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Recupera los eventos de auditoría para trazabilidad de catálogo."""
    cerrar = False
    if conn is None:
        conn = _obtener_conexion_turso()
        cerrar = True

    try:
        check = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='productos_auditoria';"
        ).fetchone()
        if not check:
            return []

        query = """
            SELECT id, producto_id, accion, valores_anteriores, valores_nuevos, fecha_utc, actor
            FROM productos_auditoria
        """
        params = []
        if id_producto is not None:
            query += " WHERE producto_id = ?"
            params.append(id_producto)
        query += " ORDER BY id DESC;"

        cur = conn.execute(query, tuple(params))
        filas = cur.fetchall()

        resultado = []
        for f in filas:
            resultado.append({
                "id": int(f[0]),
                "producto_id": int(f[1]),
                "accion": str(f[2]),
                "valores_anteriores": json.loads(f[3]) if f[3] else None,
                "valores_nuevos": json.loads(f[4]) if f[4] else {},
                "fecha_utc": str(f[5]),
                "actor": str(f[6]),
            })
        return resultado
    finally:
        if cerrar:
            try:
                conn.close()
            except Exception:
                pass
