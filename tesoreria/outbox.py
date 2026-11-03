"""Outbox Transaccional y Deduplicación de Consumo — LAB-012.

Reglas e Invariantes:
- `enqueueOutboxEvent` se ejecuta en la misma transacción SQLite que la mutación de negocio.
- Los eventos inician en estado `PENDING`.
- `processOutbox` entrega a suscriptores registrados asegurando entrega 'at-least-once'.
- `tesoreria_eventos_consumidos` garantiza deduplicación e idempotencia estricta por consumidor.
- Reintentos progresivos: si un evento falla repetidamente, se marca `DEAD_LETTER` tras alcanzar max_retries.
- `retryDeadLetter` permite el reprocesamiento manual y documentado de eventos en letra muerta.
- No se conecta aún al Libro Mayor comercial ni a Turso.
"""

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Callable, Dict, List, Optional
import uuid


class ErrorOutbox(Exception):
    """Excepción para fallas de outbox y entrega de eventos."""
    pass


def canonical_json_dumps(obj: Any) -> str:
    """Serializa determinísticamente."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def enqueueOutboxEvent(
    conn: sqlite3.Connection,
    event_type: str,
    aggregate_id: str,
    payload: Dict[str, Any],
    max_retries: int = 3,
) -> Dict[str, Any]:
    """Encola un evento en la tabla outbox dentro de la transacción activa."""
    event_id = str(uuid.uuid4())
    now_utc = datetime.now(timezone.utc).isoformat()
    payload_json = canonical_json_dumps(payload)

    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO tesoreria_outbox (
            event_id, event_type, aggregate_id, payload_json,
            status, retry_count, max_retries, created_at, scheduled_at
        ) VALUES (?, ?, ?, ?, 'PENDING', 0, ?, ?, ?)
        """,
        (event_id, event_type, str(aggregate_id), payload_json, max_retries, now_utc, now_utc),
    )

    return {
        "event_id": event_id,
        "event_type": event_type,
        "aggregate_id": str(aggregate_id),
        "payload": payload,
        "status": "PENDING",
        "created_at": now_utc,
    }


def processOutbox(
    conn: sqlite3.Connection,
    consumer_id: str = "LOCAL_LAB_WORKER",
    max_batch: int = 50,
    subscriber_handler: Optional[Callable[[Dict[str, Any]], bool]] = None,
) -> Dict[str, Any]:
    """Procesa eventos pendientes del outbox garantizando publicación e idempotencia de consumo.

    Si no se provee subscriber_handler, utiliza un despachador local no-op que confirma recepción.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, event_id, event_type, aggregate_id, payload_json,
               status, retry_count, max_retries, created_at
        FROM tesoreria_outbox
        WHERE status IN ('PENDING', 'FAILED')
        ORDER BY id ASC
        LIMIT ?
        """,
        (max_batch,),
    )
    rows = cursor.fetchall()

    processed_count = 0
    published_count = 0
    failed_count = 0
    dead_letter_count = 0
    skipped_dedup_count = 0

    now_utc = datetime.now(timezone.utc).isoformat()

    for r in rows:
        processed_count += 1
        event_id = r["event_id"]
        event_type = r["event_type"]
        aggregate_id = r["aggregate_id"]
        retry_count = r["retry_count"]
        max_retries = r["max_retries"]

        try:
            payload = json.loads(r["payload_json"])
        except Exception:
            payload = {}

        event_data = {
            "event_id": event_id,
            "event_type": event_type,
            "aggregate_id": aggregate_id,
            "payload": payload,
        }

        # 1. Comprobar si el consumidor ya procesó este evento (Deduplicación estricta)
        cursor.execute(
            """
            SELECT 1 FROM tesoreria_eventos_consumidos
            WHERE consumer_id = ? AND event_id = ?
            """,
            (consumer_id, event_id),
        )
        if cursor.fetchone():
            # Ya fue consumido por este consumidor: marcar como PUBLISHED sin re-ejecutar efectos
            cursor.execute(
                """
                UPDATE tesoreria_outbox
                SET status = 'PUBLISHED', published_at = ?
                WHERE event_id = ?
                """,
                (now_utc, event_id),
            )
            skipped_dedup_count += 1
            published_count += 1
            continue

        # 2. Despachar al suscriptor
        dispatch_success = True
        err_msg: Optional[str] = None
        if subscriber_handler is not None:
            try:
                dispatch_success = subscriber_handler(event_data)
            except Exception as ex:
                dispatch_success = False
                err_msg = str(ex)

        if dispatch_success:
            # Registrar consumo para prevenir duplicación futura
            cursor.execute(
                """
                INSERT OR IGNORE INTO tesoreria_eventos_consumidos (
                    consumer_id, event_id, consumed_at
                ) VALUES (?, ?, ?)
                """,
                (consumer_id, event_id, now_utc),
            )

            # Actualizar outbox a PUBLISHED
            cursor.execute(
                """
                UPDATE tesoreria_outbox
                SET status = 'PUBLISHED', published_at = ?, last_error = NULL
                WHERE event_id = ?
                """,
                (now_utc, event_id),
            )
            published_count += 1
        else:
            new_retries = retry_count + 1
            if new_retries >= max_retries:
                cursor.execute(
                    """
                    UPDATE tesoreria_outbox
                    SET status = 'DEAD_LETTER', retry_count = ?, last_error = ?
                    WHERE event_id = ?
                    """,
                    (new_retries, err_msg or "Excedido límite de reintentos", event_id),
                )
                dead_letter_count += 1
            else:
                cursor.execute(
                    """
                    UPDATE tesoreria_outbox
                    SET status = 'FAILED', retry_count = ?, last_error = ?
                    WHERE event_id = ?
                    """,
                    (new_retries, err_msg or "Fallo transitorio de despacho", event_id),
                )
                failed_count += 1

    conn.commit()
    cursor.close()

    return {
        "processed": processed_count,
        "published": published_count,
        "failed": failed_count,
        "dead_letter": dead_letter_count,
        "skipped_dedup": skipped_dedup_count,
    }


def retryDeadLetter(
    conn: sqlite3.Connection,
    event_id: str,
    authorized_by: str,
) -> Dict[str, Any]:
    """Permite el reprocesamiento autorizado de un evento en estado DEAD_LETTER."""
    if not authorized_by or not authorized_by.strip():
        raise ErrorOutbox("La autorización es obligatoria para reprocesar eventos en letra muerta.")

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT event_id, status FROM tesoreria_outbox
        WHERE event_id = ?
        """,
        (event_id,),
    )
    row = cursor.fetchone()
    if not row:
        cursor.close()
        raise ErrorOutbox(f"Evento {event_id} no encontrado en Outbox.")

    if row["status"] != "DEAD_LETTER":
        cursor.close()
        raise ErrorOutbox(f"El evento {event_id} no está en estado DEAD_LETTER (estado actual: {row['status']}).")

    now_utc = datetime.now(timezone.utc).isoformat()
    cursor.execute(
        """
        UPDATE tesoreria_outbox
        SET status = 'PENDING', retry_count = 0, last_error = NULL, scheduled_at = ?
        WHERE event_id = ?
        """,
        (now_utc, event_id),
    )
    conn.commit()
    cursor.close()

    return {
        "event_id": event_id,
        "status": "PENDING",
        "reprocessed_by": authorized_by.strip(),
        "reprocessed_at": now_utc,
    }


def getOutboxStatus(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Retorna contadores agregados del estado actual del Outbox."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT status, COUNT(*) as cantidad
        FROM tesoreria_outbox
        GROUP BY status
        """
    )
    filas = cursor.fetchall()
    cursor.close()

    counts = {"PENDING": 0, "PUBLISHED": 0, "FAILED": 0, "DEAD_LETTER": 0}
    for f in filas:
        counts[f["status"]] = f["cantidad"]

    return {
        "resumen": counts,
        "total_eventos": sum(counts.values()),
    }
