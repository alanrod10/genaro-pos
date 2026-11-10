"""Router de la API para GENARO TESORERÍA LABORATORIO.

Endpoints aislados bajo /api/tesoreria:
- Estado del laboratorio y verificación de aislamiento (ISO-001 a ISO-008).
- Resumen general de fondos, saldo registrado y disponibilidad (LAB-008).
- Catálogo independiente de los 9 fondos (LAB-003).
- Detalle y auditoría por fondo.
- Gestión de jornadas de tesorería (LAB-004).
- Movimientos de entrada y salida reales e inmutables (LAB-005).
- Traslados de fondos y tratamiento de dinero en tránsito (LAB-006).
- Reservas de fondos con invariante estricto (LAB-007).
"""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from api.dependencies import get_current_user, require_admin_or_socio
from api.auth import AuthenticatedUser
from tesoreria.aperturas import (
    ErrorAperturaTesoreria,
    ErrorFondoConMovimientos,
    modificar_saldo_inicial,
    obtener_historial_aperturas,
    obtener_saldos_iniciales_completos,
)
from tesoreria.isolation import (
    ErrorViolacionAislamiento,
    conectar_db_laboratorio,
    obtener_ruta_db_laboratorio,
)
from tesoreria.schema import inicializar_esquema_laboratorio
from tesoreria.catalog import (
    ErrorCatalogoFondos,
    actualizar_estado_fondo,
    obtener_auditoria_fondo,
    obtener_catalogo_fondos,
    obtener_fondo_por_codigo,
    sembrar_catalogo_canónico,
    verificar_integridad_catalogo,
)
from tesoreria.jornadas import (
    ErrorJornada,
    abrir_jornada,
    cerrar_jornada,
    obtener_jornada_actual,
    obtener_jornada_por_id,
)
from tesoreria.movements import (
    ErrorMovimiento,
    ErrorSaldoInsuficiente,
    listar_movimientos,
    registerEntry,
    registerExit,
)
from tesoreria.transfers import (
    ErrorTraslado,
    cancelPendingTransfer,
    confirmTransferReceipt,
    listar_traslados,
    transferFunds,
)
from tesoreria.reservations import (
    ErrorReserva,
    applyReservation,
    createReservation,
    executeReservedExit,
    getReservationHistory,
    listar_reservas,
    releaseReservation,
)
from tesoreria.availability import (
    getFundAvailability,
    getFundBalance,
    getTreasurySummary,
)
from tesoreria.reconciliations import (
    ErrorArqueo,
    authorizeDifference,
    createDifferenceCase,
    createReconciliation,
    getDifferenceCases,
    getPendingTransfersForFund,
    getReconciliation,
    getReconciliationHistory,
    reconcileDigitalAccount,
    reconcilePhysicalFund,
    resolveDifferenceCase,
    updateReconciliationNotes,
)
from tesoreria.closures import (
    ErrorCierre,
    beginClosure,
    confirmClosure,
    executeCashDrawerSweep,
    executePrepayDrawerSweep,
    getClosure,
    getClosureHistory,
    openNextJournal,
    validateClosure,
)
from tesoreria.reversals import (
    ErrorReversion,
    getReversals,
    reverseMovement,
    reverseReservationOperation,
    reverseTransfer,
)
from tesoreria.audit_chain import (
    ErrorAuditoria,
    queryAuditEvents,
    verifyAuditChain,
)
from tesoreria.outbox import (
    ErrorOutbox,
    getOutboxStatus,
    processOutbox,
    retryDeadLetter,
)
from tesoreria.money import addMoney, formatMoney
from tesoreria.cuenta_corriente_david import (
    ErrorCuentaCorrienteDavid,
    ErrorSaldoInsuficienteDavid,
    ErrorValidacionDavid,
    ErrorIdempotenciaDavid,
    obtener_estado_cuenta_david,
    registrar_acreditacion_david,
    registrar_pago_david,
    registrar_reconocimiento_deuda_historica,
)
from tesoreria.cuenta_corriente_ezequiel import (
    ErrorCuentaCorrienteEzequiel,
    ErrorSaldoInsuficienteEzequiel,
    ErrorValidacionEzequiel,
    ErrorIdempotenciaEzequiel,
    consultar_posicion_ezequiel,
    ejecutar_fondeo_pendiente,
    listar_fondeos_pendientes,
    registrar_acreditacion_ezequiel,
    registrar_fondeo_pendiente,
    registrar_liquidacion_ezequiel,
    registrar_reconocimiento_historico_ezequiel,
)
from tesoreria.anticipos_preventistas import (
    AnticipoNoEncontrado,
    ErrorAnticipoPreventista,
    ErrorIdempotenciaAnticipo,
    ErrorSaldoInsuficienteAnticipo,
    ErrorSaldoInsuficienteReposicion,
    ErrorValidacionAnticipo,
    crear_anticipo_preventista,
    listar_anticipos_preventistas,
    obtener_anticipo_preventista,
    obtener_historial_reposiciones,
    obtener_resumen_obligaciones_preventistas,
    reponer_anticipo_preventista,
)

router = APIRouter()


# Schemas Pydantic
class FondoResponse(BaseModel):
    id: int
    codigo: str
    nombre: str
    naturaleza: str
    titularidad: str
    moneda: str
    estado: str
    saldo_centavos: int
    saldo_formateado: str
    disponibilidad_inmediata: bool
    permite_sobregiro: int
    version: int
    created_at: str
    updated_at: str
    creado_por: str
    actualizado_por: str


class SaldoInicialItem(BaseModel):
    codigo: str
    nombre: str
    categoria: str
    saldo_inicial_cents: int
    saldo_actual_cents: int
    saldo_inicial_formateado: str
    saldo_actual_formateado: str
    naturaleza: Optional[str] = None
    estado_apertura: Optional[str] = "PENDING_OPENING"
    subestado_apertura: Optional[str] = "SIN_MOVIMIENTOS"
    disponibilidad_confirmada: Optional[bool] = False
    fecha_corte: Optional[str] = None
    naturaleza_saldo: Optional[str] = None
    observaciones: Optional[str] = None
    comprobante: Optional[str] = None
    configurado_por: Optional[str] = None
    configurado_en: Optional[str] = None
    tiene_movimientos: bool = False
    puede_editar_apertura: bool = True
    estado_configuracion: str


class SaldosInicialesResponse(BaseModel):
    fondos_fisicos: List[SaldoInicialItem]
    cuentas_digitales: List[SaldoInicialItem]
    obligaciones: List[SaldoInicialItem]
    total_fondos_fisicos_cents: int
    total_cuentas_digitales_cents: int
    total_obligaciones_cents: int
    conteo_abiertos: Optional[int] = 0
    conteo_pendientes: Optional[int] = 0
    apertura_completa: Optional[bool] = False
    estado_general_apertura: Optional[str] = "DIFERIDO_PENDIENTE"


class ModificarSaldoInicialRequest(BaseModel):
    codigo: str = Field(..., description="Código de fondo (1110-1310) u obligación (DAVID, CLARO, PREVENTISTAS)")
    saldo_nuevo_cents: int = Field(..., ge=0, description="Nuevo saldo inicial en centavos (entero >= 0)")
    motivo: str = Field(..., min_length=3, description="Motivo obligatorio de la modificación")
    referencia_autorizacion: Optional[str] = Field(None, description="Referencia de autorización o acta")
    fecha_corte: Optional[str] = Field(None, description="Fecha y hora de corte (ISO o texto)")
    naturaleza_saldo: Optional[str] = Field(None, description="Naturaleza del saldo (EFECTIVO_CONTADO, SALDO_PLATAFORMA, OBLIGACION_EXIGIBLE)")
    comprobante: Optional[str] = Field(None, description="Comprobante o evidencia")
    idempotency_key: Optional[str] = Field(None, description="Clave de idempotencia única")
    tipo_operacion: Optional[str] = Field("APERTURA_INICIAL", description="APERTURA_INICIAL o AJUSTE_COMPENSATORIO")


class AuditoriaSaldoInicialItem(BaseModel):
    id: int
    item_codigo: str
    item_nombre: str
    categoria: str
    saldo_anterior_cents: int
    saldo_nuevo_cents: int
    saldo_anterior_formateado: str
    saldo_nuevo_formateado: str
    fecha_hora: str
    usuario_responsable: str
    motivo: str
    referencia_autorizacion: Optional[str] = None
    es_apertura_inicial: bool = False
    fecha_corte: Optional[str] = None
    naturaleza_saldo: Optional[str] = None
    idempotency_key: Optional[str] = None
    tipo_operacion: Optional[str] = "APERTURA_INICIAL"
    estado_apertura: Optional[str] = "OPENED"


class ResumenTesoreriaResponse(BaseModel):
    aislamiento_confirmado: bool
    db_path: str
    total_saldo_registrado_centavos: int
    total_saldo_registrado_formateado: str
    total_saldo_disponible_centavos: int
    total_saldo_disponible_formateado: str
    total_fondos: int
    fondos_activos: int
    jornada_activa: Optional[Dict[str, Any]] = None


class ActualizarEstadoFondoRequest(BaseModel):
    nuevo_estado: str = Field(..., description="Nuevo estado: ACTIVO, INACTIVO o BLOQUEADO")
    motivo: Optional[str] = Field("Ajuste administrativo", description="Motivo del cambio de estado")


class AbrirJornadaRequest(BaseModel):
    observaciones: Optional[str] = Field(None, description="Observaciones opcionales de apertura")


class CerrarJornadaRequest(BaseModel):
    jornada_id: int
    arqueo_centavos_por_fondo: Optional[Dict[str, int]] = Field(
        None, description="Mapeo de código de fondo a centavos arqueados físicamente"
    )
    observaciones: Optional[str] = Field(None, description="Observaciones de cierre")


class MovimientoRequest(BaseModel):
    codigo_fondo: str = Field(..., description="Código del fondo canónico (ej: '1110')")
    monto_centavos: int = Field(..., gt=0, description="Monto en centavos enteros (> 0)")
    contrapartida: str = Field(..., min_length=1, description="Contrapartida económica justificada")
    motivo: str = Field(..., min_length=1, description="Motivo del movimiento")
    terminal_id: str = Field("TERM-01", description="Identificador de la terminal")
    referencia_evidencia: Optional[str] = Field(None, description="Referencia a comprobante o recibo")


class TrasladoRequest(BaseModel):
    origin_fund_code: Optional[str] = Field(None, description="Código del fondo origen")
    destination_fund_code: Optional[str] = Field(None, description="Código del fondo destino")
    amount_cents: Optional[int] = Field(None, description="Monto en centavos enteros (> 0)")
    reason: Optional[str] = Field(None, description="Motivo del traslado")
    evidence_reference: Optional[str] = Field(None, description="Referencia a comprobante o remito")
    # Aliases en español
    codigo_origen: Optional[str] = None
    codigo_destino: Optional[str] = None
    monto_centavos: Optional[int] = None
    motivo: Optional[str] = None
    evidencia: Optional[str] = None

    def get_origin(self) -> str:
        orig = self.origin_fund_code or self.codigo_origen
        if not orig:
            raise ValueError("El fondo de origen es obligatorio.")
        return orig

    def get_destination(self) -> str:
        dest = self.destination_fund_code or self.codigo_destino
        if not dest:
            raise ValueError("El fondo de destino es obligatorio.")
        return dest

    def get_amount(self) -> int:
        amt = self.amount_cents if self.amount_cents is not None else self.monto_centavos
        if amt is None or amt <= 0:
            raise ValueError("El importe de traslado debe ser estrictamente positivo (> 0).")
        return amt

    def get_reason(self) -> str:
        r = self.reason or self.motivo
        if not r or not r.strip():
            raise ValueError("El motivo del traslado es obligatorio.")
        return r.strip()

    def get_evidence(self) -> Optional[str]:
        return self.evidence_reference or self.evidencia


class CancelarTrasladoRequest(BaseModel):
    reason: Optional[str] = Field(None, description="Motivo de cancelación del traslado")
    motivo: Optional[str] = None

    def get_reason(self) -> str:
        r = self.reason or self.motivo
        if not r or not r.strip():
            raise ValueError("El motivo de cancelación del traslado es obligatorio.")
        return r.strip()


class ReservaRequest(BaseModel):
    fund_code: str = Field(..., description="Código del fondo canónico a reservar")
    amount_cents: int = Field(..., gt=0, description="Monto en centavos enteros a reservar")
    reason: str = Field(..., min_length=1, description="Motivo de la afectación preventiva")
    beneficiary: str = Field(..., min_length=1, description="Beneficiario u obligación prevista")
    obligation_id: Optional[str] = Field(None, description="Identificador único del compromiso")
    evidence_reference: Optional[str] = Field(None, description="Referencia a presupuesto o contrato")


class OperacionReservaRequest(BaseModel):
    amount_cents: int = Field(..., gt=0, description="Monto en centavos enteros a aplicar o liberar")
    reason: str = Field(..., min_length=1, description="Motivo de la operación")
    evidence_reference: Optional[str] = Field(None, description="Referencia de pago o justificación")


def _get_lab_db():
    """Obtiene una conexión SQLite aislada para tesorería e inicializa si es necesario."""
    conn = conectar_db_laboratorio()
    inicializar_esquema_laboratorio(conn)
    sembrar_catalogo_canónico(conn)
    return conn


@router.get("/estado", summary="Estado del laboratorio y verificación de aislamiento")
def get_estado_laboratorio(user: AuthenticatedUser = Depends(get_current_user)):
    try:
        conn = _get_lab_db()
        ruta = obtener_ruta_db_laboratorio()
        integridad = verificar_integridad_catalogo(conn)
        jornada = obtener_jornada_actual(conn)
        conn.close()
        return {
            "modulo": "GENARO TESORERÍA LABORATORIO",
            "fase": "SPRINT 2 (LAB-005 a LAB-008)",
            "aislamiento": "ESTRICTO_LOCAL_SQLITE",
            "db_path": ruta,
            "conexion_remota": False,
            "turso_conectado": False,
            "integridad_catalogo": integridad,
            "jornada_activa": bool(jornada),
            "usuario": user.username,
        }
    except ErrorViolacionAislamiento as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.get("/resumen", response_model=ResumenTesoreriaResponse, summary="Resumen general de tesorería")
def get_resumen(user: AuthenticatedUser = Depends(get_current_user)):
    conn = _get_lab_db()
    fondos = obtener_catalogo_fondos(conn)
    jornada = obtener_jornada_actual(conn)
    ruta = obtener_ruta_db_laboratorio()
    summary = getTreasurySummary(conn)
    conn.close()

    total_registrado = summary["total_consolidado"]["total_registrado_cents"]
    total_disponible = summary["fondos_propios"]["total_disponible_cents"]

    activos_count = sum(1 for f in fondos if f["estado"] == "ACTIVO")
    ruta_lower = ruta.lower()
    es_aislado = ("lab" in ruta_lower or ":memory:" in ruta_lower) and "prod" not in ruta_lower and "live" not in ruta_lower

    return ResumenTesoreriaResponse(
        aislamiento_confirmado=es_aislado,
        db_path=ruta,
        total_saldo_registrado_centavos=total_registrado,
        total_saldo_registrado_formateado=formatMoney(total_registrado),
        total_saldo_disponible_centavos=total_disponible,
        total_saldo_disponible_formateado=formatMoney(total_disponible),
        total_fondos=len(fondos),
        fondos_activos=activos_count,
        jornada_activa=jornada,
    )


@router.get("/fondos", response_model=List[FondoResponse], summary="Listado de los 9 fondos canónicos")
def get_fondos(user: AuthenticatedUser = Depends(get_current_user)):
    conn = _get_lab_db()
    fondos = obtener_catalogo_fondos(conn)
    conn.close()

    resultado = []
    for f in fondos:
        f_dict = dict(f)
        f_dict["saldo_formateado"] = formatMoney(f["saldo_centavos"])
        resultado.append(FondoResponse(**f_dict))
    return resultado


@router.get("/fondos/{codigo}", response_model=FondoResponse, summary="Detalle de un fondo específico")
def get_fondo_detalle(codigo: str, user: AuthenticatedUser = Depends(get_current_user)):
    conn = _get_lab_db()
    try:
        fondo = obtener_fondo_por_codigo(conn, codigo)
    except ErrorCatalogoFondos as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    conn.close()

    if not fondo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Fondo no encontrado: {codigo}")

    fondo_dict = dict(fondo)
    fondo_dict["saldo_formateado"] = formatMoney(fondo["saldo_centavos"])
    return FondoResponse(**fondo_dict)


@router.get("/fondos/{codigo}/auditoria", summary="Historial inmutable de auditoría de un fondo")
def get_auditoria_fondo(codigo: str, user: AuthenticatedUser = Depends(get_current_user)):
    conn = _get_lab_db()
    try:
        registros = obtener_auditoria_fondo(conn, codigo)
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    conn.close()
    return registros


@router.patch("/fondos/{codigo}/estado", response_model=FondoResponse, summary="Actualizar estado operativo del fondo")
def patch_estado_fondo(
    codigo: str,
    payload: ActualizarEstadoFondoRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    conn = _get_lab_db()
    try:
        fondo_act = actualizar_estado_fondo(
            conn,
            codigo=codigo,
            nuevo_estado=payload.nuevo_estado,
            usuario=user.username,
            motivo=payload.motivo or "Actualización por API",
        )
    except ErrorCatalogoFondos as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    conn.close()

    f_dict = dict(fondo_act)
    f_dict["saldo_formateado"] = formatMoney(f_dict["saldo_centavos"])
    return FondoResponse(**f_dict)


@router.get("/apertura/saldos-iniciales", response_model=SaldosInicialesResponse, summary="Consultar saldos iniciales y estado de aperturas")
def get_saldos_iniciales(user: AuthenticatedUser = Depends(get_current_user)):
    conn = _get_lab_db()
    try:
        data = obtener_saldos_iniciales_completos(conn)
        return SaldosInicialesResponse(**data)
    finally:
        conn.close()


@router.post("/apertura/saldos-iniciales", summary="Modificar saldo inicial o registrar apertura con auditoría (Alan y Yilda)")
def post_modificar_saldo_inicial(
    payload: ModificarSaldoInicialRequest,
    user: AuthenticatedUser = Depends(require_admin_or_socio),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    conn = _get_lab_db()
    try:
        jornada = obtener_jornada_actual(conn)
        j_id = jornada["id"] if jornada else None
        idem_key = payload.idempotency_key or idempotency_key
        ref_comp = payload.comprobante or payload.referencia_autorizacion
        res = modificar_saldo_inicial(
            conn,
            codigo=payload.codigo,
            saldo_nuevo_cents=payload.saldo_nuevo_cents,
            usuario=user.username,
            motivo=payload.motivo,
            referencia_autorizacion=ref_comp,
            fecha_corte=payload.fecha_corte,
            naturaleza_saldo=payload.naturaleza_saldo,
            idempotency_key=idem_key,
            tipo_operacion=payload.tipo_operacion or "APERTURA_INICIAL",
            jornada_id=j_id,
        )
        return res
    except ErrorFondoConMovimientos as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except ErrorAperturaTesoreria as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    finally:
        conn.close()


@router.get("/apertura/historial", response_model=List[AuditoriaSaldoInicialItem], summary="Historial inmutable de auditoría de aperturas")
def get_historial_aperturas(user: AuthenticatedUser = Depends(get_current_user)):
    conn = _get_lab_db()
    try:
        registros = obtener_historial_aperturas(conn)
        return [AuditoriaSaldoInicialItem(**r) for r in registros]
    finally:
        conn.close()


@router.get("/jornada/actual", summary="Obtener jornada de tesorería actualmente activa")
def get_jornada_activa(user: AuthenticatedUser = Depends(get_current_user)):
    conn = _get_lab_db()
    jornada = obtener_jornada_actual(conn)
    conn.close()
    return jornada


@router.post("/jornada/abrir", summary="Abrir una nueva jornada de tesorería")
def post_abrir_jornada(
    payload: AbrirJornadaRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    conn = _get_lab_db()
    try:
        jornada = abrir_jornada(
            conn,
            usuario=user.username,
            observaciones=payload.observaciones,
        )
    except ErrorJornada as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    conn.close()
    return jornada


@router.post("/jornada/cerrar", summary="Cerrar la jornada de tesorería actual")
def post_cerrar_jornada(
    payload: CerrarJornadaRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    conn = _get_lab_db()
    try:
        jornada = cerrar_jornada(
            conn,
            jornada_id=payload.jornada_id,
            usuario=user.username,
            arqueo_centavos_por_fondo=payload.arqueo_centavos_por_fondo,
            observaciones=payload.observaciones,
        )
    except ErrorJornada as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    conn.close()
    return jornada


# =====================================================================
# LAB-005 — MOVIMIENTOS DE ENTRADA Y SALIDA
# =====================================================================

@router.post("/movimientos/entrada", summary="Registrar entrada de fondos (registerEntry)")
def post_registrar_entrada(
    payload: MovimientoRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    conn = _get_lab_db()
    try:
        res = registerEntry(
            conn,
            fund_code=payload.codigo_fondo,
            amount_cents=payload.monto_centavos,
            counterpart=payload.contrapartida,
            reason=payload.motivo,
            created_by=user.username,
            terminal_id=payload.terminal_id,
            idempotency_key=idempotency_key,
            evidence_reference=payload.referencia_evidencia,
        )
    except ErrorMovimiento as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    conn.close()
    return res


@router.post("/movimientos/salida", summary="Registrar salida de fondos (registerExit)")
def post_registrar_salida(
    payload: MovimientoRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    conn = _get_lab_db()
    try:
        res = registerExit(
            conn,
            fund_code=payload.codigo_fondo,
            amount_cents=payload.monto_centavos,
            counterpart=payload.contrapartida,
            reason=payload.motivo,
            created_by=user.username,
            terminal_id=payload.terminal_id,
            idempotency_key=idempotency_key,
            evidence_reference=payload.referencia_evidencia,
        )
    except ErrorSaldoInsuficiente as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except ErrorMovimiento as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    conn.close()
    return res


@router.get("/movimientos", summary="Listar movimientos de tesorería")
def get_movimientos(
    codigo_fondo: Optional[str] = None,
    jornada_id: Optional[int] = None,
    movement_type: Optional[str] = None,
    search: Optional[str] = None,
    limite: int = 50,
    user: AuthenticatedUser = Depends(get_current_user),
):
    conn = _get_lab_db()
    movs = listar_movimientos(
        conn,
        codigo_fondo=codigo_fondo,
        jornada_id=jornada_id,
        movement_type=movement_type,
        search=search,
        limite=limite,
    )
    conn.close()
    return movs


# =====================================================================
# LAB-006 — TRASLADOS DE FONDOS
# =====================================================================

@router.post("/traslados", summary="Iniciar despacho de traslado (transferFunds)")
def post_iniciar_traslado(
    payload: TrasladoRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    orig = payload.get_origin()
    dest = payload.get_destination()
    if orig == "1120" and dest == "1130":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El traslado de compensación 1120 -> 1130 es un proceso interno automático del outbox PREPAY y no admite ejecución manual por API.",
        )
    if orig == "1160" and dest == "1130":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El traslado 1160 -> 1130 es un barrido de cierre (executePrepayDrawerSweep) y no admite ejecución manual por API.",
        )

    conn = _get_lab_db()
    try:
        res = transferFunds(
            conn,
            origin_fund_code=orig,
            destination_fund_code=dest,
            amount_cents=payload.get_amount(),
            dispatched_by=user.username,
            reason=payload.get_reason(),
            idempotency_key=idempotency_key,
            evidence_reference=payload.get_evidence(),
        )
    except (ErrorTraslado, ValueError) as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    conn.close()
    return res


@router.post("/traslados/{transfer_id}/confirmar", summary="Confirmar recepción de traslado (confirmTransferReceipt)")
def post_confirmar_traslado(
    transfer_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    conn = _get_lab_db()
    try:
        res = confirmTransferReceipt(
            conn,
            transfer_id=transfer_id,
            received_by=user.username,
            idempotency_key=idempotency_key,
        )
    except (ErrorTraslado, ValueError) as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    conn.close()
    return res


@router.post("/traslados/{transfer_id}/cancelar", summary="Cancelar traslado pendiente (cancelPendingTransfer)")
def post_cancelar_traslado(
    transfer_id: str,
    payload: CancelarTrasladoRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    conn = _get_lab_db()
    try:
        res = cancelPendingTransfer(
            conn,
            transfer_id=transfer_id,
            cancelled_by=user.username,
            reason=payload.get_reason(),
            idempotency_key=idempotency_key,
        )
    except (ErrorTraslado, ValueError) as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    conn.close()
    return res


@router.get("/traslados", summary="Listar traslados de tesorería")
def get_traslados(
    limite: int = 50,
    status: Optional[str] = None,
    origin_fund_code: Optional[str] = None,
    destination_fund_code: Optional[str] = None,
    search: Optional[str] = None,
    user: AuthenticatedUser = Depends(get_current_user),
):
    conn = _get_lab_db()
    traslados = listar_traslados(
        conn,
        limite=limite,
        status=status,
        origin_fund_code=origin_fund_code,
        destination_fund_code=destination_fund_code,
        search=search,
    )
    conn.close()
    return traslados


# =====================================================================
# LAB-007 — RESERVAS DE FONDOS
# =====================================================================

@router.post("/reservas", summary="Crear reserva preventiva de fondos (createReservation)")
def post_crear_reserva(
    payload: ReservaRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    conn = _get_lab_db()
    try:
        res = createReservation(
            conn,
            fund_code=payload.fund_code,
            amount_cents=payload.amount_cents,
            reason=payload.reason,
            beneficiary=payload.beneficiary,
            created_by=user.username,
            obligation_id=payload.obligation_id,
            idempotency_key=idempotency_key,
            evidence_reference=payload.evidence_reference,
        )
    except ErrorReserva as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    conn.close()
    return res


@router.post("/reservas/{reservation_id}/aplicar", summary="Aplicar reserva contra obligación (applyReservation)")
def post_aplicar_reserva(
    reservation_id: str,
    payload: OperacionReservaRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    conn = _get_lab_db()
    try:
        res = applyReservation(
            conn,
            reservation_id=reservation_id,
            amount_cents=payload.amount_cents,
            performed_by=user.username,
            reason=payload.reason,
            idempotency_key=idempotency_key,
            evidence_reference=payload.evidence_reference,
        )
    except ErrorReserva as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    conn.close()
    return res


@router.post("/reservas/{reservation_id}/liberar", summary="Liberar reserva hacia disponible (releaseReservation)")
def post_liberar_reserva(
    reservation_id: str,
    payload: OperacionReservaRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    conn = _get_lab_db()
    try:
        res = releaseReservation(
            conn,
            reservation_id=reservation_id,
            amount_cents=payload.amount_cents,
            performed_by=user.username,
            reason=payload.reason,
            idempotency_key=idempotency_key,
            evidence_reference=payload.evidence_reference,
        )
    except ErrorReserva as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    conn.close()
    return res


@router.get("/reservas/{reservation_id}", summary="Consultar detalle e historial de una reserva (getReservationHistory)")
def get_historial_reserva(reservation_id: str, user: AuthenticatedUser = Depends(get_current_user)):
    conn = _get_lab_db()
    try:
        res = getReservationHistory(conn, reservation_id)
    except ErrorReserva as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    conn.close()
    return res


@router.get("/reservas", summary="Listar reservas de fondos")
def get_reservas(
    codigo_fondo: Optional[str] = None,
    solo_activas: bool = False,
    limite: int = 50,
    user: AuthenticatedUser = Depends(get_current_user),
):
    conn = _get_lab_db()
    reservas = listar_reservas(conn, codigo_fondo=codigo_fondo, solo_activas=solo_activas, limite=limite)
    conn.close()
    return reservas


# =====================================================================
# LAB-008 — DISPONIBILIDAD Y REPORTES
# =====================================================================

@router.get("/fondos/{codigo}/balance", summary="Obtener balance de un fondo (getFundBalance)")
def get_balance_fondo(codigo: str, user: AuthenticatedUser = Depends(get_current_user)):
    conn = _get_lab_db()
    try:
        res = getFundBalance(conn, codigo)
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    conn.close()
    return res


@router.get("/fondos/{codigo}/disponibilidad", summary="Obtener análisis de disponibilidad de un fondo (getFundAvailability)")
def get_disponibilidad_fondo(codigo: str, user: AuthenticatedUser = Depends(get_current_user)):
    conn = _get_lab_db()
    try:
        res = getFundAvailability(conn, codigo)
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    conn.close()
    return res


@router.get("/disponibilidad/resumen", summary="Obtener resumen integral de tesorería y disponibilidad (getTreasurySummary)")
def get_disponibilidad_resumen(user: AuthenticatedUser = Depends(get_current_user)):
    conn = _get_lab_db()
    summary = getTreasurySummary(conn)
    conn.close()
    return summary


# =====================================================================
# SPRINT 3 — LAB-009: ARQUEOS Y EXPEDIENTES DE DIFERENCIAS
# =====================================================================

class ArqueoRequest(BaseModel):
    codigo_fondo: Optional[str] = None
    fund_code: Optional[str] = None
    tipo_arqueo: Optional[str] = Field(default="TOTAL")
    reconciliation_type: Optional[str] = None
    monto_verificado_centavos: Optional[int] = None
    verified_amount_cents: Optional[int] = None
    observaciones: Optional[str] = None
    notes: Optional[str] = None
    crear_expediente_diferencia: bool = False
    notas_investigacion: Optional[str] = None
    # Extensiones para turnos y conciliación digital
    turno: Optional[str] = None
    es_digital: bool = False
    evidencia_referencia: Optional[str] = None
    fecha_consulta: Optional[str] = None
    # Autorización inmediata por Alan o Yilda si surge diferencia
    autorizar_inmediato: bool = False
    observaciones_autorizacion: Optional[str] = None

    def get_fund_code(self) -> str:
        code = self.codigo_fondo or self.fund_code
        if not code:
            raise ValueError("El código de fondo es obligatorio.")
        return code

    def get_verified_cents(self) -> int:
        amt = self.monto_verificado_centavos if self.monto_verificado_centavos is not None else self.verified_amount_cents
        if amt is None:
            raise ValueError("El monto verificado o declarado es obligatorio.")
        return amt

    def get_notes(self) -> Optional[str]:
        return self.observaciones or self.notes

    def get_type(self) -> str:
        return self.reconciliation_type or self.tipo_arqueo or "TOTAL"


class ResolverExpedienteRequest(BaseModel):
    notas_resolucion: str
    referencia_evidencia: str


class ExpedienteRequest(BaseModel):
    notas_investigacion: str


class AutorizarDiferenciaRequest(BaseModel):
    observaciones: Optional[str] = Field(None, description="Observaciones breves de la autorización de Alan o Yilda")


class ActualizarObservacionArqueoRequest(BaseModel):
    observaciones: str = Field(..., min_length=1, description="Nueva observación documentada del arqueo")


@router.post("/arqueos", summary="Registrar arqueo físico o conciliación digital")
def post_crear_arqueo(
    payload: ArqueoRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    if user.username.lower() not in ("alan", "yilda"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Usuario '{user.username}' no autorizado. Solo Alan y Yilda pueden realizar arqueos de tesorería.",
        )
    conn = _get_lab_db()
    try:
        fund_code = payload.get_fund_code()
        amount_cents = payload.get_verified_cents()
        notes = payload.get_notes()
        rec_type = payload.get_type()

        if payload.es_digital or fund_code in ("1210", "1220", "1230"):
            res = reconcileDigitalAccount(
                conn,
                fund_code=fund_code,
                declared_amount_cents=amount_cents,
                user_id=user.username,
                evidence_reference=payload.evidencia_referencia,
                consultation_timestamp=payload.fecha_consulta,
                notes=notes,
                investigation_notes=payload.notas_investigacion,
                reconciliation_type=rec_type,
            )
        else:
            res = reconcilePhysicalFund(
                conn,
                fund_code=fund_code,
                verified_amount_cents=amount_cents,
                user_id=user.username,
                shift_name=payload.turno,
                notes=notes,
                create_difference_case=payload.crear_expediente_diferencia,
                investigation_notes=payload.notas_investigacion,
                reconciliation_type=rec_type,
            )
    except (ErrorArqueo, ValueError) as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    conn.close()
    return res


@router.patch("/arqueos/{reconciliation_id}/observaciones", summary="Actualizar observación de un arqueo con trazabilidad")
def patch_observacion_arqueo(
    reconciliation_id: str,
    payload: ActualizarObservacionArqueoRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    if user.username.lower() not in ("alan", "yilda"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Usuario '{user.username}' no autorizado. Solo Alan y Yilda pueden modificar observaciones de arqueos.",
        )
    conn = _get_lab_db()
    try:
        res = updateReconciliationNotes(
            conn,
            reconciliation_id=reconciliation_id,
            new_notes=payload.observaciones,
            user_id=user.username,
        )
    except ErrorArqueo as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    conn.close()
    return res


@router.post("/arqueos/{reconciliation_id}/autorizar", summary="Autorizar diferencia de arqueo por administrador (Alan o Yilda)")
def post_autorizar_diferencia(
    reconciliation_id: str,
    payload: Optional[AutorizarDiferenciaRequest] = None,
    user: AuthenticatedUser = Depends(get_current_user),
):
    if user.username.lower() not in ("alan", "yilda"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Usuario '{user.username}' no autorizado. Solo Alan y Yilda pueden autorizar diferencias de arqueo.",
        )
    conn = _get_lab_db()
    try:
        obs = payload.observaciones if payload else None
        res = authorizeDifference(
            conn,
            reconciliation_id=reconciliation_id,
            user_id=user.username,
            notes=obs,
        )
    except (ErrorArqueo, ValueError) as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    conn.close()
    return res


@router.get("/fondos/{codigo_fondo}/traslados-transito", summary="Consultar traslados en tránsito que afectan a un fondo")
def get_traslados_transito_fondo(codigo_fondo: str, user: AuthenticatedUser = Depends(get_current_user)):
    conn = _get_lab_db()
    res = getPendingTransfersForFund(conn, codigo_fondo)
    conn.close()
    return res


@router.get("/arqueos/{reconciliation_id}", summary="Consultar detalle de un arqueo (getReconciliation)")
def get_arqueo(reconciliation_id: str, user: AuthenticatedUser = Depends(get_current_user)):
    conn = _get_lab_db()
    res = getReconciliation(conn, reconciliation_id)
    conn.close()
    if not res:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Arqueo no encontrado.")
    return res


@router.get("/arqueos", summary="Historial de arqueos (getReconciliationHistory)")
def get_historial_arqueos(
    codigo_fondo: Optional[str] = None,
    jornada_id: Optional[int] = None,
    limite: int = 50,
    user: AuthenticatedUser = Depends(get_current_user),
):
    conn = _get_lab_db()
    res = getReconciliationHistory(conn, fund_code=codigo_fondo, journal_id=jornada_id, limit=limite)
    conn.close()
    return res


@router.post("/arqueos/{reconciliation_id}/expediente", summary="Abrir expediente de diferencia (createDifferenceCase)")
def post_abrir_expediente(
    reconciliation_id: str,
    payload: ExpedienteRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    conn = _get_lab_db()
    try:
        res = createDifferenceCase(
            conn,
            reconciliation_id=reconciliation_id,
            investigation_notes=payload.notas_investigacion,
            user_id=user.username,
        )
    except ErrorArqueo as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    conn.close()
    return res


@router.post("/expedientes/{case_id}/resolver", summary="Resolver expediente de diferencia (resolveDifferenceCase)")
def post_resolver_expediente(
    case_id: str,
    payload: ResolverExpedienteRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    conn = _get_lab_db()
    try:
        res = resolveDifferenceCase(
            conn,
            case_id=case_id,
            resolution_notes=payload.notas_resolucion,
            evidence_reference=payload.referencia_evidencia,
            resolved_by=user.username,
        )
    except ErrorArqueo as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    conn.close()
    return res


@router.get("/expedientes", summary="Listar expedientes de diferencias")
def get_expedientes(
    codigo_fondo: Optional[str] = None,
    jornada_id: Optional[int] = None,
    estado: Optional[str] = None,
    limite: int = 50,
    user: AuthenticatedUser = Depends(get_current_user),
):
    conn = _get_lab_db()
    res = getDifferenceCases(conn, fund_code=codigo_fondo, journal_id=jornada_id, status=estado, limit=limite)
    conn.close()
    return res


# =====================================================================
# SPRINT 3 — LAB-010: CIERRES DEFINITIVOS Y PROTOCOLO DE CIERRE
# =====================================================================

class IniciarCierreRequest(BaseModel):
    journal_id: int


class VaciadoRequest(BaseModel):
    journal_id: int
    motivo: Optional[str] = "Vaciado obligatorio de cierre 1110 -> 1120"


class ConfirmarCierreRequest(BaseModel):
    journal_id: int
    observaciones: Optional[str] = None
    observations: Optional[str] = None
    allow_pending_transfers: bool = False
    allow_nonzero_1110: bool = False
    permitir_traslados_pendientes: Optional[bool] = None
    permitir_saldo_1110: Optional[bool] = None

    def get_observations(self) -> Optional[str]:
        return self.observaciones or self.observations

    def get_allow_pending_transfers(self) -> bool:
        if self.permitir_traslados_pendientes is not None:
            return self.permitir_traslados_pendientes
        return self.allow_pending_transfers

    def get_allow_nonzero_1110(self) -> bool:
        if self.permitir_saldo_1110 is not None:
            return self.permitir_saldo_1110
        return self.allow_nonzero_1110


@router.post("/cierres/iniciar", summary="Iniciar proceso de cierre de jornada (beginClosure)")
def post_iniciar_cierre(
    payload: IniciarCierreRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    if user.username.lower() not in ("alan", "yilda"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Usuario '{user.username}' no autorizado. Solo Alan y Yilda pueden iniciar cierres de tesorería.",
        )
    conn = _get_lab_db()
    try:
        res = beginClosure(conn, journal_id=payload.journal_id, user_id=user.username)
    except ErrorCierre as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    conn.close()
    return res


@router.get("/cierres/validar/{journal_id}", summary="Validar precondiciones de cierre (validateClosure)")
def get_validar_cierre(
    journal_id: int,
    allow_pending_transfers: bool = False,
    allow_nonzero_1110: bool = False,
    user: AuthenticatedUser = Depends(get_current_user),
):
    conn = _get_lab_db()
    try:
        res = validateClosure(
            conn,
            journal_id=journal_id,
            allow_pending_transfers=allow_pending_transfers,
            allow_nonzero_1110=allow_nonzero_1110,
        )
    except ErrorCierre as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    conn.close()
    return res


@router.post("/cierres/vaciado-1110", summary="Ejecutar vaciado de Caja Principal 1110 a 1120 (executeCashDrawerSweep)")
def post_vaciado_1110(
    payload: VaciadoRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    if user.username.lower() not in ("alan", "yilda"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Usuario '{user.username}' no autorizado. Solo Alan y Yilda pueden ejecutar el vaciado de cierre.",
        )
    conn = _get_lab_db()
    try:
        res = executeCashDrawerSweep(
            conn,
            journal_id=payload.journal_id,
            user_id=user.username,
            reason=payload.motivo or "Vaciado obligatorio de cierre 1110 -> 1120",
        )
    except (ErrorCierre, ErrorTraslado) as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    conn.close()
    return res


@router.post("/cierres/vaciado-1160", summary="Ejecutar vaciado de Caja PREPAY 1160 a 1130 (executePrepayDrawerSweep)")
def post_vaciado_1160(
    payload: VaciadoRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    if user.username.lower() not in ("alan", "yilda"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Usuario '{user.username}' no autorizado. Solo Alan y Yilda pueden ejecutar el vaciado PREPAY de cierre.",
        )
    conn = _get_lab_db()
    try:
        res = executePrepayDrawerSweep(
            conn,
            journal_id=payload.journal_id,
            user_id=user.username,
            reason=payload.motivo or "Barrido obligatorio de cierre PREPAY 1160 -> 1130",
        )
    except (ErrorCierre, ErrorTraslado) as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    conn.close()
    return res


@router.post("/cierres/confirmar", summary="Confirmar cierre definitivo con snapshot canónico (confirmClosure)")
def post_confirmar_cierre(
    payload: ConfirmarCierreRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    if user.username.lower() not in ("alan", "yilda"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Usuario '{user.username}' no autorizado. Solo Alan y Yilda pueden confirmar el cierre de jornada.",
        )
    conn = _get_lab_db()
    try:
        res = confirmClosure(
            conn,
            journal_id=payload.journal_id,
            user_id=user.username,
            observations=payload.get_observations(),
            allow_pending_transfers=payload.get_allow_pending_transfers(),
            allow_nonzero_1110=payload.get_allow_nonzero_1110(),
        )
    except ErrorCierre as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    conn.close()
    return res


@router.get("/cierres", summary="Historial de cierres definitivos (getClosureHistory)")
def get_historial_cierres(limite: int = 50, user: AuthenticatedUser = Depends(get_current_user)):
    conn = _get_lab_db()
    res = getClosureHistory(conn, limit=limite)
    conn.close()
    return res


@router.get("/cierres/{journal_id}", summary="Consultar snapshot de cierre confirmado (getClosure)")
def get_cierre(journal_id: int, user: AuthenticatedUser = Depends(get_current_user)):
    conn = _get_lab_db()
    res = getClosure(conn, journal_id=journal_id)
    conn.close()
    if not res:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cierre no encontrado para la jornada indicada.")
    return res


@router.post("/jornada/siguiente", summary="Abrir siguiente jornada con saldo 1110 = 0 (openNextJournal)")
def post_siguiente_jornada(
    payload: AbrirJornadaRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    if user.username.lower() not in ("alan", "yilda"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Usuario '{user.username}' no autorizado. Solo Alan y Yilda pueden abrir una nueva jornada de tesorería.",
        )
    conn = _get_lab_db()
    try:
        res = openNextJournal(conn, user_id=user.username, observations=payload.observaciones)
    except (ErrorCierre, ErrorJornada) as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    conn.close()
    return res


# =====================================================================
# SPRINT 3 — LAB-011: REVERSIONES INMUTABLES
# =====================================================================

class RevertirMovimientoRequest(BaseModel):
    movement_id: str
    motivo: str
    referencia_evidencia: Optional[str] = None
    monto_parcial_centavos: Optional[int] = None


class RevertirTrasladoRequest(BaseModel):
    transfer_id: str
    motivo: str
    referencia_evidencia: Optional[str] = None


class RevertirReservaRequest(BaseModel):
    reservation_id: str
    operation_id: int
    motivo: str
    referencia_evidencia: Optional[str] = None


@router.post("/reversiones/movimiento", summary="Revertir movimiento (reverseMovement)")
def post_revertir_movimiento(
    payload: RevertirMovimientoRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    conn = _get_lab_db()
    try:
        res = reverseMovement(
            conn,
            movement_id=payload.movement_id,
            user_id=user.username,
            reason=payload.motivo,
            evidence_reference=payload.referencia_evidencia,
            idempotency_key=idempotency_key,
            partial_amount_cents=payload.monto_parcial_centavos,
        )
    except ErrorReversion as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    conn.close()
    return res


@router.post("/reversiones/traslado", summary="Revertir traslado (reverseTransfer)")
def post_revertir_traslado(
    payload: RevertirTrasladoRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    conn = _get_lab_db()
    try:
        res = reverseTransfer(
            conn,
            transfer_id=payload.transfer_id,
            user_id=user.username,
            reason=payload.motivo,
            evidence_reference=payload.referencia_evidencia,
            idempotency_key=idempotency_key,
        )
    except ErrorReversion as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    conn.close()
    return res


@router.post("/reversiones/reserva", summary="Revertir operación de reserva (reverseReservationOperation)")
def post_revertir_reserva(
    payload: RevertirReservaRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    conn = _get_lab_db()
    try:
        res = reverseReservationOperation(
            conn,
            reservation_id=payload.reservation_id,
            operation_id=payload.operation_id,
            user_id=user.username,
            reason=payload.motivo,
            evidence_reference=payload.referencia_evidencia,
            idempotency_key=idempotency_key,
        )
    except ErrorReversion as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    conn.close()
    return res


@router.get("/reversiones", summary="Listar reversiones ejecutadas (getReversals)")
def get_todas_reversiones(
    tipo: Optional[str] = None,
    limite: int = 50,
    user: AuthenticatedUser = Depends(get_current_user),
):
    conn = _get_lab_db()
    res = getReversals(conn, target_type=tipo, limit=limite)
    conn.close()
    return res


# =====================================================================
# SPRINT 3 — LAB-012: AUDITORÍA CON HASH CHAIN & OUTBOX TRANSACCIONAL
# =====================================================================

@router.get("/auditoria/cadena", summary="Consultar pista de auditoría encadenada (queryAuditEvents)")
def get_auditoria_cadena(
    tipo_entidad: Optional[str] = None,
    entidad_id: Optional[str] = None,
    tipo_evento: Optional[str] = None,
    limite: int = 100,
    user: AuthenticatedUser = Depends(get_current_user),
):
    conn = _get_lab_db()
    res = queryAuditEvents(conn, aggregate_type=tipo_entidad, aggregate_id=entidad_id, event_type=tipo_evento, limit=limite)
    conn.close()
    return res


@router.get("/auditoria/verificar", summary="Verificar integridad matemática de la cadena de hashes (verifyAuditChain)")
def get_verificar_auditoria(user: AuthenticatedUser = Depends(get_current_user)):
    conn = _get_lab_db()
    res = verifyAuditChain(conn)
    conn.close()
    return res


class ProcesarOutboxRequest(BaseModel):
    consumer_id: str = "LOCAL_LAB_WORKER"
    max_batch: int = 50


@router.get("/outbox/estado", summary="Consultar contadores de estado del Outbox (getOutboxStatus)")
def get_estado_outbox(user: AuthenticatedUser = Depends(get_current_user)):
    conn = _get_lab_db()
    res = getOutboxStatus(conn)
    conn.close()
    return res


@router.post("/outbox/procesar", summary="Procesar lote de eventos de Outbox (processOutbox)")
def post_procesar_outbox(
    payload: ProcesarOutboxRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    conn = _get_lab_db()
    res = processOutbox(conn, consumer_id=payload.consumer_id, max_batch=payload.max_batch)
    conn.close()
    return res


@router.post("/outbox/{event_id}/reprocesar", summary="Reprocesar evento en letra muerta (retryDeadLetter)")
def post_reprocesar_outbox(
    event_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    conn = _get_lab_db()
    try:
        res = retryDeadLetter(conn, event_id=event_id, authorized_by=user.username)
    except ErrorOutbox as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    conn.close()
    return res


# =====================================================================
# SPRINT 3 — FASE 0: OPERACIÓN ATÓMICA DE EGRESO RESERVADO
# =====================================================================

class EgresoReservadoRequest(BaseModel):
    amount_cents: int
    counterpart: str
    reason: str
    evidence_reference: Optional[str] = None


@router.post("/reservas/{reservation_id}/egreso-reservado", summary="Aplicación de reserva y egreso atómico (executeReservedExit)")
def post_egreso_reservado(
    reservation_id: str,
    payload: EgresoReservadoRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    conn = _get_lab_db()
    try:
        res = executeReservedExit(
            conn,
            reservation_id=reservation_id,
            amount_cents=payload.amount_cents,
            counterpart=payload.counterpart,
            reason=payload.reason,
            performed_by=user.username,
            idempotency_key=idempotency_key,
            evidence_reference=payload.evidence_reference,
        )
    except ErrorReserva as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    conn.close()
    return res


# =====================================================================
# CUENTA CORRIENTE PREPAY / DAVID
# =====================================================================

class AcreditacionDavidRequest(BaseModel):
    monto_centavos: int = Field(..., gt=0, description="Monto en centavos a acreditar en 1220")
    referencia: str = Field(..., min_length=1, description="Comprobante, lote o número de transferencia")
    motivo: Optional[str] = Field(None, description="Observaciones o detalle de la acreditación")
    fecha_referencia: Optional[str] = Field(None, description="Fecha de acreditación (YYYY-MM-DD)")


class PagoDavidRequest(BaseModel):
    monto_centavos: int = Field(..., gt=0, description="Monto en centavos a pagar a David desde 1130")
    referencia: str = Field(..., min_length=1, description="Comprobante o recibo de pago a David")
    motivo: Optional[str] = Field(None, description="Observaciones o detalle del pago")
    fecha_referencia: Optional[str] = Field(None, description="Fecha del pago (YYYY-MM-DD)")


class ReconocimientoDeudaHistoricaRequest(BaseModel):
    monto_centavos: int = Field(..., gt=0, description="Monto en centavos de deuda histórica reconocida")
    fecha_referencia: str = Field(..., min_length=10, description="Fecha de origen de la deuda histórica (YYYY-MM-DD)")
    motivo: str = Field(..., min_length=3, description="Justificación obligatoria de la deuda reconocida")
    referencia_externa: Optional[str] = Field(None, description="Número de acta, expediente o referencia documental")
    confirmacion_explicita: bool = Field(..., description="Confirmación explícita obligatoria del usuario")


@router.get("/cuenta-corriente/david", summary="Consultar estado y movimientos de la Cuenta Corriente PREPAY / David")
def get_cuenta_corriente_david(
    fecha_corte: Optional[str] = None,
    user: AuthenticatedUser = Depends(get_current_user),
):
    conn = _get_lab_db()
    try:
        estado = obtener_estado_cuenta_david(conn, fecha_corte=fecha_corte)
    finally:
        conn.close()
    return estado


@router.post("/cuenta-corriente/david/acreditacion", summary="Registrar acreditación de saldo digital PREPAY provisto por David")
def post_acreditacion_david(
    payload: AcreditacionDavidRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    if not idempotency_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Se requiere cabecera 'Idempotency-Key' para registrar la acreditación.",
        )
    conn = _get_lab_db()
    try:
        res = registrar_acreditacion_david(
            conn,
            amount_cents=payload.monto_centavos,
            referencia=payload.referencia,
            actor=user.username,
            idempotency_key=idempotency_key,
            motivo=payload.motivo,
            fecha_referencia=payload.fecha_referencia,
        )
    except ErrorValidacionDavid as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    conn.close()
    return res


@router.post("/cuenta-corriente/david/pago", summary="Registrar pago efectivo a David cancelando obligación pendiente desde fondo 1130")
def post_pago_david(
    payload: PagoDavidRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    if not idempotency_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Se requiere cabecera 'Idempotency-Key' para registrar el pago.",
        )
    conn = _get_lab_db()
    try:
        res = registrar_pago_david(
            conn,
            amount_cents=payload.monto_centavos,
            referencia=payload.referencia,
            actor=user.username,
            idempotency_key=idempotency_key,
            motivo=payload.motivo,
            fecha_referencia=payload.fecha_referencia,
        )
    except ErrorSaldoInsuficienteDavid as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except ErrorValidacionDavid as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    conn.close()
    return res


@router.post("/cuenta-corriente/david/reconocimiento-historico", summary="Reconocer deuda histórica previa con David")
def post_reconocimiento_historico_david(
    payload: ReconocimientoDeudaHistoricaRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    if not idempotency_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Se requiere cabecera 'Idempotency-Key' para registrar el reconocimiento histórico.",
        )
    conn = _get_lab_db()
    try:
        res = registrar_reconocimiento_deuda_historica(
            conn,
            amount_cents=payload.monto_centavos,
            fecha_referencia=payload.fecha_referencia,
            motivo=payload.motivo,
            actor=user.username,
            idempotency_key=idempotency_key,
            confirmacion_explicita=payload.confirmacion_explicita,
            referencia_externa=payload.referencia_externa,
        )
    except ErrorValidacionDavid as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    conn.close()
    return res


# ==============================================================================
# ENDPOINTS: CIRCUITO DE ANTICIPOS Y REPOSICIÓN DE PREVENTISTAS
# ==============================================================================

class CrearAnticipoPreventistaRequest(BaseModel):
    proveedor: str = Field(..., min_length=1, description="Nombre o identificación del preventista/proveedor destinatario")
    monto_centavos: int = Field(..., gt=0, description="Importe efectivamente entregado en centavos enteros (> 0)")
    motivo: str = Field(..., min_length=1, description="Motivo o justificación operativa del anticipo")
    referencia_documento: Optional[str] = Field(None, description="Remito, factura, recibo o comprobante disponible")
    pedido_id: Optional[str] = Field(None, description="Identificador correlativo de pedido vinculado si existe")


class ReponerAnticipoPreventistaRequest(BaseModel):
    monto_centavos: Optional[int] = Field(None, gt=0, description="Importe parcial a reponer en centavos. Si es omitido, repone el total pendiente.")
    motivo: Optional[str] = Field(None, description="Observaciones o notas adicionales de la reposición")


@router.get("/preventistas/anticipos", summary="Consultar lista de anticipos de preventistas")
def get_anticipos_preventistas(
    estado: Optional[str] = None,
    proveedor: Optional[str] = None,
    user: AuthenticatedUser = Depends(get_current_user),
):
    conn = _get_lab_db()
    try:
        anticipos = listar_anticipos_preventistas(conn, estado=estado, proveedor=proveedor)
    finally:
        conn.close()
    return anticipos


@router.post("/preventistas/anticipos", summary="Registrar nuevo anticipo a preventista debitando Fondo 1130")
def post_crear_anticipo_preventista(
    payload: CrearAnticipoPreventistaRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    if not idempotency_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Se requiere cabecera 'Idempotency-Key' para registrar el anticipo de preventista.",
        )
    conn = _get_lab_db()
    try:
        res = crear_anticipo_preventista(
            conn=conn,
            proveedor=payload.proveedor,
            monto_cents=payload.monto_centavos,
            motivo=payload.motivo,
            actor=user.username,
            idempotency_key=idempotency_key,
            referencia_documento=payload.referencia_documento,
            pedido_id=payload.pedido_id,
        )
    except ErrorSaldoInsuficienteAnticipo as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except ErrorValidacionAnticipo as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    conn.close()
    return res


@router.get("/preventistas/anticipos/{anticipo_id}", summary="Consultar detalle de un anticipo específico")
def get_detalle_anticipo_preventista(
    anticipo_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    conn = _get_lab_db()
    try:
        detalle = obtener_anticipo_preventista(conn, anticipo_id=anticipo_id)
        if not detalle:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Anticipo #{anticipo_id} no encontrado.",
            )
    finally:
        conn.close()
    return detalle


@router.post("/preventistas/anticipos/{anticipo_id}/reposicion", summary="Registrar reposición jerárquica de anticipo (1° 1120, 2° 1110 -> 1130)")
def post_reponer_anticipo_preventista(
    anticipo_id: str,
    payload: ReponerAnticipoPreventistaRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    if not idempotency_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Se requiere cabecera 'Idempotency-Key' para registrar la reposición del anticipo.",
        )
    conn = _get_lab_db()
    try:
        res = reponer_anticipo_preventista(
            conn=conn,
            anticipo_id=anticipo_id,
            actor=user.username,
            idempotency_key=idempotency_key,
            monto_cents=payload.monto_centavos,
            motivo=payload.motivo,
        )
    except AnticipoNoEncontrado as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ErrorSaldoInsuficienteReposicion as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except ErrorValidacionAnticipo as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    conn.close()
    return res


@router.get("/preventistas/anticipos/{anticipo_id}/reposiciones", summary="Consultar historial de reposiciones de un anticipo")
def get_reposiciones_de_anticipo(
    anticipo_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    conn = _get_lab_db()
    try:
        repos = obtener_historial_reposiciones(conn, anticipo_id=anticipo_id)
    finally:
        conn.close()
    return repos


@router.get("/preventistas/reposiciones", summary="Consultar historial global de todas las reposiciones ejecutadas")
def get_todas_las_reposiciones(
    user: AuthenticatedUser = Depends(get_current_user),
):
    conn = _get_lab_db()
    try:
        repos = obtener_historial_reposiciones(conn, anticipo_id=None)
    finally:
        conn.close()
    return repos


@router.get("/preventistas/resumen", summary="Resumen consolidado de obligaciones de reposición preventistas")
def get_resumen_preventistas(
    user: AuthenticatedUser = Depends(get_current_user),
):
    conn = _get_lab_db()
    try:
        resumen = obtener_resumen_obligaciones_preventistas(conn)
    finally:
        conn.close()
    return resumen


# =============================================================================
# CUENTA CORRIENTE CLARO COMERCIOS / EZEQUIEL Y FONDEOS PENDIENTES
# =============================================================================

class AcreditacionEzequielRequest(BaseModel):
    monto_centavos: int = Field(..., gt=0, description="Monto en centavos a acreditar en 1230")
    referencia: str = Field(..., min_length=1, description="Comprobante, lote o número de transferencia")
    motivo: Optional[str] = Field(None, description="Observaciones o detalle de la acreditación")
    fecha_referencia: Optional[str] = Field(None, description="Fecha de acreditación (YYYY-MM-DD)")


class LiquidacionEzequielRequest(BaseModel):
    monto_1150_cents: int = Field(0, ge=0, description="Efectivo en custodia Claro (1150)")
    monto_drugstore_cents: int = Field(0, ge=0, description="Efectivo complementario Drugstore (1110 o 1120)")
    fondo_drugstore: str = Field("1110", description="Código del fondo Drugstore utilizado (1110 o 1120)")
    monto_transferencia_cents: int = Field(0, ge=0, description="Transferencia bancaria Mercado Pago (1210 -> 1230)")
    referencia: str = Field(..., min_length=1, description="Comprobante o recibo emitido por el cobrador de Claro / Ezequiel")
    motivo: Optional[str] = Field(None, description="Observaciones de la liquidación")
    fecha_referencia: Optional[str] = Field(None, description="Fecha de liquidación (YYYY-MM-DD)")


class ReconocimientoDeudaHistoricaEzequielRequest(BaseModel):
    monto_centavos: int = Field(..., gt=0, description="Monto en centavos de deuda histórica reconocida")
    fecha_referencia: str = Field(..., min_length=10, description="Fecha de origen de la deuda histórica (YYYY-MM-DD)")
    motivo: str = Field(..., min_length=3, description="Justificación obligatoria de la deuda reconocida")
    referencia_externa: Optional[str] = Field(None, description="Número de acta, expediente o referencia documental")
    confirmacion_explicita: bool = Field(..., description="Confirmación explícita obligatoria del usuario")


class EjecutarFondeoRequest(BaseModel):
    observaciones: Optional[str] = Field(None, description="Notas de la ejecución del fondeo")
    fondo_origen: Optional[str] = Field(None, description="Fondo origen alternativo autorizado ('1110' o '1120')")


@router.get("/cuenta-corriente/ezequiel", summary="Consultar estado y movimientos de la Cuenta Corriente Claro / Ezequiel")
def get_cuenta_corriente_ezequiel(
    fecha_corte: Optional[str] = None,
    user: AuthenticatedUser = Depends(get_current_user),
):
    conn = _get_lab_db()
    try:
        pos = consultar_posicion_ezequiel(conn, fecha_corte=fecha_corte)
    finally:
        conn.close()
    return pos


@router.post("/cuenta-corriente/ezequiel/acreditacion", summary="Registrar acreditación de saldo digital Claro provisto por Ezequiel")
def post_acreditacion_ezequiel(
    payload: AcreditacionEzequielRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    if not idempotency_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Se requiere cabecera 'Idempotency-Key' para registrar la acreditación.",
        )
    conn = _get_lab_db()
    try:
        res = registrar_acreditacion_ezequiel(
            conn,
            amount_cents=payload.monto_centavos,
            referencia=payload.referencia,
            actor=user.username,
            idempotency_key=idempotency_key,
            motivo=payload.motivo,
            fecha_referencia=payload.fecha_referencia,
        )
    except ErrorValidacionEzequiel as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    conn.close()
    return res


@router.post("/cuenta-corriente/ezequiel/liquidacion", summary="Registrar liquidación (pago) a Ezequiel con soporte multi-fondo")
def post_liquidacion_ezequiel(
    payload: LiquidacionEzequielRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    if not idempotency_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Se requiere cabecera 'Idempotency-Key' para registrar la liquidación.",
        )
    conn = _get_lab_db()
    try:
        res = registrar_liquidacion_ezequiel(
            conn,
            monto_1150_cents=payload.monto_1150_cents,
            monto_drugstore_cents=payload.monto_drugstore_cents,
            fondo_drugstore=payload.fondo_drugstore,
            monto_transferencia_cents=payload.monto_transferencia_cents,
            referencia=payload.referencia,
            actor=user.username,
            idempotency_key=idempotency_key,
            motivo=payload.motivo,
            fecha_referencia=payload.fecha_referencia,
        )
    except ErrorSaldoInsuficienteEzequiel as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except ErrorValidacionEzequiel as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    conn.close()
    return res


@router.post("/cuenta-corriente/ezequiel/reconocimiento-historico", summary="Reconocer deuda histórica previa con Ezequiel")
def post_reconocimiento_historico_ezequiel(
    payload: ReconocimientoDeudaHistoricaEzequielRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    if not idempotency_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Se requiere cabecera 'Idempotency-Key' para registrar el reconocimiento histórico.",
        )
    conn = _get_lab_db()
    try:
        res = registrar_reconocimiento_historico_ezequiel(
            conn,
            amount_cents=payload.monto_centavos,
            fecha_referencia=payload.fecha_referencia,
            motivo=payload.motivo,
            actor=user.username,
            idempotency_key=idempotency_key,
            confirmacion_explicita=payload.confirmacion_explicita,
            referencia_externa=payload.referencia_externa,
        )
    except ErrorValidacionEzequiel as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    conn.close()
    return res


@router.get("/fondeos-pendientes", summary="Listar obligaciones de fondeo físico pendientes de custodia (Claro/Prepay)")
def get_fondeos_pendientes(
    estado: Optional[str] = "PENDIENTE",
    tipo_circuito: Optional[str] = None,
    user: AuthenticatedUser = Depends(get_current_user),
):
    conn = _get_lab_db()
    try:
        res = listar_fondeos_pendientes(conn, estado=estado, tipo_circuito=tipo_circuito)
    finally:
        conn.close()
    return res


@router.post("/fondeos-pendientes/{fondeo_id}/ejecutar", summary="Ejecutar fondeo físico pendiente una vez disponible el efectivo")
def post_ejecutar_fondeo(
    fondeo_id: int,
    payload: Optional[EjecutarFondeoRequest] = None,
    user: AuthenticatedUser = Depends(get_current_user),
):
    conn = _get_lab_db()
    try:
        res = ejecutar_fondeo_pendiente(
            conn,
            fondeo_id=fondeo_id,
            actor=user.username,
            fondo_origen=payload.fondo_origen if payload else None,
        )
    except ErrorSaldoInsuficienteEzequiel as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except ErrorValidacionEzequiel as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    conn.close()
    return res

