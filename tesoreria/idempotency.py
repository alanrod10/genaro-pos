"""Gestión de Idempotencia — GENARO TESORERÍA LABORATORIO.

Garantiza que reintentos de red, doble clic o solicitudes duplicadas con la misma clave
retornen exactamente la respuesta original sin duplicar efectos contables ni movimientos.
"""

import hashlib
import json
import sqlite3
from typing import Any, Dict, Optional, Tuple


class ErrorIdempotencia(Exception):
    """Excepción por conflicto de parámetros con la misma clave de idempotencia."""
    pass


def calcular_hash_solicitud(payload: Dict[str, Any]) -> str:
    """Calcula un hash SHA256 determinista de los parámetros de la solicitud."""
    payload_str = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(payload_str.encode("utf-8")).hexdigest()


def verificar_idempotencia(
    conn: sqlite3.Connection,
    idempotency_key: Optional[str],
    operation_type: str,
    payload_hash: str,
) -> Optional[Dict[str, Any]]:
    """Consulta si ya existe un resultado previo para la clave de idempotencia.

    Si existe con el mismo hash: retorna el response_json decodificado.
    Si existe con distinto hash: lanza ErrorIdempotencia por conflicto de payload.
    Si no existe o key es None: retorna None.
    """
    if not idempotency_key or not idempotency_key.strip():
        return None

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT resource_id, request_hash, response_json, status
        FROM tesoreria_idempotencia
        WHERE idempotency_key = ?
        """,
        (idempotency_key.strip(),),
    )
    fila = cursor.fetchone()
    cursor.close()

    if not fila:
        return None

    resource_id, guardado_hash, response_json_str, status = fila
    if guardado_hash != payload_hash:
        raise ErrorIdempotencia(
            f"Conflicto de Idempotencia: La clave '{idempotency_key}' ya fue utilizada con diferentes parámetros."
        )

    return json.loads(response_json_str)


def registrar_idempotencia(
    conn: sqlite3.Connection,
    idempotency_key: Optional[str],
    operation_type: str,
    resource_id: str,
    payload_hash: str,
    response_data: Dict[str, Any],
    status: str = "SUCCESS",
    created_at_iso: str = "",
) -> None:
    """Registra la clave de idempotencia y su respuesta dentro de la transacción activa."""
    if not idempotency_key or not idempotency_key.strip():
        return

    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO tesoreria_idempotencia (
            idempotency_key, operation_type, resource_id, request_hash, response_json, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            idempotency_key.strip(),
            operation_type,
            resource_id,
            payload_hash,
            json.dumps(response_data, sort_keys=True, default=str),
            status,
            created_at_iso,
        ),
    )
    cursor.close()


def get_idempotent_response(
    conn: sqlite3.Connection,
    key: Optional[str],
    payload: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Alias ergonómico de consulta de idempotencia con cálculo de hash automático."""
    if not key:
        return None
    h = calcular_hash_solicitud(payload)
    return verificar_idempotencia(conn, key, "GENERIC", h)


def record_idempotent_response(
    conn: sqlite3.Connection,
    key: Optional[str],
    operation_type: str,
    resource_id: str,
    request_payload: Dict[str, Any],
    response_payload: Dict[str, Any],
) -> None:
    """Alias ergonómico de registro de idempotencia con cálculo de hash y timestamp automático."""
    if not key:
        return
    from datetime import datetime, timezone
    h = calcular_hash_solicitud(request_payload)
    now_utc = datetime.now(timezone.utc).isoformat()
    registrar_idempotencia(
        conn=conn,
        idempotency_key=key,
        operation_type=operation_type,
        resource_id=resource_id,
        payload_hash=h,
        response_data=response_payload,
        status="SUCCESS",
        created_at_iso=now_utc,
    )
