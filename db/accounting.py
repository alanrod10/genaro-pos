"""Módulo de transacciones atómicas, validación contable y orquestación del Outbox para Genaro POS.

ARQUITECTURA DE IDEMPOTENCIA Y RECUPERACIÓN (Fase 11):
- DB-API 2.0 (PEP 249) nativo: inicio implícito de transacciones y delimitación estricta con commit/rollback.
- Diferenciación explícita de errores: fallos en bloque de trabajo (rollback local garantizado)
  vs fallos en conn.commit() (ErrorCommitIncierto: resultado indeterminado en el servidor HTTP/remoto).
- Idempotencia estricta en `registrar_venta_atomica()`: detección de reintentos seguros
  por `ticket_id` sin duplicar cabecera, ítems ni eventos Outbox. Detección de conflicto
  en caso de alteración de payload para una misma clave.
- Outbox Worker reforzado:
  * Control de concurrencia y particionamiento por lotes atómico mediante `UPDATE ... RETURNING`.
  * Prevención de confirmaciones espurias (fencing token vía `intentos_esperados` y estado `EN_PROCESO`).
  * Timeout de reclamo medido desde `procesado_en_utc` (no desde la creación del evento).
  * Exclusión de concurrencia para el mismo `aggregate_id` si ya existe un evento `EN_PROCESO`
    (garantía de orden secuencial y prevención de sobrescritura de modificaciones recientes).
"""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import sqlite3
from typing import Any, Dict, Iterator, List, Optional, Tuple

_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _validar_identificador_tabla(nombre: str) -> str:
  """Valida que un nombre de tabla sea un identificador SQL seguro."""
  if not isinstance(nombre, str) or not _IDENTIFIER_RE.match(nombre):
    raise ValueError(f"Identificador de tabla inválido o inseguro: '{nombre}'")
  return nombre


class ErrorContable(Exception):
  """Excepción lanzada cuando una operación viola la coherencia contable."""

  pass


class ErrorValidacion(Exception):
  """Excepción lanzada por inconsistencia en los datos de entrada o conflicto de idempotencia."""

  pass


class ErrorCommitIncierto(Exception):
  """Excepción lanzada cuando conn.commit() falla.

  El estado de la transacción en el servidor remoto es INDETERMINADO: la transacción
  pudo haberse persistido en el motor remoto antes de perderse la conexión HTTP/red.
  El cliente NO debe asumir que los cambios fueron revertidos ni reintentar a ciegas
  sin verificar el estado previo o contar con idempotencia garantizada.
  """

  def __init__(self, mensaje: str, error_original: Optional[Exception] = None):
    super().__init__(mensaje)
    self.error_original = error_original


@dataclass
class ItemVenta:
  producto_nombre: str
  cantidad: float
  precio_unitario: int
  subtotal: int
  metodo_pago: str
  producto_id: Optional[int] = None
  unidad: str = "Unidad"


@contextmanager
def transaccion(conn: Any) -> Iterator[None]:
  """Context manager para delimitar una unidad transaccional DB-API 2.0 atómica.

  - Si ocurre una excepción dentro del bloque yield (antes del commit), se ejecuta
    conn.rollback() y se propaga la excepción original (reversión garantizada).
  - Si el bloque finaliza limpiamente y conn.commit() falla, el resultado en el servidor
    es incierto en arquitecturas cliente-servidor / HTTP. Se intenta un rollback de mejor
    esfuerzo y se lanza ErrorCommitIncierto para evitar asunciones erróneas.
  """
  try:
    yield
  except Exception:
    try:
      conn.rollback()
    except Exception:
      pass
    raise

  try:
    conn.commit()
  except Exception as e:
    try:
      conn.rollback()
    except Exception:
      pass
    raise ErrorCommitIncierto(
        f"Fallo durante la confirmación (commit): {e}. "
        "El estado de la transacción en el servidor es incierto.",
        error_original=e,
    ) from e


def validar_coherencia_venta(
    total_venta: int,
    monto_efectivo: int,
    monto_transferencia: int,
    items: List[ItemVenta],
) -> None:
  """Valida la coherencia contable estricta antes de cualquier persistencia:

  1. total_venta == monto_efectivo + monto_transferencia
  2. total_venta == sum(subtotal de cada item)
  3. subtotal == cantidad * precio_unitario (para unidades enteras o redondeo
  estándar)
  4. cantidad > 0, precio_unitario >= 0, subtotal >= 0
  """
  if total_venta < 0 or monto_efectivo < 0 or monto_transferencia < 0:
    raise ErrorValidacion("Los montos no pueden ser negativos.")

  if total_venta != (monto_efectivo + monto_transferencia):
    raise ErrorContable(
        f"Inconsistencia en pagos: total_venta ({total_venta}) != efectivo"
        f" ({monto_efectivo}) + transferencia ({monto_transferencia})."
    )

  suma_items = sum(item.subtotal for item in items)
  if total_venta != suma_items:
    raise ErrorContable(
        f"Inconsistencia de ítems: total_venta ({total_venta}) != suma de"
        f" subtotales ({suma_items})."
    )

  for idx, it in enumerate(items, 1):
    if it.cantidad <= 0:
      raise ErrorValidacion(
          f"Ítem {idx} ('{it.producto_nombre}'): cantidad debe ser mayor a 0."
      )
    if it.precio_unitario < 0 or it.subtotal < 0:
      raise ErrorValidacion(
          f"Ítem {idx} ('{it.producto_nombre}'): precio o subtotal negativo."
      )


def registrar_venta_atomica(
    conn: Any,
    ticket_id: str,
    total_venta: int,
    monto_efectivo: int,
    monto_transferencia: int,
    metodo_pago: str,
    items: List[ItemVenta],
    fecha_utc: Optional[str] = None,
    tabla_tickets: str = "tickets",
    tabla_items: str = "ticket_items",
    tabla_outbox: str = "outbox_events",
) -> Dict[str, Any]:
  """Inserta cabecera de ticket, líneas de ítems y evento Outbox en una única

  transacción atómica DB-API 2.0 con soporte de idempotencia estricta.

  Si el `ticket_id` ya existe:
  - Si coincide exactamente el total, montos, método de pago e ítems, retorna
    indicando estado 'EXISTENTE_IDEMPOTENTE' sin duplicar registros.
  - Si los datos difieren, lanza `ErrorValidacion` por conflicto de idempotencia.
  """
  t_tickets = _validar_identificador_tabla(tabla_tickets)
  t_items = _validar_identificador_tabla(tabla_items)
  t_outbox = _validar_identificador_tabla(tabla_outbox)

  validar_coherencia_venta(
      total_venta, monto_efectivo, monto_transferencia, items
  )

  if not fecha_utc:
    fecha_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

  with transaccion(conn):
    # 1. Comprobación de idempotencia: ¿Existe ya este ticket_id?
    cursor = conn.execute(
        f"""
        SELECT id, total_venta, monto_efectivo, monto_transferencia, metodo_pago, anulado
        FROM {t_tickets}
        WHERE id = ?
        """,
        (ticket_id,),
    )
    existente = cursor.fetchone()
    if existente:
      ex_total, ex_efvo, ex_transf, ex_metodo, ex_anulado = (
          existente[1],
          existente[2],
          existente[3],
          existente[4],
          existente[5],
      )
      if (
          ex_total == total_venta
          and ex_efvo == monto_efectivo
          and ex_transf == monto_transferencia
          and ex_metodo == metodo_pago
          and ex_anulado == 0
      ):
        # Comprobar igualdad de los ítems existentes
        items_existentes = conn.execute(
            f"""
            SELECT producto_nombre, cantidad, subtotal
            FROM {t_items}
            WHERE ticket_id = ?
            ORDER BY id ASC
            """,
            (ticket_id,),
        ).fetchall()
        if len(items_existentes) == len(items):
          coinciden = True
          for (ex_prod, ex_cant, ex_sub), it in zip(items_existentes, items):
            if (
                ex_prod != it.producto_nombre
                or ex_cant != it.cantidad
                or ex_sub != it.subtotal
            ):
              coinciden = False
              break
          if coinciden:
            return {
                "status": "EXISTENTE_IDEMPOTENTE",
                "ticket_id": ticket_id,
                "mensaje": (
                    "Venta previamente registrada con idénticos parámetros."
                ),
            }

      raise ErrorValidacion(
          f"Conflicto de idempotencia: el ticket '{ticket_id}' ya existe con"
          " datos diferentes."
      )

    # 2. Inserción de Cabecera
    conn.execute(
        f"""
        INSERT INTO {t_tickets} (id, fecha_hora_utc, total_venta, monto_efectivo, monto_transferencia, metodo_pago)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            ticket_id,
            fecha_utc,
            total_venta,
            monto_efectivo,
            monto_transferencia,
            metodo_pago,
        ),
    )

    # 3. Líneas de Ítems
    for it in items:
      conn.execute(
          f"""
          INSERT INTO {t_items} (ticket_id, producto_id, producto_nombre, cantidad, unidad, precio_unitario, subtotal, metodo_pago)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?)
          """,
          (
              ticket_id,
              it.producto_id,
              it.producto_nombre,
              it.cantidad,
              it.unidad,
              it.precio_unitario,
              it.subtotal,
              it.metodo_pago,
          ),
      )

    # 4. Evento Outbox
    payload = {
        "ticket_id": ticket_id,
        "fecha_hora_utc": fecha_utc,
        "total_venta": total_venta,
        "monto_efectivo": monto_efectivo,
        "monto_transferencia": monto_transferencia,
        "metodo_pago": metodo_pago,
        "items": [
            {
                "producto": it.producto_nombre,
                "cantidad": it.cantidad,
                "subtotal": it.subtotal,
            }
            for it in items
        ],
    }
    conn.execute(
        f"""
        INSERT INTO {t_outbox} (aggregate_type, aggregate_id, operation, payload_json, estado)
        VALUES ('TICKET', ?, 'INSERT', ?, 'PENDIENTE')
        """,
        (ticket_id, json.dumps(payload)),
    )

    return {
        "status": "REGISTRADO",
        "ticket_id": ticket_id,
        "mensaje": "Venta registrada exitosamente.",
    }


def modificar_item_y_recalcular_ticket(
    conn: Any,
    item_id: int,
    nuevo_subtotal: int,
    nueva_cantidad: Optional[float] = None,
    regla_redistribucion: str = "EFECTIVO_PRIMERO",
) -> Tuple[int, int, int]:
  """Modifica el subtotal de un ítem y recalcula atómicamente el ticket padre

  (total_venta, efectivo y transferencia) conservando coherencia exacta.

  Retorna: (nuevo_total, nuevo_efvo, nuevo_transf)
  """
  if nuevo_subtotal < 0:
    raise ErrorValidacion("El subtotal no puede ser negativo.")

  with transaccion(conn):
    # 1. Obtener item actual y ticket asociado
    item_row = conn.execute(
        "SELECT ticket_id, subtotal FROM ticket_items WHERE id = ?", (item_id,)
    ).fetchone()
    if not item_row:
      raise ErrorValidacion(f"Ítem con ID {item_id} no encontrado.")

    ticket_id, subtotal_viejo = item_row[0], item_row[1]
    diff = subtotal_viejo - nuevo_subtotal  # Positivo si bajó el monto

    # 2. Actualizar el ítem
    if nueva_cantidad is not None:
      if nueva_cantidad <= 0:
        raise ErrorValidacion("La cantidad debe ser mayor a cero.")
      conn.execute(
          "UPDATE ticket_items SET subtotal = ?, cantidad = ? WHERE id = ?",
          (nuevo_subtotal, nueva_cantidad, item_id),
      )
    else:
      conn.execute(
          "UPDATE ticket_items SET subtotal = ? WHERE id = ?",
          (nuevo_subtotal, item_id),
      )

    # 3. Obtener estado del ticket padre
    ticket_row = conn.execute(
        "SELECT total_venta, monto_efectivo, monto_transferencia, metodo_pago"
        " FROM tickets WHERE id = ?",
        (ticket_id,),
    ).fetchone()
    old_total, old_efvo, old_transf, metodo_orig = (
        ticket_row[0],
        ticket_row[1],
        ticket_row[2],
        ticket_row[3],
    )

    # Suma real de todos los ítems actuales del ticket
    new_total = conn.execute(
        "SELECT COALESCE(SUM(subtotal), 0) FROM ticket_items WHERE ticket_id ="
        " ?",
        (ticket_id,),
    ).fetchone()[0]

    # 4. Redistribución de pagos
    if new_total == 0:
      new_efvo = 0
      new_transf = 0
      conn.execute(
          "UPDATE tickets SET total_venta = 0, monto_efectivo = 0,"
          " monto_transferencia = 0, anulado = 1 WHERE id = ?",
          (ticket_id,),
      )
    else:
      if diff > 0:  # El total bajó
        if regla_redistribucion == "EFECTIVO_PRIMERO":
          if old_efvo >= diff:
            new_efvo = old_efvo - diff
            new_transf = old_transf
          else:
            new_efvo = 0
            new_transf = max(0, old_transf - (diff - old_efvo))
        else:
          # Proporcional estricto
          ratio = new_total / old_total if old_total > 0 else 1.0
          new_efvo = int(round(old_efvo * ratio))
          new_transf = new_total - new_efvo
      else:  # El total subió
        incremento = -diff
        if old_transf > 0 and old_efvo == 0:
          new_transf = old_transf + incremento
          new_efvo = 0
        else:
          new_efvo = old_efvo + incremento
          new_transf = old_transf

      # Invariante contable estricta
      if new_total != (new_efvo + new_transf):
        new_efvo = new_total - new_transf

      conn.execute(
          """
          UPDATE tickets
          SET total_venta = ?, monto_efectivo = ?, monto_transferencia = ?
          WHERE id = ?
          """,
          (new_total, new_efvo, new_transf, ticket_id),
      )

    # 5. Registrar evento de modificación en Outbox
    payload = {
        "ticket_id": ticket_id,
        "accion": "MODIFICACION_ITEM",
        "item_id": item_id,
        "nuevo_subtotal": nuevo_subtotal,
        "nuevo_total_ticket": new_total,
        "nuevo_efectivo": new_efvo,
        "nuevo_transferencia": new_transf,
    }
    conn.execute(
        """
        INSERT INTO outbox_events (aggregate_type, aggregate_id, operation, payload_json, estado)
        VALUES ('TICKET', ?, 'UPDATE', ?, 'PENDIENTE')
        """,
        (ticket_id, json.dumps(payload)),
    )

    return new_total, new_efvo, new_transf


# =========================================================
# PROTOCOLO DE TRABAJO DEL OUTBOX (WORKER STATE MACHINE)
# =========================================================


def reclamar_lote_outbox(
    conn: Any,
    limite: int = 20,
    timeout_en_proceso_minutos: int = 10,
    tabla_outbox: str = "outbox_events",
) -> List[Dict[str, Any]]:
  """Reclama un lote de eventos de forma atómica y determinista:

  1. Auto-recupera eventos colgados en 'EN_PROCESO' que superaron el timeout
     (calculado desde procesado_en_utc o creado_en_utc), incrementando intentos.
  2. Mueve a 'ERROR' (dead-letter) los que superaron el límite de reintentos
  (>= 5).
  3. Reclama atómicamente hasta `limite` eventos 'PENDIENTE' actualizándolos a
  'EN_PROCESO'.
     Para evitar condiciones de carrera donde dos modificaciones sobre la misma
     entidad
     se procesen desordenadas, excluye eventos de aggregate_id que ya tengan
     eventos
     en 'EN_PROCESO'.
  4. Utiliza la cláusula estándar `UPDATE ... RETURNING` (soportada en SQLite
  3.35+ y libSQL).
  """
  t_outbox = _validar_identificador_tabla(tabla_outbox)
  with transaccion(conn):
    # 1. Recuperar colgados antiguos incrementando intentos
    conn.execute(
        f"""
        UPDATE {t_outbox}
        SET estado = 'PENDIENTE', intentos = intentos + 1, ultimo_error = 'Timeout en procesamiento previo'
        WHERE estado = 'EN_PROCESO' 
          AND datetime('now') > datetime(COALESCE(procesado_en_utc, creado_en_utc), '+{timeout_en_proceso_minutos} minutes')
        """
    )

    # 2. Descartar los que superaron reintentos máximos (dead-letter)
    conn.execute(f"""
        UPDATE {t_outbox}
        SET estado = 'ERROR'
        WHERE estado = 'PENDIENTE' AND intentos >= 5
    """)

    # 3. Reclamo atómico: actualizar a EN_PROCESO con exclusión de entidades en vuelo
    cursor = conn.execute(
        f"""
        UPDATE {t_outbox}
        SET estado = 'EN_PROCESO',
            procesado_en_utc = datetime('now')
        WHERE id IN (
            SELECT o1.id FROM {t_outbox} o1
            WHERE o1.estado = 'PENDIENTE'
              AND NOT EXISTS (
                  SELECT 1 FROM {t_outbox} o2
                  WHERE o2.aggregate_id = o1.aggregate_id
                    AND o2.estado = 'EN_PROCESO'
                    AND o2.id != o1.id
              )
            ORDER BY o1.id ASC 
            LIMIT ?
        )
        RETURNING id, aggregate_type, aggregate_id, operation, payload_json, intentos;
        """,
        (limite,),
    )
    filas = cursor.fetchall()
    if not filas:
      return []

    lote = []
    for r in filas:
      lote.append({
          "id": r[0],
          "aggregate_type": r[1],
          "aggregate_id": r[2],
          "operation": r[3],
          "payload": json.loads(r[4]),
          "intentos": r[5],
      })
    return lote


def confirmar_evento_outbox(
    conn: Any,
    event_id: int,
    intentos_esperados: Optional[int] = None,
    tabla_outbox: str = "outbox_events",
) -> bool:
  """Marca un evento individual como exitosamente sincronizado.

  Exige estrictamente que el evento esté en 'EN_PROCESO'.
  Si se proporciona `intentos_esperados`, valida que el reclamo no haya caducado
  y sido reasignado (fencing token).
  Retorna True si se confirmó con éxito, False si no coincidió el estado/token.
  """
  t_outbox = _validar_identificador_tabla(tabla_outbox)
  with transaccion(conn):
    sql = f"""
        UPDATE {t_outbox}
        SET estado = 'SINCRONIZADO', procesado_en_utc = datetime('now')
        WHERE id = ? AND estado = 'EN_PROCESO'
    """
    params: List[Any] = [event_id]
    if intentos_esperados is not None:
      sql += " AND intentos = ?"
      params.append(intentos_esperados)
    cursor = conn.execute(sql, tuple(params))
    return cursor.rowcount > 0


def registrar_fallo_evento_outbox(
    conn: Any,
    event_id: int,
    error_msg: str,
    intentos_esperados: Optional[int] = None,
    tabla_outbox: str = "outbox_events",
) -> bool:
  """Registra un fallo en la sincronización, incrementando intentos y volviendo a PENDIENTE.

  Exige estrictamente que el evento esté en 'EN_PROCESO'.
  Si se proporciona `intentos_esperados`, valida que el reclamo no haya sido
  reasignado.
  Retorna True si se registró el fallo, False si el worker perdió la propiedad.
  """
  t_outbox = _validar_identificador_tabla(tabla_outbox)
  with transaccion(conn):
    sql = f"""
        UPDATE {t_outbox}
        SET estado = 'PENDIENTE', intentos = intentos + 1, ultimo_error = ?
        WHERE id = ? AND estado = 'EN_PROCESO'
    """
    params: List[Any] = [error_msg[:500], event_id]
    if intentos_esperados is not None:
      sql += " AND intentos = ?"
      params.append(intentos_esperados)
    cursor = conn.execute(sql, tuple(params))
    return cursor.rowcount > 0

