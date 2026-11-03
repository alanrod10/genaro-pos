"""Acceso transaccional de ventas para el laboratorio Genaro POS.

Este módulo no conecta con servicios externos al importarse. Las funciones
aceptan una conexión inyectada para poder probarse con SQLite local.
"""
import json
import os
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import pandas as pd
from db.readiness import ErrorEsquemaNoPreparado
from db.commercial_outbox_emitter import enqueue_commercial_event, to_amount_cents


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


class ErrorConflictoIdempotencia(ErrorVentasTurso):
    """El ticket ya existe pero con contenido diferente al solicitado."""


def obtener_backend_ventas():
    backend = os.getenv("VENTAS_BACKEND", BACKEND_SHEETS).strip().upper()
    if backend not in {BACKEND_SHEETS, BACKEND_TURSO}:
        raise ErrorVentasTurso("VENTAS_BACKEND debe ser SHEETS o TURSO.")
    return backend


def habilitar_foreign_keys(conn) -> bool:
    """Activa PRAGMA foreign_keys = ON de forma segura cuando el motor lo soporta."""
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        return True
    except Exception:
        return False


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

    conn = turso_serverless.connect(url, auth_token=token)
    habilitar_foreign_keys(conn)
    return conn


@contextmanager
def _conexion(conn=None):
    propia = conn is None
    conexion = _abrir_conexion_dev() if propia else conn
    try:
        yield conexion
    finally:
        if propia:
            conexion.close()


def _rango_utc_dia_local(fecha_str: str):
    """Calcula el intervalo UTC semiabierto [inicio_utc, fin_utc) para una fecha local en Argentina."""
    texto = str(fecha_str or "").strip()
    if not texto:
        raise ErrorVentasTurso("La fecha de filtro es inválida.")
    parte_fecha = texto.split(" ")[0]
    try:
        if "/" in parte_fecha:
            d, m, y = [int(p) for p in parte_fecha.split("/")[:3]]
        else:
            y, m, d = [int(p) for p in parte_fecha[:10].split("-")[:3]]
        inicio_local = datetime(y, m, d, 0, 0, 0, tzinfo=_ZONA_AR)
    except (ValueError, TypeError) as exc:
        raise ErrorVentasTurso(f"Formato de fecha de filtro inválido: {fecha_str!r}.") from exc

    fin_local = inicio_local + timedelta(days=1)
    inicio_utc = inicio_local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    fin_utc = fin_local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return inicio_utc, fin_utc


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

        raw_costo = item.get("COSTO_UNITARIO")
        if raw_costo is None and "costo_unitario" in item:
            raw_costo = item.get("costo_unitario")
        costo_snapshot = (
            _importe(raw_costo, "COSTO_UNITARIO")
            if raw_costo is not None
            else None
        )

        items_normalizados.append({
            "producto_nombre": nombre,
            "cantidad": float(cantidad),
            "unidad": str(item.get("UNIDAD", "Unidad") or "Unidad").strip(),
            "precio_unitario": _importe(
                item.get("PRECIO_UNIT"), "PRECIO_UNIT"
            ),
            "subtotal": _importe(item.get("SUBTOTAL"), "SUBTOTAL"),
            "metodo_pago": metodo,
            "costo_unitario_snapshot": costo_snapshot,
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


def _tiene_columna(conn, tabla, columna):
    """Verifica de forma segura si una columna existe en la tabla."""
    try:
        cols = {
            row[1]
            for row in conn.execute(f"PRAGMA table_info({tabla})").fetchall()
        }
        return columna in cols
    except Exception:
        return False


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


def _resolver_productos_lote(conn, nombres):
    """Resuelve en una sola consulta IDs y costos de productos no ambiguos y activos.

    Retorna un diccionario: {nombre_lower: {'id': int, 'costo': int | None}}
    Solo asigna valores si existe exactamente 1 coincidencia activa.
    Si hay 0 o más de 1 coincidencia (ambigüedad), no vincula.
    """
    nombres_unicos = {str(n).strip() for n in nombres if n and str(n).strip()}
    if not nombres_unicos:
        return {}

    placeholders = ", ".join("?" for _ in nombres_unicos)
    try:
        filas = conn.execute(
            f"""
            SELECT LOWER(nombre), COUNT(*), MIN(id), MIN(costo)
            FROM productos
            WHERE activo = 1 AND LOWER(nombre) IN ({placeholders})
            GROUP BY LOWER(nombre)
            """,
            [n.lower() for n in nombres_unicos],
        ).fetchall()
        return {
            fila[0]: {
                "id": int(fila[2]),
                "costo": int(fila[3]) if fila[3] is not None else None,
            }
            for fila in filas
            if fila[1] == 1
        }
    except Exception:
        filas = conn.execute(
            f"""
            SELECT LOWER(nombre), COUNT(*), MIN(id)
            FROM productos
            WHERE activo = 1 AND LOWER(nombre) IN ({placeholders})
            GROUP BY LOWER(nombre)
            """,
            [n.lower() for n in nombres_unicos],
        ).fetchall()
        return {
            fila[0]: {
                "id": int(fila[2]),
                "costo": None,
            }
            for fila in filas
            if fila[1] == 1
        }


def _resolver_productos_ids_lote(conn, nombres):
    """Resuelve en una sola consulta los IDs de productos no ambiguos y activos."""
    lote = _resolver_productos_lote(conn, nombres)
    return {k: v["id"] for k, v in lote.items()}


def registrar_venta_turso(ticket, items, usuario="sistema", conn=None):
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

            prod_info_por_nombre = _resolver_productos_lote(
                conexion, [item["producto_nombre"] for item in detalle]
            )

            placeholders_items = ", ".join(
                ["(?, ?, ?, ?, ?, ?, ?, ?, ?)"] * len(detalle)
            )
            params_items = []
            for item in detalle:
                prod = prod_info_por_nombre.get(
                    item["producto_nombre"].strip().lower()
                )
                producto_id = prod["id"] if prod else None
                snapshot_costo = item.get("costo_unitario_snapshot")
                costo_unitario = (
                    snapshot_costo
                    if snapshot_costo is not None
                    else (prod["costo"] if prod else None)
                )

                params_items.extend([
                    cabecera["id"],
                    producto_id,
                    item["producto_nombre"],
                    item["cantidad"],
                    item["unidad"],
                    item["precio_unitario"],
                    item["subtotal"],
                    item["metodo_pago"],
                    costo_unitario,
                ])

            try:
                conexion.execute(
                    f"""
                    INSERT INTO ticket_items (
                        ticket_id, producto_id, producto_nombre, cantidad,
                        unidad, precio_unitario, subtotal, metodo_pago, costo_unitario
                    )
                    VALUES {placeholders_items}
                    """,
                    params_items,
                )
            except Exception as e:
                if "costo_unitario" in str(e).lower():
                    raise ErrorEsquemaNoPreparado(
                        "La columna 'costo_unitario' no existe en 'ticket_items'. Se requiere aplicar la migración 009 antes de registrar ventas."
                    ) from e
                raise

            # Auditoría atómica de creación de ticket (migración 007)
            datos_nuevos = {
                "cabecera": cabecera,
                "items": detalle,
            }
            try:
                conexion.execute(
                    """
                    INSERT INTO ventas_auditoria (
                        ticket_id, item_id, accion, motivo, usuario, datos_previos_json, datos_nuevos_json
                    )
                    VALUES (?, NULL, 'CREACION', 'Registro inicial de venta', ?, NULL, ?)
                    """,
                    (
                        cabecera["id"],
                        usuario,
                        json.dumps(datos_nuevos, ensure_ascii=False),
                    ),
                )
            except Exception as e:
                if "no such table" in str(e).lower() or "ventas_auditoria" in str(e).lower():
                    raise ErrorEsquemaNoPreparado(
                        f"Error al registrar auditoría de venta: {str(e)}. Se requiere la tabla 'ventas_auditoria' (migración 007)."
                    ) from e
                raise

            # Emisión de evento outbox en la misma transacción atómica
            enqueue_commercial_event(
                conn=conexion,
                event_type="SALE_CONFIRMED",
                entity_type="TICKET",
                entity_id=cabecera["id"],
                action_type="CONFIRM",
                idempotency_key=f"COMM_SALE_{cabecera['id']}_CONFIRM",
                payload={
                    "ticket_id": cabecera["id"],
                    "fecha_hora_utc": cabecera["fecha_hora_utc"],
                    "total_venta_cents": to_amount_cents(cabecera["total_venta"]),
                    "monto_efectivo_cents": to_amount_cents(cabecera["monto_efectivo"]),
                    "monto_transferencia_cents": to_amount_cents(cabecera["monto_transferencia"]),
                    "metodo_pago": cabecera["metodo_pago"],
                    "usuario": usuario,
                    "items_count": len(detalle),
                },
                created_at=cabecera["fecha_hora_utc"],
            )

            conexion.commit()
            return cabecera["id"]

        except Exception:
            conexion.rollback()
            raise


def _consultar_venta(conexion, ticket_id):
    """Consulta la cabecera y el detalle de ítems de un ticket existente."""
    t_row = conexion.execute(
        """
        SELECT id, fecha_hora_utc, total_venta, monto_efectivo,
               monto_transferencia, metodo_pago, anulado
        FROM tickets
        WHERE id = ?
        """,
        (ticket_id,),
    ).fetchone()

    if not t_row:
        return None

    cabecera = {
        "id": t_row[0],
        "fecha_hora_utc": t_row[1],
        "total_venta": int(t_row[2]),
        "monto_efectivo": int(t_row[3]),
        "monto_transferencia": int(t_row[4]),
        "metodo_pago": str(t_row[5]),
        "anulado": int(t_row[6]),
    }

    i_rows = conexion.execute(
        """
        SELECT producto_nombre, cantidad, unidad, precio_unitario,
               subtotal, metodo_pago
        FROM ticket_items
        WHERE ticket_id = ?
        ORDER BY id ASC
        """,
        (ticket_id,),
    ).fetchall()

    items = [
        {
            "producto_nombre": str(r[0]),
            "cantidad": float(r[1]),
            "unidad": str(r[2]),
            "precio_unitario": int(r[3]),
            "subtotal": int(r[4]),
            "metodo_pago": str(r[5]),
        }
        for r in i_rows
    ]

    return cabecera, items


def consultar_venta_turso(ticket_id, conn=None):
    """Consulta la cabecera y el detalle de ítems de un ticket existente.

    Retorna una tupla (cabecera_dict, items_list) o None si el ticket no existe.
    """
    with _conexion(conn) as conexion:
        return _consultar_venta(conexion, ticket_id)


def _clave_item_comparacion(item):
    return (
        item["producto_nombre"].strip().lower(),
        round(float(item["cantidad"]), 4),
        item["unidad"].strip().lower(),
        int(item["precio_unitario"]),
        int(item["subtotal"]),
        item["metodo_pago"].strip().upper(),
    )


def verificar_identidad_venta(
    guardado_cabecera, guardado_items, cabecera_normalizada, detalle_normalizado
):
    """Compara exhaustivamente una venta guardada con un payload normalizado."""
    if guardado_cabecera.get("anulado", 0) != 0:
        return False

    if guardado_cabecera["id"] != cabecera_normalizada["id"]:
        return False

    if guardado_cabecera["fecha_hora_utc"] != cabecera_normalizada["fecha_hora_utc"]:
        return False

    if guardado_cabecera["total_venta"] != cabecera_normalizada["total_venta"]:
        return False

    if guardado_cabecera["monto_efectivo"] != cabecera_normalizada["monto_efectivo"]:
        return False

    if guardado_cabecera["monto_transferencia"] != cabecera_normalizada["monto_transferencia"]:
        return False

    if guardado_cabecera["metodo_pago"] != cabecera_normalizada["metodo_pago"]:
        return False

    if len(guardado_items) != len(detalle_normalizado):
        return False

    items_g = sorted(guardado_items, key=_clave_item_comparacion)
    items_n = sorted(detalle_normalizado, key=_clave_item_comparacion)

    for ig, in_ in zip(items_g, items_n):
        if _clave_item_comparacion(ig) != _clave_item_comparacion(in_):
            return False

    return True


def registrar_venta_idempotente_turso(ticket, items, usuario="sistema", conn=None):
    """Registra una venta de forma idempotente con protección concurrente en BD.

    Retorna una tupla (ticket_id, es_reintento):
    - (ticket_id, False): Venta nueva insertada y confirmada en la base.
    - (ticket_id, True): Venta idéntica previamente confirmada (operación idempotente).

    Lanza ErrorConflictoIdempotencia si el ticket ya existe con contenido discordante.
    Lanza sqlite3.IntegrityError / ErrorVentasTurso / Exception si ocurre un fallo real.
    """
    cabecera, detalle = _normalizar_venta(ticket, items)

    with _conexion(conn) as conexion:
        # 1. Comprobación inicial rápida
        venta_existente = _consultar_venta(conexion, cabecera["id"])
        if venta_existente is not None:
            guardado_cabecera, guardado_items = venta_existente
            if verificar_identidad_venta(
                guardado_cabecera, guardado_items, cabecera, detalle
            ):
                return cabecera["id"], True
            raise ErrorConflictoIdempotencia(
                f"El ticket '{cabecera['id']}' ya existe pero con contenido diferente."
            )

        # 2. Inserción atómica con protección de concurrencia a nivel de BD
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

            prod_info_por_nombre = _resolver_productos_lote(
                conexion, [item["producto_nombre"] for item in detalle]
            )

            placeholders_items = ", ".join(
                ["(?, ?, ?, ?, ?, ?, ?, ?, ?)"] * len(detalle)
            )
            params_items = []
            for item in detalle:
                prod = prod_info_por_nombre.get(
                    item["producto_nombre"].strip().lower()
                )
                producto_id = prod["id"] if prod else None
                snapshot_costo = item.get("costo_unitario_snapshot")
                costo_unitario = (
                    snapshot_costo
                    if snapshot_costo is not None
                    else (prod["costo"] if prod else None)
                )

                params_items.extend([
                    cabecera["id"],
                    producto_id,
                    item["producto_nombre"],
                    item["cantidad"],
                    item["unidad"],
                    item["precio_unitario"],
                    item["subtotal"],
                    item["metodo_pago"],
                    costo_unitario,
                ])

            try:
                conexion.execute(
                    f"""
                    INSERT INTO ticket_items (
                        ticket_id, producto_id, producto_nombre, cantidad,
                        unidad, precio_unitario, subtotal, metodo_pago, costo_unitario
                    )
                    VALUES {placeholders_items}
                    """,
                    params_items,
                )
            except Exception as e:
                if "costo_unitario" in str(e).lower():
                    raise ErrorEsquemaNoPreparado(
                        "La columna 'costo_unitario' no existe en 'ticket_items'. Se requiere aplicar la migración 009 antes de registrar ventas."
                    ) from e
                raise

            # Auditoría atómica de creación de ticket (migración 007)
            datos_nuevos = {
                "cabecera": cabecera,
                "items": detalle,
            }
            try:
                conexion.execute(
                    """
                    INSERT INTO ventas_auditoria (
                        ticket_id, item_id, accion, motivo, usuario, datos_previos_json, datos_nuevos_json
                    )
                    VALUES (?, NULL, 'CREACION', 'Registro inicial de venta', ?, NULL, ?)
                    """,
                    (
                        cabecera["id"],
                        usuario,
                        json.dumps(datos_nuevos, ensure_ascii=False),
                    ),
                )
            except Exception as e:
                if "no such table" in str(e).lower() or "ventas_auditoria" in str(e).lower():
                    raise ErrorEsquemaNoPreparado(
                        f"Error al registrar auditoría de venta: {str(e)}. Se requiere la tabla 'ventas_auditoria' (migración 007)."
                    ) from e
                raise

            # Emisión de evento outbox en la misma transacción atómica
            enqueue_commercial_event(
                conn=conexion,
                event_type="SALE_CONFIRMED",
                entity_type="TICKET",
                entity_id=cabecera["id"],
                action_type="CONFIRM",
                idempotency_key=f"COMM_SALE_{cabecera['id']}_CONFIRM",
                payload={
                    "ticket_id": cabecera["id"],
                    "fecha_hora_utc": cabecera["fecha_hora_utc"],
                    "total_venta_cents": to_amount_cents(cabecera["total_venta"]),
                    "monto_efectivo_cents": to_amount_cents(cabecera["monto_efectivo"]),
                    "monto_transferencia_cents": to_amount_cents(cabecera["monto_transferencia"]),
                    "metodo_pago": cabecera["metodo_pago"],
                    "usuario": usuario,
                    "items_count": len(detalle),
                },
                created_at=cabecera["fecha_hora_utc"],
            )

            conexion.commit()
            return cabecera["id"], False

        except Exception as exc:
            try:
                conexion.rollback()
            except Exception:
                pass

            # Comprobación de recuperación idempotente tras fallo de persistencia:
            # Solamente reconocemos la operación como completada si consultamos el registro
            # guardado y verificamos que su contenido corresponde al payload original.
            venta_confirmada = _consultar_venta(conexion, cabecera["id"])
            if venta_confirmada is not None:
                guardado_cabecera, guardado_items = venta_confirmada
                if verificar_identidad_venta(
                    guardado_cabecera, guardado_items, cabecera, detalle
                ):
                    return cabecera["id"], True
                raise ErrorConflictoIdempotencia(
                    f"El ticket '{cabecera['id']}' ya existe pero con contenido diferente."
                ) from exc

            # Si el registro NO existe en la base, relanzar la excepción original (sin falso éxito)
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


def listar_ventas_turso(
    fecha=None,
    metodo_pago=None,
    incluir_anuladas=True,
    busqueda=None,
    limite=200,
    conn=None,
):
    """Consulta el listado de tickets con filtros y cuenta de ítems."""
    with _conexion(conn) as conexion:
        where_clauses = []
        params = []

        if not incluir_anuladas:
            where_clauses.append("t.anulado = 0")

        if fecha:
            inicio_utc, fin_utc = _rango_utc_dia_local(fecha)
            where_clauses.append("t.fecha_hora_utc >= ? AND t.fecha_hora_utc < ?")
            params.extend([inicio_utc, fin_utc])

        if metodo_pago and metodo_pago.upper() != "TODOS":
            where_clauses.append("t.metodo_pago = ?")
            params.append(metodo_pago.upper())

        if busqueda and busqueda.strip():
            term = f"%{busqueda.strip()}%"
            where_clauses.append(
                """(
                    t.id LIKE ? OR EXISTS (
                        SELECT 1 FROM ticket_items ti2
                        WHERE ti2.ticket_id = t.id AND ti2.producto_nombre LIKE ?
                    )
                )"""
            )
            params.extend([term, term])

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        query = f"""
            SELECT t.id, t.fecha_hora_utc, t.total_venta, t.monto_efectivo,
                   t.monto_transferencia, t.metodo_pago, t.anulado, t.motivo_anulacion,
                   COUNT(ti.id) as cant_items
            FROM tickets t
            LEFT JOIN ticket_items ti ON ti.ticket_id = t.id
            {where_sql}
            GROUP BY t.id
            ORDER BY t.fecha_hora_utc DESC, t.id DESC
            LIMIT ?
        """
        params.append(limite)
        filas = conexion.execute(query, params).fetchall()

        resultado = []
        for r in filas:
            resultado.append({
                "ticket_id": str(r[0]),
                "fecha_hora_utc": str(r[1]),
                "fecha": _fecha_local(r[1]),
                "total_venta": int(r[2]),
                "monto_efectivo": int(r[3]),
                "monto_transferencia": int(r[4]),
                "metodo_pago": str(r[5]),
                "anulado": bool(r[6]),
                "motivo_anulacion": str(r[7]) if r[7] else None,
                "cantidad_items": int(r[8]),
            })
        return resultado


def obtener_detalle_ticket_turso(ticket_id, conn=None):
    """Consulta cabecera, lista completa de ítems y eventos de auditoría."""
    with _conexion(conn) as conexion:
        t_row = conexion.execute(
            """
            SELECT id, fecha_hora_utc, total_venta, monto_efectivo,
                   monto_transferencia, metodo_pago, anulado, motivo_anulacion
            FROM tickets
            WHERE id = ?
            """,
            (ticket_id,),
        ).fetchone()

        if not t_row:
            return None

        cabecera = {
            "ticket_id": str(t_row[0]),
            "fecha_hora_utc": str(t_row[1]),
            "fecha": _fecha_local(t_row[1]),
            "total_venta": int(t_row[2]),
            "monto_efectivo": int(t_row[3]),
            "monto_transferencia": int(t_row[4]),
            "metodo_pago": str(t_row[5]),
            "anulado": bool(t_row[6]),
            "motivo_anulacion": str(t_row[7]) if t_row[7] else None,
        }

        has_costo = _tiene_columna(conexion, "ticket_items", "costo_unitario")
        costo_select = ", costo_unitario" if has_costo else ", NULL as costo_unitario"

        i_rows = conexion.execute(
            f"""
            SELECT id, producto_nombre, cantidad, unidad, precio_unitario,
                   subtotal, metodo_pago{costo_select}
            FROM ticket_items
            WHERE ticket_id = ?
            ORDER BY id ASC
            """,
            (ticket_id,),
        ).fetchall()

        items = [
            {
                "id": int(r[0]),
                "ticket_id": ticket_id,
                "producto_nombre": str(r[1]),
                "cantidad": float(r[2]),
                "unidad": str(r[3]),
                "precio_unitario": int(r[4]),
                "subtotal": int(r[5]),
                "metodo_pago": str(r[6]),
                "costo_unitario": int(r[7]) if r[7] is not None else None,
            }
            for r in i_rows
        ]

        auditoria = []
        try:
            a_rows = conexion.execute(
                """
                SELECT id, ticket_id, item_id, accion, motivo, usuario,
                       datos_previos_json, datos_nuevos_json, creado_en_utc
                FROM ventas_auditoria
                WHERE ticket_id = ?
                ORDER BY creado_en_utc ASC, id ASC
                """,
                (ticket_id,),
            ).fetchall()
            for ar in a_rows:
                auditoria.append({
                    "id": int(ar[0]),
                    "ticket_id": str(ar[1]),
                    "item_id": int(ar[2]) if ar[2] is not None else None,
                    "accion": str(ar[3]),
                    "motivo": str(ar[4]) if ar[4] else None,
                    "usuario": str(ar[5]),
                    "datos_previos_json": str(ar[6]) if ar[6] else None,
                    "datos_nuevos_json": str(ar[7]) if ar[7] else None,
                    "creado_en_utc": str(ar[8]),
                })
        except Exception:
            pass

        cabecera["items"] = items
        cabecera["auditoria"] = auditoria
        return cabecera


def anular_venta_turso(
    ticket_id,
    motivo="Anulación solicitada",
    usuario="sistema",
    conn=None,
):
    """Anula lógicamente un ticket y registra evento de auditoría."""
    with _conexion(conn) as conexion:
        t_row = conexion.execute(
            """
            SELECT id, total_venta, monto_efectivo, monto_transferencia, metodo_pago, anulado, motivo_anulacion
            FROM tickets
            WHERE id = ?
            """,
            (ticket_id,),
        ).fetchone()

        if not t_row:
            raise ErrorVentasTurso(f"El ticket '{ticket_id}' no existe.")

        if t_row[5]:
            return obtener_detalle_ticket_turso(ticket_id, conn=conexion)

        prev_state = {
            "total_venta": int(t_row[1]),
            "monto_efectivo": int(t_row[2]),
            "monto_transferencia": int(t_row[3]),
            "metodo_pago": str(t_row[4]),
            "anulado": bool(t_row[5]),
            "motivo_anulacion": t_row[6],
        }

        conexion.execute(
            """
            UPDATE tickets
            SET anulado = 1, motivo_anulacion = ?
            WHERE id = ?
            """,
            (motivo, ticket_id),
        )

        new_state = {
            **prev_state,
            "anulado": True,
            "motivo_anulacion": motivo,
        }

        try:
            conexion.execute(
                """
                INSERT INTO ventas_auditoria (ticket_id, accion, motivo, usuario, datos_previos_json, datos_nuevos_json)
                VALUES (?, 'ANULACION', ?, ?, ?, ?)
                """,
                (
                    ticket_id,
                    motivo,
                    usuario,
                    json.dumps(prev_state, ensure_ascii=False),
                    json.dumps(new_state, ensure_ascii=False),
                ),
            )
        except Exception as e:
            conexion.rollback()
            raise ErrorEsquemaNoPreparado(
                f"Error al registrar auditoría de anulación: {str(e)}. Se requiere la tabla 'ventas_auditoria' (migración 007)."
            ) from e

        # Emisión de evento outbox de anulación con trazabilidad de referencia
        enqueue_commercial_event(
            conn=conexion,
            event_type="SALE_VOIDED",
            entity_type="TICKET",
            entity_id=ticket_id,
            action_type="VOID",
            idempotency_key=f"COMM_SALE_{ticket_id}_VOID",
            payload={
                "ticket_id": ticket_id,
                "motivo": motivo,
                "usuario": usuario,
                "total_venta_cents": to_amount_cents(prev_state["total_venta"]),
                "monto_efectivo_cents": to_amount_cents(prev_state["monto_efectivo"]),
                "monto_transferencia_cents": to_amount_cents(prev_state["monto_transferencia"]),
                "metodo_pago": prev_state["metodo_pago"],
                "reference_action": "CONFIRM",
                "reference_idempotency_key": f"COMM_SALE_{ticket_id}_CONFIRM",
            },
        )

        conexion.commit()
        return obtener_detalle_ticket_turso(ticket_id, conn=conexion)

def editar_item_venta_turso(
    ticket_id,
    item_id,
    nueva_cantidad=None,
    nuevo_subtotal=None,
    usuario="sistema",
    conn=None,
):
    """Edita un ítem de ticket y recalcula la cabecera preservando reglas legacy."""
    with _conexion(conn) as conexion:
        t_row = conexion.execute(
            "SELECT id, total_venta, monto_efectivo, monto_transferencia, metodo_pago, anulado FROM tickets WHERE id = ?",
            (ticket_id,),
        ).fetchone()
        if not t_row:
            raise ErrorVentasTurso(f"El ticket '{ticket_id}' no existe.")
        if t_row[5]:
            raise ErrorVentasTurso(f"No se pueden editar ítems de un ticket anulado ('{ticket_id}').")

        has_costo = _tiene_columna(conexion, "ticket_items", "costo_unitario")
        costo_select = ", costo_unitario" if has_costo else ", NULL as costo_unitario"

        i_row = conexion.execute(
            f"SELECT id, producto_nombre, cantidad, unidad, precio_unitario, subtotal, metodo_pago{costo_select} FROM ticket_items WHERE id = ? AND ticket_id = ?",
            (item_id, ticket_id),
        ).fetchone()
        if not i_row:
            raise ErrorVentasTurso(f"El ítem #{item_id} no pertenece al ticket '{ticket_id}'.")

        cant_actual = float(i_row[2])
        precio_unit = int(i_row[4])
        subtotal_actual = int(i_row[5])
        costo_unit_actual = int(i_row[7]) if i_row[7] is not None else None

        cant_final = float(nueva_cantidad) if nueva_cantidad is not None else cant_actual
        if cant_final <= 0:
            raise ErrorVentasTurso("La cantidad del ítem debe ser mayor a 0.")

        if nuevo_subtotal is not None:
            subtotal_final = int(nuevo_subtotal)
            if subtotal_final < 0:
                raise ErrorVentasTurso("El subtotal del ítem no puede ser negativo.")
        else:
            subtotal_final = int(round(cant_final * precio_unit))

        item_prev = {
            "item_id": item_id,
            "cantidad": cant_actual,
            "subtotal": subtotal_actual,
            "costo_unitario": costo_unit_actual,
        }
        item_nuevo = {
            "item_id": item_id,
            "cantidad": cant_final,
            "subtotal": subtotal_final,
            "costo_unitario": costo_unit_actual,
        }

        conexion.execute(
            "UPDATE ticket_items SET cantidad = ?, subtotal = ? WHERE id = ?",
            (cant_final, subtotal_final, item_id),
        )

        suma_row = conexion.execute(
            "SELECT COALESCE(SUM(subtotal), 0) FROM ticket_items WHERE ticket_id = ?",
            (ticket_id,),
        ).fetchone()
        new_total = int(suma_row[0])

        old_total = int(t_row[1])
        efvo = int(t_row[2])
        transf = int(t_row[3])
        diff = old_total - new_total

        if new_total <= 0:
            conexion.execute(
                "UPDATE tickets SET total_venta = 0, monto_efectivo = 0, monto_transferencia = 0, anulado = 1, motivo_anulacion = 'Total recalculado en $0' WHERE id = ?",
                (ticket_id,),
            )
        else:
            if diff > 0:
                if efvo >= diff:
                    new_efvo = efvo - diff
                    new_transf = transf
                else:
                    new_efvo = 0
                    new_transf = max(0, transf - (diff - efvo))
            elif diff < 0:
                if transf > 0 and efvo == 0:
                    new_transf = transf - diff
                    new_efvo = efvo
                else:
                    new_efvo = efvo - diff
                    new_transf = transf
            else:
                new_efvo = efvo
                new_transf = transf

            if new_efvo + new_transf != new_total:
                new_efvo = max(0, new_total - new_transf)

            if new_efvo > 0 and new_transf > 0:
                new_metodo = "MIXTO"
            elif new_efvo > 0:
                new_metodo = "EFECTIVO"
            else:
                new_metodo = "TRANSFERENCIA"

            conexion.execute(
                """
                UPDATE tickets
                SET total_venta = ?, monto_efectivo = ?, monto_transferencia = ?, metodo_pago = ?
                WHERE id = ?
                """,
                (new_total, new_efvo, new_transf, new_metodo, ticket_id),
            )

        try:
            conexion.execute(
                """
                INSERT INTO ventas_auditoria (ticket_id, item_id, accion, motivo, usuario, datos_previos_json, datos_nuevos_json)
                VALUES (?, ?, 'EDICION_ITEM', 'Corrección de ítem en historial', ?, ?, ?)
                """,
                (
                    ticket_id,
                    item_id,
                    usuario,
                    json.dumps(item_prev, ensure_ascii=False),
                    json.dumps(item_nuevo, ensure_ascii=False),
                ),
            )
        except Exception as e:
            conexion.rollback()
            raise ErrorEsquemaNoPreparado(
                f"Error al registrar auditoría de edición de ítem: {str(e)}. Se requiere la tabla 'ventas_auditoria' (migración 007)."
            ) from e

        conexion.commit()
        return obtener_detalle_ticket_turso(ticket_id, conn=conexion)
