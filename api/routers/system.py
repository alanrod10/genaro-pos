"""Router de diagnóstico de sistema y preparación del esquema de base de datos."""

from typing import Any, Optional
from fastapi import APIRouter, Depends
from api.dependencies import get_current_user, get_db_connection
from api.schemas import SystemReadinessOut, UserInfo
from db.readiness import comprobar_preparacion_esquema

router = APIRouter()


@router.get("/readiness", response_model=SystemReadinessOut)
def check_readiness(
    conn: Optional[Any] = Depends(get_db_connection),
    _current_user: UserInfo = Depends(get_current_user),
) -> SystemReadinessOut:
    """Verifica si el esquema de base de datos tiene todas las migraciones (001–009) y estructuras requeridas.

    Endpoint protegido para Alan y Yilda. No expone credenciales, tokens ni URLs de bases de datos.
    """
    diagnostico = comprobar_preparacion_esquema(conn=conn)
    return SystemReadinessOut(**diagnostico)
