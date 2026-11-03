"""Endpoints REST para el módulo de Servicios y Cargas virtuales."""
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo
import pandas as pd
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status

from api.dependencies import AuthenticatedUser, get_current_user, get_db_connection, get_tesoreria_connection
from db.readiness import ErrorEsquemaNoPreparado
from tesoreria.reservations import ErrorReserva
from api.schemas import (
    MAPA_SERVICIOS_VALIDOS,
    CargaServicioCreateIn,
    CargaServicioOut,
    CargaServicioUpdateIn,
    CargasSummaryOut,
)
from db.services import (
    COLUMNA_ID_INTERNO,
    ErrorCargasTurso,
    ErrorConflictoIdempotencia,
    _conexion,
    anular_carga_individual_turso,
    calcular_resumen_cargas_turso,
    cargar_cargas_turso,
    editar_carga_individual_turso,
    obtener_carga_por_id_turso,
    registrar_carga_idempotente_turso,
    registrar_carga_turso,
)

router = APIRouter()
_ZONA_AR = ZoneInfo("America/Argentina/Jujuy")


@router.get("", response_model=Dict[str, Any])
def listar_cargas(
    fecha: Optional[str] = Query(None, description="Filtrar por fecha en formato YYYY-MM-DD o DD/MM/YYYY"),
    incluir_anuladas: bool = Query(False, description="Incluir operaciones anuladas lógicamente"),
    plataforma_origen: Optional[str] = Query(None, description="Filtrar por plataforma de origen (PREPAY, CLARO COMERCIOS, MERCADO PAGO, HISTORICO)"),
    servicio_destino: Optional[str] = Query(None, description="Filtrar por servicio de destino"),
    conn: Optional[Any] = Depends(get_db_connection),
):
    """Devuelve el historial de cargas y servicios filtrado."""
    try:
        # El filtro de fecha se aplica en SQL (rango UTC del día local de Jujuy).
        df = cargar_cargas_turso(
            conn=conn,
            incluir_anuladas=incluir_anuladas,
            fecha=fecha or None,
            plataforma_origen=plataforma_origen or None,
            servicio_destino=servicio_destino or None,
        )
        if df.empty:
            return {"total": 0, "cargas": []}

        resultado = []
        for _, row in df.iterrows():
            plat_orig = row.get("PLATAFORMA_ORIGEN")
            if pd.isna(plat_orig) if "pd" in globals() else False:
                plat_orig = None
            if plat_orig is not None and str(plat_orig).strip().lower() in ("none", "nan", ""):
                plat_orig = None

            srv_dest = row.get("SERVICIO_DESTINO")
            if srv_dest is None or str(srv_dest).strip().lower() in ("none", "nan", ""):
                srv_dest = str(row["SERVICIO"])

            resultado.append(
                CargaServicioOut(
                    id=int(row[COLUMNA_ID_INTERNO]),
                    fecha=str(row["FECHA"]),
                    servicio=str(row["SERVICIO"]),
                    plataforma_origen=plat_orig,
                    servicio_destino=srv_dest,
                    monto_carga=int(row["MONTO_CARGA"]),
                    monto_adicional=int(row["MONTO_ADICIONAL"]),
                    total_cobrado=int(row["TOTAL_COBRADO"]),
                    pago_efvo=int(row["PAGO_EFVO"]),
                    pago_transf=int(row["PAGO_TRANSF"]),
                    anulado=bool(row.get("ANULADO", 0)),
                )
            )

        return {"total": len(resultado), "cargas": resultado}
    except Exception as e:
        if "no such table" in str(e).lower():
            return {"total": 0, "cargas": []}
        raise HTTPException(status_code=500, detail=f"Error al listar cargas: {str(e)}")


@router.get("/summary", response_model=CargasSummaryOut)
def resumen_cargas(
    fecha: Optional[str] = Query(None, description="Fecha objetivo (predeterminado: hoy)"),
    conn: Optional[Any] = Depends(get_db_connection),
):
    """Devuelve el resumen de totales y saldos (Prepay, Claro, SUBE MP) para una fecha."""
    try:
        if not fecha:
            # Por defecto: hoy en Argentina
            hoy_ar = datetime.now(_ZONA_AR).strftime("%Y-%m-%d")
            fecha = hoy_ar

        resumen = calcular_resumen_cargas_turso(fecha_filtro=fecha, conn=conn)
        return CargasSummaryOut(**resumen)
    except Exception as e:
        if "no such table" in str(e).lower():
            return CargasSummaryOut(
                total_operaciones=0,
                total_cobrado=0,
                total_capital=0,
                total_adicional=0,
                total_efectivo=0,
                total_transferencia=0,
                capital_prepay=0,
                capital_claro=0,
                capital_sube_mp=0,
                operaciones_anuladas=0,
            )
        raise HTTPException(status_code=500, detail=f"Error al calcular resumen de cargas: {str(e)}")


@router.get("/{carga_id}", response_model=CargaServicioOut)
def obtener_carga(
    carga_id: int,
    conn: Optional[Any] = Depends(get_db_connection),
):
    """Devuelve el detalle de una carga específica por ID."""
    try:
        carga = obtener_carga_por_id_turso(carga_id, conn=conn)
        if not carga:
            raise HTTPException(status_code=404, detail=f"Carga con ID {carga_id} no encontrada.")
        return CargaServicioOut(
            id=int(carga[COLUMNA_ID_INTERNO]),
            fecha=str(carga["FECHA"]),
            servicio=str(carga["SERVICIO"]),
            plataforma_origen=carga.get("PLATAFORMA_ORIGEN") or carga.get("plataforma_origen"),
            servicio_destino=carga.get("SERVICIO_DESTINO") or carga.get("servicio_destino"),
            monto_carga=int(carga["MONTO_CARGA"]),
            monto_adicional=int(carga["MONTO_ADICIONAL"]),
            total_cobrado=int(carga["TOTAL_COBRADO"]),
            pago_efvo=int(carga["PAGO_EFVO"]),
            pago_transf=int(carga["PAGO_TRANSF"]),
            anulado=bool(carga.get("ANULADO", 0)),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener carga: {str(e)}")


@router.post("", response_model=CargaServicioOut, status_code=status.HTTP_201_CREATED)
def registrar_carga(
    payload: CargaServicioCreateIn,
    response: Response,
    idempotency_key_header: Optional[str] = Header(None, alias="Idempotency-Key"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    conn: Optional[Any] = Depends(get_db_connection),
    tes_conn: Optional[Any] = Depends(get_tesoreria_connection),
):
    """Registra una nueva carga de servicio con soporte de idempotencia y auditoría."""
    try:
        fecha_texto = payload.fecha
        if not fecha_texto:
            fecha_texto = datetime.now(_ZONA_AR).strftime("%d/%m/%Y %H:%M:%S")

        idempotency_key = idempotency_key_header or payload.idempotency_key

        registro_dict = {
            "FECHA": fecha_texto,
            "SERVICIO": payload.servicio or payload.servicio_destino,
            "PLATAFORMA_ORIGEN": payload.plataforma_origen,
            "SERVICIO_DESTINO": payload.servicio_destino,
            "MONTO_CARGA": payload.monto_carga,
            "MONTO_ADICIONAL": payload.monto_adicional,
            "TOTAL_COBRADO": payload.total_cobrado,
            "PAGO_EFVO": payload.pago_efvo,
            "PAGO_TRANSF": payload.pago_transf,
            "idempotency_key": idempotency_key,
        }

        nuevo_id, es_reintento, guardado = registrar_carga_idempotente_turso(
            registro_dict,
            idempotency_key=idempotency_key,
            actor=current_user.username,
            conn=conn,
            retornar_registro=True,
            tesoreria_conn=tes_conn,
        )
        if es_reintento:
            response.status_code = status.HTTP_200_OK

        return CargaServicioOut(
            id=nuevo_id,
            fecha=str(guardado["FECHA"]),
            servicio=str(guardado["SERVICIO"]),
            plataforma_origen=guardado.get("PLATAFORMA_ORIGEN") or guardado.get("plataforma_origen"),
            servicio_destino=guardado.get("SERVICIO_DESTINO") or guardado.get("servicio_destino"),
            monto_carga=int(guardado["MONTO_CARGA"]),
            monto_adicional=int(guardado["MONTO_ADICIONAL"]),
            total_cobrado=int(guardado["TOTAL_COBRADO"]),
            pago_efvo=int(guardado["PAGO_EFVO"]),
            pago_transf=int(guardado["PAGO_TRANSF"]),
            anulado=bool(guardado.get("ANULADO", 0)),
            idempotency_key=guardado.get("IDEMPOTENCY_KEY"),
        )
    except ErrorConflictoIdempotencia as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except (ErrorCargasTurso, ErrorReserva) as e:
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
        raise HTTPException(status_code=500, detail=f"Error al registrar carga: {str(e)}")


@router.patch("/{carga_id}", response_model=CargaServicioOut)
def editar_carga(
    carga_id: int,
    payload: CargaServicioUpdateIn,
    current_user: AuthenticatedUser = Depends(get_current_user),
    conn: Optional[Any] = Depends(get_db_connection),
):
    """Edita una carga de servicio existente respetando balance contable y auditoría."""
    try:
        existente = obtener_carga_por_id_turso(carga_id, conn=conn)
        if not existente:
            raise HTTPException(status_code=404, detail=f"Carga con ID {carga_id} no encontrada.")
        if existente.get("ANULADO", 0) == 1:
            raise HTTPException(status_code=400, detail="No se puede editar una carga que ya fue anulada.")

        # Identificar qué campos fueron explícitamente enviados en el payload
        campos_enviados = payload.model_fields_set

        # Fusión respetando semántica de campos no enviados y campos nulos
        if "plataforma_origen" in campos_enviados:
            nuevo_plataforma = payload.plataforma_origen
        else:
            nuevo_plataforma = existente.get("PLATAFORMA_ORIGEN") or existente.get("plataforma_origen")

        if "servicio_destino" in campos_enviados:
            nuevo_destino = payload.servicio_destino
        else:
            nuevo_destino = existente.get("SERVICIO_DESTINO") or existente.get("servicio_destino")

        # Validación estricta de la combinación final resultante
        if nuevo_plataforma is None and nuevo_destino is None:
            # Registro histórico legado sin plataforma/destino (preservar intacto si no se modificaron)
            nuevo_servicio = payload.servicio.strip() if payload.servicio is not None else existente["SERVICIO"]
        elif (nuevo_plataforma is not None and nuevo_destino is None) or (nuevo_plataforma is None and nuevo_destino is not None):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Combinación incompleta: debe especificar tanto la plataforma de origen como el servicio de destino.",
            )
        else:
            # Ambos campos están presentes (no nulos)
            plat_clean = str(nuevo_plataforma).strip().upper()
            dest_clean = str(nuevo_destino).strip()

            matching_plat = next((k for k in MAPA_SERVICIOS_VALIDOS if k.upper() == plat_clean), None)
            if not matching_plat:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Plataforma de origen '{nuevo_plataforma}' inválida. Opciones válidas: {list(MAPA_SERVICIOS_VALIDOS.keys())}.",
                )

            opciones_validas = MAPA_SERVICIOS_VALIDOS[matching_plat]
            matching_dest = next((d for d in opciones_validas if d.lower() == dest_clean.lower()), None)
            if not matching_dest:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Combinación inválida: el servicio '{dest_clean}' no está disponible para la plataforma '{matching_plat}'. Opciones válidas: {opciones_validas}.",
                )

            nuevo_plataforma = matching_plat
            nuevo_destino = matching_dest
            nuevo_servicio = matching_dest

        nuevo_carga = payload.monto_carga if payload.monto_carga is not None else existente["MONTO_CARGA"]
        nuevo_adic = payload.monto_adicional if payload.monto_adicional is not None else existente["MONTO_ADICIONAL"]
        nuevo_total = nuevo_carga + nuevo_adic

        # Ajuste de medios de pago si no se especificaron ambos
        nuevo_efvo = payload.pago_efvo if payload.pago_efvo is not None else existente["PAGO_EFVO"]
        nuevo_transf = payload.pago_transf if payload.pago_transf is not None else existente["PAGO_TRANSF"]

        # Si el total cambió pero el usuario no especificó la nueva distribución,
        # conservar la proporción o ajustar contra efectivo
        if nuevo_efvo + nuevo_transf != nuevo_total:
            if payload.pago_transf is not None and payload.pago_efvo is None:
                nuevo_efvo = max(0, nuevo_total - nuevo_transf)
            else:
                nuevo_efvo = max(0, nuevo_total - nuevo_transf)

        registro_editado = {
            "FECHA": payload.fecha or existente["FECHA"],
            "SERVICIO": nuevo_servicio,
            "PLATAFORMA_ORIGEN": nuevo_plataforma,
            "SERVICIO_DESTINO": nuevo_destino,
            "MONTO_CARGA": nuevo_carga,
            "MONTO_ADICIONAL": nuevo_adic,
            "TOTAL_COBRADO": nuevo_total,
            "PAGO_EFVO": nuevo_efvo,
            "PAGO_TRANSF": nuevo_transf,
        }

        actualizado = editar_carga_individual_turso(carga_id, registro_editado, actor=current_user.username, conn=conn)
        return CargaServicioOut(
            id=carga_id,
            fecha=str(actualizado["FECHA"]),
            servicio=str(actualizado["SERVICIO"]),
            plataforma_origen=actualizado.get("PLATAFORMA_ORIGEN") or actualizado.get("plataforma_origen"),
            servicio_destino=actualizado.get("SERVICIO_DESTINO") or actualizado.get("servicio_destino"),
            monto_carga=int(actualizado["MONTO_CARGA"]),
            monto_adicional=int(actualizado["MONTO_ADICIONAL"]),
            total_cobrado=int(actualizado["TOTAL_COBRADO"]),
            pago_efvo=int(actualizado["PAGO_EFVO"]),
            pago_transf=int(actualizado["PAGO_TRANSF"]),
            anulado=bool(actualizado.get("ANULADO", 0)),
        )
    except HTTPException:
        raise
    except ErrorCargasTurso as e:
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
        raise HTTPException(status_code=500, detail=f"Error al editar carga: {str(e)}")


@router.post("/{carga_id}/cancel", response_model=Dict[str, Any])
@router.delete("/{carga_id}", response_model=Dict[str, Any])
def anular_carga(
    carga_id: int,
    current_user: AuthenticatedUser = Depends(get_current_user),
    conn: Optional[Any] = Depends(get_db_connection),
):
    """Anula lógicamente una carga garantizando la trazabilidad histórica y auditoría."""
    try:
        with _conexion(conn) as c:
            existente = obtener_carga_por_id_turso(carga_id, conn=c)
            if not existente:
                raise HTTPException(status_code=404, detail=f"Carga con ID {carga_id} no encontrada.")
            if existente.get("ANULADO", 0) == 1:
                return {
                    "status": "ya_anulado",
                    "id": carga_id,
                    "mensaje": f"La carga #{carga_id} ya se encontraba anulada.",
                }

            anular_carga_individual_turso(carga_id, actor=current_user.username, conn=c)
            return {
                "status": "anulado",
                "id": carga_id,
                "mensaje": f"Carga #{carga_id} anulada lógicamente con éxito.",
            }
    except HTTPException:
        raise
    except ErrorCargasTurso as e:
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
        raise HTTPException(status_code=500, detail=f"Error al anular carga: {str(e)}")
