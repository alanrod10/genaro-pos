"""Suite de pruebas sintéticas para endurecimiento contable, coherencia de

ventas/ítems y máquina de estados del Outbox para Genaro POS.
100% aisladas en SQLite local en memoria.
"""

import json
import sqlite3
from db.accounting import (
    ErrorContable,
    ErrorValidacion,
    ItemVenta,
    confirmar_evento_outbox,
    modificar_item_y_recalcular_ticket,
    reclamar_lote_outbox,
    registrar_fallo_evento_outbox,
    registrar_venta_atomica,
)
import pytest


@pytest.fixture
def db_conn():
  conn = sqlite3.connect(":memory:")
  conn.execute("PRAGMA foreign_keys = ON;")
  with open("db/schema_v1.sql", "r", encoding="utf-8") as f:
    conn.executescript(f.read())
  yield conn
  conn.close()


# -------------------------------------------------------------
# 1. PRUEBAS DE VENTA: COHERENCIA Y RECHAZOS CONTABLES
# -------------------------------------------------------------


def test_venta_un_item_valida(db_conn):
  items = [
      ItemVenta(
          producto_nombre="Coca Cola 1.5L",
          cantidad=1,
          precio_unitario=2500,
          subtotal=2500,
          metodo_pago="EFECTIVO",
      )
  ]
  registrar_venta_atomica(
      conn=db_conn,
      ticket_id="T-001",
      total_venta=2500,
      monto_efectivo=2500,
      monto_transferencia=0,
      metodo_pago="EFECTIVO",
      items=items,
  )

  t = db_conn.execute(
      "SELECT total_venta, monto_efectivo, monto_transferencia FROM tickets"
      " WHERE id = 'T-001'"
  ).fetchone()
  assert t == (2500, 2500, 0)

  it_count = db_conn.execute(
      "SELECT COUNT(*) FROM ticket_items WHERE ticket_id = 'T-001'"
  ).fetchone()[0]
  assert it_count == 1

  outbox = db_conn.execute(
      "SELECT aggregate_type, aggregate_id, operation, estado FROM"
      " outbox_events WHERE aggregate_id = 'T-001'"
  ).fetchone()
  assert outbox == ("TICKET", "T-001", "INSERT", "PENDIENTE")


def test_venta_multiples_items_pago_mixto(db_conn):
  items = [
      ItemVenta(
          producto_nombre="Galletas Diversion",
          cantidad=2,
          precio_unitario=1500,
          subtotal=3000,
          metodo_pago="MIXTO",
      ),
      ItemVenta(
          producto_nombre="Jugo Baggio",
          cantidad=1,
          precio_unitario=1000,
          subtotal=1000,
          metodo_pago="MIXTO",
      ),
  ]
  # Total = 4000 (2500 transf + 1500 efvo)
  registrar_venta_atomica(
      conn=db_conn,
      ticket_id="T-002",
      total_venta=4000,
      monto_efectivo=1500,
      monto_transferencia=2500,
      metodo_pago="MIXTO",
      items=items,
  )

  t = db_conn.execute(
      "SELECT total_venta, monto_efectivo, monto_transferencia FROM tickets"
      " WHERE id = 'T-002'"
  ).fetchone()
  assert t == (4000, 1500, 2500)


def test_rechazo_descuadre_pagos(db_conn):
  items = [
      ItemVenta(
          producto_nombre="Papas Lays",
          cantidad=1,
          precio_unitario=2000,
          subtotal=2000,
          metodo_pago="EFECTIVO",
      )
  ]
  # Total 2000 pero efvo 1000 + transf 500 = 1500 (descuadre)
  with pytest.raises(ErrorContable) as exc:
    registrar_venta_atomica(
        conn=db_conn,
        ticket_id="T-FAIL-PAGO",
        total_venta=2000,
        monto_efectivo=1000,
        monto_transferencia=500,
        metodo_pago="MIXTO",
        items=items,
    )
  assert "Inconsistencia en pagos" in str(exc.value)

  # Verificar que no quedó nada en la base
  assert (
      db_conn.execute(
          "SELECT COUNT(*) FROM tickets WHERE id = 'T-FAIL-PAGO'"
      ).fetchone()[0]
      == 0
  )


def test_rechazo_descuadre_subtotales_items(db_conn):
  items = [
      ItemVenta(
          producto_nombre="Item 1",
          cantidad=1,
          precio_unitario=1000,
          subtotal=1000,
          metodo_pago="EFECTIVO",
      ),
      ItemVenta(
          producto_nombre="Item 2",
          cantidad=1,
          precio_unitario=1000,
          subtotal=1000,
          metodo_pago="EFECTIVO",
      ),
  ]
  # Suma de items = 2000, pero declaramos total_venta = 2500
  with pytest.raises(ErrorContable) as exc:
    registrar_venta_atomica(
        conn=db_conn,
        ticket_id="T-FAIL-ITEMS",
        total_venta=2500,
        monto_efectivo=2500,
        monto_transferencia=0,
        metodo_pago="EFECTIVO",
        items=items,
    )
  assert "Inconsistencia de ítems" in str(exc.value)


# -------------------------------------------------------------
# 2. PRUEBAS DE EDICIÓN Y RECÁLCULO ATÓMICO EN HISTORIAL
# -------------------------------------------------------------


def test_modificar_item_recalculo_efectivo_primero(db_conn):
  # Venta inicial: 2 items de 2000 = 4000 total (2000 efvo + 2000 transf)
  items = [
      ItemVenta(
          producto_nombre="A",
          cantidad=1,
          precio_unitario=2000,
          subtotal=2000,
          metodo_pago="MIXTO",
      ),
      ItemVenta(
          producto_nombre="B",
          cantidad=1,
          precio_unitario=2000,
          subtotal=2000,
          metodo_pago="MIXTO",
      ),
  ]
  registrar_venta_atomica(
      db_conn, "T-EDIT", 4000, 2000, 2000, "MIXTO", items
  )

  item_id = db_conn.execute(
      "SELECT id FROM ticket_items WHERE ticket_id = 'T-EDIT' AND"
      " producto_nombre = 'A'"
  ).fetchone()[0]

  # Modificar ítem A: su subtotal baja de 2000 a 1000 (diff = 1000)
  # Regla EFECTIVO_PRIMERO: el efectivo baja de 2000 a 1000, transf queda en 2000. Nuevo total = 3000.
  new_tot, new_efvo, new_transf = modificar_item_y_recalcular_ticket(
      db_conn, item_id=item_id, nuevo_subtotal=1000, nueva_cantidad=0.5
  )

  assert new_tot == 3000
  assert new_efvo == 1000
  assert new_transf == 2000
  assert new_tot == (new_efvo + new_transf)

  # Verificar que el evento de modificación se registró en Outbox
  outbox = db_conn.execute(
      "SELECT operation, payload_json FROM outbox_events WHERE aggregate_id ="
      " 'T-EDIT' AND operation = 'UPDATE'"
  ).fetchone()
  assert outbox is not None
  payload = json.loads(outbox[1])
  assert payload["nuevo_total_ticket"] == 3000


def test_anulacion_total_al_llevar_items_a_cero(db_conn):
  items = [
      ItemVenta(
          producto_nombre="A",
          cantidad=1,
          precio_unitario=1500,
          subtotal=1500,
          metodo_pago="EFECTIVO",
      )
  ]
  registrar_venta_atomica(
      db_conn, "T-ZERO", 1500, 1500, 0, "EFECTIVO", items
  )

  item_id = db_conn.execute(
      "SELECT id FROM ticket_items WHERE ticket_id = 'T-ZERO'"
  ).fetchone()[0]

  new_tot, new_efvo, new_transf = modificar_item_y_recalcular_ticket(
      db_conn, item_id=item_id, nuevo_subtotal=0
  )
  assert new_tot == 0 and new_efvo == 0 and new_transf == 0

  ticket_anulado = db_conn.execute(
      "SELECT anulado, total_venta FROM tickets WHERE id = 'T-ZERO'"
  ).fetchone()
  assert ticket_anulado == (1, 0)


# -------------------------------------------------------------
# 3. PRUEBAS DEL OUTBOX: MÁQUINA DE ESTADOS Y RESILIENCIA
# -------------------------------------------------------------


def test_reclamar_lote_outbox_y_confirmacion(db_conn):
  # Insertar 3 eventos sintéticos
  for i in range(1, 4):
    db_conn.execute(
        """
            INSERT INTO outbox_events (id, aggregate_type, aggregate_id, operation, payload_json, estado)
            VALUES (?, 'TICKET', ?, 'INSERT', '{}', 'PENDIENTE')
        """,
        (i, f"T-{i}"),
    )
  db_conn.commit()

  # Reclamar lote de 2
  lote = reclamar_lote_outbox(db_conn, limite=2)
  assert len(lote) == 2
  assert lote[0]["id"] == 1
  assert lote[1]["id"] == 2

  # Verificar que quedaron en EN_PROCESO en la BD
  st1 = db_conn.execute(
      "SELECT estado FROM outbox_events WHERE id = 1"
  ).fetchone()[0]
  st3 = db_conn.execute(
      "SELECT estado FROM outbox_events WHERE id = 3"
  ).fetchone()[0]
  assert st1 == "EN_PROCESO"
  assert st3 == "PENDIENTE"

  # Confirmar el primero
  confirmar_evento_outbox(db_conn, event_id=1)
  st1_final = db_conn.execute(
      "SELECT estado FROM outbox_events WHERE id = 1"
  ).fetchone()[0]
  assert st1_final == "SINCRONIZADO"


def test_fallo_y_dead_letter_outbox(db_conn):
  event_id = 99
  db_conn.execute(
      """
        INSERT INTO outbox_events (id, aggregate_type, aggregate_id, operation, payload_json, estado, intentos)
        VALUES (?, 'CARGA', 'C-1', 'INSERT', '{}', 'PENDIENTE', 4)
    """,
      (event_id,),
  )
  db_conn.commit()

  # Reclamar el evento (intento 4)
  lote = reclamar_lote_outbox(db_conn, limite=1)
  assert len(lote) == 1
  assert lote[0]["id"] == event_id

  # Simular fallo de red hacia Sheets
  registrar_fallo_evento_outbox(
      db_conn, event_id=event_id, error_msg="HTTP 503 Service Unavailable"
  )

  # Ahora intentos = 5. Al siguiente reclamo debe pasar a 'ERROR' (Dead-Letter)
  lote2 = reclamar_lote_outbox(db_conn, limite=1)
  assert len(lote2) == 0  # No hay disponibles porque el 99 superó el límite

  st_dead = db_conn.execute(
      "SELECT estado, intentos FROM outbox_events WHERE id = ?", (event_id,)
  ).fetchone()
  assert st_dead == ("ERROR", 5)
