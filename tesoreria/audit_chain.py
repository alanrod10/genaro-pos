"""Pista de Auditoría Append-Only con Cadena de Hash Verificable — LAB-012.

Reglas e Invariantes:
- Cada evento de negocio genera un registro inmutable en `tesoreria_auditoria_eventos`.
- Se calcula un hash SHA-256 canónico encadenado al hash del evento inmediatamente anterior.
- El evento génesis (secuencia 1) utiliza un prev_hash de 64 ceros.
- Prohibición estricta de UPDATE y DELETE protegida por triggers de base de datos.
- Función de verificación de integridad matemática de toda la cadena en tiempo real.
"""

from datetime import datetime, timezone
import hashlib
import json
import sqlite3
from typing import Any, Dict, List, Optional
import uuid

GENESIS_HASH = "0" * 64


class ErrorAuditoria(Exception):
    """Excepción para fallas de integridad en la pista de auditoría."""
    pass


def canonical_json_dumps(obj: Any) -> str:
    """Serializa un objeto a JSON de forma canónica determinista (claves ordenadas, sin espacios)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_canonical_audit_hash(
    prev_canonical_hash: str,
    sequence_number: int,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    user_id: str,
    terminal_id: str,
    occurred_at: str,
    payload_json: str,
) -> str:
    """Calcula el hash SHA-256 canónico e inequívoco para un evento de auditoría.

    Utiliza una representación canónica JSON con claves ordenadas, asegurando
    separación inequívoca de campos y protección total contra colisiones de delimitadores.
    """
    try:
        parsed_payload = json.loads(payload_json) if isinstance(payload_json, str) else payload_json
    except Exception:
        parsed_payload = payload_json

    canonical_envelope = {
        "1_prev_hash": str(prev_canonical_hash),
        "2_sequence": int(sequence_number),
        "3_event_type": str(event_type),
        "4_aggregate_type": str(aggregate_type),
        "5_aggregate_id": str(aggregate_id),
        "6_user_id": str(user_id),
        "7_terminal_id": str(terminal_id),
        "8_occurred_at": str(occurred_at),
        "9_payload": parsed_payload,
    }
    canonical_bytes = canonical_json_dumps(canonical_envelope).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()


def recordAuditEvent(
    conn: sqlite3.Connection,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    user_id: str,
    payload: Dict[str, Any],
    terminal_id: str = "TERM-01",
    occurred_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Registra un evento de auditoría canónico encadenado criptográficamente.

    Esta operación debe ejecutarse dentro de la misma transacción que muta la entidad.
    """
    cursor = conn.cursor()

    # Obtener el último evento para la secuencia y el hash anterior
    cursor.execute(
        """
        SELECT sequence_number, canonical_hash
        FROM tesoreria_auditoria_eventos
        ORDER BY sequence_number DESC
        LIMIT 1
        """
    )
    last_row = cursor.fetchone()

    if last_row:
        sequence_number = last_row["sequence_number"] + 1
        prev_canonical_hash = last_row["canonical_hash"]
    else:
        sequence_number = 1
        prev_canonical_hash = GENESIS_HASH

    event_id = str(uuid.uuid4())
    now_utc = datetime.now(timezone.utc).isoformat()
    occ_at = occurred_at or now_utc
    payload_json = canonical_json_dumps(payload)

    # Cálculo canónico de hash inequívoco
    canonical_hash = compute_canonical_audit_hash(
        prev_canonical_hash=prev_canonical_hash,
        sequence_number=sequence_number,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=str(aggregate_id),
        user_id=user_id,
        terminal_id=terminal_id,
        occurred_at=occ_at,
        payload_json=payload_json,
    )

    cursor.execute(
        """
        INSERT INTO tesoreria_auditoria_eventos (
            event_id, event_type, aggregate_type, aggregate_id,
            sequence_number, occurred_at, recorded_at,
            user_id, terminal_id, payload_json,
            prev_canonical_hash, canonical_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            event_type,
            aggregate_type,
            str(aggregate_id),
            sequence_number,
            occ_at,
            now_utc,
            user_id,
            terminal_id,
            payload_json,
            prev_canonical_hash,
            canonical_hash,
        ),
    )

    event_dict = {
        "event_id": event_id,
        "event_type": event_type,
        "aggregate_type": aggregate_type,
        "aggregate_id": str(aggregate_id),
        "sequence_number": sequence_number,
        "occurred_at": occ_at,
        "recorded_at": now_utc,
        "user_id": user_id,
        "terminal_id": terminal_id,
        "payload": payload,
        "prev_canonical_hash": prev_canonical_hash,
        "canonical_hash": canonical_hash,
    }
    return event_dict


def verifyAuditChain(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Verifica exhaustivamente la integridad matemática de toda la cadena de auditoría.

    Retorna status: valid=True si todos los hashes y secuencias coinciden exactamente.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT event_id, event_type, aggregate_type, aggregate_id,
               sequence_number, occurred_at, recorded_at,
               user_id, terminal_id, payload_json,
               prev_canonical_hash, canonical_hash
        FROM tesoreria_auditoria_eventos
        ORDER BY sequence_number ASC
        """
    )
    filas = cursor.fetchall()
    cursor.close()

    total_events = len(filas)
    if total_events == 0:
        return {"valid": True, "total_events": 0, "errors": []}

    errors: List[str] = []
    expected_prev_hash = GENESIS_HASH

    for i, fila in enumerate(filas):
        expected_seq = i + 1
        seq = fila["sequence_number"]
        if seq != expected_seq:
            errors.append(f"Discontinuidad de secuencia en evento {fila['event_id']}: esperado {expected_seq}, encontrado {seq}")

        if fila["prev_canonical_hash"] != expected_prev_hash:
            errors.append(
                f"Ruptura de enlace en secuencia {seq}: prev_hash {fila['prev_canonical_hash']} no coincide con el hash previo {expected_prev_hash}"
            )

        # Recalcular hash canónico inequívoco
        recalculated_hash = compute_canonical_audit_hash(
            prev_canonical_hash=fila["prev_canonical_hash"],
            sequence_number=seq,
            event_type=fila["event_type"],
            aggregate_type=fila["aggregate_type"],
            aggregate_id=str(fila["aggregate_id"]),
            user_id=fila["user_id"],
            terminal_id=fila["terminal_id"],
            occurred_at=fila["occurred_at"],
            payload_json=fila["payload_json"],
        )

        if recalculated_hash != fila["canonical_hash"]:
            errors.append(
                f"Hash inválido en secuencia {seq}: almacenado {fila['canonical_hash']}, recalculado {recalculated_hash}"
            )

        expected_prev_hash = fila["canonical_hash"]

    return {
        "valid": len(errors) == 0,
        "total_events": total_events,
        "errors": errors,
    }


def queryAuditEvents(
    conn: sqlite3.Connection,
    aggregate_type: Optional[str] = None,
    aggregate_id: Optional[str] = None,
    event_type: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Consulta histórica de eventos de auditoría."""
    query = """
        SELECT event_id, event_type, aggregate_type, aggregate_id,
               sequence_number, occurred_at, recorded_at,
               user_id, terminal_id, payload_json,
               prev_canonical_hash, canonical_hash
        FROM tesoreria_auditoria_eventos
        WHERE 1=1
    """
    params: List[Any] = []
    if aggregate_type:
        query += " AND aggregate_type = ?"
        params.append(aggregate_type)
    if aggregate_id:
        query += " AND aggregate_id = ?"
        params.append(str(aggregate_id))
    if event_type:
        query += " AND event_type = ?"
        params.append(event_type)

    query += " ORDER BY sequence_number DESC LIMIT ?"
    params.append(limit)

    cursor = conn.cursor()
    cursor.execute(query, params)
    rows = cursor.fetchall()
    cursor.close()

    result = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = json.loads(d["payload_json"])
        except Exception:
            d["payload"] = {}
        result.append(d)
    return result
