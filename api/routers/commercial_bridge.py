"""Router de Monitoreo de Integración Comercial y Puente de Tesorería — ETAPA 5.

Proporciona:
- Vista simplificada de sincronización para Cajeros (/sync-status).
- Panel de control y métricas completas para Administradores / Socios (/status).
- Consulta filtrable y paginada de eventos comerciales (/events).
- Reprocesamiento auditable de eventos en FAILED o DEAD_LETTER (/reprocess/{event_id}).
- Activación forzada de drenaje inmediato (/trigger-drain).
"""

from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from api.auth import AuthenticatedUser
from api.dependencies import get_current_user, get_db_connection, require_admin_or_socio
from tesoreria.isolation import conectar_db_laboratorio
from tesoreria.commercial_bridge_worker import (
    BridgeConflictError,
    get_commercial_bridge_status,
    get_simplified_sync_status,
    list_commercial_events_detailed,
    process_pending_commercial_events,
    recover_orphaned_processing_events,
    reprocess_outbox_event,
)

router = APIRouter()


# ============================================================================
# SCHEMAS PYDANTIC
# ============================================================================

class SimplifiedSyncStatusResponse(BaseModel):
    sincronizadas: int
    pendientes: int
    requiere_atencion: bool
    mensaje_cajero: str
    timestamp: str


class CommercialBridgeStatusResponse(BaseModel):
    total: int
    pending: int
    processing: int
    published: int
    failed: int
    dead_letter: int
    retry_count_total: int


class EventoComercialDetalle(BaseModel):
    sequence_id: int
    event_id: str
    event_type: str
    entity_type: str
    entity_id: str
    action_type: str
    idempotency_key: str
    payload_canonical_hash: str
    payload: Dict[str, Any]
    status: str
    retry_count: int
    max_retries: int
    error_message: Optional[str] = None
    tesoreria_movement_id: Optional[str] = None
    created_at: str
    updated_at: str
    published_at: Optional[str] = None
    reprocessed_by: Optional[str] = None
    reprocessed_at: Optional[str] = None


class CommercialEventsListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    events: List[EventoComercialDetalle]


class ReprocessResponse(BaseModel):
    event_id: str
    status: str
    tesoreria_movement_id: Optional[str] = None
    error_message: Optional[str] = None
    authorized_by: str
    reprocessed_at: str
    drain_stats: Dict[str, int]


class DrainResponse(BaseModel):
    published: int
    failed: int
    dead_letter: int
    message: str


# ============================================================================
# CONEXIONES DE BASE DE DATOS
# ============================================================================

def _obtener_conn_comercial_activa(conn_override: Optional[Any] = None) -> Any:
    """Retorna la conexión activa a la base comercial donde reside commercial_events_outbox."""
    if conn_override is not None:
        return conn_override

    # En laboratorio local, verificar variable de entorno o fallback a SQLite dev
    env_path = os.getenv("GENARO_COMMERCIAL_DB_PATH") or os.getenv("COMMERCIAL_DB_PATH")
    if env_path:
        conn = sqlite3.connect(env_path)
        conn.row_factory = sqlite3.Row
        return conn

    # Si no hay override ni env, fallback a archivo SQLite local de desarrollo
    raiz = Path(__file__).resolve().parents[2]
    ruta_dev = raiz / "db" / "genaro-pos-dev-lab.db"
    if not ruta_dev.exists():
        # Crear tabla en base local si no existe para laboratorio
        conn = sqlite3.connect(str(ruta_dev))
        conn.row_factory = sqlite3.Row
        return conn
    conn = sqlite3.connect(str(ruta_dev))
    conn.row_factory = sqlite3.Row
    return conn


def _obtener_conn_tesoreria_activa() -> sqlite3.Connection:
    """Retorna la conexión aislada del motor de Tesorería."""
    return conectar_db_laboratorio()


def get_tesoreria_connection() -> Optional[Any]:
    """Provee la conexión de Tesorería a los endpoints.
    
    En pruebas se sobreescribe mediante:
    app.dependency_overrides[get_tesoreria_connection] = lambda: tes_conn
    """
    return None


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.get(
    "/sync-status",
    response_model=SimplifiedSyncStatusResponse,
    summary="Indicador simplificado de sincronización (Apto para Cajero)",
)
def get_sync_status(
    user: AuthenticatedUser = Depends(get_current_user),
    conn: Optional[Any] = Depends(get_db_connection),
) -> SimplifiedSyncStatusResponse:
    """Permite al Cajero conocer si sus ventas y cargas están sincronizadas con Tesorería.
    
    No expone información financiera sensible, hashes ni herramientas administrativas.
    """
    comm_conn = _obtener_conn_comercial_activa(conn)
    try:
        data = get_simplified_sync_status(comm_conn)
        return SimplifiedSyncStatusResponse(**data)
    finally:
        if conn is None and hasattr(comm_conn, "close"):
            comm_conn.close()


@router.get(
    "/status",
    response_model=CommercialBridgeStatusResponse,
    summary="Métricas globales del Outbox comercial (Admin / Socios)",
)
def get_bridge_status(
    user: AuthenticatedUser = Depends(require_admin_or_socio),
    conn: Optional[Any] = Depends(get_db_connection),
) -> CommercialBridgeStatusResponse:
    """Métricas cuantitativas para supervisión de la integración con Tesorería."""
    comm_conn = _obtener_conn_comercial_activa(conn)
    try:
        data = get_commercial_bridge_status(comm_conn)
        return CommercialBridgeStatusResponse(**data)
    finally:
        if conn is None and hasattr(comm_conn, "close"):
            comm_conn.close()


@router.get(
    "/events",
    response_model=CommercialEventsListResponse,
    summary="Listado detallado de eventos comerciales con conciliación (Admin / Socios)",
)
def get_commercial_events(
    status_filter: Optional[str] = Query(None, alias="status", description="Filtrar por PENDING, PROCESSING, PUBLISHED, FAILED, DEAD_LETTER"),
    event_type: Optional[str] = Query(None, description="Filtrar por tipo (SALE, SERVICE, EXPENSE)"),
    search: Optional[str] = Query(None, description="Buscar por ticket, clave de idempotencia o entity_id"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: AuthenticatedUser = Depends(require_admin_or_socio),
    conn: Optional[Any] = Depends(get_db_connection),
) -> CommercialEventsListResponse:
    """Permite a Administradores y Socios auditar eventos y diagnosticar errores de conciliación."""
    comm_conn = _obtener_conn_comercial_activa(conn)
    try:
        data = list_commercial_events_detailed(
            comm_conn=comm_conn,
            status_filter=status_filter,
            event_type_filter=event_type,
            search=search,
            limit=limit,
            offset=offset,
        )
        return CommercialEventsListResponse(**data)
    finally:
        if conn is None and hasattr(comm_conn, "close"):
            comm_conn.close()


@router.post(
    "/reprocess/{event_id}",
    response_model=ReprocessResponse,
    summary="Reprocesamiento manual auditable de evento comercial (Admin / Socios)",
)
def reprocess_event(
    event_id: str,
    user: AuthenticatedUser = Depends(require_admin_or_socio),
    conn: Optional[Any] = Depends(get_db_connection),
    tes_conn_override: Optional[Any] = Depends(get_tesoreria_connection),
) -> ReprocessResponse:
    """Reprocesa un evento en FAILED o DEAD_LETTER registrando la auditoría de quién lo autorizó."""
    comm_conn = _obtener_conn_comercial_activa(conn)
    should_close_tes = tes_conn_override is None
    tes_conn = tes_conn_override or _obtener_conn_tesoreria_activa()
    try:
        res = reprocess_outbox_event(
            comm_conn=comm_conn,
            tes_conn=tes_conn,
            event_id=event_id,
            authorized_by=user.username,
        )
        return ReprocessResponse(**res)
    except BridgeConflictError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    finally:
        if conn is None and hasattr(comm_conn, "close"):
            comm_conn.close()
        if should_close_tes and hasattr(tes_conn, "close"):
            tes_conn.close()


@router.post(
    "/trigger-drain",
    response_model=DrainResponse,
    summary="Fuerza un ciclo de drenaje inmediato de eventos (Admin / Socios)",
)
def trigger_drain(
    user: AuthenticatedUser = Depends(require_admin_or_socio),
    conn: Optional[Any] = Depends(get_db_connection),
    tes_conn_override: Optional[Any] = Depends(get_tesoreria_connection),
) -> DrainResponse:
    """Ejecuta un ciclo de recuperación y drenaje sobre eventos pendientes."""
    comm_conn = _obtener_conn_comercial_activa(conn)
    should_close_tes = tes_conn_override is None
    tes_conn = tes_conn_override or _obtener_conn_tesoreria_activa()
    try:
        recover_orphaned_processing_events(comm_conn, timeout_seconds=30)
        stats = process_pending_commercial_events(comm_conn, tes_conn)
        return DrainResponse(
            published=stats["published"],
            failed=stats["failed"],
            dead_letter=stats["dead_letter"],
            message=f"Ciclo completado: {stats['published']} confirmados, {stats['failed']} con fallo, {stats['dead_letter']} en letra muerta.",
        )
    finally:
        if conn is None and hasattr(comm_conn, "close"):
            comm_conn.close()
        if should_close_tes and hasattr(tes_conn, "close"):
            tes_conn.close()
