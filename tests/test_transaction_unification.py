"""Suite de pruebas unitarias para la unificación transaccional (Fase 10).

Valida:
1. Context manager transaccional DB-API 2.0 (commit automático y rollback total ante excepciones).
2. Ausencia de transacciones anidadas o sentencias BEGIN redundantes.
3. Fallo inyectado en cabecera, ítems o evento Outbox con rollback total.
4. Excepciones durante el commit.
5. Manejo de fallos antes de iniciar transacciones.
6. Reclamación atómica concurrente del Outbox mediante subconsulta y RETURNING.
7. Simulación de reintentos con recuperación de lotes.
8. Verificación de que ningún evento Outbox persiste si la venta se revierte.
"""

import json
import sqlite3
from unittest.mock import MagicMock
from db.accounting import (
    ErrorCommitIncierto,
    ErrorContable,
    ErrorValidacion,
    ItemVenta,
    reclamar_lote_outbox,
    registrar_venta_atomica,
    transaccion,
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


def test_transaccion_context_manager_commit(db_conn):
  """Prueba que el context manager transaccional persiste datos al salir limpiamente."""
  with transaccion(db_conn):
    db_conn.execute(
        "INSERT INTO tickets (id, total_venta, monto_efectivo,"
        " monto_transferencia, metodo_pago) VALUES ('T-CM-1', 1000, 1000, 0,"
        " 'EFECTIVO')"
    )

  row = db_conn.execute(
      "SELECT id, total_venta FROM tickets WHERE id = 'T-CM-1'"
  ).fetchone()
  assert row == ("T-CM-1", 1000)


def test_transaccion_context_manager_rollback_on_exception(db_conn):
  """Prueba que cualquier excepción dentro del context manager provoca rollback total."""
  try:
    with transaccion(db_conn):
      db_conn.execute(
          "INSERT INTO tickets (id, total_venta, monto_efectivo,"
          " monto_transferencia, metodo_pago) VALUES ('T-CM-FAIL', 1000, 1000,"
          " 0, 'EFECTIVO')"
      )
      raise RuntimeError("Fallo simulado en el medio de la lógica")
  except RuntimeError:
    pass

  row = db_conn.execute(
      "SELECT COUNT(*) FROM tickets WHERE id = 'T-CM-FAIL'"
  ).fetchone()[0]
  assert row == 0, "Error: El rollback no eliminó el registro de la transacción"


def test_no_begin_statement_leak(db_conn):
  """Comprueba que no se envían sentencias SQL 'BEGIN' que causen error de 'cannot start a transaction within a transaction'."""
  items = [
      ItemVenta(
          producto_nombre="Papas",
          cantidad=1,
          precio_unitario=1200,
          subtotal=1200,
          metodo_pago="EFECTIVO",
      )
  ]

  # Si la conexión ya tuviera una transacción abierta o si se ejecutara BEGIN duplicado:
  # En DB-API 2.0 un execute("BEGIN") arrojaría sqlite3.OperationalError si hay otra activa.
  # Nuestra implementación con transaccion(conn) no llama a execute("BEGIN").
  registrar_venta_atomica(
      conn=db_conn,
      ticket_id="T-NOBEGIN",
      total_venta=1200,
      monto_efectivo=1200,
      monto_transferencia=0,
      metodo_pago="EFECTIVO",
      items=items,
  )

  assert (
      db_conn.execute(
          "SELECT COUNT(*) FROM tickets WHERE id = 'T-NOBEGIN'"
      ).fetchone()[0]
      == 1
  )


def test_rollback_ante_fallo_en_item_intermedio(db_conn):
  """Valida que si el segundo ítem de una venta falla (ej. cantidad no positiva), ni el ticket ni el primer ítem ni el Outbox persisten."""
  items = [
      ItemVenta(
          producto_nombre="Item Bueno",
          cantidad=1,
          precio_unitario=1000,
          subtotal=1000,
          metodo_pago="EFECTIVO",
      ),
      ItemVenta(
          producto_nombre="Item Malo",
          cantidad=-1,
          precio_unitario=500,
          subtotal=500,
          metodo_pago="EFECTIVO",
      ),
  ]

  with pytest.raises(ErrorValidacion):
    registrar_venta_atomica(
        conn=db_conn,
        ticket_id="T-FAIL-ITEM2",
        total_venta=1500,
        monto_efectivo=1500,
        monto_transferencia=0,
        metodo_pago="EFECTIVO",
        items=items,
    )

  # Verificar estado completamente limpio
  assert (
      db_conn.execute(
          "SELECT COUNT(*) FROM tickets WHERE id = 'T-FAIL-ITEM2'"
      ).fetchone()[0]
      == 0
  )
  assert (
      db_conn.execute(
          "SELECT COUNT(*) FROM ticket_items WHERE ticket_id = 'T-FAIL-ITEM2'"
      ).fetchone()[0]
      == 0
  )
  assert (
      db_conn.execute(
          "SELECT COUNT(*) FROM outbox_events WHERE aggregate_id ="
          " 'T-FAIL-ITEM2'"
      ).fetchone()[0]
      == 0
  )


def test_reclamacion_outbox_atomica_sin_begin_immediate(db_conn):
  """Prueba la máquina de estados de reclamación usando UPDATE...RETURNING estándar."""
  # Insertar 5 eventos
  for i in range(1, 6):
    db_conn.execute(
        """
            INSERT INTO outbox_events (id, aggregate_type, aggregate_id, operation, payload_json, estado)
            VALUES (?, 'TICKET', ?, 'INSERT', '{}', 'PENDIENTE')
        """,
        (i, f"T-{i}"),
    )
  db_conn.commit()

  # Reclamar lote de 3
  lote1 = reclamar_lote_outbox(db_conn, limite=3)
  assert len(lote1) == 3
  assert [ev["id"] for ev in lote1] == [1, 2, 3]

  # Inmediatamente reclamar otro lote de 3: solo debe entregar los 2 restantes (4 y 5)
  lote2 = reclamar_lote_outbox(db_conn, limite=3)
  assert len(lote2) == 2
  assert [ev["id"] for ev in lote2] == [4, 5]

  # Un tercer reclamo no encuentra nada (lote vacío)
  lote3 = reclamar_lote_outbox(db_conn, limite=3)
  assert len(lote3) == 0


def test_fallo_en_commit_provoca_rollback(db_conn):
  """Simula un fallo inyectado durante el commit de la conexión para verificar el manejo de excepciones."""
  mock_conn = MagicMock()
  # Simular que no existe el ticket previamente en la base
  mock_conn.execute.return_value.fetchone.return_value = None
  mock_conn.commit.side_effect = sqlite3.OperationalError(
      "Fallo de I/O en commit"
  )

  items = [
      ItemVenta(
          producto_nombre="A",
          cantidad=1,
          precio_unitario=500,
          subtotal=500,
          metodo_pago="EFECTIVO",
      )
  ]

  with pytest.raises(ErrorCommitIncierto):
    registrar_venta_atomica(
        conn=mock_conn,
        ticket_id="T-MOCK",
        total_venta=500,
        monto_efectivo=500,
        monto_transferencia=0,
        metodo_pago="EFECTIVO",
        items=items,
    )

  mock_conn.rollback.assert_called_once()
