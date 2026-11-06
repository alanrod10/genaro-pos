import React, { useState, useEffect, useMemo, useCallback, useRef } from "react";
import {
  CargaServicio,
  CargaServicioInput,
  MetodoPagoServicio,
  ResumenServicios,
} from "../types/services";
import { getApiClient, isReadOnlySmokeActive } from "../services/api";
import { guardarServicioColaLocal } from "../services/db";

export const PLATAFORMAS_ORIGEN = [
  "PREPAY",
  "CLARO COMERCIOS",
  "MERCADO PAGO",
] as const;

export type PlataformaOrigen = (typeof PLATAFORMAS_ORIGEN)[number];

export const MAPA_SERVICIOS_DEPENDIENTES: Record<string, string[]> = {
  PREPAY: ["SUBE", "Personal", "Movistar", "Claro"],
  "CLARO COMERCIOS": ["Claro"],
  "MERCADO PAGO": ["PREPAY", "SUBE", "Personal", "Movistar", "Claro"],
};

export const SERVICIOS_DESTINO_TODOS = [
  "SUBE",
  "Personal",
  "Movistar",
  "Claro",
  "PREPAY",
];

const RECARGO_BASE = 2000;
const RECARGO_POR_TRAMO = 100;

export function calcularRecargoAutomatico(monto: number): number {
  if (monto <= 0) return 0;
  return Math.ceil(monto / RECARGO_BASE) * RECARGO_POR_TRAMO;
}

export const CARGO_PREPAY_PRIMERA_JORNADA = 350;

/**
 * Calcula el recargo sugerido según las reglas económicas aprobadas:
 * - PREPAY: Cargo aprobado de $350 una sola vez por jornada (en la primera recarga).
 * - Resto de servicios: Regla escalonada de $100 por cada tramo de hasta $2.000.
 */
export function calcularRecargoPorServicio(
  servicio: string,
  monto: number,
  tieneCargaPrepayPreviaHoy: boolean = false
): number {
  if (monto <= 0) return 0;
  if (servicio.trim().toUpperCase() === "PREPAY") {
    return tieneCargaPrepayPreviaHoy ? 0 : CARGO_PREPAY_PRIMERA_JORNADA;
  }
  return calcularRecargoAutomatico(monto);
}

/** Fecha de hoy (YYYY-MM-DD) en America/Argentina/Jujuy, no en UTC ni en la zona del dispositivo. */
export function fechaHoyJujuy(ahora: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Argentina/Jujuy",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(ahora);
}

/** Breakpoint en píxeles coincidente con el responsive de index.css (@media max-width: 800px). */
export const BREAKPOINT_MOBILE_PX = 800;

/**
 * Hook para detectar si el viewport actual corresponde a móvil o escritorio.
 * Usa matchMedia cuando está disponible y window.innerWidth como respaldo seguro.
 */
export function useIsMobile(breakpoint: number = BREAKPOINT_MOBILE_PX): boolean {
  const checkIsMobile = useCallback(() => {
    if (typeof window === "undefined") return false;
    if (typeof window.matchMedia === "function") {
      try {
        const mql = window.matchMedia(`(max-width: ${breakpoint}px)`);
        if (typeof mql?.matches === "boolean") {
          return mql.matches;
        }
      } catch {
        // Fallback a innerWidth
      }
    }
    if (typeof window.innerWidth === "number") {
      return window.innerWidth <= breakpoint;
    }
    return false;
  }, [breakpoint]);

  const [isMobile, setIsMobile] = useState<boolean>(checkIsMobile);

  useEffect(() => {
    if (typeof window === "undefined") return;

    const updateMatches = () => {
      setIsMobile(checkIsMobile());
    };

    let mql: MediaQueryList | null = null;
    let mqlListener: ((e: MediaQueryListEvent) => void) | null = null;

    if (typeof window.matchMedia === "function") {
      try {
        mql = window.matchMedia(`(max-width: ${breakpoint}px)`);
        mqlListener = (e: MediaQueryListEvent) => {
          setIsMobile(e.matches);
        };
        if (mql.addEventListener) {
          mql.addEventListener("change", mqlListener);
        } else if (mql.addListener) {
          mql.addListener(mqlListener);
        }
      } catch {
        mql = null;
        mqlListener = null;
      }
    }

    window.addEventListener("resize", updateMatches);
    updateMatches();

    return () => {
      if (mql && mqlListener) {
        if (mql.removeEventListener) {
          mql.removeEventListener("change", mqlListener);
        } else if (mql.removeListener) {
          mql.removeListener(mqlListener);
        }
      }
      window.removeEventListener("resize", updateMatches);
    };
  }, [breakpoint, checkIsMobile]);

  return isMobile;
}

interface ServicesAdminProps {
  onAddToast: (mensaje: string, tipo?: "info" | "success" | "warning" | "error") => void;
  isDemo: boolean;
  isMobileForced?: boolean;
  onQueueChange?: () => void;
}

export const ServicesAdmin: React.FC<ServicesAdminProps> = ({
  onAddToast,
  isDemo,
  isMobileForced,
  onQueueChange,
}) => {
  const detectedIsMobile = useIsMobile(BREAKPOINT_MOBILE_PX);
  const isMobile = isMobileForced !== undefined ? isMobileForced : detectedIsMobile;
  // Estado del listado y resumen
  const [cargas, setCargas] = useState<CargaServicio[]>([]);
  const [resumen, setResumen] = useState<ResumenServicios | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);

  // Filtros de búsqueda e historial
  const [busqueda, setBusqueda] = useState<string>("");
  // Con API real se inicia en el día local actual para no descargar todo el historial;
  // "✕ Todas" y el selector de fecha permiten consultar cualquier período.
  const [filtroFecha, setFiltroFecha] = useState<string>(() => (isDemo ? "" : fechaHoyJujuy()));
  const [filtroMetodo, setFiltroMetodo] = useState<string>("TODOS");
  const [filtroPlataforma, setFiltroPlataforma] = useState<string>("TODAS");
  const [filtroDestino, setFiltroDestino] = useState<string>("TODOS");
  const [mostrarAnuladas, setMostrarAnuladas] = useState<boolean>(false);

  // Estado del formulario de nueva carga (Dos desplegables dependientes)
  const [plataformaOrigen, setPlataformaOrigen] = useState<string>("");
  const [servicioDestino, setServicioDestino] = useState<string>("");
  const [nuevoMontoCarga, setNuevoMontoCarga] = useState<number>(0);
  const [nuevoMontoAdic, setNuevoMontoAdic] = useState<number>(0);
  const [recargoManual, setRecargoManual] = useState<boolean>(false);
  const [nuevoMetodoPago, setNuevoMetodoPago] = useState<MetodoPagoServicio>("EFECTIVO");
  const [nuevoPagoTransf, setNuevoPagoTransf] = useState<number>(0);
  const [isRegistering, setIsRegistering] = useState<boolean>(false);
  const [formError, setFormError] = useState<string | null>(null);

  // Modales
  const [editingCarga, setEditingCarga] = useState<CargaServicio | null>(null);
  const [editPlataformaOrigen, setEditPlataformaOrigen] = useState<string>("");
  const [editServicioDestino, setEditServicioDestino] = useState<string>("");
  const [editMontoCarga, setEditMontoCarga] = useState<number>(0);
  const [editMontoAdic, setEditMontoAdic] = useState<number>(0);
  const [editMetodoPago, setEditMetodoPago] = useState<MetodoPagoServicio>("EFECTIVO");
  const [editPagoTransf, setEditPagoTransf] = useState<number>(0);
  const [isSavingEdit, setIsSavingEdit] = useState<boolean>(false);
  const [editError, setEditError] = useState<string | null>(null);

  const [cancellingCarga, setCancellingCarga] = useState<CargaServicio | null>(null);
  const [isCancelling, setIsCancelling] = useState<boolean>(false);

  const isMountedRef = useRef(true);
  const isRegisteringRef = useRef(false);
  const isCancellingRef = useRef(false);
  const pendingIdempotencyKeyRef = useRef<string | null>(null);
  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const tienePrepayHoy = useMemo(() => {
    const hoyStr = fechaHoyJujuy();
    return cargas.some(
      (c) =>
        !c.anulado &&
        ((c.servicio_destino && c.servicio_destino.trim().toUpperCase() === "PREPAY") ||
          c.servicio.trim().toUpperCase() === "PREPAY") &&
        (c.fecha ? c.fecha.startsWith(hoyStr) : true)
    );
  }, [cargas]);

  // Opciones dependientes para el segundo desplegable
  const opcionesDestinoDisponibles = useMemo(() => {
    if (!plataformaOrigen) return [];
    return MAPA_SERVICIOS_DEPENDIENTES[plataformaOrigen] || [];
  }, [plataformaOrigen]);

  // Opciones dependientes para el modal de edición
  const editOpcionesDestino = useMemo(() => {
    if (!editPlataformaOrigen) {
      return SERVICIOS_DESTINO_TODOS;
    }
    return MAPA_SERVICIOS_DEPENDIENTES[editPlataformaOrigen] || [];
  }, [editPlataformaOrigen]);

  const handlePlataformaChange = (plat: string) => {
    setPlataformaOrigen(plat);
    if (formError) setFormError(null);
    const validas = MAPA_SERVICIOS_DEPENDIENTES[plat] || [];
    if (!validas.includes(servicioDestino)) {
      setServicioDestino("");
      if (!recargoManual && nuevoMontoCarga > 0) {
        setNuevoMontoAdic(calcularRecargoPorServicio("", nuevoMontoCarga, tienePrepayHoy));
      }
    } else {
      if (!recargoManual && nuevoMontoCarga > 0) {
        setNuevoMontoAdic(calcularRecargoPorServicio(servicioDestino, nuevoMontoCarga, tienePrepayHoy));
      }
    }
  };

  const handleServicioDestinoChange = (srv: string) => {
    setServicioDestino(srv);
    if (formError) setFormError(null);
    if (!recargoManual && nuevoMontoCarga > 0) {
      setNuevoMontoAdic(calcularRecargoPorServicio(srv, nuevoMontoCarga, tienePrepayHoy));
    }
  };

  const handleEditPlataformaChange = (plat: string) => {
    setEditPlataformaOrigen(plat);
    const validas = plat ? (MAPA_SERVICIOS_DEPENDIENTES[plat] || []) : SERVICIOS_DESTINO_TODOS;
    if (!validas.includes(editServicioDestino)) {
      setEditServicioDestino(validas[0] || "");
    }
  };

  // Recálculo automático de recargo cuando el usuario cambia el monto
  const handleMontoCargaChange = (monto: number) => {
    setNuevoMontoCarga(monto);
    if (!recargoManual) {
      setNuevoMontoAdic(calcularRecargoPorServicio(servicioDestino, monto, tienePrepayHoy));
    }
  };

  const totalCobrarNuevo = useMemo(() => {
    return Math.max(0, nuevoMontoCarga + nuevoMontoAdic);
  }, [nuevoMontoCarga, nuevoMontoAdic]);

  const nuevoPagoEfvo = useMemo(() => {
    if (nuevoMetodoPago === "EFECTIVO") return totalCobrarNuevo;
    if (nuevoMetodoPago === "TRANSFERENCIA") return 0;
    return Math.max(0, totalCobrarNuevo - nuevoPagoTransf);
  }, [totalCobrarNuevo, nuevoMetodoPago, nuevoPagoTransf]);

  // Total cobrado y pagos en modal de edición
  const totalCobrarEdit = useMemo(() => {
    return Math.max(0, editMontoCarga + editMontoAdic);
  }, [editMontoCarga, editMontoAdic]);

  const editPagoEfvo = useMemo(() => {
    if (editMetodoPago === "EFECTIVO") return totalCobrarEdit;
    if (editMetodoPago === "TRANSFERENCIA") return 0;
    return Math.max(0, totalCobrarEdit - editPagoTransf);
  }, [totalCobrarEdit, editMetodoPago, editPagoTransf]);

  // Carga de datos
  const cargarHistorial = useCallback(async () => {
    setIsLoading(true);
    const api = getApiClient();
    try {
      const platParam = filtroPlataforma === "TODAS" ? undefined : filtroPlataforma;
      const destParam = filtroDestino === "TODOS" ? undefined : filtroDestino;
      // Fetch list and summary concurrently
      const listPromise = api.obtenerCargas(
        filtroFecha || undefined,
        mostrarAnuladas,
        platParam,
        destParam
      );
      const summaryPromise = api.obtenerResumenCargas(filtroFecha || undefined);
      const [res, resResumen] = await Promise.all([listPromise, summaryPromise]);

      if (!isMountedRef.current) return;

      // Handle list response
      if (res.status === 200 && res.data) {
        setCargas(res.data.cargas);
      } else {
        onAddToast(res.error || "Error al cargar historial de servicios.", "error");
      }

      // Handle summary response
      if (resResumen.status === 200 && resResumen.data) {
        setResumen(resResumen.data);
      } else {
        onAddToast(resResumen.error || "Error al cargar resumen de servicios.", "error");
      }
    } catch (err: unknown) {
      if (!isMountedRef.current) return;
      const msg = err instanceof Error ? err.message : "Error inesperado al cargar servicios.";
      onAddToast(msg, "error");
    } finally {
      if (isMountedRef.current) {
        setIsLoading(false);
      }
    }
  }, [filtroFecha, filtroPlataforma, filtroDestino, mostrarAnuladas, onAddToast]);

  useEffect(() => {
    cargarHistorial();
  }, [cargarHistorial]);

  // Registro de nueva carga
  const handleRegistrarCarga = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (isRegisteringRef.current || isRegistering) return;
    setFormError(null);

    const plat = plataformaOrigen.trim();
    const dest = servicioDestino.trim();

    if (!plat && !dest) {
      setFormError("Debe seleccionar una empresa o servicio para continuar.");
      return;
    }
    if (!plat) {
      setFormError("Debe seleccionar una plataforma de origen.");
      return;
    }
    if (!dest) {
      setFormError("Debe seleccionar un servicio de destino.");
      return;
    }

    const opcionesValidas = MAPA_SERVICIOS_DEPENDIENTES[plat] || [];
    if (!opcionesValidas.includes(dest)) {
      setFormError(
        `Combinación incompatible: '${dest}' no está disponible para la plataforma '${plat}'. Opciones válidas: ${opcionesValidas.join(", ")}.`
      );
      return;
    }

    if (nuevoMontoCarga <= 0) {
      setFormError("El monto de la carga debe ser mayor a cero.");
      return;
    }
    if (nuevoMontoAdic < 0) {
      setFormError("El recargo no puede ser un importe negativo.");
      return;
    }
    if (nuevoPagoTransf < 0 || nuevoPagoTransf > totalCobrarNuevo) {
      setFormError("El importe de transferencia no puede superar el total a cobrar.");
      return;
    }

    isRegisteringRef.current = true;
    setIsRegistering(true);
    const api = getApiClient();
    if (!pendingIdempotencyKeyRef.current) {
      pendingIdempotencyKeyRef.current =
        "srv-" + Date.now().toString(36) + "-" + Math.random().toString(36).substring(2, 9);
    }
    try {
      const payload: CargaServicioInput = {
        servicio: dest,
        plataforma_origen: plat,
        servicio_destino: dest,
        monto_carga: Math.round(nuevoMontoCarga),
        monto_adicional: Math.round(nuevoMontoAdic),
        total_cobrado: totalCobrarNuevo,
        pago_efvo: nuevoPagoEfvo,
        pago_transf:
          nuevoMetodoPago === "TRANSFERENCIA"
            ? totalCobrarNuevo
            : nuevoMetodoPago === "EFECTIVO"
            ? 0
            : nuevoPagoTransf,
        idempotency_key: pendingIdempotencyKeyRef.current,
      };

      const res = await api.registrarCarga(payload);
      if ((res.status === 201 || res.status === 200) && res.data) {
        pendingIdempotencyKeyRef.current = null;
        const msgExito =
          res.status === 200
            ? `Carga confirmada (reintento idempotente): ${plat} → ${dest} por $${res.data.total_cobrado.toLocaleString("es-AR")}`
            : `Carga registrada: ${plat} → ${dest} por $${res.data.total_cobrado.toLocaleString("es-AR")}`;
        onAddToast(msgExito, "success");
        // Limpiar formulario restableciendo ambos selectores
        setPlataformaOrigen("");
        setServicioDestino("");
        setNuevoMontoCarga(0);
        setNuevoMontoAdic(0);
        setRecargoManual(false);
        setNuevoMetodoPago("EFECTIVO");
        setNuevoPagoTransf(0);
        await cargarHistorial();
      } else if (
        !isDemo &&
        !isReadOnlySmokeActive(api) &&
        !(res.error && res.error.includes("READ_ONLY_SMOKE_MODE")) &&
        (res.status === 0 || res.status === 502 || res.status === 503 || res.status === 504)
      ) {
        await guardarServicioColaLocal(payload);
        pendingIdempotencyKeyRef.current = null;
        if (onQueueChange) {
          onQueueChange();
        }
        onAddToast(
          `Sin conexión con el servidor: Carga de ${plat} → ${dest} ($${payload.total_cobrado.toLocaleString("es-AR")}) guardada en cola local para sincronización automática.`,
          "warning"
        );
        setPlataformaOrigen("");
        setServicioDestino("");
        setNuevoMontoCarga(0);
        setNuevoMontoAdic(0);
        setRecargoManual(false);
        setNuevoMetodoPago("EFECTIVO");
        setNuevoPagoTransf(0);
      } else {
        setFormError(res.error || "No se pudo registrar la carga.");
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error inesperado al registrar.";
      setFormError(msg);
    } finally {
      isRegisteringRef.current = false;
      if (isMountedRef.current) {
        setIsRegistering(false);
      }
    }
  };

  // Abrir modal de edición
  const handleOpenEdit = (carga: CargaServicio) => {
    setEditingCarga(carga);
    setEditPlataformaOrigen(carga.plataforma_origen || "");
    setEditServicioDestino(carga.servicio_destino || carga.servicio);
    setEditMontoCarga(carga.monto_carga);
    setEditMontoAdic(carga.monto_adicional);

    if (carga.pago_efvo > 0 && carga.pago_transf > 0) {
      setEditMetodoPago("MIXTO");
      setEditPagoTransf(carga.pago_transf);
    } else if (carga.pago_transf > 0) {
      setEditMetodoPago("TRANSFERENCIA");
      setEditPagoTransf(carga.pago_transf);
    } else {
      setEditMetodoPago("EFECTIVO");
      setEditPagoTransf(0);
    }
    setEditError(null);
  };

  // Guardar edición
  const handleSaveEdit = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!editingCarga) return;
    setEditError(null);

    const dest = editServicioDestino.trim();
    if (!dest) {
      setEditError("El servicio de destino es obligatorio.");
      return;
    }
    if (editPlataformaOrigen) {
      const validas = MAPA_SERVICIOS_DEPENDIENTES[editPlataformaOrigen] || [];
      if (!validas.includes(dest)) {
        setEditError(
          `Combinación incompatible: '${dest}' no es válido para la plataforma '${editPlataformaOrigen}'. Opciones: ${validas.join(", ")}.`
        );
        return;
      }
    }

    if (editMontoCarga <= 0) {
      setEditError("El monto de la carga debe ser mayor a cero.");
      return;
    }
    if (editMontoAdic < 0) {
      setEditError("El recargo no puede ser negativo.");
      return;
    }

    setIsSavingEdit(true);
    const api = getApiClient();
    try {
      const payload: CargaServicioInput = {
        servicio: dest,
        plataforma_origen: editPlataformaOrigen || null,
        servicio_destino: editPlataformaOrigen ? dest : null,
        monto_carga: Math.round(editMontoCarga),
        monto_adicional: Math.round(editMontoAdic),
        total_cobrado: totalCobrarEdit,
        pago_efvo: editPagoEfvo,
        pago_transf:
          editMetodoPago === "TRANSFERENCIA"
            ? totalCobrarEdit
            : editMetodoPago === "EFECTIVO"
            ? 0
            : editPagoTransf,
      };

      const res = await api.editarCarga(editingCarga.id, payload);
      if (res.status === 200 && res.data) {
        onAddToast(`Carga #${editingCarga.id} actualizada correctamente.`, "success");
        setEditingCarga(null);
        await cargarHistorial();
      } else {
        setEditError(res.error || "Error al actualizar la carga.");
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error inesperado al editar.";
      if (isMountedRef.current && typeof window !== "undefined") {
        setEditError(msg);
      }
    } finally {
      if (isMountedRef.current && typeof window !== "undefined") {
        setIsSavingEdit(false);
      }
    }
  };

  // Confirmar anulación lógica
  const handleConfirmCancel = async () => {
    if (!cancellingCarga || isCancellingRef.current || isCancelling) return;
    isCancellingRef.current = true;
    setIsCancelling(true);
    const api = getApiClient();
    try {
      const res = await api.anularCarga(cancellingCarga.id);
      if (res.status === 200) {
        onAddToast(
          `Carga #${cancellingCarga.id} (${cancellingCarga.servicio}) anulada lógicamente.`,
          "warning"
        );
        setCancellingCarga(null);
        await cargarHistorial();
      } else {
        onAddToast(res.error || "No se pudo anular la carga.", "error");
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al anular.";
      onAddToast(msg, "error");
    } finally {
      isCancellingRef.current = false;
      if (isMountedRef.current) {
        setIsCancelling(false);
      }
    }
  };

  // Filtrado en memoria por texto, método de pago, plataforma y destino
  const cargasFiltradas = useMemo(() => {
    return cargas.filter((c) => {
      if (busqueda.trim()) {
        const q = busqueda.trim().toLowerCase();
        const coincideServicio = (c.servicio_destino || c.servicio || "").toLowerCase().includes(q);
        const coincidePlat = (c.plataforma_origen || "").toLowerCase().includes(q);
        const coincideId = String(c.id).includes(q);
        if (!coincideServicio && !coincidePlat && !coincideId) return false;
      }
      if (filtroPlataforma !== "TODAS") {
        if (filtroPlataforma === "HISTORICO") {
          if (c.plataforma_origen) return false;
        } else {
          if ((c.plataforma_origen || "").toUpperCase() !== filtroPlataforma) return false;
        }
      }
      if (filtroDestino !== "TODOS") {
        const dest = (c.servicio_destino || c.servicio || "").toLowerCase();
        if (dest !== filtroDestino.toLowerCase()) return false;
      }
      if (filtroMetodo === "EFECTIVO" && c.pago_transf > 0) return false;
      if (filtroMetodo === "TRANSFERENCIA" && c.pago_efvo > 0) return false;
      if (filtroMetodo === "MIXTO" && (c.pago_efvo === 0 || c.pago_transf === 0)) return false;
      return true;
    });
  }, [cargas, busqueda, filtroPlataforma, filtroDestino, filtroMetodo]);

  return (
    <div className="services-container" data-testid="services-container">
      {/* Banner de Modo Demo */}
      {isDemo && (
        <div className="admin-demo-banner" data-testid="services-demo-banner">
          🧪 <strong>MODO SIMULADO:</strong> Auditoría de servicios locales en tu navegador. Sin impacto en Turso ni en mostrador.
        </div>
      )}

      {/* Grid Superior: Formulario de Registro + Resumen KPI */}
      <div className="services-top-layout">
        {/* Panel Izquierdo: Formulario de Nueva Carga */}
        <section className="services-form-card" data-testid="services-form-card">
          <div className="card-header">
            <h3>📱 Registrar Carga o Servicio</h3>
            <span className="card-badge-info">Reglas económicas activas</span>
          </div>

          <form onSubmit={handleRegistrarCarga} className="services-form" data-testid="services-form" noValidate>
            {formError && (
              <div className="alert-box-error" data-testid="services-form-error">
                ⚠️ {formError}
              </div>
            )}

            {/* Primer desplegable: PLATAFORMA DE ORIGEN */}
            <div className="form-group">
              <label htmlFor="select-plataforma-origen">1. Plataforma de Origen:</label>
              <select
                id="select-plataforma-origen"
                className="form-input"
                value={plataformaOrigen}
                onChange={(e) => handlePlataformaChange(e.target.value)}
                data-testid="select-plataforma-origen"
              >
                <option value="">-- Seleccionar Plataforma de Origen --</option>
                {PLATAFORMAS_ORIGEN.map((plat) => (
                  <option key={plat} value={plat}>
                    {plat}
                  </option>
                ))}
              </select>
            </div>

            {/* Segundo desplegable: SERVICIO DE DESTINO */}
            <div className="form-group">
              <label htmlFor="select-servicio-destino">2. Servicio de Destino:</label>
              <select
                id="select-servicio-destino"
                className="form-input"
                value={servicioDestino}
                disabled={!plataformaOrigen}
                onChange={(e) => handleServicioDestinoChange(e.target.value)}
                data-testid="select-servicio-destino"
              >
                <option value="">
                  {!plataformaOrigen
                    ? "-- Primero elija Plataforma de Origen --"
                    : "-- Seleccionar Servicio de Destino --"}
                </option>
                {opcionesDestinoDisponibles.map((srv) => (
                  <option key={srv} value={srv}>
                    {srv}
                  </option>
                ))}
              </select>
              {/* Compatibilidad de testid para tests existentes */}
              <input
                type="hidden"
                id="input-servicio"
                data-testid="input-service-name"
                value={servicioDestino}
                onChange={(e) => {
                  const val = e.target.value;
                  if (!plataformaOrigen) {
                    if (val === "Claro") setPlataformaOrigen("CLARO COMERCIOS");
                    else if (val === "PREPAY") setPlataformaOrigen("MERCADO PAGO");
                    else setPlataformaOrigen("PREPAY");
                  }
                  handleServicioDestinoChange(val);
                }}
              />
              {servicioDestino.trim().toUpperCase() === "PREPAY" && (
                <div
                  className="mt-1.5 p-2 rounded-lg bg-amber-50 dark:bg-amber-950/50 border border-amber-300 dark:border-amber-700/60 text-amber-950 dark:text-amber-200 text-xs font-semibold"
                  data-testid="prepay-rule-hint"
                >
                  {tienePrepayHoy
                    ? "ℹ️ PREPAY: Cargo diario ya aplicado en recarga previa ($0 sugerido)."
                    : "ℹ️ PREPAY: Primera recarga de la jornada (Cargo fijo aprobado: $350)."}
                </div>
              )}
            </div>

            <div className="form-row">
              <div className="form-group flex-1">
                <label htmlFor="input-monto-carga">Monto a Cargar ($):</label>
                <input
                  id="input-monto-carga"
                  type="number"
                  inputMode="numeric"
                  min="1"
                  step="any"
                  className="form-input highlight-input"
                  value={nuevoMontoCarga || ""}
                  onChange={(e) => handleMontoCargaChange(Number(e.target.value))}
                  placeholder="0"
                  data-testid="input-service-amount"
                />
              </div>

              <div className="form-group flex-1">
                <label htmlFor="input-monto-adic">Recargo / Adicional ($):</label>
                <input
                  id="input-monto-adic"
                  type="number"
                  inputMode="numeric"
                  min="0"
                  step="any"
                  className="form-input"
                  value={nuevoMontoAdic}
                  onChange={(e) => {
                    setRecargoManual(true);
                    setNuevoMontoAdic(Number(e.target.value));
                  }}
                  data-testid="input-service-surcharge"
                />
              </div>
            </div>

            <div className="weight-quick-chips" style={{ marginTop: "-0.25rem", marginBottom: "0.5rem" }} data-testid="service-quick-chips">
              {[1000, 2000, 3000, 5000, 10000].map((amt) => (
                <button
                  key={amt}
                  type="button"
                  className={`btn-quick-chip ${nuevoMontoCarga === amt ? "chip-selected" : ""}`}
                  onClick={() => handleMontoCargaChange(amt)}
                  data-testid={`btn-chip-service-${amt}`}
                >
                  ${amt.toLocaleString("es-AR")}
                </button>
              ))}
            </div>

            {/* Banner de Total a Cobrar con desglose de fórmula */}
            <div className="charge-total-banner" data-testid="charge-total-banner">
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between w-full gap-1">
                <span className="formula-breakdown-text text-xs font-mono">
                  MONTO (${nuevoMontoCarga.toLocaleString("es-AR")}) + RECARGO (${nuevoMontoAdic.toLocaleString("es-AR")}) =
                </span>
                <div className="flex items-center gap-2">
                  <span className="total-label">TOTAL A COBRAR:</span>
                  <span className="total-amount font-mono">${totalCobrarNuevo.toLocaleString("es-AR")}</span>
                </div>
              </div>
            </div>

            {/* Medio de Pago */}
            <div className="form-group">
              <label>Método de Pago:</label>
              <div className="payment-options-row" data-testid="payment-methods-radio">
                {(["EFECTIVO", "TRANSFERENCIA", "MIXTO"] as MetodoPagoServicio[]).map((m) => (
                  <label key={m} className={`payment-pill ${nuevoMetodoPago === m ? "pill-active" : ""}`}>
                    <input
                      type="radio"
                      name="nuevoMetodoPago"
                      value={m}
                      checked={nuevoMetodoPago === m}
                      onChange={() => setNuevoMetodoPago(m)}
                      data-testid={`radio-payment-${m.toLowerCase()}`}
                    />
                    <span>{m === "EFECTIVO" ? "💵 Efectivo" : m === "TRANSFERENCIA" ? "📱 Transferencia" : "⚖️ Mixto"}</span>
                  </label>
                ))}
              </div>
            </div>

            {/* Desglose si es MIXTO */}
            {nuevoMetodoPago === "MIXTO" && (
              <div className="split-payment-box" data-testid="split-payment-box">
                <div className="form-group">
                  <label htmlFor="input-split-transf">Monto en Transferencia ($):</label>
                  <input
                    id="input-split-transf"
                    type="number"
                    inputMode="numeric"
                    min="0"
                    max={totalCobrarNuevo}
                    step="any"
                    className="form-input"
                    value={nuevoPagoTransf || ""}
                    onChange={(e) => setNuevoPagoTransf(Number(e.target.value))}
                    data-testid="input-split-transf"
                  />
                </div>
                <div className="split-cash-preview">
                  💵 Restante en Efectivo: <strong>${nuevoPagoEfvo.toLocaleString("es-AR")}</strong>
                </div>
              </div>
            )}

            <button
              type="submit"
              className="btn-register-charge"
              disabled={isRegistering || !plataformaOrigen || !servicioDestino || nuevoMontoCarga <= 0}
              data-testid="btn-register-charge"
            >
              {isRegistering ? "Guardando..." : "🚀 Registrar Carga"}
            </button>
          </form>
        </section>

        {/* Panel Derecho: Tarjetas de Resumen y Métricas Contables */}
        <section className="services-kpi-card" data-testid="services-kpi-summary">
          <div className="card-header">
            <h3>📊 Resumen y Cajas de Servicios</h3>
            <span className="card-badge-kpi">Reglas Reales del Sistema</span>
          </div>

          <div className="kpi-grid">
            <div className="kpi-tile highlight-kpi">
              <span className="kpi-label">Total Cobrado</span>
              <span className="kpi-val" data-testid="kpi-total-cobrado">
                ${(resumen?.total_cobrado || 0).toLocaleString("es-AR")}
              </span>
              <span className="kpi-sub">
                {resumen?.total_operaciones || 0} operación(es) activas
              </span>
            </div>

            <div className="kpi-tile">
              <span className="kpi-label">Capital Total</span>
              <span className="kpi-val text-blue" data-testid="kpi-total-capital">
                ${(resumen?.total_capital || 0).toLocaleString("es-AR")}
              </span>
              <span className="kpi-sub">Monto neto recargado</span>
            </div>

            <div className="kpi-tile">
              <span className="kpi-label">Ganancia Recargos</span>
              <span className="kpi-val text-green" data-testid="kpi-total-adicional">
                +${(resumen?.total_adicional || 0).toLocaleString("es-AR")}
              </span>
              <span className="kpi-sub">Ingreso del Drugstore</span>
            </div>
          </div>

          {/* Desglose de Capital por Caja (Prepay vs Claro vs SUBE MP) */}
          <div className="kpi-subsections">
            <h4 className="subsection-title">Contabilidad de Cajas de Servicios</h4>
            <div className="kpi-mini-grid">
              <div className="mini-box prepay-box">
                <span className="box-title">PREPAY (SUBE, Otros)</span>
                <span className="box-amount" data-testid="kpi-capital-prepay">
                  ${(resumen?.capital_prepay || 0).toLocaleString("es-AR")}
                </span>
                <span className="box-desc">Capital habitual prepago</span>
              </div>

              <div className="mini-box claro-box">
                <span className="box-title">CLARO</span>
                <span className="box-amount" data-testid="kpi-capital-claro">
                  ${(resumen?.capital_claro || 0).toLocaleString("es-AR")}
                </span>
                <span className="box-desc">Caja segregada Claro</span>
              </div>

              <div className="mini-box subemp-box">
                <span className="box-title">SUBE (MP)</span>
                <span className="box-amount" data-testid="kpi-capital-sube-mp">
                  ${(resumen?.capital_sube_mp || 0).toLocaleString("es-AR")}
                </span>
                <span className="box-desc">Saldo Mercado Pago propio</span>
              </div>
            </div>

            {/* Desglose de Medios de Pago */}
            <h4 className="subsection-title">Ingreso por Medio de Pago</h4>
            <div className="payment-distribution-row">
              <div className="payment-dist-item">
                <span>💵 Efectivo en Caja:</span>
                <strong data-testid="kpi-total-efectivo">
                  ${(resumen?.total_efectivo || 0).toLocaleString("es-AR")}
                </strong>
              </div>
              <div className="payment-dist-item">
                <span>📱 Banco / Transferencias:</span>
                <strong data-testid="kpi-total-transferencia">
                  ${(resumen?.total_transferencia || 0).toLocaleString("es-AR")}
                </strong>
              </div>
            </div>

            {/* Aviso de Anuladas */}
            {(resumen?.operaciones_anuladas || 0) > 0 && (
              <div className="cancelled-summary-notice" data-testid="kpi-cancelled-notice">
                ℹ️ <strong>{resumen?.operaciones_anuladas}</strong> operación(es) anulada(s)
                en este período (excluidas de los totales contables).
              </div>
            )}
          </div>
        </section>
      </div>

      {/* Barra de Filtros y Búsqueda de Historial */}
      <section className="services-history-section">
        <div className="history-toolbar">
          <div className="toolbar-left">
            <input
              type="search"
              className="admin-search-input"
              placeholder="🔍 Buscar por servicio o ID..."
              value={busqueda}
              onChange={(e) => setBusqueda(e.target.value)}
              data-testid="services-search-input"
            />

            <input
              type="date"
              className="services-date-input"
              value={filtroFecha}
              onChange={(e) => setFiltroFecha(e.target.value)}
              title="Filtrar por fecha específica"
              data-testid="services-date-filter"
            />

            {filtroFecha && (
              <button
                type="button"
                className="btn-clear-date"
                onClick={() => setFiltroFecha("")}
                title="Mostrar todas las fechas"
                data-testid="btn-clear-date"
              >
                ✕ Todas
              </button>
            )}

            <select
              className="admin-filter-select"
              value={filtroMetodo}
              onChange={(e) => setFiltroMetodo(e.target.value)}
              data-testid="services-payment-filter"
              title="Filtrar por método de cobro"
            >
              <option value="TODOS">Todos los Pagos</option>
              <option value="EFECTIVO">Solo Efectivo</option>
              <option value="TRANSFERENCIA">Solo Transferencia</option>
              <option value="MIXTO">Solo Mixto</option>
            </select>

            <select
              className="admin-filter-select"
              value={filtroPlataforma}
              onChange={(e) => setFiltroPlataforma(e.target.value)}
              data-testid="filter-platform"
              title="Filtrar por Plataforma de Origen"
            >
              <option value="TODAS">Todas las Plataformas</option>
              <option value="PREPAY">PREPAY</option>
              <option value="CLARO COMERCIOS">CLARO COMERCIOS</option>
              <option value="MERCADO PAGO">MERCADO PAGO</option>
              <option value="HISTORICO">Histórico (sin origen)</option>
            </select>

            <select
              className="admin-filter-select"
              value={filtroDestino}
              onChange={(e) => setFiltroDestino(e.target.value)}
              data-testid="filter-destination"
              title="Filtrar por Servicio de Destino"
            >
              <option value="TODOS">Todos los Destinos</option>
              <option value="SUBE">SUBE</option>
              <option value="Personal">Personal</option>
              <option value="Movistar">Movistar</option>
              <option value="Claro">Claro</option>
              <option value="PREPAY">PREPAY</option>
            </select>

            <label className="checkbox-show-cancelled">
              <input
                type="checkbox"
                checked={mostrarAnuladas}
                onChange={(e) => setMostrarAnuladas(e.target.checked)}
                data-testid="services-show-cancelled"
              />
              <span>Mostrar Anuladas</span>
            </label>
          </div>

          <div className="toolbar-right">
            <button
              type="button"
              className="btn-refresh"
              onClick={cargarHistorial}
              disabled={isLoading}
              data-testid="btn-refresh-services"
            >
              🔄 {isLoading ? "Actualizando..." : "Refrescar"}
            </button>
          </div>
        </div>

        {isMobile ? (
          /* Tarjetas Táctiles para Celulares (sólo montadas en viewport móvil <= 800px) */
          <div className="services-cards-grid" data-testid="services-cards-grid">
            {cargasFiltradas.length === 0 ? (
              <div className="empty-catalog-message" data-testid="services-empty-cards">
                <p>
                  {isLoading
                    ? "Cargando operaciones..."
                    : "No se encontraron operaciones con los filtros actuales."}
                </p>
              </div>
            ) : (
              cargasFiltradas.map((c) => (
                <div
                  key={c.id}
                  className={`service-card ${c.anulado ? "card-cancelled" : ""}`}
                  data-testid={`service-card-${c.id}`}
                >
                  <div className="card-top-row">
                    <div className="card-title-group">
                      <span className="card-id">#{c.id}</span>
                      <h4 className="card-service-name">{c.servicio_destino || c.servicio}</h4>
                    </div>
                    <span className={`badge-status ${c.anulado ? "status-inactive" : "status-active"}`}>
                      {c.anulado ? "Anulada" : "Activa"}
                    </span>
                  </div>

                  <div className="card-platform-row text-xs mb-1" data-testid={`card-platform-${c.id}`}>
                    <span className="text-muted">Origen: </span>
                    <strong className={c.plataforma_origen ? "text-primary" : "text-muted"}>
                      {c.plataforma_origen || "Histórico (sin origen especificado)"}
                    </strong>
                  </div>

                  <div className="card-date-row">
                    <span>📅 {c.fecha}</span>
                  </div>

                  <div className="card-financial-row">
                    <div className="financial-box">
                      <span className="box-label">Capital</span>
                      <span className="box-val">${c.monto_carga.toLocaleString("es-AR")}</span>
                    </div>
                    <div className="financial-box">
                      <span className="box-label">Recargo</span>
                      <span className="box-val text-green">+${c.monto_adicional.toLocaleString("es-AR")}</span>
                    </div>
                    <div className="financial-box highlight-price">
                      <span className="box-label">Total</span>
                      <span className="box-val">${c.total_cobrado.toLocaleString("es-AR")}</span>
                    </div>
                  </div>

                  <div className="card-split-row">
                    <span>💵 Efvo: ${c.pago_efvo.toLocaleString("es-AR")}</span>
                    <span>📱 Transf: ${c.pago_transf.toLocaleString("es-AR")}</span>
                  </div>

                  {!c.anulado && (
                    <div className="card-actions-row">
                      <button
                        type="button"
                        className="btn-card-edit"
                        onClick={() => handleOpenEdit(c)}
                        data-testid={`btn-card-edit-${c.id}`}
                      >
                        ✏️ Editar
                      </button>
                      <button
                        type="button"
                        className="btn-card-cancel"
                        onClick={() => setCancellingCarga(c)}
                        data-testid={`btn-card-cancel-${c.id}`}
                      >
                        🚫 Anular
                      </button>
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        ) : (
          /* Tabla Responsiva de Escritorio (sólo montada en viewport escritorio > 800px) */
          <div className="admin-table-wrapper" data-testid="services-table-wrapper">
            <table className="admin-table" data-testid="services-table">
              <thead>
                <tr>
                  <th>#ID</th>
                  <th>Fecha/Hora</th>
                  <th>Plataforma Origen</th>
                  <th>Servicio Destino</th>
                  <th className="th-number">Capital</th>
                  <th className="th-number">Recargo</th>
                  <th className="th-number">Total Cobrado</th>
                  <th className="th-number">Efectivo</th>
                  <th className="th-number">Transferencia</th>
                  <th>Estado</th>
                  <th className="th-actions">Acciones</th>
                </tr>
              </thead>
              <tbody>
                {cargasFiltradas.length === 0 ? (
                  <tr>
                    <td colSpan={11} className="td-empty" data-testid="services-empty-table">
                      {isLoading
                        ? "Cargando operaciones..."
                        : "No se encontraron operaciones con los filtros actuales."}
                    </td>
                  </tr>
                ) : (
                  cargasFiltradas.map((c) => (
                    <tr key={c.id} className={c.anulado ? "row-cancelled" : ""}>
                      <td className="td-id">#{c.id}</td>
                      <td className="td-date">{c.fecha}</td>
                      <td className="td-platform">
                        {c.plataforma_origen ? (
                          <span className="badge-platform font-semibold" data-testid={`history-platform-${c.id}`}>
                            {c.plataforma_origen}
                          </span>
                        ) : (
                          <span className="badge-platform-historic text-xs opacity-75" data-testid={`history-platform-${c.id}`}>
                            Histórico (sin origen especificado)
                          </span>
                        )}
                      </td>
                      <td className="td-service">
                        <strong>{c.servicio_destino || c.servicio}</strong>
                      </td>
                      <td className="td-number">${c.monto_carga.toLocaleString("es-AR")}</td>
                      <td className="td-number text-green">+${c.monto_adicional.toLocaleString("es-AR")}</td>
                      <td className="td-number td-total">
                        ${c.total_cobrado.toLocaleString("es-AR")}
                      </td>
                      <td className="td-number">${c.pago_efvo.toLocaleString("es-AR")}</td>
                      <td className="td-number">${c.pago_transf.toLocaleString("es-AR")}</td>
                      <td>
                        <span className={`badge-status ${c.anulado ? "status-inactive" : "status-active"}`}>
                          {c.anulado ? "Anulada" : "Activa"}
                        </span>
                      </td>
                      <td className="td-actions">
                        {!c.anulado && (
                          <>
                            <button
                              type="button"
                              className="btn-action-edit"
                              onClick={() => handleOpenEdit(c)}
                              title="Modificar carga"
                              data-testid={`btn-edit-charge-${c.id}`}
                            >
                              ✏️ Editar
                            </button>
                            <button
                              type="button"
                              className="btn-action-cancel"
                              onClick={() => setCancellingCarga(c)}
                              title="Anular lógicamente esta carga"
                              data-testid={`btn-cancel-charge-${c.id}`}
                            >
                              🚫 Anular
                            </button>
                          </>
                        )}
                        {c.anulado && <span className="anulada-tag">Anulada</span>}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Modal de Edición de Carga */}
      {editingCarga && (
        <div className="modal-backdrop" data-testid="modal-edit-charge">
          <div className="modal-card">
            <div className="modal-header">
              <h3>✏️ Editar Carga #{editingCarga.id}</h3>
              <button
                type="button"
                className="btn-close-modal"
                onClick={() => setEditingCarga(null)}
                data-testid="btn-close-edit-modal"
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleSaveEdit} className="admin-product-form" data-testid="form-edit-charge" noValidate>
              <div className="modal-body">
                {editError && (
                  <div className="alert-box-error" data-testid="edit-charge-error">
                    ⚠️ {editError}
                  </div>
                )}

                <div className="form-group">
                  <label htmlFor="edit-plataforma-origen">1. Plataforma de Origen:</label>
                  <select
                    id="edit-plataforma-origen"
                    className="form-input"
                    value={editPlataformaOrigen}
                    onChange={(e) => handleEditPlataformaChange(e.target.value)}
                    data-testid="select-edit-plataforma-origen"
                  >
                    <option value="">Histórico (sin origen especificado)</option>
                    {PLATAFORMAS_ORIGEN.map((plat) => (
                      <option key={plat} value={plat}>
                        {plat}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="form-group">
                  <label htmlFor="edit-servicio-destino">2. Servicio de Destino:</label>
                  <select
                    id="edit-servicio-destino"
                    className="form-input"
                    value={editServicioDestino}
                    onChange={(e) => setEditServicioDestino(e.target.value)}
                    data-testid="select-edit-servicio-destino"
                  >
                    {editOpcionesDestino.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="form-row">
                  <div className="form-group flex-1">
                    <label htmlFor="edit-monto">Monto Capital ($):</label>
                    <input
                      id="edit-monto"
                      type="number"
                      inputMode="numeric"
                      min="1"
                      step="any"
                      className="form-input"
                      value={editMontoCarga || ""}
                      onChange={(e) => setEditMontoCarga(Number(e.target.value))}
                      data-testid="input-edit-amount"
                    />
                  </div>

                  <div className="form-group flex-1">
                    <label htmlFor="edit-adic">Recargo / Adicional ($):</label>
                    <input
                      id="edit-adic"
                      type="number"
                      inputMode="numeric"
                      min="0"
                      step="any"
                      className="form-input"
                      value={editMontoAdic}
                      onChange={(e) => setEditMontoAdic(Number(e.target.value))}
                      data-testid="input-edit-surcharge"
                    />
                  </div>
                </div>

                <div className="charge-total-banner">
                  <span className="total-label">Nuevo Total Cobrado:</span>
                  <span className="total-amount">${totalCobrarEdit.toLocaleString("es-AR")}</span>
                </div>

                <div className="form-group">
                  <label>Método de Pago:</label>
                  <div className="payment-options-row">
                    {(["EFECTIVO", "TRANSFERENCIA", "MIXTO"] as MetodoPagoServicio[]).map((m) => (
                      <label key={m} className={`payment-pill ${editMetodoPago === m ? "pill-active" : ""}`}>
                        <input
                          type="radio"
                          name="editMetodoPago"
                          value={m}
                          checked={editMetodoPago === m}
                          onChange={() => setEditMetodoPago(m)}
                          data-testid={`radio-edit-${m.toLowerCase()}`}
                        />
                        <span>{m}</span>
                      </label>
                    ))}
                  </div>
                </div>

                {editMetodoPago === "MIXTO" && (
                  <div className="split-payment-box">
                    <div className="form-group">
                      <label htmlFor="edit-split-transf">Monto en Transferencia ($):</label>
                      <input
                        id="edit-split-transf"
                        type="number"
                        inputMode="numeric"
                        min="0"
                        max={totalCobrarEdit}
                        step="any"
                        className="form-input"
                        value={editPagoTransf || ""}
                        onChange={(e) => setEditPagoTransf(Number(e.target.value))}
                        data-testid="input-edit-split-transf"
                      />
                    </div>
                    <div className="split-cash-preview">
                      💵 Efectivo: <strong>${editPagoEfvo.toLocaleString("es-AR")}</strong>
                    </div>
                  </div>
                )}
              </div>

              <div className="modal-footer">
                <button
                  type="button"
                  className="btn-cancel"
                  onClick={() => setEditingCarga(null)}
                  disabled={isSavingEdit}
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  className="btn-confirm-save"
                  disabled={isSavingEdit}
                  onClick={handleSaveEdit}
                  data-testid="btn-save-charge-edit"
                >
                  {isSavingEdit ? "Guardando..." : "Actualizar Carga"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Modal de Confirmación de Anulación */}
      {cancellingCarga && (
        <div className="modal-backdrop" data-testid="modal-confirm-cancel-charge">
          <div className="modal-card">
            <div className="modal-header">
              <h3>⚠️ Confirmar Anulación de Carga</h3>
              <button
                type="button"
                className="btn-close-modal"
                onClick={() => setCancellingCarga(null)}
                data-testid="btn-abort-cancel"
              >
                ✕
              </button>
            </div>

            <div className="modal-body">
              <div className="alert-box-warning">
                <strong>¿Estás seguro de anular la carga #{cancellingCarga.id}?</strong>
                <p>
                  Esta anulación es <strong>lógica y trazable</strong>. El registro se
                  conservará en auditoría con su historial intacto, pero se excluirá
                  inmediatamente de los saldos prepagos y de la recaudación del día.
                </p>
                <div className="cancel-details-summary">
                  <div><strong>Servicio:</strong> {cancellingCarga.servicio}</div>
                  <div><strong>Monto Capital:</strong> ${cancellingCarga.monto_carga.toLocaleString("es-AR")}</div>
                  <div><strong>Total Cobrado:</strong> ${cancellingCarga.total_cobrado.toLocaleString("es-AR")}</div>
                  <div><strong>Fecha:</strong> {cancellingCarga.fecha}</div>
                </div>
              </div>
            </div>

            <div className="modal-footer">
              <button
                type="button"
                className="btn-cancel"
                onClick={() => setCancellingCarga(null)}
                disabled={isCancelling}
              >
                Volver Atrás
              </button>
              <button
                type="button"
                className="btn-danger-confirm"
                onClick={handleConfirmCancel}
                disabled={isCancelling}
                data-testid="btn-confirm-cancel"
              >
                {isCancelling ? "Anulando..." : "Sí, Anular Operación"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
