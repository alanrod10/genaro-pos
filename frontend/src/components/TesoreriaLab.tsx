import React, { useState, useEffect, useCallback } from "react";
import {
  ArqueoTesoreria,
  AuditoriaFondoItem,
  CierreTesoreria,
  EventoAuditoria,
  ExpedienteDiferencia,
  EstadoFondo,
  FondoTesoreria,
  FundAvailability,
  JornadaTesoreria,
  MovimientoTesoreria,
  ReservaTesoreria,
  ResumenOutbox,
  ResumenTesoreria,
  ReversionTesoreria,
  TipoArqueo,
  TrasladoTesoreria,
  TreasurySummary,
  ValidacionCierre,
  VerificacionAuditoria,
  CommercialBridgeStatus,
  EventoComercialOutbox,
  EstadoCuentaDavidResponse,
  AnticipoPreventistaItem,
  ReposicionPreventistaItem,
  ResumenPreventistasResponse,
  SaldosInicialesResponse,
  SaldoInicialItem,
  AuditoriaSaldoInicialItem,
} from "../types/tesoreria";
import { TesoreriaService } from "../services/tesoreria";
import { formatMoney, parseMoney } from "../utils/money";

interface TesoreriaLabProps {
  currentUser?: { username: string; display_name?: string; role?: string };
}

type TabActiva =
  | "disponibilidad"
  | "movimientos"
  | "traslados"
  | "reservas"
  | "arqueos"
  | "cierres"
  | "reversiones"
  | "auditoria"
  | "outbox"
  | "cuenta_corriente_david"
  | "anticipos_preventistas"
  | "aperturas";

// Matriz canónica de transferencias internas autorizadas
const TRANSFERENCIAS_PERMITIDAS_SET = new Set([
  "1110->1120",
  "1120->1110",
  "1110->1140",
  "1140->1120",
  "1130->1120",
  "1130->1110",
  "1110->1130",
  "1110->1210",
  "1310->1110",
  "1310->1120",
]);

function getTransferPolicyWarning(origen: string, destino: string): string | null {
  if (origen === destino) return "El fondo de origen y destino deben ser diferentes.";
  const key = `${origen}->${destino}`;
  if (TRANSFERENCIAS_PERMITIDAS_SET.has(key)) return null;

  if (origen === "1150" || destino === "1150") {
    return "Fondo 1150 (Claro Efectivo) es custodia de terceros; no admite traslados internos comerciales.";
  }
  if (destino === "1310") {
    return "Fondo 1310 (Tránsito Preventistas) es una cuenta segregada externa; no puede recibir fondos propios del negocio.";
  }
  if (origen === "1220" || destino === "1220" || origen === "1230" || destino === "1230") {
    const target = origen === "1220" || origen === "1230" ? origen : destino;
    return `Las cuentas mayoristas digitales (${target}) requieren definición formal del propietario para operaciones de fondeo o retiro.`;
  }
  const oFis = origen.startsWith("11");
  const dFis = destino.startsWith("11");
  const oDig = origen.startsWith("12");
  const dDig = destino.startsWith("12");
  if ((oFis && dDig) || (oDig && dFis)) {
    return `VIOLACIÓN DE AISLAMIENTO: Prohibido transferir fondos entre cuentas físicas y cuentas digitales (${origen} -> ${destino}).`;
  }
  return `La combinación de traslado ${origen} -> ${destino} no está autorizada por la política de fondos.`;
}

export const TesoreriaLab: React.FC<TesoreriaLabProps> = ({ currentUser }) => {
  const [tabActiva, setTabActiva] = useState<TabActiva>("disponibilidad");
  const [resumen, setResumen] = useState<ResumenTesoreria | null>(null);
  const [summaryAvail, setSummaryAvail] = useState<TreasurySummary | null>(null);
  const [fondos, setFondos] = useState<FondoTesoreria[]>([]);
  const [jornadaActiva, setJornadaActiva] = useState<JornadaTesoreria | null>(null);
  const [movimientos, setMovimientos] = useState<MovimientoTesoreria[]>([]);
  const [movFondoFiltro, setMovFondoFiltro] = useState<string>("");
  const [movTipoFiltro, setMovTipoFiltro] = useState<string>("");
  const [movSearch, setMovSearch] = useState<string>("");
  const [traslados, setTraslados] = useState<TrasladoTesoreria[]>([]);
  const [reservas, setReservas] = useState<ReservaTesoreria[]>([]);
  const [cargando, setCargando] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // SPRINT 3 — Estados de Arqueos y Expedientes
  const [arqueos, setArqueos] = useState<ArqueoTesoreria[]>([]);
  const [expedientes, setExpedientes] = useState<ExpedienteDiferencia[]>([]);
  const [modalArqueo, setModalArqueo] = useState<boolean>(false);
  const [arqFondo, setArqFondo] = useState<string>("1110");
  const [arqTipo, setArqTipo] = useState<TipoArqueo>("PARTIAL");
  const [arqMontoStr, setArqMontoStr] = useState<string>("");
  const [arqObservaciones, setArqObservaciones] = useState<string>("");
  const [arqCrearExpediente, setArqCrearExpediente] = useState<boolean>(false);
  const [arqNotasInvestigacion, setArqNotasInvestigacion] = useState<string>("");
  const [expedienteSeleccionado, setExpedienteSeleccionado] = useState<ExpedienteDiferencia | null>(null);
  const [resNotas, setResNotas] = useState<string>("");
  const [resEvidencia, setResEvidencia] = useState<string>("");

  // FASE SIGUIENTE — Arqueos Físicos y Digitales
  const [subTabArqueos, setSubTabArqueos] = useState<"fisicos" | "digitales">("fisicos");
  const [arqTurno, setArqTurno] = useState<string>("MAÑANA");
  const [digFondo, setDigFondo] = useState<string>("1210");
  const [digFechaConsulta, setDigFechaConsulta] = useState<string>(new Date().toISOString().slice(0, 16));
  const [digMontoStr, setDigMontoStr] = useState<string>("");
  const [digEvidencia, setDigEvidencia] = useState<string>("");
  const [digObservaciones, setDigObservaciones] = useState<string>("");
  const [digCrearExpediente, setDigCrearExpediente] = useState<boolean>(false);
  const [digNotasInvestigacion, setDigNotasInvestigacion] = useState<string>("");

  // SPRINT 3 & FASE SIGUIENTE — Estados de Cierres
  const [validacionCierre, setValidacionCierre] = useState<ValidacionCierre | null>(null);
  const [cierreSnapshot, setCierreSnapshot] = useState<CierreTesoreria | null>(null);
  const [modalConfirmarCierre, setModalConfirmarCierre] = useState<boolean>(false);
  const [cierreObs, setCierreObs] = useState<string>("");
  const [vaciadoMsg, setVaciadoMsg] = useState<string | null>(null);
  const [allowPendingTransfers, setAllowPendingTransfers] = useState<boolean>(false);
  const [allowNonzero1110, setAllowNonzero1110] = useState<boolean>(false);
  const [historialCierres, setHistorialCierres] = useState<CierreTesoreria[]>([]);
  const [modalVerSnapshot, setModalVerSnapshot] = useState<CierreTesoreria | null>(null);

  // SPRINT 3 — Estados de Reversiones
  const [reversiones, setReversiones] = useState<ReversionTesoreria[]>([]);
  const [modalReversion, setModalReversion] = useState<{
    tipo: "MOVEMENT" | "TRANSFER" | "RESERVATION_OPERATION";
    targetId: string;
    subId?: number;
    montoMaxCents?: number;
  } | null>(null);
  const [revMotivo, setRevMotivo] = useState<string>("");
  const [revEvidencia, setRevEvidencia] = useState<string>("");
  const [revMontoParcialStr, setRevMontoParcialStr] = useState<string>("");

  // SPRINT 3 — Estados de Auditoría
  const [auditoriaEventos, setAuditoriaEventos] = useState<EventoAuditoria[]>([]);
  const [verificacionAuditoria, setVerificacionAuditoria] = useState<VerificacionAuditoria | null>(null);
  const [verificandoAuditoria, setVerificandoAuditoria] = useState<boolean>(false);

  // SPRINT 3 — Estados de Outbox
  const [outboxStatus, setOutboxStatus] = useState<ResumenOutbox | null>(null);
  const [outboxMsg, setOutboxMsg] = useState<string | null>(null);
  const [deadLetterId, setDeadLetterId] = useState<string>("");

  // ETAPA 5 — Estados del Commercial Bridge Monitor
  const [commBridgeStatus, setCommBridgeStatus] = useState<CommercialBridgeStatus | null>(null);
  const [commEvents, setCommEvents] = useState<EventoComercialOutbox[]>([]);
  const [commTotalEvents, setCommTotalEvents] = useState<number>(0);
  const [commStatusFilter, setCommStatusFilter] = useState<string>("ALL");
  const [commTypeFilter, setCommTypeFilter] = useState<string>("");
  const [commSearch, setCommSearch] = useState<string>("");
  const [commBridgeMsg, setCommBridgeMsg] = useState<string | null>(null);
  const [cargandoBridge, setCargandoBridge] = useState<boolean>(false);

  // SPRINT 3 — FASE 0 Egreso Reservado
  const [modalEgresoReservado, setModalEgresoReservado] = useState<ReservaTesoreria | null>(null);
  const [egresoMontoStr, setEgresoMontoStr] = useState<string>("");
  const [egresoContrapartida, setEgresoContrapartida] = useState<string>("");
  const [egresoMotivo, setEgresoMotivo] = useState<string>("");
  const [egresoEvidencia, setEgresoEvidencia] = useState<string>("");

  // Modales
  const [fondoSeleccionado, setFondoSeleccionado] = useState<FondoTesoreria | null>(null);
  const [auditoriaFondo, setAuditoriaFondo] = useState<AuditoriaFondoItem[]>([]);
  const [cargandoAuditoria, setCargandoAuditoria] = useState<boolean>(false);
  const [motivoCambio, setMotivoCambio] = useState<string>("");

  // Jornada
  const [modalAbrirJornada, setModalAbrirJornada] = useState<boolean>(false);
  const [obsApertura, setObsApertura] = useState<string>("");
  const [modalCerrarJornada, setModalCerrarJornada] = useState<boolean>(false);
  const [arqueoValores, setArqueoValores] = useState<Record<string, string>>({});
  const [obsCierre, setObsCierre] = useState<string>("");

  // Formulario de Entrada / Salida
  const [modalMovimiento, setModalMovimiento] = useState<"ENTRY" | "EXIT" | null>(null);
  const [movFondo, setMovFondo] = useState<string>("1110");
  const [movMontoStr, setMovMontoStr] = useState<string>("");
  const [movContrapartida, setMovContrapartida] = useState<string>("");
  const [movMotivo, setMovMotivo] = useState<string>("");
  const [movEvidencia, setMovEvidencia] = useState<string>("");

  // Formulario y filtros de Traslados (LAB-006)
  const [modalTraslado, setModalTraslado] = useState<boolean>(false);
  const [trfOrigen, setTrfOrigen] = useState<string>("1110");
  const [trfDestino, setTrfDestino] = useState<string>("1120");
  const [trfMontoStr, setTrfMontoStr] = useState<string>("");
  const [trfMotivo, setTrfMotivo] = useState<string>("");
  const [trfEvidencia, setTrfEvidencia] = useState<string>("");
  const [trfDespachando, setTrfDespachando] = useState<boolean>(false);
  const [trfFiltroEstado, setTrfFiltroEstado] = useState<string>("ALL");
  const [trfFiltroOrigen, setTrfFiltroOrigen] = useState<string>("");
  const [trfFiltroDestino, setTrfFiltroDestino] = useState<string>("");
  const [trfBusqueda, setTrfBusqueda] = useState<string>("");

  // Modales de confirmación y anulación de traslado
  const [modalConfirmacionTrf, setModalConfirmacionTrf] = useState<TrasladoTesoreria | null>(null);
  const [obsConfirmacionTrf, setObsConfirmacionTrf] = useState<string>("");
  const [confirmandoTrf, setConfirmandoTrf] = useState<boolean>(false);

  const [modalAnulacionTrf, setModalAnulacionTrf] = useState<TrasladoTesoreria | null>(null);
  const [motivoAnulacionTrf, setMotivoAnulacionTrf] = useState<string>("");
  const [anulandoTrf, setAnulandoTrf] = useState<boolean>(false);

  // Formulario de Reserva
  const [modalReserva, setModalReserva] = useState<boolean>(false);
  const [resFondo, setResFondo] = useState<string>("1130");
  const [resMontoStr, setResMontoStr] = useState<string>("");
  const [resMotivo, setResMotivo] = useState<string>("");
  const [resBeneficiario, setResBeneficiario] = useState<string>("");
  const [resObligationId, setResObligationId] = useState<string>("");

  // Detalle de Reserva
  const [reservaSeleccionada, setReservaSeleccionada] = useState<ReservaTesoreria | null>(null);
  const [modalAccionReserva, setModalAccionReserva] = useState<"APPLY" | "RELEASE" | null>(null);
  const [accionMontoStr, setAccionMontoStr] = useState<string>("");
  const [accionMotivo, setAccionMotivo] = useState<string>("");

  // =====================================================================
  // ESTADO: CUENTA CORRIENTE PREPAY / DAVID
  // =====================================================================
  const [estadoCCDavid, setEstadoCCDavid] = useState<EstadoCuentaDavidResponse | null>(null);
  const [cargandoCCDavid, setCargandoCCDavid] = useState<boolean>(false);
  const [filtroFechaCorteDavid, setFiltroFechaCorteDavid] = useState<string>("");
  const [modalCCDavid, setModalCCDavid] = useState<"ACREDITACION" | "PAGO" | "HISTORICO" | null>(null);

  // Formulario Acreditación David
  const [formAcrMonto, setFormAcrMonto] = useState<string>("");
  const [formAcrRef, setFormAcrRef] = useState<string>("");
  const [formAcrMotivo, setFormAcrMotivo] = useState<string>("");
  const [formAcrFecha, setFormAcrFecha] = useState<string>("");

  // Formulario Pago David
  const [formPagMonto, setFormPagMonto] = useState<string>("");
  const [formPagRef, setFormPagRef] = useState<string>("");
  const [formPagMotivo, setFormPagMotivo] = useState<string>("");
  const [formPagFecha, setFormPagFecha] = useState<string>("");

  // Formulario Reconocimiento Deuda Histórica
  const [formHisMonto, setFormHisMonto] = useState<string>("");
  const [formHisFecha, setFormHisFecha] = useState<string>("");
  const [formHisMotivo, setFormHisMotivo] = useState<string>("");
  const [formHisRef, setFormHisRef] = useState<string>("");
  const [formHisConfirmacion, setFormHisConfirmacion] = useState<boolean>(false);

  const [errorCCDavid, setErrorCCDavid] = useState<string | null>(null);
  const [submittingCCDavid, setSubmittingCCDavid] = useState<boolean>(false);

  // =====================================================================
  // ESTADO: ANTICIPOS Y REPOSICIONES DE PREVENTISTAS (1130 -> 1120 / 1110)
  // =====================================================================
  const [anticiposPreventistas, setAnticiposPreventistas] = useState<AnticipoPreventistaItem[]>([]);
  const [resumenPreventistas, setResumenPreventistas] = useState<ResumenPreventistasResponse | null>(null);
  const [cargandoAnticipos, setCargandoAnticipos] = useState<boolean>(false);
  const [filtroEstadoAnticipo, setFiltroEstadoAnticipo] = useState<string>("ALL");
  const [filtroProveedorAnticipo, setFiltroProveedorAnticipo] = useState<string>("");
  const [modalAnticipo, setModalAnticipo] = useState<"NUEVO" | "REPONER" | "HISTORIAL" | null>(null);
  const [anticipoSeleccionado, setAnticipoSeleccionado] = useState<AnticipoPreventistaItem | null>(null);
  const [reposicionesSeleccionadas, setReposicionesSeleccionadas] = useState<ReposicionPreventistaItem[]>([]);
  const [cargandoReposiciones, setCargandoReposiciones] = useState<boolean>(false);
  const [submittingAnticipo, setSubmittingAnticipo] = useState<boolean>(false);
  const [errorAnticipo, setErrorAnticipo] = useState<string | null>(null);

  // Formulario Nuevo Anticipo
  const [formAntProveedor, setFormAntProveedor] = useState<string>("");
  const [formAntMonto, setFormAntMonto] = useState<string>("");
  const [formAntMotivo, setFormAntMotivo] = useState<string>("");
  const [formAntPedidoId, setFormAntPedidoId] = useState<string>("");
  const [formAntRef, setFormAntRef] = useState<string>("");

  // Formulario Reponer Anticipo
  const [formRepMonto, setFormRepMonto] = useState<string>("");
  const [formRepMotivo, setFormRepMotivo] = useState<string>("");
  const [formRepRef, setFormRepRef] = useState<string>("");

  // Fondos clave para preventistas
  const fondo1130 = fondos.find((f) => f.codigo === "1130");
  const fondo1120 = fondos.find((f) => f.codigo === "1120");
  const fondo1110 = fondos.find((f) => f.codigo === "1110");
  const saldo1130Cents = fondo1130?.saldo_centavos ?? 0;
  const saldo1120Cents = fondo1120?.saldo_centavos ?? 0;
  const saldo1110Cents = fondo1110?.saldo_centavos ?? 0;

  const cargarDatos = useCallback(async (isMounted?: () => boolean) => {
    setCargando(true);
    setError(null);
    try {
      const [resumenData, fondosData, jornadaData, summaryData] = await Promise.all([
        TesoreriaService.obtenerResumen().catch((e) => {
          if (!isMounted || isMounted()) setError(e.message);
          return null;
        }),
        TesoreriaService.obtenerFondos().catch(() => []),
        TesoreriaService.obtenerJornadaActiva().catch(() => null),
        TesoreriaService.obtenerResumenDisponibilidad().catch(() => null),
      ]);
      if (isMounted && !isMounted()) return;

      if (resumenData) setResumen(resumenData);
      setFondos(fondosData);
      setJornadaActiva(jornadaData);
      if (summaryData) setSummaryAvail(summaryData);

      // Cargar listas operativas de forma segura
      const [movs, trfs, rsvs, arqs, exps, revs, obx, aud, cierresHist] = await Promise.all([
        TesoreriaService.obtenerMovimientos().catch(() => []),
        TesoreriaService.obtenerTraslados().catch(() => []),
        TesoreriaService.obtenerReservas().catch(() => []),
        TesoreriaService.obtenerHistorialArqueos().catch(() => []),
        TesoreriaService.obtenerExpedientesDiferencias().catch(() => []),
        TesoreriaService.obtenerReversiones().catch(() => []),
        TesoreriaService.obtenerEstadoOutbox().catch(() => null),
        TesoreriaService.obtenerCadenaAuditoria(undefined, undefined, undefined, 50).catch(() => []),
        TesoreriaService.obtenerHistorialCierres().catch(() => []),
      ]);
      if (isMounted && !isMounted()) return;

      setMovimientos(movs);
      setTraslados(trfs);
      setReservas(rsvs);
      setArqueos(arqs);
      setExpedientes(exps);
      setReversiones(revs);
      if (obx) setOutboxStatus(obx);
      setAuditoriaEventos(aud);
      setHistorialCierres(cierresHist);
    } catch (err) {
      if (!isMounted || isMounted()) {
        setError(err instanceof Error ? err.message : "Error al conectar con el servidor de laboratorio.");
      }
    } finally {
      if (!isMounted || isMounted()) {
        setCargando(false);
      }
    }
  }, []);

  // ETAPA 5 — Carga del Commercial Bridge Monitor
  const cargarMonitorComercial = useCallback(async () => {
    setCargandoBridge(true);
    try {
      const [statusRes, eventsRes] = await Promise.all([
        TesoreriaService.getCommercialBridgeStatus(),
        TesoreriaService.getCommercialBridgeEvents({
          status: commStatusFilter !== "ALL" ? commStatusFilter : undefined,
          event_type: commTypeFilter || undefined,
          search: commSearch || undefined,
        }),
      ]);
      setCommBridgeStatus(statusRes);
      setCommEvents(eventsRes.events);
      setCommTotalEvents(eventsRes.total);
    } catch {
      // Ignorar si cajero o offline
    } finally {
      setCargandoBridge(false);
    }
  }, [commStatusFilter, commTypeFilter, commSearch]);

  useEffect(() => {
    let mounted = true;
    cargarDatos(() => mounted);
    return () => {
      mounted = false;
    };
  }, [cargarDatos]);

  const cargarMovimientosFiltrados = useCallback(async () => {
    try {
      const movs = await TesoreriaService.obtenerMovimientos(
        movFondoFiltro || undefined,
        undefined,
        50,
        movTipoFiltro || undefined,
        movSearch || undefined
      );
      setMovimientos(movs);
    } catch {
      // Ignorar error de red transitorio
    }
  }, [movFondoFiltro, movTipoFiltro, movSearch]);

  useEffect(() => {
    if (tabActiva === "movimientos") {
      cargarMovimientosFiltrados();
    }
  }, [tabActiva, cargarMovimientosFiltrados]);

  const cargarTrasladosFiltrados = useCallback(async () => {
    try {
      const data = await TesoreriaService.obtenerTraslados(
        50,
        trfFiltroEstado !== "ALL" ? trfFiltroEstado : undefined,
        trfFiltroOrigen || undefined,
        trfFiltroDestino || undefined,
        trfBusqueda || undefined
      );
      setTraslados(data);
    } catch {
      // Ignorar error transitorio
    }
  }, [trfFiltroEstado, trfFiltroOrigen, trfFiltroDestino, trfBusqueda]);

  useEffect(() => {
    if (tabActiva === "traslados") {
      cargarTrasladosFiltrados();
    }
  }, [tabActiva, cargarTrasladosFiltrados]);

  useEffect(() => {
    if (tabActiva === "outbox") {
      cargarMonitorComercial();
    }
  }, [tabActiva, cargarMonitorComercial]);

  const handleVerDetalleFondo = async (fondo: FondoTesoreria) => {
    setFondoSeleccionado(fondo);
    setCargandoAuditoria(true);
    try {
      const registros = await TesoreriaService.obtenerAuditoriaFondo(fondo.codigo);
      setAuditoriaFondo(registros);
    } catch (err) {
      console.error(err);
    } finally {
      setCargandoAuditoria(false);
    }
  };

  const handleCambiarEstadoFondo = async (nuevoEstado: EstadoFondo) => {
    if (!fondoSeleccionado) return;
    try {
      const actualizado = await TesoreriaService.actualizarEstadoFondo(
        fondoSeleccionado.codigo,
        nuevoEstado,
        motivoCambio || "Ajuste manual desde panel de control"
      );
      setFondoSeleccionado(actualizado);
      setMotivoCambio("");
      await cargarDatos();
      const registros = await TesoreriaService.obtenerAuditoriaFondo(actualizado.codigo);
      setAuditoriaFondo(registros);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Error al cambiar estado");
    }
  };

  // Apertura y Cierre de Jornada
  const handleAbrirJornadaSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await TesoreriaService.abrirJornada(currentUser?.username, obsApertura);
      setModalAbrirJornada(false);
      setObsApertura("");
      await cargarDatos();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Error al abrir jornada");
    }
  };

  const handleCerrarJornadaSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!jornadaActiva) return;
    try {
      const arqueoCentavos: Record<string, number> = {};
      for (const [cod, valStr] of Object.entries(arqueoValores)) {
        if (valStr.trim()) {
          arqueoCentavos[cod] = parseMoney(valStr);
        }
      }
      await TesoreriaService.cerrarJornada(
        jornadaActiva.id,
        arqueoCentavos,
        obsCierre
      );
      setModalCerrarJornada(false);
      setArqueoValores({});
      setObsCierre("");
      await cargarDatos();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Error al cerrar jornada");
    }
  };

  // Movimientos (LAB-005)
  const handleMovimientoSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!modalMovimiento) return;
    try {
      const centavos = parseMoney(movMontoStr);
      const idempotencyKey = `mov_${modalMovimiento.toLowerCase()}_${Date.now()}_${Math.random().toString(36).substring(2, 7)}`;
      if (modalMovimiento === "ENTRY") {
        await TesoreriaService.registrarEntrada(
          movFondo,
          centavos,
          movContrapartida,
          movMotivo,
          idempotencyKey,
          movEvidencia
        );
      } else {
        await TesoreriaService.registrarSalida(
          movFondo,
          centavos,
          movContrapartida,
          movMotivo,
          idempotencyKey,
          movEvidencia
        );
      }
      setModalMovimiento(null);
      setMovMontoStr("");
      setMovContrapartida("");
      setMovMotivo("");
      setMovEvidencia("");
      await cargarDatos();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Error al registrar movimiento");
    }
  };

  // Traslados (LAB-006)
  const handleTrasladoSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (trfDespachando) return;

    const warning = getTransferPolicyWarning(trfOrigen, trfDestino);
    if (warning) {
      alert(`Operación no permitida: ${warning}`);
      return;
    }

    const centavos = parseMoney(trfMontoStr);
    if (centavos <= 0) {
      alert("El importe del traslado debe ser estrictamente positivo (> $0,00).");
      return;
    }

    if (!trfMotivo || !trfMotivo.trim()) {
      alert("El motivo del traslado es obligatorio.");
      return;
    }

    const fondoOrig = fondos.find((f) => f.codigo === trfOrigen);
    if (fondoOrig && fondoOrig.permite_sobregiro === 0 && fondoOrig.saldo_centavos < centavos) {
      alert(
        `Saldo disponible insuficiente en origen (${fondoOrig.codigo} - ${fondoOrig.nombre}). Saldo actual: ${fondoOrig.saldo_formateado}.`
      );
      return;
    }

    setTrfDespachando(true);
    try {
      const idempotencyKey = `trf_init_${Date.now()}_${Math.random().toString(36).substring(2, 7)}`;
      await TesoreriaService.iniciarTraslado(
        trfOrigen,
        trfDestino,
        centavos,
        trfMotivo.trim(),
        idempotencyKey,
        trfEvidencia.trim() || undefined
      );
      setModalTraslado(false);
      setTrfMontoStr("");
      setTrfMotivo("");
      setTrfEvidencia("");
      await cargarDatos();
      await cargarTrasladosFiltrados();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Error al despachar traslado");
    } finally {
      setTrfDespachando(false);
    }
  };

  const handleConfirmarTraslado = async (target: string | TrasladoTesoreria, obs?: string) => {
    const transferId = typeof target === "string" ? target : target.transfer_id;
    if (confirmandoTrf) return;
    setConfirmandoTrf(true);
    try {
      const idempotencyKey = `trf_conf_${transferId}_${Date.now()}`;
      await TesoreriaService.confirmarTraslado(transferId, idempotencyKey, obs || obsConfirmacionTrf || undefined);
      setModalConfirmacionTrf(null);
      setObsConfirmacionTrf("");
      await cargarDatos();
      await cargarTrasladosFiltrados();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Error al confirmar traslado");
    } finally {
      setConfirmandoTrf(false);
    }
  };

  const handleCancelarTraslado = async (target?: string | TrasladoTesoreria, motivoParam?: string) => {
    let transferId = "";
    if (typeof target === "string") {
      transferId = target;
    } else if (target && typeof target === "object") {
      transferId = target.transfer_id;
    } else if (modalAnulacionTrf) {
      transferId = modalAnulacionTrf.transfer_id;
    }

    let motivo = motivoParam || motivoAnulacionTrf;
    if (!motivo) {
      const promptMotivo = prompt("Ingrese el motivo obligatorio de anulación del traslado:");
      if (!promptMotivo || !promptMotivo.trim()) return;
      motivo = promptMotivo.trim();
    }

    if (!transferId) return;
    setAnulandoTrf(true);
    try {
      const idempotencyKey = `trf_canc_${transferId}_${Date.now()}`;
      await TesoreriaService.cancelarTraslado(transferId, motivo, idempotencyKey);
      setModalAnulacionTrf(null);
      setMotivoAnulacionTrf("");
      await cargarDatos();
      await cargarTrasladosFiltrados();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Error al anular traslado");
    } finally {
      setAnulandoTrf(false);
    }
  };

  // Reservas (LAB-007)
  const handleReservaSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const centavos = parseMoney(resMontoStr);
      const idempotencyKey = `rsv_create_${Date.now()}_${Math.random().toString(36).substring(2, 7)}`;
      await TesoreriaService.crearReserva(
        resFondo,
        centavos,
        resMotivo,
        resBeneficiario,
        resObligationId || undefined,
        idempotencyKey
      );
      setModalReserva(false);
      setResMontoStr("");
      setResMotivo("");
      setResBeneficiario("");
      setResObligationId("");
      await cargarDatos();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Error al crear reserva");
    }
  };

  const handleAccionReservaSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!reservaSeleccionada || !modalAccionReserva) return;
    try {
      const centavos = parseMoney(accionMontoStr);
      const idempotencyKey = `rsv_${modalAccionReserva.toLowerCase()}_${reservaSeleccionada.reservation_id}_${Date.now()}`;
      if (modalAccionReserva === "APPLY") {
        await TesoreriaService.aplicarReserva(
          reservaSeleccionada.reservation_id,
          centavos,
          accionMotivo,
          idempotencyKey
        );
      } else {
        await TesoreriaService.liberarReserva(
          reservaSeleccionada.reservation_id,
          centavos,
          accionMotivo,
          idempotencyKey
        );
      }
      setModalAccionReserva(null);
      setAccionMontoStr("");
      setAccionMotivo("");
      await cargarDatos();
      // Refrescar modal de detalle si está abierto
      const rActual = await TesoreriaService.obtenerHistorialReserva(reservaSeleccionada.reservation_id);
      setReservaSeleccionada(rActual);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Error al operar sobre la reserva");
    }
  };

  // SPRINT 3 & FASE SIGUIENTE — Handlers de Arqueos y Conciliaciones
  const handleCrearArqueoSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const fondoSel = fondos.find((f) => f.codigo === arqFondo);
      const saldoTeorico = fondoSel ? fondoSel.saldo_centavos : 0;
      const centavos = parseMoney(arqMontoStr);
      if (centavos !== saldoTeorico && (!arqObservaciones || !arqObservaciones.trim())) {
        alert("Es obligatorio ingresar una observación cuando el arqueo presenta diferencias (faltante o sobrante).");
        return;
      }
      await TesoreriaService.crearArqueo(
        arqFondo,
        arqTipo,
        centavos,
        arqObservaciones,
        arqCrearExpediente,
        arqNotasInvestigacion,
        arqTurno,
        false
      );
      setModalArqueo(false);
      setArqMontoStr("");
      setArqObservaciones("");
      setArqCrearExpediente(false);
      setArqNotasInvestigacion("");
      await cargarDatos();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Error al registrar arqueo físico");
    }
  };

  const handleCrearConciliacionDigitalSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const centavos = parseMoney(digMontoStr);
      await TesoreriaService.conciliarCuentaDigital(
        digFondo,
        centavos,
        digEvidencia || "DECLARADO_MANUAL",
        digObservaciones,
        digFechaConsulta ? new Date(digFechaConsulta).toISOString() : new Date().toISOString(),
        digCrearExpediente,
        digNotasInvestigacion
      );
      setDigMontoStr("");
      setDigEvidencia("");
      setDigObservaciones("");
      setDigCrearExpediente(false);
      setDigNotasInvestigacion("");
      await cargarDatos();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Error al registrar conciliación digital");
    }
  };

  const handleResolverExpedienteSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!expedienteSeleccionado) return;
    try {
      await TesoreriaService.resolverExpedienteDiferencia(
        expedienteSeleccionado.case_id,
        resNotas,
        resEvidencia
      );
      setExpedienteSeleccionado(null);
      setResNotas("");
      setResEvidencia("");
      await cargarDatos();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Error al resolver expediente");
    }
  };

  // SPRINT 3 & FASE SIGUIENTE — Handlers de Cierres
  const handleIniciarCierre = async () => {
    if (!jornadaActiva) return;
    try {
      const res = await TesoreriaService.iniciarCierre(jornadaActiva.id);
      setJornadaActiva(res);
      await handleValidarCierre();
      await cargarDatos();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Error al iniciar cierre de jornada");
    }
  };

  const handleValidarCierre = async () => {
    if (!jornadaActiva) return;
    try {
      const val = await TesoreriaService.validarCierre(jornadaActiva.id, allowPendingTransfers, allowNonzero1110);
      setValidacionCierre(val);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Error al validar precondiciones de cierre");
    }
  };

  const handleVaciado1110 = async () => {
    if (!jornadaActiva) return;
    try {
      const res = await TesoreriaService.ejecutarVaciado1110(jornadaActiva.id);
      setVaciadoMsg(`Barrido ejecutado con éxito: ${res.message} (Traslado ID: ${res.transfer_id})`);
      await handleValidarCierre();
      await cargarDatos();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Error al ejecutar barrido de 1110");
    }
  };

  const handleVaciado1160 = async () => {
    if (!jornadaActiva) return;
    try {
      const res = await TesoreriaService.ejecutarVaciado1160(jornadaActiva.id);
      setVaciadoMsg(`Barrido PREPAY ejecutado con éxito: ${res.message} ${res.transfer_id ? `(Traslado ID: ${res.transfer_id})` : ""}`);
      await handleValidarCierre();
      await cargarDatos();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Error al ejecutar barrido de 1160");
    }
  };

  const handleConfirmarCierreSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!jornadaActiva) return;
    try {
      const snap = await TesoreriaService.confirmarCierre(
        jornadaActiva.id,
        cierreObs,
        allowPendingTransfers,
        allowNonzero1110
      );
      setCierreSnapshot(snap);
      setModalConfirmarCierre(false);
      setCierreObs("");
      await cargarDatos();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Error al confirmar cierre definitivo");
    }
  };

  const handleAbrirSiguienteJornada = async () => {
    const obs = prompt("Observaciones para la nueva jornada (opcional):");
    try {
      const nextJornada = await TesoreriaService.abrirSiguienteJornada(obs || undefined);
      setJornadaActiva(nextJornada);
      setValidacionCierre(null);
      setCierreSnapshot(null);
      setVaciadoMsg(null);
      await cargarDatos();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Error al abrir siguiente jornada");
    }
  };

  // SPRINT 3 — Handlers de Reversiones (LAB-011)
  const handleReversionSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!modalReversion) return;
    try {
      const idempotencyKey = `rev_${modalReversion.tipo.toLowerCase()}_${modalReversion.targetId}_${Date.now()}`;
      if (modalReversion.tipo === "MOVEMENT") {
        const parcial = revMontoParcialStr ? parseMoney(revMontoParcialStr) : undefined;
        await TesoreriaService.revertirMovimiento(
          modalReversion.targetId,
          revMotivo,
          revEvidencia,
          parcial,
          idempotencyKey
        );
      } else if (modalReversion.tipo === "TRANSFER") {
        await TesoreriaService.revertirTraslado(
          modalReversion.targetId,
          revMotivo,
          revEvidencia,
          idempotencyKey
        );
      } else if (modalReversion.tipo === "RESERVATION_OPERATION" && modalReversion.subId !== undefined) {
        await TesoreriaService.revertirOperacionReserva(
          modalReversion.targetId,
          modalReversion.subId,
          revMotivo,
          revEvidencia,
          idempotencyKey
        );
      }
      setModalReversion(null);
      setRevMotivo("");
      setRevEvidencia("");
      setRevMontoParcialStr("");
      await cargarDatos();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Error al ejecutar reversión");
    }
  };

  // SPRINT 3 — Handlers de Auditoría & Outbox (LAB-012)
  const handleVerificarAuditoria = async () => {
    setVerificandoAuditoria(true);
    try {
      const res = await TesoreriaService.verificarAuditoria();
      setVerificacionAuditoria(res);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Error al verificar auditoría");
    } finally {
      setVerificandoAuditoria(false);
    }
  };

  const handleProcesarOutbox = async () => {
    try {
      const res = await TesoreriaService.procesarOutbox();
      setOutboxMsg(`Lote procesado: ${res.processed_count} eventos consumidos.`);
      await cargarDatos();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Error al procesar outbox");
    }
  };

  const handleReprocesarDeadLetter = async () => {
    if (!deadLetterId) return;
    try {
      const res = await TesoreriaService.reprocesarOutbox(deadLetterId);
      setOutboxMsg(res.message);
      setDeadLetterId("");
      await cargarDatos();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Error al reprocesar mensaje");
    }
  };

  // ETAPA 5 — Handlers del Commercial Bridge Monitor
  const handleReprocesarEventoComercial = async (eventId: string) => {
    if (!window.confirm(`¿Confirmás el reprocesamiento auditable del evento comercial ${eventId}?`)) return;
    try {
      const res = await TesoreriaService.reprocessCommercialEvent(eventId);
      setCommBridgeMsg(`Evento ${res.event_id} reprocesado con éxito. Estado resultante: ${res.status}.`);
      await cargarMonitorComercial();
      await cargarDatos();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Error al reprocesar evento comercial");
    }
  };

  const handleForzarDrenajeComercial = async () => {
    try {
      const res = await TesoreriaService.triggerCommercialBridgeDrain();
      setCommBridgeMsg(res.message);
      await cargarMonitorComercial();
      await cargarDatos();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Error al forzar drenaje comercial");
    }
  };

  // SPRINT 3 — FASE 0 Egreso Reservado
  const handleEgresoReservadoSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!modalEgresoReservado) return;
    try {
      const centavos = parseMoney(egresoMontoStr);
      const idempotencyKey = `egr_res_${modalEgresoReservado.reservation_id}_${Date.now()}`;
      await TesoreriaService.ejecutarEgresoReservado(
        modalEgresoReservado.reservation_id,
        centavos,
        egresoContrapartida,
        egresoMotivo,
        idempotencyKey,
        egresoEvidencia || undefined
      );
      setModalEgresoReservado(null);
      setEgresoMontoStr("");
      setEgresoContrapartida("");
      setEgresoMotivo("");
      setEgresoEvidencia("");
      await cargarDatos();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Error al ejecutar egreso reservado");
    }
  };

  // Handlers Cuenta Corriente PREPAY / David
  const cargarCCDavid = useCallback(async (fechaCorte?: string) => {
    setCargandoCCDavid(true);
    setErrorCCDavid(null);
    try {
      const data = await TesoreriaService.getCuentaCorrienteDavid(fechaCorte);
      setEstadoCCDavid(data);
    } catch (err: any) {
      console.warn("Fallo al consultar Cuenta Corriente David:", err);
      setEstadoCCDavid({
        obligacion_pendiente_cents: 0,
        obligacion_pendiente_formatted: "$0,00",
        total_acreditaciones_cents: 0,
        total_acreditaciones_formatted: "$0,00",
        total_pagos_cents: 0,
        total_pagos_formatted: "$0,00",
        total_deuda_historica_cents: 0,
        total_deuda_historica_formatted: "$0,00",
        saldo_disponible_1130_cents: 0,
        saldo_disponible_1130_formatted: "$0,00",
        cantidad_movimientos: 0,
        movimientos: [],
      });
    } finally {
      setCargandoCCDavid(false);
    }
  }, []);

  useEffect(() => {
    if (tabActiva === "cuenta_corriente_david") {
      cargarCCDavid(filtroFechaCorteDavid);
    }
  }, [tabActiva, filtroFechaCorteDavid, cargarCCDavid]);

  const handleAcreditacionDavidSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const parsed = parseMoney(formAcrMonto);
    if (parsed <= 0) {
      alert("El monto de la acreditación debe ser mayor a 0.");
      return;
    }
    if (!formAcrRef.trim()) {
      alert("La referencia o comprobante de acreditación es obligatoria.");
      return;
    }
    setSubmittingCCDavid(true);
    setErrorCCDavid(null);
    try {
      await TesoreriaService.registrarAcreditacionDavid({
        monto_centavos: parsed,
        referencia: formAcrRef.trim(),
        motivo: formAcrMotivo.trim() || undefined,
        fecha_referencia: formAcrFecha || undefined,
      });
      setModalCCDavid(null);
      setFormAcrMonto("");
      setFormAcrRef("");
      setFormAcrMotivo("");
      setFormAcrFecha("");
      await Promise.all([cargarCCDavid(filtroFechaCorteDavid), cargarDatos()]);
    } catch (err: any) {
      setErrorCCDavid(err.message || "Error al registrar la acreditación.");
      alert(err.message || "Error al registrar la acreditación.");
    } finally {
      setSubmittingCCDavid(false);
    }
  };

  const handlePagoDavidSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const parsed = parseMoney(formPagMonto);
    if (parsed <= 0) {
      alert("El monto del pago debe ser mayor a 0.");
      return;
    }
    if (!formPagRef.trim()) {
      alert("El comprobante o recibo de pago es obligatorio.");
      return;
    }
    setSubmittingCCDavid(true);
    setErrorCCDavid(null);
    try {
      await TesoreriaService.registrarPagoDavid({
        monto_centavos: parsed,
        referencia: formPagRef.trim(),
        motivo: formPagMotivo.trim() || undefined,
        fecha_referencia: formPagFecha || undefined,
      });
      setModalCCDavid(null);
      setFormPagMonto("");
      setFormPagRef("");
      setFormPagMotivo("");
      setFormPagFecha("");
      await Promise.all([cargarCCDavid(filtroFechaCorteDavid), cargarDatos()]);
    } catch (err: any) {
      setErrorCCDavid(err.message || "Error al registrar el pago a David.");
      alert(err.message || "Error al registrar el pago a David.");
    } finally {
      setSubmittingCCDavid(false);
    }
  };

  const handleHistoricoDavidSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const parsed = parseMoney(formHisMonto);
    if (parsed <= 0) {
      alert("El monto de la deuda histórica debe ser mayor a 0.");
      return;
    }
    if (!formHisFecha.trim()) {
      alert("La fecha de origen de la deuda histórica es obligatoria.");
      return;
    }
    if (!formHisMotivo.trim()) {
      alert("El motivo u observación del reconocimiento histórico es obligatorio.");
      return;
    }
    if (!formHisConfirmacion) {
      alert("Debe confirmar explícitamente el reconocimiento de deuda histórica.");
      return;
    }
    setSubmittingCCDavid(true);
    setErrorCCDavid(null);
    try {
      await TesoreriaService.registrarReconocimientoDeudaHistorica({
        monto_centavos: parsed,
        fecha_referencia: formHisFecha.trim(),
        motivo: formHisMotivo.trim(),
        referencia_externa: formHisRef.trim() || undefined,
        confirmacion_explicita: true,
      });
      setModalCCDavid(null);
      setFormHisMonto("");
      setFormHisFecha("");
      setFormHisMotivo("");
      setFormHisRef("");
      setFormHisConfirmacion(false);
      await Promise.all([cargarCCDavid(filtroFechaCorteDavid), cargarDatos()]);
    } catch (err: any) {
      setErrorCCDavid(err.message || "Error al registrar el reconocimiento histórico.");
      alert(err.message || "Error al registrar el reconocimiento histórico.");
    } finally {
      setSubmittingCCDavid(false);
    }
  };

  // =====================================================================
  // ANTICIPOS PREVENTISTAS — HANDLERS Y EFECTOS
  // =====================================================================
  const cargarAnticipos = useCallback(async (estado?: string, prov?: string) => {
    setCargandoAnticipos(true);
    setErrorAnticipo(null);
    try {
      const [list, res] = await Promise.all([
        TesoreriaService.getAnticiposPreventistas(estado, prov),
        TesoreriaService.getResumenPreventistas(),
      ]);
      setAnticiposPreventistas(list);
      setResumenPreventistas(res);
    } catch (err: any) {
      setErrorAnticipo(err.message || "Error al cargar anticipos de preventistas.");
    } finally {
      setCargandoAnticipos(false);
    }
  }, []);

  useEffect(() => {
    if (tabActiva === "anticipos_preventistas") {
      cargarAnticipos(filtroEstadoAnticipo, filtroProveedorAnticipo);
    }
  }, [tabActiva, filtroEstadoAnticipo, filtroProveedorAnticipo, cargarAnticipos]);

  const handleCrearAnticipoSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const parsed = parseMoney(formAntMonto);
    if (parsed <= 0) {
      alert("El importe del anticipo debe ser mayor a 0.");
      return;
    }
    if (!formAntProveedor.trim()) {
      alert("El nombre o identificación del preventista/proveedor es obligatorio.");
      return;
    }
    if (!formAntMotivo.trim()) {
      alert("El motivo de entrega del anticipo es obligatorio.");
      return;
    }
    const saldo1130 = fondos.find((f) => f.codigo === "1130")?.saldo_centavos ?? 0;
    if (parsed > saldo1130) {
      alert(`Saldo insuficiente en Fondo 1130 (Prepay Efectivo). Disponible: ${formatMoney(saldo1130)}, Solicitado: ${formatMoney(parsed)}.`);
      return;
    }
    setSubmittingAnticipo(true);
    setErrorAnticipo(null);
    try {
      await TesoreriaService.crearAnticipoPreventista({
        proveedor: formAntProveedor.trim(),
        monto_centavos: parsed,
        motivo: formAntMotivo.trim(),
        pedido_id: formAntPedidoId.trim() || undefined,
        referencia_documento: formAntRef.trim() || undefined,
      });
      setModalAnticipo(null);
      setFormAntProveedor("");
      setFormAntMonto("");
      setFormAntMotivo("");
      setFormAntPedidoId("");
      setFormAntRef("");
      await Promise.all([cargarAnticipos(filtroEstadoAnticipo, filtroProveedorAnticipo), cargarDatos()]);
    } catch (err: any) {
      setErrorAnticipo(err.message || "Error al emitir el anticipo.");
      alert(err.message || "Error al emitir el anticipo.");
    } finally {
      setSubmittingAnticipo(false);
    }
  };

  const handleReponerAnticipoSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!anticipoSeleccionado) return;
    const parsed = parseMoney(formRepMonto);
    if (parsed <= 0) {
      alert("El importe a reponer debe ser mayor a 0.");
      return;
    }
    if (parsed > anticipoSeleccionado.monto_pendiente_cents) {
      alert(`El importe no puede superar el saldo pendiente del anticipo (${anticipoSeleccionado.monto_pendiente_formatted}).`);
      return;
    }
    const saldo1120 = fondos.find((f) => f.codigo === "1120")?.saldo_centavos ?? 0;
    const saldo1110 = fondos.find((f) => f.codigo === "1110")?.saldo_centavos ?? 0;
    const totalDisp = saldo1120 + saldo1110;
    if (parsed > totalDisp) {
      alert(`Fondos insuficientes para reponer. Disponible conjunto (1120 + 1110): ${formatMoney(totalDisp)}, requerido: ${formatMoney(parsed)}.`);
      return;
    }
    setSubmittingAnticipo(true);
    setErrorAnticipo(null);
    try {
      await TesoreriaService.reponerAnticipoPreventista(anticipoSeleccionado.id, {
        monto_centavos: parsed,
        motivo: formRepMotivo.trim() || undefined,
      });
      setModalAnticipo(null);
      setAnticipoSeleccionado(null);
      setFormRepMonto("");
      setFormRepMotivo("");
      setFormRepRef("");
      await Promise.all([cargarAnticipos(filtroEstadoAnticipo, filtroProveedorAnticipo), cargarDatos()]);
    } catch (err: any) {
      setErrorAnticipo(err.message || "Error al registrar la reposición.");
      alert(err.message || "Error al registrar la reposición.");
    } finally {
      setSubmittingAnticipo(false);
    }
  };

  const handleVerHistorialAnticipo = async (ant: AnticipoPreventistaItem) => {
    setAnticipoSeleccionado(ant);
    setModalAnticipo("HISTORIAL");
    setCargandoReposiciones(true);
    try {
      const reps = await TesoreriaService.getReposicionesDeAnticipo(ant.id);
      setReposicionesSeleccionadas(reps);
    } catch (err: any) {
      alert(err.message || "Error al cargar el historial de reposiciones.");
      setReposicionesSeleccionadas([]);
    } finally {
      setCargandoReposiciones(false);
    }
  };

  // --- Estados de Saldos Iniciales / Aperturas ---
  const [saldosApertura, setSaldosApertura] = useState<SaldosInicialesResponse | null>(null);
  const [historialAperturas, setHistorialAperturas] = useState<AuditoriaSaldoInicialItem[]>([]);
  const [cargandoAperturas, setCargandoAperturas] = useState(false);
  const [errorAperturas, setErrorAperturas] = useState<string | null>(null);
  const [modalEditarApertura, setModalEditarApertura] = useState<SaldoInicialItem | null>(null);
  const [nuevoSaldoAperturaInput, setNuevoSaldoAperturaInput] = useState("");
  const [motivoAperturaInput, setMotivoAperturaInput] = useState("");
  const [referenciaAperturaInput, setReferenciaAperturaInput] = useState("");
  const [guardandoApertura, setGuardandoApertura] = useState(false);
  const [confirmandoApertura, setConfirmandoApertura] = useState(false);

  const cargarSaldosApertura = useCallback(async () => {
    setCargandoAperturas(true);
    setErrorAperturas(null);
    try {
      const [saldos, hist] = await Promise.all([
        TesoreriaService.getSaldosIniciales(),
        TesoreriaService.getHistorialAperturas(),
      ]);
      setSaldosApertura(saldos);
      setHistorialAperturas(hist);
    } catch (err: any) {
      setErrorAperturas(err.message || "Error al cargar saldos iniciales de apertura.");
    } finally {
      setCargandoAperturas(false);
    }
  }, []);

  useEffect(() => {
    if (tabActiva === "aperturas") {
      cargarSaldosApertura();
    }
  }, [tabActiva, cargarSaldosApertura]);

  const handleGuardarSaldoInicial = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!modalEditarApertura) return;
    if (!motivoAperturaInput.trim()) {
      alert("El motivo de la modificación es obligatorio para la pista de auditoría append-only.");
      return;
    }
    const nuevoCents = parseMoney(nuevoSaldoAperturaInput);
    if (nuevoCents < 0) {
      alert("El nuevo saldo inicial debe ser mayor o igual a 0.");
      return;
    }
    if (!confirmandoApertura) {
      setConfirmandoApertura(true);
      return;
    }

    setGuardandoApertura(true);
    try {
      await TesoreriaService.modificarSaldoInicial({
        codigo: modalEditarApertura.codigo,
        saldo_nuevo_cents: nuevoCents,
        motivo: motivoAperturaInput.trim(),
        referencia_autorizacion: referenciaAperturaInput.trim() || undefined,
      });
      setModalEditarApertura(null);
      setNuevoSaldoAperturaInput("");
      setMotivoAperturaInput("");
      setReferenciaAperturaInput("");
      setConfirmandoApertura(false);
      await Promise.all([cargarSaldosApertura(), cargarDatos()]);
      alert("Saldo inicial configurado correctamente con trazabilidad y auditoría.");
    } catch (err: any) {
      alert(`Error al configurar saldo inicial: ${err.message}`);
    } finally {
      setGuardandoApertura(false);
    }
  };

  return (
    <div className="space-y-6 p-4 md:p-6 max-w-7xl mx-auto" data-testid="tesoreria-lab-container">
      {/* Encabezado y Badges de Aislamiento */}
      <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-3 sm:gap-4 pb-4 border-b border-gray-200 dark:border-gray-800">
        <div className="w-full md:w-auto">
          <div className="flex items-center flex-wrap gap-2 sm:gap-3">
            <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-gray-900 dark:text-gray-100">
              GENARO TESORERÍA
            </h1>
            <span className="px-2.5 py-0.5 text-xs font-semibold rounded-full bg-amber-100 text-amber-800 dark:bg-amber-950/60 dark:text-amber-200 border border-amber-300 dark:border-amber-800">
              Centro de Control Contable
              <span className="sr-only"> (LABORATORIO — SPRINT 3)</span>
            </span>
          </div>
          <p className="text-xs sm:text-sm text-gray-500 dark:text-gray-400 mt-1 break-words">
            Arqueos, Cierres Definitivos, Reversiones Inmutables, Hash Chain y Outbox Transaccional.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-1.5 sm:gap-2">
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>
            Trazabilidad Contable Activa
            <span className="sr-only"> Aislamiento Estricto Activo</span>
          </span>
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300 border border-gray-200 dark:border-gray-700">
            Turso Cloud Unificado
            <span className="sr-only"> SQLite Local Exclusivo</span>
          </span>
          {cargando && (
            <span className="text-xs text-gray-400 animate-pulse font-medium">Sincronizando...</span>
          )}
        </div>
      </div>

      {error && (
        <div className="p-4 rounded-lg bg-red-50 dark:bg-red-950/50 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-300 text-sm flex justify-between items-center">
          <div>
            <strong>Error del Servidor:</strong> {error}
          </div>
          <button
            onClick={() => cargarDatos()}
            className="px-3 py-1 bg-red-600 hover:bg-red-700 text-white rounded text-xs font-semibold"
          >
            Reintentar
          </button>
        </div>
      )}

      {/* Navegación por Pestañas (Fila unificada con desplazamiento táctil y acento Terracota) */}
      <div className="flex items-center gap-1.5 overflow-x-auto pb-2 scrollbar-thin border-b border-gray-200 dark:border-gray-800" style={{ WebkitOverflowScrolling: "touch" }}>
        <button
          type="button"
          onClick={() => setTabActiva("disponibilidad")}
          data-testid="tab-disponibilidad"
          className={`px-3 py-1.5 text-xs font-bold rounded-lg transition whitespace-nowrap flex-shrink-0 ${
            tabActiva === "disponibilidad"
              ? "bg-[#C97B5A] text-white shadow-sm"
              : "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-200"
          }`}
        >
          📊 Disponibilidad & Fondos
        </button>
        <button
          type="button"
          onClick={() => setTabActiva("movimientos")}
          data-testid="tab-movimientos"
          className={`px-3 py-1.5 text-xs font-bold rounded-lg transition whitespace-nowrap flex-shrink-0 ${
            tabActiva === "movimientos"
              ? "bg-[#C97B5A] text-white shadow-sm"
              : "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-200"
          }`}
        >
          💳 Entradas y Salidas ({movimientos.length})
        </button>
        <button
          type="button"
          onClick={() => setTabActiva("traslados")}
          data-testid="tab-traslados"
          className={`px-3 py-1.5 text-xs font-bold rounded-lg transition whitespace-nowrap flex-shrink-0 ${
            tabActiva === "traslados"
              ? "bg-[#C97B5A] text-white shadow-sm"
              : "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-200"
          }`}
        >
          🔄 Traslados ({traslados.length})
        </button>
        <button
          type="button"
          onClick={() => setTabActiva("reservas")}
          data-testid="tab-reservas"
          className={`px-3 py-1.5 text-xs font-bold rounded-lg transition whitespace-nowrap flex-shrink-0 ${
            tabActiva === "reservas"
              ? "bg-[#C97B5A] text-white shadow-sm"
              : "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-200"
          }`}
        >
          🔒 Reservas ({reservas.length})
        </button>
        <button
          type="button"
          onClick={() => setTabActiva("arqueos")}
          data-testid="tab-arqueos"
          className={`px-3 py-1.5 text-xs font-bold rounded-lg transition whitespace-nowrap flex-shrink-0 ${
            tabActiva === "arqueos"
              ? "bg-[#C97B5A] text-white shadow-sm"
              : "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-200"
          }`}
        >
          ⚖️ Arqueos ({arqueos.length})
        </button>
        <button
          type="button"
          onClick={() => setTabActiva("cierres")}
          data-testid="tab-cierres"
          className={`px-3 py-1.5 text-xs font-bold rounded-lg transition whitespace-nowrap flex-shrink-0 ${
            tabActiva === "cierres"
              ? "bg-[#C97B5A] text-white shadow-sm"
              : "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-200"
          }`}
        >
          🚪 Cierre Diario
        </button>
        <button
          type="button"
          onClick={() => setTabActiva("reversiones")}
          data-testid="tab-reversiones"
          className={`px-3 py-1.5 text-xs font-bold rounded-lg transition whitespace-nowrap flex-shrink-0 ${
            tabActiva === "reversiones"
              ? "bg-[#C97B5A] text-white shadow-sm"
              : "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-200"
          }`}
        >
          ↩️ Reversiones ({reversiones.length})
        </button>
        <button
          type="button"
          onClick={() => setTabActiva("auditoria")}
          data-testid="tab-auditoria"
          className={`px-3 py-1.5 text-xs font-bold rounded-lg transition whitespace-nowrap flex-shrink-0 ${
            tabActiva === "auditoria"
              ? "bg-[#C97B5A] text-white shadow-sm"
              : "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-200"
          }`}
        >
          ⛓️ Auditoría Hash Chain
        </button>
        <button
          type="button"
          onClick={() => setTabActiva("outbox")}
          data-testid="tab-outbox"
          className={`px-3 py-1.5 text-xs font-bold rounded-lg transition whitespace-nowrap flex-shrink-0 ${
            tabActiva === "outbox"
              ? "bg-[#C97B5A] text-white shadow-sm"
              : "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-200"
          }`}
        >
          📬 Outbox {outboxStatus ? `(${outboxStatus.pending} pend)` : ""}
        </button>
        <button
          type="button"
          onClick={() => setTabActiva("cuenta_corriente_david")}
          data-testid="tab-cuenta-corriente-david"
          className={`px-3 py-1.5 text-xs font-bold rounded-lg transition whitespace-nowrap flex-shrink-0 ${
            tabActiva === "cuenta_corriente_david"
              ? "bg-[#C97B5A] text-white shadow-sm"
              : "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-200"
          }`}
        >
          🤝 Cuenta Corriente PREPAY / David
        </button>
        <button
          type="button"
          onClick={() => setTabActiva("anticipos_preventistas")}
          data-testid="tab-anticipos-preventistas"
          className={`px-3 py-1.5 text-xs font-bold rounded-lg transition whitespace-nowrap flex-shrink-0 ${
            tabActiva === "anticipos_preventistas"
              ? "bg-[#C97B5A] text-white shadow-sm"
              : "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-200"
          }`}
        >
          🚚 Anticipos Preventistas (1130)
        </button>
        <button
          type="button"
          onClick={() => setTabActiva("aperturas")}
          data-testid="tab-aperturas"
          className={`px-3 py-1.5 text-xs font-bold rounded-lg transition whitespace-nowrap flex-shrink-0 ${
            tabActiva === "aperturas"
              ? "bg-[#C97B5A] text-white shadow-sm"
              : "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-200"
          }`}
        >
          ⚙️ Saldos Iniciales / Apertura
        </button>
      </div>

      {/* TAB 1: DISPONIBILIDAD Y CATÁLOGO DE FONDOS */}
      {tabActiva === "disponibilidad" && (
        <div className="space-y-6">
          {/* Indicadores de Disponibilidad Global */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <div className="bg-white dark:bg-gray-900 p-5 rounded-xl border border-gray-200 dark:border-gray-800 shadow-sm">
              <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">
                Total Registrado en Libros
              </p>
              <p className="text-2xl font-bold text-gray-900 dark:text-gray-100 font-mono mt-2" data-testid="saldo-registrado-total">
                {summaryAvail?.total_consolidado?.total_registrado_formatted || (resumen ? resumen.total_saldo_registrado_formateado : "$0,00")}
              </p>
              <span className="text-xs text-gray-400 mt-1 block">Consolidado general (9 cuentas + tránsito)</span>
            </div>

            <div className="bg-white dark:bg-gray-900 p-5 rounded-xl border border-gray-200 dark:border-gray-800 shadow-sm">
              <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">
                Disponibilidad Líquida Propia
              </p>
              <p className={`text-2xl font-bold font-mono mt-2 ${
                (summaryAvail?.fondos_propios?.total_disponible_cents ?? 0) > 0
                  ? "text-emerald-600 dark:text-emerald-400"
                  : "text-gray-900 dark:text-gray-100"
              }`} data-testid="saldo-disponible-total">
                {summaryAvail?.fondos_propios?.total_disponible_formatted || (resumen ? resumen.total_saldo_disponible_formateado : "$0,00")}
              </p>
              <span className="text-xs text-emerald-700 dark:text-emerald-400 mt-1 block">
                Fondos propios menos reservas
              </span>
            </div>

            <div className="bg-white dark:bg-gray-900 p-5 rounded-xl border border-gray-200 dark:border-gray-800 shadow-sm">
              <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">
                Dinero en Tránsito
              </p>
              <p className={`text-2xl font-bold font-mono mt-2 ${
                (summaryAvail?.dinero_en_transito?.amount_cents ?? 0) > 0
                  ? "text-indigo-600 dark:text-indigo-400"
                  : "text-gray-900 dark:text-gray-100"
              }`} data-testid="dinero-transito-total">
                {summaryAvail?.dinero_en_transito?.amount_formatted || "$0,00"}
              </p>
              <span className="text-xs text-gray-500 mt-1 block">
                {summaryAvail?.dinero_en_transito?.traslados_pendientes_count || 0} traslados pendientes de recepción
              </span>
            </div>

            <div className="bg-white dark:bg-gray-900 p-5 rounded-xl border border-gray-200 dark:border-gray-800 shadow-sm">
              <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">
                Estado de Jornada
              </p>
              <div className="mt-2 flex items-center justify-between">
                <span
                  data-testid="estado-jornada-badge"
                  className={`px-3 py-1 rounded-full text-xs font-bold ${
                    jornadaActiva
                      ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
                      : "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300"
                  }`}
                >
                  {jornadaActiva ? `JORNADA #${jornadaActiva.numero_jornada} ABIERTA` : "SIN JORNADA ACTIVA"}
                </span>
              </div>
              <span className="text-xs text-gray-500 mt-1 block">
                {jornadaActiva
                  ? `Iniciada por ${jornadaActiva.usuario_apertura}`
                  : "Apertura requerida para operar"}
              </span>
            </div>
          </div>

          {/* Control Operativo de Jornada */}
          <div className="bg-gradient-to-r from-blue-50 to-indigo-50 dark:from-gray-900 dark:to-gray-800/80 p-4 rounded-xl border border-blue-200 dark:border-gray-700 flex flex-col sm:flex-row items-center justify-between gap-4">
            <div>
              <h3 className="font-semibold text-gray-900 dark:text-gray-100 text-sm">
                Control Operativo de Jornada
              </h3>
              <p className="text-xs text-gray-600 dark:text-gray-400 mt-0.5">
                {jornadaActiva
                  ? `Jornada activa #${jornadaActiva.numero_jornada} iniciada el ${new Date(jornadaActiva.apertura_timestamp_utc).toLocaleString("es-AR")}.`
                  : "No hay una jornada abierta. Abra una jornada para habilitar movimientos y traslados."}
              </p>
            </div>
            <div>
              {jornadaActiva ? (
                <button
                  onClick={() => {
                    const initVals: Record<string, string> = {};
                    fondos.forEach((f) => { initVals[f.codigo] = "0.00"; });
                    setArqueoValores(initVals);
                    setModalCerrarJornada(true);
                  }}
                  data-testid="btn-cerrar-jornada"
                  className="px-4 py-2 bg-amber-600 hover:bg-amber-700 text-white font-medium text-xs rounded-lg shadow-sm transition"
                >
                  Cerrar Jornada / Realizar Arqueo
                </button>
              ) : (
                <button
                  onClick={() => setModalAbrirJornada(true)}
                  data-testid="btn-abrir-jornada"
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white font-medium text-xs rounded-lg shadow-sm transition"
                >
                  Abrir Nueva Jornada
                </button>
              )}
            </div>
          </div>

          {/* Catálogo de Fondos con desglose de Disponibilidad */}
          <div>
            <div className="flex items-center justify-between mb-4">
              <div>
                <h2 className="text-lg font-bold text-gray-900 dark:text-gray-100">
                  Catálogo y Disponibilidad de Fondos
                </h2>
                <p className="text-xs text-gray-500">
                  Diferenciación estricta entre Saldo Registrado, Saldo Reservado y Saldo Disponible.
                </p>
              </div>
            </div>

            <div className="space-y-6" data-testid="fondos-grid">
              {(() => {
                const listaFondos: FundAvailability[] = (
                  (summaryAvail && summaryAvail.fondos && summaryAvail.fondos.length > 0)
                    ? summaryAvail.fondos
                    : fondos.map((f) => ({
                        fund_id: f.id,
                        fund_code: f.codigo,
                        fund_name: f.nombre,
                        nature: f.naturaleza,
                        ownership: f.titularidad,
                        status: f.estado,
                        saldo_registrado_cents: f.saldo_centavos,
                        saldo_registrado_formatted: f.saldo_formateado,
                        saldo_teorico_cents: f.saldo_centavos,
                        saldo_teorico_formatted: f.saldo_formateado,
                        diferencia_teorica_cents: 0,
                        diferencia_teorica_formatted: "$0,00",
                        saldo_reservado_cents: 0,
                        saldo_reservado_formatted: "$0,00",
                        saldo_disponible_cents: f.saldo_centavos,
                        saldo_disponible_formatted: f.saldo_formateado,
                        cantidad_reservas_activas: 0,
                      }))
                );

                const FONDOS_FISICOS_CODES = ["1110", "1120", "1130", "1140", "1150"];
                const CUENTAS_DIGITALES_CODES = ["1210", "1220", "1230"];
                const TRANSITO_CODES = ["1310"];

                const fondosFisicos = listaFondos.filter((f) => FONDOS_FISICOS_CODES.includes(f.fund_code));
                const cuentasDigitales = listaFondos.filter((f) => CUENTAS_DIGITALES_CODES.includes(f.fund_code));
                const fondosTransito = listaFondos.filter((f) => TRANSITO_CODES.includes(f.fund_code));

                const renderCard = (fondo: typeof listaFondos[0]) => {
                  const isCustodia = fondo.fund_code === "1150";
                  const isTransito = fondo.fund_code === "1310";
                  const isDigital = CUENTAS_DIGITALES_CODES.includes(fondo.fund_code);
                  const isAvailablePos = (fondo.saldo_disponible_cents || 0) > 0;
                  const saldoTeorico = fondo.saldo_teorico_formatted || fondo.saldo_registrado_formatted;
                  const tieneDescuadre = fondo.diferencia_teorica_cents !== undefined && fondo.diferencia_teorica_cents !== 0;

                  return (
                    <div
                      key={fondo.fund_code}
                      className={`rounded-xl p-4 sm:p-5 shadow-sm transition flex flex-col justify-between ${
                        isCustodia
                          ? "bg-purple-50/40 dark:bg-purple-950/20 border-2 border-purple-300 dark:border-purple-800"
                          : isTransito
                          ? "bg-amber-50/30 dark:bg-amber-950/20 border border-amber-300 dark:border-amber-800"
                          : isDigital
                          ? "bg-blue-50/20 dark:bg-blue-950/20 border border-blue-200 dark:border-blue-800 hover:border-blue-400"
                          : "bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 hover:border-indigo-400 dark:hover:border-indigo-600"
                      }`}
                      data-testid={`card-fondo-${fondo.fund_code}`}
                    >
                      <div>
                        <div className="flex items-start justify-between gap-2 mb-2">
                          <span className="px-2 py-0.5 font-mono text-xs font-bold rounded bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300">
                            {fondo.fund_code}
                          </span>
                          <div className="flex items-center gap-1.5">
                            {isCustodia && (
                              <span className="px-2 py-0.5 text-[10px] font-bold rounded-full bg-purple-100 text-purple-800 dark:bg-purple-950 dark:text-purple-300">
                                🛡️ CUSTODIA
                              </span>
                            )}
                            {isDigital && (
                              <span className="px-2 py-0.5 text-[10px] font-bold rounded-full bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300">
                                📱 DIGITAL
                              </span>
                            )}
                            <span
                              className={`px-2 py-0.5 text-xs font-semibold rounded-full ${
                                fondo.status === "ACTIVO"
                                  ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
                                  : "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300"
                              }`}
                            >
                              {fondo.status}
                            </span>
                          </div>
                        </div>

                        <h3 className="font-semibold text-gray-900 dark:text-gray-100 text-sm sm:text-base mb-1">
                          {fondo.fund_name}
                        </h3>

                        <div className="flex flex-wrap gap-1.5 mt-2 mb-4">
                          <span className="px-2 py-0.5 text-[11px] rounded bg-indigo-50 dark:bg-indigo-950/60 text-indigo-700 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-800">
                            {fondo.nature}
                          </span>
                          <span className="px-2 py-0.5 text-[11px] rounded bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 border border-gray-200 dark:border-gray-700">
                            {fondo.ownership}
                          </span>
                        </div>

                        <div className="space-y-1.5 py-2 border-y border-gray-100 dark:border-gray-800 text-xs">
                          <div className="flex justify-between">
                            <span className="text-gray-500">Saldo Registrado:</span>
                            <span className="font-semibold text-gray-900 dark:text-gray-100 font-mono">
                              {fondo.saldo_registrado_formatted}
                            </span>
                          </div>
                          <div className="flex justify-between">
                            <span className="text-gray-500">Saldo Teórico (Mayor):</span>
                            <span className="font-semibold text-gray-700 dark:text-gray-300 font-mono">
                              {saldoTeorico}
                            </span>
                          </div>
                          {tieneDescuadre ? (
                            <div className="text-[10px] text-amber-600 dark:text-amber-400 font-semibold text-right">
                              ⚠️ Descuadre teórico: {fondo.diferencia_teorica_formatted}
                            </div>
                          ) : (
                            <div className="text-[10px] text-emerald-600 dark:text-emerald-400 font-medium text-right">
                              ✓ Cuadre exacto con libro mayor
                            </div>
                          )}
                          <div className="flex justify-between">
                            <span className="text-gray-500">Saldo Reservado:</span>
                            <span className="font-semibold text-gray-400 font-mono">
                              {fondo.saldo_reservado_formatted}
                            </span>
                          </div>
                          <div className="flex justify-between font-bold pt-1 border-t border-dashed border-gray-200 dark:border-gray-800">
                            <span className="text-gray-700 dark:text-gray-300">Disponible Líquido:</span>
                            <span
                              className={`font-mono text-sm ${
                                isAvailablePos
                                  ? "text-emerald-600 dark:text-emerald-400 font-bold"
                                  : "text-gray-400 font-medium"
                              }`}
                            >
                              {fondo.saldo_disponible_formatted}
                            </span>
                          </div>
                        </div>
                      </div>

                      <div className="pt-3 flex items-center justify-end mt-2">
                        <button
                          onClick={() => {
                            const originalFondo = fondos.find((f) => f.codigo === fondo.fund_code);
                            if (originalFondo) handleVerDetalleFondo(originalFondo);
                          }}
                          className="px-3 py-1.5 text-xs font-medium text-indigo-600 dark:text-indigo-400 hover:bg-indigo-50 dark:hover:bg-indigo-950/50 rounded-lg border border-indigo-200 dark:border-indigo-800 transition"
                          data-testid={`btn-detalle-${fondo.fund_code}`}
                        >
                          Auditoría & Detalle
                        </button>
                      </div>
                    </div>
                  );
                };

                return (
                  <>
                    {/* SECCIÓN 1: FONDOS FÍSICOS (EFECTIVO EN CAJA Y CUSTODIA) — 5 FONDOS */}
                    <div>
                      <div className="flex flex-wrap items-center gap-2 mb-3">
                        <span className="text-base">💵</span>
                        <h3 className="text-xs font-bold uppercase tracking-wider text-gray-800 dark:text-gray-200">
                          Fondos Físicos — Efectivo en Caja y Custodia (5 Cuentas)
                        </h3>
                        <span className="text-[11px] bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300 font-semibold px-2 py-0.5 rounded-full">
                          {fondosFisicos.length} Físicos (1110, 1120, 1130, 1140, 1150)
                        </span>
                      </div>
                      <p className="text-xs text-gray-500 mb-3">
                        Billetes físicos en gaveta o tesoro. Separación estricta entre mostrador, resguardo, Prepay, adicionales y custodia Claro.
                      </p>
                      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                        {fondosFisicos.map(renderCard)}
                      </div>
                    </div>

                    {/* SECCIÓN 2: CUENTAS DIGITALES (SALDOS VIRTUALES / PLATAFORMAS) — 3 CUENTAS */}
                    <div>
                      <div className="flex flex-wrap items-center gap-2 mb-3">
                        <span className="text-base">📱</span>
                        <h3 className="text-xs font-bold uppercase tracking-wider text-blue-800 dark:text-blue-300">
                          Cuentas Digitales — Plataformas y Billeteras Virtuales (3 Cuentas)
                        </h3>
                        <span className="text-[11px] bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300 font-semibold px-2 py-0.5 rounded-full">
                          {cuentasDigitales.length} Digitales (1210, 1220, 1230)
                        </span>
                      </div>
                      <p className="text-xs text-gray-500 mb-3">
                        Saldos bancarios y billeteras mayoristas independientes. Prohibido consolidar saldos o asumir equivalencias automáticas.
                      </p>
                      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                        {cuentasDigitales.map(renderCard)}
                      </div>
                    </div>

                    {/* SECCIÓN 3: COBRANZAS EN TRÁNSITO PREVENTISTAS — 1 CUENTA */}
                    {fondosTransito.length > 0 && (
                      <div>
                        <div className="flex flex-wrap items-center gap-2 mb-3">
                          <span className="text-base">🚚</span>
                          <h3 className="text-xs font-bold uppercase tracking-wider text-amber-800 dark:text-amber-300">
                            Cobranzas en Tránsito Preventistas (1 Cuenta)
                          </h3>
                          <span className="text-[11px] bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300 font-semibold px-2 py-0.5 rounded-full">
                            1310 En Tránsito (No computable como disponibilidad propia)
                          </span>
                        </div>
                        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                          {fondosTransito.map(renderCard)}
                        </div>
                      </div>
                    )}
                  </>
                );
              })()}
            </div>
          </div>
        </div>
      )}

      {/* TAB 2: PANTALLA DE MOVIMIENTOS */}
      {tabActiva === "movimientos" && (
        <div className="space-y-4">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-bold text-gray-900 dark:text-gray-100">
                Movimientos Reales de Tesorería
              </h2>
              <p className="text-xs text-gray-500">
                Entradas y salidas inmutables con contrapartida económica, trazabilidad de reversiones y auditoría.
              </p>
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => setModalMovimiento("ENTRY")}
                data-testid="btn-nueva-entrada"
                className="px-3.5 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg text-xs font-semibold shadow-sm transition"
              >
                + Registrar Entrada
              </button>
              <button
                onClick={() => setModalMovimiento("EXIT")}
                data-testid="btn-nueva-salida"
                className="px-3.5 py-2 bg-red-600 hover:bg-red-700 text-white rounded-lg text-xs font-semibold shadow-sm transition"
              >
                - Registrar Salida
              </button>
            </div>
          </div>

          {/* Barra de Filtros y Búsqueda de Movimientos */}
          <div className="bg-white dark:bg-gray-900 p-3 rounded-xl border border-gray-200 dark:border-gray-800 shadow-sm flex flex-col sm:flex-row gap-3">
            <div className="flex-1 flex gap-2">
              <input
                type="text"
                placeholder="Buscar por motivo, contrapartida, referencia de evidencia o ID..."
                value={movSearch}
                onChange={(e) => setMovSearch(e.target.value)}
                className="flex-1 p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100 text-xs"
                data-testid="input-search-movimientos"
              />
            </div>
            <div className="flex gap-2">
              <select
                value={movFondoFiltro}
                onChange={(e) => setMovFondoFiltro(e.target.value)}
                className="p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100 text-xs"
                data-testid="select-fondo-movimientos"
              >
                <option value="" className="dark:bg-gray-900">Todos los Fondos</option>
                <option value="1110" className="dark:bg-gray-900">1110 - Drugstore Diario</option>
                <option value="1120" className="dark:bg-gray-900">1120 - Drugstore Acumulado</option>
                <option value="1130" className="dark:bg-gray-900">1130 - Prepay Físico</option>
                <option value="1140" className="dark:bg-gray-900">1140 - Adicionales</option>
                <option value="1150" className="dark:bg-gray-900">1150 - Claro Físico Custodia</option>
                <option value="1210" className="dark:bg-gray-900">1210 - Mercado Pago Propio</option>
                <option value="1220" className="dark:bg-gray-900">1220 - Prepay Digital</option>
                <option value="1230" className="dark:bg-gray-900">1230 - Claro Comercios Digital</option>
                <option value="1310" className="dark:bg-gray-900">1310 - Tránsito Preventistas</option>
              </select>
              <select
                value={movTipoFiltro}
                onChange={(e) => setMovTipoFiltro(e.target.value)}
                className="p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100 text-xs"
                data-testid="select-tipo-movimientos"
              >
                <option value="" className="dark:bg-gray-900">Todos los Tipos</option>
                <option value="ENTRY" className="dark:bg-gray-900">ENTRY (Ingresos)</option>
                <option value="EXIT" className="dark:bg-gray-900">EXIT (Egresos)</option>
                <option value="TRANSFER_IN" className="dark:bg-gray-900">TRANSFER_IN (Recepción traslado)</option>
                <option value="TRANSFER_OUT" className="dark:bg-gray-900">TRANSFER_OUT (Despacho traslado)</option>
                <option value="REVERSAL" className="dark:bg-gray-900">REVERSAL (Reversiones)</option>
              </select>
            </div>
          </div>

          <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-200 dark:border-gray-800 overflow-hidden shadow-sm">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs" data-testid="tabla-movimientos">
                <thead className="bg-gray-50 dark:bg-gray-800/60 text-gray-500 uppercase tracking-wider font-semibold border-b border-gray-200 dark:border-gray-800">
                  <tr>
                    <th className="py-3 px-4">Fecha / ID</th>
                    <th className="py-3 px-4">Fondo</th>
                    <th className="py-3 px-4">Tipo</th>
                    <th className="py-3 px-4">Importe</th>
                    <th className="py-3 px-4">Contrapartida</th>
                    <th className="py-3 px-4">Motivo & Evidencia</th>
                    <th className="py-3 px-4">Usuario</th>
                    <th className="py-3 px-4">Estado</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                  {movimientos.length === 0 ? (
                    <tr>
                      <td colSpan={8} className="py-8 text-center text-gray-400">
                        No hay movimientos registrados que coincidan con los criterios seleccionados.
                      </td>
                    </tr>
                  ) : (
                    movimientos.map((m) => {
                      const esReversion = m.is_reversal || m.movement_type === "REVERSAL";
                      const esTraslado = m.movement_type === "TRANSFER_IN" || m.movement_type === "TRANSFER_OUT";

                      return (
                        <tr key={m.movement_id} className="hover:bg-gray-50 dark:hover:bg-gray-800/40">
                          <td className="py-3 px-4 font-mono text-[11px]">
                            {new Date(m.occurred_at).toLocaleString("es-AR")}
                            <span className="block text-[10px] text-gray-400 truncate max-w-[120px]">
                              {m.movement_id}
                            </span>
                          </td>
                          <td className="py-3 px-4 font-semibold text-gray-900 dark:text-gray-100">
                            {m.codigo_fondo} - {m.nombre_fondo}
                          </td>
                          <td className="py-3 px-4">
                            {esReversion ? (
                              <div>
                                <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-purple-100 text-purple-800 dark:bg-purple-950 dark:text-purple-300">
                                  ↩️ REVERSAL
                                </span>
                                {m.reversal_of_id && (
                                  <span className="block text-[10px] text-purple-600 dark:text-purple-400 font-mono mt-0.5" title={m.reversal_of_id}>
                                    Orig: {m.reversal_of_id.substring(0, 8)}...
                                  </span>
                                )}
                              </div>
                            ) : esTraslado ? (
                              <span
                                className={`px-2 py-0.5 rounded text-[11px] font-bold ${
                                  m.movement_type === "TRANSFER_IN"
                                    ? "bg-indigo-100 text-indigo-800 dark:bg-indigo-950 dark:text-indigo-300"
                                    : "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300"
                                }`}
                              >
                                🔄 {m.movement_type}
                              </span>
                            ) : (
                              <span
                                className={`px-2 py-0.5 rounded text-[11px] font-bold ${
                                  m.movement_type === "ENTRY"
                                    ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
                                    : "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300"
                                }`}
                              >
                                {m.movement_type}
                              </span>
                            )}
                          </td>
                          <td className="py-3 px-4 font-mono font-bold text-sm text-gray-900 dark:text-gray-100">
                            {m.amount_formatted}
                          </td>
                          <td className="py-3 px-4 text-gray-700 dark:text-gray-300 font-medium">
                            {m.counterpart}
                          </td>
                          <td className="py-3 px-4 text-gray-600 dark:text-gray-400">
                            <div>{m.reason}</div>
                            {m.evidence_reference && (
                              <span className="inline-block mt-0.5 px-1.5 py-0.2 rounded text-[10px] font-mono bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 border border-gray-200 dark:border-gray-700">
                                📎 {m.evidence_reference}
                              </span>
                            )}
                          </td>
                          <td className="py-3 px-4 text-gray-500">
                            {m.created_by}
                          </td>
                          <td className="py-3 px-4">
                            <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300">
                              {m.status}
                            </span>
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>

          </div>
        </div>
      )}

      {/* TAB 3: TRASLADOS DE FONDOS */}
      {tabActiva === "traslados" && (
        <div className="space-y-6">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-bold text-gray-900 dark:text-gray-100 flex items-center gap-2">
                <span>🔄 Traslados de Fondos</span>
                {traslados.filter((t) => t.status === "PENDING_RECEIPT").length > 0 && (
                  <span className="px-2 py-0.5 rounded-full text-xs font-bold bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300 animate-pulse">
                    {traslados.filter((t) => t.status === "PENDING_RECEIPT").length} en tránsito
                  </span>
                )}
              </h2>
              <p className="text-xs text-gray-500">
                Gestión de traslados internos y trazabilidad de dinero en tránsito. Requiere confirmación de recepción en destino.
              </p>
            </div>
            <div>
              <button
                onClick={() => setModalTraslado(true)}
                data-testid="btn-nuevo-traslado"
                className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-xs font-semibold shadow-sm transition flex items-center gap-1.5"
              >
                <span>+ Iniciar Traslado</span>
              </button>
            </div>
          </div>

          {/* BARRA DE FILTROS */}
          <div className="p-3 bg-gray-50 dark:bg-gray-800/40 rounded-xl border border-gray-200 dark:border-gray-800 flex flex-wrap gap-2.5 items-center text-xs">
            <div className="flex-1 min-w-[200px]">
              <input
                type="text"
                data-testid="input-filtro-trf-busqueda"
                placeholder="Buscar por ID, motivo, responsable o remito..."
                value={trfBusqueda}
                onChange={(e) => setTrfBusqueda(e.target.value)}
                className="w-full px-3 py-1.5 rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100"
              />
            </div>
            <div>
              <select
                data-testid="filtro-trf-estado"
                value={trfFiltroEstado}
                onChange={(e) => setTrfFiltroEstado(e.target.value)}
                className="px-3 py-1.5 rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100"
              >
                <option value="ALL">Todos los Estados</option>
                <option value="PENDING_RECEIPT">⏳ En Tránsito (Pendiente)</option>
                <option value="CONFIRMED">✓ Confirmados</option>
                <option value="CANCELLED">✕ Cancelados</option>
              </select>
            </div>
            <div>
              <select
                data-testid="filtro-trf-origen"
                value={trfFiltroOrigen}
                onChange={(e) => setTrfFiltroOrigen(e.target.value)}
                className="px-3 py-1.5 rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100"
              >
                <option value="">Todos los Orígenes</option>
                {fondos.map((f) => (
                  <option key={f.codigo} value={f.codigo}>
                    {f.codigo} - {f.nombre}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <select
                data-testid="filtro-trf-destino"
                value={trfFiltroDestino}
                onChange={(e) => setTrfFiltroDestino(e.target.value)}
                className="px-3 py-1.5 rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100"
              >
                <option value="">Todos los Destinos</option>
                {fondos.map((f) => (
                  <option key={f.codigo} value={f.codigo}>
                    {f.codigo} - {f.nombre}
                  </option>
                ))}
              </select>
            </div>
            {(trfBusqueda || trfFiltroEstado !== "ALL" || trfFiltroOrigen || trfFiltroDestino) && (
              <button
                onClick={() => {
                  setTrfBusqueda("");
                  setTrfFiltroEstado("ALL");
                  setTrfFiltroOrigen("");
                  setTrfFiltroDestino("");
                }}
                className="text-[11px] text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 underline"
              >
                Limpiar filtros
              </button>
            )}
          </div>

          {/* BANDEJA 1: TRASLADOS EN TRÁNSITO */}
          <div data-testid="seccion-traslados-transito" className="bg-amber-50/40 dark:bg-amber-950/20 rounded-xl border border-amber-200 dark:border-amber-800/60 p-4 shadow-sm space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-bold text-amber-900 dark:text-amber-200 flex items-center gap-2">
                <span>⏳ TRASLADOS EN TRÁNSITO</span>
                <span className="text-[11px] font-normal text-amber-700 dark:text-amber-300">
                  (Pendientes de confirmación en destino)
                </span>
              </h3>
              <span data-testid="badge-traslados-transito" className="text-xs font-mono font-bold text-amber-800 dark:text-amber-300">
                {traslados.filter((t) => t.status === "PENDING_RECEIPT").length} pendiente(s)
              </span>
            </div>

            {traslados.filter((t) => t.status === "PENDING_RECEIPT").length === 0 ? (
              <div className="py-6 text-center text-xs text-gray-500 dark:text-gray-400 italic bg-white/60 dark:bg-gray-900/40 rounded-lg border border-amber-100 dark:border-amber-900/40">
                ✓ No hay traslados en tránsito pendientes de recepción en este momento.
              </div>
            ) : (
              <div className="bg-white dark:bg-gray-900 rounded-lg border border-amber-200 dark:border-amber-800/40 overflow-x-auto">
                <table className="w-full text-left text-xs" data-testid="tabla-traslados-transito">
                  <thead className="bg-amber-100/60 dark:bg-amber-900/40 text-amber-900 dark:text-amber-200 uppercase tracking-wider font-semibold border-b border-amber-200 dark:border-amber-800">
                    <tr>
                      <th className="py-2.5 px-3">ID / Fecha</th>
                      <th className="py-2.5 px-3">Origen</th>
                      <th className="py-2.5 px-3">Destino</th>
                      <th className="py-2.5 px-3">Importe</th>
                      <th className="py-2.5 px-3">Despachado Por</th>
                      <th className="py-2.5 px-3">Motivo / Remito</th>
                      <th className="py-2.5 px-3">Salida Vinculada</th>
                      <th className="py-2.5 px-3 text-right">Acciones</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-amber-100 dark:divide-amber-900/30">
                    {traslados
                      .filter((t) => t.status === "PENDING_RECEIPT")
                      .map((t) => (
                        <tr key={t.transfer_id} className="hover:bg-amber-50/60 dark:hover:bg-amber-900/20">
                          <td className="py-3 px-3">
                            <span className="font-mono font-bold text-gray-900 dark:text-gray-100 block">
                              {t.transfer_id.substring(0, 8)}...
                            </span>
                            <span className="text-[10px] text-gray-500">
                              {new Date(t.dispatched_at).toLocaleString("es-AR")}
                            </span>
                          </td>
                          <td className="py-3 px-3 font-medium text-gray-900 dark:text-gray-100">
                            {t.origin_fund_code} - {t.origin_fund_name}
                          </td>
                          <td className="py-3 px-3 font-medium text-gray-900 dark:text-gray-100">
                            {t.destination_fund_code} - {t.destination_fund_name}
                          </td>
                          <td className="py-3 px-3 font-mono font-bold text-sm text-gray-900 dark:text-gray-100">
                            {t.amount_formatted}
                          </td>
                          <td className="py-3 px-3 text-gray-600 dark:text-gray-400">
                            {t.dispatched_by}
                          </td>
                          <td className="py-3 px-3 text-gray-600 dark:text-gray-400">
                            <div>{t.reason}</div>
                            {t.evidence_reference && (
                              <span className="text-[10px] text-indigo-600 dark:text-indigo-400 font-mono">
                                📎 {t.evidence_reference}
                              </span>
                            )}
                          </td>
                          <td className="py-3 px-3 font-mono text-[10px] text-gray-500">
                            Out: {t.out_movement_id ? `${t.out_movement_id.substring(0, 8)}...` : "—"}
                          </td>
                          <td className="py-3 px-3 text-right">
                            <div className="flex justify-end gap-1.5">
                              <button
                                onClick={() => setModalConfirmacionTrf(t)}
                                data-testid={`btn-confirmar-trf-${t.transfer_id}`}
                                className="px-2.5 py-1 bg-emerald-600 hover:bg-emerald-700 text-white rounded text-[11px] font-semibold transition"
                                title="Confirmar recepción en destino"
                              >
                                Confirmar Recepción
                              </button>
                              <button
                                onClick={() => setModalAnulacionTrf(t)}
                                data-testid={`btn-anular-trf-${t.transfer_id}`}
                                className="px-2 py-1 bg-red-100 text-red-700 hover:bg-red-200 dark:bg-red-950 dark:text-red-300 rounded text-[11px] font-semibold transition"
                                title="Anular traslado y restituir fondos a origen"
                              >
                                Anular
                              </button>
                            </div>
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* SECCIÓN 2: HISTORIAL DE TRASLADOS */}
          <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-200 dark:border-gray-800 overflow-hidden shadow-sm">
            <div className="p-3.5 border-b border-gray-200 dark:border-gray-800 flex justify-between items-center bg-gray-50/50 dark:bg-gray-800/30">
              <h3 className="text-xs font-bold uppercase tracking-wider text-gray-700 dark:text-gray-300">
                Historial de Traslados
              </h3>
              <span className="text-xs text-gray-500">
                Total: {traslados.length} registro(s)
              </span>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs" data-testid="tabla-traslados">
                <thead className="bg-gray-50 dark:bg-gray-800/60 text-gray-500 uppercase tracking-wider font-semibold border-b border-gray-200 dark:border-gray-800">
                  <tr>
                    <th className="py-3 px-4">Fecha Despacho</th>
                    <th className="py-3 px-4">Origen</th>
                    <th className="py-3 px-4">Destino</th>
                    <th className="py-3 px-4">Importe</th>
                    <th className="py-3 px-4">Motivo / Remito</th>
                    <th className="py-3 px-4">Asientos Vinculados</th>
                    <th className="py-3 px-4">Responsables</th>
                    <th className="py-3 px-4">Estado</th>
                    <th className="py-3 px-4 text-right">Acciones</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                  {traslados.length === 0 ? (
                    <tr>
                      <td colSpan={9} className="py-8 text-center text-gray-400">
                        No hay traslados registrados con los filtros seleccionados.
                      </td>
                    </tr>
                  ) : (
                    traslados.map((t) => (
                      <tr key={t.transfer_id} className="hover:bg-gray-50 dark:hover:bg-gray-800/40">
                        <td className="py-3 px-4 font-mono text-[11px]">
                          {new Date(t.dispatched_at).toLocaleString("es-AR")}
                          <span className="block text-[10px] text-gray-400">
                            ID: {t.transfer_id.substring(0, 8)}...
                          </span>
                        </td>
                        <td className="py-3 px-4 font-semibold text-gray-900 dark:text-gray-100">
                          {t.origin_fund_code} - {t.origin_fund_name}
                        </td>
                        <td className="py-3 px-4 font-semibold text-gray-900 dark:text-gray-100">
                          {t.destination_fund_code} - {t.destination_fund_name}
                        </td>
                        <td className="py-3 px-4 font-mono font-bold text-sm text-gray-900 dark:text-gray-100">
                          {t.amount_formatted}
                        </td>
                        <td className="py-3 px-4 text-gray-600 dark:text-gray-400">
                          <div>{t.reason}</div>
                          {t.evidence_reference && (
                            <span className="text-[10px] text-indigo-600 dark:text-indigo-400 font-mono">
                              📎 {t.evidence_reference}
                            </span>
                          )}
                        </td>
                        <td className="py-3 px-4 font-mono text-[10px] text-gray-500">
                          <div>
                            <span className="font-semibold text-gray-700 dark:text-gray-300">Out:</span>{" "}
                            {t.out_movement_id ? `${t.out_movement_id.substring(0, 8)}...` : "—"}
                          </div>
                          <div>
                            <span className="font-semibold text-gray-700 dark:text-gray-300">In:</span>{" "}
                            {t.in_movement_id ? (
                              `${t.in_movement_id.substring(0, 8)}...`
                            ) : t.status === "PENDING_RECEIPT" ? (
                              <span className="text-amber-600 dark:text-amber-400 font-sans font-bold">⏳ En Tránsito</span>
                            ) : t.status === "CANCELLED" ? (
                              <span className="text-gray-400 font-sans italic">Revertido a origen</span>
                            ) : (
                              "—"
                            )}
                          </div>
                        </td>
                        <td className="py-3 px-4 text-gray-500 text-[11px]">
                          <div>Desp: <span className="font-medium text-gray-700 dark:text-gray-300">{t.dispatched_by}</span></div>
                          {t.received_by && (
                            <div>Rec: <span className="font-medium text-emerald-600 dark:text-emerald-400">{t.received_by}</span></div>
                          )}
                        </td>
                        <td className="py-3 px-4">
                          <span
                            className={`px-2.5 py-0.5 rounded-full text-[10px] font-bold ${
                              t.status === "CONFIRMED"
                                ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
                                : t.status === "PENDING_RECEIPT"
                                ? "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300 animate-pulse"
                                : "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300"
                            }`}
                          >
                            {t.status === "PENDING_RECEIPT" ? "EN TRÁNSITO" : t.status === "CONFIRMED" ? "CONFIRMADO" : "CANCELADO"}
                          </span>
                        </td>
                        <td className="py-3 px-4 text-right">
                          {t.status === "PENDING_RECEIPT" ? (
                            <div className="flex justify-end gap-1.5">
                              <button
                                onClick={() => setModalConfirmacionTrf(t)}
                                className="px-2.5 py-1 bg-emerald-600 hover:bg-emerald-700 text-white rounded text-[11px] font-semibold transition"
                                title="Confirmar recepción en destino"
                              >
                                Confirmar Recepción
                              </button>
                              <button
                                onClick={() => setModalAnulacionTrf(t)}
                                className="px-2 py-1 bg-red-100 text-red-700 hover:bg-red-200 dark:bg-red-950 dark:text-red-300 rounded text-[11px] font-semibold transition"
                                title="Cancelar traslado y restituir fondos"
                              >
                                Anular
                              </button>
                            </div>
                          ) : (
                            <span className="text-gray-400 text-[11px]">—</span>
                          )}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* TAB 4: RESERVAS DE FONDOS */}
      {tabActiva === "reservas" && (
        <div className="space-y-4">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-bold text-gray-900 dark:text-gray-100">
                Reservas Preventivas de Fondos
              </h2>
              <p className="text-xs text-gray-500">
                Afectación preventiva sin salida física de efectivo. Invariante: Original = Aplicado + Liberado + Pendiente.
              </p>
            </div>
            <div>
              <button
                onClick={() => setModalReserva(true)}
                data-testid="btn-nueva-reserva"
                className="px-3.5 py-2 bg-purple-600 hover:bg-purple-700 text-white rounded-lg text-xs font-semibold shadow-sm transition"
              >
                + Crear Reserva
              </button>
            </div>
          </div>

          <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-200 dark:border-gray-800 overflow-hidden shadow-sm">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs" data-testid="tabla-reservas">
                <thead className="bg-gray-50 dark:bg-gray-800/60 text-gray-500 uppercase tracking-wider font-semibold border-b border-gray-200 dark:border-gray-800">
                  <tr>
                    <th className="py-3 px-4">Fecha / Obligación</th>
                    <th className="py-3 px-4">Fondo</th>
                    <th className="py-3 px-4">Monto Original</th>
                    <th className="py-3 px-4">Pendiente</th>
                    <th className="py-3 px-4">Aplicado</th>
                    <th className="py-3 px-4">Liberado</th>
                    <th className="py-3 px-4">Beneficiario / Motivo</th>
                    <th className="py-3 px-4">Estado</th>
                    <th className="py-3 px-4 text-right">Acciones</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                  {reservas.length === 0 ? (
                    <tr>
                      <td colSpan={9} className="py-8 text-center text-gray-400">
                        No hay reservas registradas en el laboratorio aún.
                      </td>
                    </tr>
                  ) : (
                    reservas.map((r) => (
                      <tr key={r.reservation_id} className="hover:bg-gray-50 dark:hover:bg-gray-800/40">
                        <td className="py-3 px-4 font-mono text-[11px]">
                          {new Date(r.created_at).toLocaleDateString("es-AR")}
                          {r.obligation_id && (
                            <span className="block text-[10px] text-purple-600 font-bold">
                              {r.obligation_id}
                            </span>
                          )}
                        </td>
                        <td className="py-3 px-4 font-semibold text-gray-900 dark:text-gray-100">
                          {r.fund_code} - {r.fund_name}
                        </td>
                        <td className="py-3 px-4 font-mono font-bold text-gray-900 dark:text-gray-100">
                          {r.original_amount_formatted}
                        </td>
                        <td className="py-3 px-4 font-mono font-bold text-amber-600 dark:text-amber-400">
                          {r.pending_amount_formatted}
                        </td>
                        <td className="py-3 px-4 font-mono text-emerald-600 dark:text-emerald-400">
                          {r.applied_amount_formatted}
                        </td>
                        <td className="py-3 px-4 font-mono text-blue-600 dark:text-blue-400">
                          {r.released_amount_formatted}
                        </td>
                        <td className="py-3 px-4 text-gray-700 dark:text-gray-300">
                          <strong>{r.beneficiary}</strong>: {r.reason}
                        </td>
                        <td className="py-3 px-4">
                          <span
                            className={`px-2.5 py-0.5 rounded-full text-[10px] font-bold ${
                              r.status === "ACTIVE"
                                ? "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300"
                                : r.status === "PARTIALLY_APPLIED"
                                ? "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300"
                                : r.status === "FULLY_APPLIED"
                                ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
                                : "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300"
                            }`}
                          >
                            {r.status}
                          </span>
                        </td>
                        <td className="py-3 px-4 text-right">
                          <button
                            onClick={async () => {
                              const rCompleta = await TesoreriaService.obtenerHistorialReserva(r.reservation_id);
                              setReservaSeleccionada(rCompleta);
                            }}
                            className="px-2.5 py-1 text-xs text-purple-600 dark:text-purple-400 hover:bg-purple-50 dark:hover:bg-purple-950/50 rounded border border-purple-200 dark:border-purple-800 font-medium"
                          >
                            Detalle & Gestión
                          </button>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>

          </div>
        </div>
      )}

      {/* TAB 5: ARQUEOS Y CONCILIACIONES DIGITALES (LAB-009) */}
      {tabActiva === "arqueos" && (
        <div className="space-y-6">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-bold text-gray-900 dark:text-gray-100">
                Arqueos de Fondos y Expedientes de Diferencias — Conciliación Digital
              </h2>
              <p className="text-xs text-gray-500 dark:text-gray-400">
                Recuentos físicos de efectivo y conciliación declarativa de cuentas digitales sin ajustes automáticos ni invento de saldos.
              </p>
            </div>
            <button
              onClick={() => setModalArqueo(true)}
              data-testid="btn-nuevo-arqueo"
              className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold rounded-lg shadow-sm transition"
            >
              + Modal Arqueo Rápido
            </button>
          </div>

          {/* Sub-navegación entre Fondos Físicos y Cuentas Digitales */}
          <div className="flex border-b border-gray-200 dark:border-gray-800 gap-4">
            <button
              onClick={() => setSubTabArqueos("fisicos")}
              data-testid="subtab-arqueos-fisicos"
              className={`pb-2.5 text-xs font-bold border-b-2 transition flex items-center gap-1.5 ${
                subTabArqueos === "fisicos"
                  ? "border-blue-600 text-blue-600 dark:text-blue-400"
                  : "border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
              }`}
            >
              <span>💵</span> Recuento de Fondos Físicos (1110 - 1150)
            </button>
            <button
              onClick={() => setSubTabArqueos("digitales")}
              data-testid="subtab-conciliacion-digital"
              className={`pb-2.5 text-xs font-bold border-b-2 transition flex items-center gap-1.5 ${
                subTabArqueos === "digitales"
                  ? "border-purple-600 text-purple-600 dark:text-purple-400"
                  : "border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
              }`}
            >
              <span>💳</span> Conciliación Cuentas Digitales (1210 - 1230)
            </button>
          </div>

          {/* FORMULARIO OPERATIVO: FONDOS FÍSICOS */}
          {subTabArqueos === "fisicos" && (
            <div className="bg-white dark:bg-gray-900 rounded-xl border border-blue-200 dark:border-blue-900/40 p-5 shadow-sm space-y-4">
              <div className="border-b border-gray-100 dark:border-gray-800 pb-2">
                <h3 className="text-sm font-bold text-gray-900 dark:text-gray-100 flex items-center gap-2">
                  <span>💵</span> Nuevo Arqueo de Fondo Físico
                </h3>
                <p className="text-[11px] text-gray-500">
                  Verificación física de billetes y monedas. No modifica el saldo de fondos ni genera asientos automáticos.
                </p>
              </div>

              {/* Advertencia de traslados en tránsito para el fondo seleccionado */}
              {(() => {
                const trfFondo = traslados.filter(
                  (t) =>
                    t.status === "PENDING_RECEIPT" &&
                    (t.origin_fund_code === arqFondo || t.destination_fund_code === arqFondo)
                );
                if (trfFondo.length === 0) return null;
                const totalTrfCentavos = trfFondo.reduce((acc, t) => acc + (t.amount_cents || 0), 0);
                return (
                  <div className="p-3 bg-amber-50 dark:bg-amber-950/40 border border-amber-300 dark:border-amber-700 rounded-lg text-xs space-y-1">
                    <div className="flex items-center gap-2 font-bold text-amber-800 dark:text-amber-200">
                      <span>⚠️ TRASLADOS EN TRÁNSITO DETECTADOS</span>
                    </div>
                    <p className="text-amber-700 dark:text-amber-300 text-[11px]">
                      Existen {trfFondo.length} traslado(s) pendiente(s) por un total de{" "}
                      <strong>{(totalTrfCentavos / 100).toLocaleString("es-AR", { style: "currency", currency: "ARS" })}</strong>{" "}
                      que involucran al fondo {arqFondo}.
                      <span className="block mt-0.5 font-semibold text-red-600 dark:text-red-400">
                        El dinero en tránsito NO está disponible ni acreditado en destino y NO debe ser contado físicamente hasta su recepción.
                      </span>
                    </p>
                  </div>
                );
              })()}

              <form onSubmit={handleCrearArqueoSubmit} className="space-y-4 text-xs">
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                  <div>
                    <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                      Fondo Físico
                    </label>
                    <select
                      value={arqFondo}
                      data-testid="select-arq-fondo-fisico"
                      onChange={(e) => setArqFondo(e.target.value)}
                      className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100 font-medium"
                    >
                      {fondos
                        .filter((f) => f.codigo.startsWith("11"))
                        .map((f) => (
                          <option key={f.codigo} value={f.codigo} className="dark:bg-gray-900">
                            {f.codigo} - {f.nombre} ({f.saldo_formateado})
                          </option>
                        ))}
                    </select>
                  </div>

                  <div>
                    <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                      Turno Operativo
                    </label>
                    <select
                      value={arqTurno}
                      data-testid="select-arq-turno"
                      onChange={(e) => setArqTurno(e.target.value)}
                      className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100 font-medium"
                    >
                      <option value="MAÑANA">MAÑANA</option>
                      <option value="TARDE">TARDE</option>
                      <option value="NOCHE">NOCHE</option>
                      <option value="UNICO">UNICO</option>
                    </select>
                  </div>

                  <div>
                    <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                      Tipo de Arqueo
                    </label>
                    <select
                      value={arqTipo}
                      data-testid="select-arq-tipo-fisico"
                      onChange={(e) => setArqTipo(e.target.value as TipoArqueo)}
                      className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100 font-medium"
                    >
                      <option value="PARTIAL">PARTIAL — Parcial / Rutinario</option>
                      <option value="OPENING">OPENING — Apertura</option>
                      <option value="CLOSING">CLOSING — Cierre de Jornada</option>
                      <option value="EXTRAORDINARY">EXTRAORDINARY — Extraordinario</option>
                      <option value="TOTAL">TOTAL — Total</option>
                    </select>
                  </div>
                </div>

                {/* Resumen dinámico de saldo teórico y recuento */}
                {(() => {
                  const fondoActual = fondos.find((f) => f.codigo === arqFondo);
                  const teoricoCentavos = fondoActual?.saldo_centavos || 0;
                  const contadoCentavos = arqMontoStr ? parseMoney(arqMontoStr) : 0;
                  const diffCentavos = arqMontoStr ? contadoCentavos - teoricoCentavos : 0;
                  const diffFormateado = (diffCentavos / 100).toLocaleString("es-AR", {
                    style: "currency",
                    currency: "ARS",
                  });
                  return (
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 p-3 bg-gray-50 dark:bg-gray-800/40 rounded-lg border border-gray-200 dark:border-gray-700">
                      <div>
                        <span className="text-[10px] text-gray-400 uppercase tracking-wider block">
                          Saldo Teórico en Libros
                        </span>
                        <span className="text-sm font-bold font-mono text-gray-900 dark:text-gray-100">
                          {fondoActual?.saldo_formateado || "$0,00"}
                        </span>
                      </div>
                      <div>
                        <span className="text-[10px] text-gray-400 uppercase tracking-wider block">
                          Saldo Físico Contado
                        </span>
                        <input
                          type="text"
                          required
                          data-testid="input-arq-contado"
                          placeholder="0.00"
                          value={arqMontoStr}
                          onChange={(e) => setArqMontoStr(e.target.value)}
                          className="w-full p-1.5 rounded border border-blue-400 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 font-mono text-sm font-bold"
                        />
                      </div>
                      <div>
                        <span className="text-[10px] text-gray-400 uppercase tracking-wider block">
                          Diferencia Calculada
                        </span>
                        <div className="flex items-center gap-2 mt-1">
                          <span
                            className={`text-sm font-mono font-bold ${
                              !arqMontoStr || diffCentavos === 0
                                ? "text-emerald-600 dark:text-emerald-400"
                                : diffCentavos < 0
                                ? "text-red-600 dark:text-red-400"
                                : "text-amber-600 dark:text-amber-400"
                            }`}
                          >
                            {arqMontoStr ? diffFormateado : "$0,00"}
                          </span>
                          {arqMontoStr && (
                            <span
                              className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                                diffCentavos === 0
                                  ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
                                  : diffCentavos < 0
                                  ? "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300"
                                  : "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300"
                              }`}
                            >
                              {diffCentavos === 0 ? "CONCILIADO" : diffCentavos < 0 ? "FALTANTE" : "SOBRANTE"}
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                })()}

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                      Observaciones del Arqueo
                    </label>
                    <input
                      type="text"
                      data-testid="input-arq-obs"
                      placeholder="Observaciones o notas del arqueo físico..."
                      value={arqObservaciones}
                      onChange={(e) => setArqObservaciones(e.target.value)}
                      className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100"
                    />
                  </div>
                  <div>
                    <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                      Responsable / Operador
                    </label>
                    <div className="p-2 bg-gray-100 dark:bg-gray-800 rounded-lg text-gray-700 dark:text-gray-300 font-medium">
                      {currentUser?.display_name || currentUser?.username || "alan"} (Sesión autenticada)
                    </div>
                  </div>
                </div>

                <div className="pt-2 border-t border-gray-100 dark:border-gray-800 flex flex-col sm:flex-row justify-between items-start sm:items-center gap-3">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={arqCrearExpediente}
                      onChange={(e) => setArqCrearExpediente(e.target.checked)}
                      className="rounded text-blue-600"
                    />
                    <span className="text-gray-700 dark:text-gray-300 text-xs">
                      Abrir expediente de investigación si existe diferencia
                    </span>
                  </label>

                  <button
                    type="submit"
                    data-testid="btn-confirmar-arqueo-fisico"
                    className="px-5 py-2 bg-blue-600 hover:bg-blue-700 text-white font-semibold rounded-lg shadow-sm transition"
                  >
                    Confirmar Arqueo Físico
                  </button>
                </div>
              </form>
            </div>
          )}

          {/* FORMULARIO OPERATIVO: CONCILIACIÓN DIGITAL */}
          {subTabArqueos === "digitales" && (
            <div className="bg-white dark:bg-gray-900 rounded-xl border border-purple-200 dark:border-purple-900/40 p-5 shadow-sm space-y-4">
              <div className="border-b border-gray-100 dark:border-gray-800 pb-2">
                <h3 className="text-sm font-bold text-gray-900 dark:text-gray-100 flex items-center gap-2">
                  <span>💳</span> Conciliación de Cuenta Digital
                </h3>
                <p className="text-[11px] text-gray-500">
                  Registro declarativo del saldo informado por plataformas externas (Mercado Pago, PREPAY, Claro Comercios).
                </p>
              </div>

              {/* Banner obligatorio de Declaración Manual */}
              <div className="p-3 bg-blue-50 dark:bg-blue-950/40 border border-blue-200 dark:border-blue-800 rounded-lg text-xs space-y-1">
                <div className="flex items-center gap-2 font-bold text-blue-900 dark:text-blue-200">
                  <span>ℹ️ DECLARACIÓN MANUAL DE SALDO DIGITAL</span>
                </div>
                <p className="text-blue-800 dark:text-blue-300 text-[11px]">
                  Esta conciliación no realiza consultas automáticas vía API externa ni altera saldos contables. Registra el
                  saldo observado e informado manualmente por el operador tras consultar el portal o extracto oficial de la plataforma.
                </p>
              </div>

              <form onSubmit={handleCrearConciliacionDigitalSubmit} className="space-y-4 text-xs">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                      Cuenta Digital
                    </label>
                    <select
                      value={digFondo}
                      data-testid="select-dig-fondo"
                      onChange={(e) => setDigFondo(e.target.value)}
                      className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100 font-medium"
                    >
                      {fondos
                        .filter((f) => f.codigo.startsWith("12"))
                        .map((f) => (
                          <option key={f.codigo} value={f.codigo} className="dark:bg-gray-900">
                            {f.codigo} - {f.nombre} ({f.saldo_formateado})
                          </option>
                        ))}
                    </select>
                  </div>

                  <div>
                    <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                      Fecha y Hora de Consulta en Plataforma
                    </label>
                    <input
                      type="datetime-local"
                      required
                      data-testid="input-dig-fecha-consulta"
                      value={digFechaConsulta}
                      onChange={(e) => setDigFechaConsulta(e.target.value)}
                      className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100 font-mono"
                    />
                  </div>
                </div>

                {/* Resumen dinámico digital */}
                {(() => {
                  const fondoActual = fondos.find((f) => f.codigo === digFondo);
                  const teoricoCentavos = fondoActual?.saldo_centavos || 0;
                  const informadoCentavos = digMontoStr ? parseMoney(digMontoStr) : 0;
                  const diffCentavos = digMontoStr ? informadoCentavos - teoricoCentavos : 0;
                  const diffFormateado = (diffCentavos / 100).toLocaleString("es-AR", {
                    style: "currency",
                    currency: "ARS",
                  });
                  return (
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 p-3 bg-gray-50 dark:bg-gray-800/40 rounded-lg border border-gray-200 dark:border-gray-700">
                      <div>
                        <span className="text-[10px] text-gray-400 uppercase tracking-wider block">
                          Saldo Teórico Interno
                        </span>
                        <span className="text-sm font-bold font-mono text-gray-900 dark:text-gray-100">
                          {fondoActual?.saldo_formateado || "$0,00"}
                        </span>
                      </div>
                      <div>
                        <span className="text-[10px] text-gray-400 uppercase tracking-wider block">
                          Saldo Informado por Plataforma
                        </span>
                        <input
                          type="text"
                          required
                          data-testid="input-dig-monto"
                          placeholder="0.00"
                          value={digMontoStr}
                          onChange={(e) => setDigMontoStr(e.target.value)}
                          className="w-full p-1.5 rounded border border-purple-400 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 font-mono text-sm font-bold"
                        />
                      </div>
                      <div>
                        <span className="text-[10px] text-gray-400 uppercase tracking-wider block">
                          Diferencia Detectada
                        </span>
                        <div className="flex items-center gap-2 mt-1">
                          <span
                            className={`text-sm font-mono font-bold ${
                              !digMontoStr || diffCentavos === 0
                                ? "text-emerald-600 dark:text-emerald-400"
                                : diffCentavos < 0
                                ? "text-red-600 dark:text-red-400"
                                : "text-amber-600 dark:text-amber-400"
                            }`}
                          >
                            {digMontoStr ? diffFormateado : "$0,00"}
                          </span>
                          {digMontoStr && (
                            <span
                              className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                                diffCentavos === 0
                                  ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
                                  : diffCentavos < 0
                                  ? "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300"
                                  : "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300"
                              }`}
                            >
                              {diffCentavos === 0 ? "CONCILIADO" : diffCentavos < 0 ? "FALTANTE" : "SOBRANTE"}
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                })()}

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                      Evidencia o Referencia de Consulta (Ej. ID Extracto / Liquidación)
                    </label>
                    <input
                      type="text"
                      required
                      data-testid="input-dig-evidencia"
                      placeholder="Ej. Extracto MP #48921 del portal..."
                      value={digEvidencia}
                      onChange={(e) => setDigEvidencia(e.target.value)}
                      className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100"
                    />
                  </div>

                  <div>
                    <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                      Observaciones
                    </label>
                    <input
                      type="text"
                      data-testid="input-dig-obs"
                      placeholder="Observaciones de la conciliación digital..."
                      value={digObservaciones}
                      onChange={(e) => setDigObservaciones(e.target.value)}
                      className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100"
                    />
                  </div>
                </div>

                <div className="pt-2 border-t border-gray-100 dark:border-gray-800 flex flex-col sm:flex-row justify-between items-start sm:items-center gap-3">
                  <div className="text-gray-500 text-[11px]">
                    Operador responsable: <strong>{currentUser?.display_name || currentUser?.username || "alan"}</strong>
                  </div>

                  <button
                    type="submit"
                    data-testid="btn-confirmar-conciliacion-digital"
                    className="px-5 py-2 bg-purple-600 hover:bg-purple-700 text-white font-semibold rounded-lg shadow-sm transition"
                  >
                    Registrar Conciliación Digital
                  </button>
                </div>
              </form>
            </div>
          )}

          {/* Historial de Arqueos y Conciliaciones */}
          <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-200 dark:border-gray-800 shadow-sm overflow-hidden">
            <div className="p-4 border-b border-gray-200 dark:border-gray-800 flex justify-between items-center">
              <h3 className="text-xs font-bold text-gray-900 dark:text-gray-100 uppercase tracking-wider">
                Historial de Arqueos y Conciliaciones ({arqueos.length})
              </h3>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse text-xs">
                <thead>
                  <tr className="bg-gray-50 dark:bg-gray-800/60 text-gray-500 uppercase text-[10px] tracking-wider border-b border-gray-200 dark:border-gray-800">
                    <th className="py-2.5 px-4 font-semibold">ID / Fecha</th>
                    <th className="py-2.5 px-4 font-semibold">Fondo</th>
                    <th className="py-2.5 px-4 font-semibold">Turno / Consulta</th>
                    <th className="py-2.5 px-4 font-semibold text-right">Saldo Teórico</th>
                    <th className="py-2.5 px-4 font-semibold text-right">Saldo Constatado</th>
                    <th className="py-2.5 px-4 font-semibold text-right">Diferencia</th>
                    <th className="py-2.5 px-4 font-semibold">Estado</th>
                    <th className="py-2.5 px-4 font-semibold">Evidencia / Notas</th>
                    <th className="py-2.5 px-4 font-semibold">Operador</th>
                    <th className="py-2.5 px-4 font-semibold">Expediente</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                  {arqueos.length === 0 ? (
                    <tr>
                      <td colSpan={10} className="py-8 text-center text-gray-400 italic">
                        No hay arqueos ni conciliaciones registrados en la jornada actual.
                      </td>
                    </tr>
                  ) : (
                    arqueos.map((a) => {
                      const esDigital = a.fund_code.startsWith("12") || a.is_digital;
                      return (
                        <tr key={a.reconciliation_id} className="hover:bg-gray-50/50 dark:hover:bg-gray-800/30">
                          <td className="py-3 px-4">
                            <span className="font-mono text-gray-900 dark:text-gray-100 font-bold block">
                              {a.reconciliation_id.substring(0, 8)}...
                            </span>
                            <span className="text-[10px] text-gray-400">
                              {new Date(a.created_at || Date.now()).toLocaleString("es-AR")}
                            </span>
                          </td>
                          <td className="py-3 px-4">
                            <div className="font-medium text-gray-800 dark:text-gray-200 flex items-center gap-1.5">
                              <span
                                className={`px-1.5 py-0.5 rounded text-[9px] font-bold ${
                                  esDigital
                                    ? "bg-purple-100 dark:bg-purple-950 text-purple-700 dark:text-purple-300"
                                    : "bg-blue-100 dark:bg-blue-950 text-blue-700 dark:text-blue-300"
                                }`}
                              >
                                {esDigital ? "DIGITAL" : "FÍSICO"}
                              </span>
                              <span>{a.fund_code} - {a.fund_name}</span>
                            </div>
                          </td>
                          <td className="py-3 px-4 text-gray-600 dark:text-gray-400">
                            {a.turno ? (
                              <span className="font-semibold text-gray-700 dark:text-gray-300">Turno: {a.turno}</span>
                            ) : a.fecha_consulta ? (
                              <span className="text-[10px]">Cons: {new Date(a.fecha_consulta).toLocaleTimeString("es-AR")}</span>
                            ) : (
                              <span className="text-[10px] text-gray-400">{a.reconciliation_type}</span>
                            )}
                          </td>
                          <td className="py-3 px-4 text-right font-mono text-gray-700 dark:text-gray-300">
                            {a.registered_amount_formatted}
                          </td>
                          <td className="py-3 px-4 text-right font-mono text-gray-700 dark:text-gray-300">
                            {a.verified_amount_formatted}
                          </td>
                          <td className="py-3 px-4 text-right font-mono font-bold">
                            <span
                              className={
                                (a.difference_cents ?? 0) === 0
                                  ? "text-emerald-600 dark:text-emerald-400"
                                  : (a.difference_cents ?? 0) < 0
                                  ? "text-red-600 dark:text-red-400"
                                  : "text-amber-600 dark:text-amber-400"
                              }
                            >
                              {a.difference_formatted}
                            </span>
                          </td>
                          <td className="py-3 px-4">
                            <div className="flex flex-col gap-1 items-start">
                              <span
                                className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                                  a.difference_cents === 0
                                    ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
                                    : (a.difference_cents ?? 0) < 0
                                    ? "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300"
                                    : "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300"
                                }`}
                              >
                                {a.difference_cents === 0
                                  ? "CONCILIADO"
                                  : (a.difference_cents ?? 0) < 0
                                  ? "FALTANTE"
                                  : "SOBRANTE"}
                              </span>
                              {a.difference_cents !== 0 && (
                                a.is_authorized || a.authorized_by ? (
                                  <div className="flex flex-col gap-0.5">
                                    <span
                                      className="px-2 py-0.5 rounded text-[9px] font-bold bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300"
                                      title={a.authorization_notes || "Autorizado"}
                                    >
                                      AUTORIZADO ({a.authorized_by})
                                    </span>
                                    {a.authorized_at && (
                                      <span className="text-[9px] text-gray-500 font-mono">
                                        {new Date(a.authorized_at).toLocaleString("es-AR")}
                                      </span>
                                    )}
                                  </div>
                                ) : (
                                  <span className="px-2 py-0.5 rounded text-[9px] font-bold bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300">
                                    PENDIENTE AUTORIZACIÓN
                                  </span>
                                )
                              )}
                            </div>
                          </td>
                          <td className="py-3 px-4 text-gray-500 max-w-xs truncate">
                            {a.evidencia_referencia && (
                              <span className="block font-medium text-gray-700 dark:text-gray-300 text-[10px]">
                                Evid: {a.evidencia_referencia}
                              </span>
                            )}
                            {a.notes && <span className="text-[10px] italic">{a.notes}</span>}
                            {a.authorization_notes && (
                              <span className="block text-[9px] text-blue-600 dark:text-blue-400">
                                Auth: {a.authorization_notes}
                              </span>
                            )}
                          </td>
                          <td className="py-3 px-4 text-gray-500">{a.performed_by}</td>
                          <td className="py-3 px-4">
                            <div className="flex flex-col gap-1 items-start">
                              {a.difference_cents !== 0 ? (
                                <span className="px-2 py-0.5 bg-blue-50 dark:bg-blue-950 text-blue-700 dark:text-blue-300 rounded font-mono text-[10px] font-medium">
                                  Diferencia documentada
                                </span>
                              ) : (
                                <span className="text-gray-400 text-[10px]">Sin diferencias</span>
                              )}
                              {a.difference_case_id && (
                                <span className="px-2 py-0.5 bg-purple-100 dark:bg-purple-950 text-purple-800 dark:text-purple-300 rounded font-mono text-[10px] font-bold">
                                  EXP: {a.difference_case_id.substring(0, 6)}...
                                </span>
                              )}
                            </div>
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* Expedientes de Diferencias */}
          <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-200 dark:border-gray-800 shadow-sm overflow-hidden">
            <div className="p-4 border-b border-gray-200 dark:border-gray-800">
              <h3 className="text-xs font-bold text-gray-900 dark:text-gray-100 uppercase tracking-wider">
                Expedientes de Investigación ({expedientes.length})
              </h3>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse text-xs">
                <thead>
                  <tr className="bg-gray-50 dark:bg-gray-800/60 text-gray-500 uppercase text-[10px] tracking-wider border-b border-gray-200 dark:border-gray-800">
                    <th className="py-2.5 px-4 font-semibold">Expediente ID</th>
                    <th className="py-2.5 px-4 font-semibold text-right">Diferencia</th>
                    <th className="py-2.5 px-4 font-semibold">Estado</th>
                    <th className="py-2.5 px-4 font-semibold">Notas de Investigación</th>
                    <th className="py-2.5 px-4 font-semibold">Resolución</th>
                    <th className="py-2.5 px-4 font-semibold text-right">Acción</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                  {expedientes.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="py-8 text-center text-gray-400 italic">
                        No hay expedientes de diferencias abiertos.
                      </td>
                    </tr>
                  ) : (
                    expedientes.map((exp) => (
                      <tr key={exp.case_id} className="hover:bg-gray-50/50 dark:hover:bg-gray-800/30">
                        <td className="py-3 px-4 font-mono font-bold text-gray-900 dark:text-gray-100">
                          {exp.case_id.substring(0, 10)}...
                        </td>
                        <td className="py-3 px-4 text-right font-mono font-bold text-red-600 dark:text-red-400">
                          {exp.difference_formatted}
                        </td>
                        <td className="py-3 px-4">
                          <span
                            className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                              exp.status === "RESOLVED"
                                ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
                                : "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300"
                            }`}
                          >
                            {exp.status}
                          </span>
                        </td>
                        <td className="py-3 px-4 text-gray-600 dark:text-gray-400">
                          {exp.investigation_notes || "—"}
                        </td>
                        <td className="py-3 px-4 text-gray-600 dark:text-gray-400">
                          {exp.status === "RESOLVED" ? (
                            <div>
                              <p className="font-semibold text-gray-900 dark:text-gray-100">{exp.resolution_notes}</p>
                              <span className="text-[10px] text-gray-400">
                                Por {exp.resolved_by} (Evidencia: {exp.evidence_reference || "N/A"})
                              </span>
                            </div>
                          ) : (
                            <span className="text-gray-400 italic">En investigación</span>
                          )}
                        </td>
                        <td className="py-3 px-4 text-right">
                          {exp.status !== "RESOLVED" && (
                            <button
                              onClick={() => {
                                setExpedienteSeleccionado(exp);
                                setResNotas("");
                                setResEvidencia("");
                              }}
                              className="px-2.5 py-1 bg-purple-600 hover:bg-purple-700 text-white rounded text-[11px] font-semibold"
                            >
                              Resolver
                            </button>
                          )}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* TAB 6: CIERRES DEFINITIVOS Y CONTROL INTEGRAL (LAB-010) */}
      {tabActiva === "cierres" && (
        <div className="space-y-6">
          <div>
            <h2 className="text-lg font-bold text-gray-900 dark:text-gray-100">
              Protocolo de Cierre Diario Definitivo
            </h2>
            <p className="text-xs text-gray-500 dark:text-gray-400">
              Control exhaustivo de los 9 fondos, verificación de traslados en tránsito, snapshot inmutable con SHA-256 canónico y preservación sin mutaciones.
            </p>
          </div>

          {/* Estado de la Jornada para Cierre */}
          <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-200 dark:border-gray-800 p-5 shadow-sm space-y-4">
            <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-3">
              <div>
                <span className="text-xs text-gray-500 uppercase tracking-wider block">Jornada Activa</span>
                <span className="text-xl font-bold text-gray-900 dark:text-gray-100">
                  {jornadaActiva ? `Jornada #${jornadaActiva.numero_jornada} (${jornadaActiva.fecha_operativa})` : "Sin jornada abierta"}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <span
                  className={`px-3 py-1 rounded-md text-xs font-bold ${
                    jornadaActiva?.estado === "ABIERTA"
                      ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
                      : jornadaActiva?.estado === "EN_CIERRE"
                      ? "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300 animate-pulse"
                      : "bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-300"
                  }`}
                >
                  ESTADO: {jornadaActiva?.estado || "CERRADA"}
                </span>
              </div>
            </div>

            {/* TABLA DE LOS 9 FONDOS Y ESTADO DE ARQUEO EN LA JORNADA */}
            <div className="border border-gray-100 dark:border-gray-800 rounded-lg overflow-hidden">
              <div className="p-3 bg-gray-50 dark:bg-gray-800/60 font-bold text-xs text-gray-700 dark:text-gray-300 uppercase tracking-wider">
                Estado de Fondos y Conciliaciones al Cierre
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs border-collapse">
                  <thead>
                    <tr className="border-b border-gray-100 dark:border-gray-800 text-[10px] text-gray-400 uppercase">
                      <th className="p-2.5 px-3">Fondo</th>
                      <th className="p-2.5 px-3">Naturaleza</th>
                      <th className="p-2.5 px-3 text-right">Saldo Teórico</th>
                      <th className="p-2.5 px-3 text-right">Saldo Constatado</th>
                      <th className="p-2.5 px-3 text-right">Diferencia</th>
                      <th className="p-2.5 px-3 text-center">Estado</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                    {fondos.map((f) => {
                      const ultArqueo = arqueos.find((a) => a.fund_code === f.codigo);
                      const esFisico = f.codigo.startsWith("11");
                      const esDigital = f.codigo.startsWith("12");
                      const naturaleza = esFisico ? "Físico" : esDigital ? "Digital" : "Segregada";
                      const conciliado = ultArqueo ? ultArqueo.difference_cents === 0 : false;
                      return (
                        <tr key={f.codigo} className="hover:bg-gray-50/50 dark:hover:bg-gray-800/20">
                          <td className="p-2.5 px-3 font-medium">
                            <span className="font-mono font-bold mr-1">{f.codigo}</span> {f.nombre}
                          </td>
                          <td className="p-2.5 px-3">
                            <span
                              className={`px-1.5 py-0.5 rounded text-[9px] font-bold ${
                                esFisico
                                  ? "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300"
                                  : esDigital
                                  ? "bg-purple-100 text-purple-700 dark:bg-purple-950 dark:text-purple-300"
                                  : "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300"
                              }`}
                            >
                              {naturaleza}
                            </span>
                          </td>
                          <td className="p-2.5 px-3 text-right font-mono text-gray-700 dark:text-gray-300">
                            {f.saldo_formateado}
                          </td>
                          <td className="p-2.5 px-3 text-right font-mono">
                            {ultArqueo ? ultArqueo.verified_amount_formatted : "—"}
                          </td>
                          <td className="p-2.5 px-3 text-right font-mono font-bold">
                            {ultArqueo ? (
                              <span
                                className={
                                  ultArqueo.difference_cents === 0
                                    ? "text-emerald-600 dark:text-emerald-400"
                                    : "text-red-600 dark:text-red-400"
                                }
                              >
                                {ultArqueo.difference_formatted}
                              </span>
                            ) : (
                              <span className="text-gray-400">—</span>
                            )}
                          </td>
                          <td className="p-2.5 px-3 text-center">
                            {ultArqueo ? (
                              <span
                                className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                                  conciliado
                                    ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
                                    : "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300"
                                }`}
                              >
                                {conciliado ? "CONCILIADO" : "CON DIFERENCIA"}
                              </span>
                            ) : (
                              <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400">
                                PENDIENTE
                              </span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>

            {/* SECCIÓN TRASLADOS EN TRÁNSITO */}
            {(() => {
              const trasladosEnTransito = traslados.filter((t) => t.status === "PENDING_RECEIPT");
              const totalTrfCentavos = trasladosEnTransito.reduce((acc, t) => acc + (t.amount_cents || 0), 0);
              return (
                <div className="border border-gray-100 dark:border-gray-800 rounded-lg p-3 space-y-2">
                  <div className="flex justify-between items-center">
                    <span className="text-xs font-bold text-gray-700 dark:text-gray-300 uppercase tracking-wider flex items-center gap-1.5">
                      <span>🚚</span> Traslados en Tránsito ({trasladosEnTransito.length})
                    </span>
                    <span className="font-mono text-xs font-bold text-amber-700 dark:text-amber-300">
                      Total: {(totalTrfCentavos / 100).toLocaleString("es-AR", { style: "currency", currency: "ARS" })}
                    </span>
                  </div>

                  {trasladosEnTransito.length > 0 ? (
                    <div className="space-y-2">
                      <div className="p-2.5 bg-amber-50 dark:bg-amber-950/40 border border-amber-300 dark:border-amber-700 rounded text-xs text-amber-800 dark:text-amber-200">
                        ⚠️ <strong>ATENCIÓN:</strong> El dinero en tránsito NO está acreditado en destino y NO debe ser
                        computado como disponibilidad de la cuenta receptora hasta que se confirme formalmente la recepción.
                      </div>
                      <div className="overflow-x-auto">
                        <table className="w-full text-left text-xs">
                          <thead>
                            <tr className="border-b border-gray-100 dark:border-gray-800 text-[10px] text-gray-400">
                              <th className="p-1.5">ID Traslado</th>
                              <th className="p-1.5">Origen ➔ Destino</th>
                              <th className="p-1.5 text-right">Importe</th>
                              <th className="p-1.5">Responsable</th>
                              <th className="p-1.5">Fecha Despacho</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                            {trasladosEnTransito.map((trf) => (
                              <tr key={trf.transfer_id}>
                                <td className="p-1.5 font-mono font-bold text-blue-600 dark:text-blue-400">
                                  {trf.transfer_id.substring(0, 8)}...
                                </td>
                                <td className="p-1.5 font-mono">
                                  {trf.origin_fund_code} ➔ {trf.destination_fund_code}
                                </td>
                                <td className="p-1.5 text-right font-mono font-bold">
                                  {trf.amount_formatted}
                                </td>
                                <td className="p-1.5 text-gray-500">{trf.dispatched_by}</td>
                                <td className="p-1.5 text-gray-400">
                                  {new Date(trf.dispatched_at).toLocaleString("es-AR")}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  ) : (
                    <p className="text-xs text-gray-400 italic">No hay traslados en tránsito pendientes de recepción.</p>
                  )}
                </div>
              );
            })()}

            {/* Checkboxes de Autorización y Cierre */}
            <div className="p-3 bg-gray-50 dark:bg-gray-800/40 rounded-lg border border-gray-200 dark:border-gray-700 space-y-2 text-xs">
              <span className="font-bold text-gray-700 dark:text-gray-300 block">
                Opciones de Autorización Operativa para Cierre:
              </span>
              <div className="flex flex-col sm:flex-row gap-4">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    data-testid="chk-allow-pending-transfers"
                    checked={allowPendingTransfers}
                    onChange={(e) => setAllowPendingTransfers(e.target.checked)}
                    className="rounded text-amber-600"
                  />
                  <span className="text-gray-700 dark:text-gray-300">
                    Autorizar cierre con traslados en tránsito pendientes (se registrará como advertencia en el snapshot)
                  </span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    data-testid="chk-allow-nonzero-1110"
                    checked={allowNonzero1110}
                    onChange={(e) => setAllowNonzero1110(e.target.checked)}
                    className="rounded text-amber-600"
                  />
                  <span className="text-gray-700 dark:text-gray-300">
                    Autorizar cierre conservando saldo en Caja 1110 sin requerir barrido forzado
                  </span>
                </label>
              </div>
            </div>

            {/* Acciones de Flujo de Cierre */}
            <div className="flex flex-wrap gap-2 pt-2 border-t border-gray-100 dark:border-gray-800">
              {jornadaActiva?.estado === "ABIERTA" && (
                <button
                  onClick={handleIniciarCierre}
                  data-testid="btn-iniciar-cierre"
                  className="px-4 py-2 bg-amber-600 hover:bg-amber-700 text-white text-xs font-semibold rounded-lg shadow-sm"
                >
                  1. Iniciar Protocolo de Cierre (Bloquear Operaciones)
                </button>
              )}

              {jornadaActiva?.estado === "EN_CIERRE" && (
                <>
                  <button
                    onClick={handleValidarCierre}
                    data-testid="btn-validar-cierre"
                    className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold rounded-lg shadow-sm"
                  >
                    2. Validar Precondiciones de Cierre
                  </button>
                  <button
                    onClick={handleVaciado1110}
                    data-testid="btn-vaciado-1110"
                    className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold rounded-lg shadow-sm"
                  >
                    3. Ejecutar Barrido 1110 a 1120
                  </button>
                  <button
                    onClick={handleVaciado1160}
                    data-testid="btn-vaciado-1160"
                    className="px-4 py-2 bg-purple-600 hover:bg-purple-700 text-white text-xs font-semibold rounded-lg shadow-sm"
                  >
                    3b. Ejecutar Barrido PREPAY 1160 a 1130
                  </button>
                  <button
                    onClick={() => setModalConfirmarCierre(true)}
                    data-testid="btn-confirmar-cierre"
                    className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold rounded-lg shadow-sm"
                  >
                    4. Confirmar Cierre Definitivo
                  </button>
                </>
              )}

              {(!jornadaActiva || jornadaActiva.estado === "CERRADA") && (
                <button
                  onClick={handleAbrirSiguienteJornada}
                  data-testid="btn-siguiente-jornada"
                  className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold rounded-lg shadow-sm"
                >
                  Abrir Siguiente Jornada (1110 = $0,00)
                </button>
              )}
            </div>

            {vaciadoMsg && (
              <div className="p-3 bg-blue-50 dark:bg-blue-950/40 text-blue-800 dark:text-blue-300 rounded text-xs border border-blue-200 dark:border-blue-800">
                {vaciadoMsg}
              </div>
            )}
          </div>

          {/* Panel de Validación de Precondiciones */}
          {validacionCierre && (
            <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-200 dark:border-gray-800 p-5 shadow-sm space-y-4">
              <div className="flex justify-between items-center">
                <h3 className="text-xs font-bold uppercase tracking-wider text-gray-500">
                  Informe de Precondiciones de Cierre
                </h3>
                <span
                  className={`px-2.5 py-0.5 rounded text-xs font-bold ${
                    validacionCierre.can_close
                      ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
                      : "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300"
                  }`}
                >
                  {validacionCierre.can_close ? "LISTO PARA CIERRE" : "BLOQUEADO"}
                </span>
              </div>

              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
                <div className="p-3 bg-gray-50 dark:bg-gray-800/40 rounded">
                  <span className="text-gray-400 block">Movimientos Pendientes</span>
                  <span className="font-bold text-gray-900 dark:text-gray-100">{validacionCierre.pending_reviews_count}</span>
                </div>
                <div className="p-3 bg-gray-50 dark:bg-gray-800/40 rounded">
                  <span className="text-gray-400 block">Traslados en Tránsito</span>
                  <span className="font-bold text-gray-900 dark:text-gray-100">{validacionCierre.transfers_in_transit_count}</span>
                </div>
                <div className="p-3 bg-gray-50 dark:bg-gray-800/40 rounded">
                  <span className="text-gray-400 block">Saldo Caja 1110</span>
                  <span className="font-bold text-gray-900 dark:text-gray-100">{validacionCierre.cash_drawer_balance_formatted}</span>
                </div>
                <div className="p-3 bg-gray-50 dark:bg-gray-800/40 rounded">
                  <span className="text-gray-400 block">Diferencias Abiertas</span>
                  <span className="font-bold text-gray-900 dark:text-gray-100">{validacionCierre.open_differences_count}</span>
                </div>
              </div>

              {validacionCierre.blockers.length > 0 && (
                <div className="space-y-1">
                  <span className="text-xs font-bold text-red-600 dark:text-red-400">Bloqueadores Críticos:</span>
                  <ul className="list-disc pl-5 text-xs text-red-700 dark:text-red-300 space-y-0.5">
                    {validacionCierre.blockers.map((b, idx) => (
                      <li key={idx}>{b}</li>
                    ))}
                  </ul>
                </div>
              )}

              {validacionCierre.warnings.length > 0 && (
                <div className="space-y-1">
                  <span className="text-xs font-bold text-amber-600 dark:text-amber-400">Advertencias Operativas:</span>
                  <ul className="list-disc pl-5 text-xs text-amber-700 dark:text-amber-300 space-y-0.5">
                    {validacionCierre.warnings.map((w, idx) => (
                      <li key={idx}>{w}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}

          {/* Snapshot de Cierre Confirmado */}
          {cierreSnapshot && (
            <div className="bg-emerald-50 dark:bg-emerald-950/30 border border-emerald-200 dark:border-emerald-800 rounded-xl p-5 space-y-3">
              <div className="flex items-center gap-2 text-emerald-800 dark:text-emerald-300 font-bold">
                <span>✓ Cierre Definitivo Confirmado Exitosamente</span>
              </div>
              <p className="text-xs text-emerald-700 dark:text-emerald-400">
                La jornada #{cierreSnapshot.journal_id} ha sido archivada. Se generó el siguiente hash SHA-256 canónico:
              </p>
              <div className="p-3 bg-white dark:bg-gray-900 rounded border border-emerald-300 dark:border-emerald-700 font-mono text-xs break-all text-gray-900 dark:text-gray-100 select-all">
                {cierreSnapshot.snapshot_hash}
              </div>
            </div>
          )}

          {/* HISTORIAL DE CIERRES DEFINITIVOS */}
          <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-200 dark:border-gray-800 shadow-sm overflow-hidden">
            <div className="p-4 border-b border-gray-200 dark:border-gray-800">
              <h3 className="text-xs font-bold text-gray-900 dark:text-gray-100 uppercase tracking-wider">
                Historial de Cierres de Jornada ({historialCierres.length})
              </h3>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse text-xs">
                <thead>
                  <tr className="bg-gray-50 dark:bg-gray-800/60 text-gray-500 uppercase text-[10px] tracking-wider border-b border-gray-200 dark:border-gray-800">
                    <th className="py-2.5 px-4 font-semibold">Jornada #</th>
                    <th className="py-2.5 px-4 font-semibold">Fecha Cierre</th>
                    <th className="py-2.5 px-4 font-semibold">Cerrado Por</th>
                    <th className="py-2.5 px-4 font-semibold font-mono">Hash SHA-256 Canónico</th>
                    <th className="py-2.5 px-4 font-semibold text-right">Acción</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                  {historialCierres.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="py-8 text-center text-gray-400 italic">
                        No hay cierres definitivos históricos registrados aún.
                      </td>
                    </tr>
                  ) : (
                    historialCierres.map((c) => (
                      <tr key={c.journal_id} className="hover:bg-gray-50/50 dark:hover:bg-gray-800/30">
                        <td className="py-3 px-4 font-bold text-gray-900 dark:text-gray-100">
                          Jornada #{c.journal_id}
                        </td>
                        <td className="py-3 px-4 text-gray-500">
                          {c.closed_at ? new Date(c.closed_at).toLocaleString("es-AR") : "—"}
                        </td>
                        <td className="py-3 px-4 text-gray-600 dark:text-gray-400">
                          {c.closed_by || "SISTEMA"}
                        </td>
                        <td className="py-3 px-4 font-mono text-[11px] text-gray-500 select-all">
                          {c.snapshot_hash ? `${c.snapshot_hash.substring(0, 16)}...` : "—"}
                        </td>
                        <td className="py-3 px-4 text-right">
                          <button
                            onClick={() => setModalVerSnapshot(c)}
                            data-testid={`btn-ver-snapshot-${c.journal_id}`}
                            className="px-2.5 py-1 bg-blue-600 hover:bg-blue-700 text-white rounded text-[11px] font-semibold"
                          >
                            Ver Snapshot
                          </button>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* TAB 7: REVERSIONES INMUTABLES (LAB-011) */}
      {tabActiva === "reversiones" && (
        <div className="space-y-6">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-bold text-gray-900 dark:text-gray-100">
                Reversiones Inmutables y Contra-Asientos
              </h2>
              <p className="text-xs text-gray-500 dark:text-gray-400">
                Registro inmutable de reversiones totales y parciales vinculadas al objeto original sin mutación destructiva.
              </p>
            </div>
            <button
              onClick={() => {
                const targetId = prompt("Ingrese el Movement ID a revertir:");
                if (targetId) {
                  setModalReversion({ tipo: "MOVEMENT", targetId });
                }
              }}
              data-testid="btn-nueva-reversion"
              className="px-4 py-2 bg-red-600 hover:bg-red-700 text-white text-xs font-semibold rounded-lg shadow-sm transition"
            >
              + Revertir Movimiento
            </button>
          </div>

          <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-200 dark:border-gray-800 shadow-sm overflow-hidden">
            <div className="p-4 border-b border-gray-200 dark:border-gray-800">
              <h3 className="text-xs font-bold text-gray-900 dark:text-gray-100 uppercase tracking-wider">
                Historial de Reversiones Ejecutadas ({reversiones.length})
              </h3>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse text-xs">
                <thead>
                  <tr className="bg-gray-50 dark:bg-gray-800/60 text-gray-500 uppercase text-[10px] tracking-wider border-b border-gray-200 dark:border-gray-800">
                    <th className="py-2.5 px-4 font-semibold">ID Reversión</th>
                    <th className="py-2.5 px-4 font-semibold">Tipo</th>
                    <th className="py-2.5 px-4 font-semibold">Objeto Revertido</th>
                    <th className="py-2.5 px-4 font-semibold text-right">Monto Revertido</th>
                    <th className="py-2.5 px-4 font-semibold">Motivo</th>
                    <th className="py-2.5 px-4 font-semibold">Autorizado Por</th>
                    <th className="py-2.5 px-4 font-semibold">Fecha</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                  {reversiones.length === 0 ? (
                    <tr>
                      <td colSpan={7} className="py-8 text-center text-gray-400 italic">
                        No se han registrado reversiones en el laboratorio.
                      </td>
                    </tr>
                  ) : (
                    reversiones.map((rev) => (
                      <tr key={rev.reversal_id} className="hover:bg-gray-50/50 dark:hover:bg-gray-800/30">
                        <td className="py-3 px-4 font-mono font-bold text-gray-900 dark:text-gray-100">
                          {rev.reversal_id.substring(0, 8)}...
                        </td>
                        <td className="py-3 px-4">
                          <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300">
                            {rev.reversal_type}
                          </span>
                        </td>
                        <td className="py-3 px-4 font-mono text-gray-600 dark:text-gray-400">
                          {rev.original_movement_id || rev.transfer_id || rev.reservation_id}
                        </td>
                        <td className="py-3 px-4 text-right font-mono font-bold text-red-600 dark:text-red-400">
                          {rev.reversed_amount_formatted}
                        </td>
                        <td className="py-3 px-4 text-gray-600 dark:text-gray-400">{rev.reason}</td>
                        <td className="py-3 px-4 text-gray-500">{rev.authorized_by}</td>
                        <td className="py-3 px-4 text-gray-400">
                          {new Date(rev.occurred_at).toLocaleString("es-AR")}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>

          </div>
        </div>
      )}

      {/* TAB 8: AUDITORÍA CON HASH CHAIN (LAB-012) */}
      {tabActiva === "auditoria" && (
        <div className="space-y-6">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-bold text-gray-900 dark:text-gray-100">
                Pista de Auditoría Encadenada (Hash Chain)
              </h2>
              <p className="text-xs text-gray-500 dark:text-gray-400">
                Cadena inmutable con hash SHA-256 canónico desde génesis y triggers de solo inserción.
              </p>
            </div>
            <button
              onClick={handleVerificarAuditoria}
              data-testid="btn-verificar-auditoria"
              disabled={verificandoAuditoria}
              className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold rounded-lg shadow-sm transition disabled:opacity-50"
            >
              {verificandoAuditoria ? "Verificando..." : "🛡️ Verificar Cadena Criptográfica"}
            </button>
          </div>

          {verificacionAuditoria && (
            <div
              className={`p-4 rounded-xl border flex items-center justify-between ${
                verificacionAuditoria.valid
                  ? "bg-emerald-50 dark:bg-emerald-950/40 border-emerald-300 dark:border-emerald-700 text-emerald-800 dark:text-emerald-200"
                  : "bg-red-50 dark:bg-red-950/40 border-red-300 dark:border-red-700 text-red-800 dark:text-red-200"
              }`}
            >
              <div>
                <span className="font-bold block">
                  {verificacionAuditoria.valid
                    ? "✓ CADENA DE AUDITORÍA ÍNTEGRA Y CONSECUTIVA"
                    : "✗ VIOLACIÓN DE INTEGRIDAD CRIPTOGRÁFICA DETECTADA"}
                </span>
                <span className="text-xs opacity-90">
                  Total eventos verificados: {verificacionAuditoria.total_events} | Último hash:{" "}
                  {verificacionAuditoria.last_hash.substring(0, 16)}...
                </span>
              </div>
            </div>
          )}

          <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-200 dark:border-gray-800 shadow-sm overflow-hidden">
            <div className="p-4 border-b border-gray-200 dark:border-gray-800">
              <h3 className="text-xs font-bold text-gray-900 dark:text-gray-100 uppercase tracking-wider">
                Eventos Encadenados ({auditoriaEventos.length})
              </h3>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse text-xs">
                <thead>
                  <tr className="bg-gray-50 dark:bg-gray-800/60 text-gray-500 uppercase text-[10px] tracking-wider border-b border-gray-200 dark:border-gray-800">
                    <th className="py-2.5 px-4 font-semibold">Sec (#)</th>
                    <th className="py-2.5 px-4 font-semibold">Timestamp</th>
                    <th className="py-2.5 px-4 font-semibold">Tipo de Evento</th>
                    <th className="py-2.5 px-4 font-semibold">Entidad</th>
                    <th className="py-2.5 px-4 font-semibold">Operador</th>
                    <th className="py-2.5 px-4 font-semibold font-mono">Current Hash (SHA-256)</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 dark:divide-gray-800 font-mono text-[11px]">
                  {auditoriaEventos.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="py-8 text-center text-gray-400 italic font-sans">
                        No hay eventos en la cadena de auditoría aún.
                      </td>
                    </tr>
                  ) : (
                    auditoriaEventos.map((ev) => (
                      <tr key={ev.sequence_number} className="hover:bg-gray-50/50 dark:hover:bg-gray-800/30">
                        <td className="py-3 px-4 font-bold text-blue-600 dark:text-blue-400">
                          #{ev.sequence_number}
                        </td>
                        <td className="py-3 px-4 text-gray-500">
                          {new Date(ev.timestamp_utc).toLocaleTimeString("es-AR")}
                        </td>
                        <td className="py-3 px-4 font-sans font-semibold text-gray-900 dark:text-gray-100">
                          {ev.event_type}
                        </td>
                        <td className="py-3 px-4 text-gray-600 dark:text-gray-400">
                          {ev.aggregate_type}:{ev.aggregate_id.substring(0, 8)}
                        </td>
                        <td className="py-3 px-4 text-gray-500 font-sans">{ev.performed_by}</td>
                        <td className="py-3 px-4 text-gray-600 dark:text-gray-400">
                          {ev.current_hash.substring(0, 16)}...
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>

          </div>
        </div>
      )}

      {/* TAB 9: BANDEJA TRANSACCIONAL OUTBOX Y MONITOR COMERCIAL (LAB-012 & ETAPA 5) */}
      {tabActiva === "outbox" && (
        <div className="space-y-6" data-testid="commercial-bridge-monitor">
          {/* SECCIÓN 1: MONITOR DE INTEGRACIÓN COMERCIAL (ETAPA 5) */}
          <div className="bg-white dark:bg-gray-900 p-5 rounded-2xl border border-gray-200 dark:border-gray-800 shadow-sm space-y-5">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-gray-100 dark:border-gray-800">
              <div>
                <h2 className="text-lg font-bold text-gray-900 dark:text-gray-100 flex items-center gap-2">
                  <span>🌉</span> Bandeja Transaccional de Salida (Outbox Pattern) y Commercial Bridge
                </h2>
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  Etapa 5: Monitoreo en tiempo real del buffer Outbox, asientos automáticos y conciliación de eventos comerciales.
                </p>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={cargarMonitorComercial}
                  disabled={cargandoBridge}
                  className="px-3 py-1.5 bg-gray-100 hover:bg-gray-200 dark:bg-gray-800 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 text-xs font-semibold rounded-lg transition"
                  data-testid="btn-actualizar-monitor"
                >
                  {cargandoBridge ? "⏳ Actualizando..." : "🔄 Actualizar"}
                </button>
                <button
                  type="button"
                  onClick={handleForzarDrenajeComercial}
                  className="px-3.5 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold rounded-lg shadow-sm transition"
                  data-testid="btn-forzar-drenaje"
                >
                  ⚡ Sincronizar Ahora
                </button>
              </div>
            </div>

            {/* Mensaje de Estado / Notificación */}
            {commBridgeMsg && (
              <div className="p-3 bg-emerald-50 dark:bg-emerald-950/40 text-emerald-800 dark:text-emerald-300 rounded-lg text-xs border border-emerald-200 dark:border-emerald-800 flex justify-between items-center">
                <span>{commBridgeMsg}</span>
                <button onClick={() => setCommBridgeMsg(null)} className="text-emerald-600 font-bold ml-2">×</button>
              </div>
            )}

            {/* Tarjetas de Métricas del Puente Comercial */}
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
              <div className="bg-gray-50 dark:bg-gray-800/60 p-3 rounded-xl border border-gray-200 dark:border-gray-700/60">
                <span className="text-[10px] text-gray-500 dark:text-gray-400 uppercase tracking-wider block font-semibold">Total Eventos</span>
                <span className="text-xl font-bold text-gray-900 dark:text-gray-100" data-testid="metric-total">
                  {commBridgeStatus ? commBridgeStatus.total : 0}
                </span>
              </div>
              <div className="bg-amber-50 dark:bg-amber-950/20 p-3 rounded-xl border border-amber-200 dark:border-amber-900/40">
                <span className="text-[10px] text-amber-600 dark:text-amber-400 uppercase tracking-wider block font-semibold">Pendientes</span>
                <span className="text-xl font-bold text-amber-600 dark:text-amber-400" data-testid="metric-pending">
                  {commBridgeStatus ? commBridgeStatus.pending : 0}
                </span>
              </div>
              <div className="bg-blue-50 dark:bg-blue-950/20 p-3 rounded-xl border border-blue-200 dark:border-blue-900/40">
                <span className="text-[10px] text-blue-600 dark:text-blue-400 uppercase tracking-wider block font-semibold">En Proceso</span>
                <span className="text-xl font-bold text-blue-600 dark:text-blue-400" data-testid="metric-processing">
                  {commBridgeStatus ? commBridgeStatus.processing : 0}
                </span>
              </div>
              <div className="bg-emerald-50 dark:bg-emerald-950/20 p-3 rounded-xl border border-emerald-200 dark:border-emerald-900/40">
                <span className="text-[10px] text-emerald-600 dark:text-emerald-400 uppercase tracking-wider block font-semibold">Publicados</span>
                <span className="text-xl font-bold text-emerald-600 dark:text-emerald-400" data-testid="metric-published">
                  {commBridgeStatus ? commBridgeStatus.published : 0}
                </span>
              </div>
              <div className="bg-orange-50 dark:bg-orange-950/20 p-3 rounded-xl border border-orange-200 dark:border-orange-900/40">
                <span className="text-[10px] text-orange-600 dark:text-orange-400 uppercase tracking-wider block font-semibold">Reintentos / Fallos</span>
                <span className="text-xl font-bold text-orange-600 dark:text-orange-400" data-testid="metric-failed">
                  {commBridgeStatus ? commBridgeStatus.failed : 0}
                </span>
              </div>
              <div className="bg-red-50 dark:bg-red-950/20 p-3 rounded-xl border border-red-200 dark:border-red-900/40">
                <span className="text-[10px] text-red-600 dark:text-red-400 uppercase tracking-wider block font-semibold">Letra Muerta</span>
                <span className="text-xl font-bold text-red-600 dark:text-red-400" data-testid="metric-dead-letter">
                  {commBridgeStatus ? commBridgeStatus.dead_letter : 0}
                </span>
              </div>
            </div>

            {/* Barra de Filtros y Búsqueda */}
            <div className="flex flex-col sm:flex-row gap-3 pt-2">
              <div className="flex-1 flex gap-2">
                <input
                  type="text"
                  placeholder="Buscar por ticket, ID o clave idempotente..."
                  value={commSearch}
                  onChange={(e) => setCommSearch(e.target.value)}
                  className="flex-1 p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100 text-xs"
                  data-testid="input-search-events"
                />
              </div>
              <div className="flex gap-2">
                <select
                  value={commStatusFilter}
                  onChange={(e) => setCommStatusFilter(e.target.value)}
                  className="p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100 text-xs"
                  data-testid="select-status-filter"
                >
                  <option value="ALL" className="dark:bg-gray-900">Todos los Estados</option>
                  <option value="PENDING_DEFINITION" className="dark:bg-gray-900">⚠️ Pendientes de Definición</option>
                  <option value="PENDING" className="dark:bg-gray-900">Pendientes</option>
                  <option value="PROCESSING" className="dark:bg-gray-900">En Proceso</option>
                  <option value="PUBLISHED" className="dark:bg-gray-900">Publicados (OK)</option>
                  <option value="FAILED" className="dark:bg-gray-900">Fallidos</option>
                  <option value="DEAD_LETTER" className="dark:bg-gray-900">Letra Muerta (DEAD_LETTER)</option>
                </select>
                <select
                  value={commTypeFilter}
                  onChange={(e) => setCommTypeFilter(e.target.value)}
                  className="p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100 text-xs"
                  data-testid="select-type-filter"
                >
                  <option value="" className="dark:bg-gray-900">Todos los Tipos</option>
                  <option value="SALE" className="dark:bg-gray-900">Ventas</option>
                  <option value="SERVICE" className="dark:bg-gray-900">Servicios / Cargas</option>
                  <option value="EXPENSE" className="dark:bg-gray-900">Gastos</option>
                </select>
              </div>
            </div>

            {/* Tabla de Eventos Comerciales */}
            <div className="overflow-x-auto border border-gray-200 dark:border-gray-800 rounded-xl">
              <table className="w-full text-left text-xs" data-testid="table-commercial-events">
                <thead className="bg-gray-50 dark:bg-gray-800/80 text-gray-600 dark:text-gray-300 font-semibold border-b border-gray-200 dark:border-gray-800">
                  <tr>
                    <th className="py-2.5 px-3">Seq / ID</th>
                    <th className="py-2.5 px-3">Fecha</th>
                    <th className="py-2.5 px-3">Operación Comercial</th>
                    <th className="py-2.5 px-3">Idempotency Key</th>
                    <th className="py-2.5 px-3">Estado</th>
                    <th className="py-2.5 px-3">Movimiento Tesorería</th>
                    <th className="py-2.5 px-3">Error / Causa</th>
                    <th className="py-2.5 px-3 text-right">Acción</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 dark:divide-gray-800/60 font-mono">
                  {commEvents.length === 0 ? (
                    <tr>
                      <td colSpan={8} className="py-6 text-center text-gray-400 font-sans">
                        No hay eventos registrados que coincidan con los filtros aplicados.
                      </td>
                    </tr>
                  ) : (
                    commEvents.map((ev) => (
                      <tr key={ev.event_id} className="hover:bg-gray-50/50 dark:hover:bg-gray-800/30">
                        <td className="py-2 px-3 font-bold text-gray-700 dark:text-gray-300">
                          #{ev.sequence_id}
                          <span className="block text-[10px] text-gray-400 font-normal">{ev.event_id.substring(0, 8)}...</span>
                        </td>
                        <td className="py-2 px-3 text-gray-500 font-sans whitespace-nowrap">
                          {ev.created_at ? new Date(ev.created_at).toLocaleString("es-AR") : "—"}
                        </td>
                        <td className="py-2 px-3 font-sans">
                          <span className="font-semibold text-gray-800 dark:text-gray-200 block">{ev.event_type}</span>
                          <span className="text-[10px] text-gray-400">{ev.entity_type}: {ev.entity_id}</span>
                        </td>
                        <td className="py-2 px-3 text-gray-600 dark:text-gray-400 text-[11px] truncate max-w-[120px]" title={ev.idempotency_key}>
                          {ev.idempotency_key}
                        </td>
                        <td className="py-2 px-3">
                          <span
                            className={`px-2 py-0.5 rounded-full text-[10px] font-bold ${
                              ev.status === "PUBLISHED"
                                ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/60 dark:text-emerald-200"
                                : ev.status === "PENDING"
                                ? "bg-amber-100 text-amber-800 dark:bg-amber-900/60 dark:text-amber-200"
                                : ev.status === "PROCESSING"
                                ? "bg-blue-100 text-blue-800 dark:bg-blue-900/60 dark:text-blue-200"
                                : ev.status === "FAILED"
                                ? "bg-orange-100 text-orange-800 dark:bg-orange-900/60 dark:text-orange-200"
                                : "bg-red-100 text-red-800 dark:bg-red-900/60 dark:text-red-200"
                            }`}
                          >
                            {ev.status}
                          </span>
                        </td>
                        <td className="py-2 px-3 font-sans">
                          {ev.tesoreria_movement_id ? (
                            ev.tesoreria_movement_id.startsWith("PENDING_DEFINITION") ||
                            ev.tesoreria_movement_id.startsWith("UNSPECIFIED_PLATFORM") ? (
                              <span
                                className="px-2 py-0.5 rounded text-[10px] font-bold bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300 inline-block"
                                title={ev.tesoreria_movement_id}
                              >
                                ⚠️ PENDIENTE DEFINICIÓN
                              </span>
                            ) : (
                              <span className="text-[11px] font-semibold text-emerald-700 dark:text-emerald-400" title={ev.tesoreria_movement_id}>
                                ✓ {ev.tesoreria_movement_id.substring(0, 8)}...
                              </span>
                            )
                          ) : (
                            <span className="text-gray-400 text-[10px]">Sin asignar</span>
                          )}
                        </td>
                        <td className="py-2 px-3 text-red-600 dark:text-red-400 text-[11px] max-w-[150px] truncate" title={ev.error_message || ""}>
                          {ev.error_message || "—"}
                          {ev.reprocessed_by && (
                            <span className="block text-[10px] text-blue-500 font-sans">
                              Reprocesado por: {ev.reprocessed_by}
                            </span>
                          )}
                        </td>
                        <td className="py-2 px-3 text-right">
                          {(ev.status === "FAILED" || ev.status === "DEAD_LETTER") ? (
                            <button
                              type="button"
                              onClick={() => handleReprocesarEventoComercial(ev.event_id)}
                              className="px-2.5 py-1 bg-amber-600 hover:bg-amber-700 text-white rounded text-[11px] font-semibold transition"
                              data-testid={`btn-reprocesar-${ev.event_id}`}
                            >
                              Reprocesar
                            </button>
                          ) : (
                            <span className="text-gray-400 text-[11px]">—</span>
                          )}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>

            <div className="flex justify-between items-center text-[11px] text-gray-500 dark:text-gray-400 px-1 pt-2">
              <span>Mostrando {commEvents.length} de {commTotalEvents} eventos comerciales registrados</span>
              <span>Actualización en tiempo real vía worker de integración</span>
            </div>
          </div>

          {/* SECCIÓN 2: BANDEJA TRANSACCIONAL AUDITORÍA INTERNA (LAB-012) */}
          <div className="bg-white dark:bg-gray-900 p-5 rounded-2xl border border-gray-200 dark:border-gray-800 shadow-sm space-y-4">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div>
                <h3 className="text-sm font-bold text-gray-900 dark:text-gray-100">
                  Buffer de Auditoría Criptográfica (Internal Outbox)
                </h3>
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  Eventos de pista de auditoría inmutable en cola interna de persistencia local.
                </p>
              </div>
              <button
                onClick={handleProcesarOutbox}
                data-testid="btn-procesar-outbox"
                className="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold rounded-lg shadow-sm transition"
              >
                Procesar Buffer Auditoría
              </button>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <div className="bg-gray-50 dark:bg-gray-800/40 p-3 rounded-lg border border-gray-200 dark:border-gray-800">
                <span className="text-[10px] text-gray-400 uppercase block font-semibold">Total</span>
                <span className="text-lg font-bold text-gray-900 dark:text-gray-100">{outboxStatus ? outboxStatus.total : 0}</span>
              </div>
              <div className="bg-gray-50 dark:bg-gray-800/40 p-3 rounded-lg border border-gray-200 dark:border-gray-800">
                <span className="text-[10px] text-amber-500 uppercase block font-semibold">Pendientes</span>
                <span className="text-lg font-bold text-amber-600 dark:text-amber-400">{outboxStatus ? outboxStatus.pending : 0}</span>
              </div>
              <div className="bg-gray-50 dark:bg-gray-800/40 p-3 rounded-lg border border-gray-200 dark:border-gray-800">
                <span className="text-[10px] text-emerald-500 uppercase block font-semibold">Enviados</span>
                <span className="text-lg font-bold text-emerald-600 dark:text-emerald-400">{outboxStatus ? outboxStatus.sent : 0}</span>
              </div>
              <div className="bg-gray-50 dark:bg-gray-800/40 p-3 rounded-lg border border-gray-200 dark:border-gray-800">
                <span className="text-[10px] text-red-500 uppercase block font-semibold">Letra Muerta</span>
                <span className="text-lg font-bold text-red-600 dark:text-red-400">{outboxStatus ? outboxStatus.dead_letter : 0}</span>
              </div>
            </div>

            {outboxMsg && (
              <div className="p-2.5 bg-blue-50 dark:bg-blue-950/40 text-blue-800 dark:text-blue-300 rounded text-xs border border-blue-200 dark:border-blue-800">
                {outboxMsg}
              </div>
            )}

            <div className="flex gap-2 items-center pt-2">
              <input
                type="text"
                placeholder="ID de evento en letra muerta..."
                value={deadLetterId}
                onChange={(e) => setDeadLetterId(e.target.value)}
                className="p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100 text-xs flex-1"
                data-testid="input-reprocesar-deadletter"
              />
              <button
                type="button"
                onClick={handleReprocesarDeadLetter}
                className="px-3 py-2 bg-red-600 hover:bg-red-700 text-white rounded-lg text-xs font-semibold"
                data-testid="btn-reprocesar-deadletter"
              >
                Reprocesar Dead Letter
              </button>
            </div>
          </div>
        </div>
      )}

      {/* TAB: CUENTA CORRIENTE PREPAY / DAVID */}
      {tabActiva === "cuenta_corriente_david" && (
        <div className="space-y-6" data-testid="seccion-cuenta-corriente-david">
          {/* Banner Informativo Estado Inicial */}
          <div className="p-4 rounded-xl bg-blue-50 dark:bg-blue-950/40 border border-blue-200 dark:border-blue-800 text-blue-900 dark:text-blue-200 text-xs flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 shadow-sm">
            <div className="flex items-center gap-2.5">
              <span className="text-xl">🤝</span>
              <div>
                <p className="font-bold">Cuenta Corriente PREPAY / David — Monitoreo de Obligaciones</p>
                <p className="text-blue-700 dark:text-blue-300">
                  La cuenta inicia formalmente en $0,00. No se ha reconocido deuda histórica previa.
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              {cargandoCCDavid && (
                <span className="text-xs text-blue-500 font-medium animate-pulse">Sincronizando...</span>
              )}
              <span className="px-2.5 py-1 rounded-full text-[11px] font-semibold bg-emerald-100 text-emerald-800 dark:bg-emerald-900/60 dark:text-emerald-200 border border-emerald-300 dark:border-emerald-700">
                Línea Base: $0,00
              </span>
            </div>
          </div>

          {errorCCDavid && (
            <div className="p-3 bg-red-50 dark:bg-red-950/40 text-red-700 dark:text-red-300 text-xs rounded-xl border border-red-200 dark:border-red-800" data-testid="error-cc-david">
              {errorCCDavid}
            </div>
          )}

          {/* Grilla de 5 Indicadores Contables Obligatorios */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3.5">
            {/* KPI 1: Obligación Pendiente */}
            <div className="bg-white dark:bg-gray-900 p-4 rounded-2xl border-2 border-[#C97B5A] shadow-sm">
              <span className="text-[10px] font-bold uppercase tracking-wider text-[#C97B5A] block">
                Obligación Pendiente
              </span>
              <span
                className="text-2xl font-black text-gray-900 dark:text-gray-100 font-mono mt-1 block"
                data-testid="kpi-obligacion-pendiente"
              >
                {estadoCCDavid ? estadoCCDavid.obligacion_pendiente_formatted : "$0,00"}
              </span>
              <span className="text-[10px] text-gray-400 mt-0.5 block">
                Histórica + Cargas - Pagos
              </span>
            </div>

            {/* KPI 2: Total Cargas Acreditadas */}
            <div className="bg-white dark:bg-gray-900 p-4 rounded-2xl border border-gray-200 dark:border-gray-800 shadow-sm">
              <span className="text-[10px] font-bold uppercase tracking-wider text-blue-600 dark:text-blue-400 block">
                Total Acreditaciones
              </span>
              <span
                className="text-2xl font-black text-gray-900 dark:text-gray-100 font-mono mt-1 block"
                data-testid="kpi-total-acreditaciones"
              >
                {estadoCCDavid ? estadoCCDavid.total_acreditaciones_formatted : "$0,00"}
              </span>
              <span className="text-[10px] text-gray-400 mt-0.5 block">
                Saldo Digital a 1220
              </span>
            </div>

            {/* KPI 3: Total Pagos Efectivos */}
            <div className="bg-white dark:bg-gray-900 p-4 rounded-2xl border border-gray-200 dark:border-gray-800 shadow-sm">
              <span className="text-[10px] font-bold uppercase tracking-wider text-emerald-600 dark:text-emerald-400 block">
                Total Pagos Realizados
              </span>
              <span
                className="text-2xl font-black text-gray-900 dark:text-gray-100 font-mono mt-1 block"
                data-testid="kpi-total-pagos"
              >
                {estadoCCDavid ? estadoCCDavid.total_pagos_formatted : "$0,00"}
              </span>
              <span className="text-[10px] text-gray-400 mt-0.5 block">
                Salidas de Fondo 1130
              </span>
            </div>

            {/* KPI 4: Deuda Histórica Reconocida */}
            <div className="bg-white dark:bg-gray-900 p-4 rounded-2xl border border-gray-200 dark:border-gray-800 shadow-sm">
              <span className="text-[10px] font-bold uppercase tracking-wider text-purple-600 dark:text-purple-400 block">
                Deuda Histórica
              </span>
              <span
                className="text-2xl font-black text-gray-900 dark:text-gray-100 font-mono mt-1 block"
                data-testid="kpi-deuda-historica"
              >
                {estadoCCDavid ? estadoCCDavid.total_deuda_historica_formatted : "$0,00"}
              </span>
              <span className="text-[10px] text-gray-400 mt-0.5 block">
                Reconocida sin fondo
              </span>
            </div>

            {/* KPI 5: Saldo Disponible en 1130 */}
            <div className="bg-white dark:bg-gray-900 p-4 rounded-2xl border border-gray-200 dark:border-gray-800 shadow-sm">
              <span className="text-[10px] font-bold uppercase tracking-wider text-amber-600 dark:text-amber-400 block">
                Disponible Fondo 1130
              </span>
              <span
                className="text-2xl font-black text-gray-900 dark:text-gray-100 font-mono mt-1 block"
                data-testid="kpi-disponible-1130"
              >
                {estadoCCDavid ? estadoCCDavid.saldo_disponible_1130_formatted : "$0,00"}
              </span>
              <span className="text-[10px] text-gray-400 mt-0.5 block">
                Efectivo Prepay Acumulado
              </span>
            </div>
          </div>

          {/* Barra de Acciones y Filtros */}
          <div className="bg-white dark:bg-gray-900 p-4 rounded-2xl border border-gray-200 dark:border-gray-800 shadow-sm flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => setModalCCDavid("ACREDITACION")}
                data-testid="btn-abrir-acreditacion"
                className="px-3.5 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-xs font-bold shadow-sm transition flex items-center gap-1.5"
              >
                <span>➕</span> Registrar Acreditación de Saldo (1220)
              </button>

              <button
                type="button"
                onClick={() => setModalCCDavid("PAGO")}
                data-testid="btn-abrir-pago"
                className="px-3.5 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg text-xs font-bold shadow-sm transition flex items-center gap-1.5"
              >
                <span>💵</span> Registrar Pago a David (1130)
              </button>

              <button
                type="button"
                onClick={() => setModalCCDavid("HISTORICO")}
                data-testid="btn-abrir-historico"
                className="px-3.5 py-2 bg-purple-600 hover:bg-purple-700 text-white rounded-lg text-xs font-bold shadow-sm transition flex items-center gap-1.5"
              >
                <span>📜</span> Reconocer Deuda Histórica Futura
              </button>
            </div>

            {/* Filtro Fecha de Corte */}
            <div className="flex items-center gap-2">
              <label htmlFor="input-fecha-corte-david" className="text-xs text-gray-500 whitespace-nowrap">
                Fecha de Corte:
              </label>
              <input
                id="input-fecha-corte-david"
                type="date"
                value={filtroFechaCorteDavid}
                onChange={(e) => setFiltroFechaCorteDavid(e.target.value)}
                className="p-1.5 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-xs text-gray-900 dark:text-gray-100"
                data-testid="input-fecha-corte-david"
              />
              {filtroFechaCorteDavid && (
                <button
                  type="button"
                  onClick={() => setFiltroFechaCorteDavid("")}
                  className="text-xs text-gray-400 hover:text-gray-600 px-1"
                  title="Limpiar fecha de corte"
                >
                  ✕
                </button>
              )}
            </div>
          </div>

          {/* Tabla de Historial Cronológico de Movimientos */}
          <div className="bg-white dark:bg-gray-900 p-5 rounded-2xl border border-gray-200 dark:border-gray-800 shadow-sm space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-bold text-gray-900 dark:text-gray-100">
                Historial Cronológico de Cuenta Corriente ({estadoCCDavid ? estadoCCDavid.cantidad_movimientos : 0})
              </h3>
              <span className="text-[11px] text-gray-400">
                Inmutable · Append-only · Derivado del Libro Mayor
              </span>
            </div>

            <div className="overflow-x-auto scrollbar-thin">
              <table className="w-full text-left text-xs border-collapse min-w-[850px]" data-testid="tabla-cc-david">
                <thead>
                  <tr className="border-b border-gray-200 dark:border-gray-800 text-gray-400 uppercase text-[10px] tracking-wider font-semibold">
                    <th className="py-2.5 px-3">Fecha</th>
                    <th className="py-2.5 px-3">Tipo de Movimiento</th>
                    <th className="py-2.5 px-3">Importe</th>
                    <th className="py-2.5 px-3">Referencia / Comprobante</th>
                    <th className="py-2.5 px-3">Fondo Afectado</th>
                    <th className="py-2.5 px-3">Responsable</th>
                    <th className="py-2.5 px-3">Movement ID / Op ID</th>
                    <th className="py-2.5 px-3 text-right">Saldo Obligación Resultante</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                  {(!estadoCCDavid || estadoCCDavid.movimientos.length === 0) ? (
                    <tr>
                      <td colSpan={8} className="py-8 text-center text-gray-400 text-xs">
                        No hay movimientos registrados en la cuenta corriente. La obligación actual es de $0,00.
                      </td>
                    </tr>
                  ) : (
                    estadoCCDavid.movimientos.map((m) => (
                      <tr key={m.id} className="hover:bg-gray-50/50 dark:hover:bg-gray-800/30 transition">
                        <td className="py-2.5 px-3 whitespace-nowrap font-mono text-[11px]">
                          {m.fecha_referencia}
                        </td>
                        <td className="py-2.5 px-3 whitespace-nowrap">
                          {m.tipo_movimiento === "ACREDITACION_SALDO" && (
                            <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300">
                              ➕ ACREDITACIÓN EFECTIVA
                            </span>
                          )}
                          {m.tipo_movimiento === "PAGO_EFECTIVO" && (
                            <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300">
                              💵 PAGO EFECTIVO
                            </span>
                          )}
                          {m.tipo_movimiento === "RECONOCIMIENTO_DEUDA_HISTORICA" && (
                            <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-purple-100 text-purple-800 dark:bg-purple-950 dark:text-purple-300">
                              📜 DEUDA HISTÓRICA
                            </span>
                          )}
                        </td>
                        <td className="py-2.5 px-3 font-mono font-bold text-gray-900 dark:text-gray-100 whitespace-nowrap">
                          {m.monto_formatted}
                        </td>
                        <td className="py-2.5 px-3 text-gray-600 dark:text-gray-300 max-w-[200px] truncate" title={m.referencia_externa || m.motivo}>
                          <span className="font-semibold block">{m.referencia_externa || "—"}</span>
                          <span className="text-[10px] text-gray-400 block truncate">{m.motivo}</span>
                        </td>
                        <td className="py-2.5 px-3 whitespace-nowrap font-mono">
                          {m.fondo_afectado ? (
                            <span className="px-1.5 py-0.5 rounded text-[10px] bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300">
                              {m.fondo_afectado === "1220" ? "1220 (Prepay Digital)" : "1130 (Prepay Físico)"}
                            </span>
                          ) : (
                            <span className="text-gray-400 text-[10px]">Sin impacto en fondos</span>
                          )}
                        </td>
                        <td className="py-2.5 px-3 whitespace-nowrap text-gray-500 font-sans">
                          {m.actor}
                        </td>
                        <td className="py-2.5 px-3 font-mono text-[10px] text-gray-400 max-w-[130px] truncate" title={m.movement_id || m.operation_id}>
                          {m.movement_id ? `mov: ${m.movement_id.substring(0, 8)}...` : m.operation_id}
                        </td>
                        <td className="py-2.5 px-3 text-right font-mono font-extrabold text-gray-900 dark:text-gray-100 whitespace-nowrap">
                          {m.saldo_obligacion_resultante_formatted}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* TAB: ANTICIPOS Y REPOSICIÓN DE PREVENTISTAS */}
      {tabActiva === "anticipos_preventistas" && (
        <div className="space-y-6" data-testid="seccion-anticipos-preventistas">
          {/* Banner Informativo Reglas Económicas */}
          <div className="p-4 rounded-xl bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-800 text-amber-900 dark:text-amber-200 text-xs flex flex-col md:flex-row items-start md:items-center justify-between gap-3 shadow-sm">
            <div className="flex items-center gap-2.5">
              <span className="text-xl">🚚</span>
              <div>
                <p className="font-bold text-sm">Circuito de Anticipos a Preventistas y Reposición en Cascada</p>
                <p className="text-[11px] text-amber-700 dark:text-amber-300">
                  Uso temporal del fondo físico 1130 (Prepay Efectivo) para pagar entregas. La reposición hacia 1130 se debita en estricto orden de prelación: 1° 1120 (Tesoro) y 2° 1110 (Mostrador). No genera gasto duplicado ni deuda personal.
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setModalAnticipo("NUEVO")}
                data-testid="btn-abrir-nuevo-anticipo"
                className="px-3 py-1.5 bg-amber-600 hover:bg-amber-700 text-white font-bold rounded-lg text-xs transition shadow-sm whitespace-nowrap"
              >
                + Nuevo Anticipo (Salida 1130)
              </button>
            </div>
          </div>

          {/* Tarjetas KPI de Estado */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <div className="bg-white dark:bg-gray-900 p-4 rounded-xl border border-gray-200 dark:border-gray-800 shadow-sm">
              <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">
                Total Anticipado
              </p>
              <p className="text-xl font-bold text-gray-900 dark:text-gray-100 font-mono mt-1" data-testid="kpi-total-anticipado">
                {resumenPreventistas?.total_anticipado_formatted || "$0,00"}
              </p>
              <span className="text-[11px] text-gray-400 mt-1 block">
                {resumenPreventistas?.cantidad_anticipos ?? 0} expedientes emitidos
              </span>
            </div>

            <div className="bg-white dark:bg-gray-900 p-4 rounded-xl border border-gray-200 dark:border-gray-800 shadow-sm">
              <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">
                Total Repuesto a 1130
              </p>
              <p className="text-xl font-bold text-emerald-600 dark:text-emerald-400 font-mono mt-1" data-testid="kpi-total-repuesto">
                {resumenPreventistas?.total_repuesto_formatted || "$0,00"}
              </p>
              <span className="text-[11px] text-gray-400 mt-1 block">
                {resumenPreventistas?.cantidad_reposiciones ?? 0} reposiciones registradas
              </span>
            </div>

            <div className="bg-white dark:bg-gray-900 p-4 rounded-xl border border-gray-200 dark:border-gray-800 shadow-sm">
              <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">
                Pendiente de Reposición
              </p>
              <p className="text-xl font-bold text-amber-600 dark:text-amber-400 font-mono mt-1" data-testid="kpi-total-pendiente">
                {resumenPreventistas?.total_pendiente_reposicion_formatted || "$0,00"}
              </p>
              <span className="text-[11px] text-gray-400 mt-1 block">
                {anticiposPreventistas.filter((a) => a.estado !== "TOTALMENTE_REPUESTO").length} con saldo a reponer
              </span>
            </div>

            <div className="bg-white dark:bg-gray-900 p-4 rounded-xl border border-gray-200 dark:border-gray-800 shadow-sm">
              <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">
                Fondos de la Operatoria
              </p>
              <div className="text-xs font-mono mt-1 space-y-0.5">
                <div className="flex justify-between">
                  <span className="text-gray-500">1130 Prepay:</span>
                  <span className="font-bold text-gray-900 dark:text-gray-100">{formatMoney(saldo1130Cents)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-500">1120 Tesoro:</span>
                  <span className="font-bold text-blue-600 dark:text-blue-400">{formatMoney(saldo1120Cents)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-500">1110 Mostrador:</span>
                  <span className="font-bold text-purple-600 dark:text-purple-400">{formatMoney(saldo1110Cents)}</span>
                </div>
              </div>
            </div>
          </div>

          {/* Filtros y Controles */}
          <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 bg-white dark:bg-gray-900 p-4 rounded-xl border border-gray-200 dark:border-gray-800 shadow-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-gray-500 font-medium">Estado:</span>
              {(["ALL", "PENDIENTE_REPOSICION", "PARCIALMENTE_REPUESTO", "TOTALMENTE_REPUESTO"] as const).map((est) => (
                <button
                  key={est}
                  type="button"
                  onClick={() => setFiltroEstadoAnticipo(est)}
                  className={`px-2.5 py-1 text-xs rounded-lg font-medium transition ${
                    filtroEstadoAnticipo === est
                      ? "bg-amber-600 text-white"
                      : "bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 hover:bg-gray-200"
                  }`}
                >
                  {est === "ALL" ? "Todos" : est === "TOTALMENTE_REPUESTO" ? "Completados" : est === "PARCIALMENTE_REPUESTO" ? "Parciales" : "Pendientes"}
                </button>
              ))}
            </div>

            <div className="flex items-center gap-2">
              <input
                type="text"
                placeholder="Buscar por preventista..."
                value={filtroProveedorAnticipo}
                onChange={(e) => setFiltroProveedorAnticipo(e.target.value)}
                className="px-3 py-1.5 text-xs rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-1 focus:ring-amber-500"
              />
              <button
                type="button"
                onClick={() => cargarAnticipos(filtroEstadoAnticipo, filtroProveedorAnticipo)}
                className="p-1.5 text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition"
                title="Refrescar"
              >
                🔄
              </button>
            </div>
          </div>

          {/* Mensaje de Error si lo hubiera */}
          {errorAnticipo && (
            <div className="p-3 rounded-lg bg-red-50 dark:bg-red-950/40 border border-red-200 dark:border-red-800 text-red-800 dark:text-red-300 text-xs">
              ⚠️ {errorAnticipo}
            </div>
          )}

          {/* Tabla de Anticipos */}
          <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-200 dark:border-gray-800 overflow-hidden shadow-sm">
            <div className="p-4 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
              <h3 className="font-bold text-sm text-gray-900 dark:text-gray-100">
                Expedientes de Anticipos a Preventistas
              </h3>
              <span className="text-xs text-gray-400">
                {anticiposPreventistas.length} registro(s)
              </span>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-xs text-left" data-testid="tabla-anticipos-preventistas">
                <thead className="bg-gray-50 dark:bg-gray-800/50 text-gray-500 dark:text-gray-400 font-medium">
                  <tr>
                    <th className="py-2.5 px-3">Fecha</th>
                    <th className="py-2.5 px-3">Expediente ID</th>
                    <th className="py-2.5 px-3">Preventista / Proveedor</th>
                    <th className="py-2.5 px-3">Pedido / Remito</th>
                    <th className="py-2.5 px-3 text-right">Importe Original</th>
                    <th className="py-2.5 px-3 text-right">Repuesto</th>
                    <th className="py-2.5 px-3 text-right">Pendiente</th>
                    <th className="py-2.5 px-3 text-center">Estado</th>
                    <th className="py-2.5 px-3 text-right">Acciones</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200 dark:divide-gray-800 font-mono">
                  {cargandoAnticipos ? (
                    <tr>
                      <td colSpan={9} className="py-8 text-center text-gray-400 font-sans">
                        Cargando expedientes...
                      </td>
                    </tr>
                  ) : anticiposPreventistas.length === 0 ? (
                    <tr>
                      <td colSpan={9} className="py-8 text-center text-gray-400 font-sans">
                        No hay anticipos registrados para el criterio seleccionado.
                      </td>
                    </tr>
                  ) : (
                    anticiposPreventistas.map((ant) => (
                      <tr key={ant.id} className="hover:bg-gray-50/50 dark:hover:bg-gray-800/30 transition">
                        <td className="py-2.5 px-3 whitespace-nowrap text-gray-600 dark:text-gray-400 text-[11px]">
                          {new Date(ant.creado_en_utc).toLocaleString("es-AR", {
                            day: "2-digit",
                            month: "2-digit",
                            year: "2-digit",
                            hour: "2-digit",
                            minute: "2-digit",
                          })}
                        </td>
                        <td className="py-2.5 px-3 text-gray-500 text-[11px] max-w-[110px] truncate" title={ant.id}>
                          {ant.id.substring(0, 8)}...
                        </td>
                        <td className="py-2.5 px-3 font-sans font-semibold text-gray-900 dark:text-gray-100 whitespace-nowrap">
                          {ant.proveedor}
                        </td>
                        <td className="py-2.5 px-3 text-gray-500 font-sans text-[11px] max-w-[130px] truncate" title={ant.referencia_documento || (ant.pedido_id ? `Pedido #${ant.pedido_id}` : "—")}>
                          {ant.pedido_id ? `Pedido #${ant.pedido_id}` : ant.referencia_documento || "—"}
                        </td>
                        <td className="py-2.5 px-3 text-right font-bold text-gray-900 dark:text-gray-100 whitespace-nowrap">
                          {ant.monto_original_formatted}
                        </td>
                        <td className="py-2.5 px-3 text-right font-bold text-emerald-600 dark:text-emerald-400 whitespace-nowrap">
                          {ant.monto_repuesto_formatted}
                        </td>
                        <td className="py-2.5 px-3 text-right font-extrabold text-amber-600 dark:text-amber-400 whitespace-nowrap">
                          {ant.monto_pendiente_formatted}
                        </td>
                        <td className="py-2.5 px-3 text-center whitespace-nowrap">
                          <span
                            className={`px-2 py-0.5 rounded-full text-[10px] font-bold tracking-wider ${
                              ant.estado === "TOTALMENTE_REPUESTO"
                                ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300"
                                : ant.estado === "PARCIALMENTE_REPUESTO"
                                ? "bg-blue-100 text-blue-800 dark:bg-blue-950/60 dark:text-blue-300"
                                : "bg-amber-100 text-amber-800 dark:bg-amber-950/60 dark:text-amber-300"
                            }`}
                          >
                            {ant.estado === "TOTALMENTE_REPUESTO" ? "COMPLETO" : ant.estado === "PARCIALMENTE_REPUESTO" ? "PARCIAL" : "PENDIENTE"}
                          </span>
                        </td>
                        <td className="py-2.5 px-3 text-right font-sans whitespace-nowrap space-x-1.5">
                          <button
                            type="button"
                            onClick={() => {
                              setAnticipoSeleccionado(ant);
                              setFormRepMonto((ant.monto_pendiente_cents / 100).toFixed(2));
                              setModalAnticipo("REPONER");
                            }}
                            disabled={ant.monto_pendiente_cents <= 0}
                            className={`px-2.5 py-1 text-xs font-semibold rounded transition ${
                              ant.monto_pendiente_cents > 0
                                ? "bg-emerald-600 hover:bg-emerald-700 text-white shadow-sm"
                                : "bg-gray-100 dark:bg-gray-800 text-gray-400 cursor-not-allowed"
                            }`}
                            data-testid={`btn-reponer-${ant.id}`}
                          >
                            Reponer
                          </button>
                          <button
                            type="button"
                            onClick={() => handleVerHistorialAnticipo(ant)}
                            className="px-2.5 py-1 text-xs font-medium rounded bg-gray-100 hover:bg-gray-200 dark:bg-gray-800 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 transition"
                            data-testid={`btn-historial-${ant.id}`}
                          >
                            Historial
                          </button>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* TAB: SALDOS INICIALES / APERTURA DE TESORERÍA (MEGA SYSTEM PROMPT DIRECTIVAS 2 Y 3) */}
      {tabActiva === "aperturas" && (
        <div className="space-y-6" data-testid="seccion-aperturas-tesoreria">
          {/* Banner Informativo Directiva de Apertura */}
          <div className="p-4 rounded-xl bg-blue-50 dark:bg-blue-950/40 border border-blue-200 dark:border-blue-800 text-blue-900 dark:text-blue-200 text-xs flex flex-col md:flex-row items-start md:items-center justify-between gap-3 shadow-sm">
            <div className="flex items-center gap-2.5">
              <span className="text-xl">⚙️</span>
              <div>
                <p className="font-bold text-sm">Configuración de Saldos Iniciales / Apertura Oficial en $0</p>
                <p className="text-[11px] text-blue-700 dark:text-blue-300">
                  Todos los saldos abren estrictamente en $0 (fondos físicos, digitales y obligaciones). Toda modificación de apertura genera auditoría append-only inmutable con motivo obligatorio. Si el fondo ya posee movimientos, la alteración silenciosa queda bloqueada.
                </p>
                {errorAperturas && <p className="text-red-600 font-bold mt-1">{errorAperturas}</p>}
              </div>
            </div>
            <button
              type="button"
              onClick={cargarSaldosApertura}
              disabled={cargandoAperturas}
              className="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-xs font-semibold shadow-sm transition whitespace-nowrap"
            >
              {cargandoAperturas ? "Actualizando..." : "🔄 Refrescar Saldos"}
            </button>
          </div>

          {/* Tarjetas Resumen de Apertura */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div className="bg-white dark:bg-gray-900 p-4 rounded-xl border border-gray-200 dark:border-gray-800 shadow-sm">
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Apertura Fondos Físicos</p>
              <p className="text-xl font-bold font-mono text-gray-900 dark:text-gray-100 mt-1" data-testid="total-apertura-fisicos">
                {formatMoney(saldosApertura?.total_fondos_fisicos_cents ?? 0)}
              </p>
              <p className="text-[10px] text-gray-400 mt-1">Cajas 1110, 1120, 1130, 1140, 1150, 1160</p>
            </div>
            <div className="bg-white dark:bg-gray-900 p-4 rounded-xl border border-gray-200 dark:border-gray-800 shadow-sm">
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Apertura Cuentas Digitales</p>
              <p className="text-xl font-bold font-mono text-gray-900 dark:text-gray-100 mt-1" data-testid="total-apertura-digitales">
                {formatMoney(saldosApertura?.total_cuentas_digitales_cents ?? 0)}
              </p>
              <p className="text-[10px] text-gray-400 mt-1">Cuentas 1210, 1220, 1230, 1310</p>
            </div>
            <div className="bg-white dark:bg-gray-900 p-4 rounded-xl border border-gray-200 dark:border-gray-800 shadow-sm">
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Obligaciones Iniciales</p>
              <p className="text-xl font-bold font-mono text-amber-600 dark:text-amber-400 mt-1" data-testid="total-apertura-obligaciones">
                {formatMoney(saldosApertura?.total_obligaciones_cents ?? 0)}
              </p>
              <p className="text-[10px] text-gray-400 mt-1">David, Claro/Ezequiel, Preventistas</p>
            </div>
          </div>

          {/* GRUPO 1: FONDOS FÍSICOS */}
          <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-200 dark:border-gray-800 p-5 shadow-sm space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-bold text-gray-900 dark:text-gray-100 flex items-center gap-2">
                <span>💵</span> 2.1. Fondos Físicos de Tesorería
              </h3>
              <span className="text-xs text-gray-400 font-mono">Total: {formatMoney(saldosApertura?.total_fondos_fisicos_cents ?? 0)}</span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs text-left" data-testid="tabla-apertura-fisicos">
                <thead className="text-[11px] text-gray-500 bg-gray-50 dark:bg-gray-800/50 uppercase border-y border-gray-100 dark:border-gray-800">
                  <tr>
                    <th className="py-2 px-3">Código</th>
                    <th className="py-2 px-3">Fondo Físico</th>
                    <th className="py-2 px-3 text-right">Saldo Inicial Apertura</th>
                    <th className="py-2 px-3 text-right">Saldo Actual Libros</th>
                    <th className="py-2 px-3 text-center">Estado Configuración</th>
                    <th className="py-2 px-3 text-right">Acción</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                  {saldosApertura?.fondos_fisicos.map((f) => (
                    <tr key={f.codigo} className="hover:bg-gray-50/50 dark:hover:bg-gray-800/30 transition">
                      <td className="py-2.5 px-3 font-mono font-bold text-gray-900 dark:text-gray-100">{f.codigo}</td>
                      <td className="py-2.5 px-3 font-semibold text-gray-800 dark:text-gray-200">{f.nombre}</td>
                      <td className="py-2.5 px-3 text-right font-mono font-bold text-gray-900 dark:text-gray-100">
                        {formatMoney(f.saldo_inicial_cents)}
                      </td>
                      <td className="py-2.5 px-3 text-right font-mono text-gray-500">
                        {formatMoney(f.saldo_actual_cents)}
                      </td>
                      <td className="py-2.5 px-3 text-center">
                        {f.estado_configuracion === "CONFIGURADO" ? (
                          <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-100 text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300">
                            CONFIGURADO {f.configurado_por ? `(${f.configurado_por})` : ""}
                          </span>
                        ) : (
                          <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300">
                            $0 PREDETERMINADO
                          </span>
                        )}
                      </td>
                      <td className="py-2.5 px-3 text-right">
                        <button
                          type="button"
                          onClick={() => {
                            setModalEditarApertura(f);
                            setNuevoSaldoAperturaInput((f.saldo_inicial_cents / 100).toFixed(2));
                            setMotivoAperturaInput("");
                            setReferenciaAperturaInput("");
                            setConfirmandoApertura(false);
                          }}
                          data-testid={`btn-configurar-apertura-${f.codigo}`}
                          className="px-2.5 py-1 text-xs font-semibold rounded bg-[#C97B5A] hover:bg-[#b06748] text-white shadow-sm transition"
                        >
                          Configurar
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* GRUPO 2: CUENTAS DIGITALES */}
          <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-200 dark:border-gray-800 p-5 shadow-sm space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-bold text-gray-900 dark:text-gray-100 flex items-center gap-2">
                <span>💳</span> 2.2. Cuentas Digitales y Tránsito
              </h3>
              <span className="text-xs text-gray-400 font-mono">Total: {formatMoney(saldosApertura?.total_cuentas_digitales_cents ?? 0)}</span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs text-left" data-testid="tabla-apertura-digitales">
                <thead className="text-[11px] text-gray-500 bg-gray-50 dark:bg-gray-800/50 uppercase border-y border-gray-100 dark:border-gray-800">
                  <tr>
                    <th className="py-2 px-3">Código</th>
                    <th className="py-2 px-3">Cuenta Digital</th>
                    <th className="py-2 px-3 text-right">Saldo Inicial Apertura</th>
                    <th className="py-2 px-3 text-right">Saldo Actual Libros</th>
                    <th className="py-2 px-3 text-center">Estado Configuración</th>
                    <th className="py-2 px-3 text-right">Acción</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                  {saldosApertura?.cuentas_digitales.map((c) => (
                    <tr key={c.codigo} className="hover:bg-gray-50/50 dark:hover:bg-gray-800/30 transition">
                      <td className="py-2.5 px-3 font-mono font-bold text-gray-900 dark:text-gray-100">{c.codigo}</td>
                      <td className="py-2.5 px-3 font-semibold text-gray-800 dark:text-gray-200">{c.nombre}</td>
                      <td className="py-2.5 px-3 text-right font-mono font-bold text-gray-900 dark:text-gray-100">
                        {formatMoney(c.saldo_inicial_cents)}
                      </td>
                      <td className="py-2.5 px-3 text-right font-mono text-gray-500">
                        {formatMoney(c.saldo_actual_cents)}
                      </td>
                      <td className="py-2.5 px-3 text-center">
                        {c.estado_configuracion === "CONFIGURADO" ? (
                          <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-100 text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300">
                            CONFIGURADO {c.configurado_por ? `(${c.configurado_por})` : ""}
                          </span>
                        ) : (
                          <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300">
                            $0 PREDETERMINADO
                          </span>
                        )}
                      </td>
                      <td className="py-2.5 px-3 text-right">
                        <button
                          type="button"
                          onClick={() => {
                            setModalEditarApertura(c);
                            setNuevoSaldoAperturaInput((c.saldo_inicial_cents / 100).toFixed(2));
                            setMotivoAperturaInput("");
                            setReferenciaAperturaInput("");
                            setConfirmandoApertura(false);
                          }}
                          data-testid={`btn-configurar-apertura-${c.codigo}`}
                          className="px-2.5 py-1 text-xs font-semibold rounded bg-[#C97B5A] hover:bg-[#b06748] text-white shadow-sm transition"
                        >
                          Configurar
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* GRUPO 3: OBLIGACIONES Y CUENTAS CORRIENTES */}
          <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-200 dark:border-gray-800 p-5 shadow-sm space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-bold text-gray-900 dark:text-gray-100 flex items-center gap-2">
                <span>📋</span> 2.3. Obligaciones y Cuentas Corrientes
              </h3>
              <span className="text-xs text-amber-500 font-mono font-bold">Total: {formatMoney(saldosApertura?.total_obligaciones_cents ?? 0)}</span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs text-left" data-testid="tabla-apertura-obligaciones">
                <thead className="text-[11px] text-gray-500 bg-gray-50 dark:bg-gray-800/50 uppercase border-y border-gray-100 dark:border-gray-800">
                  <tr>
                    <th className="py-2 px-3">Código</th>
                    <th className="py-2 px-3">Obligación / Contraparte</th>
                    <th className="py-2 px-3 text-right">Deuda Inicial Apertura</th>
                    <th className="py-2 px-3 text-center">Estado Configuración</th>
                    <th className="py-2 px-3 text-right">Acción</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                  {saldosApertura?.obligaciones.map((ob) => (
                    <tr key={ob.codigo} className="hover:bg-gray-50/50 dark:hover:bg-gray-800/30 transition">
                      <td className="py-2.5 px-3 font-mono font-bold text-gray-900 dark:text-gray-100">{ob.codigo}</td>
                      <td className="py-2.5 px-3 font-semibold text-gray-800 dark:text-gray-200">{ob.nombre}</td>
                      <td className="py-2.5 px-3 text-right font-mono font-bold text-amber-600 dark:text-amber-400">
                        {formatMoney(ob.saldo_inicial_cents)}
                      </td>
                      <td className="py-2.5 px-3 text-center">
                        {ob.estado_configuracion === "CONFIGURADO" ? (
                          <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-100 text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300">
                            CONFIGURADO {ob.configurado_por ? `(${ob.configurado_por})` : ""}
                          </span>
                        ) : (
                          <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300">
                            $0 PREDETERMINADO
                          </span>
                        )}
                      </td>
                      <td className="py-2.5 px-3 text-right">
                        <button
                          type="button"
                          onClick={() => {
                            setModalEditarApertura(ob);
                            setNuevoSaldoAperturaInput((ob.saldo_inicial_cents / 100).toFixed(2));
                            setMotivoAperturaInput("");
                            setReferenciaAperturaInput("");
                            setConfirmandoApertura(false);
                          }}
                          data-testid={`btn-configurar-apertura-${ob.codigo}`}
                          className="px-2.5 py-1 text-xs font-semibold rounded bg-[#C97B5A] hover:bg-[#b06748] text-white shadow-sm transition"
                        >
                          Configurar
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* HISTORIAL DE MODIFICACIONES (PISTA DE AUDITORÍA APPEND-ONLY) */}
          <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-200 dark:border-gray-800 p-5 shadow-sm space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-bold text-gray-900 dark:text-gray-100 flex items-center gap-2">
                <span>📜</span> 3.4. Pista de Auditoría de Aperturas y Saldos Iniciales (Append-Only)
              </h3>
              <span className="text-xs text-gray-400 font-mono">{historialAperturas.length} registros</span>
            </div>
            <div className="overflow-x-auto max-h-96">
              <table className="w-full text-xs text-left" data-testid="tabla-historial-aperturas">
                <thead className="text-[11px] text-gray-500 bg-gray-50 dark:bg-gray-800/50 uppercase border-y border-gray-100 dark:border-gray-800 sticky top-0">
                  <tr>
                    <th className="py-2 px-3">Fecha y Hora</th>
                    <th className="py-2 px-3">Ítem</th>
                    <th className="py-2 px-3">Categoría</th>
                    <th className="py-2 px-3 text-right">Saldo Anterior</th>
                    <th className="py-2 px-3 text-right">Saldo Nuevo</th>
                    <th className="py-2 px-3">Responsable</th>
                    <th className="py-2 px-3">Motivo Obligatorio</th>
                    <th className="py-2 px-3">Referencia</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                  {historialAperturas.map((h) => (
                    <tr key={h.id} className="hover:bg-gray-50/50 dark:hover:bg-gray-800/30 transition text-[11px]">
                      <td className="py-2 px-3 font-mono text-gray-500 whitespace-nowrap">
                        {new Date(h.fecha_hora).toLocaleString("es-AR", {
                          day: "2-digit",
                          month: "2-digit",
                          year: "numeric",
                          hour: "2-digit",
                          minute: "2-digit",
                          second: "2-digit",
                        })}
                      </td>
                      <td className="py-2 px-3 font-semibold text-gray-900 dark:text-gray-100 whitespace-nowrap">
                        <span className="font-mono text-gray-500 mr-1.5">{h.item_codigo}</span>
                        {h.item_nombre}
                      </td>
                      <td className="py-2 px-3 whitespace-nowrap">
                        <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400">
                          {h.categoria}
                        </span>
                      </td>
                      <td className="py-2 px-3 text-right font-mono text-gray-500 whitespace-nowrap">
                        {formatMoney(h.saldo_anterior_cents)}
                      </td>
                      <td className="py-2 px-3 text-right font-mono font-bold text-gray-900 dark:text-gray-100 whitespace-nowrap">
                        {formatMoney(h.saldo_nuevo_cents)}
                      </td>
                      <td className="py-2 px-3 font-semibold text-gray-700 dark:text-gray-300 whitespace-nowrap">
                        {h.usuario_responsable}
                      </td>
                      <td className="py-2 px-3 text-gray-600 dark:text-gray-400 max-w-xs truncate" title={h.motivo}>
                        {h.motivo}
                      </td>
                      <td className="py-2 px-3 text-gray-400 font-mono text-[10px] whitespace-nowrap">
                        {h.referencia_autorizacion || "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* MODAL: CONFIGURAR SALDO INICIAL CON CONFIRMACIÓN OBLIGATORIA */}
      {modalEditarApertura && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm" data-testid="modal-configurar-apertura">
          <div className="bg-white dark:bg-gray-900 rounded-2xl max-w-md w-full p-6 shadow-2xl border border-gray-200 dark:border-gray-800 space-y-4">
            <div>
              <h3 className="text-lg font-bold text-gray-900 dark:text-gray-100 flex items-center gap-2">
                <span>⚙️</span> Configurar Saldo Inicial
              </h3>
              <p className="text-xs text-gray-500 mt-0.5">
                {modalEditarApertura.codigo} — {modalEditarApertura.nombre} ({modalEditarApertura.categoria})
              </p>
            </div>

            <div className="bg-amber-50 dark:bg-amber-950/40 p-3 rounded-lg border border-amber-200 dark:border-amber-800 text-[11px] text-amber-800 dark:text-amber-200 space-y-1">
              <p className="font-semibold">⚠️ Regla de Inmutabilidad Económica:</p>
              <p>
                Esta modificación se registrará inmutablemente en la pista de auditoría. Si el fondo u obligación ya registra movimientos, el sistema bloqueará la alteración silenciosa.
              </p>
            </div>

            <form onSubmit={handleGuardarSaldoInicial} className="space-y-3.5 text-xs">
              <div className="grid grid-cols-2 gap-3 p-3 rounded-lg bg-gray-50 dark:bg-gray-800/60 border border-gray-100 dark:border-gray-800">
                <div>
                  <span className="text-gray-500 block text-[10px] uppercase font-bold">Valor Anterior</span>
                  <span className="text-base font-bold font-mono text-gray-700 dark:text-gray-300">
                    {formatMoney(modalEditarApertura.saldo_inicial_cents)}
                  </span>
                </div>
                <div>
                  <span className="text-gray-500 block text-[10px] uppercase font-bold">Valor Nuevo</span>
                  <span className="text-base font-bold font-mono text-emerald-600 dark:text-emerald-400">
                    {formatMoney(parseMoney(nuevoSaldoAperturaInput))}
                  </span>
                </div>
              </div>

              <div>
                <label className="block text-gray-700 dark:text-gray-300 font-semibold mb-1">
                  Nuevo Saldo Inicial ($ ARS) *
                </label>
                <input
                  type="text"
                  required
                  placeholder="0,00"
                  value={nuevoSaldoAperturaInput}
                  onChange={(e) => {
                    setNuevoSaldoAperturaInput(e.target.value);
                    setConfirmandoApertura(false);
                  }}
                  disabled={confirmandoApertura || guardandoApertura}
                  className="w-full p-2.5 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100 font-mono text-sm"
                  data-testid="input-nuevo-saldo-apertura"
                />
              </div>

              <div>
                <label className="block text-gray-700 dark:text-gray-300 font-semibold mb-1">
                  Motivo de la Modificación * (Obligatorio para Auditoría)
                </label>
                <textarea
                  required
                  rows={2}
                  placeholder="Motivo formal de la apertura / regularización..."
                  value={motivoAperturaInput}
                  onChange={(e) => {
                    setMotivoAperturaInput(e.target.value);
                    setConfirmandoApertura(false);
                  }}
                  disabled={confirmandoApertura || guardandoApertura}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100 text-xs"
                  data-testid="input-motivo-apertura"
                />
              </div>

              <div>
                <label className="block text-gray-700 dark:text-gray-300 font-semibold mb-1">
                  Referencia de Autorización (Opcional)
                </label>
                <input
                  type="text"
                  placeholder="Ej: Acta Arqueo Conjunto, Firma Alan/Yilda"
                  value={referenciaAperturaInput}
                  onChange={(e) => setReferenciaAperturaInput(e.target.value)}
                  disabled={confirmandoApertura || guardandoApertura}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100 text-xs"
                  data-testid="input-referencia-apertura"
                />
              </div>

              {confirmandoApertura && (
                <div className="p-3 bg-red-50 dark:bg-red-950/40 border border-red-200 dark:border-red-800 rounded-lg text-red-900 dark:text-red-200 space-y-1">
                  <p className="font-bold text-xs">⚠️ ¿Confirmás guardar esta apertura?</p>
                  <p className="text-[11px]">
                    El saldo inicial de <strong>{modalEditarApertura.codigo}</strong> cambiará de{" "}
                    <strong>{formatMoney(modalEditarApertura.saldo_inicial_cents)}</strong> a{" "}
                    <strong>{formatMoney(parseMoney(nuevoSaldoAperturaInput))}</strong>.
                  </p>
                </div>
              )}

              <div className="flex items-center justify-end gap-2 pt-2 border-t border-gray-100 dark:border-gray-800">
                <button
                  type="button"
                  onClick={() => {
                    setModalEditarApertura(null);
                    setConfirmandoApertura(false);
                  }}
                  disabled={guardandoApertura}
                  className="px-3.5 py-2 text-xs font-semibold rounded-lg bg-gray-100 hover:bg-gray-200 dark:bg-gray-800 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 transition"
                >
                  Cancelar
                </button>
                {confirmandoApertura ? (
                  <>
                    <button
                      type="button"
                      onClick={() => setConfirmandoApertura(false)}
                      disabled={guardandoApertura}
                      className="px-3 py-2 text-xs font-semibold rounded-lg bg-gray-200 dark:bg-gray-700 text-gray-800 dark:text-gray-200 transition"
                    >
                      Volver
                    </button>
                    <button
                      type="submit"
                      disabled={guardandoApertura}
                      data-testid="btn-confirmar-guardar-apertura"
                      className="px-4 py-2 text-xs font-bold rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white shadow-sm transition"
                    >
                      {guardandoApertura ? "Guardando..." : "Confirmar y Guardar"}
                    </button>
                  </>
                ) : (
                  <button
                    type="submit"
                    data-testid="btn-guardar-apertura-previa"
                    className="px-4 py-2 text-xs font-bold rounded-lg bg-[#C97B5A] hover:bg-[#b06748] text-white shadow-sm transition"
                  >
                    Revisar y Confirmar
                  </button>
                )}
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL: REGISTRAR ACREDITACIÓN DAVID */}
      {modalCCDavid === "ACREDITACION" && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm" data-testid="modal-acreditacion-david">
          <div className="bg-white dark:bg-gray-900 rounded-2xl max-w-md w-full p-6 shadow-2xl border border-gray-200 dark:border-gray-800 space-y-4">
            <div>
              <h3 className="text-lg font-bold text-gray-900 dark:text-gray-100">
                Registrar Acreditación de Saldo PREPAY (David)
              </h3>
              <p className="text-xs text-gray-500 mt-1">
                Acredita saldo digital en el fondo 1220 y aumenta la obligación pendiente con David.
              </p>
            </div>

            <form onSubmit={handleAcreditacionDavidSubmit} className="space-y-3.5 text-xs">
              <div>
                <label className="block text-gray-700 dark:text-gray-300 font-semibold mb-1">
                  Monto a Acreditar ($ ARS) *
                </label>
                <input
                  type="text"
                  required
                  placeholder="Ej: 10000 o 10.000,00"
                  value={formAcrMonto}
                  onChange={(e) => setFormAcrMonto(e.target.value)}
                  className="w-full p-2.5 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100 font-mono text-sm"
                  data-testid="input-acr-monto"
                />
              </div>

              <div>
                <label className="block text-gray-700 dark:text-gray-300 font-semibold mb-1">
                  Comprobante / Lote / Referencia *
                </label>
                <input
                  type="text"
                  required
                  placeholder="Ej: LOTE-DAVID-2026-11"
                  value={formAcrRef}
                  onChange={(e) => setFormAcrRef(e.target.value)}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100"
                  data-testid="input-acr-ref"
                />
              </div>

              <div>
                <label className="block text-gray-700 dark:text-gray-300 font-semibold mb-1">
                  Fecha de Acreditación (opcional)
                </label>
                <input
                  type="date"
                  value={formAcrFecha}
                  onChange={(e) => setFormAcrFecha(e.target.value)}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100"
                  data-testid="input-acr-fecha"
                />
              </div>

              <div>
                <label className="block text-gray-700 dark:text-gray-300 font-semibold mb-1">
                  Motivo u Observación
                </label>
                <input
                  type="text"
                  placeholder="Detalle o descripción de la acreditación..."
                  value={formAcrMotivo}
                  onChange={(e) => setFormAcrMotivo(e.target.value)}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100"
                  data-testid="input-acr-motivo"
                />
              </div>

              <div className="flex justify-end gap-2 pt-3 border-t border-gray-200 dark:border-gray-800">
                <button
                  type="button"
                  onClick={() => setModalCCDavid(null)}
                  disabled={submittingCCDavid}
                  className="px-4 py-2 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg font-semibold transition"
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  disabled={submittingCCDavid}
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-bold shadow-sm transition"
                  data-testid="btn-confirmar-acreditacion"
                >
                  {submittingCCDavid ? "Procesando..." : "Confirmar Acreditación (1220)"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL: REGISTRAR PAGO A DAVID */}
      {modalCCDavid === "PAGO" && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm" data-testid="modal-pago-david">
          <div className="bg-white dark:bg-gray-900 rounded-2xl max-w-md w-full p-6 shadow-2xl border border-gray-200 dark:border-gray-800 space-y-4">
            <div>
              <h3 className="text-lg font-bold text-gray-900 dark:text-gray-100">
                Registrar Pago Efectivo a David
              </h3>
              <p className="text-xs text-gray-500 mt-1">
                Debita exclusivamente del fondo físico 1130 (Prepay Efectivo) y reduce la obligación pendiente.
              </p>
            </div>

            <div className="p-3 bg-amber-50 dark:bg-amber-950/40 rounded-xl border border-amber-200 dark:border-amber-800 text-xs text-amber-900 dark:text-amber-200 space-y-1">
              <div className="flex justify-between">
                <span>Saldo disponible en Fondo 1130:</span>
                <span className="font-bold font-mono">{estadoCCDavid ? estadoCCDavid.saldo_disponible_1130_formatted : "$0,00"}</span>
              </div>
              <div className="flex justify-between">
                <span>Obligación pendiente actual:</span>
                <span className="font-bold font-mono">{estadoCCDavid ? estadoCCDavid.obligacion_pendiente_formatted : "$0,00"}</span>
              </div>
            </div>

            <form onSubmit={handlePagoDavidSubmit} className="space-y-3.5 text-xs">
              <div>
                <label className="block text-gray-700 dark:text-gray-300 font-semibold mb-1">
                  Monto a Pagar ($ ARS) *
                </label>
                <input
                  type="text"
                  required
                  placeholder="Ej: 5000 o 5.000,00"
                  value={formPagMonto}
                  onChange={(e) => setFormPagMonto(e.target.value)}
                  className="w-full p-2.5 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100 font-mono text-sm"
                  data-testid="input-pago-monto"
                />
              </div>

              <div>
                <label className="block text-gray-700 dark:text-gray-300 font-semibold mb-1">
                  Comprobante / Recibo de Pago *
                </label>
                <input
                  type="text"
                  required
                  placeholder="Ej: REC-MANO-DAVID-01"
                  value={formPagRef}
                  onChange={(e) => setFormPagRef(e.target.value)}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100"
                  data-testid="input-pago-ref"
                />
              </div>

              <div>
                <label className="block text-gray-700 dark:text-gray-300 font-semibold mb-1">
                  Fecha del Pago (opcional)
                </label>
                <input
                  type="date"
                  value={formPagFecha}
                  onChange={(e) => setFormPagFecha(e.target.value)}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100"
                  data-testid="input-pago-fecha"
                />
              </div>

              <div>
                <label className="block text-gray-700 dark:text-gray-300 font-semibold mb-1">
                  Motivo u Observación
                </label>
                <input
                  type="text"
                  placeholder="Detalle de la entrega de efectivo..."
                  value={formPagMotivo}
                  onChange={(e) => setFormPagMotivo(e.target.value)}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100"
                  data-testid="input-pago-motivo"
                />
              </div>

              <div className="flex justify-end gap-2 pt-3 border-t border-gray-200 dark:border-gray-800">
                <button
                  type="button"
                  onClick={() => setModalCCDavid(null)}
                  disabled={submittingCCDavid}
                  className="px-4 py-2 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg font-semibold transition"
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  disabled={submittingCCDavid}
                  className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg font-bold shadow-sm transition"
                  data-testid="btn-confirmar-pago"
                >
                  {submittingCCDavid ? "Procesando..." : "Confirmar Pago (1130)"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL: RECONOCIMIENTO DE DEUDA HISTÓRICA */}
      {modalCCDavid === "HISTORICO" && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm" data-testid="modal-historico-david">
          <div className="bg-white dark:bg-gray-900 rounded-2xl max-w-lg w-full p-6 shadow-2xl border border-gray-200 dark:border-gray-800 space-y-4">
            <div>
              <h3 className="text-lg font-bold text-gray-900 dark:text-gray-100">
                Reconocimiento de Deuda Histórica (David)
              </h3>
              <p className="text-xs text-gray-500 mt-1">
                Registra una obligación acordada de períodos previos.
              </p>
            </div>

            {/* ADVERTENCIA VISUAL OBLIGATORIA */}
            <div className="p-3.5 bg-purple-50 dark:bg-purple-950/50 border border-purple-200 dark:border-purple-800 rounded-xl text-xs text-purple-900 dark:text-purple-200 space-y-1.5" data-testid="alerta-reconocimiento-historico">
              <div className="flex items-center gap-1.5 font-bold text-purple-800 dark:text-purple-300">
                <span>⚠️</span> ADVERTENCIA OPERATIVA INNEGOCIABLE
              </div>
              <p>
                Esta operación reconoce una obligación anterior. No acredita saldo PREPAY ni modifica los fondos 1130 o 1220.
              </p>
            </div>

            <form onSubmit={handleHistoricoDavidSubmit} className="space-y-3.5 text-xs">
              <div>
                <label className="block text-gray-700 dark:text-gray-300 font-semibold mb-1">
                  Monto de Deuda Reconocida ($ ARS) *
                </label>
                <input
                  type="text"
                  required
                  placeholder="Ej: 25000 o 25.000,00"
                  value={formHisMonto}
                  onChange={(e) => setFormHisMonto(e.target.value)}
                  className="w-full p-2.5 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100 font-mono text-sm"
                  data-testid="input-his-monto"
                />
              </div>

              <div>
                <label className="block text-gray-700 dark:text-gray-300 font-semibold mb-1">
                  Fecha de Origen de la Deuda *
                </label>
                <input
                  type="date"
                  required
                  value={formHisFecha}
                  onChange={(e) => setFormHisFecha(e.target.value)}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100"
                  data-testid="input-his-fecha"
                />
              </div>

              <div>
                <label className="block text-gray-700 dark:text-gray-300 font-semibold mb-1">
                  Motivo / Justificación Obligatoria *
                </label>
                <textarea
                  required
                  rows={2}
                  placeholder="Motivo documentado del acuerdo o saldo histórico reconocido..."
                  value={formHisMotivo}
                  onChange={(e) => setFormHisMotivo(e.target.value)}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100"
                  data-testid="input-his-motivo"
                />
              </div>

              <div>
                <label className="block text-gray-700 dark:text-gray-300 font-semibold mb-1">
                  Referencia Externa / Acta (opcional)
                </label>
                <input
                  type="text"
                  placeholder="Ej: ACTA-AUDITORIA-OCT2026"
                  value={formHisRef}
                  onChange={(e) => setFormHisRef(e.target.value)}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100"
                  data-testid="input-his-ref"
                />
              </div>

              <div className="pt-2 border-t border-gray-200 dark:border-gray-800">
                <label className="flex items-start gap-2 cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={formHisConfirmacion}
                    onChange={(e) => setFormHisConfirmacion(e.target.checked)}
                    className="mt-0.5 rounded border-gray-300 text-purple-600 focus:ring-purple-500"
                    data-testid="checkbox-his-confirmacion"
                  />
                  <span className="text-gray-700 dark:text-gray-300 text-[11px] leading-tight">
                    Confirmo formalmente el reconocimiento de esta obligación histórica anterior sin afectación de fondos.
                  </span>
                </label>
              </div>

              <div className="flex justify-end gap-2 pt-3 border-t border-gray-200 dark:border-gray-800">
                <button
                  type="button"
                  onClick={() => setModalCCDavid(null)}
                  disabled={submittingCCDavid}
                  className="px-4 py-2 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg font-semibold transition"
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  disabled={submittingCCDavid || !formHisConfirmacion}
                  className="px-4 py-2 bg-purple-600 hover:bg-purple-700 disabled:opacity-50 text-white rounded-lg font-bold shadow-sm transition"
                  data-testid="btn-confirmar-historico"
                >
                  {submittingCCDavid ? "Procesando..." : "Registrar Reconocimiento Histórico"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL: NUEVO ANTICIPO A PREVENTISTA */}
      {modalAnticipo === "NUEVO" && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm" data-testid="modal-nuevo-anticipo">
          <div className="bg-white dark:bg-gray-900 rounded-2xl max-w-md w-full p-6 shadow-2xl border border-gray-200 dark:border-gray-800 space-y-4">
            <div>
              <h3 className="text-lg font-bold text-gray-900 dark:text-gray-100">
                Nuevo Anticipo a Preventista
              </h3>
              <p className="text-xs text-gray-500 mt-1">
                Registra la salida física de efectivo del fondo 1130 (Prepay Efectivo) para pagar al preventista.
              </p>
            </div>

            <div className="p-3 rounded-lg bg-blue-50 dark:bg-blue-950/40 border border-blue-200 dark:border-blue-800 text-xs flex justify-between items-center text-blue-900 dark:text-blue-200 font-mono">
              <span>Disponible en Fondo 1130:</span>
              <span className="font-extrabold text-sm">{formatMoney(saldo1130Cents)}</span>
            </div>

            <form onSubmit={handleCrearAnticipoSubmit} className="space-y-3 text-xs">
              <div>
                <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Preventista / Proveedor *
                </label>
                <input
                  type="text"
                  required
                  placeholder="Ej: Distribuidora Arcor / Juan Pérez"
                  value={formAntProveedor}
                  onChange={(e) => setFormAntProveedor(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-amber-500"
                  data-testid="input-anticipo-proveedor"
                />
              </div>

              <div>
                <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Importe Entregado ($) *
                </label>
                <input
                  type="text"
                  required
                  placeholder="0,00"
                  value={formAntMonto}
                  onChange={(e) => setFormAntMonto(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 font-mono text-sm focus:outline-none focus:ring-2 focus:ring-amber-500"
                  data-testid="input-anticipo-monto"
                />
              </div>

              <div>
                <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Motivo de Entrega *
                </label>
                <input
                  type="text"
                  required
                  placeholder="Ej: Pago de mercadería semanal"
                  value={formAntMotivo}
                  onChange={(e) => setFormAntMotivo(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-amber-500"
                  data-testid="input-anticipo-motivo"
                />
              </div>

              <div>
                <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Pedido ID (Opcional)
                </label>
                <input
                  type="text"
                  placeholder="Ej: PED-1002"
                  value={formAntPedidoId}
                  onChange={(e) => setFormAntPedidoId(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-amber-500"
                  data-testid="input-anticipo-pedidoid"
                />
              </div>

              <div>
                <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Comprobante / Remito (Opcional)
                </label>
                <input
                  type="text"
                  placeholder="Ej: Remito R-0001-000452"
                  value={formAntRef}
                  onChange={(e) => setFormAntRef(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-amber-500"
                  data-testid="input-anticipo-referencia"
                />
              </div>

              <div className="flex items-center justify-end gap-2 pt-3 border-t border-gray-200 dark:border-gray-800">
                <button
                  type="button"
                  onClick={() => setModalAnticipo(null)}
                  className="px-4 py-2 text-xs font-medium rounded-lg text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 transition"
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  disabled={submittingAnticipo}
                  className="px-4 py-2 text-xs font-bold rounded-lg bg-amber-600 hover:bg-amber-700 text-white transition shadow-sm disabled:opacity-50"
                  data-testid="btn-confirmar-nuevo-anticipo"
                >
                  {submittingAnticipo ? "Registrando..." : "Emitir Anticipo (Salida 1130)"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL: REPONER ANTICIPO A PREVENTISTA */}
      {modalAnticipo === "REPONER" && anticipoSeleccionado && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm" data-testid="modal-reponer-anticipo">
          <div className="bg-white dark:bg-gray-900 rounded-2xl max-w-md w-full p-6 shadow-2xl border border-gray-200 dark:border-gray-800 space-y-4">
            <div>
              <h3 className="text-lg font-bold text-gray-900 dark:text-gray-100">
                Reponer Fondos al Expediente
              </h3>
              <p className="text-xs text-gray-500 mt-1">
                Destinatario: <span className="font-semibold text-gray-800 dark:text-gray-200">{anticipoSeleccionado.proveedor}</span> | Expediente: <span className="font-mono">{anticipoSeleccionado.id.slice(0, 8)}...</span>
              </p>
            </div>

            {/* Resumen de Deuda y Disponibilidad en Cascada */}
            <div className="space-y-2 p-3 rounded-lg bg-gray-50 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700 text-xs">
              <div className="flex justify-between items-center">
                <span className="text-gray-500">Importe Original:</span>
                <span className="font-mono font-bold text-gray-800 dark:text-gray-200">{anticipoSeleccionado.monto_original_formatted}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-gray-500">Ya Repuesto:</span>
                <span className="font-mono font-bold text-emerald-600 dark:text-emerald-400">{anticipoSeleccionado.monto_repuesto_formatted}</span>
              </div>
              <div className="flex justify-between items-center pt-1 border-t border-gray-200 dark:border-gray-700 font-extrabold">
                <span className="text-amber-700 dark:text-amber-400">Saldo Pendiente:</span>
                <span className="font-mono text-sm text-amber-600 dark:text-amber-400">{anticipoSeleccionado.monto_pendiente_formatted}</span>
              </div>
            </div>

            {/* Disponibilidad Cascada */}
            <div className="p-3 rounded-lg bg-blue-50 dark:bg-blue-950/40 border border-blue-200 dark:border-blue-800 text-xs space-y-1">
              <p className="font-bold text-blue-900 dark:text-blue-200">Prelación de Reposición (hacia 1130):</p>
              <div className="flex justify-between text-blue-800 dark:text-blue-300 font-mono text-[11px]">
                <span>1° Fondo 1120 (Tesoro Acumulado):</span>
                <span className="font-bold">{formatMoney(saldo1120Cents)}</span>
              </div>
              <div className="flex justify-between text-blue-800 dark:text-blue-300 font-mono text-[11px]">
                <span>2° Fondo 1110 (Diario Mostrador):</span>
                <span className="font-bold">{formatMoney(saldo1110Cents)}</span>
              </div>
              <div className="flex justify-between text-blue-950 dark:text-blue-100 font-mono text-[11px] pt-1 border-t border-blue-200 dark:border-blue-800 font-bold">
                <span>Capacidad Total Disponible:</span>
                <span>{formatMoney(saldo1120Cents + saldo1110Cents)}</span>
              </div>
            </div>

            <form onSubmit={handleReponerAnticipoSubmit} className="space-y-3 text-xs">
              <div>
                <div className="flex justify-between items-center mb-1">
                  <label className="font-medium text-gray-700 dark:text-gray-300">
                    Importe a Reponer ($) *
                  </label>
                  <button
                    type="button"
                    onClick={() => setFormRepMonto((anticipoSeleccionado.monto_pendiente_cents / 100).toFixed(2))}
                    className="text-[11px] text-amber-600 hover:text-amber-700 dark:text-amber-400 font-semibold underline"
                  >
                    Reponer Total Pendiente
                  </button>
                </div>
                <input
                  type="text"
                  required
                  placeholder="0,00"
                  value={formRepMonto}
                  onChange={(e) => setFormRepMonto(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 font-mono text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
                  data-testid="input-reponer-monto"
                />
              </div>

              <div>
                <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Motivo / Detalle (Opcional)
                </label>
                <input
                  type="text"
                  placeholder="Ej: Reposición apertura de caja turno tarde"
                  value={formRepMotivo}
                  onChange={(e) => setFormRepMotivo(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-emerald-500"
                  data-testid="input-reponer-motivo"
                />
              </div>

              <div>
                <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Referencia de Auditoría (Opcional)
                </label>
                <input
                  type="text"
                  placeholder="Ej: CIERRE-TURNO-01"
                  value={formRepRef}
                  onChange={(e) => setFormRepRef(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-emerald-500"
                  data-testid="input-reponer-referencia"
                />
              </div>

              <div className="flex items-center justify-end gap-2 pt-3 border-t border-gray-200 dark:border-gray-800">
                <button
                  type="button"
                  onClick={() => {
                    setModalAnticipo(null);
                    setAnticipoSeleccionado(null);
                  }}
                  className="px-4 py-2 text-xs font-medium rounded-lg text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 transition"
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  disabled={submittingAnticipo}
                  className="px-4 py-2 text-xs font-bold rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white transition shadow-sm disabled:opacity-50"
                  data-testid="btn-confirmar-reposicion"
                >
                  {submittingAnticipo ? "Procesando..." : "Confirmar Reposición"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL: HISTORIAL DE REPOSICIONES */}
      {modalAnticipo === "HISTORIAL" && anticipoSeleccionado && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm" data-testid="modal-historial-reposiciones">
          <div className="bg-white dark:bg-gray-900 rounded-2xl max-w-2xl w-full p-6 shadow-2xl border border-gray-200 dark:border-gray-800 space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-lg font-bold text-gray-900 dark:text-gray-100">
                  Historial de Reposiciones
                </h3>
                <p className="text-xs text-gray-500 mt-0.5">
                  Expediente: <span className="font-mono">{anticipoSeleccionado.id}</span> | Preventista: <span className="font-semibold">{anticipoSeleccionado.proveedor}</span>
                </p>
              </div>
              <button
                type="button"
                onClick={() => {
                  setModalAnticipo(null);
                  setAnticipoSeleccionado(null);
                }}
                className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-lg font-bold"
              >
                ✕
              </button>
            </div>

            <div className="grid grid-cols-3 gap-3 p-3 bg-gray-50 dark:bg-gray-800/50 rounded-xl border border-gray-200 dark:border-gray-700 text-center font-mono text-xs">
              <div>
                <span className="text-[10px] text-gray-400 uppercase font-sans block">Original</span>
                <span className="font-bold text-gray-900 dark:text-gray-100">{anticipoSeleccionado.monto_original_formatted}</span>
              </div>
              <div>
                <span className="text-[10px] text-gray-400 uppercase font-sans block">Total Repuesto</span>
                <span className="font-bold text-emerald-600 dark:text-emerald-400">{anticipoSeleccionado.monto_repuesto_formatted}</span>
              </div>
              <div>
                <span className="text-[10px] text-gray-400 uppercase font-sans block">Saldo Restante</span>
                <span className="font-bold text-amber-600 dark:text-amber-400">{anticipoSeleccionado.monto_pendiente_formatted}</span>
              </div>
            </div>

            <div className="max-h-72 overflow-y-auto border border-gray-200 dark:border-gray-800 rounded-xl">
              <table className="w-full text-xs text-left">
                <thead className="bg-gray-50 dark:bg-gray-800/60 text-gray-500 dark:text-gray-400 sticky top-0 font-medium">
                  <tr>
                    <th className="py-2 px-3">Fecha</th>
                    <th className="py-2 px-3">Origen</th>
                    <th className="py-2 px-3 text-right">Monto</th>
                    <th className="py-2 px-3">Operador</th>
                    <th className="py-2 px-3">Movimientos</th>
                    <th className="py-2 px-3">Motivo / Ref</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200 dark:divide-gray-800 font-mono">
                  {cargandoReposiciones ? (
                    <tr>
                      <td colSpan={6} className="py-6 text-center text-gray-400 font-sans">
                        Cargando reposiciones...
                      </td>
                    </tr>
                  ) : reposicionesSeleccionadas.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="py-6 text-center text-gray-400 font-sans">
                        No se registran reposiciones para este expediente.
                      </td>
                    </tr>
                  ) : (
                    reposicionesSeleccionadas.map((rep) => (
                      <tr key={rep.id} className="hover:bg-gray-50/50 dark:hover:bg-gray-800/30">
                        <td className="py-2 px-3 whitespace-nowrap text-gray-600 dark:text-gray-400 text-[11px]">
                          {new Date(rep.creado_en_utc).toLocaleString("es-AR", {
                            day: "2-digit",
                            month: "2-digit",
                            year: "2-digit",
                            hour: "2-digit",
                            minute: "2-digit",
                          })}
                        </td>
                        <td className="py-2 px-3 whitespace-nowrap">
                          <span
                            className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                              rep.fondo_origen === "1120"
                                ? "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300"
                                : "bg-purple-100 text-purple-800 dark:bg-purple-950 dark:text-purple-300"
                            }`}
                          >
                            {rep.fondo_origen} → 1130
                          </span>
                        </td>
                        <td className="py-2 px-3 text-right font-extrabold text-emerald-600 dark:text-emerald-400 whitespace-nowrap">
                          {rep.monto_formatted}
                        </td>
                        <td className="py-2 px-3 font-sans text-gray-600 dark:text-gray-400 text-[11px] whitespace-nowrap">
                          {rep.actor}
                        </td>
                        <td className="py-2 px-3 text-[10px] text-gray-400 max-w-[120px] truncate" title={`Origen: ${rep.movement_id_egreso || 'N/A'} | Destino: ${rep.movement_id_ingreso || 'N/A'}`}>
                          {rep.movement_id_egreso ? `${rep.movement_id_egreso.slice(0, 6)}...` : "—"}
                        </td>
                        <td className="py-2 px-3 font-sans text-gray-600 dark:text-gray-400 text-[11px] max-w-[150px] truncate" title={rep.motivo || ""}>
                          {rep.motivo || "—"}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>

            <div className="flex justify-end pt-2">
              <button
                type="button"
                onClick={() => {
                  setModalAnticipo(null);
                  setAnticipoSeleccionado(null);
                }}
                className="px-4 py-2 text-xs font-semibold rounded-lg bg-gray-100 hover:bg-gray-200 dark:bg-gray-800 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 transition"
              >
                Cerrar
              </button>
            </div>
          </div>
        </div>
      )}

      {/* MODAL: REGISTRAR ENTRADA / SALIDA */}
      {modalMovimiento && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
          <div className="bg-white dark:bg-gray-900 rounded-2xl max-w-md w-full p-6 shadow-2xl border border-gray-200 dark:border-gray-800">
            <h3 className="text-lg font-bold text-gray-900 dark:text-gray-100 mb-1">
              {modalMovimiento === "ENTRY" ? "Registrar Entrada de Fondos" : "Registrar Salida de Fondos"}
            </h3>
            <p className="text-xs text-gray-500 mb-4">
              {modalMovimiento === "ENTRY"
                ? "Acreditación directa con justificación de contrapartida económica."
                : "Débito validando saldo disponible en el fondo seleccionado."}
            </p>

            <form onSubmit={handleMovimientoSubmit} className="space-y-3 text-xs">
              <div>
                <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Fondo de Tesorería
                </label>
                <select
                  value={movFondo}
                  onChange={(e) => setMovFondo(e.target.value)}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100 font-medium"
                >
                  {fondos.map((f) => (
                    <option key={f.codigo} value={f.codigo} className="dark:bg-gray-900">
                      {f.codigo} - {f.nombre} ({f.saldo_formateado})
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Importe en Pesos (ej: 10000.50)
                </label>
                <input
                  type="text"
                  required
                  data-testid="input-mov-monto"
                  placeholder="0.00"
                  value={movMontoStr}
                  onChange={(e) => setMovMontoStr(e.target.value)}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100 font-mono text-sm"
                />
              </div>

              <div>
                <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Contrapartida Económica (Obligatorio)
                </label>
                <input
                  type="text"
                  required
                  data-testid="input-mov-contrapartida"
                  placeholder="Ej: Aporte de socios / Pago proveedor insumos"
                  value={movContrapartida}
                  onChange={(e) => setMovContrapartida(e.target.value)}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100"
                />
              </div>

              <div>
                <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Motivo y Descripción (Obligatorio)
                </label>
                <textarea
                  required
                  rows={2}
                  placeholder="Justificación clara del movimiento..."
                  value={movMotivo}
                  onChange={(e) => setMovMotivo(e.target.value)}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100"
                />
              </div>

              <div>
                <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Referencia de Evidencia (Opcional)
                </label>
                <input
                  type="text"
                  placeholder="Ej: Recibo R-00129"
                  value={movEvidencia}
                  onChange={(e) => setMovEvidencia(e.target.value)}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100"
                />
              </div>

              <div className="flex justify-end gap-2 pt-2 border-t border-gray-200 dark:border-gray-800">
                <button
                  type="button"
                  onClick={() => setModalMovimiento(null)}
                  className="px-3.5 py-2 text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition"
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  className={`px-4 py-2 text-white font-semibold rounded-lg transition ${
                    modalMovimiento === "ENTRY"
                      ? "bg-emerald-600 hover:bg-emerald-700"
                      : "bg-red-600 hover:bg-red-700"
                  }`}
                >
                  Confirmar {modalMovimiento === "ENTRY" ? "Entrada" : "Salida"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL: INICIAR TRASLADO */}
      {modalTraslado && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
          <div className="bg-white dark:bg-gray-900 rounded-2xl max-w-md w-full p-6 shadow-2xl border border-gray-200 dark:border-gray-800">
            <h3 className="text-lg font-bold text-gray-900 dark:text-gray-100 mb-1">
              Despachar Traslado de Fondos
            </h3>
            <p className="text-xs text-gray-500 mb-4">
              El importe se debitará del fondo origen y quedará en tránsito hasta que el destino confirme recepción.
            </p>

            <form onSubmit={handleTrasladoSubmit} className="space-y-3 text-xs">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Fondo Origen
                  </label>
                  <select
                    value={trfOrigen}
                    data-testid="select-trf-origen"
                    onChange={(e) => setTrfOrigen(e.target.value)}
                    className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100 font-medium"
                  >
                    {fondos.map((f) => (
                      <option key={f.codigo} value={f.codigo} className="dark:bg-gray-900">
                        {f.codigo} - {f.nombre}
                      </option>
                    ))}
                  </select>
                  <span className="text-[10px] text-gray-500 mt-1 block">
                    Disp: <strong className="text-gray-700 dark:text-gray-300">{fondos.find((f) => f.codigo === trfOrigen)?.saldo_formateado || "$0,00"}</strong>
                  </span>
                </div>

                <div>
                  <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Fondo Destino
                  </label>
                  <select
                    value={trfDestino}
                    data-testid="select-trf-destino"
                    onChange={(e) => setTrfDestino(e.target.value)}
                    className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100 font-medium"
                  >
                    {fondos.map((f) => (
                      <option key={f.codigo} value={f.codigo} className="dark:bg-gray-900">
                        {f.codigo} - {f.nombre}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              {/* Advertencia de política de traslados */}
              {getTransferPolicyWarning(trfOrigen, trfDestino) && (
                <div className="p-2.5 rounded-lg bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-800 text-amber-800 dark:text-amber-300 text-[11px] space-y-1">
                  <div className="font-bold flex items-center gap-1">
                    <span>⚠️ Restricción de Fondos</span>
                  </div>
                  <div>{getTransferPolicyWarning(trfOrigen, trfDestino)}</div>
                </div>
              )}

              <div>
                <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Importe a Trasladar
                </label>
                <input
                  type="text"
                  required
                  data-testid="input-trf-monto"
                  placeholder="0.00"
                  value={trfMontoStr}
                  onChange={(e) => setTrfMontoStr(e.target.value)}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100 font-mono text-sm"
                />
              </div>

              <div>
                <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Motivo del Traslado
                </label>
                <textarea
                  required
                  rows={2}
                  data-testid="input-trf-motivo"
                  placeholder="Motivo de la transferencia entre fondos..."
                  value={trfMotivo}
                  onChange={(e) => setTrfMotivo(e.target.value)}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100"
                />
              </div>

              <div>
                <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Observaciones / Remito / Evidencia (Opcional)
                </label>
                <input
                  type="text"
                  data-testid="input-trf-evidencia"
                  placeholder="N° comprobante, remito o referencia..."
                  value={trfEvidencia}
                  onChange={(e) => setTrfEvidencia(e.target.value)}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100"
                />
              </div>

              <div className="text-[11px] text-gray-500 pt-1">
                Responsable del despacho: <strong className="text-gray-700 dark:text-gray-300">{currentUser?.display_name || currentUser?.username || "alan"}</strong>
              </div>

              <div className="flex justify-end gap-2 pt-2 border-t border-gray-200 dark:border-gray-800">
                <button
                  type="button"
                  onClick={() => {
                    setModalTraslado(false);
                    setTrfEvidencia("");
                  }}
                  className="px-3.5 py-2 text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition"
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  data-testid="btn-submit-despachar-trf"
                  disabled={!!getTransferPolicyWarning(trfOrigen, trfDestino) || trfDespachando}
                  className={`px-4 py-2 font-semibold rounded-lg transition ${
                    getTransferPolicyWarning(trfOrigen, trfDestino) || trfDespachando
                      ? "bg-gray-300 dark:bg-gray-700 text-gray-500 cursor-not-allowed"
                      : "bg-indigo-600 hover:bg-indigo-700 text-white"
                  }`}
                >
                  {trfDespachando ? "Despachando..." : "Confirmar Despacho"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL: CONFIRMAR RECEPCIÓN DE TRASLADO */}
      {modalConfirmacionTrf && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
          <div className="bg-white dark:bg-gray-900 rounded-2xl max-w-md w-full p-6 shadow-2xl border border-gray-200 dark:border-gray-800 space-y-4">
            <div>
              <h3 className="text-lg font-bold text-gray-900 dark:text-gray-100">
                Confirmar Recepción de Traslado
              </h3>
              <p className="text-xs text-gray-500">
                Al confirmar la recepción, los fondos se acreditarán en el fondo destino y se generará el movimiento de entrada vinculado.
              </p>
            </div>

            <div className="p-3 bg-gray-50 dark:bg-gray-800/60 rounded-xl space-y-1.5 text-xs">
              <div className="flex justify-between">
                <span className="text-gray-500">ID Traslado:</span>
                <span className="font-mono font-bold text-gray-900 dark:text-gray-100">{modalConfirmacionTrf.transfer_id}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">Origen:</span>
                <span className="font-semibold text-gray-800 dark:text-gray-200">{modalConfirmacionTrf.origin_fund_code} - {modalConfirmacionTrf.origin_fund_name}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">Destino:</span>
                <span className="font-semibold text-gray-800 dark:text-gray-200">{modalConfirmacionTrf.destination_fund_code} - {modalConfirmacionTrf.destination_fund_name}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">Importe:</span>
                <span className="font-mono font-bold text-sm text-emerald-600 dark:text-emerald-400">{modalConfirmacionTrf.amount_formatted}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">Motivo:</span>
                <span className="text-gray-700 dark:text-gray-300">{modalConfirmacionTrf.reason}</span>
              </div>
            </div>

            <div className="text-xs space-y-1">
              <label className="block font-medium text-gray-700 dark:text-gray-300">
                Observaciones de Recepción (Opcional)
              </label>
              <input
                type="text"
                placeholder="Detalle o verificación al recibir..."
                value={obsConfirmacionTrf}
                onChange={(e) => setObsConfirmacionTrf(e.target.value)}
                className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100"
              />
            </div>

            <div className="text-[11px] text-gray-500">
              Receptor: <strong className="text-gray-700 dark:text-gray-300">{currentUser?.display_name || currentUser?.username || "alan"}</strong>
            </div>

            <div className="flex justify-end gap-2 pt-2 border-t border-gray-200 dark:border-gray-800">
              <button
                type="button"
                onClick={() => setModalConfirmacionTrf(null)}
                className="px-3.5 py-2 text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition text-xs"
              >
                Volver
              </button>
              <button
                type="button"
                disabled={confirmandoTrf}
                data-testid="btn-submit-confirmar-recepcion"
                onClick={() => handleConfirmarTraslado(modalConfirmacionTrf)}
                className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white font-semibold rounded-lg transition text-xs"
              >
                {confirmandoTrf ? "Acreditando..." : "Confirmar Recepción"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* MODAL: ANULAR TRASLADO PENDIENTE */}
      {modalAnulacionTrf && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
          <div className="bg-white dark:bg-gray-900 rounded-2xl max-w-md w-full p-6 shadow-2xl border border-gray-200 dark:border-gray-800 space-y-4">
            <div>
              <h3 className="text-lg font-bold text-red-600 dark:text-red-400">
                Anular Traslado en Tránsito
              </h3>
              <p className="text-xs text-gray-500">
                El traslado quedará cancelado y los fondos se restituirán íntegramente al fondo origen mediante un contra-asiento de REVERSIÓN inmutable.
              </p>
            </div>

            <div className="p-3 bg-red-50/60 dark:bg-red-950/30 rounded-xl space-y-1.5 text-xs border border-red-100 dark:border-red-900/40">
              <div className="flex justify-between">
                <span className="text-gray-500">ID Traslado:</span>
                <span className="font-mono font-bold text-gray-900 dark:text-gray-100">{modalAnulacionTrf.transfer_id.substring(0, 8)}...</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">Restitución a origen:</span>
                <span className="font-semibold text-gray-800 dark:text-gray-200">{modalAnulacionTrf.origin_fund_code} - {modalAnulacionTrf.origin_fund_name}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">Importe a reintegrar:</span>
                <span className="font-mono font-bold text-sm text-red-600 dark:text-red-400">{modalAnulacionTrf.amount_formatted}</span>
              </div>
            </div>

            <div className="text-xs space-y-1">
              <label className="block font-medium text-gray-700 dark:text-gray-300">
                Motivo Obligatorio de Anulación
              </label>
              <textarea
                required
                rows={2}
                data-testid="input-motivo-anulacion-trf"
                placeholder="Explicación detallada de la anulación..."
                value={motivoAnulacionTrf}
                onChange={(e) => setMotivoAnulacionTrf(e.target.value)}
                className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100 text-xs"
              />
            </div>

            <div className="text-[11px] text-gray-500">
              Autorizado por: <strong className="text-gray-700 dark:text-gray-300">{currentUser?.display_name || currentUser?.username || "alan"}</strong>
            </div>

            <div className="flex justify-end gap-2 pt-2 border-t border-gray-200 dark:border-gray-800">
              <button
                type="button"
                onClick={() => setModalAnulacionTrf(null)}
                className="px-3.5 py-2 text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition text-xs"
              >
                Volver
              </button>
              <button
                type="button"
                data-testid="btn-submit-anular-trf"
                disabled={anulandoTrf || !motivoAnulacionTrf.trim()}
                onClick={() => handleCancelarTraslado()}
                className={`px-4 py-2 font-semibold rounded-lg transition text-xs ${
                  anulandoTrf || !motivoAnulacionTrf.trim()
                    ? "bg-gray-300 dark:bg-gray-700 text-gray-500 cursor-not-allowed"
                    : "bg-red-600 hover:bg-red-700 text-white"
                }`}
              >
                {anulandoTrf ? "Anulando..." : "Confirmar Anulación"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* MODAL: CREAR RESERVA */}
      {modalReserva && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
          <div className="bg-white dark:bg-gray-900 rounded-2xl max-w-md w-full p-6 shadow-2xl border border-gray-200 dark:border-gray-800">
            <h3 className="text-lg font-bold text-gray-900 dark:text-gray-100 mb-1">
              Crear Reserva Preventiva
            </h3>
            <p className="text-xs text-gray-500 mb-4">
              Afecta preventivamente la disponibilidad sin mover efectivo ni generar movimientos contables.
            </p>

            <form onSubmit={handleReservaSubmit} className="space-y-3 text-xs">
              <div>
                <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Fondo a Afectar
                </label>
                <select
                  value={resFondo}
                  onChange={(e) => setResFondo(e.target.value)}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100 font-medium"
                >
                  {fondos.map((f) => (
                    <option key={f.codigo} value={f.codigo} className="dark:bg-gray-900">
                      {f.codigo} - {f.nombre} ({f.saldo_formateado})
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Monto a Reservar
                </label>
                <input
                  type="text"
                  required
                  placeholder="0.00"
                  value={resMontoStr}
                  onChange={(e) => setResMontoStr(e.target.value)}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100 font-mono text-sm"
                />
              </div>

              <div>
                <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Beneficiario / Destino Previsto
                </label>
                <input
                  type="text"
                  required
                  data-testid="input-res-beneficiario"
                  placeholder="Ej: Distribuidora / AFIP / Empleado"
                  value={resBeneficiario}
                  onChange={(e) => setResBeneficiario(e.target.value)}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100"
                />
              </div>

              <div>
                <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Identificador de Obligación (Anti-doble reserva)
                </label>
                <input
                  type="text"
                  placeholder="Ej: OBL-2026-FLETE-01 (opcional)"
                  value={resObligationId}
                  onChange={(e) => setResObligationId(e.target.value)}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100 font-mono"
                />
              </div>

              <div>
                <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Motivo de la Reserva
                </label>
                <textarea
                  required
                  rows={2}
                  data-testid="input-res-motivo"
                  placeholder="Justificación de la reserva preventiva..."
                  value={resMotivo}
                  onChange={(e) => setResMotivo(e.target.value)}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100"
                />
              </div>

              <div className="flex justify-end gap-2 pt-2 border-t border-gray-200 dark:border-gray-800">
                <button
                  type="button"
                  onClick={() => setModalReserva(false)}
                  className="px-3.5 py-2 text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition"
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 bg-purple-600 hover:bg-purple-700 text-white font-semibold rounded-lg transition"
                >
                  Registrar Reserva
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL: DETALLE Y GESTIÓN DE RESERVA */}
      {reservaSeleccionada && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
          <div className="bg-white dark:bg-gray-900 rounded-2xl max-w-xl w-full p-6 shadow-2xl border border-gray-200 dark:border-gray-800 max-h-[90vh] overflow-y-auto">
            <div className="flex justify-between items-start mb-4">
              <div>
                <span className="px-2 py-0.5 rounded bg-purple-100 text-purple-800 dark:bg-purple-950 dark:text-purple-300 text-xs font-mono font-bold">
                  {reservaSeleccionada.fund_code} - {reservaSeleccionada.fund_name}
                </span>
                <h3 className="text-lg font-bold text-gray-900 dark:text-gray-100 mt-1">
                  Reserva: {reservaSeleccionada.beneficiary}
                </h3>
                <p className="text-xs text-gray-500">{reservaSeleccionada.reason}</p>
              </div>
              <button
                onClick={() => setReservaSeleccionada(null)}
                className="text-gray-400 hover:text-gray-600 text-lg"
              >
                ✕
              </button>
            </div>

            {/* Invariante: Original = Aplicado + Liberado + Pendiente */}
            <div className="grid grid-cols-4 gap-2 p-3 bg-gray-50 dark:bg-gray-800/50 rounded-xl text-center text-xs font-mono mb-4 border border-gray-200 dark:border-gray-700">
              <div>
                <span className="text-[10px] text-gray-400 uppercase block">Original</span>
                <span className="font-bold text-gray-900 dark:text-gray-100">{reservaSeleccionada.original_amount_formatted}</span>
              </div>
              <div>
                <span className="text-[10px] text-gray-400 uppercase block">Pendiente</span>
                <span className="font-bold text-amber-600 dark:text-amber-400">{reservaSeleccionada.pending_amount_formatted}</span>
              </div>
              <div>
                <span className="text-[10px] text-gray-400 uppercase block">Aplicado</span>
                <span className="font-bold text-emerald-600 dark:text-emerald-400">{reservaSeleccionada.applied_amount_formatted}</span>
              </div>
              <div>
                <span className="text-[10px] text-gray-400 uppercase block">Liberado</span>
                <span className="font-bold text-blue-600 dark:text-blue-400">{reservaSeleccionada.released_amount_formatted}</span>
              </div>
            </div>

            {/* Acciones de Reserva */}
            {reservaSeleccionada.pending_amount_cents > 0 && (
              <div className="flex flex-wrap gap-2 mb-4">
                <button
                  onClick={() => setModalAccionReserva("APPLY")}
                  className="flex-1 py-2 bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold rounded-lg transition"
                >
                  ✓ Aplicar Fondos de Reserva
                </button>
                <button
                  onClick={() => setModalAccionReserva("RELEASE")}
                  className="flex-1 py-2 bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold rounded-lg transition"
                >
                  ↺ Liberar Fondos hacia Disponible
                </button>
                <button
                  onClick={() => setModalEgresoReservado(reservaSeleccionada)}
                  data-testid="btn-egreso-reservado"
                  className="flex-1 py-2 bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold rounded-lg transition"
                >
                  ⚡ Egreso Reservado Atómico
                </button>
              </div>
            )}

            {/* Formulario Inline para Aplicar/Liberar */}
            {modalAccionReserva && (
              <form onSubmit={handleAccionReservaSubmit} className="p-3 bg-purple-50 dark:bg-purple-950/30 rounded-xl border border-purple-200 dark:border-purple-800 space-y-2 mb-4 text-xs">
                <h4 className="font-bold text-purple-900 dark:text-purple-200">
                  {modalAccionReserva === "APPLY" ? "Aplicar Reserva a Obligación" : "Liberar Fondos Reservados"}
                </h4>
                <div className="grid grid-cols-2 gap-2">
                  <input
                    type="text"
                    required
                    placeholder="Monto a aplicar/liberar"
                    value={accionMontoStr}
                    onChange={(e) => setAccionMontoStr(e.target.value)}
                    className="p-1.5 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 font-mono"
                  />
                  <input
                    type="text"
                    required
                    placeholder="Motivo de la operación"
                    value={accionMotivo}
                    onChange={(e) => setAccionMotivo(e.target.value)}
                    className="p-1.5 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100"
                  />
                </div>
                <div className="flex justify-end gap-2 pt-1">
                  <button
                    type="button"
                    onClick={() => setModalAccionReserva(null)}
                    className="px-2.5 py-1 text-gray-600 dark:text-gray-400 hover:bg-gray-100 rounded"
                  >
                    Cancelar
                  </button>
                  <button
                    type="submit"
                    className="px-3 py-1 bg-purple-600 text-white font-semibold rounded hover:bg-purple-700"
                  >
                    Confirmar {modalAccionReserva === "APPLY" ? "Aplicación" : "Liberación"}
                  </button>
                </div>
              </form>
            )}

            {/* Historial de Movimientos de la Reserva */}
            <div>
              <h4 className="text-xs font-bold uppercase tracking-wider text-gray-500 mb-2">
                Historial de Aplicaciones y Liberaciones
              </h4>
              <div className="space-y-1.5 max-h-40 overflow-y-auto">
                {reservaSeleccionada.movimientos && reservaSeleccionada.movimientos.length > 0 ? (
                  reservaSeleccionada.movimientos.map((m) => (
                    <div
                      key={m.id}
                      className="p-2 bg-gray-50 dark:bg-gray-800/40 rounded border border-gray-200 dark:border-gray-700 text-xs flex justify-between items-center"
                    >
                      <div>
                        <span
                          className={`px-1.5 py-0.5 rounded text-[10px] font-bold mr-2 ${
                            m.action_type === "APPLY"
                              ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
                              : "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300"
                          }`}
                        >
                          {m.action_type}
                        </span>
                        <span>{m.reason}</span>
                      </div>
                      <span className="font-mono font-bold">{m.amount_formatted}</span>
                    </div>
                  ))
                ) : (
                  <p className="text-xs text-gray-400 italic">No se han registrado aplicaciones ni liberaciones aún.</p>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* MODAL DETALLE DE FONDO Y AUDITORÍA */}
      {fondoSeleccionado && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm" data-testid="modal-detalle-fondo">
          <div className="bg-white dark:bg-gray-900 rounded-2xl max-w-2xl w-full max-h-[90vh] overflow-hidden flex flex-col shadow-2xl border border-gray-200 dark:border-gray-800">
            <div className="p-5 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
              <div>
                <div className="flex items-center gap-2">
                  <span className="font-mono text-sm px-2 py-0.5 bg-blue-100 dark:bg-blue-950 text-blue-800 dark:text-blue-300 font-bold rounded">
                    {fondoSeleccionado.codigo}
                  </span>
                  <h3 className="text-lg font-bold text-gray-900 dark:text-gray-100">
                    {fondoSeleccionado.nombre}
                  </h3>
                </div>
                <p className="text-xs text-gray-500 mt-0.5">Versión actual: v{fondoSeleccionado.version}</p>
              </div>
              <button onClick={() => setFondoSeleccionado(null)} className="text-gray-400 hover:text-gray-600 text-lg p-1">
                ✕
              </button>
            </div>

            <div className="p-5 overflow-y-auto space-y-4">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 bg-gray-50 dark:bg-gray-800/50 p-4 rounded-xl border border-gray-200 dark:border-gray-700 text-xs">
                <div>
                  <span className="text-gray-400 block">Naturaleza</span>
                  <span className="font-semibold text-gray-900 dark:text-gray-100">{fondoSeleccionado.naturaleza}</span>
                </div>
                <div>
                  <span className="text-gray-400 block">Titularidad</span>
                  <span className="font-semibold text-gray-900 dark:text-gray-100">{fondoSeleccionado.titularidad}</span>
                </div>
                <div>
                  <span className="text-gray-400 block">Estado</span>
                  <span className="font-semibold text-gray-900 dark:text-gray-100">{fondoSeleccionado.estado}</span>
                </div>
                <div>
                  <span className="text-gray-400 block">Saldo</span>
                  <span className="font-bold text-gray-900 dark:text-gray-100">{fondoSeleccionado.saldo_formateado}</span>
                </div>
              </div>

              <div className="border border-gray-200 dark:border-gray-800 p-4 rounded-xl">
                <h4 className="text-xs font-bold uppercase tracking-wider text-gray-500 mb-2">Control de Estado</h4>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={motivoCambio}
                    onChange={(e) => setMotivoCambio(e.target.value)}
                    placeholder="Motivo del cambio de estado..."
                    className="flex-1 text-xs p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100"
                  />
                  {fondoSeleccionado.estado === "ACTIVO" ? (
                    <button
                      onClick={() => handleCambiarEstadoFondo("BLOQUEADO")}
                      className="px-3 py-2 bg-red-600 hover:bg-red-700 text-white text-xs font-semibold rounded-lg"
                    >
                      Bloquear
                    </button>
                  ) : (
                    <button
                      onClick={() => handleCambiarEstadoFondo("ACTIVO")}
                      className="px-3 py-2 bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold rounded-lg"
                    >
                      Activar
                    </button>
                  )}
                </div>
              </div>

              <div>
                <h4 className="text-xs font-bold uppercase tracking-wider text-gray-500 mb-2">Pista de Auditoría</h4>
                {cargandoAuditoria ? (
                  <p className="text-xs text-gray-400">Cargando...</p>
                ) : (
                  <div className="space-y-1.5 max-h-48 overflow-y-auto">
                    {auditoriaFondo.map((item) => (
                      <div key={item.id} className="p-2.5 bg-gray-50 dark:bg-gray-800/40 rounded border border-gray-200 dark:border-gray-700 text-xs">
                        <div className="flex justify-between">
                          <span className="font-semibold text-gray-900 dark:text-gray-100">
                            {item.accion} por {item.usuario}
                          </span>
                          <span className="text-[10px] text-gray-400">
                            {new Date(item.timestamp_utc).toLocaleString("es-AR")}
                          </span>
                        </div>
                        {item.motivo && <p className="text-gray-500 mt-0.5 italic">{item.motivo}</p>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>

            <div className="p-4 border-t border-gray-200 dark:border-gray-800 flex justify-end">
              <button
                onClick={() => setFondoSeleccionado(null)}
                className="px-4 py-2 bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 text-xs font-medium rounded-lg"
              >
                Cerrar
              </button>
            </div>
          </div>
        </div>
      )}

      {/* MODAL: ABRIR JORNADA */}
      {modalAbrirJornada && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
          <div className="bg-white dark:bg-gray-900 rounded-2xl max-w-md w-full p-6 shadow-2xl border border-gray-200 dark:border-gray-800">
            <h3 className="text-lg font-bold text-gray-900 dark:text-gray-100 mb-2">
              Abrir Jornada de Tesorería
            </h3>
            <form onSubmit={handleAbrirJornadaSubmit} className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Observaciones de Apertura
                </label>
                <textarea
                  value={obsApertura}
                  onChange={(e) => setObsApertura(e.target.value)}
                  rows={2}
                  className="w-full text-xs p-2.5 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100"
                  placeholder="Inicio de jornada operativa..."
                />
              </div>
              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setModalAbrirJornada(false)}
                  className="px-4 py-2 text-xs text-gray-600 dark:text-gray-400"
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold rounded-lg"
                >
                  Confirmar Apertura
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL: CERRAR JORNADA */}
      {modalCerrarJornada && jornadaActiva && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
          <div className="bg-white dark:bg-gray-900 rounded-2xl max-w-lg w-full max-h-[90vh] overflow-hidden flex flex-col shadow-2xl border border-gray-200 dark:border-gray-800">
            <div className="p-5 border-b border-gray-200 dark:border-gray-800">
              <h3 className="text-lg font-bold text-gray-900 dark:text-gray-100">
                Cierre y Arqueo de Jornada #{jornadaActiva.numero_jornada}
              </h3>
            </div>
            <form onSubmit={handleCerrarJornadaSubmit} className="p-5 overflow-y-auto space-y-4">
              <div className="space-y-2">
                {fondos.map((f) => (
                  <div key={f.codigo} className="flex items-center justify-between gap-3 text-xs">
                    <span className="font-medium text-gray-800 dark:text-gray-200 truncate flex-1">
                      {f.codigo} - {f.nombre}
                    </span>
                    <input
                      type="text"
                      placeholder="0.00"
                      value={arqueoValores[f.codigo] || ""}
                      onChange={(e) => setArqueoValores({ ...arqueoValores, [f.codigo]: e.target.value })}
                      className="w-32 text-right p-1.5 rounded border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100 font-mono"
                    />
                  </div>
                ))}
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Observaciones de Cierre
                </label>
                <textarea
                  value={obsCierre}
                  onChange={(e) => setObsCierre(e.target.value)}
                  rows={2}
                  className="w-full text-xs p-2.5 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100"
                />
              </div>
              <div className="flex justify-end gap-2 pt-2 border-t border-gray-200 dark:border-gray-800">
                <button
                  type="button"
                  onClick={() => setModalCerrarJornada(false)}
                  className="px-4 py-2 text-xs text-gray-600 dark:text-gray-400"
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 bg-amber-600 hover:bg-amber-700 text-white text-xs font-semibold rounded-lg"
                >
                  Confirmar Cierre
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL: NUEVO ARQUEO (LAB-009) */}
      {modalArqueo && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
          <div className="bg-white dark:bg-gray-900 rounded-2xl max-w-md w-full p-6 shadow-2xl border border-gray-200 dark:border-gray-800">
            <h3 className="text-lg font-bold text-gray-900 dark:text-gray-100 mb-1">
              Registrar Arqueo de Fondo
            </h3>
            <p className="text-xs text-gray-500 mb-4">
              Captura el recuento físico verificado contra el saldo registrado en libros sin alterar automáticamente los fondos.
            </p>

            <form onSubmit={handleCrearArqueoSubmit} className="space-y-3 text-xs">
              <div>
                <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Fondo de Tesorería
                </label>
                <select
                  value={arqFondo}
                  data-testid="select-arq-fondo"
                  onChange={(e) => setArqFondo(e.target.value)}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100 font-medium"
                >
                  {fondos.map((f) => (
                    <option key={f.codigo} value={f.codigo} className="dark:bg-gray-900">
                      {f.codigo} - {f.nombre} ({f.saldo_formateado})
                    </option>
                  ))}
                </select>
              </div>

              <div className="p-2.5 bg-gray-50 dark:bg-gray-800/50 rounded-lg flex justify-between items-center">
                <span className="text-gray-500 font-medium">Saldo Teórico Registrado:</span>
                <span className="font-mono font-bold text-gray-900 dark:text-gray-100">
                  {fondos.find((f) => f.codigo === arqFondo)?.saldo_formateado || "$0,00"}
                </span>
              </div>

              <div>
                <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Tipo de Arqueo
                </label>
                <select
                  value={arqTipo}
                  data-testid="select-arq-tipo"
                  onChange={(e) => setArqTipo(e.target.value as TipoArqueo)}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100 font-medium"
                >
                  <option value="OPENING">OPENING — Apertura</option>
                  <option value="PARTIAL">PARTIAL — Parcial / Rutinario</option>
                  <option value="TOTAL">TOTAL — Total</option>
                  <option value="EXTRAORDINARY">EXTRAORDINARY — Extraordinario</option>
                  <option value="CLOSING">CLOSING — Cierre de Jornada</option>
                </select>
              </div>

              <div>
                <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Saldo Verificado (Efectivo Contado)
                </label>
                <input
                  type="text"
                  required
                  data-testid="input-arq-verificado"
                  placeholder="0.00"
                  value={arqMontoStr}
                  onChange={(e) => setArqMontoStr(e.target.value)}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100 font-mono text-sm"
                />
              </div>

              {(() => {
                const fSel = fondos.find((f) => f.codigo === arqFondo);
                const sTeorico = fSel ? fSel.saldo_centavos : 0;
                const sVerificado = parseMoney(arqMontoStr || "0");
                const diff = sVerificado - sTeorico;
                if (!arqMontoStr) return null;
                return (
                  <div
                    className={`p-2.5 rounded-lg border flex justify-between items-center ${
                      diff === 0
                        ? "bg-emerald-50 dark:bg-emerald-950/30 border-emerald-200 dark:border-emerald-800 text-emerald-800 dark:text-emerald-300"
                        : diff < 0
                        ? "bg-red-50 dark:bg-red-950/30 border-red-200 dark:border-red-800 text-red-800 dark:text-red-300"
                        : "bg-amber-50 dark:bg-amber-950/30 border-amber-200 dark:border-amber-800 text-amber-800 dark:text-amber-300"
                    }`}
                  >
                    <span className="font-semibold">
                      {diff === 0 ? "Sin Diferencia" : diff < 0 ? "Faltante Detectado:" : "Sobrante Detectado:"}
                    </span>
                    <span className="font-mono font-bold">
                      {diff === 0 ? "$0,00" : formatMoney(diff)}
                    </span>
                  </div>
                );
              })()}

              {(() => {
                const fSel = fondos.find((f) => f.codigo === arqFondo);
                const sTeorico = fSel ? fSel.saldo_centavos : 0;
                const sVerificado = parseMoney(arqMontoStr || "0");
                const diff = sVerificado - sTeorico;
                const tieneDiff = arqMontoStr !== "" && diff !== 0;
                return (
                  <div>
                    <div className="flex justify-between items-center mb-1">
                      <label className="font-medium text-gray-700 dark:text-gray-300">
                        Observaciones del Arqueo
                      </label>
                      {tieneDiff && (
                        <span className="text-[10px] text-amber-600 dark:text-amber-400 font-bold">
                          * Obligatorio por diferencia de caja
                        </span>
                      )}
                    </div>
                    <textarea
                      rows={2}
                      required={tieneDiff}
                      placeholder={
                        tieneDiff
                          ? "Explique el motivo del faltante o sobrante..."
                          : "Comentarios del recuento físico..."
                      }
                      value={arqObservaciones}
                      onChange={(e) => setArqObservaciones(e.target.value)}
                      className={`w-full p-2 rounded-lg border bg-transparent text-gray-900 dark:text-gray-100 ${
                        tieneDiff && !arqObservaciones.trim()
                          ? "border-amber-500 ring-1 ring-amber-500"
                          : "border-gray-300 dark:border-gray-700"
                      }`}
                    />
                  </div>
                );
              })()}

              <div className="pt-2 border-t border-gray-200 dark:border-gray-800">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={arqCrearExpediente}
                    onChange={(e) => setArqCrearExpediente(e.target.checked)}
                    className="rounded text-blue-600"
                  />
                  <span className="font-medium text-gray-700 dark:text-gray-300">
                    Abrir expediente de diferencia si no coincide el saldo
                  </span>
                </label>
                {arqCrearExpediente && (
                  <textarea
                    rows={2}
                    placeholder="Hipótesis inicial o notas de investigación..."
                    value={arqNotasInvestigacion}
                    onChange={(e) => setArqNotasInvestigacion(e.target.value)}
                    className="w-full mt-2 p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100"
                  />
                )}
              </div>

              <div className="flex justify-end gap-2 pt-2 border-t border-gray-200 dark:border-gray-800">
                <button
                  type="button"
                  onClick={() => setModalArqueo(false)}
                  className="px-3.5 py-2 text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition"
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white font-semibold rounded-lg transition"
                >
                  Registrar Arqueo
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL: RESOLVER EXPEDIENTE DE DIFERENCIA (LAB-009) */}
      {expedienteSeleccionado && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
          <div className="bg-white dark:bg-gray-900 rounded-2xl max-w-md w-full p-6 shadow-2xl border border-gray-200 dark:border-gray-800">
            <h3 className="text-lg font-bold text-gray-900 dark:text-gray-100 mb-1">
              Resolver Expediente de Diferencia
            </h3>
            <p className="text-xs text-gray-500 mb-2">
              Expediente: <span className="font-mono font-bold">{expedienteSeleccionado.case_id}</span>
            </p>
            <div className="p-3 bg-red-50 dark:bg-red-950/40 border border-red-200 dark:border-red-800 rounded text-xs mb-4">
              <span className="font-bold text-red-800 dark:text-red-300 block">
                Diferencia bajo investigación: {expedienteSeleccionado.difference_formatted}
              </span>
              <span className="text-gray-600 dark:text-gray-400">
                Notas: {expedienteSeleccionado.investigation_notes || "Sin notas"}
              </span>
            </div>

            <form onSubmit={handleResolverExpedienteSubmit} className="space-y-3 text-xs">
              <div>
                <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Notas de Resolución y Justificación Económica (Obligatorio)
                </label>
                <textarea
                  required
                  rows={3}
                  data-testid="input-res-notas"
                  placeholder="Dictamen final y conclusión de la investigación..."
                  value={resNotas}
                  onChange={(e) => setResNotas(e.target.value)}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100"
                />
              </div>

              <div>
                <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Referencia de Evidencia Documental (Obligatorio)
                </label>
                <input
                  type="text"
                  required
                  data-testid="input-res-evidencia"
                  placeholder="Ej: ACTA-ARQ-2026-004 / TICKET-POS-9812"
                  value={resEvidencia}
                  onChange={(e) => setResEvidencia(e.target.value)}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100"
                />
              </div>

              <div className="flex justify-end gap-2 pt-2 border-t border-gray-200 dark:border-gray-800">
                <button
                  type="button"
                  onClick={() => setExpedienteSeleccionado(null)}
                  className="px-3.5 py-2 text-gray-600 dark:text-gray-400 hover:bg-gray-100 rounded-lg"
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 bg-purple-600 hover:bg-purple-700 text-white font-semibold rounded-lg"
                >
                  Emitir Resolución Definitiva
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL: CONFIRMAR CIERRE DEFINITIVO (LAB-010) */}
      {modalConfirmarCierre && jornadaActiva && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
          <div className="bg-white dark:bg-gray-900 rounded-2xl max-w-md w-full p-6 shadow-2xl border border-gray-200 dark:border-gray-800">
            <h3 className="text-lg font-bold text-gray-900 dark:text-gray-100 mb-1">
              Confirmar Cierre Definitivo de Jornada
            </h3>
            <p className="text-xs text-red-600 dark:text-red-400 mb-4 font-medium">
              Esta acción es irreversible. Se archivará la jornada, se calculará el snapshot canónico y se sellará con hash SHA-256 inmutable.
            </p>

            <form onSubmit={handleConfirmarCierreSubmit} className="space-y-3 text-xs">
              <div>
                <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Observaciones Finales de Cierre
                </label>
                <textarea
                  rows={2}
                  placeholder="Comentarios de cierre de tesorería..."
                  value={cierreObs}
                  onChange={(e) => setCierreObs(e.target.value)}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100"
                />
              </div>

              <div className="flex justify-end gap-2 pt-2 border-t border-gray-200 dark:border-gray-800">
                <button
                  type="button"
                  onClick={() => setModalConfirmarCierre(false)}
                  className="px-3.5 py-2 text-gray-600 dark:text-gray-400 hover:bg-gray-100 rounded-lg"
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white font-semibold rounded-lg"
                >
                  Confirmar y Sellar Cierre
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL: EGRESO RESERVADO ATÓMICO (SPRINT 3 FASE 0) */}
      {modalEgresoReservado && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
          <div className="bg-white dark:bg-gray-900 rounded-2xl max-w-md w-full p-6 shadow-2xl border border-gray-200 dark:border-gray-800">
            <h3 className="text-lg font-bold text-gray-900 dark:text-gray-100 mb-1">
              ⚡ Egreso Reservado Atómico (FASE 0)
            </h3>
            <p className="text-xs text-gray-500 mb-4">
              Aplica los fondos de la reserva y genera la salida monetaria correspondiente en una única transacción atómica.
            </p>

            <div className="p-3 bg-purple-50 dark:bg-purple-950/40 border border-purple-200 dark:border-purple-800 rounded text-xs mb-3">
              <span className="font-semibold block text-purple-900 dark:text-purple-200">
                Reserva: {modalEgresoReservado.beneficiary} ({modalEgresoReservado.fund_code})
              </span>
              <span className="text-purple-700 dark:text-purple-300">
                Saldo pendiente reservado: {modalEgresoReservado.pending_amount_formatted}
              </span>
            </div>

            <form onSubmit={handleEgresoReservadoSubmit} className="space-y-3 text-xs">
              <div>
                <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Monto del Egreso
                </label>
                <input
                  type="text"
                  required
                  data-testid="input-egreso-monto"
                  placeholder="0.00"
                  value={egresoMontoStr}
                  onChange={(e) => setEgresoMontoStr(e.target.value)}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100 font-mono text-sm"
                />
              </div>

              <div>
                <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Contrapartida Económica
                </label>
                <input
                  type="text"
                  required
                  data-testid="input-egreso-contrapartida"
                  placeholder="Ej: Distribuidora Central / AFIP"
                  value={egresoContrapartida}
                  onChange={(e) => setEgresoContrapartida(e.target.value)}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100"
                />
              </div>

              <div>
                <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Motivo
                </label>
                <input
                  type="text"
                  required
                  data-testid="input-egreso-motivo"
                  placeholder="Cancelación de factura..."
                  value={egresoMotivo}
                  onChange={(e) => setEgresoMotivo(e.target.value)}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100"
                />
              </div>

              <div>
                <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Referencia de Evidencia
                </label>
                <input
                  type="text"
                  data-testid="input-egreso-evidencia"
                  placeholder="FAC-2026-0001 (opcional)"
                  value={egresoEvidencia}
                  onChange={(e) => setEgresoEvidencia(e.target.value)}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100"
                />
              </div>

              <div className="flex justify-end gap-2 pt-2 border-t border-gray-200 dark:border-gray-800">
                <button
                  type="button"
                  onClick={() => setModalEgresoReservado(null)}
                  className="px-3.5 py-2 text-gray-600 dark:text-gray-400 hover:bg-gray-100 rounded-lg"
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold rounded-lg"
                >
                  Ejecutar Egreso Atómico
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL: REVERSIÓN INMUTABLE (LAB-011) */}
      {modalReversion && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
          <div className="bg-white dark:bg-gray-900 rounded-2xl max-w-md w-full p-6 shadow-2xl border border-gray-200 dark:border-gray-800">
            <h3 className="text-lg font-bold text-gray-900 dark:text-gray-100 mb-1">
              Ejecutar Reversión Inmutable
            </h3>
            <p className="text-xs text-gray-500 mb-3">
              Tipo: <span className="font-bold">{modalReversion.tipo}</span> | Destino: <span className="font-mono">{modalReversion.targetId}</span>
            </p>

            <form onSubmit={handleReversionSubmit} className="space-y-3 text-xs">
              <div>
                <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Motivo de la Reversión (Obligatorio)
                </label>
                <textarea
                  required
                  rows={2}
                  data-testid="input-rev-motivo"
                  placeholder="Justificación del contra-asiento..."
                  value={revMotivo}
                  onChange={(e) => setRevMotivo(e.target.value)}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100"
                />
              </div>

              <div>
                <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Referencia de Evidencia / Expediente
                </label>
                <input
                  type="text"
                  data-testid="input-rev-evidencia"
                  placeholder="Ej: EXP-2026-004 / ACTA-01"
                  value={revEvidencia}
                  onChange={(e) => setRevEvidencia(e.target.value)}
                  className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100"
                />
              </div>

              {modalReversion.tipo === "MOVEMENT" && (
                <div>
                  <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Monto Parcial en Pesos (dejar vacío para reversión total)
                  </label>
                  <input
                    type="text"
                    data-testid="input-rev-monto-parcial"
                    placeholder="0.00"
                    value={revMontoParcialStr}
                    onChange={(e) => setRevMontoParcialStr(e.target.value)}
                    className="w-full p-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-gray-900 dark:text-gray-100 font-mono"
                  />
                </div>
              )}

              <div className="flex justify-end gap-2 pt-2 border-t border-gray-200 dark:border-gray-800">
                <button
                  type="button"
                  onClick={() => setModalReversion(null)}
                  className="px-3.5 py-2 text-gray-600 dark:text-gray-400 hover:bg-gray-100 rounded-lg"
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 bg-red-600 hover:bg-red-700 text-white font-semibold rounded-lg"
                >
                  Confirmar Reversión
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL: VER SNAPSHOT CRIPTOGRÁFICO DE CIERRE */}
      {modalVerSnapshot && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
          <div className="bg-white dark:bg-gray-900 rounded-2xl max-w-2xl w-full p-6 shadow-2xl border border-gray-200 dark:border-gray-800 max-h-[85vh] flex flex-col">
            <div className="flex justify-between items-start border-b border-gray-100 dark:border-gray-800 pb-3 mb-3">
              <div>
                <h3 className="text-base font-bold text-gray-900 dark:text-gray-100">
                  Fotografía Criptográfica de Cierre — Jornada #{modalVerSnapshot.journal_id}
                </h3>
                <span className="text-[11px] text-gray-400">
                  Cerrado el {modalVerSnapshot.closed_at ? new Date(modalVerSnapshot.closed_at).toLocaleString("es-AR") : "—"} por {modalVerSnapshot.closed_by || "SISTEMA"}
                </span>
              </div>
              <button
                onClick={() => setModalVerSnapshot(null)}
                className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-lg font-bold"
              >
                ✕
              </button>
            </div>

            <div className="space-y-4 overflow-y-auto pr-1 text-xs">
              <div>
                <span className="text-[10px] uppercase font-bold text-gray-400 block mb-1">
                  Hash SHA-256 Canónico (Sellado Inmutable)
                </span>
                <div className="p-2.5 bg-gray-100 dark:bg-gray-800 rounded font-mono text-[11px] break-all select-all text-gray-900 dark:text-gray-100 border border-gray-200 dark:border-gray-700">
                  {modalVerSnapshot.snapshot_hash}
                </div>
              </div>

              {modalVerSnapshot.observations && (
                <div>
                  <span className="text-[10px] uppercase font-bold text-gray-400 block mb-1">
                    Observaciones Registradas
                  </span>
                  <p className="p-2 bg-gray-50 dark:bg-gray-800/40 rounded text-gray-700 dark:text-gray-300 italic">
                    {modalVerSnapshot.observations}
                  </p>
                </div>
              )}

              {modalVerSnapshot.snapshot_data && (
                <div>
                  <span className="text-[10px] uppercase font-bold text-gray-400 block mb-1">
                    Contenido Canónico del Snapshot
                  </span>
                  <pre className="p-3 bg-gray-900 text-emerald-400 font-mono text-[10px] rounded-lg overflow-x-auto max-h-64">
                    {JSON.stringify(modalVerSnapshot.snapshot_data, null, 2)}
                  </pre>
                </div>
              )}
            </div>

            <div className="flex justify-end pt-3 mt-3 border-t border-gray-100 dark:border-gray-800">
              <button
                type="button"
                onClick={() => setModalVerSnapshot(null)}
                className="px-4 py-2 bg-gray-200 dark:bg-gray-800 hover:bg-gray-300 text-gray-800 dark:text-gray-200 rounded-lg text-xs font-semibold"
              >
                Cerrar
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
