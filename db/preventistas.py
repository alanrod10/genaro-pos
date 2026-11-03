"""Adaptador de persistencia para el módulo de Preventistas y Pedidos a Proveedores.

Reproduce exactamente la lógica de precios y altas de 'app.py' (mostrar_preventistas),
asegura transaccionalidad atómica, auditoría completa y soporta toma y consulta de pedidos
a preventistas/proveedores sin modificar inventario ni generar tickets de venta.
"""

from datetime import datetime, timezone
import json
import logging
import os
import sqlite3
from typing import Any, Dict, List, Optional
from db.catalog import _obtener_conexion_turso
from db.admin_catalog import _asegurar_tabla_auditoria, _verificar_tabla_auditoria, crear_producto_turso
from db.readiness import ErrorEsquemaNoPreparado

logger = logging.getLogger(__name__)


class ErrorPreventistas(Exception):
    """Excepción base para operaciones del módulo de preventistas."""
    pass


class PedidoNoEncontrado(ErrorPreventistas):
    """El pedido solicitado no existe."""
    pass


class ErrorValidacionPreventistas(ErrorPreventistas):
    """Los datos de la operación no cumplen con las reglas de negocio."""
    pass


def _fecha_actual_utc() -> str:
    """Retorna la fecha y hora UTC en formato ISO 8601."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _verificar_tablas_pedidos(conn: Any) -> None:
    """Verifica que las tablas de pedidos y su auditoría existan sin ejecutar DDL."""
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('pedidos_proveedor', 'pedido_items', 'pedidos_auditoria');"
    )
    encontradas = {r[0] for r in cur.fetchall()}
    faltantes = {"pedidos_proveedor", "pedido_items", "pedidos_auditoria"} - encontradas
    if faltantes:
        raise ErrorEsquemaNoPreparado(
            f"Las tablas requeridas {sorted(list(faltantes))} no existen. Se requiere aplicar la migración 008 antes de operar."
        )


def _asegurar_tablas_pedidos(conn: Any) -> None:
    """Función de compatibilidad: verifica la existencia de tablas de pedidos sin ejecutar DDL."""
    _verificar_tablas_pedidos(conn)


# ==============================================================================
# 1. GESTIÓN DE PRECIOS POR PREVENTISTA (Equivalente exacto a app.py)
# ==============================================================================

def listar_proveedores_turso(conn: Optional[Any] = None) -> List[str]:
    """Retorna la lista ordenada y sin duplicados de todos los proveedores en el catálogo."""
    cerrar_al_terminar = False
    if conn is None:
        conn = _obtener_conexion_turso()
        cerrar_al_terminar = True

    try:
        cursor = conn.execute(
            """
            SELECT DISTINCT proveedor
            FROM productos
            WHERE proveedor IS NOT NULL AND TRIM(proveedor) != ''
            ORDER BY proveedor COLLATE NOCASE ASC;
            """
        )
        proveedores = [str(r[0]).strip() for r in cursor.fetchall() if r[0]]
        return proveedores
    finally:
        if cerrar_al_terminar:
            conn.close()


def listar_productos_por_proveedor_turso(
    proveedor: str, conn: Optional[Any] = None
) -> List[Dict[str, Any]]:
    """Lista todos los productos asignados al proveedor indicado."""
    cerrar_al_terminar = False
    if conn is None:
        conn = _obtener_conexion_turso()
        cerrar_al_terminar = True

    try:
        cursor = conn.execute(
            """
            SELECT id, nombre, categoria, proveedor, unidad, costo, precio_dia, margen_ratio, activo, fecha_actualizacion
            FROM productos
            WHERE LOWER(TRIM(proveedor)) = LOWER(TRIM(?))
            ORDER BY nombre COLLATE NOCASE ASC;
            """,
            (proveedor.strip(),),
        )
        filas = cursor.fetchall()
        resultado = []
        for f in filas:
            p_id = int(f[0])
            nom = str(f[1])
            cat = str(f[2]) if f[2] else "Otro"
            prov = str(f[3]) if f[3] else "General"
            uni = str(f[4]) if f[4] else "Unidad"
            costo = float(f[5]) if f[5] is not None else 0.0
            precio = float(f[6]) if f[6] is not None else 0.0
            margen_ratio = float(f[7]) if f[7] is not None else (
                round((precio - costo) / costo, 4) if costo > 0 else 0.0
            )
            margen_pct = round(margen_ratio * 100, 1)
            activo = bool(f[8]) if f[8] is not None else True
            fecha_act = str(f[9]) if f[9] else ""

            resultado.append({
                "id_producto": p_id,
                "nombre": nom,
                "categoria": cat,
                "proveedor": prov,
                "unidad": uni,
                "costo": costo,
                "precio_dia": precio,
                "precio_venta": precio,
                "margen_ratio": margen_ratio,
                "margen_porcentaje": margen_pct,
                "activo": activo,
                "fecha_actualizacion": fecha_act,
            })
        return resultado
    finally:
        if cerrar_al_terminar:
            conn.close()


def actualizar_precios_lote_preventista_turso(
    proveedor: str,
    cambios: List[Dict[str, Any]],
    actor: str = "sistema",
    conn: Optional[Any] = None,
) -> Dict[str, Any]:
    """Actualiza en lote los costos y precios de productos de un proveedor determinado.

    Detecta cambios reales y actualiza costo, precio_dia, precio_noche y margen_ratio,
    registrando auditoría para cada producto modificado dentro de una transacción única.
    """
    if not proveedor.strip():
        raise ErrorValidacionPreventistas("El proveedor no puede estar vacío.")

    if not cambios:
        return {"actualizados": 0, "mensaje": "No se enviaron modificaciones."}

    cerrar_al_terminar = False
    if conn is None:
        conn = _obtener_conexion_turso()
        cerrar_al_terminar = True

    try:
        try:
            conn.execute("PRAGMA foreign_keys = ON")
        except Exception:
            pass
        _asegurar_tabla_auditoria(conn)
        fecha_utc = _fecha_actual_utc()
        fecha_texto = fecha_utc
        actualizados = 0

        for item in cambios:
            p_id = int(item.get("id_producto", 0))
            if p_id <= 0:
                continue

            nuevo_costo = float(item.get("costo", 0.0))
            nuevo_precio = float(item.get("precio_venta", item.get("precio_dia", 0.0)))

            if nuevo_costo < 0:
                raise ErrorValidacionPreventistas(f"El costo no puede ser negativo para producto ID {p_id}.")
            if nuevo_precio <= 0:
                raise ErrorValidacionPreventistas(f"El precio debe ser mayor a 0 para producto ID {p_id}.")

            cursor = conn.execute(
                "SELECT costo, precio_dia, margen_ratio, nombre FROM productos WHERE id = ?;",
                (p_id,),
            )
            fila = cursor.fetchone()
            if not fila:
                continue

            c_viejo = float(fila[0]) if fila[0] is not None else 0.0
            p_viejo = float(fila[1]) if fila[1] is not None else 0.0
            p_nombre = str(fila[3])

            if abs(nuevo_costo - c_viejo) > 0.001 or abs(nuevo_precio - p_viejo) > 0.001:
                nuevo_margen = round((nuevo_precio - nuevo_costo) / nuevo_costo, 4) if nuevo_costo > 0 else 0.0

                conn.execute(
                    """
                    UPDATE productos
                    SET costo = ?,
                        precio_dia = ?,
                        precio_noche = ?,
                        margen_ratio = ?,
                        fecha_actualizacion = ?
                    WHERE id = ?;
                    """,
                    (nuevo_costo, nuevo_precio, nuevo_precio, nuevo_margen, fecha_texto, p_id),
                )

                valores_ant = json.dumps({"costo": c_viejo, "precio_dia": p_viejo})
                valores_nuev = json.dumps({"costo": nuevo_costo, "precio_dia": nuevo_precio, "margen_ratio": nuevo_margen})

                conn.execute(
                    """
                    INSERT INTO productos_auditoria (producto_id, accion, valores_anteriores, valores_nuevos, fecha_utc, actor)
                    VALUES (?, 'EDITAR', ?, ?, ?, ?);
                    """,
                    (p_id, valores_ant, valores_nuev, fecha_utc, actor),
                )
                actualizados += 1

        if cerrar_al_terminar and hasattr(conn, "commit"):
            conn.commit()

        return {
            "actualizados": actualizados,
            "proveedor": proveedor,
            "mensaje": f"Se actualizaron {actualizados} productos para {proveedor}.",
        }
    except Exception as e:
        if cerrar_al_terminar and hasattr(conn, "rollback"):
            conn.rollback()
        raise e
    finally:
        if cerrar_al_terminar:
            conn.close()


def alta_rapida_producto_preventista_turso(
    proveedor: str,
    datos: Dict[str, Any],
    actor: str = "sistema",
    conn: Optional[Any] = None,
) -> Dict[str, Any]:
    """Crea un nuevo producto asignado al proveedor indicado (equivalente al alta rápida de app.py)."""
    if not proveedor.strip():
        raise ErrorValidacionPreventistas("El proveedor no puede estar vacío.")

    payload = dict(datos)
    payload["proveedor"] = proveedor.strip()
    return crear_producto_turso(payload, actor=actor, conn=conn)


# ==============================================================================
# 2. TOMA Y GESTIÓN DE PEDIDOS A PROVEEDORES
# ==============================================================================

def generar_siguiente_id_pedido(conn: Any) -> str:
    """Genera un ID correlativo con prefijo de fecha: PED-YYYYMMDD-XXX."""
    hoy_str = datetime.now().strftime("%Y%m%d")
    prefijo = f"PED-{hoy_str}-"
    cursor = conn.execute(
        "SELECT id FROM pedidos_proveedor WHERE id LIKE ? ORDER BY id DESC LIMIT 1;",
        (f"{prefijo}%",),
    )
    fila = cursor.fetchone()
    if not fila:
        return f"{prefijo}001"
    try:
        ultimo_num = int(fila[0].replace(prefijo, ""))
        return f"{prefijo}{ultimo_num + 1:03d}"
    except ValueError:
        return f"{prefijo}001"


def crear_pedido_proveedor_turso(
    pedido_data: Dict[str, Any],
    items: List[Dict[str, Any]],
    actor: str = "sistema",
    conn: Optional[Any] = None,
) -> Dict[str, Any]:
    """Crea un nuevo pedido de reposición a un preventista / proveedor."""
    proveedor = str(pedido_data.get("proveedor", "")).strip()
    if not proveedor:
        raise ErrorValidacionPreventistas("El proveedor es obligatorio para registrar un pedido.")

    fecha = str(pedido_data.get("fecha", "")).strip() or datetime.now().strftime("%Y-%m-%d")
    observaciones = pedido_data.get("observaciones") or ""

    if not items:
        raise ErrorValidacionPreventistas("El pedido debe contener al menos un producto.")

    cerrar_al_terminar = False
    if conn is None:
        conn = _obtener_conexion_turso()
        cerrar_al_terminar = True

    try:
        _asegurar_tablas_pedidos(conn)
        pedido_id = pedido_data.get("id") or generar_siguiente_id_pedido(conn)

        total_pedido = 0.0
        items_procesados = []

        for it in items:
            p_nom = str(it.get("producto_nombre", "")).strip()
            if not p_nom:
                raise ErrorValidacionPreventistas("Cada ítem del pedido debe tener un nombre de producto.")
            p_cant = float(it.get("cantidad", 0.0))
            if p_cant <= 0:
                raise ErrorValidacionPreventistas(f"La cantidad para '{p_nom}' debe ser mayor a 0.")
            costo_unit = float(it.get("costo_unitario", 0.0))
            if costo_unit < 0:
                raise ErrorValidacionPreventistas(f"El costo unitario para '{p_nom}' no puede ser negativo.")

            subtot = round(p_cant * costo_unit, 2)
            total_pedido += subtot

            items_procesados.append({
                "producto_id": it.get("producto_id"),
                "producto_nombre": p_nom,
                "cantidad": p_cant,
                "costo_unitario": costo_unit,
                "subtotal": subtot,
            })

        total_pedido = round(total_pedido, 2)
        fecha_utc = _fecha_actual_utc()

        conn.execute(
            """
            INSERT INTO pedidos_proveedor (id, fecha, proveedor, estado, total, observaciones, creado_en_utc, actualizado_en_utc)
            VALUES (?, ?, ?, 'PENDIENTE', ?, ?, ?, ?);
            """,
            (pedido_id, fecha, proveedor, total_pedido, observaciones, fecha_utc, fecha_utc),
        )

        for ip in items_procesados:
            cur_it = conn.execute(
                """
                INSERT INTO pedido_items (pedido_id, producto_id, producto_nombre, cantidad, costo_unitario, subtotal)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (pedido_id, ip["producto_id"], ip["producto_nombre"], ip["cantidad"], ip["costo_unitario"], ip["subtotal"]),
            )
            ip["id"] = cur_it.lastrowid

        auditoria_datos = {
            "pedido_id": pedido_id,
            "proveedor": proveedor,
            "total": total_pedido,
            "items_count": len(items_procesados),
        }
        conn.execute(
            """
            INSERT INTO pedidos_auditoria (pedido_id, accion, valores_anteriores, valores_nuevos, fecha_utc, actor)
            VALUES (?, 'CREAR_PEDIDO', NULL, ?, ?, ?);
            """,
            (pedido_id, json.dumps(auditoria_datos), fecha_utc, actor),
        )

        if cerrar_al_terminar and hasattr(conn, "commit"):
            conn.commit()

        return {
            "id": pedido_id,
            "fecha": fecha,
            "proveedor": proveedor,
            "estado": "PENDIENTE",
            "total": total_pedido,
            "observaciones": observaciones,
            "items": items_procesados,
            "creado_en_utc": fecha_utc,
        }
    except Exception as e:
        if cerrar_al_terminar and hasattr(conn, "rollback"):
            conn.rollback()
        raise e
    finally:
        if cerrar_al_terminar:
            conn.close()


def listar_pedidos_proveedor_turso(
    proveedor: Optional[str] = None,
    fecha: Optional[str] = None,
    estado: Optional[str] = None,
    conn: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Consulta la lista de pedidos con filtros opcionales."""
    cerrar_al_terminar = False
    if conn is None:
        conn = _obtener_conexion_turso()
        cerrar_al_terminar = True

    try:
        check = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pedidos_proveedor';"
        ).fetchone()
        if not check:
            return []

        clausulas = ["1=1"]
        params: List[Any] = []

        if proveedor and proveedor.strip():
            clausulas.append("LOWER(p.proveedor) = LOWER(?)")
            params.append(proveedor.strip())

        if fecha and fecha.strip():
            clausulas.append("p.fecha = ?")
            params.append(fecha.strip())

        if estado and estado.strip() and estado.strip().upper() != "TODOS":
            clausulas.append("p.estado = ?")
            params.append(estado.strip().upper())

        where_sql = " AND ".join(clausulas)
        query = f"""
            SELECT p.id, p.fecha, p.proveedor, p.estado, p.total, p.observaciones, p.creado_en_utc,
                   COUNT(pi.id) as items_count
            FROM pedidos_proveedor p
            LEFT JOIN pedido_items pi ON pi.pedido_id = p.id
            WHERE {where_sql}
            GROUP BY p.id
            ORDER BY p.fecha DESC, p.id DESC;
        """
        cursor = conn.execute(query, params)
        filas = cursor.fetchall()
        pedidos = []
        for f in filas:
            pedidos.append({
                "id": str(f[0]),
                "fecha": str(f[1]),
                "proveedor": str(f[2]),
                "estado": str(f[3]),
                "total": float(f[4]) if f[4] is not None else 0.0,
                "observaciones": str(f[5]) if f[5] else "",
                "creado_en_utc": str(f[6]) if f[6] else "",
                "items_count": int(f[7]) if f[7] is not None else 0,
            })
        return pedidos
    finally:
        if cerrar_al_terminar:
            conn.close()


def obtener_detalle_pedido_proveedor_turso(
    pedido_id: str, conn: Optional[Any] = None
) -> Optional[Dict[str, Any]]:
    """Obtiene el detalle completo de un pedido incluyendo todos sus ítems."""
    cerrar_al_terminar = False
    if conn is None:
        conn = _obtener_conexion_turso()
        cerrar_al_terminar = True

    try:
        _asegurar_tablas_pedidos(conn)
        cursor = conn.execute(
            """
            SELECT id, fecha, proveedor, estado, total, observaciones, creado_en_utc, actualizado_en_utc
            FROM pedidos_proveedor
            WHERE id = ?;
            """,
            (pedido_id,),
        )
        fila = cursor.fetchone()
        if not fila:
            return None

        cursor_items = conn.execute(
            """
            SELECT id, producto_id, producto_nombre, cantidad, costo_unitario, subtotal
            FROM pedido_items
            WHERE pedido_id = ?
            ORDER BY id ASC;
            """,
            (pedido_id,),
        )
        items_db = cursor_items.fetchall()
        items = []
        for it in items_db:
            items.append({
                "id": int(it[0]),
                "producto_id": int(it[1]) if it[1] is not None else None,
                "producto_nombre": str(it[2]),
                "cantidad": float(it[3]),
                "costo_unitario": float(it[4]),
                "subtotal": float(it[5]),
            })

        return {
            "id": str(fila[0]),
            "fecha": str(fila[1]),
            "proveedor": str(fila[2]),
            "estado": str(fila[3]),
            "total": float(fila[4]) if fila[4] is not None else 0.0,
            "observaciones": str(fila[5]) if fila[5] else "",
            "creado_en_utc": str(fila[6]) if fila[6] else "",
            "actualizado_en_utc": str(fila[7]) if fila[7] else "",
            "items": items,
        }
    finally:
        if cerrar_al_terminar:
            conn.close()


def cambiar_estado_pedido_turso(
    pedido_id: str,
    nuevo_estado: str,
    observaciones: Optional[str] = None,
    actor: str = "sistema",
    conn: Optional[Any] = None,
) -> Dict[str, Any]:
    """Actualiza el estado de un pedido (ej: de PENDIENTE a RECIBIDO o CANCELADO)."""
    estado_upper = nuevo_estado.strip().upper()
    if estado_upper not in ("PENDIENTE", "RECIBIDO", "CANCELADO"):
        raise ErrorValidacionPreventistas(f"Estado '{nuevo_estado}' no válido. Permitidos: PENDIENTE, RECIBIDO, CANCELADO.")

    cerrar_al_terminar = False
    if conn is None:
        conn = _obtener_conexion_turso()
        cerrar_al_terminar = True

    try:
        _asegurar_tablas_pedidos(conn)
        cursor = conn.execute("SELECT estado, observaciones FROM pedidos_proveedor WHERE id = ?;", (pedido_id,))
        fila = cursor.fetchone()
        if not fila:
            raise PedidoNoEncontrado(f"No existe el pedido {pedido_id}.")

        estado_anterior = str(fila[0])
        obs_anterior = str(fila[1]) if fila[1] else ""
        fecha_utc = _fecha_actual_utc()

        nueva_obs = obs_anterior
        if observaciones and observaciones.strip():
            nueva_obs = f"{obs_anterior} | {observaciones.strip()}".strip(" |")

        conn.execute(
            """
            UPDATE pedidos_proveedor
            SET estado = ?, observaciones = ?, actualizado_en_utc = ?
            WHERE id = ?;
            """,
            (estado_upper, nueva_obs, fecha_utc, pedido_id),
        )

        accion = "CAMBIAR_ESTADO" if estado_upper != "CANCELADO" else "CANCELAR_PEDIDO"
        conn.execute(
            """
            INSERT INTO pedidos_auditoria (pedido_id, accion, valores_anteriores, valores_nuevos, fecha_utc, actor)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            (
                pedido_id,
                accion,
                json.dumps({"estado": estado_anterior}),
                json.dumps({"estado": estado_upper, "observaciones": nueva_obs}),
                fecha_utc,
                actor,
            ),
        )

        if cerrar_al_terminar and hasattr(conn, "commit"):
            conn.commit()

        return {
            "id": pedido_id,
            "estado": estado_upper,
            "observaciones": nueva_obs,
            "actualizado_en_utc": fecha_utc,
        }
    except Exception as e:
        if cerrar_al_terminar and hasattr(conn, "rollback"):
            conn.rollback()
        raise e
    finally:
        if cerrar_al_terminar:
            conn.close()
