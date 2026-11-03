"""Router para el módulo de Preventistas y Pedidos a Proveedores."""
import logging
from typing import Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from api.dependencies import AuthenticatedUser, get_current_user, get_db_connection
from db.readiness import ErrorEsquemaNoPreparado
from api.schemas import (
    AltaRapidaProductoPreventistaIn,
    CambioEstadoPedidoIn,
    CancelarPedidoIn,
    LotePreciosPreventistaIn,
    LotePreciosPreventistaOut,
    PedidoProveedorCreateIn,
    PedidoProveedorDetalleOut,
    PedidoProveedorOut,
    ProductoCatalogo,
)
from db.preventistas import (
    ErrorPreventistas,
    ErrorValidacionPreventistas,
    PedidoNoEncontrado,
    actualizar_precios_lote_preventista_turso,
    alta_rapida_producto_preventista_turso,
    cambiar_estado_pedido_turso,
    crear_pedido_proveedor_turso,
    listar_pedidos_proveedor_turso,
    listar_productos_por_proveedor_turso,
    listar_proveedores_turso,
    obtener_detalle_pedido_proveedor_turso,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/proveedores", response_model=List[str])
def get_proveedores(
    conn: Optional[Any] = Depends(get_db_connection),
) -> List[str]:
    """Lista todos los proveedores únicos registrados en el catálogo."""
    try:
        return listar_proveedores_turso(conn=conn)
    except Exception as e:
        logger.error("Error al listar proveedores: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al consultar lista de proveedores.",
        ) from e


@router.get("/productos", response_model=List[dict])
def get_productos_por_proveedor(
    proveedor: str = Query(..., min_length=1, description="Nombre del proveedor"),
    conn: Optional[Any] = Depends(get_db_connection),
) -> List[dict]:
    """Retorna los productos filtrados por proveedor para la grilla de edición."""
    try:
        return listar_productos_por_proveedor_turso(proveedor, conn=conn)
    except Exception as e:
        logger.error("Error al listar productos de %s: %s", proveedor, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al consultar productos del proveedor '{proveedor}'.",
        ) from e


@router.post("/precios", response_model=LotePreciosPreventistaOut)
def update_precios_preventista(
    payload: LotePreciosPreventistaIn,
    current_user: AuthenticatedUser = Depends(get_current_user),
    conn: Optional[Any] = Depends(get_db_connection),
) -> LotePreciosPreventistaOut:
    """Actualiza en lote los costos y precios de un preventista detectando cambios reales."""
    try:
        cambios = [c.model_dump() for c in payload.cambios]
        res = actualizar_precios_lote_preventista_turso(
            proveedor=payload.proveedor,
            cambios=cambios,
            actor=current_user.username,
            conn=conn,
        )
        return LotePreciosPreventistaOut(**res)
    except ErrorValidacionPreventistas as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except ErrorEsquemaNoPreparado as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"SCHEMA_NOT_READY: {str(e)}",
        ) from e
    except Exception as e:
        logger.error("Error al actualizar precios de preventista %s: %s", payload.proveedor, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error interno al actualizar precios de preventista.",
        ) from e


@router.post("/productos/alta-rapida", response_model=ProductoCatalogo, status_code=status.HTTP_201_CREATED)
def quick_create_producto_preventista(
    payload: AltaRapidaProductoPreventistaIn,
    current_user: AuthenticatedUser = Depends(get_current_user),
    conn: Optional[Any] = Depends(get_db_connection),
) -> ProductoCatalogo:
    """Crea un nuevo producto en el catálogo vinculado al preventista elegido."""
    try:
        datos = {
            "nombre": payload.nombre,
            "categoria": payload.categoria,
            "unidad": payload.unidad,
            "costo": payload.costo,
            "precio_venta": payload.precio_venta,
        }
        res = alta_rapida_producto_preventista_turso(
            proveedor=payload.proveedor,
            datos=datos,
            actor=current_user.username,
            conn=conn,
        )
        return ProductoCatalogo.from_adapter_dict(res)
    except ErrorValidacionPreventistas as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except ErrorEsquemaNoPreparado as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"SCHEMA_NOT_READY: {str(e)}",
        ) from e
    except Exception as e:
        logger.error("Error al dar de alta producto para %s: %s", payload.proveedor, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error interno al crear el producto.",
        ) from e


# ==============================================================================
# RUTAS DE PEDIDOS
# ==============================================================================

@router.get("/pedidos", response_model=List[PedidoProveedorOut])
def get_pedidos(
    proveedor: Optional[str] = Query(None, description="Filtrar por proveedor"),
    fecha: Optional[str] = Query(None, description="Filtrar por fecha (YYYY-MM-DD)"),
    estado: Optional[str] = Query(None, description="Filtrar por estado"),
    conn: Optional[Any] = Depends(get_db_connection),
) -> List[PedidoProveedorOut]:
    """Consulta la lista de pedidos a proveedores."""
    try:
        pedidos = listar_pedidos_proveedor_turso(
            proveedor=proveedor,
            fecha=fecha,
            estado=estado,
            conn=conn,
        )
        return [PedidoProveedorOut(**p) for p in pedidos]
    except Exception as e:
        if "no such table" in str(e).lower():
            logger.warning("Tablas de pedidos no existen en la base: %s", e)
            return []
        logger.error("Error al listar pedidos: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al consultar lista de pedidos.",
        ) from e


@router.post("/pedidos", response_model=PedidoProveedorDetalleOut, status_code=status.HTTP_201_CREATED)
def create_pedido(
    payload: PedidoProveedorCreateIn,
    current_user: AuthenticatedUser = Depends(get_current_user),
    conn: Optional[Any] = Depends(get_db_connection),
) -> PedidoProveedorDetalleOut:
    """Registra un nuevo pedido a proveedor con sus ítems."""
    try:
        pedido_data = {
            "id": payload.id,
            "proveedor": payload.proveedor,
            "fecha": payload.fecha,
            "observaciones": payload.observaciones,
        }
        items = [it.model_dump() for it in payload.items]
        res = crear_pedido_proveedor_turso(
            pedido_data=pedido_data,
            items=items,
            actor=current_user.username,
            conn=conn,
        )
        return PedidoProveedorDetalleOut(**res)
    except ErrorValidacionPreventistas as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except ErrorEsquemaNoPreparado as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"SCHEMA_NOT_READY: {str(e)}",
        ) from e
    except Exception as e:
        logger.error("Error al crear pedido: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error interno al registrar pedido.",
        ) from e


@router.get("/pedidos/{pedido_id}", response_model=PedidoProveedorDetalleOut)
def get_pedido_detalle(
    pedido_id: str,
    conn: Optional[Any] = Depends(get_db_connection),
) -> PedidoProveedorDetalleOut:
    """Retorna el detalle completo de un pedido con sus ítems."""
    try:
        detalle = obtener_detalle_pedido_proveedor_turso(pedido_id, conn=conn)
        if not detalle:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No se encontró el pedido {pedido_id}.",
            )
        return PedidoProveedorDetalleOut(**detalle)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error al consultar pedido %s: %s", pedido_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al consultar detalle del pedido.",
        ) from e


@router.patch("/pedidos/{pedido_id}/estado", response_model=dict)
def update_pedido_estado(
    pedido_id: str,
    payload: CambioEstadoPedidoIn,
    current_user: AuthenticatedUser = Depends(get_current_user),
    conn: Optional[Any] = Depends(get_db_connection),
) -> dict:
    """Actualiza el estado de un pedido (PENDIENTE, RECIBIDO, CANCELADO)."""
    try:
        res = cambiar_estado_pedido_turso(
            pedido_id=pedido_id,
            nuevo_estado=payload.estado,
            observaciones=payload.observaciones,
            actor=current_user.username,
            conn=conn,
        )
        return res
    except PedidoNoEncontrado as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except ErrorValidacionPreventistas as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except ErrorEsquemaNoPreparado as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"SCHEMA_NOT_READY: {str(e)}",
        ) from e
    except Exception as e:
        logger.error("Error al cambiar estado del pedido %s: %s", pedido_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error interno al cambiar estado del pedido.",
        ) from e


@router.post("/pedidos/{pedido_id}/cancelar", response_model=dict)
def cancel_pedido(
    pedido_id: str,
    payload: CancelarPedidoIn,
    current_user: AuthenticatedUser = Depends(get_current_user),
    conn: Optional[Any] = Depends(get_db_connection),
) -> dict:
    """Cancela un pedido con motivo obligatorio."""
    try:
        res = cambiar_estado_pedido_turso(
            pedido_id=pedido_id,
            nuevo_estado="CANCELADO",
            observaciones=f"Motivo cancelación: {payload.motivo}",
            actor=current_user.username,
            conn=conn,
        )
        return res
    except PedidoNoEncontrado as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except ErrorEsquemaNoPreparado as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"SCHEMA_NOT_READY: {str(e)}",
        ) from e
    except Exception as e:
        logger.error("Error al cancelar pedido %s: %s", pedido_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error interno al cancelar el pedido.",
        ) from e
