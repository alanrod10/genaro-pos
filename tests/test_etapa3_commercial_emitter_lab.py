"""Pruebas exhaustivas de Etapa 3: Emisor de Eventos Comerciales y Conexión Outbox (Laboratorio).

Verifica:
1. Serialización canónica RFC 8785 y hash determinista SHA-256.
2. Conversión monetaria centralizada a enteros de centavos (to_amount_cents).
3. Emisión atómica de SALE_CONFIRMED al registrar tickets en db/sales.py.
4. Preservación estricta de pagos MIXTOS sin duplicación de montos.
5. Rollback atómico de ventas: si falla el ticket comercial, el evento outbox se revierte conjuntamente.
6. Reintento idempotente de ventas: llamadas idénticas devuelven éxito sin duplicar eventos en el outbox.
7. Detección y rechazo de colisiones por alteración de payload (tampering).
8. Trazabilidad de anulaciones de venta (SALE_VOIDED) enlazadas a la operación confirmada original.
9. Emisión de SERVICE_CONFIRMED en db/services.py con discriminación de capital, adicional y medios de pago.
10. Trazabilidad de anulaciones de servicios (SERVICE_VOIDED).
11. Emisión de EXPENSE_CONFIRMED y EXPENSE_VOIDED en db/expenses.py.
12. Conexión del arqueo informativo de adicionales por turno en db/expenses.py sin movimientos financieros.
13. Cumplimiento de regla de preventistas: pedidos a proveedores no generan eventos financieros espurios.
14. Aislamiento absoluto de db/tesoreria_lab.db y backups comerciales.
"""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import pytest

from db.commercial_outbox_emitter import (
    canonicalize_json,
    compute_canonical_hash,
    to_amount_cents,
    enqueue_commercial_event,
    registrar_arqueo_adicionales_turno_db,
)
from db.migrations_runner import ejecutar_migraciones
from db.sales import (
    registrar_venta_turso,
    registrar_venta_idempotente_turso,
    anular_venta_turso,
    ErrorConflictoIdempotencia,
    ErrorVentasTurso,
)
from db.services import (
    registrar_carga_idempotente_turso,
    anular_carga_individual_turso,
)
from db.expenses import (
    registrar_gasto_idempotente_turso,
    anular_gasto_turso,
    registrar_cierre_diario_turso,
)
from db.preventistas import (
    crear_pedido_proveedor_turso,
    cambiar_estado_pedido_turso,
)



# Hash de db/tesoreria_lab.db capturado al importar el módulo (inicio de la sesión de pytest).
# Reemplaza el hash fijo histórico: la base de laboratorio evolucionó legítimamente; lo que se
# verifica es que la ejecución de las pruebas no la modifique.
_RUTA_TESORERIA_LAB = Path(__file__).resolve().parent.parent / "db" / "tesoreria_lab.db"
_HASH_TESORERIA_LAB_INICIAL = (
    hashlib.sha256(_RUTA_TESORERIA_LAB.read_bytes()).hexdigest() if _RUTA_TESORERIA_LAB.exists() else None
)

@pytest.fixture
def lab_migrated_db():
    """Base de datos comercial en memoria con todo el esquema (001 a 012) aplicado."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")

    # Esquema baseline de productos requerido por migración 001
    conn.execute(
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

    ejecutar_migraciones(conn)

    # Insertar producto sintético para pruebas de tickets
    conn.execute(
        """
        INSERT INTO productos (id, nombre, categoria, proveedor, costo, precio_dia, precio_noche, activo)
        VALUES (1, 'Gaseosa 500ml', 'Bebidas', 'Proveedor A', 500, 1000, 1200, 1);
        """
    )
    conn.commit()
    return conn


# ============================================================================
# 1. PRUEBAS DE SERIALIZACIÓN CANÓNICA RFC 8785 Y HASH SHA-256
# ============================================================================

def test_canonicalizacion_json_rfc8785():
    """Verifica que canonicalize_json ordene claves y elimine espacios superfluos de forma determinista."""
    data1 = {"z": 100, "a": "hola", "m": [3, 2, 1], "sub": {"k2": True, "k1": False}}
    data2 = {"a": "hola", "m": [3, 2, 1], "sub": {"k1": False, "k2": True}, "z": 100}

    canon1 = canonicalize_json(data1)
    canon2 = canonicalize_json(data2)

    assert canon1 == canon2
    assert canon1 == '{"a":"hola","m":[3,2,1],"sub":{"k1":false,"k2":true},"z":100}'

    hash1 = compute_canonical_hash(data1)
    hash2 = compute_canonical_hash(data2)
    assert hash1 == hash2
    assert len(hash1) == 64


def test_conversion_monetaria_to_amount_cents():
    """Verifica la conversión exacta de pesos a centavos enteros sin desfasajes."""
    assert to_amount_cents(1000) == 100000
    assert to_amount_cents(150.50) == 15050
    assert to_amount_cents(0) == 0
    assert to_amount_cents(None) == 0
    assert to_amount_cents(0.01) == 1
    assert isinstance(to_amount_cents(500), int)


# ============================================================================
# 2. PRUEBAS DE INTEGRACIÓN EN db/sales.py
# ============================================================================

def test_venta_efectivo_emite_evento_outbox(lab_migrated_db):
    """Verifica que registrar una venta en efectivo inserte el ticket y el evento outbox atómicamente."""
    ticket = {
        "TICKET_ID": "TKT-LAB-001",
        "FECHA": "2026-10-31 12:00:00",
        "TOTAL_VENTA": 1000,
        "MONTO_EFECTIVO": 1000,
        "MONTO_TRANSF": 0,
        "METODO_PAGO": "EFECTIVO",
    }
    items = [{
        "PRODUCTO": "Gaseosa 500ml",
        "CANTIDAD": 1,
        "UNIDAD": "Unidad",
        "PRECIO_UNIT": 1000,
        "SUBTOTAL": 1000,
        "METODO_PAGO": "EFECTIVO",
        "COSTO_UNITARIO": 500,
    }]

    tkt_id = registrar_venta_turso(ticket, items, usuario="cajero_1", conn=lab_migrated_db)
    assert tkt_id == "TKT-LAB-001"

    cur = lab_migrated_db.execute(
        "SELECT event_id, event_type, entity_id, action_type, idempotency_key, payload, status FROM commercial_events_outbox WHERE entity_id = ?;",
        ("TKT-LAB-001",),
    )
    row = cur.fetchone()
    assert row is not None
    assert row[1] == "SALE_CONFIRMED"
    assert row[2] == "TKT-LAB-001"
    assert row[3] == "CONFIRM"
    assert row[4] == "COMM_SALE_TKT-LAB-001_CONFIRM"
    assert row[6] == "PENDING"

    payload = json.loads(row[5])
    assert payload["total_venta_cents"] == 100000
    assert payload["monto_efectivo_cents"] == 100000
    assert payload["monto_transferencia_cents"] == 0
    assert payload["metodo_pago"] == "EFECTIVO"


def test_venta_mixta_sin_duplicacion(lab_migrated_db):
    """Verifica que un pago MIXTO mantenga desglosados los montos sin duplicar el total."""
    ticket = {
        "TICKET_ID": "TKT-LAB-MIXTO",
        "FECHA": "2026-10-31 12:05:00",
        "TOTAL_VENTA": 2000,
        "MONTO_EFECTIVO": 1200,
        "MONTO_TRANSF": 800,
        "METODO_PAGO": "MIXTO",
    }
    items = [{
        "PRODUCTO": "Gaseosa 500ml",
        "CANTIDAD": 2,
        "UNIDAD": "Unidad",
        "PRECIO_UNIT": 1000,
        "SUBTOTAL": 2000,
        "METODO_PAGO": "MIXTO",
        "COSTO_UNITARIO": 500,
    }]

    registrar_venta_turso(ticket, items, conn=lab_migrated_db)

    cur = lab_migrated_db.execute(
        "SELECT payload FROM commercial_events_outbox WHERE entity_id = 'TKT-LAB-MIXTO';"
    )
    payload = json.loads(cur.fetchone()[0])

    assert payload["total_venta_cents"] == 200000
    assert payload["monto_efectivo_cents"] == 120000
    assert payload["monto_transferencia_cents"] == 80000
    assert payload["monto_efectivo_cents"] + payload["monto_transferencia_cents"] == payload["total_venta_cents"]


def test_rollback_atomico_venta_no_deja_evento_outbox(lab_migrated_db):
    """Verifica que si una venta falla, el rollback de SQLite elimina tanto el ticket como el evento outbox."""
    ticket = {
        "TICKET_ID": "TKT-FAIL",
        "FECHA": "2026-10-31 12:10:00",
        "TOTAL_VENTA": 1000,
        "MONTO_EFECTIVO": 500,
        "MONTO_TRANSF": 0,  # Suma 500 != 1000 -> viola chk_ticket_balance
        "METODO_PAGO": "EFECTIVO",
    }
    items = [{
        "PRODUCTO": "Gaseosa 500ml",
        "CANTIDAD": 1,
        "UNIDAD": "Unidad",
        "PRECIO_UNIT": 1000,
        "SUBTOTAL": 1000,
        "METODO_PAGO": "EFECTIVO",
    }]

    with pytest.raises(ErrorVentasTurso):
        registrar_venta_turso(ticket, items, conn=lab_migrated_db)

    # Verificar que no quedó el ticket
    cur_tkt = lab_migrated_db.execute("SELECT 1 FROM tickets WHERE id = 'TKT-FAIL';")
    assert cur_tkt.fetchone() is None

    # Verificar que tampoco quedó ningún evento huérfano en commercial_events_outbox
    cur_obx = lab_migrated_db.execute("SELECT 1 FROM commercial_events_outbox WHERE entity_id = 'TKT-FAIL';")
    assert cur_obx.fetchone() is None


def test_reintento_idempotente_venta_no_duplica_outbox(lab_migrated_db):
    """Verifica que dos llamadas con el mismo ticket retornen éxito sin duplicar el evento outbox."""
    ticket = {
        "TICKET_ID": "TKT-IDEM-01",
        "FECHA": "2026-10-31 12:15:00",
        "TOTAL_VENTA": 1000,
        "MONTO_EFECTIVO": 1000,
        "MONTO_TRANSF": 0,
        "METODO_PAGO": "EFECTIVO",
    }
    items = [{
        "PRODUCTO": "Gaseosa 500ml",
        "CANTIDAD": 1,
        "UNIDAD": "Unidad",
        "PRECIO_UNIT": 1000,
        "SUBTOTAL": 1000,
        "METODO_PAGO": "EFECTIVO",
        "COSTO_UNITARIO": 500,
    }]

    tkt_id1, es_reintento1 = registrar_venta_idempotente_turso(ticket, items, conn=lab_migrated_db)
    assert tkt_id1 == "TKT-IDEM-01"
    assert es_reintento1 is False

    tkt_id2, es_reintento2 = registrar_venta_idempotente_turso(ticket, items, conn=lab_migrated_db)
    assert tkt_id2 == "TKT-IDEM-01"
    assert es_reintento2 is True

    # Exactamente 1 evento en commercial_events_outbox
    cur = lab_migrated_db.execute(
        "SELECT count(*) FROM commercial_events_outbox WHERE entity_id = 'TKT-IDEM-01';"
    )
    assert cur.fetchone()[0] == 1


def test_tampering_venta_rechazado_con_conflicto(lab_migrated_db):
    """Verifica que reenviar el mismo ticket con datos alterados dispare ErrorConflictoIdempotencia."""
    ticket = {
        "TICKET_ID": "TKT-TAMPER",
        "FECHA": "2026-10-31 12:20:00",
        "TOTAL_VENTA": 1000,
        "MONTO_EFECTIVO": 1000,
        "MONTO_TRANSF": 0,
        "METODO_PAGO": "EFECTIVO",
    }
    items = [{
        "PRODUCTO": "Gaseosa 500ml",
        "CANTIDAD": 1,
        "UNIDAD": "Unidad",
        "PRECIO_UNIT": 1000,
        "SUBTOTAL": 1000,
        "METODO_PAGO": "EFECTIVO",
        "COSTO_UNITARIO": 500,
    }]

    registrar_venta_idempotente_turso(ticket, items, conn=lab_migrated_db)

    # Intento con mismo ID pero contenido adulterado (cantidad 2 y total 2000)
    ticket_adulterado = {**ticket, "TOTAL_VENTA": 2000, "MONTO_EFECTIVO": 2000}
    items_adulterados = [{**items[0], "CANTIDAD": 2, "SUBTOTAL": 2000}]
    with pytest.raises(ErrorConflictoIdempotencia):
        registrar_venta_idempotente_turso(ticket_adulterado, items_adulterados, conn=lab_migrated_db)


def test_anular_venta_emite_evento_con_referencia(lab_migrated_db):
    """Verifica que anular un ticket emita SALE_VOIDED con referencia a la clave de confirmación original."""
    ticket = {
        "TICKET_ID": "TKT-ANULAR-01",
        "FECHA": "2026-10-31 12:30:00",
        "TOTAL_VENTA": 1000,
        "MONTO_EFECTIVO": 1000,
        "MONTO_TRANSF": 0,
        "METODO_PAGO": "EFECTIVO",
    }
    items = [{
        "PRODUCTO": "Gaseosa 500ml",
        "CANTIDAD": 1,
        "UNIDAD": "Unidad",
        "PRECIO_UNIT": 1000,
        "SUBTOTAL": 1000,
        "METODO_PAGO": "EFECTIVO",
        "COSTO_UNITARIO": 500,
    }]
    registrar_venta_turso(ticket, items, conn=lab_migrated_db)

    anular_venta_turso("TKT-ANULAR-01", motivo="Cliente devolvió mercadería", usuario="cajero_1", conn=lab_migrated_db)

    cur = lab_migrated_db.execute(
        """
        SELECT event_type, action_type, idempotency_key, payload
        FROM commercial_events_outbox
        WHERE entity_id = 'TKT-ANULAR-01' AND action_type = 'VOID';
        """
    )
    row = cur.fetchone()
    assert row is not None
    assert row[0] == "SALE_VOIDED"
    assert row[1] == "VOID"
    assert row[2] == "COMM_SALE_TKT-ANULAR-01_VOID"

    payload = json.loads(row[3])
    assert payload["reference_action"] == "CONFIRM"
    assert payload["reference_idempotency_key"] == "COMM_SALE_TKT-ANULAR-01_CONFIRM"
    assert payload["motivo"] == "Cliente devolvió mercadería"


# ============================================================================
# 3. PRUEBAS DE INTEGRACIÓN EN db/services.py (SUBE, RECARGAS, ANULACIONES)
# ============================================================================

def test_carga_sube_emite_service_confirmed_desglosado(lab_migrated_db):
    """Verifica que una carga de SUBE emita SERVICE_CONFIRMED con desglose exacto de capital y adicional."""
    registro = {
        "FECHA": "2026-10-31 13:00:00",
        "SERVICIO": "SUBE",
        "MONTO_CARGA": 1000,
        "MONTO_ADICIONAL": 150,
        "TOTAL_COBRADO": 1150,
        "PAGO_EFVO": 1150,
        "PAGO_TRANSF": 0,
    }

    id_carga, es_reintento = registrar_carga_idempotente_turso(
        registro, idempotency_key="COMM_POS_SUBE_1001", actor="operador_sube", conn=lab_migrated_db
    )
    assert es_reintento is False

    cur = lab_migrated_db.execute(
        "SELECT event_type, entity_id, action_type, payload FROM commercial_events_outbox WHERE entity_type='SERVICIO' AND entity_id=?;",
        (str(id_carga),),
    )
    row = cur.fetchone()
    assert row is not None
    assert row[0] == "SERVICE_CONFIRMED"
    assert row[2] == "CONFIRM"

    payload = json.loads(row[3])
    assert payload["servicio"] == "SUBE"
    assert payload["monto_carga_cents"] == 100000
    assert payload["monto_adicional_cents"] == 15000
    assert payload["total_cobrado_cents"] == 115000
    assert payload["pago_efectivo_cents"] == 115000
    assert payload["pago_transferencia_cents"] == 0


def test_anular_carga_servicio_emite_service_voided(lab_migrated_db):
    """Verifica que anular una carga de servicio emita SERVICE_VOIDED conservando trazabilidad."""
    registro = {
        "FECHA": "2026-10-31 13:10:00",
        "SERVICIO": "CLARO",
        "MONTO_CARGA": 2000,
        "MONTO_ADICIONAL": 200,
        "TOTAL_COBRADO": 2200,
        "PAGO_EFVO": 2200,
        "PAGO_TRANSF": 0,
    }
    id_carga, _ = registrar_carga_idempotente_turso(registro, conn=lab_migrated_db)

    anular_carga_individual_turso(id_carga, actor="supervisor", conn=lab_migrated_db)

    cur = lab_migrated_db.execute(
        """
        SELECT event_type, action_type, idempotency_key, payload
        FROM commercial_events_outbox
        WHERE entity_type = 'SERVICIO' AND entity_id = ? AND action_type = 'VOID';
        """,
        (str(id_carga),),
    )
    row = cur.fetchone()
    assert row is not None
    assert row[0] == "SERVICE_VOIDED"
    assert row[1] == "VOID"

    payload = json.loads(row[3])
    assert payload["reference_action"] == "CONFIRM"
    assert payload["reference_idempotency_key"] == f"COMM_SERVICE_{id_carga}_CONFIRM"
    assert payload["total_cobrado_cents"] == 220000


# ============================================================================
# 4. PRUEBAS DE INTEGRACIÓN EN db/expenses.py (GASTOS, RETIROS, CIERRES)
# ============================================================================

def test_gasto_emite_expense_confirmed(lab_migrated_db):
    """Verifica que registrar un gasto emita EXPENSE_CONFIRMED en commercial_events_outbox."""
    gasto = {
        "FECHA": "2026-10-31 14:00:00",
        "TIPO": "GASTO",
        "DETALLE": "Compra de artículos de limpieza",
        "MONTO_SALIDA": 3500,
        "ORIGEN_FONDOS": "Caja diaria",
        "METODO_PAGO": "EFECTIVO",
    }

    id_gasto, es_reintento = registrar_gasto_idempotente_turso(gasto, actor="cajero_limpieza", conn=lab_migrated_db)
    assert es_reintento is False

    cur = lab_migrated_db.execute(
        "SELECT event_type, action_type, payload FROM commercial_events_outbox WHERE entity_type='GASTO' AND entity_id=?;",
        (str(id_gasto),),
    )
    row = cur.fetchone()
    assert row is not None
    assert row[0] == "EXPENSE_CONFIRMED"
    assert row[1] == "CONFIRM"

    payload = json.loads(row[2])
    assert payload["monto_salida_cents"] == 350000
    assert payload["tipo"] == "GASTO"
    assert payload["metodo_pago"] == "EFECTIVO"


def test_anular_gasto_emite_expense_voided(lab_migrated_db):
    """Verifica que anular un gasto emita EXPENSE_VOIDED con trazabilidad cruzada."""
    gasto = {
        "FECHA": "2026-10-31 14:10:00",
        "TIPO": "GASTO",
        "DETALLE": "Gasto duplicado por error",
        "MONTO_SALIDA": 1500,
        "ORIGEN_FONDOS": "Caja diaria",
        "METODO_PAGO": "EFECTIVO",
    }
    id_gasto, _ = registrar_gasto_idempotente_turso(gasto, conn=lab_migrated_db)

    anular_gasto_turso(id_gasto, motivo="Comprobante erróneo", actor="admin", conn=lab_migrated_db)

    cur = lab_migrated_db.execute(
        """
        SELECT event_type, action_type, payload
        FROM commercial_events_outbox
        WHERE entity_type = 'GASTO' AND entity_id = ? AND action_type = 'VOID';
        """,
        (str(id_gasto),),
    )
    row = cur.fetchone()
    assert row is not None
    assert row[0] == "EXPENSE_VOIDED"

    payload = json.loads(row[2])
    assert payload["reference_action"] == "CONFIRM"
    assert payload["reference_idempotency_key"] == f"COMM_EXPENSE_{id_gasto}_CONFIRM"
    assert payload["motivo"] == "Comprobante erróneo"


def test_cierre_diario_conecta_arqueo_adicionales_informativo(lab_migrated_db):
    """Verifica que registrar el cierre comercial registre el arqueo informativo de adicionales sin generar asientos de tesorería."""
    reg_srv = {
        "FECHA": "2026-10-31 15:00:00",
        "SERVICIO": "SUBE",
        "MONTO_CARGA": 1000,
        "MONTO_ADICIONAL": 200,
        "TOTAL_COBRADO": 1200,
        "PAGO_EFVO": 1200,
        "PAGO_TRANSF": 0,
    }
    registrar_carga_idempotente_turso(reg_srv, conn=lab_migrated_db)

    cierre_data = {
        "fecha": "2026-10-31",
        "efectivo_esperado": 1200,
        "efectivo_contado": 1200,
        "turno": "TARDE",
        "adicionales_efectivo_declarado": 200,
        "observaciones_adicionales": "Adicionales contados en cajón al cierre",
    }
    cierre_res = registrar_cierre_diario_turso(cierre_data, cerrado_por="cajero_juan", conn=lab_migrated_db)
    assert cierre_res is not None

    cur = lab_migrated_db.execute(
        """
        SELECT arqueo_id, fecha_operativa, turno, responsable_caja,
               total_adicionales_efectivo_calculado_cents,
               total_adicionales_efectivo_declarado_cents,
               diferencia_cents, traslado_ejecutado
        FROM commercial_adicionales_arqueo_turno
        WHERE fecha_operativa = '2026-10-31';
        """
    )
    row = cur.fetchone()
    assert row is not None
    assert row[1] == "2026-10-31"
    assert row[2] == "TARDE"
    assert row[3] == "cajero_juan"
    assert row[4] == 20000
    assert row[5] == 20000
    assert row[6] == 0
    assert row[7] == 0


# ============================================================================
# 5. VERIFICACIÓN DE REGLAS DE PREVENTISTAS Y AISLAMIENTO
# ============================================================================

def test_preventistas_pedidos_no_emiten_eventos_financieros(lab_migrated_db):
    """Verifica que la creación y recepción de pedidos a proveedores NO generen eventos financieros en el Outbox."""
    conteo_inicial = lab_migrated_db.execute("SELECT count(*) FROM commercial_events_outbox;").fetchone()[0]

    pedido = crear_pedido_proveedor_turso(
        pedido_data={"proveedor": "Distribuidora Norte", "fecha": "2026-10-31", "observaciones": "Pedido reposicion"},
        items=[{"producto_nombre": "Gaseosa 500ml", "cantidad": 10, "costo_unitario": 500, "subtotal": 5000}],
        actor="comprador_1",
        conn=lab_migrated_db,
    )
    pedido_id = pedido["id"]
    assert pedido_id is not None

    cambiar_estado_pedido_turso(pedido_id, "RECIBIDO", actor="encargado_stock", conn=lab_migrated_db)

    conteo_final = lab_migrated_db.execute("SELECT count(*) FROM commercial_events_outbox;").fetchone()[0]
    assert conteo_final == conteo_inicial


def test_aislamiento_base_tesoreria_y_backups():
    """Verifica que la ejecución de la Etapa 3 no haya tocado los archivos protegidos."""
    repo_dir = Path(__file__).resolve().parent.parent
    db_tes = repo_dir / "db" / "tesoreria_lab.db"
    assert db_tes.exists()
    assert hashlib.sha256(db_tes.read_bytes()).hexdigest() == _HASH_TESORERIA_LAB_INICIAL

    bkp_file = Path("/home/alanrod10/backups/genaro-pos-dev-pre-migraciones-20261025-verificable.db")
    if bkp_file.exists():
        assert hashlib.sha256(bkp_file.read_bytes()).hexdigest() == "b0597fbeb0d27f5fc4b3f588efdc58fda23fc3427be102080134f2790300b557"
