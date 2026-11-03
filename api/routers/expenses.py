"""Endpoints REST para el módulo de Gastos, Arqueo y Cierre Diario de Caja."""
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status

from api.dependencies import AuthenticatedUser, get_current_user, get_db_connection
from db.readiness import ErrorEsquemaNoPreparado
from api.schemas import (
    ArqueoDiarioOut,
    CierreDiarioIn,
    CierreDiarioOut,
    GastoCreateIn,
    GastoOut,
    GastosSummaryOut,
    GastoUpdateIn,
    MetricasDashboardOut,
)
from db.reports import calcular_metricas_dashboard_turso
from db.expenses import (
    COLUMNA_ID_INTERNO,
    ErrorGastosTurso,
    ErrorConflictoIdempotencia,
    anular_gasto_turso,
    calcular_arqueo_diario_turso,
    calcular_resumen_gastos_turso,
    cargar_gastos_turso,
    editar_gasto_turso,
    listar_cierres_turso,
    obtener_cierre_diario_turso,
    obtener_gasto_por_id_turso,
    registrar_cierre_diario_turso,
    registrar_gasto_idempotente_turso,
    registrar_gasto_turso,
)

router = APIRouter()
_ZONA_AR = ZoneInfo("America/Argentina/Jujuy")


@router.get("", response_model=Dict[str, Any])
def listar_gastos(
    fecha: Optional[str] = Query(None, description="Filtrar por fecha YYYY-MM-DD o DD/MM/YYYY"),
    tipo: Optional[str] = Query(None, description="Filtrar por categoría/tipo"),
    incluir_anulados: bool = Query(False, description="Incluir gastos anulados"),
    conn: Optional[Any] = Depends(get_db_connection),
):
    """Devuelve la lista de gastos registrados."""
    try:
        df = cargar_gastos_turso(conn=conn, fecha_filtro=fecha, incluir_anulados=incluir_anulados)
        if df.empty:
            return {"total": 0, "gastos": []}

        if tipo and tipo != "TODAS":
            df = df[df["TIPO"].str.lower() == tipo.lower()]

        resultado = []
        for _, row in df.iterrows():
            resultado.append(
                GastoOut(
                    id=int(row[COLUMNA_ID_INTERNO]),
                    fecha=str(row["FECHA"]),
                    tipo=str(row["TIPO"]),
                    detalle=str(row["DETALLE"]),
                    monto_salida=int(row["MONTO_SALIDA"]),
                    origen_fondos=str(row["ORIGEN_FONDOS"]),
                    metodo_pago=str(row["METODO_PAGO"]),
                    anulado=bool(row.get("ANULADO", 0)),
                )
            )

        return {"total": len(resultado), "gastos": resultado}
    except Exception as e:
        if "no such table" in str(e).lower():
            return {"total": 0, "gastos": []}
        raise HTTPException(status_code=500, detail=f"Error al listar gastos: {str(e)}")


@router.get("/summary", response_model=GastosSummaryOut)
def resumen_gastos(
    fecha: Optional[str] = Query(None, description="Fecha de consulta (predeterminado: hoy)"),
    conn: Optional[Any] = Depends(get_db_connection),
):
    """Devuelve los totales de gastos (efectivo y transferencia) para la fecha."""
    try:
        resumen = calcular_resumen_gastos_turso(fecha_filtro=fecha, conn=conn)
        return GastosSummaryOut(**resumen)
    except Exception as e:
        if "no such table" in str(e).lower():
            return GastosSummaryOut(
                total_operaciones=0,
                gastos_efvo=0,
                gastos_transf=0,
                gastos_total=0,
                operaciones_anuladas=0,
            )
        raise HTTPException(status_code=500, detail=f"Error al calcular resumen de gastos: {str(e)}")


@router.get("/arqueo", response_model=ArqueoDiarioOut)
def arqueo_diario(
    fecha: Optional[str] = Query(None, description="Fecha a conciliar (predeterminado: hoy)"),
    conn: Optional[Any] = Depends(get_db_connection),
):
    """Devuelve el arqueo consolidado del día (Ventas, Servicios y Gastos)."""
    try:
        if not fecha:
            fecha = datetime.now(_ZONA_AR).strftime("%Y-%m-%d")

        arqueo = calcular_arqueo_diario_turso(fecha_objetivo=fecha, conn=conn)
        return ArqueoDiarioOut(**arqueo)
    except Exception as e:
        if "no such table" in str(e).lower():
            return ArqueoDiarioOut(
                fecha=fecha,
                total_ventas=0,
                ventas_efvo=0,
                ventas_transf=0,
                ganancia_estimada_ventas=0,
                servicios_efvo=0,
                servicios_transf=0,
                total_servicios=0,
                capital_prepay=0,
                capital_claro=0,
                capital_sube_mp=0,
                adicionales_ganancia=0,
                d_recargas=0,
                gastos_efvo=0,
                gastos_transf=0,
                gastos_total=0,
                efectivo_disponible_drugstore=0,
                efectivo_esperado=0,
                total_banco=0,
            )
        raise HTTPException(status_code=500, detail=f"Error al calcular arqueo diario: {str(e)}")


@router.get("/dashboard", response_model=MetricasDashboardOut)
def get_expenses_dashboard_metrics(
    desde: Optional[str] = Query(None, description="Fecha de inicio"),
    hasta: Optional[str] = Query(None, description="Fecha de fin"),
    periodo: str = Query("hoy", description="Tipo de período"),
    conn: Optional[Any] = Depends(get_db_connection),
) -> MetricasDashboardOut:
    """Ruta alias de compatibilidad para métricas consolidadas del Dashboard."""
    try:
        metricas = calcular_metricas_dashboard_turso(
            desde_param=desde,
            hasta_param=hasta,
            periodo_tipo=periodo.strip().lower(),
            conn=conn,
        )
        return MetricasDashboardOut(**metricas)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al calcular dashboard: {str(e)}")


@router.get("/cierres", response_model=List[CierreDiarioOut])
def listar_cierres(
    conn: Optional[Any] = Depends(get_db_connection),
):
    """Lista todos los cierres diarios registrados."""
    try:
        cierres = listar_cierres_turso(conn=conn)
        return [CierreDiarioOut(**c) for c in cierres]
    except Exception as e:
        if "no such table" in str(e).lower():
            return []
        raise HTTPException(status_code=500, detail=f"Error al listar cierres: {str(e)}")


@router.get("/cierres/{fecha}", response_model=CierreDiarioOut)
def obtener_cierre(
    fecha: str,
    conn: Optional[Any] = Depends(get_db_connection),
):
    """Obtiene el cierre registrado para una fecha."""
    try:
        cierre = obtener_cierre_diario_turso(fecha=fecha, conn=conn)
        if not cierre:
            raise HTTPException(status_code=404, detail=f"No hay cierre registrado para la fecha {fecha}.")
        return CierreDiarioOut(**cierre)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener cierre: {str(e)}")


@router.post("/cierres", response_model=CierreDiarioOut, status_code=status.HTTP_201_CREATED)
def registrar_cierre(
    payload: CierreDiarioIn,
    current_user: AuthenticatedUser = Depends(get_current_user),
    conn: Optional[Any] = Depends(get_db_connection),
):
    """Registra o actualiza el cierre formal del día con atribución de usuario."""
    try:
        guardado = registrar_cierre_diario_turso(
            payload.model_dump(),
            cerrado_por=current_user.username,
            conn=conn,
        )
        return CierreDiarioOut(**guardado)
    except ErrorGastosTurso as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ErrorEsquemaNoPreparado as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"SCHEMA_NOT_READY: {str(e)}",
        ) from e
    except Exception as e:
        if "no such table" in str(e).lower():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"SCHEMA_NOT_READY: {str(e)}",
            ) from e
        raise HTTPException(status_code=500, detail=f"Error al registrar cierre: {str(e)}")


@router.get("/{gasto_id}", response_model=GastoOut)
def obtener_gasto(
    gasto_id: int,
    conn: Optional[Any] = Depends(get_db_connection),
):
    """Obtiene el detalle de un gasto específico por ID."""
    try:
        gasto = obtener_gasto_por_id_turso(gasto_id, conn=conn)
        if not gasto:
            raise HTTPException(status_code=404, detail=f"Gasto #{gasto_id} no encontrado.")
        return GastoOut(
            id=int(gasto[COLUMNA_ID_INTERNO]),
            fecha=str(gasto["FECHA"]),
            tipo=str(gasto["TIPO"]),
            detalle=str(gasto["DETALLE"]),
            monto_salida=int(gasto["MONTO_SALIDA"]),
            origen_fondos=str(gasto["ORIGEN_FONDOS"]),
            metodo_pago=str(gasto["METODO_PAGO"]),
            anulado=bool(gasto.get("ANULADO", 0)),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener gasto: {str(e)}")


@router.post("", response_model=GastoOut, status_code=status.HTTP_201_CREATED)
def registrar_gasto(
    payload: GastoCreateIn,
    response: Response,
    idempotency_key_header: Optional[str] = Header(None, alias="Idempotency-Key"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    conn: Optional[Any] = Depends(get_db_connection),
):
    """Registra un nuevo gasto o retiro con soporte de idempotencia y auditoría."""
    try:
        fecha_texto = payload.fecha
        if not fecha_texto:
            fecha_texto = datetime.now(_ZONA_AR).strftime("%d/%m/%Y %H:%M:%S")

        idempotency_key = idempotency_key_header or payload.idempotency_key

        registro_dict = {
            "FECHA": fecha_texto,
            "TIPO": payload.tipo,
            "DETALLE": payload.detalle,
            "MONTO_SALIDA": payload.monto_salida,
            "ORIGEN_FONDOS": payload.origen_fondos,
            "METODO_PAGO": payload.metodo_pago,
            "idempotency_key": idempotency_key,
        }

        nuevo_id, es_reintento = registrar_gasto_idempotente_turso(
            registro_dict,
            idempotency_key=idempotency_key,
            actor=current_user.username,
            conn=conn,
        )
        if es_reintento:
            response.status_code = status.HTTP_200_OK

        guardado = obtener_gasto_por_id_turso(nuevo_id, conn=conn)
        return GastoOut(
            id=nuevo_id,
            fecha=str(guardado["FECHA"]),
            tipo=str(guardado["TIPO"]),
            detalle=str(guardado["DETALLE"]),
            monto_salida=int(guardado["MONTO_SALIDA"]),
            origen_fondos=str(guardado["ORIGEN_FONDOS"]),
            metodo_pago=str(guardado["METODO_PAGO"]),
            anulado=bool(guardado.get("ANULADO", 0)),
            idempotency_key=guardado.get("IDEMPOTENCY_KEY"),
        )
    except ErrorConflictoIdempotencia as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except ErrorGastosTurso as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ErrorEsquemaNoPreparado as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"SCHEMA_NOT_READY: {str(e)}",
        ) from e
    except Exception as e:
        msg = str(e).lower()
        if "no such table" in msg or "no such column" in msg or "no column named" in msg:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"SCHEMA_NOT_READY: {str(e)}",
            ) from e
        raise HTTPException(status_code=500, detail=f"Error al registrar gasto: {str(e)}")


@router.patch("/{gasto_id}", response_model=GastoOut)
def editar_gasto(
    gasto_id: int,
    payload: GastoUpdateIn,
    current_user: AuthenticatedUser = Depends(get_current_user),
    conn: Optional[Any] = Depends(get_db_connection),
):
    """Edita un gasto específico sin alterar otros registros respetando auditoría."""
    try:
        existente = obtener_gasto_por_id_turso(gasto_id, conn=conn)
        if not existente:
            raise HTTPException(status_code=404, detail=f"Gasto #{gasto_id} no encontrado.")
        if existente.get("ANULADO", 0) == 1:
            raise HTTPException(status_code=400, detail="No se puede editar un gasto anulado.")

        registro_editado = {
            "FECHA": payload.fecha or existente["FECHA"],
            "TIPO": payload.tipo.strip() if payload.tipo is not None else existente["TIPO"],
            "DETALLE": payload.detalle.strip() if payload.detalle is not None else existente["DETALLE"],
            "MONTO_SALIDA": payload.monto_salida if payload.monto_salida is not None else existente["MONTO_SALIDA"],
            "ORIGEN_FONDOS": payload.origen_fondos if payload.origen_fondos is not None else existente["ORIGEN_FONDOS"],
            "METODO_PAGO": payload.metodo_pago.strip().upper() if payload.metodo_pago is not None else existente["METODO_PAGO"],
        }

        actualizado = editar_gasto_turso(
            gasto_id,
            registro_editado,
            actor=current_user.username,
            conn=conn,
        )
        return GastoOut(
            id=gasto_id,
            fecha=str(actualizado["FECHA"]),
            tipo=str(actualizado["TIPO"]),
            detalle=str(actualizado["DETALLE"]),
            monto_salida=int(actualizado["MONTO_SALIDA"]),
            origen_fondos=str(actualizado["ORIGEN_FONDOS"]),
            metodo_pago=str(actualizado["METODO_PAGO"]),
            anulado=bool(actualizado.get("ANULADO", 0)),
        )
    except HTTPException:
        raise
    except ErrorGastosTurso as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ErrorEsquemaNoPreparado as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"SCHEMA_NOT_READY: {str(e)}",
        ) from e
    except Exception as e:
        if "no such table" in str(e).lower():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"SCHEMA_NOT_READY: {str(e)}",
            ) from e
        raise HTTPException(status_code=500, detail=f"Error al editar gasto: {str(e)}")


@router.post("/{gasto_id}/cancel", response_model=Dict[str, Any])
@router.delete("/{gasto_id}", response_model=Dict[str, Any])
def anular_gasto(
    gasto_id: int,
    current_user: AuthenticatedUser = Depends(get_current_user),
    conn: Optional[Any] = Depends(get_db_connection),
):
    """Anula lógicamente un gasto garantizando trazabilidad y auditoría."""
    try:
        existente = obtener_gasto_por_id_turso(gasto_id, conn=conn)
        if not existente:
            raise HTTPException(status_code=404, detail=f"Gasto #{gasto_id} no encontrado.")
        if existente.get("ANULADO", 0) == 1:
            return {
                "status": "ya_anulado",
                "id": gasto_id,
                "mensaje": f"El gasto #{gasto_id} ya se encontraba anulado.",
            }

        anular_gasto_turso(gasto_id, actor=current_user.username, conn=conn)
        return {
            "status": "anulado",
            "id": gasto_id,
            "mensaje": f"Gasto #{gasto_id} anulado lógicamente con éxito.",
        }
    except HTTPException:
        raise
    except ErrorGastosTurso as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ErrorEsquemaNoPreparado as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"SCHEMA_NOT_READY: {str(e)}",
        ) from e
    except Exception as e:
        if "no such table" in str(e).lower():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"SCHEMA_NOT_READY: {str(e)}",
            ) from e
        raise HTTPException(status_code=500, detail=f"Error al anular gasto: {str(e)}")
