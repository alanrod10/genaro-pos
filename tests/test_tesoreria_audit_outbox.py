"""Pruebas de Auditoría con Cadena de Hash y Outbox Transaccional — LAB-012 (AUD-001 a AUD-003 y OUT-001 a OUT-006)."""

import sqlite3
import pytest
from tesoreria.audit_chain import (
    GENESIS_HASH,
    ErrorAuditoria,
    queryAuditEvents,
    recordAuditEvent,
    verifyAuditChain,
)
from tesoreria.outbox import (
    ErrorOutbox,
    enqueueOutboxEvent,
    getOutboxStatus,
    processOutbox,
    retryDeadLetter,
)
from tesoreria.schema import inicializar_esquema_laboratorio


@pytest.fixture
def conn_lab():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    inicializar_esquema_laboratorio(conn)
    yield conn
    conn.close()


# =====================================================================
# AUDITORÍA (AUD-001 a AUD-003)
# =====================================================================

def test_aud_001_canonical_hash_chain_linking(conn_lab):
    """AUD-001: Encadenamiento canónico de hashes SHA-256 desde el bloque génesis."""
    # Evento 1
    ev1 = recordAuditEvent(
        conn=conn_lab,
        event_type="FUND_CREATED",
        aggregate_type="FUND",
        aggregate_id="1110",
        user_id="alan",
        payload={"nombre": "Caja Principal", "saldo": 0},
    )
    assert ev1["sequence_number"] == 1
    assert ev1["prev_canonical_hash"] == GENESIS_HASH
    assert len(ev1["canonical_hash"]) == 64

    # Evento 2
    ev2 = recordAuditEvent(
        conn=conn_lab,
        event_type="ENTRY_REGISTERED",
        aggregate_type="MOVEMENT",
        aggregate_id="mov-100",
        user_id="alan",
        payload={"amount_cents": 5000},
    )
    assert ev2["sequence_number"] == 2
    assert ev2["prev_canonical_hash"] == ev1["canonical_hash"]

    # Evento 3
    ev3 = recordAuditEvent(
        conn=conn_lab,
        event_type="TRANSFER_CONFIRMED",
        aggregate_type="TRANSFER",
        aggregate_id="trf-200",
        user_id="supervisor",
        payload={"amount_cents": 2000},
    )
    assert ev3["sequence_number"] == 3
    assert ev3["prev_canonical_hash"] == ev2["canonical_hash"]


def test_aud_002_verify_audit_chain_integrity(conn_lab):
    """AUD-002: verifyAuditChain valida matemáticamente la integridad y detecta corrupciones."""
    recordAuditEvent(conn_lab, "EV1", "AGG", "1", "user", {"k": 1})
    recordAuditEvent(conn_lab, "EV2", "AGG", "2", "user", {"k": 2})
    recordAuditEvent(conn_lab, "EV3", "AGG", "3", "user", {"k": 3})

    # Cadena intacta
    res = verifyAuditChain(conn_lab)
    assert res["valid"] is True
    assert res["total_events"] == 3
    assert len(res["errors"]) == 0


def test_aud_003_inmutabilidad_triggers_auditoria(conn_lab):
    """AUD-003: Triggers prohíben estrictamente UPDATE y DELETE en la pista de auditoría."""
    ev = recordAuditEvent(conn_lab, "EV1", "AGG", "1", "user", {"k": 1})

    cursor = conn_lab.cursor()

    # Intento de modificar auditoría
    with pytest.raises(sqlite3.IntegrityError, match="La pista de auditoría es estrictamente inmutable"):
        cursor.execute("UPDATE tesoreria_auditoria_eventos SET user_id = 'hacker' WHERE event_id = ?", (ev["event_id"],))

    # Intento de borrar auditoría
    with pytest.raises(sqlite3.IntegrityError, match="Prohibido eliminar registros de la pista de auditoría"):
        cursor.execute("DELETE FROM tesoreria_auditoria_eventos WHERE event_id = ?", (ev["event_id"],))


# =====================================================================
# OUTBOX TRANSACCIONAL (OUT-001 a OUT-006)
# =====================================================================

def test_out_001_enqueue_outbox_event_in_same_transaction(conn_lab):
    """OUT-001: Encolado de eventos en estado PENDING dentro de la misma transacción de negocio."""
    ev = enqueueOutboxEvent(
        conn=conn_lab,
        event_type="MOVEMENT_POSTED",
        aggregate_id="mov-99",
        payload={"fund_code": "1110", "amount_cents": 1000},
    )
    assert ev["status"] == "PENDING"
    assert ev["event_type"] == "MOVEMENT_POSTED"

    status = getOutboxStatus(conn_lab)
    assert status["resumen"]["PENDING"] == 1
    assert status["resumen"]["PUBLISHED"] == 0


def test_out_002_process_outbox_publishes_events(conn_lab):
    """OUT-002: processOutbox entrega los eventos y los marca como PUBLISHED con timestamp."""
    enqueueOutboxEvent(conn_lab, "EV_A", "1", {"val": "A"})
    enqueueOutboxEvent(conn_lab, "EV_B", "2", {"val": "B"})

    delivered = []

    def subscriber(data):
        delivered.append(data["event_type"])
        return True

    res = processOutbox(conn_lab, consumer_id="WORKER_1", subscriber_handler=subscriber)
    assert res["published"] == 2
    assert delivered == ["EV_A", "EV_B"]

    status = getOutboxStatus(conn_lab)
    assert status["resumen"]["PUBLISHED"] == 2
    assert status["resumen"]["PENDING"] == 0


def test_out_003_consumption_deduplication_idempotency(conn_lab):
    """OUT-003: Deduplicación con tesoreria_eventos_consumidos impide doble ejecución de efectos."""
    ev = enqueueOutboxEvent(conn_lab, "CRITICAL_PAYMENT", "999", {"amount": 50000})

    call_count = 0

    def subscriber(data):
        nonlocal call_count
        call_count += 1
        return True

    # 1. Primer despacho
    res1 = processOutbox(conn_lab, consumer_id="SERVICE_WORKER", subscriber_handler=subscriber)
    assert res1["published"] == 1
    assert call_count == 1

    # 2. Simular reenvío forzado del mismo evento marcándolo FAILED temporalmente
    conn_lab.execute("UPDATE tesoreria_outbox SET status = 'FAILED' WHERE event_id = ?", (ev["event_id"],))

    # 3. Segundo despacho
    res2 = processOutbox(conn_lab, consumer_id="SERVICE_WORKER", subscriber_handler=subscriber)
    assert res2["skipped_dedup"] == 1
    assert call_count == 1  # El suscriptor NO fue invocado nuevamente


def test_out_004_progressive_retries_on_failure(conn_lab):
    """OUT-004: Reintentos progresivos incrementan retry_count y registran last_error."""
    enqueueOutboxEvent(conn_lab, "FAILING_EVENT", "1", {"data": "test"}, max_retries=3)

    def failing_subscriber(data):
        raise RuntimeError("Conexión rechazada por servicio destino")

    # Intento 1
    res1 = processOutbox(conn_lab, subscriber_handler=failing_subscriber)
    assert res1["failed"] == 1
    assert res1["dead_letter"] == 0

    cursor = conn_lab.cursor()
    cursor.execute("SELECT status, retry_count, last_error FROM tesoreria_outbox")
    r = cursor.fetchone()
    assert r["status"] == "FAILED"
    assert r["retry_count"] == 1
    assert "Conexión rechazada" in r["last_error"]


def test_out_005_transition_to_dead_letter_after_max_retries(conn_lab):
    """OUT-005: Eventos que superan max_retries pasan a estado DEAD_LETTER."""
    enqueueOutboxEvent(conn_lab, "UNRECOVERABLE", "1", {"data": "bad"}, max_retries=2)

    def failing_sub(data):
        return False

    # Intento 1 -> FAILED (retry_count = 1)
    processOutbox(conn_lab, subscriber_handler=failing_sub)
    # Intento 2 -> DEAD_LETTER (retry_count = 2 >= max_retries)
    res2 = processOutbox(conn_lab, subscriber_handler=failing_sub)

    assert res2["dead_letter"] == 1

    status = getOutboxStatus(conn_lab)
    assert status["resumen"]["DEAD_LETTER"] == 1
    assert status["resumen"]["FAILED"] == 0


def test_out_006_authorized_retry_dead_letter(conn_lab):
    """OUT-006: retryDeadLetter reinicia un evento en letra muerta con autorización expresa."""
    ev = enqueueOutboxEvent(conn_lab, "STUCK_EVENT", "1", {"data": "x"}, max_retries=1)

    # Llevar a DEAD_LETTER
    processOutbox(conn_lab, subscriber_handler=lambda d: False)
    assert getOutboxStatus(conn_lab)["resumen"]["DEAD_LETTER"] == 1

    # Reprocesamiento sin autorizante debe fallar
    with pytest.raises(ErrorOutbox, match="La autorización es obligatoria"):
        retryDeadLetter(conn_lab, event_id=ev["event_id"], authorized_by="")

    # Reprocesamiento autorizado
    res = retryDeadLetter(conn_lab, event_id=ev["event_id"], authorized_by="operador_admin")
    assert res["status"] == "PENDING"
    assert res["reprocessed_by"] == "operador_admin"

    assert getOutboxStatus(conn_lab)["resumen"]["PENDING"] == 1
    assert getOutboxStatus(conn_lab)["resumen"]["DEAD_LETTER"] == 0
