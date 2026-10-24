"""Pruebas unitarias de integridad, restricciones de base de datos, parseo

monetario y transacciones atómicas para Genaro POS.
Totalmente sintéticas, ejecutadas sobre SQLite local en memoria (aislado de
producción).
"""

from datetime import datetime, timezone
import json
import sqlite3
from zoneinfo import ZoneInfo
from db.normalization import (
    argentina_to_utc_str,
    calcular_reparto_prepay,
    parse_moneda_pesos,
    utc_to_argentina,
)
import pytest


@pytest.fixture
def db_conn():
  """Crea una conexión SQLite en memoria con foreign keys activadas y el esquema DDL V1."""
  conn = sqlite3.connect(":memory:")
  conn.execute("PRAGMA foreign_keys = ON;")

  with open("db/schema_v1.sql", "r", encoding="utf-8") as f:
    ddl = f.read()

  conn.executescript(ddl)
  yield conn
  conn.close()


# ---------------------------------------------------------
# 1. PRUEBAS DE PARSEO MONETARIO Y FECHAS
# ---------------------------------------------------------
def test_parse_moneda_localizada():
  # Casos limpios y formateados comunes en Sheets
  assert parse_moneda_pesos("$3,700.00") == (3700, True)
  assert parse_moneda_pesos("$2,900") == (2900, True)
  assert parse_moneda_pesos("1500") == (1500, True)
  assert parse_moneda_pesos(2500) == (2500, True)
  assert parse_moneda_pesos("$ 40,670.00") == (40670, True)

  # Casos con centavos reales o inválidos -> marca flag cuarentena (False)
  monto, valido = parse_moneda_pesos("$3,700.50")
  assert valido is False

  monto, valido = parse_moneda_pesos("texto_invalido")
  assert valido is False


def test_conversion_fechas_utc_local():
  # 2026-10-22 15:00:00 UTC equivale a 2026-10-22 12:00:00 en Jujuy (UTC-3)
  dt_local = utc_to_argentina("2026-10-22 15:00:00")
  assert dt_local.hour == 12
  assert dt_local.day == 22

  # Inversa: 10:00 local debe ser 13:00 UTC
  local_sample = datetime(
      2026, 10, 22, 10, 0, 0, tzinfo=ZoneInfo("America/Argentina/Jujuy")
  )
  utc_str = argentina_to_utc_str(local_sample)
  assert utc_str == "2026-10-22 13:00:00"


# ---------------------------------------------------------
# 2. PRUEBA DE PRODUCTOS: NOMBRES DUPLICADOS Y MARGEN
# ---------------------------------------------------------
def test_productos_nombres_duplicados_y_margen(db_conn):
  # Insertar dos productos con exactamente el mismo nombre pero distinto proveedor/costo
  db_conn.execute(
      """
        INSERT INTO productos (nombre, proveedor, costo, precio_dia)
        VALUES ('Galletas Pitusas 160gr', 'Distribuidora A', 1000, 1500)
    """
  )
  db_conn.execute(
      """
        INSERT INTO productos (nombre, proveedor, costo, precio_dia)
        VALUES ('Galletas Pitusas 160gr', 'Distribuidora B', 1200, 1500)
    """
  )
  db_conn.commit()

  cursor = db_conn.execute(
      "SELECT id, nombre, margen_ratio FROM productos ORDER BY id ASC"
  )
  filas = cursor.fetchall()
  assert len(filas) == 2
  assert filas[0][1] == filas[1][1]  # Mismo nombre permitido
  assert filas[0][2] == 0.5  # (1500-1000)/1000 = 0.5 (50%)
  assert filas[1][2] == 0.25  # (1500-1200)/1200 = 0.25 (25%)


# ---------------------------------------------------------
# 3. PRUEBA DE RESTRICCIONES CHECK DE PAGO EN TICKETS
# ---------------------------------------------------------
def test_balance_pago_tickets(db_conn):
  # Venta válida: Total 3000 = 2000 efvo + 1000 transf
  db_conn.execute(
      """
        INSERT INTO tickets (id, total_venta, monto_efectivo, monto_transferencia, metodo_pago)
        VALUES ('T-100', 3000, 2000, 1000, 'MIXTO')
    """
  )
  db_conn.commit()

  # Venta inválida: Total 3000 != 1000 efvo + 1000 transf (falta dinero) -> debe fallar CHECK
  with pytest.raises(sqlite3.IntegrityError):
    db_conn.execute(
        """
            INSERT INTO tickets (id, total_venta, monto_efectivo, monto_transferencia, metodo_pago)
            VALUES ('T-FAIL', 3000, 1000, 1000, 'MIXTO')
        """
    )


# ---------------------------------------------------------
# 4. PRUEBA DE CLAVE FORÁNEA: RECHAZO DE ÍTEM HUÉRFANO
# ---------------------------------------------------------
def test_foreign_key_rechaza_huerfano(db_conn):
  # Intentar insertar un ítem con un ticket_id inexistente debe disparar IntegrityError
  with pytest.raises(sqlite3.IntegrityError):
    db_conn.execute(
        """
            INSERT INTO ticket_items (ticket_id, producto_nombre, cantidad, precio_unitario, subtotal, metodo_pago)
            VALUES ('T-INEXISTENTE', 'Producto Test', 1, 500, 500, 'EFECTIVO')
        """
    )


# ---------------------------------------------------------
# 5. PRUEBA DE ATOMICIDAD COMPLETA (TICKET + ÍTEMS + OUTBOX)
# ---------------------------------------------------------
def test_creacion_atomica_y_rollback(db_conn):
  ticket_id = "T-20261022-001"

  # 1. Transacción Exitosa Completa
  db_conn.execute("BEGIN")
  db_conn.execute(
      """
        INSERT INTO tickets (id, total_venta, monto_efectivo, monto_transferencia, metodo_pago)
        VALUES (?, 1500, 1500, 0, 'EFECTIVO')
    """,
      (ticket_id,),
  )

  db_conn.execute(
      """
        INSERT INTO ticket_items (ticket_id, producto_nombre, cantidad, precio_unitario, subtotal, metodo_pago)
        VALUES (?, 'Alfajor', 2, 500, 1000, 'EFECTIVO')
    """,
      (ticket_id,),
  )
  db_conn.execute(
      """
        INSERT INTO ticket_items (ticket_id, producto_nombre, cantidad, precio_unitario, subtotal, metodo_pago)
        VALUES (?, 'Jugo', 1, 500, 500, 'EFECTIVO')
    """,
      (ticket_id,),
  )

  payload = json.dumps({"ticket_id": ticket_id, "total": 1500, "items": 2})
  db_conn.execute(
      """
        INSERT INTO outbox_events (aggregate_type, aggregate_id, operation, payload_json)
        VALUES ('TICKET', ?, 'INSERT', ?)
    """,
      (ticket_id, payload),
  )
  db_conn.commit()

  # Verificar inserción
  c_ticket = db_conn.execute(
      "SELECT COUNT(*) FROM tickets WHERE id = ?", (ticket_id,)
  ).fetchone()[0]
  c_items = db_conn.execute(
      "SELECT COUNT(*) FROM ticket_items WHERE ticket_id = ?", (ticket_id,)
  ).fetchone()[0]
  c_outbox = db_conn.execute(
      "SELECT COUNT(*) FROM outbox_events WHERE aggregate_id = ?", (ticket_id,)
  ).fetchone()[0]
  assert c_ticket == 1 and c_items == 2 and c_outbox == 1

  # 2. Prueba de Rollback ante error en el medio de la transacción
  bad_ticket_id = "T-BAD"
  try:
    db_conn.execute("BEGIN")
    db_conn.execute(
        """
            INSERT INTO tickets (id, total_venta, monto_efectivo, monto_transferencia, metodo_pago)
            VALUES (?, 1000, 1000, 0, 'EFECTIVO')
        """,
        (bad_ticket_id,),
    )
    # Insertar ítem con cantidad no positiva -> viola CHECK (cantidad > 0)
    db_conn.execute(
        """
            INSERT INTO ticket_items (ticket_id, producto_nombre, cantidad, precio_unitario, subtotal, metodo_pago)
            VALUES (?, 'Item Inválido', -1, 500, -500, 'EFECTIVO')
        """,
        (bad_ticket_id,),
    )
    db_conn.commit()
  except sqlite3.IntegrityError:
    db_conn.rollback()

  # Confirmar que NADA del ticket inválido persiste
  assert (
      db_conn.execute(
          "SELECT COUNT(*) FROM tickets WHERE id = ?", (bad_ticket_id,)
      ).fetchone()[0]
      == 0
  )


# ---------------------------------------------------------
# 6. PRUEBA DE IDEMPOTENCIA DEL OUTBOX
# ---------------------------------------------------------
def test_outbox_idempotencia(db_conn):
  event_id = "EVT-001"
  # Simular procesamiento de cola
  db_conn.execute(
      """
        INSERT INTO outbox_events (id, aggregate_type, aggregate_id, operation, payload_json, estado)
        VALUES (101, 'TICKET', 'T-100', 'INSERT', '{}', 'PENDIENTE')
    """
  )
  db_conn.commit()

  # Procesar primera vez
  db_conn.execute(
      """
        UPDATE outbox_events
        SET estado = 'SINCRONIZADO', procesado_en_utc = datetime('now')
        WHERE id = 101 AND estado = 'PENDIENTE'
    """
  )
  filas_afectadas = db_conn.total_changes

  # Reintento (idempotencia): si ya está SINCRONIZADO, no debe volver a actualizarse
  cursor = db_conn.execute(
      """
        UPDATE outbox_events
        SET estado = 'SINCRONIZADO', procesado_en_utc = datetime('now')
        WHERE id = 101 AND estado = 'PENDIENTE'
    """
  )
  assert cursor.rowcount == 0


# ---------------------------------------------------------
# 7. PRUEBA PARAMETRIZADA: POLÍTICAS PREPAY EN CONFLICTO
# ---------------------------------------------------------
@pytest.mark.parametrize(
    (
        "monto_carga, monto_adicional, pago_efvo, pago_transf, esperado_visor,"
        " esperado_caja"
    ),
    [
        # Caso 1: Carga 2000 + 100 adic = 2100 total. Cliente paga 1100 transf y 1000 efvo.
        # Visor: cap_transf = min(1100, 2000) = 1100. cap_efvo = 2000 - 1100 = 900.
        # Caja Proporcional: cap_efvo = round(2000 * 1000 / 2100) = 952. cap_transf = 2000 - 952 = 1048.
        (2000, 100, 1000, 1100, (900, 1100), (952, 1048)),
        # Caso 2: Carga 1000 + 100 adic = 1100. Cliente paga 100% efvo.
        (1000, 100, 1100, 0, (1000, 0), (1000, 0)),
        # Caso 3: Carga 1000 + 100 adic = 1100. Cliente paga 100% transf.
        (1000, 100, 0, 1100, (0, 1000), (0, 1000)),
    ],
)
def test_politicas_prepay_parametrizadas(
    monto_carga,
    monto_adicional,
    pago_efvo,
    pago_transf,
    esperado_visor,
    esperado_caja,
):
  # Política A (Visor / Sheets):
  res_visor = calcular_reparto_prepay(
      monto_carga, monto_adicional, pago_efvo, pago_transf, politica="VISOR"
  )
  assert res_visor == esperado_visor

  # Política B (Caja Proporcional):
  res_caja = calcular_reparto_prepay(
      monto_carga,
      monto_adicional,
      pago_efvo,
      pago_transf,
      politica="CAJA_PROPORCIONAL",
  )
  assert res_caja == esperado_caja
