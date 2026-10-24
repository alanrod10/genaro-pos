"""Acceso transaccional de ventas para el laboratorio Genaro POS.

Este módulo no conecta con servicios externos al importarse. Las funciones
aceptan una conexión inyectada para poder probarse con SQLite local.
"""
import os
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import pandas as pd


BACKEND_SHEETS = "SHEETS"
BACKEND_TURSO = "TURSO"

_HOST_DEV_AUTORIZADO = "genaro-pos-dev-alanrod10.aws-sa-east-1.turso.io"
_ENTORNO_DEV_AUTORIZADO = "genaro-pos-dev"
_ZONA_AR = ZoneInfo("America/Argentina/Jujuy")

COLUMNAS_MOVIMIENTOS = (
    "TICKET_ID",
    "FECHA",
    "TOTAL_VENTA",
    "MONTO_EFECTIVO",
    "MONTO_TRANSF",
    "ES_NOCTURNO",
)
COLUMNAS_ITEMS = (
    "TICKET_ID",
    "FECHA",
    "PRODUCTO",
    "CANTIDAD",
    "UNIDAD",
    "PRECIO_UNIT",
    "SUBTOTAL",
    "METODO_PAGO",
)
ID_ITEM_INTERNO = "_TURSO_ITEM_ID"


class ErrorVentasTurso(ValueError):
    """Error de validación o acceso a ventas en Turso."""


def obtener_backend_ventas():
    backend = os.getenv("VENTAS_BACKEND", BACKEND_SHEETS).strip().upper()
    if backend not in {BACKEND_SHEETS, BACKEND_TURSO}:
        raise ErrorVentasTurso("VENTAS_BACKEND debe ser SHEETS o TURSO.")
    return backend


def _abrir_conexion_dev():
    url = os.getenv("TURSO_DEV_DATABASE_URL", "").strip()
    token = os.getenv("TURSO_DEV_AUTH_TOKEN", "").strip()
    entorno = os.getenv("ENV_NAME", "").strip()
    parsed = urlparse(url)

    if (
        entorno != _ENTORNO_DEV_AUTORIZADO
        or parsed.scheme != "turso"
        or parsed.hostname != _HOST_DEV_AUTORIZADO
        or not token
    ):
        raise ErrorVentasTurso(
            "Conexión bloqueada: se requiere el endpoint autorizado de DEV."
        )

    import turso_serverless

    return turso_serverless.connect(url, auth_token=token)


@contextmanager
def _conexion(conn=None):
    propia = conn is None
    conexion = _abrir_conexion_dev() if propia else conn
    try:
        yield conexion
    finally:
        if propia:
            conexion.close()


def _fecha_utc(valor):
    if valor is None or pd.isna(valor):
        raise ErrorVentasTurso("La fecha de la venta es obligatoria.")

    if isinstance(valor, pd.Timestamp):
        valor = valor.to_pydatetime()

    if isinstance(valor, datetime):
        dt = valor
    elif isinstance(valor, date):
        dt = datetime.combine(valor, datetime.min.time())
    else:
        texto = str(valor).strip()
        dt = None
        for formato in (
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y %H:%M",
            "%d/%m/%Y",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
        ):
            try:
                dt = datetime.strptime(texto, formato)
                break
            except ValueError:
                continue

        if dt is None:
            try:
                dt = datetime.fromisoformat(texto.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ErrorVentasTurso(
                    f"Formato de fecha no reconocido: {texto!r}."
                ) from exc

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_ZONA_AR)
    else:
        dt = dt.astimezone(_ZONA_AR)

    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _fecha_local(valor_utc):
    dt = datetime.fromisoformat(str(valor_utc).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_ZONA_AR).strftime("%d/%m/%Y %H:%M:%S")


def _importe(valor, campo):
    try:
        n = Decimal(str(valor))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ErrorVentasTurso(f"{campo} no es un importe válido.") from exc

    if not n.is_finite() or n != n.to_integral_value() or n < 0:
        raise ErrorVentasTurso(
            f"{campo} debe ser un importe entero no negativo."
        )
    return int(n)


def _normalizar_venta(ticket, items):
    if not hasattr(ticket, "get"):
        raise ErrorVentasTurso("El ticket debe ser un registro.")

    ticket_id = str(ticket.get("TICKET_ID", "")).strip()
    if not ticket_id:
        raise ErrorVentasTurso("TICKET_ID es obligatorio.")

    fecha = _fecha_utc(ticket.get("FECHA"))
    metodo = str(ticket.get("METODO_PAGO", "")).strip().upper()
    if metodo not in {"EFECTIVO", "TRANSFERENCIA", "MIXTO"}:
        raise ErrorVentasTurso("Método de pago no permitido.")

    total = _importe(ticket.get("TOTAL_VENTA"), "TOTAL_VENTA")
    efectivo = _importe(ticket.get("MONTO_EFECTIVO"), "MONTO_EFECTIVO")
    transferencia = _importe(ticket.get("MONTO_TRANSF"), "MONTO_TRANSF")

    if efectivo + transferencia != total:
        raise ErrorVentasTurso(
            "Efectivo más transferencia debe coincidir con el total."
        )
    if metodo == "EFECTIVO" and (efectivo != total or transferencia != 0):
        raise ErrorVentasTurso("El pago en efectivo no coincide con el método.")
    if metodo == "TRANSFERENCIA" and (
        transferencia != total or efectivo != 0
    ):
        raise ErrorVentasTurso(
            "El pago por transferencia no coincide con el método."
        )

    if not isinstance(items, (list, tuple)) or not items:
        raise ErrorVentasTurso("La venta debe tener al menos un ítem.")

    items_normalizados = []
    for item in items:
        nombre = str(item.get("PRODUCTO", "") or "").strip()
        if not nombre:
            raise ErrorVentasTurso("Cada ítem requiere nombre de producto.")

        try:
            cantidad = Decimal(str(item.get("CANTIDAD")))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise ErrorVentasTurso("Cantidad inválida.") from exc
        if not cantidad.is_finite() or cantidad <= 0:
            raise ErrorVentasTurso("La cantidad debe ser mayor que cero.")

        items_normalizados.append({
            "producto_nombre": nombre,
            "cantidad": float(cantidad),
            "unidad": str(item.get("UNIDAD", "Unidad") or "Unidad").strip(),
            "precio_unitario": _importe(
                item.get("PRECIO_UNIT"), "PRECIO_UNIT"
            ),
            "subtotal": _importe(item.get("SUBTOTAL"), "SUBTOTAL"),
            "metodo_pago": metodo,
        })

    suma_items = sum(item["subtotal"] for item in items_normalizados)
    if suma_items != total:
        raise ErrorVentasTurso(
            f"Los subtotales ({suma_items}) no coinciden con el ticket ({total})."
        )

    return {
        "id": ticket_id,
        "fecha_hora_utc": fecha,
        "total_venta": total,
        "monto_efectivo": efectivo,
        "monto_transferencia": transferencia,
        "metodo_pago": metodo,
    }, items_normalizados


def _producto_id_si_no_ambiguo(conn, nombre):
    """Vincula el producto solo si existe exactamente una coincidencia activa."""
    filas = conn.execute(
        """
        SELECT id FROM productos
        WHERE nombre = ? COLLATE NOCASE AND activo = 1
        LIMIT 2
        """,
        (nombre,),
    ).fetchall()
    return int(filas[0][0]) if len(filas) == 1 else None


def registrar_venta_turso(ticket, items, conn=None):
    """Guarda ticket e ítems juntos; ante un fallo hace rollback completo."""
    cabecera, detalle = _normalizar_venta(ticket, items)

    with _conexion(conn) as conexion:
        try:
            conexion.execute("BEGIN IMMEDIATE")

            conexion.execute(
                """
                INSERT INTO tickets (
                    id, fecha_hora_utc, total_venta, monto_efectivo,
                    monto_transferencia, metodo_pago
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    cabecera["id"],
                    cabecera["fecha_hora_utc"],
                    cabecera["total_venta"],
                    cabecera["monto_efectivo"],
                    cabecera["monto_transferencia"],
                    cabecera["metodo_pago"],
                ),
            )

            for item in detalle:
                producto_id = _producto_id_si_no_ambiguo(
                    conexion, item["producto_nombre"]
                )
                conexion.execute(
                    """
                    INSERT INTO ticket_items (
                        ticket_id, producto_id, producto_nombre, cantidad,
                        unidad, precio_unitario, subtotal, metodo_pago
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cabecera["id"],
                        producto_id,
                        item["producto_nombre"],
                        item["cantidad"],
                        item["unidad"],
                        item["precio_unitario"],
                        item["subtotal"],
                        item["metodo_pago"],
                    ),
                )

            conexion.commit()
            return cabecera["id"]

        except Exception:
            conexion.rollback()
            raise


def cargar_movimientos_turso(conn=None):
    """Devuelve tickets activos con las columnas de DB_MOVIMIENTOS_CAJA."""
    with _conexion(conn) as conexion:
        filas = conexion.execute(
            """
            SELECT id, fecha_hora_utc, total_venta, monto_efectivo,
                   monto_transferencia
            FROM tickets
            WHERE anulado = 0
            ORDER BY fecha_hora_utc DESC, id DESC
            """
        ).fetchall()

    registros = [
        {
            "TICKET_ID": fila[0],
            "FECHA": _fecha_local(fila[1]),
            "TOTAL_VENTA": int(fila[2]),
            "MONTO_EFECTIVO": int(fila[3]),
            "MONTO_TRANSF": int(fila[4]),
            "ES_NOCTURNO": False,
        }
        for fila in filas
    ]
    return pd.DataFrame(registros, columns=COLUMNAS_MOVIMIENTOS)


def cargar_items_turso(conn=None):
    """Devuelve ítems de tickets activos con columnas compatibles con la app."""
    with _conexion(conn) as conexion:
        filas = conexion.execute(
            """
            SELECT ti.id, t.id, t.fecha_hora_utc, ti.producto_nombre,
                   ti.cantidad, ti.unidad, ti.precio_unitario,
                   ti.subtotal, ti.metodo_pago
            FROM ticket_items AS ti
            JOIN tickets AS t ON t.id = ti.ticket_id
            WHERE t.anulado = 0
            ORDER BY t.fecha_hora_utc DESC, t.id DESC, ti.id
            """
        ).fetchall()

    registros = [
        {
            ID_ITEM_INTERNO: int(fila[0]),
            "TICKET_ID": fila[1],
            "FECHA": _fecha_local(fila[2]),
            "PRODUCTO": fila[3],
            "CANTIDAD": float(fila[4]),
            "UNIDAD": fila[5],
            "PRECIO_UNIT": int(fila[6]),
            "SUBTOTAL": int(fila[7]),
            "METODO_PAGO": fila[8],
        }
        for fila in filas
    ]
    return pd.DataFrame(
        registros, columns=[ID_ITEM_INTERNO, *COLUMNAS_ITEMS]
    )
