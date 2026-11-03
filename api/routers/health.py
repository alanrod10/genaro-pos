"""Router de diagnóstico y salud del proceso de la API."""
import os
from typing import Any, Optional
from fastapi import APIRouter, Depends
from api.dependencies import get_db_connection
from api.schemas import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health_check(
    check_db: bool = False,
    conn: Optional[Any] = Depends(get_db_connection),
) -> HealthResponse:
    """Comprueba la disponibilidad del proceso de la API local.

    Por defecto verifica exclusivamente que el proceso FastAPI esté activo,
    sin asumir ni falsear la conectividad con la base remota.
    Si se solicita check_db=True, realiza una verificación pasiva sin exponer credenciales.
    """
    db_status = "not_checked"
    if check_db:
        if conn is not None:
            try:
                conn.execute("SELECT 1")
                db_status = "connected_local"
            except Exception:
                db_status = "error"
        else:
            has_url = bool(os.getenv("TURSO_DEV_DATABASE_URL"))
            has_token = bool(os.getenv("TURSO_DEV_AUTH_TOKEN"))
            db_status = "configured" if (has_url and has_token) else "unconfigured"

    return HealthResponse(
        status="ok",
        service="genaro-pos-api",
        version="0.1.0",
        db_connectivity=db_status,
    )
