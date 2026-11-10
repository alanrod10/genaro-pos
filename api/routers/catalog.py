"""Router para consulta y administración de catálogo de productos."""
import logging
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from api.dependencies import AuthenticatedUser, get_current_user, get_db_connection
from api.schemas import (
    CatalogoResponse,
    ProductoCatalogo,
    ProductoCreateIn,
    ProductoDeleteOut,
    ProductoStatusIn,
    ProductoUpdateIn,
)
from db.catalog import (
    ErrorAccesoCatalogoTurso,
    cargar_catalogo_turso_filas,
    eliminar_producto_inteligente_turso,
)
from db.readiness import ErrorEsquemaNoPreparado
from db.admin_catalog import (
    ErrorAdminCatalogo,
    ErrorDuplicadoProducto,
    ErrorValidacionProducto,
    ProductoNoEncontrado,
    cambiar_estado_producto_turso,
    crear_producto_turso,
    editar_producto_turso,
    listar_productos_admin_turso,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("", response_model=CatalogoResponse)
def get_catalog(
    include_inactive: bool = Query(
        default=False,
        description="Si es True, incluye productos activos e inactivos (para administración)",
    ),
    conn: Optional[Any] = Depends(get_db_connection),
) -> CatalogoResponse:
    """Retorna los productos del catálogo mediante el adaptador Turso."""
    try:
        if include_inactive:
            filas = listar_productos_admin_turso(conn=conn)
        else:
            filas = cargar_catalogo_turso_filas(conn=conn)
        productos = [ProductoCatalogo.from_adapter_dict(f) for f in filas]
        return CatalogoResponse(total=len(productos), productos=productos)
    except ErrorAccesoCatalogoTurso as e:
        logger.error("Error al acceder al catálogo Turso: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Servicio de catálogo temporalmente no disponible.",
        ) from e
    except Exception as e:
        if "no such table" in str(e).lower():
            logger.warning("Tabla 'productos' no encontrada en la base de datos: %s", e)
            return CatalogoResponse(total=0, productos=[])
        logger.error("Error inesperado en endpoint de catálogo: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error interno al consultar el catálogo.",
        ) from e


@router.post("", response_model=ProductoCatalogo, status_code=status.HTTP_201_CREATED)
def create_product(
    payload: ProductoCreateIn,
    current_user: AuthenticatedUser = Depends(get_current_user),
    conn: Optional[Any] = Depends(get_db_connection),
) -> ProductoCatalogo:
    """Crea un nuevo producto en el catálogo con precio único y registro de auditoría."""
    try:
        res = crear_producto_turso(payload.model_dump(), actor=current_user.username, conn=conn)
        return ProductoCatalogo.from_adapter_dict(res)
    except ErrorDuplicadoProducto as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        ) from e
    except ErrorValidacionProducto as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except ErrorEsquemaNoPreparado as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"SCHEMA_NOT_READY: {str(e)}",
        ) from e
    except ErrorAdminCatalogo as e:
        logger.error("Error en administración de catálogo al crear producto: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error interno al crear el producto.",
        ) from e


@router.patch("/{id_producto}", response_model=ProductoCatalogo)
def update_product(
    id_producto: int,
    payload: ProductoUpdateIn,
    current_user: AuthenticatedUser = Depends(get_current_user),
    conn: Optional[Any] = Depends(get_db_connection),
) -> ProductoCatalogo:
    """Actualiza granularmente campos de un producto por su ID."""
    cambios = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not cambios:
        # Sin modificaciones
        from db.admin_catalog import obtener_producto_por_id_turso
        p = obtener_producto_por_id_turso(id_producto, conn=conn)
        if not p:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No se encontró el producto con ID {id_producto}.",
            )
        return ProductoCatalogo.from_adapter_dict(p)

    try:
        res = editar_producto_turso(id_producto, cambios, actor=current_user.username, conn=conn)
        return ProductoCatalogo.from_adapter_dict(res)
    except ProductoNoEncontrado as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except ErrorEsquemaNoPreparado as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"SCHEMA_NOT_READY: {str(e)}",
        ) from e
    except ErrorDuplicadoProducto as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        ) from e
    except ErrorValidacionProducto as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except ErrorEsquemaNoPreparado as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"SCHEMA_NOT_READY: {str(e)}",
        ) from e
    except ErrorAdminCatalogo as e:
        logger.error("Error al actualizar producto ID %d: %s", id_producto, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error interno al actualizar el producto.",
        ) from e


@router.patch("/{id_producto}/status", response_model=ProductoCatalogo)
def update_product_status(
    id_producto: int,
    payload: ProductoStatusIn,
    current_user: AuthenticatedUser = Depends(get_current_user),
    conn: Optional[Any] = Depends(get_db_connection),
) -> ProductoCatalogo:
    """Activa o desactiva lógicamente un producto sin eliminarlo físicamente."""
    try:
        res = cambiar_estado_producto_turso(id_producto, activo=payload.activo, actor=current_user.username, conn=conn)
        return ProductoCatalogo.from_adapter_dict(res)
    except ProductoNoEncontrado as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except ErrorEsquemaNoPreparado as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"SCHEMA_NOT_READY: {str(e)}",
        ) from e
    except ErrorAdminCatalogo as e:
        logger.error("Error al cambiar estado del producto ID %d: %s", id_producto, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error interno al cambiar el estado del producto.",
        ) from e


@router.delete("/{id_producto}", response_model=ProductoDeleteOut)
def delete_product(
    id_producto: int,
    current_user: AuthenticatedUser = Depends(get_current_user),
    conn: Optional[Any] = Depends(get_db_connection),
) -> ProductoDeleteOut:
    """Elimina un producto mediante Smart Delete (borrado físico si no tiene ventas, baja lógica si tiene ventas)."""
    try:
        res = eliminar_producto_inteligente_turso(
            id_producto, conn=conn, actor=current_user.username
        )
        return ProductoDeleteOut(**res)
    except ProductoNoEncontrado as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except ErrorEsquemaNoPreparado as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"SCHEMA_NOT_READY: {str(e)}",
        ) from e
    except ErrorAdminCatalogo as e:
        logger.error("Error al eliminar producto ID %d: %s", id_producto, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error interno al eliminar el producto.",
        ) from e

