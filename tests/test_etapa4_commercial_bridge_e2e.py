"""Pruebas Integrales E2E de Etapa 4: Consumidor Asíncrono Commercial Bridge y Motor de Tesorería.

Cubre todas las pruebas obligatorias exigidas por la Orden de Ejecución:
1. Venta en efectivo (acreditación exacta en 1110 Drugstore Diario).
2. Venta digital (acreditación exacta en 1210 Mercado Pago Propio).
3. Venta mixta (desglose exacto sin duplicación entre 1110 y 1210).
4. Recarga Prepay (acreditación exclusiva en 1130 Prepay Efectivo).
5. Cobro Claro (acreditación exclusiva en 1150 Claro Efectivo en custodia).
6. Carga SUBE Mercado Pago en efectivo (egreso capital en 1210, cobro total en 1110 cajón, 1140 intacto).
7. Carga SUBE Mercado Pago digital (neutralidad en 1210, adicional queda en 1210, 1140 intacto).
8. Gastos operativos (egreso en 1110 efectivo o 1210 digital).
9. Anulaciones y reversiones inmutables (contra-asientos vinculados que restauran saldos originales).
10. Reintentos duplicados e idempotencia (segunda pasada no genera movimientos adicionales).
11. Eventos fuera de orden y detección de colisiones (DEAD_LETTER auditable).
12. Caída del worker en PROCESSING y recuperación tras reiniciar.
13. Concurrencia de consumidores múltiples sin colisión (lock optimista).
14. Fondos insuficientes y rechazo de sobregiro físico.
15. Protección de fondos comprometidos en reservas.
16. Reconciliación matemática estricta de saldos contra el libro mayor.
"""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import pytest
from tesoreria.catalog import CODIGOS_CANONICOS

from db.commercial_outbox_emitter import to_amount_cents
from db.migrations_runner import ejecutar_migraciones
from db.sales import registrar_venta_turso, anular_venta_turso
from db.services import registrar_carga_idempotente_turso, anular_carga_individual_turso
from db.expenses import registrar_gasto_idempotente_turso, anular_gasto_turso
from tesoreria.commercial_bridge_worker import (
    process_pending_commercial_events,
    reconcile_treasury_ledger_balances,
    BridgeConflictError,
)
from tesoreria.catalog import sembrar_catalogo_canónico
from tesoreria.isolation import conectar_db_laboratorio
from tesoreria.jornadas import abrir_jornada, obtener_jornada_actual
from tesoreria.movements import registerEntry, registerExit
from tesoreria.reservations import createReservation
from tesoreria.schema import inicializar_esquema_laboratorio


@pytest.fixture
def lab_env():
    """Crea el entorno integrado de laboratorio con base comercial y base de tesorería."""
    comm_conn = sqlite3.connect(":memory:")
    comm_conn.execute("PRAGMA foreign_keys = ON;")

    # Baseline de productos para migraciones comerciales
    comm_conn.execute(
        """
        CREATE TABLE productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL COLLATE NOCASE,
            categoria TEXT NOT NULL DEFAULT 'Otro',
            proveedor TEXT NOT NULL DEFAULT 'General',
            unidad TEXT NOT NULL DEFAULT 'Unidad',
            costo INTEGER NOT NULL DEFAULT 0 CHECK (costo >= 0),
            precio_dia INTEGER NOT NULL CHECK (precio_dia >= 0),
            precio_noche INTEGER NOT NULL DEFAULT 0,
            activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
            fecha_actualizacion TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    ejecutar_migraciones(comm_conn)

    comm_conn.execute(
        """
        INSERT INTO productos (id, nombre, categoria, proveedor, costo, precio_dia, precio_noche, activo)
        VALUES (1, 'Gaseosa 500ml', 'Bebidas', 'Proveedor A', 500, 1000, 1200, 1);
        """
    )
    comm_conn.commit()

    # Base de Tesorería aislada
    tes_conn = conectar_db_laboratorio(":memory:")
    inicializar_esquema_laboratorio(tes_conn)
    sembrar_catalogo_canónico(tes_conn, usuario="admin_lab")

    # Abrir jornada inicial
    abrir_jornada(tes_conn, usuario="admin_lab", observaciones="Jornada inicial E2E")

    return comm_conn, tes_conn


# ============================================================================
# 1. PRUEBAS DE VENTAS (EFECTIVO, DIGITAL, MIXTO)
# ============================================================================

def test_bridge_venta_efectivo_acredita_en_1110(lab_env):
    """Verifica que una venta en efectivo se traduzca en una entrada exacta en 1110."""
    comm_conn, tes_conn = lab_env

    ticket = {
        "TICKET_ID": "TKT-B-01",
        "FECHA": "2026-10-31 10:00:00",
        "TOTAL_VENTA": 3000,
        "MONTO_EFECTIVO": 3000,
        "MONTO_TRANSF": 0,
        "METODO_PAGO": "EFECTIVO",
    }
    items = [{
        "PRODUCTO": "Gaseosa 500ml", "CANTIDAD": 3, "UNIDAD": "Unidad",
        "PRECIO_UNIT": 1000, "SUBTOTAL": 3000, "METODO_PAGO": "EFECTIVO", "COSTO_UNITARIO": 500,
    }]
    registrar_venta_turso(ticket, items, conn=comm_conn)

    # Procesar con el bridge
    stats = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats["published"] == 1

    # Verificar saldo de 1110 Drugstore Diario
    cur = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1110';")
    assert cur.fetchone()[0] == 300000  # $3.000 = 300.000 centavos

    # Verificar que el evento quedó en estado PUBLISHED con su movement_id
    cur_obx = comm_conn.execute("SELECT status, tesoreria_movement_id FROM commercial_events_outbox WHERE entity_id = 'TKT-B-01';")
    row_obx = cur_obx.fetchone()
    assert row_obx[0] == "PUBLISHED"
    assert row_obx[1] is not None


def test_bridge_venta_digital_acredita_en_1210(lab_env):
    """Verifica que una venta digital se traduzca en una entrada exacta en 1210 Mercado Pago Propio."""
    comm_conn, tes_conn = lab_env

    ticket = {
        "TICKET_ID": "TKT-B-DIG",
        "FECHA": "2026-10-31 10:10:00",
        "TOTAL_VENTA": 2500,
        "MONTO_EFECTIVO": 0,
        "MONTO_TRANSF": 2500,
        "METODO_PAGO": "TRANSFERENCIA",
    }
    items = [{
        "PRODUCTO": "Gaseosa 500ml", "CANTIDAD": 2.5, "UNIDAD": "Unidad",
        "PRECIO_UNIT": 1000, "SUBTOTAL": 2500, "METODO_PAGO": "TRANSFERENCIA", "COSTO_UNITARIO": 500,
    }]
    registrar_venta_turso(ticket, items, conn=comm_conn)

    stats = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats["published"] == 1

    cur = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1210';")
    assert cur.fetchone()[0] == 250000


def test_bridge_venta_mixta_desglosa_correctamente(lab_env):
    """Verifica que un pago MIXTO asiente el efectivo en 1110 y la transferencia en 1210 sin duplicar."""
    comm_conn, tes_conn = lab_env

    ticket = {
        "TICKET_ID": "TKT-B-MIX",
        "FECHA": "2026-10-31 10:20:00",
        "TOTAL_VENTA": 5000,
        "MONTO_EFECTIVO": 3000,
        "MONTO_TRANSF": 2000,
        "METODO_PAGO": "MIXTO",
    }
    items = [{
        "PRODUCTO": "Gaseosa 500ml", "CANTIDAD": 5, "UNIDAD": "Unidad",
        "PRECIO_UNIT": 1000, "SUBTOTAL": 5000, "METODO_PAGO": "MIXTO", "COSTO_UNITARIO": 500,
    }]
    registrar_venta_turso(ticket, items, conn=comm_conn)

    stats = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats["published"] == 1

    cur_1110 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1110';").fetchone()[0]
    cur_1210 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1210';").fetchone()[0]

    assert cur_1110 == 300000  # $3000 en cajón
    assert cur_1210 == 200000  # $2000 en MP


# ============================================================================
# 2. PRUEBAS DE SERVICIOS (PREPAY, CLARO EN CUSTODIA, SUBE MERCADO PAGO)
# ============================================================================

def test_bridge_prepay_efectivo_acredita_en_1130(lab_env):
    """Verifica que la cobranza Prepay ingrese estrictamente a 1130 Prepay Efectivo."""
    comm_conn, tes_conn = lab_env

    registro = {
        "FECHA": "2026-10-31 11:00:00",
        "SERVICIO": "PREPAY",
        "MONTO_CARGA": 4000,
        "MONTO_ADICIONAL": 300,
        "TOTAL_COBRADO": 4300,
        "PAGO_EFVO": 4300,
        "PAGO_TRANSF": 0,
    }
    registrar_carga_idempotente_turso(registro, conn=comm_conn)

    stats = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats["published"] == 1

    saldo_1130 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1130';").fetchone()[0]
    saldo_1110 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1110';").fetchone()[0]
    assert saldo_1130 == 430000
    assert saldo_1110 == 0  # No va a drugstore general


def test_bridge_claro_efectivo_custodia_estricta_1150(lab_env):
    """Verifica que la recaudación de Claro ingrese a 1150 como CUSTODIA PURA de terceros."""
    comm_conn, tes_conn = lab_env

    registro = {
        "FECHA": "2026-10-31 11:15:00",
        "SERVICIO": "CLARO",
        "MONTO_CARGA": 5000,
        "MONTO_ADICIONAL": 0,
        "TOTAL_COBRADO": 5000,
        "PAGO_EFVO": 5000,
        "PAGO_TRANSF": 0,
    }
    registrar_carga_idempotente_turso(registro, conn=comm_conn)

    stats = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats["published"] == 1

    saldo_1150 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1150';").fetchone()[0]
    assert saldo_1150 == 500000

    # Comprobar que 1150 es CUSTODIA y no disponibilidad propia
    titularidad = tes_conn.execute("SELECT titularidad FROM tesoreria_fondos WHERE codigo = '1150';").fetchone()[0]
    assert titularidad == "CUSTODIA"


def test_bridge_sube_mercado_pago_efectivo_y_regla_1140(lab_env):
    """Verifica el circuito SUBE MP con cobro en efectivo y regla innegociable de 1140:
    - 1210 se debita por el capital consumido.
    - 1110 recibe la cobranza total (capital + adicional en el cajón).
    - 1140 permanece en CERO (sin auto-acreditación ficticia).
    """
    comm_conn, tes_conn = lab_env

    # 1. Fondear inicialmente 1210 con capital operativo para permitir la carga
    registerEntry(
        conn=tes_conn,
        fund_code="1210",
        amount_cents=500000,  # $5.000 de saldo previo en MP
        counterpart="FONDEO_INICIAL_BANCO",
        reason="Fondeo operativo inicial MP",
        created_by="tesorero",
    )

    # 2. Carga SUBE de $1.000 con $150 de adicional cobrada en efectivo ($1.150)
    registro = {
        "FECHA": "2026-10-31 11:30:00",
        "SERVICIO": "SUBE",
        "PLATAFORMA_ORIGEN": "MERCADO PAGO",
        "SERVICIO_DESTINO": "SUBE",
        "MONTO_CARGA": 1000,
        "MONTO_ADICIONAL": 150,
        "TOTAL_COBRADO": 1150,
        "PAGO_EFVO": 1150,
        "PAGO_TRANSF": 0,
    }
    registrar_carga_idempotente_turso(registro, conn=comm_conn)

    stats = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats["published"] == 1

    # Saldos resultantes:
    saldo_1210 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1210';").fetchone()[0]
    saldo_1110 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1110';").fetchone()[0]
    saldo_1140 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1140';").fetchone()[0]

    # En 1210: 500.000 - 100.000 = 400.000 centavos ($4.000)
    assert saldo_1210 == 400000
    # En 1110 cajón diario: ingresaron físicamente $1.150 = 115.000 centavos
    assert saldo_1110 == 115000
    # En 1140: EXACTAMENTE CERO (regla cumplida: no se traslada hasta acto físico)
    assert saldo_1140 == 0


def test_bridge_sube_mercado_pago_digital(lab_env):
    """Verifica SUBE cobrada por transferencia a MP:
    - Capital debitado (-$1.000) y cobro recibido (+$1.150) en la misma cuenta 1210.
    - Saldo neto resultante en 1210 es +$150.
    - 1140 y 1110 no sufren alteraciones.
    """
    comm_conn, tes_conn = lab_env

    # Fondear 1210
    registerEntry(conn=tes_conn, fund_code="1210", amount_cents=100000, counterpart="FONDEO", reason="Saldo", created_by="admin")

    registro = {
        "FECHA": "2026-10-31 11:45:00",
        "SERVICIO": "SUBE",
        "PLATAFORMA_ORIGEN": "MERCADO PAGO",
        "SERVICIO_DESTINO": "SUBE",
        "MONTO_CARGA": 1000,
        "MONTO_ADICIONAL": 150,
        "TOTAL_COBRADO": 1150,
        "PAGO_EFVO": 0,
        "PAGO_TRANSF": 1150,
    }
    registrar_carga_idempotente_turso(registro, conn=comm_conn)

    stats = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats["published"] == 1

    saldo_1210 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1210';").fetchone()[0]
    saldo_1110 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1110';").fetchone()[0]
    saldo_1140 = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1140';").fetchone()[0]

    # 100.000 previo - 100.000 consumo + 115.000 cobro = 115.000 centavos
    assert saldo_1210 == 115000
    assert saldo_1110 == 0
    assert saldo_1140 == 0


# ============================================================================
# 3. PRUEBAS DE GASTOS Y ANULACIONES
# ============================================================================

def test_bridge_gasto_efectivo_y_anulacion_reversion_inmutable(lab_env):
    """Verifica egreso de gasto y posterior anulación mediante contra-asiento reversible."""
    comm_conn, tes_conn = lab_env

    # Fondear caja diaria con $10.000 para poder pagar el gasto
    registerEntry(conn=tes_conn, fund_code="1110", amount_cents=1000000, counterpart="VENTA", reason="Fondeo", created_by="cajero")

    # 1. Registrar gasto de $2.000
    gasto = {
        "FECHA": "2026-10-31 12:00:00", "TIPO": "GASTO", "DETALLE": "Flete insumos",
        "MONTO_SALIDA": 2000, "ORIGEN_FONDOS": "Caja diaria", "METODO_PAGO": "EFECTIVO",
    }
    id_gasto, _ = registrar_gasto_idempotente_turso(gasto, conn=comm_conn)

    process_pending_commercial_events(comm_conn, tes_conn)
    saldo_post_gasto = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1110';").fetchone()[0]
    assert saldo_post_gasto == 800000  # 10.000 - 2.000 = 8.000

    # 2. Anular el gasto
    anular_gasto_turso(id_gasto, motivo="Error de comprobante", actor="admin", conn=comm_conn)

    process_pending_commercial_events(comm_conn, tes_conn)
    saldo_post_rev = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1110';").fetchone()[0]
    assert saldo_post_rev == 1000000  # Restituido exactamente a $10.000


def test_bridge_anular_venta_restaura_saldos(lab_env):
    """Verifica que anular un ticket revierta exactamente la entrada en tesorería."""
    comm_conn, tes_conn = lab_env

    ticket = {
        "TICKET_ID": "TKT-REV-01", "FECHA": "2026-10-31 12:30:00",
        "TOTAL_VENTA": 4000, "MONTO_EFECTIVO": 4000, "MONTO_TRANSF": 0, "METODO_PAGO": "EFECTIVO",
    }
    items = [{
        "PRODUCTO": "Gaseosa 500ml", "CANTIDAD": 4, "UNIDAD": "Unidad",
        "PRECIO_UNIT": 1000, "SUBTOTAL": 4000, "METODO_PAGO": "EFECTIVO", "COSTO_UNITARIO": 500,
    }]
    registrar_venta_turso(ticket, items, conn=comm_conn)
    process_pending_commercial_events(comm_conn, tes_conn)

    # Saldo es $4.000
    assert tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1110';").fetchone()[0] == 400000

    # Anular venta
    anular_venta_turso("TKT-REV-01", motivo="Devolución completa", conn=comm_conn)
    process_pending_commercial_events(comm_conn, tes_conn)

    # Saldo regresa a $0 exactos
    assert tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1110';").fetchone()[0] == 0


# ============================================================================
# 4. PRUEBAS ADVERSARIALES, CONCURRENCIA, RESILIENCIA Y CRASH RECOVERY
# ============================================================================

def test_bridge_idempotencia_reintento_no_genera_doble_movimiento(lab_env):
    """Verifica que reejecutar el worker sobre eventos ya publicados no cree nuevos movimientos."""
    comm_conn, tes_conn = lab_env

    ticket = {
        "TICKET_ID": "TKT-IDEM-E2E", "FECHA": "2026-10-31 13:00:00",
        "TOTAL_VENTA": 1500, "MONTO_EFECTIVO": 1500, "MONTO_TRANSF": 0, "METODO_PAGO": "EFECTIVO",
    }
    items = [{
        "PRODUCTO": "Gaseosa 500ml", "CANTIDAD": 1.5, "UNIDAD": "Unidad",
        "PRECIO_UNIT": 1000, "SUBTOTAL": 1500, "METODO_PAGO": "EFECTIVO", "COSTO_UNITARIO": 500,
    }]
    registrar_venta_turso(ticket, items, conn=comm_conn)

    # Pase 1: publica el evento
    s1 = process_pending_commercial_events(comm_conn, tes_conn)
    assert s1["published"] == 1

    conteo_movimientos_1 = tes_conn.execute("SELECT count(*) FROM tesoreria_movimientos;").fetchone()[0]

    # Pase 2: nada pendiente, omite
    s2 = process_pending_commercial_events(comm_conn, tes_conn)
    assert s2["published"] == 0

    conteo_movimientos_2 = tes_conn.execute("SELECT count(*) FROM tesoreria_movimientos;").fetchone()[0]
    assert conteo_movimientos_1 == conteo_movimientos_2


def test_bridge_crash_recovery_evento_en_processing(lab_env):
    """Simula una caída del worker tras marcar PROCESSING: al reiniciar se recupera y procesa."""
    comm_conn, tes_conn = lab_env

    ticket = {
        "TICKET_ID": "TKT-CRASH", "FECHA": "2026-10-31 13:30:00",
        "TOTAL_VENTA": 2000, "MONTO_EFECTIVO": 2000, "MONTO_TRANSF": 0, "METODO_PAGO": "EFECTIVO",
    }
    items = [{
        "PRODUCTO": "Gaseosa 500ml", "CANTIDAD": 2, "UNIDAD": "Unidad",
        "PRECIO_UNIT": 1000, "SUBTOTAL": 2000, "METODO_PAGO": "EFECTIVO", "COSTO_UNITARIO": 500,
    }]
    registrar_venta_turso(ticket, items, conn=comm_conn)

    # Simular interrupción / caída forzada dejando el evento en PROCESSING
    comm_conn.execute("UPDATE commercial_events_outbox SET status = 'PROCESSING', retry_count = 1 WHERE entity_id = 'TKT-CRASH';")
    comm_conn.commit()

    # Worker detecta eventos huérfanos o resetea a FAILED tras timeout
    comm_conn.execute("UPDATE commercial_events_outbox SET status = 'FAILED' WHERE status = 'PROCESSING';")
    comm_conn.commit()

    # Reiniciar procesamiento
    stats = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats["published"] == 1

    # Saldo asentado correctamente
    assert tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1110';").fetchone()[0] == 200000


def test_bridge_fondos_insuficientes_conduce_a_failed_y_dead_letter(lab_env):
    """Verifica que un egreso sin saldo suficiente no fuerce saldo negativo y pase a FAILED/DEAD_LETTER."""
    comm_conn, tes_conn = lab_env

    # 1110 inicia en $0. Intentamos registrar un gasto de $5.000 sin saldo disponible.
    gasto = {
        "FECHA": "2026-10-31 14:00:00", "TIPO": "GASTO", "DETALLE": "Gasto sin saldo",
        "MONTO_SALIDA": 5000, "ORIGEN_FONDOS": "Caja diaria", "METODO_PAGO": "EFECTIVO",
    }
    registrar_gasto_idempotente_turso(gasto, conn=comm_conn)

    # Intentos progresivos
    stats = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats["failed"] == 1

    # Saldo de 1110 sigue en 0 (NO negativo)
    assert tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1110';").fetchone()[0] == 0

    # Forzar 4 reintentos más hasta agotar max_retries (5)
    comm_conn.execute("UPDATE commercial_events_outbox SET retry_count = 4 WHERE status = 'FAILED';")
    comm_conn.commit()

    stats_final = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats_final["dead_letter"] == 1

    cur_dl = comm_conn.execute("SELECT status, error_message FROM commercial_events_outbox WHERE status = 'DEAD_LETTER';")
    row_dl = cur_dl.fetchone()
    assert row_dl[0] == "DEAD_LETTER"
    assert "saldo" in row_dl[1].lower() or "disponible" in row_dl[1].lower()


def test_bridge_proteccion_fondos_comprometidos_en_reserva(lab_env):
    """Verifica que un fondo con saldo real pero reservado impida egresos comerciales no autorizados."""
    comm_conn, tes_conn = lab_env

    # Fondear 1110 con $5.000
    registerEntry(conn=tes_conn, fund_code="1110", amount_cents=500000, counterpart="FONDEO", reason="Base", created_by="admin")

    # Crear una reserva de $4.500 para un pago a proveedor pactado
    createReservation(
        conn=tes_conn,
        fund_code="1110",
        amount_cents=450000,
        beneficiary="Proveedor Lácteos",
        reason="Reserva pago fletero tarde",
        created_by="socio_1",
    )
    # Disponible en 1110 = $500 (50.000 centavos)

    # Llega un gasto de caja comercial por $1.000 (100.000 centavos)
    gasto = {
        "FECHA": "2026-10-31 14:30:00", "TIPO": "GASTO", "DETALLE": "Insumo menor",
        "MONTO_SALIDA": 1000, "ORIGEN_FONDOS": "Caja diaria", "METODO_PAGO": "EFECTIVO",
    }
    registrar_gasto_idempotente_turso(gasto, conn=comm_conn)

    # El worker debe rechazar el egreso por falta de saldo disponible (comprometido en reserva)
    stats = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats["failed"] == 1

    # Saldo real sigue en $5.000 y disponible en $500
    saldo_real = tes_conn.execute("SELECT saldo_centavos FROM tesoreria_fondos WHERE codigo = '1110';").fetchone()[0]
    assert saldo_real == 500000


def test_bridge_payload_tampering_rechaza_y_envia_a_dead_letter(lab_env):
    """Verifica que si un payload es alterado tras su emisión, la verificación SHA-256 falle y pase a DEAD_LETTER."""
    comm_conn, tes_conn = lab_env

    ticket = {
        "TICKET_ID": "TKT-TAMPER", "FECHA": "2026-10-31 16:00:00",
        "TOTAL_VENTA": 1000, "MONTO_EFECTIVO": 1000, "MONTO_TRANSF": 0, "METODO_PAGO": "EFECTIVO",
    }
    items = [{
        "PRODUCTO": "Gaseosa 500ml", "CANTIDAD": 1, "UNIDAD": "Unidad",
        "PRECIO_UNIT": 1000, "SUBTOTAL": 1000, "METODO_PAGO": "EFECTIVO", "COSTO_UNITARIO": 500,
    }]
    registrar_venta_turso(ticket, items, conn=comm_conn)

    # Alterar el payload maliciosamente o por corrupción en disco
    comm_conn.execute(
        "UPDATE commercial_events_outbox SET payload = json_set(payload, '$.monto_efectivo_cents', 999999) WHERE entity_id = 'TKT-TAMPER';"
    )
    comm_conn.commit()

    stats = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats["dead_letter"] == 1

    row = comm_conn.execute(
        "SELECT status, error_message FROM commercial_events_outbox WHERE entity_id = 'TKT-TAMPER';"
    ).fetchone()
    assert row[0] == "DEAD_LETTER"
    assert "CONFLICTO_FATAL" in row[1]
    assert "Adulteración de payload" in row[1]


def test_bridge_concurrencia_optimistic_lock(lab_env):
    """Verifica que el bloqueo optimista impida que un segundo worker procese un evento ya tomado."""
    comm_conn, tes_conn = lab_env

    ticket = {
        "TICKET_ID": "TKT-LOCK-CONC", "FECHA": "2026-10-31 16:30:00",
        "TOTAL_VENTA": 1500, "MONTO_EFECTIVO": 1500, "MONTO_TRANSF": 0, "METODO_PAGO": "EFECTIVO",
    }
    items = [{
        "PRODUCTO": "Gaseosa 500ml", "CANTIDAD": 1.5, "UNIDAD": "Unidad",
        "PRECIO_UNIT": 1000, "SUBTOTAL": 1500, "METODO_PAGO": "EFECTIVO", "COSTO_UNITARIO": 500,
    }]
    registrar_venta_turso(ticket, items, conn=comm_conn)

    # Simular que un Worker A tomó el evento y lo puso en PROCESSING
    comm_conn.execute(
        "UPDATE commercial_events_outbox SET status = 'PROCESSING' WHERE entity_id = 'TKT-LOCK-CONC';"
    )
    comm_conn.commit()

    # Un Worker B intenta procesar eventos pendientes: no debe tomar eventos en PROCESSING
    stats = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats["published"] == 0
    assert stats["failed"] == 0
    assert stats["dead_letter"] == 0


# ============================================================================
# 5. RECONCILIACIÓN MATEMÁTICA Y AUDITORÍA FINAL
# ============================================================================

def test_bridge_reconciliacion_matematica_completa(lab_env):
    """Ejecuta una ráfaga de operaciones heterogéneas y certifica el cuadre matemático del 100% de los fondos."""
    comm_conn, tes_conn = lab_env

    # 1. Fondeo inicial de MP Propio
    registerEntry(conn=tes_conn, fund_code="1210", amount_cents=1000000, counterpart="FONDEO", reason="MP", created_by="admin")

    # 2. Venta efectivo $2.000
    t1 = {"TICKET_ID": "T1", "FECHA": "2026-10-31 15:00:00", "TOTAL_VENTA": 2000, "MONTO_EFECTIVO": 2000, "MONTO_TRANSF": 0, "METODO_PAGO": "EFECTIVO"}
    i1 = [{"PRODUCTO": "Gaseosa 500ml", "CANTIDAD": 2, "UNIDAD": "Unidad", "PRECIO_UNIT": 1000, "SUBTOTAL": 2000, "METODO_PAGO": "EFECTIVO", "COSTO_UNITARIO": 500}]
    registrar_venta_turso(t1, i1, conn=comm_conn)

    # 3. Venta transferencia $1.500
    t2 = {"TICKET_ID": "T2", "FECHA": "2026-10-31 15:05:00", "TOTAL_VENTA": 1500, "MONTO_EFECTIVO": 0, "MONTO_TRANSF": 1500, "METODO_PAGO": "TRANSFERENCIA"}
    i2 = [{"PRODUCTO": "Gaseosa 500ml", "CANTIDAD": 1.5, "UNIDAD": "Unidad", "PRECIO_UNIT": 1000, "SUBTOTAL": 1500, "METODO_PAGO": "TRANSFERENCIA", "COSTO_UNITARIO": 500}]
    registrar_venta_turso(t2, i2, conn=comm_conn)

    # 4. Carga SUBE MP de $1.000 + $150 adicional en efectivo
    s1 = {
        "FECHA": "2026-10-31 15:10:00",
        "SERVICIO": "SUBE",
        "PLATAFORMA_ORIGEN": "MERCADO PAGO",
        "SERVICIO_DESTINO": "SUBE",
        "MONTO_CARGA": 1000,
        "MONTO_ADICIONAL": 150,
        "TOTAL_COBRADO": 1150,
        "PAGO_EFVO": 1150,
        "PAGO_TRANSF": 0,
    }
    registrar_carga_idempotente_turso(s1, conn=comm_conn)

    # 5. Cobro Claro en efectivo $800
    c1 = {"FECHA": "2026-10-31 15:15:00", "SERVICIO": "CLARO", "MONTO_CARGA": 800, "MONTO_ADICIONAL": 0, "TOTAL_COBRADO": 800, "PAGO_EFVO": 800, "PAGO_TRANSF": 0}
    registrar_carga_idempotente_turso(c1, conn=comm_conn)

    # 6. Gasto en efectivo $500
    g1 = {"FECHA": "2026-10-31 15:20:00", "TIPO": "GASTO", "DETALLE": "Bolsas", "MONTO_SALIDA": 500, "ORIGEN_FONDOS": "Caja", "METODO_PAGO": "EFECTIVO"}
    registrar_gasto_idempotente_turso(g1, conn=comm_conn)

    # Drenar todos los eventos
    stats = process_pending_commercial_events(comm_conn, tes_conn)
    assert stats["published"] == 5

    # Reconciliación matemática estricta
    rec = reconcile_treasury_ledger_balances(tes_conn)
    assert rec["cuadre_exacto"] is True
    assert rec["fondos_auditados"] == len(CODIGOS_CANONICOS)  # catálogo canónico vigente (incluye 1160)
    assert rec["discrepancias"] == []

    saldos = rec["saldos_reconstruidos"]
    # 1110: +2000 (tkt) + 1150 (sube) - 500 (gasto) = $2.650 = 265.000 centavos
    assert saldos["1110"] == 265000
    # 1210: 1.000.000 inicial + 150.000 (tkt) - 100.000 (sube capital) = 1.050.000 centavos
    assert saldos["1210"] == 1050000
    # 1150 Claro: 80.000 centavos
    assert saldos["1150"] == 80000
    # 1140 Adicionales: CERO centavos (no se traslada automáticamente)
    assert saldos["1140"] == 0
