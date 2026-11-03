"""Pruebas Adversariales de Cadena Criptográfica de Auditoría y Outbox Transaccional — SPRINT 4 FASE E & F.

Verificación de:
- Detección de manipulaciones en la cadena de auditoría (tamper detection):
  * Mutación de payload.
  * Eliminación de eslabón intermedio (discontinuidad de secuencia).
  * Alteración de prev_hash (ruptura de enlace criptográfico).
- Bloqueo activo mediante triggers contra UPDATE y DELETE en tesoreria_auditoria_eventos.
- Atomicidad estricta entre mutaciones de tesorería y encolamiento en Outbox.
- Idempotencia y deduplicación de consumo ante fallos de worker / caídas post-efecto.
- Retrying progresivo y transición determinista a DEAD_LETTER.
- Reprocesamiento controlado mediante retryDeadLetter.
"""

import json
import sqlite3
import pytest

from tesoreria.audit_chain import recordAuditEvent, verifyAuditChain
from tesoreria.catalog import sembrar_catalogo_canónico
from tesoreria.isolation import conectar_db_laboratorio
from tesoreria.jornadas import abrir_jornada
from tesoreria.movements import registerEntry
from tesoreria.outbox import enqueueOutboxEvent, processOutbox, retryDeadLetter
from tesoreria.schema import inicializar_esquema_laboratorio


@pytest.fixture
def conn_audit_lab(tmp_path):
    """Base de laboratorio con esquema inicializado."""
    db_file = tmp_path / "tesoreria_adv_audit_outbox.db"
    conn = conectar_db_laboratorio(str(db_file))
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)
    abrir_jornada(conn, usuario="auditor_lab")
    yield conn
    conn.close()


def test_audit_chain_tamper_detection_and_immutability(conn_audit_lab):
    """Verifica que la cadena de auditoría detecte cualquier manipulación arbitraria y que
    los triggers de la base de datos aborten cualquier UPDATE o DELETE."""
    # Generar 3 eventos legítimos
    e1 = recordAuditEvent(
        conn=conn_audit_lab,
        event_type="ENTRY_POSTED",
        aggregate_type="MOVEMENT",
        aggregate_id="mov-001",
        user_id="cajero",
        payload={"amount": 1000},
    )
    e2 = recordAuditEvent(
        conn=conn_audit_lab,
        event_type="TRANSFER_DISPATCHED",
        aggregate_type="TRANSFER",
        aggregate_id="trf-001",
        user_id="cajero",
        payload={"origin": "1110", "dest": "1210", "amount": 500},
    )
    e3 = recordAuditEvent(
        conn=conn_audit_lab,
        event_type="RESERVATION_CREATED",
        aggregate_type="RESERVATION",
        aggregate_id="rsv-001",
        user_id="cajero",
        payload={"amount": 200},
    )
    conn_audit_lab.commit()

    # 1. Verificar cadena válida inicial
    v_init = verifyAuditChain(conn_audit_lab)
    assert v_init["valid"] is True
    assert v_init["total_events"] >= 3  # incluye apertura de jornada
    assert len(v_init["errors"]) == 0

    # 2. Trigger de inmutabilidad: UPDATE directo abortado
    with pytest.raises(sqlite3.IntegrityError) as exc_upd:
        conn_audit_lab.execute(
            "UPDATE tesoreria_auditoria_eventos SET user_id = 'hacker' WHERE event_id = ?",
            (e2["event_id"],),
        )
    assert "INMUTABILIDAD" in str(exc_upd.value)

    # 3. Trigger de inmutabilidad: DELETE directo abortado
    with pytest.raises(sqlite3.IntegrityError) as exc_del:
        conn_audit_lab.execute(
            "DELETE FROM tesoreria_auditoria_eventos WHERE event_id = ?",
            (e3["event_id"],),
        )
    assert "INMUTABILIDAD" in str(exc_del.value)


def test_audit_chain_mathematical_tamper_detection(tmp_path):
    """En una base manipulada a bajo nivel (sin triggers activos), la función verifyAuditChain
    debe detectar matemáticamente cualquier alteración de contenido o secuencia."""
    db_file = tmp_path / "tamper_sim.db"
    conn = conectar_db_laboratorio(str(db_file))
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)
    abrir_jornada(conn, usuario="auditor")

    recordAuditEvent(conn, "TEST_1", "AGG", "1", "user", {"k": "v1"})
    recordAuditEvent(conn, "TEST_2", "AGG", "2", "user", {"k": "v2"})
    recordAuditEvent(conn, "TEST_3", "AGG", "3", "user", {"k": "v3"})
    conn.commit()

    # Desactivar temporalmente los triggers borrándolos para simular corrupción de archivo en reposo
    conn.execute("DROP TRIGGER trg_proteger_auditoria_eventos_update")
    conn.execute("DROP TRIGGER trg_proteger_auditoria_eventos_delete")

    # A. Alterar el payload de la fila 2
    conn.execute(
        "UPDATE tesoreria_auditoria_eventos SET payload_json = '{\"k\":\"corrupto\"}' WHERE sequence_number = 2"
    )
    conn.commit()

    v_tampered_payload = verifyAuditChain(conn)
    assert v_tampered_payload["valid"] is False
    assert any("Hash inválido en secuencia 2" in err for err in v_tampered_payload["errors"])

    # B. Restaurar payload pero romper el prev_hash de la fila 3
    conn.execute(
        "UPDATE tesoreria_auditoria_eventos SET prev_canonical_hash = 'abcdef0123456789' WHERE sequence_number = 3"
    )
    conn.commit()

    v_broken_link = verifyAuditChain(conn)
    assert v_broken_link["valid"] is False
    assert any("Ruptura de enlace en secuencia 3" in err for err in v_broken_link["errors"])

    # C. Eliminar una fila intermedia: discontinuidad de secuencia
    conn.execute("DELETE FROM tesoreria_auditoria_eventos WHERE sequence_number = 2")
    conn.commit()

    v_deleted_row = verifyAuditChain(conn)
    assert v_deleted_row["valid"] is False
    assert any("Discontinuidad de secuencia" in err for err in v_deleted_row["errors"])
    conn.close()


def test_outbox_atomic_rollback(conn_audit_lab):
    """Verifica que si una operación financiera falla o hace rollback, el evento de outbox también se descarte."""
    cur = conn_audit_lab.cursor()
    cur.execute("SELECT COUNT(*) FROM tesoreria_outbox")
    count_before = cur.fetchone()[0]

    # Iniciar bloque transaccional simulado que falla
    try:
        conn_audit_lab.execute("BEGIN TRANSACTION")
        # Encolar outbox
        enqueueOutboxEvent(conn_audit_lab, "SIMULATED_EVENT", "AGG-001", {"info": "should rollback"})
        # Provocar excepción deliberada
        raise RuntimeError("Fallo deliberado para probar rollback atómico")
    except RuntimeError:
        conn_audit_lab.rollback()

    cur.execute("SELECT COUNT(*) FROM tesoreria_outbox")
    count_after = cur.fetchone()[0]
    assert count_before == count_after
    cur.close()


def test_outbox_deduplication_and_consumer_crash_recovery(conn_audit_lab):
    """Verifica la deduplicación estricta cuando un consumidor ya ejecutó el efecto pero el outbox no se marcó."""
    ev = enqueueOutboxEvent(
        conn=conn_audit_lab,
        event_type="PAYMENT_PROCESSED",
        aggregate_id="pay-123",
        payload={"amount_cents": 50000},
    )
    conn_audit_lab.commit()

    # Simular caída: El consumidor ya guardó en tesoreria_eventos_consumidos
    conn_audit_lab.execute(
        "INSERT INTO tesoreria_eventos_consumidos (consumer_id, event_id, consumed_at) VALUES ('WORKER_A', ?, '2026-10-31T00:00:00Z')",
        (ev["event_id"],),
    )
    conn_audit_lab.commit()

    # Contador de ejecuciones de efecto
    efectos_ejecutados = []

    def handler_con_efecto(event):
        efectos_ejecutados.append(event["event_id"])
        return True

    # Procesar con el mismo consumer_id
    res = processOutbox(conn_audit_lab, consumer_id="WORKER_A", subscriber_handler=handler_con_efecto)

    # Debe detectar que ya fue consumido, no ejecutar el efecto de nuevo, y marcar como publicado
    assert len(efectos_ejecutados) == 0
    assert res["skipped_dedup"] == 1
    assert res["published"] == 1

    # Verificar estado en BD
    cur = conn_audit_lab.cursor()
    cur.execute("SELECT status FROM tesoreria_outbox WHERE event_id = ?", (ev["event_id"],))
    assert cur.fetchone()["status"] == "PUBLISHED"
    cur.close()


def test_outbox_progressive_retries_and_dead_letter(conn_audit_lab):
    """Verifica reintentos progresivos hasta alcanzar max_retries y posterior reprocesamiento manual."""
    ev = enqueueOutboxEvent(
        conn=conn_audit_lab,
        event_type="EXTERNAL_SYNC",
        aggregate_id="ext-999",
        payload={"data": "test"},
        max_retries=3,
    )
    conn_audit_lab.commit()

    # Handler que siempre falla
    def handler_falla(event):
        raise ConnectionError("Fallo de conexión simulado")

    # Intento 1: de PENDING pasa a FAILED con retry_count=1
    p1 = processOutbox(conn_audit_lab, subscriber_handler=handler_falla)
    assert p1["failed"] == 1
    assert p1["dead_letter"] == 0

    cur = conn_audit_lab.cursor()
    cur.execute("SELECT status, retry_count FROM tesoreria_outbox WHERE event_id = ?", (ev["event_id"],))
    row = cur.fetchone()
    assert row["status"] == "FAILED"
    assert row["retry_count"] == 1

    # Intento 2: retry_count=2
    p2 = processOutbox(conn_audit_lab, subscriber_handler=handler_falla)
    assert p2["failed"] == 1
    cur.execute("SELECT status, retry_count FROM tesoreria_outbox WHERE event_id = ?", (ev["event_id"],))
    row = cur.fetchone()
    assert row["status"] == "FAILED"
    assert row["retry_count"] == 2

    # Intento 3: alcanza max_retries=3 -> DEAD_LETTER
    p3 = processOutbox(conn_audit_lab, subscriber_handler=handler_falla)
    assert p3["dead_letter"] == 1
    cur.execute("SELECT status, retry_count FROM tesoreria_outbox WHERE event_id = ?", (ev["event_id"],))
    row = cur.fetchone()
    assert row["status"] == "DEAD_LETTER"
    assert row["retry_count"] == 3

    # Proceso posterior no debe intentar eventos en DEAD_LETTER
    p_idle = processOutbox(conn_audit_lab, subscriber_handler=handler_falla)
    assert p_idle["processed"] == 0

    # Reprocesamiento manual documentado: retryDeadLetter
    retried = retryDeadLetter(
        conn=conn_audit_lab,
        event_id=ev["event_id"],
        authorized_by="admin_outbox",
    )
    assert retried["status"] == "PENDING"
    cur.execute("SELECT retry_count FROM tesoreria_outbox WHERE event_id = ?", (ev["event_id"],))
    assert cur.fetchone()["retry_count"] == 0

    # Ahora handler exitoso procesa el evento
    def handler_ok(event):
        return True

    p_final = processOutbox(conn_audit_lab, subscriber_handler=handler_ok)
    assert p_final["published"] == 1

    cur.execute("SELECT status FROM tesoreria_outbox WHERE event_id = ?", (ev["event_id"],))
    assert cur.fetchone()["status"] == "PUBLISHED"
    cur.close()


def test_outbox_crash_between_effect_and_ack_demonstrates_consumer_idempotency_requirement(conn_audit_lab):
    """Demuestra formalmente el escenario de caída entre la ejecución del efecto y el registro del consumo:
    1. Consumidor recibe el evento de Outbox.
    2. Ejecuta su efecto externo (ej. mutación en sistema externo).
    3. Se interrumpe el proceso antes de registrar el consumo en tesoreria_eventos_consumidos.
    4. El evento se reintenta por el Outbox worker.
    
    Demostración:
    - La garantía de no duplicación del efecto depende de la IDEMPOTENCIA DEL RECEPTOR / CONSUMIDOR
      usando el `event_id` como clave de deduplicación.
    - Sin idempotencia del receptor, un sistema con entrega at-least-once causaría doble efecto.
    - Con receptor idempotente, el efecto secundario se ejecuta estrictamente UNA SOLA VEZ.
    """
    ev = enqueueOutboxEvent(
        conn=conn_audit_lab,
        event_type="EXTERNAL_SETTLEMENT",
        aggregate_id="settle-888",
        payload={"amount_cents": 75000},
    )
    conn_audit_lab.commit()
    event_id = ev["event_id"]

    # Simulación de un receptor externo con almacén de idempotencia por event_id
    receptor_externo = {
        "efectos_reales_ejecutados": 0,
        "eventos_procesados": set(),
    }

    primer_intento = True

    def handler_externo_con_caida(event):
        nonlocal primer_intento
        eid = event["event_id"]
        # Efecto externo idempotente: deduplica por event_id
        if eid not in receptor_externo["eventos_procesados"]:
            receptor_externo["efectos_reales_ejecutados"] += 1
            receptor_externo["eventos_procesados"].add(eid)

        # En el primer intento, se produce una caída abrupta post-efecto
        # antes de que processOutbox registre el consumo
        if primer_intento:
            primer_intento = False
            raise ConnectionResetError("Caída abrupta de red/proceso post-efecto")

        return True

    # Intento 1: se ejecuta el efecto externo (contador = 1), pero el proceso falla antes del commit de outbox
    p1 = processOutbox(conn_audit_lab, consumer_id="WORKER_EXT", subscriber_handler=handler_externo_con_caida)
    assert p1["failed"] == 1
    assert receptor_externo["efectos_reales_ejecutados"] == 1

    # Verificación en BD: tesoreria_eventos_consumidos NO tiene el registro porque cayó antes
    cur = conn_audit_lab.cursor()
    cur.execute("SELECT 1 FROM tesoreria_eventos_consumidos WHERE event_id = ?", (event_id,))
    assert cur.fetchone() is None
    cur.close()

    # Intento 2 (Reintento automático tras recuperación):
    p2 = processOutbox(conn_audit_lab, consumer_id="WORKER_EXT", subscriber_handler=handler_externo_con_caida)
    assert p2["published"] == 1

    # DEMOSTRACIÓN CRÍTICA:
    # Gracias a la idempotencia del consumidor con el event_id, los efectos reales siguen siendo 1 (CERO DOBLE EFECTO).
    assert receptor_externo["efectos_reales_ejecutados"] == 1

    # Ahora sí quedó registrado en tesoreria_eventos_consumidos
    cur = conn_audit_lab.cursor()
    cur.execute("SELECT 1 FROM tesoreria_eventos_consumidos WHERE event_id = ?", (event_id,))
    assert cur.fetchone() is not None
    cur.close()
