"""Router de reportes y métricas financieras consolidadas del Dashboard."""
import logging
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.dependencies import get_db_connection
from api.schemas import MetricasDashboardOut
from db.reports import calcular_metricas_dashboard_turso

logger = logging.getLogger("genaro_pos.reports.router")

router = APIRouter()


@router.get("/dashboard", response_model=MetricasDashboardOut)
def get_dashboard_metrics(
    desde: Optional[str] = Query(
        None,
        description="Fecha inicial del reporte (YYYY-MM-DD o DD/MM/YYYY)",
    ),
    hasta: Optional[str] = Query(
        None,
        description="Fecha final del reporte (YYYY-MM-DD o DD/MM/YYYY)",
    ),
    periodo: str = Query(
        "hoy",
        description="Tipo de período: 'hoy', 'ultimos_7_dias', 'ultimos_30_dias' o 'personalizado'",
    ),
    conn: Optional[Any] = Depends(get_db_connection),
) -> MetricasDashboardOut:
    """Calcula y retorna las métricas consolidadas del Dashboard Financiero sobre la persistencia central.

    Requiere autenticación válida. Las métricas reflejan las fórmulas de conciliación de negocio:
    - Caja A (Drugstore con análisis de costo unitario histórico congelado)
    - Cajas B, C, D, E de servicios y banco
    - Gastos y retiros
    - Arqueo físico de cajón
    - Serie temporal diaria para gráficos
    """
    periodos_validos = {"hoy", "ultimos_7_dias", "ultimos_30_dias", "personalizado"}
    periodo_normalizado = periodo.strip().lower()
    if periodo_normalizado not in periodos_validos:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Período '{periodo}' no válido. Valores admitidos: {sorted(list(periodos_validos))}",
        )

    try:
        metricas = calcular_metricas_dashboard_turso(
            desde_param=desde,
            hasta_param=hasta,
            periodo_tipo=periodo_normalizado,
            conn=conn,
        )
        return MetricasDashboardOut(**metricas)
    except Exception as e:
        logger.error("Error al calcular métricas de dashboard central: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error interno al consolidar el visor financiero central.",
        ) from e
