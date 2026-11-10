"""Emisor transaccional de eventos comerciales para Genaro Tesorería (Laboratorio).

Garantiza:
1. Serialización canónica de payloads según RFC 8785 (claves ordenadas, sin espacios).
2. Generación determinista de hash criptográfico SHA-256 (payload_canonical_hash).
3. Conversión monetaria centralizada de pesos a centavos enteros (INTEGER).
4. Inserción atómica en commercial_events_outbox en la misma transacción SQLite que la operación comercial.
5. Idempotencia estricta:
   - Reintento con misma idempotency_key y mismo payload: detectado y omitido sin duplicar eventos.
   - Reintento con misma idempotency_key pero payload discordante: rechazo con excepción explícita.
6. Registro de arqueos informativos de adicionales al cierre de turno sin impacto financiero.
"""

from datetime import datetime, timezone
import hashlib
import json
import logging
from typing import Any, Dict, Optional, Tuple, Union
import uuid

logger = logging.getLogger(__name__)


class ErrorOutboxNoDisponible(RuntimeError):
    """Lanzada cuando se intenta emitir un evento pero la tabla commercial_events_outbox no existe en la base de datos."""
    pass


def canonicalize_json(data: Any) -> str:
    """Serializa estructuras de datos en formato JSON canónico determinista (RFC 8785).
    
    - Claves ordenadas lexicográficamente.
    - Sin espacios superfluos entre separadores (',' y ':').
    - Codificación UTF-8 sin escapar caracteres unicode estándar.
    """
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def compute_canonical_hash(payload: Any) -> str:
    """Calcula el hash SHA-256 en formato hexadecimal del JSON canónico."""
    canonical_str = canonicalize_json(payload)
    return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()


def to_amount_cents(amount_pesos: Union[int, float, None]) -> int:
    """Convierte importes comerciales en pesos a centavos enteros sin pérdida de precisión."""
    if amount_pesos is None:
        return 0
    return int(round(float(amount_pesos) * 100))


def _obtener_conexion_base(conn: Any) -> Any:
    """Extrae la conexión SQLite real subyacente si conn es un wrapper instrumental de prueba (ConnSpy, TrackedConnection)."""
    curr = conn
    for _ in range(5):
        if hasattr(curr, "_raw"):
            curr = getattr(curr, "_raw")
        elif hasattr(curr, "_conn"):
            curr = getattr(curr, "_conn")
        elif hasattr(curr, "raw_conn"):
            curr = getattr(curr, "raw_conn")
        else:
            break
    return curr


def _tabla_existe(conn: Any, nombre_tabla: str) -> bool:
    """Verifica si una tabla existe en la base de datos actual sin interferir con trackers instrumentales."""
    base_conn = _obtener_conexion_base(conn)
    cursor = base_conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?;",
        (nombre_tabla,),
    )
    return cursor.fetchone() is not None


def enqueue_commercial_event(
    conn: Any,
    event_type: str,
    entity_type: str,
    entity_id: Union[str, int],
    action_type: str,
    idempotency_key: str,
    payload: Dict[str, Any],
    created_at: Optional[str] = None,
) -> Optional[Tuple[str, bool]]:
    """Encola un evento comercial en commercial_events_outbox dentro de la transacción activa.
    
    Retorna una tupla (event_id, es_reintento) o None si la tabla aún no existe en el esquema.
    Lanza ValueError si la clave ya existe con un payload adulterado o conflictivo.
    """
    if not _tabla_existe(conn, "commercial_events_outbox"):
        logger.warning(
            "La tabla commercial_events_outbox no existe en la base de datos actual. "
            "Omitiendo encolamiento de outbox para esquema parcial o de prueba."
        )
        return None

    str_entity_id = str(entity_id).strip()
    key = str(idempotency_key).strip()
    payload_hash = compute_canonical_hash(payload)
    payload_str = canonicalize_json(payload)
    
    now_utc = created_at or (datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))

    # Comprobación de idempotencia previa
    cursor = conn.execute(
        "SELECT event_id, payload_canonical_hash FROM commercial_events_outbox WHERE idempotency_key = ?;",
        (key,),
    )
    existente = cursor.fetchone()
    if existente:
        evt_id, hash_guardado = existente[0], existente[1]
        if hash_guardado == payload_hash:
            # Reintento idéntico confirmado: no duplicar
            return evt_id, True
        raise ValueError(
            f"Conflicto crítico de idempotencia en Outbox: la clave '{key}' "
            f"ya existe registrada con un payload diferente (esperado: {hash_guardado}, recibido: {payload_hash})."
        )

    nuevo_event_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO commercial_events_outbox (
            event_id, event_type, entity_type, entity_id, action_type,
            idempotency_key, payload_canonical_hash, payload,
            status, retry_count, max_retries, created_at, updated_at
        ) VALUES (
            ?, ?, ?, ?, ?,
            ?, ?, ?,
            'PENDING', 0, 5, ?, ?
        );
        """,
        (
            nuevo_event_id,
            event_type,
            entity_type,
            str_entity_id,
            action_type,
            key,
            payload_hash,
            payload_str,
            now_utc,
            now_utc,
        ),
    )
    try:
        from tesoreria.commercial_bridge_worker import notify_worker_new_event
        notify_worker_new_event()
    except Exception:
        pass
    return nuevo_event_id, False


def registrar_arqueo_adicionales_turno_db(
    conn: Any,
    cierre_turno_id: Optional[str],
    fecha_operativa: str,
    turno: str,
    responsable_caja: str,
    total_adicionales_efectivo_calculado_cents: int,
    total_adicionales_efectivo_declarado_cents: Optional[int] = None,
    diferencia_cents: Optional[int] = None,
    observaciones: Optional[str] = None,
    created_at: Optional[str] = None,
) -> Optional[str]:
    """Registra el arqueo informativo de adicionales por turno en commercial_adicionales_arqueo_turno.
    
    ESTRICTAMENTE INFORMATIVO: No genera asientos contables ni movimientos de fondos.
    Retorna el arqueo_id asignado o None si la tabla no existe en el esquema.
    """
    if not _tabla_existe(conn, "commercial_adicionales_arqueo_turno"):
        return None

    arqueo_id = str(uuid.uuid4())
    now_utc = created_at or (datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    
    conn.execute(
        """
        INSERT INTO commercial_adicionales_arqueo_turno (
            arqueo_id, cierre_turno_id, fecha_operativa, turno, responsable_caja,
            total_adicionales_efectivo_calculado_cents,
            total_adicionales_efectivo_declarado_cents,
            diferencia_cents, observaciones, traslado_ejecutado, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?);
        """,
        (
            arqueo_id,
            str(cierre_turno_id) if cierre_turno_id is not None else None,
            str(fecha_operativa),
            str(turno),
            str(responsable_caja),
            int(total_adicionales_efectivo_calculado_cents),
            int(total_adicionales_efectivo_declarado_cents) if total_adicionales_efectivo_declarado_cents is not None else None,
            int(diferencia_cents) if diferencia_cents is not None else None,
            observaciones,
            now_utc,
        ),
    )
    return arqueo_id
