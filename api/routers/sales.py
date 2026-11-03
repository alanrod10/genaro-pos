"""Router para persistencia de ventas y tickets con idempotencia real."""
import logging
import sqlite3
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Response, status
from api.dependencies import AuthenticatedUser, get_current_user, get_db_connection
from db.readiness import ErrorEsquemaNoPreparado
from api.schemas import (
    AnularVentaIn,
    EditarItemVentaIn,
    TicketDetalleOut,
    VentaRequest,
    VentaResponse,
    VentasHistorialResponse,
)
from db.sales import (
    ErrorConflictoIdempotencia,
    ErrorVentasTurso,
    anular_venta_turso,
    editar_item_venta_turso,
    listar_ventas_turso,
    obtener_detalle_ticket_turso,
    registrar_venta_idempotente_turso,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("", response_model=VentaResponse)
def crear_venta(
    payload: VentaRequest,
    response: Response,
    current_user: AuthenticatedUser = Depends(get_current_user),
    conn: Optional[Any] = Depends(get_db_connection),
) -> VentaResponse:
    """Registra una venta de forma atómica e idempotente con Turso.

    Contrato de idempotencia:
    - Primera solicitud válida: HTTP 201 Created (status: 'registrado').
    - Reintento idéntico confirmado: HTTP 200 OK (status: 'existente') sin registros adicionales.
    - Mismo ticket_id con contenido discordante: HTTP 409 Conflict sin modificar el existente.
    - La unicidad en la base de datos previene condiciones de carrera concurrentes.
    """
    ticket_dict = {
        "TICKET_ID": payload.ticket.ticket_id,
        "FECHA": payload.ticket.fecha,
        "TOTAL_VENTA": payload.ticket.total_venta,
        "MONTO_EFECTIVO": payload.ticket.monto_efectivo,
        "MONTO_TRANSF": payload.ticket.monto_transf,
        "METODO_PAGO": payload.ticket.metodo_pago,
        "ES_NOCTURNO": payload.ticket.es_nocturno,
    }
    items_list = [
        {
            "TICKET_ID": payload.ticket.ticket_id,
            "FECHA": payload.ticket.fecha,
            "PRODUCTO": item.producto,
            "CANTIDAD": item.cantidad,
            "UNIDAD": item.unidad,
            "PRECIO_UNIT": item.precio_unit,
            "SUBTOTAL": item.subtotal,
            "METODO_PAGO": payload.ticket.metodo_pago,
            "COSTO_UNITARIO": item.costo_unitario,
        }
        for item in payload.items
    ]

    try:
        resultado_id, es_reintento = registrar_venta_idempotente_turso(
            ticket_dict, items_list, usuario=current_user.username, conn=conn
        )
        if es_reintento:
            response.status_code = status.HTTP_200_OK
            return VentaResponse(
                ticket_id=str(resultado_id),
                status="existente",
                mensaje="Venta previamente registrada (operación idempotente).",
            )
        else:
            response.status_code = status.HTTP_201_CREATED
            return VentaResponse(
                ticket_id=str(resultado_id),
                status="registrado",
                mensaje="Venta registrada exitosamente.",
            )
    except ErrorConflictoIdempotencia as e:
        logger.warning(
            "Conflicto de idempotencia en ticket %s: %s",
            payload.ticket.ticket_id,
            e,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        ) from e
    except ErrorEsquemaNoPreparado as e:
        logger.error(
            "Esquema no preparado al registrar venta %s: %s",
            payload.ticket.ticket_id,
            e,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"SCHEMA_NOT_READY: {str(e)}",
        ) from e
    except ErrorVentasTurso as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except ErrorEsquemaNoPreparado as e:
        logger.error("Esquema no preparado al anular venta %s: %s", ticket_id, e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"SCHEMA_NOT_READY: {str(e)}",
        ) from e
    except sqlite3.IntegrityError as e:
        msg = str(e)
        logger.warning(
            "Conflicto de integridad en base de datos para ticket %s: %s",
            payload.ticket.ticket_id,
            msg,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Conflicto de integridad: el ticket '{payload.ticket.ticket_id}' "
                "ya está registrado o infringe una restricción de clave."
            ),
        ) from e
    except Exception as e:
        msg = str(e)
        if "IntegrityError" in type(e).__name__ or "UNIQUE constraint failed" in msg:
            logger.warning(
                "Conflicto de integridad en driver para ticket %s: %s",
                payload.ticket.ticket_id,
                msg,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Conflicto de integridad: el ticket '{payload.ticket.ticket_id}' "
                    "ya está registrado o infringe unicidad."
                ),
            ) from e
        logger.error(
            "Error interno de persistencia al registrar venta %s: %s",
            payload.ticket.ticket_id,
            msg,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error interno al procesar la persistencia de la venta.",
        ) from e


@router.get("", response_model=VentasHistorialResponse)
def listar_ventas(
    fecha: Optional[str] = None,
    busqueda: Optional[str] = None,
    metodo_pago: Optional[str] = None,
    incluir_anuladas: bool = True,
    limite: int = 100,
    conn: Optional[Any] = Depends(get_db_connection),
) -> VentasHistorialResponse:
    """Consulta el historial de tickets con filtros y conteo de ítems."""
    try:
        ventas = listar_ventas_turso(
            fecha=fecha,
            metodo_pago=metodo_pago,
            incluir_anuladas=incluir_anuladas,
            busqueda=busqueda,
            limite=limite,
            conn=conn,
        )
        return VentasHistorialResponse(total=len(ventas), ventas=ventas)
    except Exception as e:
        if "no such table" in str(e).lower():
            logger.warning("Tablas de tickets/ventas no encontradas: %s", e)
            return VentasHistorialResponse(total=0, ventas=[])
        logger.error("Error al listar historial de ventas: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error interno al consultar historial de ventas.",
        ) from e


@router.get("/{ticket_id}", response_model=TicketDetalleOut)
def obtener_detalle_venta(
    ticket_id: str,
    conn: Optional[Any] = Depends(get_db_connection),
) -> TicketDetalleOut:
    """Consulta el detalle exhaustivo de un ticket con sus ítems y auditoría."""
    try:
        detalle = obtener_detalle_ticket_turso(ticket_id, conn=conn)
        if not detalle:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"El ticket '{ticket_id}' no fue encontrado.",
            )
        return TicketDetalleOut(**detalle)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error al consultar detalle de ticket %s: %s", ticket_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error interno al consultar el detalle de la venta.",
        ) from e


@router.post("/{ticket_id}/anular", response_model=TicketDetalleOut)
def anular_venta(
    ticket_id: str,
    payload: AnularVentaIn,
    current_user: AuthenticatedUser = Depends(get_current_user),
    conn: Optional[Any] = Depends(get_db_connection),
) -> TicketDetalleOut:
    """Anula lógicamente un ticket y registra evento de auditoría."""
    try:
        detalle = anular_venta_turso(
            ticket_id,
            motivo=payload.motivo,
            usuario=current_user.username,
            conn=conn,
        )
        return TicketDetalleOut(**detalle)
    except ErrorVentasTurso as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except ErrorEsquemaNoPreparado as e:
        logger.error("Esquema no preparado al anular venta %s: %s", ticket_id, e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"SCHEMA_NOT_READY: {str(e)}",
        ) from e
    except Exception as e:
        logger.error("Error al anular venta %s: %s", ticket_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error interno al anular la venta.",
        ) from e


@router.put("/{ticket_id}/items/{item_id}", response_model=TicketDetalleOut)
def editar_item_venta(
    ticket_id: str,
    item_id: int,
    payload: EditarItemVentaIn,
    current_user: AuthenticatedUser = Depends(get_current_user),
    conn: Optional[Any] = Depends(get_db_connection),
) -> TicketDetalleOut:
    """Edita un ítem de ticket y recalcula la cabecera preservando reglas contables."""
    try:
        detalle = editar_item_venta_turso(
            ticket_id=ticket_id,
            item_id=item_id,
            nueva_cantidad=payload.nueva_cantidad,
            nuevo_subtotal=payload.nuevo_subtotal,
            usuario=current_user.username,
            conn=conn,
        )
        return TicketDetalleOut(**detalle)
    except ErrorVentasTurso as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except ErrorEsquemaNoPreparado as e:
        logger.error("Esquema no preparado al anular venta %s: %s", ticket_id, e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"SCHEMA_NOT_READY: {str(e)}",
        ) from e
    except Exception as e:
        logger.error("Error al editar ítem %s de ticket %s: %s", item_id, ticket_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error interno al editar el ítem de la venta.",
        ) from e

