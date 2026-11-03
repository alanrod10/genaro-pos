"""Router de la API para el módulo HISTÓRICO de GenaroPOS.

Endpoints de solo lectura protegidos por autenticación:
- GET /api/historical/summary
- GET /api/historical/sales
- GET /api/historical/products
- GET /api/historical/services
- GET /api/historical/tickets
- GET /api/historical/tickets/{ticket_id}
- GET /api/historical/compare
"""

import logging
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.dependencies import AuthenticatedUser, get_current_user, get_db_connection
from api.schemas_historical import (
    HistoricalCompareOut,
    HistoricalProductsOut,
    HistoricalSalesOut,
    HistoricalServicesOut,
    HistoricalSummaryOut,
    HistoricalTicketDetalleOut,
    HistoricalTicketsSearchOut,
)
from db.historical import (
    ErrorHistorico,
    buscar_tickets_historicos,
    comparar_periodos_historicos,
    obtener_ranking_productos_historico,
    obtener_resumen_historico,
    obtener_servicios_historicos,
    obtener_ticket_detalle_historico,
    obtener_ventas_historicas,
)

logger = logging.getLogger("genaro_pos.historical.router")

router = APIRouter()


@router.get("/summary", response_model=HistoricalSummaryOut)
def get_historical_summary(
    current_user: AuthenticatedUser = Depends(get_current_user),
    conn: Optional[Any] = Depends(get_db_connection),
) -> HistoricalSummaryOut:
    """Retorna las métricas consolidadas del archivo histórico persistido (2025-2026)."""
    try:
        data = obtener_resumen_historico(conn=conn)
        return HistoricalSummaryOut(**data)
    except Exception as e:
        logger.error("Error al obtener resumen histórico: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error interno al consultar el resumen histórico.",
        )


@router.get("/sales", response_model=HistoricalSalesOut)
def get_historical_sales(
    desde: Optional[str] = Query(None, description="Fecha inicio YYYY-MM-DD"),
    hasta: Optional[str] = Query(None, description="Fecha fin YYYY-MM-DD"),
    metodo_pago: Optional[str] = Query(None, description="EFECTIVO, TRANSFERENCIA, MIXTO"),
    agrupacion: str = Query("mes", description="'mes' o 'dia'"),
    limit: int = Query(50, ge=1, le=200, description="Límite de paginación"),
    offset: int = Query(0, ge=0, description="Offset de paginación"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    conn: Optional[Any] = Depends(get_db_connection),
) -> HistoricalSalesOut:
    """Retorna ventas históricas filtradas, serie temporal y tabla paginada de tickets."""
    agrup = agrupacion.strip().lower()
    if agrup not in ("mes", "dia"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Agrupación inválida. Valores admitidos: 'mes', 'dia'",
        )

    try:
        data = obtener_ventas_historicas(
            desde=desde,
            hasta=hasta,
            metodo_pago=metodo_pago,
            agrupacion=agrup,
            limit=limit,
            offset=offset,
            conn=conn,
        )
        return HistoricalSalesOut(**data)
    except Exception as e:
        logger.error("Error al consultar ventas históricas: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error interno al consultar ventas históricas.",
        )


@router.get("/products", response_model=HistoricalProductsOut)
def get_historical_products(
    desde: Optional[str] = Query(None, description="Fecha inicio YYYY-MM-DD"),
    hasta: Optional[str] = Query(None, description="Fecha fin YYYY-MM-DD"),
    depurado: bool = Query(True, description="True excluye 6 reintentos duplicados; False muestra original"),
    limit: int = Query(50, ge=1, le=200, description="Límite de paginación"),
    offset: int = Query(0, ge=0, description="Offset de paginación"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    conn: Optional[Any] = Depends(get_db_connection),
) -> HistoricalProductsOut:
    """Retorna el ranking de productos vendidos con soporte para exclusión de reintentos."""
    try:
        data = obtener_ranking_productos_historico(
            desde=desde,
            hasta=hasta,
            depurado=depurado,
            limit=limit,
            offset=offset,
            conn=conn,
        )
        return HistoricalProductsOut(**data)
    except Exception as e:
        logger.error("Error al consultar ranking de productos histórico: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error interno al consultar productos históricos.",
        )


@router.get("/services", response_model=HistoricalServicesOut)
def get_historical_services(
    desde: Optional[str] = Query(None, description="Fecha inicio YYYY-MM-DD"),
    hasta: Optional[str] = Query(None, description="Fecha fin YYYY-MM-DD"),
    excluir_duplicados: bool = Query(False, description="True excluye las 24 filas con estampa idéntica al segundo"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    conn: Optional[Any] = Depends(get_db_connection),
) -> HistoricalServicesOut:
    """Retorna métricas de servicios de recargas históricas con opción de excluir posibles duplicados."""
    try:
        data = obtener_servicios_historicos(
            desde=desde,
            hasta=hasta,
            excluir_duplicados=excluir_duplicados,
            conn=conn,
        )
        return HistoricalServicesOut(**data)
    except Exception as e:
        logger.error("Error al consultar servicios históricos: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error interno al consultar servicios históricos.",
        )


@router.get("/tickets", response_model=HistoricalTicketsSearchOut)
def search_historical_tickets(
    query: Optional[str] = Query(None, description="Término de búsqueda por ID de ticket"),
    fecha: Optional[str] = Query(None, description="Filtro exacto por fecha YYYY-MM-DD"),
    limit: int = Query(20, ge=1, le=100, description="Límite de paginación"),
    offset: int = Query(0, ge=0, description="Offset de paginación"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    conn: Optional[Any] = Depends(get_db_connection),
) -> HistoricalTicketsSearchOut:
    """Búsqueda paginada de tickets históricos por ID o fecha."""
    try:
        data = buscar_tickets_historicos(
            query_str=query,
            fecha=fecha,
            limit=limit,
            offset=offset,
            conn=conn,
        )
        return HistoricalTicketsSearchOut(**data)
    except Exception as e:
        logger.error("Error en búsqueda de tickets históricos: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error interno en búsqueda de tickets históricos.",
        )


@router.get("/tickets/{ticket_id}", response_model=HistoricalTicketDetalleOut)
def get_historical_ticket_detail(
    ticket_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    conn: Optional[Any] = Depends(get_db_connection),
) -> HistoricalTicketDetalleOut:
    """Retorna la ficha detallada de un ticket histórico con sus ítems y alertas asociadas."""
    try:
        detalle = obtener_ticket_detalle_historico(ticket_id=ticket_id, conn=conn)
        if not detalle:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Ticket histórico '{ticket_id}' no encontrado.",
            )
        return HistoricalTicketDetalleOut(**detalle)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error al consultar detalle de ticket histórico '%s': %s", ticket_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error interno al consultar detalle de ticket histórico.",
        )


@router.get("/compare", response_model=HistoricalCompareOut)
def compare_historical_periods(
    desde_a: str = Query(..., description="Fecha inicio período A (YYYY-MM-DD)"),
    hasta_a: str = Query(..., description="Fecha fin período A (YYYY-MM-DD)"),
    desde_b: str = Query(..., description="Fecha inicio período B (YYYY-MM-DD)"),
    hasta_b: str = Query(..., description="Fecha fin período B (YYYY-MM-DD)"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    conn: Optional[Any] = Depends(get_db_connection),
) -> HistoricalCompareOut:
    """Compara métricas analíticas y porcentajes de variación entre dos períodos históricos."""
    try:
        data = comparar_periodos_historicos(
            desde_a=desde_a.strip(),
            hasta_a=hasta_a.strip(),
            desde_b=desde_b.strip(),
            hasta_b=hasta_b.strip(),
            conn=conn,
        )
        return HistoricalCompareOut(**data)
    except Exception as e:
        logger.error("Error al comparar períodos históricos: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error interno al comparar períodos históricos.",
        )
