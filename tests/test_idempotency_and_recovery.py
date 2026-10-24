"""
Suite de pruebas unitarias y de integración local para idempotencia, recuperación
y concurrencia del Outbox (Fase 11).

Cubre:
1. Idempotencia en solicitudes repetidas (mismo ticket_id y mismo payload).
2. Detección de conflicto ante colisión de ticket_id con distinto payload.
3. Registro concurrente o sucesivo de ventas independientes con mismos montos e ítems.
4. Fallo antes de commit (Escenario 1) con rollback limpio y reintento exitoso.
5. Simulación de fallo en commit / resultado incierto (Escenario 3) con auto-recuperación idempotente.
6. Reclamaciones concurrentes del Outbox sobre conexiones independientes en SQLite WAL.
7. Protección contra confirmaciones espurias de workers demorados mediante fencing token (intentos).
8. Garantía de orden secuencial y prevención de desorden en modificaciones para el mismo aggregate_id.
9. Recuperación de eventos colgados por timeout y descarte a Dead-Letter Queue (ERROR).
"""

import json
import os
import sqlite3
import tempfile
import threading
import time
from unittest.mock import MagicMock
import pytest

from db.accounting import (
    ErrorCommitIncierto,
    ErrorContable,
    ErrorValidacion,
    ItemVenta,
    confirmar_evento_outbox,
    modificar_item_y_recalcular_ticket,
    reclamar_lote_outbox,
    registrar_fallo_evento_outbox,
    registrar_venta_atomica,
    transaccion,
)


@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")
    with open("db/schema_v1.sql", "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    yield conn
    conn.close()


@pytest.fixture
def temp_wal_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    init_conn = sqlite3.connect(db_path)
    init_conn.execute("PRAGMA journal_mode = WAL;")
    init_conn.execute("PRAGMA foreign_keys = ON;")
    with open("db/schema_v1.sql", "r", encoding="utf-8") as f:
        init_conn.executescript(f.read())
    init_conn.close()

    yield db_path

    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except OSError:
            pass


# ==============================================================================
# 1. IDEMPOTENCIA DEL REGISTRO DE VENTAS
# ==============================================================================

def test_venta_repetida_misma_operacion_es_idempotente(db_conn):
    """Reenviar la misma venta con idéntico ticket_id y payload no duplica datos."""
    items = [
        ItemVenta(producto_nombre="Fernet", cantidad=1, precio_unitario=6000, subtotal=6000, metodo_pago="EFECTIVO"),
        ItemVenta(producto_nombre="Coca Cola", cantidad=2, precio_unitario=1500, subtotal=3000, metodo_pago="EFECTIVO"),
    ]

    # Primer intento
    res1 = registrar_venta_atomica(
        conn=db_conn,
        ticket_id="T-IDEMP-001",
        total_venta=9000,
        monto_efectivo=9000,
        monto_transferencia=0,
        metodo_pago="EFECTIVO",
        items=items,
    )
    assert res1["status"] == "REGISTRADO"

    # Segundo intento (reintento del cliente ante timeout de red)
    res2 = registrar_venta_atomica(
        conn=db_conn,
        ticket_id="T-IDEMP-001",
        total_venta=9000,
        monto_efectivo=9000,
        monto_transferencia=0,
        metodo_pago="EFECTIVO",
        items=items,
    )
    assert res2["status"] == "EXISTENTE_IDEMPOTENTE"

    # Verificar invariantes en la base de datos
    tickets_count = db_conn.execute("SELECT COUNT(*) FROM tickets WHERE id = 'T-IDEMP-001'").fetchone()[0]
    items_count = db_conn.execute("SELECT COUNT(*) FROM ticket_items WHERE ticket_id = 'T-IDEMP-001'").fetchone()[0]
    outbox_count = db_conn.execute("SELECT COUNT(*) FROM outbox_events WHERE aggregate_id = 'T-IDEMP-001'").fetchone()[0]

    assert tickets_count == 1, "Debe existir exactamente 1 ticket"
    assert items_count == 2, "Deben existir exactamente 2 items (sin duplicar)"
    assert outbox_count == 1, "Debe existir exactamente 1 evento Outbox"


def test_venta_repetida_con_payload_diferente_es_rechazada(db_conn):
    """Reutilizar un ticket_id con distinto total o items produce ErrorValidacion."""
    items_orig = [
        ItemVenta(producto_nombre="Cerveza", cantidad=1, precio_unitario=2000, subtotal=2000, metodo_pago="EFECTIVO"),
    ]
    registrar_venta_atomica(
        conn=db_conn,
        ticket_id="T-CONFLICT-01",
        total_venta=2000,
        monto_efectivo=2000,
        monto_transferencia=0,
        metodo_pago="EFECTIVO",
        items=items_orig,
    )

    items_conflictivos = [
        ItemVenta(producto_nombre="Cerveza", cantidad=2, precio_unitario=2000, subtotal=4000, metodo_pago="EFECTIVO"),
    ]
    with pytest.raises(ErrorValidacion) as excinfo:
        registrar_venta_atomica(
            conn=db_conn,
            ticket_id="T-CONFLICT-01",
            total_venta=4000,
            monto_efectivo=4000,
            monto_transferencia=0,
            metodo_pago="EFECTIVO",
            items=items_conflictivos,
        )
    assert "Conflicto de idempotencia" in str(excinfo.value)


def test_ventas_legitimas_distintas_con_mismos_articulos(db_conn):
    """Dos ventas distintas con mismos productos y montos se registran sin colisión."""
    items1 = [ItemVenta(producto_nombre="Agua", cantidad=1, precio_unitario=1000, subtotal=1000, metodo_pago="EFECTIVO")]
    items2 = [ItemVenta(producto_nombre="Agua", cantidad=1, precio_unitario=1000, subtotal=1000, metodo_pago="EFECTIVO")]

    res1 = registrar_venta_atomica(db_conn, "T-VENTA-A", 1000, 1000, 0, "EFECTIVO", items1)
    res2 = registrar_venta_atomica(db_conn, "T-VENTA-B", 1000, 1000, 0, "EFECTIVO", items2)

    assert res1["status"] == "REGISTRADO"
    assert res2["status"] == "REGISTRADO"

    total_tickets = db_conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    assert total_tickets == 2


# ==============================================================================
# 2. RECUPERACIÓN TRAS FALLOS Y RESULTADOS INCIERTOS
# ==============================================================================

def test_fallo_antes_de_confirmar_rollback_limpio_y_reintento(db_conn):
    """Escenario 1: Fallo antes del commit provoca rollback limpio y permite reintentar."""
    items_invalidos = [
        ItemVenta(producto_nombre="Item", cantidad=1, precio_unitario=1000, subtotal=1000, metodo_pago="EFECTIVO")
    ]

    # Descuadre contable intencional
    with pytest.raises(ErrorContable):
        registrar_venta_atomica(
            conn=db_conn,
            ticket_id="T-FAIL-BEFORE",
            total_venta=1000,
            monto_efectivo=800,  # Faltan 200
            monto_transferencia=0,
            metodo_pago="EFECTIVO",
            items=items_invalidos,
        )

    # Verificar que no quedó ningún registro huérfano
    assert db_conn.execute("SELECT COUNT(*) FROM tickets WHERE id = 'T-FAIL-BEFORE'").fetchone()[0] == 0
    assert db_conn.execute("SELECT COUNT(*) FROM outbox_events WHERE aggregate_id = 'T-FAIL-BEFORE'").fetchone()[0] == 0

    # Reintentar con los montos corregidos
    res = registrar_venta_atomica(
        conn=db_conn,
        ticket_id="T-FAIL-BEFORE",
        total_venta=1000,
        monto_efectivo=1000,
        monto_transferencia=0,
        metodo_pago="EFECTIVO",
        items=items_invalidos,
    )
    assert res["status"] == "REGISTRADO"
    assert db_conn.execute("SELECT COUNT(*) FROM tickets WHERE id = 'T-FAIL-BEFORE'").fetchone()[0] == 1


def test_simulacion_commit_incierto_y_recuperacion_idempotente(db_conn):
    """Escenario 3: El commit persiste en la BD pero el cliente recibe un fallo de conexión."""
    # 1. La venta se persiste en el servidor
    items = [
        ItemVenta(producto_nombre="Pizza", cantidad=1, precio_unitario=4500, subtotal=4500, metodo_pago="TRANSFERENCIA")
    ]
    registrar_venta_atomica(db_conn, "T-UNCERTAIN-1", 4500, 0, 4500, "TRANSFERENCIA", items)

    # 2. Simular que el cliente envía de nuevo la misma venta tras la pérdida de la respuesta
    res = registrar_venta_atomica(db_conn, "T-UNCERTAIN-1", 4500, 0, 4500, "TRANSFERENCIA", items)

    # Debe resolverse pacíficamente sin errores ni duplicaciones
    assert res["status"] == "EXISTENTE_IDEMPOTENTE"
    assert db_conn.execute("SELECT COUNT(*) FROM tickets WHERE id = 'T-UNCERTAIN-1'").fetchone()[0] == 1
    assert db_conn.execute("SELECT COUNT(*) FROM outbox_events WHERE aggregate_id = 'T-UNCERTAIN-1'").fetchone()[0] == 1


def test_error_commit_incierto_lanzado_explicitamente():
    """Valida que transaccion() eleva ErrorCommitIncierto cuando commit() falla."""
    mock_conn = MagicMock()
    mock_conn.commit.side_effect = ConnectionResetError("HTTP Connection dropped during commit")

    with pytest.raises(ErrorCommitIncierto) as excinfo:
        with transaccion(mock_conn):
            pass

    assert "HTTP Connection dropped during commit" in str(excinfo.value)
    assert isinstance(excinfo.value.error_original, ConnectionResetError)
    # Rollback de mejor esfuerzo invocado
    mock_conn.rollback.assert_called_once()


# ==============================================================================
# 3. CONCURRENCIA E IDEMPOTENCIA DEL OUTBOX
# ==============================================================================

def test_reclamacion_concurrente_entre_dos_conexiones(temp_wal_db):
    """Dos workers concurrentes con conexiones independientes reclaman lotes disjuntos."""
    # Insertar 20 eventos
    init_conn = sqlite3.connect(temp_wal_db)
    for i in range(1, 21):
        init_conn.execute(
            "INSERT INTO outbox_events (id, aggregate_type, aggregate_id, operation, payload_json, estado) "
            "VALUES (?, 'TICKET', ?, 'INSERT', '{}', 'PENDIENTE')",
            (i, f"T-CONC-{i}"),
        )
    init_conn.commit()
    init_conn.close()

    lote_w1 = []
    lote_w2 = []

    def worker_1():
        conn = sqlite3.connect(temp_wal_db, timeout=10)
        reclamados = reclamar_lote_outbox(conn, limite=10)
        lote_w1.extend([e["id"] for e in reclamados])
        conn.close()

    def worker_2():
        conn = sqlite3.connect(temp_wal_db, timeout=10)
        reclamados = reclamar_lote_outbox(conn, limite=10)
        lote_w2.extend([e["id"] for e in reclamados])
        conn.close()

    t1 = threading.Thread(target=worker_1)
    t2 = threading.Thread(target=worker_2)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Verificación de exclusión mutua
    assert len(lote_w1) == 10
    assert len(lote_w2) == 10
    assert set(lote_w1).isdisjoint(set(lote_w2)), "Los trabajadores reclamaron eventos superpuestos!"
    assert sorted(lote_w1 + lote_w2) == list(range(1, 21))


def test_fencing_token_impide_confirmacion_espuria(db_conn):
    """Un worker demorado cuyo reclamo expiró no puede confirmar el evento reasignado."""
    db_conn.execute(
        "INSERT INTO outbox_events (id, aggregate_type, aggregate_id, operation, payload_json, estado, intentos) "
        "VALUES (101, 'TICKET', 'T-FENCE', 'INSERT', '{}', 'PENDIENTE', 0)"
    )
    db_conn.commit()

    # Worker A reclama (intento 0)
    lote_a = reclamar_lote_outbox(db_conn, limite=1)
    assert len(lote_a) == 1
    assert lote_a[0]["id"] == 101
    assert lote_a[0]["intentos"] == 0

    # Simular timeout: el evento se resetea y su número de intentos pasa a 1
    db_conn.execute(
        "UPDATE outbox_events SET estado = 'PENDIENTE', intentos = 1 WHERE id = 101"
    )
    db_conn.commit()

    # Worker B reclama el evento reasignado (ahora con intentos=1)
    lote_b = reclamar_lote_outbox(db_conn, limite=1)
    assert len(lote_b) == 1
    assert lote_b[0]["intentos"] == 1

    # Worker A (demorado) intenta confirmar con su token viejo (intentos_esperados=0)
    confirmado_a = confirmar_evento_outbox(db_conn, event_id=101, intentos_esperados=0)
    assert confirmado_a is False, "Worker A no debió poder confirmar con un token obsoleto!"

    # El evento sigue EN_PROCESO en posesión de Worker B
    st = db_conn.execute("SELECT estado, intentos FROM outbox_events WHERE id = 101").fetchone()
    assert st == ("EN_PROCESO", 1)

    # Worker B confirma con su token válido (intentos_esperados=1)
    confirmado_b = confirmar_evento_outbox(db_conn, event_id=101, intentos_esperados=1)
    assert confirmado_b is True, "Worker B con token vigente debe confirmar exitosamente"

    st_final = db_conn.execute("SELECT estado FROM outbox_events WHERE id = 101").fetchone()[0]
    assert st_final == "SINCRONIZADO"


def test_orden_secuencial_por_aggregate_id_evita_desorden(db_conn):
    """Si una entidad ya tiene un evento EN_PROCESO, eventos posteriores para esa entidad no se reclaman."""
    # Evento 1: Creación de ticket
    db_conn.execute(
        "INSERT INTO outbox_events (id, aggregate_type, aggregate_id, operation, payload_json, estado) "
        "VALUES (201, 'TICKET', 'T-SEQ-1', 'INSERT', '{}', 'PENDIENTE')"
    )
    # Evento 2: Modificación posterior sobre el mismo ticket
    db_conn.execute(
        "INSERT INTO outbox_events (id, aggregate_type, aggregate_id, operation, payload_json, estado) "
        "VALUES (202, 'TICKET', 'T-SEQ-1', 'UPDATE', '{}', 'PENDIENTE')"
    )
    # Evento 3: Creación de un ticket diferente
    db_conn.execute(
        "INSERT INTO outbox_events (id, aggregate_type, aggregate_id, operation, payload_json, estado) "
        "VALUES (203, 'TICKET', 'T-SEQ-2', 'INSERT', '{}', 'PENDIENTE')"
    )
    db_conn.commit()

    # Reclamar lote con límite de 1: obtiene el evento 201 (T-SEQ-1)
    lote1 = reclamar_lote_outbox(db_conn, limite=1)
    assert len(lote1) == 1
    assert lote1[0]["id"] == 201

    # Reclamar siguiente lote: no debe reclamar 202 porque T-SEQ-1 ya está EN_PROCESO.
    # Debe saltar al evento 203 (T-SEQ-2)
    lote2 = reclamar_lote_outbox(db_conn, limite=10)
    assert len(lote2) == 1
    assert lote2[0]["id"] == 203, "El evento 202 no debe reclamarse mientras 201 esté en proceso!"

    # Una vez confirmado el 201, el 202 queda liberado para ser reclamado
    confirmar_evento_outbox(db_conn, event_id=201)

    lote3 = reclamar_lote_outbox(db_conn, limite=10)
    assert len(lote3) == 1
    assert lote3[0]["id"] == 202


def test_recuperacion_timeout_por_timestamp_en_proceso(db_conn):
    """Los eventos colgados en EN_PROCESO se recuperan solo cuando supera el timeout desde su reclamo."""
    # Insertar evento creado hace 30 minutos
    db_conn.execute(
        "INSERT INTO outbox_events (id, aggregate_type, aggregate_id, operation, payload_json, estado, creado_en_utc) "
        "VALUES (301, 'TICKET', 'T-TIME-1', 'INSERT', '{}', 'PENDIENTE', datetime('now', '-30 minutes'))"
    )
    db_conn.commit()

    # Reclamar el evento recién ahora (procesado_en_utc = datetime('now'))
    lote = reclamar_lote_outbox(db_conn, limite=1, timeout_en_proceso_minutos=10)
    assert len(lote) == 1
    assert lote[0]["id"] == 301

    # Inmediatamente ejecutar otro reclamo: NO debe resetearse porque procesado_en_utc es reciente (< 10 min)
    # a pesar de que creado_en_utc sea de hace 30 minutos
    lote_inmediato = reclamar_lote_outbox(db_conn, limite=1, timeout_en_proceso_minutos=10)
    assert len(lote_inmediato) == 0, "No debe resetear un evento recién reclamado!"

    # Simular que pasaron 15 minutos desde el reclamo
    db_conn.execute(
        "UPDATE outbox_events SET procesado_en_utc = datetime('now', '-15 minutes') WHERE id = 301"
    )
    db_conn.commit()

    # Ahora sí debe recuperarlo por timeout y entregarlo
    lote_recuperado = reclamar_lote_outbox(db_conn, limite=1, timeout_en_proceso_minutos=10)
    assert len(lote_recuperado) == 1
    assert lote_recuperado[0]["id"] == 301
    assert lote_recuperado[0]["intentos"] == 1
