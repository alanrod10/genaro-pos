import React, { useState, useEffect, useMemo, useCallback, useRef } from "react";
import {
  ArqueoDiario,
  CierreDiario,
  Gasto,
  GastoInput,
  MetodoPagoGasto,
  ResumenGastos,
} from "../types/expenses";
import { getApiClient, isReadOnlySmokeActive, obtenerSesionGuardada } from "../services/api";
import { guardarGastoColaLocal } from "../services/db";

const CATEGORIAS_GASTOS = [
  "Proveedores",
  "Limpieza",
  "Servicios del local",
  "Comida",
  "Mantenimiento",
  "Retiro personal",
  "General",
];

interface ExpensesCashAdminProps {
  onAddToast: (mensaje: string, tipo?: "info" | "success" | "warning" | "error") => void;
  isDemo: boolean;
  onQueueChange?: () => void;
}

export const ExpensesCashAdmin: React.FC<ExpensesCashAdminProps> = ({
  onAddToast,
  isDemo,
  onQueueChange,
}) => {
  // Pestaña activa dentro del módulo
  const [activeTab, setActiveTab] = useState<"gastos" | "arqueo">("gastos");

  // ============================================================================
  // ESTADO DE GASTOS
  // ============================================================================
  const [gastos, setGastos] = useState<Gasto[]>([]);
  const [resumenGastos, setResumenGastos] = useState<ResumenGastos | null>(null);
  const [isLoadingGastos, setIsLoadingGastos] = useState<boolean>(false);

  // Filtros
  const [busqueda, setBusqueda] = useState<string>("");
  const [filtroFecha, setFiltroFecha] = useState<string>("");
  const [filtroCategoria, setFiltroCategoria] = useState<string>("TODAS");
  const [mostrarAnulados, setMostrarAnulados] = useState<boolean>(false);

  // Formulario alta de gasto
  const [nuevoDetalle, setNuevoDetalle] = useState<string>("");
  const [nuevoMonto, setNuevoMonto] = useState<number>(0);
  const [nuevaCategoria, setNuevaCategoria] = useState<string>("General");
  const [nuevoMetodoPago, setNuevoMetodoPago] = useState<MetodoPagoGasto>("EFECTIVO");
  const [nuevoOrigen, setNuevoOrigen] = useState<string>("Caja diaria");
  const [isRegisteringGasto, setIsRegisteringGasto] = useState<boolean>(false);
  const [formGastoError, setFormGastoError] = useState<string | null>(null);

  // Modal edición de gasto
  const [editingGasto, setEditingGasto] = useState<Gasto | null>(null);
  const [editDetalle, setEditDetalle] = useState<string>("");
  const [editMonto, setEditMonto] = useState<number>(0);
  const [editCategoria, setEditCategoria] = useState<string>("General");
  const [editMetodoPago, setEditMetodoPago] = useState<MetodoPagoGasto>("EFECTIVO");
  const [isSavingEdit, setIsSavingEdit] = useState<boolean>(false);
  const [editError, setEditError] = useState<string | null>(null);

  // Modal anulación de gasto
  const [cancellingGasto, setCancellingGasto] = useState<Gasto | null>(null);
  const [cancelMotivo, setCancelMotivo] = useState<string>("");
  const [isCancelling, setIsCancelling] = useState<boolean>(false);

  // ============================================================================
  // ESTADO DE ARQUEO Y CIERRES
  // ============================================================================
  const [fechaArqueo, setFechaArqueo] = useState<string>(() => {
    const now = new Date();
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  });
  const [arqueo, setArqueo] = useState<ArqueoDiario | null>(null);
  const [isLoadingArqueo, setIsLoadingArqueo] = useState<boolean>(false);

  // Formulario cierre diario
  const [efectivoContado, setEfectivoContado] = useState<number | "">("");
  const [observacionesCierre, setObservacionesCierre] = useState<string>("");
  const [cerradoPor, setCerradoPor] = useState<string>(() => {
    const sesion = obtenerSesionGuardada();
    const display = sesion?.user?.display_name;
    if (display === "Alan" || display === "Yilda") return display;
    const uname = sesion?.user?.username?.toLowerCase();
    if (uname === "yilda") return "Yilda";
    return "Alan";
  });
  const [isSavingCierre, setIsSavingCierre] = useState<boolean>(false);
  const [cierrePrevio, setCierrePrevio] = useState<CierreDiario | null>(null);
  const [historialCierres, setHistorialCierres] = useState<CierreDiario[]>([]);

  const isMountedRef = useRef(true);
  const submittingGastoRef = useRef<boolean>(false);
  const submittingCierreRef = useRef<boolean>(false);
  const pendingIdempotencyKeyRef = useRef<string | null>(null);
  const lastGastoSignatureRef = useRef<string | null>(null);
  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  // ============================================================================
  // CARGA DE DATOS DE GASTOS
  // ============================================================================
  const cargarGastos = useCallback(async () => {
    setIsLoadingGastos(true);
    const api = getApiClient();
    try {
      const res = await api.obtenerGastos(
        filtroFecha || undefined,
        filtroCategoria !== "TODAS" ? filtroCategoria : undefined,
        mostrarAnulados
      );
      if (!isMountedRef.current) return;
      if (res.status === 200 && res.data) {
        setGastos(res.data.gastos);
      } else {
        onAddToast(res.error || "Error al cargar gastos.", "error");
      }

      const resResumen = await api.obtenerResumenGastos(filtroFecha || undefined);
      if (!isMountedRef.current) return;
      if (resResumen.status === 200 && resResumen.data) {
        setResumenGastos(resResumen.data);
      }
    } catch (err: unknown) {
      if (!isMountedRef.current) return;
      const msg = err instanceof Error ? err.message : "Error inesperado al cargar gastos.";
      onAddToast(msg, "error");
    } finally {
      if (isMountedRef.current) {
        setIsLoadingGastos(false);
      }
    }
  }, [filtroFecha, filtroCategoria, mostrarAnulados, onAddToast]);

  useEffect(() => {
    if (activeTab === "gastos") {
      cargarGastos();
    }
  }, [activeTab, cargarGastos]);

  // ============================================================================
  // CARGA DE ARQUEO Y CIERRES
  // ============================================================================
  const cargarArqueoYCierres = useCallback(async () => {
    setIsLoadingArqueo(true);
    const api = getApiClient();
    try {
      const resArqueo = await api.obtenerArqueoDiario(fechaArqueo);
      if (!isMountedRef.current) return;
      if (resArqueo.status === 200 && resArqueo.data) {
        setArqueo(resArqueo.data);
      }

      const resCierre = await api.obtenerCierreDiario(fechaArqueo);
      if (!isMountedRef.current) return;
      if (resCierre.status === 200 && resCierre.data) {
        setCierrePrevio(resCierre.data);
        setEfectivoContado(resCierre.data.efectivo_contado);
        setObservacionesCierre(resCierre.data.observaciones || "");
      } else {
        setCierrePrevio(null);
        setEfectivoContado("");
        setObservacionesCierre("");
      }

      const resListaCierres = await api.listarCierres();
      if (!isMountedRef.current) return;
      if (resListaCierres.status === 200 && resListaCierres.data) {
        setHistorialCierres(resListaCierres.data);
      }
    } catch (err: unknown) {
      if (!isMountedRef.current) return;
      const msg = err instanceof Error ? err.message : "Error al consultar arqueo.";
      onAddToast(msg, "error");
    } finally {
      if (isMountedRef.current) {
        setIsLoadingArqueo(false);
      }
    }
  }, [fechaArqueo, onAddToast]);

  useEffect(() => {
    if (activeTab === "arqueo") {
      cargarArqueoYCierres();
    }
  }, [activeTab, cargarArqueoYCierres]);

  // ============================================================================
  // REGISTRAR GASTO
  // ============================================================================
  const handleRegistrarGasto = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (submittingGastoRef.current || isRegisteringGasto) return;
    setFormGastoError(null);

    const det = nuevoDetalle.trim();
    if (!det) {
      setFormGastoError("El concepto o detalle del gasto es obligatorio.");
      return;
    }
    if (nuevoMonto <= 0) {
      setFormGastoError("El importe del gasto debe ser mayor a $0.");
      return;
    }

    submittingGastoRef.current = true;
    setIsRegisteringGasto(true);
    const api = getApiClient();
    const montoRedondeado = Math.round(nuevoMonto);
    const currentSignature = `${det}|${montoRedondeado}|${nuevaCategoria}|${nuevoOrigen}|${nuevoMetodoPago}`;
    if (!pendingIdempotencyKeyRef.current || lastGastoSignatureRef.current !== currentSignature) {
      pendingIdempotencyKeyRef.current =
        "gst-" + Date.now().toString(36) + "-" + Math.random().toString(36).substring(2, 9);
      lastGastoSignatureRef.current = currentSignature;
    }
    try {
      const payload: GastoInput = {
        detalle: det,
        monto_salida: montoRedondeado,
        tipo: nuevaCategoria,
        origen_fondos: nuevoOrigen,
        metodo_pago: nuevoMetodoPago,
        idempotency_key: pendingIdempotencyKeyRef.current,
      };

      const res = await api.registrarGasto(payload);
      if ((res.status === 201 || res.status === 200) && res.data) {
        pendingIdempotencyKeyRef.current = null;
        lastGastoSignatureRef.current = null;
        const msgExito =
          res.status === 200
            ? `Gasto confirmado (reintento idempotente): ${res.data.detalle} ($${res.data.monto_salida.toLocaleString("es-AR")})`
            : `Gasto registrado: ${res.data.detalle} ($${res.data.monto_salida.toLocaleString("es-AR")})`;
        onAddToast(msgExito, "success");
        setNuevoDetalle("");
        setNuevoMonto(0);
        setNuevaCategoria("General");
        await cargarGastos();
      } else if (
        !isDemo &&
        !isReadOnlySmokeActive(api) &&
        !(res.error && res.error.includes("READ_ONLY_SMOKE_MODE")) &&
        (res.status === 0 || res.status === 502 || res.status === 503 || res.status === 504)
      ) {
        await guardarGastoColaLocal(payload);
        pendingIdempotencyKeyRef.current = null;
        lastGastoSignatureRef.current = null;
        if (onQueueChange) {
          onQueueChange();
        }
        onAddToast(
          `Sin conexión con el servidor: Gasto "${payload.detalle}" ($${payload.monto_salida.toLocaleString("es-AR")}) guardado en cola local para sincronización automática.`,
          "warning"
        );
        setNuevoDetalle("");
        setNuevoMonto(0);
        setNuevaCategoria("General");
      } else {
        setFormGastoError(res.error || "Error al registrar el gasto.");
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error inesperado.";
      setFormGastoError(msg);
    } finally {
      submittingGastoRef.current = false;
      if (isMountedRef.current) {
        setIsRegisteringGasto(false);
      }
    }
  };

  // ============================================================================
  // EDITAR GASTO
  // ============================================================================
  const handleOpenEditGasto = (gasto: Gasto) => {
    setEditingGasto(gasto);
    setEditDetalle(gasto.detalle);
    setEditMonto(gasto.monto_salida);
    setEditCategoria(gasto.tipo);
    setEditMetodoPago(gasto.metodo_pago);
    setEditError(null);
  };

  const handleSaveEditGasto = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!editingGasto) return;
    setEditError(null);

    const det = editDetalle.trim();
    if (!det) {
      setEditError("El concepto del gasto no puede estar vacío.");
      return;
    }
    if (editMonto <= 0) {
      setEditError("El importe del gasto debe ser mayor a cero.");
      return;
    }

    setIsSavingEdit(true);
    const api = getApiClient();
    try {
      const payload: GastoInput = {
        detalle: det,
        monto_salida: Math.round(editMonto),
        tipo: editCategoria,
        metodo_pago: editMetodoPago,
      };

      const res = await api.editarGasto(editingGasto.id, payload);
      if (res.status === 200 && res.data) {
        onAddToast(`Gasto #${editingGasto.id} actualizado con éxito.`, "success");
        setEditingGasto(null);
        await cargarGastos();
      } else {
        setEditError(res.error || "Error al editar gasto.");
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error inesperado al editar.";
      setEditError(msg);
    } finally {
      setIsSavingEdit(false);
    }
  };

  // ============================================================================
  // ANULAR GASTO
  // ============================================================================
  const handleConfirmCancelGasto = async () => {
    if (!cancellingGasto) return;
    setIsCancelling(true);
    const api = getApiClient();
    try {
      const res = await api.anularGasto(cancellingGasto.id, cancelMotivo);
      if (res.status === 200) {
        onAddToast(`Gasto #${cancellingGasto.id} anulado lógicamente.`, "warning");
        setCancellingGasto(null);
        setCancelMotivo("");
        await cargarGastos();
      } else {
        onAddToast(res.error || "Error al anular gasto.", "error");
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error inesperado al anular.";
      onAddToast(msg, "error");
    } finally {
      setIsCancelling(false);
    }
  };

  // ============================================================================
  // GUARDAR CIERRE DIARIO
  // ============================================================================
  const diferenciaArqueo = useMemo(() => {
    if (efectivoContado === "" || !arqueo) return 0;
    return Number(efectivoContado) - arqueo.efectivo_esperado;
  }, [efectivoContado, arqueo]);

  const handleConfirmCierre = async () => {
    if (!arqueo || submittingCierreRef.current || isSavingCierre) return;
    if (efectivoContado === "") {
      onAddToast("Debe ingresar el efectivo contado físicamente.", "warning");
      return;
    }

    submittingCierreRef.current = true;
    setIsSavingCierre(true);
    const api = getApiClient();
    try {
      const payload: CierreDiario = {
        fecha: fechaArqueo,
        efectivo_esperado: arqueo.efectivo_esperado,
        efectivo_contado: Number(efectivoContado),
        diferencia: diferenciaArqueo,
        total_ventas_efvo: arqueo.ventas_efvo,
        total_ventas_transf: arqueo.ventas_transf,
        total_servicios_efvo: arqueo.servicios_efvo,
        total_servicios_transf: arqueo.servicios_transf,
        total_gastos_efvo: arqueo.gastos_efvo,
        total_gastos_transf: arqueo.gastos_transf,
        total_banco: arqueo.total_banco,
        observaciones: observacionesCierre.trim(),
        cerrado_por: cerradoPor,
      };

      const res = await api.registrarCierreDiario(payload);
      if ((res.status === 201 || res.status === 200) && res.data) {
        onAddToast(
          `Cierre del día ${fechaArqueo} guardado exitosamente (${cerradoPor}).`,
          "success"
        );
        setCierrePrevio(res.data);
        await cargarArqueoYCierres();
      } else {
        onAddToast(res.error || "Error al guardar cierre.", "error");
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al registrar cierre.";
      onAddToast(msg, "error");
    } finally {
      submittingCierreRef.current = false;
      if (isMountedRef.current) {
        setIsSavingCierre(false);
      }
    }
  };

  // Gastos filtrados por texto
  const gastosFiltrados = useMemo(() => {
    return gastos.filter((g) => {
      if (busqueda.trim()) {
        const q = busqueda.trim().toLowerCase();
        const coincideDetalle = g.detalle.toLowerCase().includes(q);
        const coincideId = String(g.id).includes(q);
        const coincideTipo = g.tipo.toLowerCase().includes(q);
        if (!coincideDetalle && !coincideId && !coincideTipo) return false;
      }
      return true;
    });
  }, [gastos, busqueda]);

  return (
    <div className="expenses-container" data-testid="expenses-container">
      {/* Banner de Modo Demo */}
      {isDemo && (
        <div className="admin-demo-banner" data-testid="expenses-demo-banner">
          🧪 <strong>MODO DEMO:</strong> Registros de gastos, arqueos y cierres de prueba
          almacenados localmente en tu navegador. Totalmente aislados de bases operativas.
        </div>
      )}

      {/* Subnavegación del Módulo: Gastos vs Arqueo */}
      <div className="subnav-tabs" data-testid="subnav-tabs">
        <button
          type="button"
          className={`subnav-btn ${activeTab === "gastos" ? "subnav-active" : ""}`}
          onClick={() => setActiveTab("gastos")}
          data-testid="tab-btn-gastos"
        >
          💸 Gastos y Retiros
        </button>
        <button
          type="button"
          className={`subnav-btn ${activeTab === "arqueo" ? "subnav-active" : ""}`}
          onClick={() => setActiveTab("arqueo")}
          data-testid="tab-btn-arqueo"
        >
          📊 Arqueo y Cierre Diario
        </button>
      </div>

      {/* VISTA 1: GASTOS Y RETIROS */}
      {activeTab === "gastos" && (
        <div className="gastos-layout">
          {/* Formulario y Resumen Superior */}
          <div className="services-top-layout">
            <section className="services-form-card" data-testid="expense-form-card">
              <div className="card-header">
                <h3>💸 Registrar Gasto o Retiro</h3>
                <span className="card-badge-info">Salida de Fondos</span>
              </div>

              <form onSubmit={handleRegistrarGasto} className="services-form" data-testid="expense-form" noValidate>
                {formGastoError && (
                  <div className="alert-box-error" data-testid="expense-form-error">
                    ⚠️ {formGastoError}
                  </div>
                )}

                <div className="form-group">
                  <label htmlFor="input-expense-detail">Concepto / Detalle del Gasto:</label>
                  <input
                    id="input-expense-detail"
                    type="text"
                    className="form-input"
                    placeholder="Ej. Pago a proveedor panadería, artículos de limpieza..."
                    value={nuevoDetalle}
                    onChange={(e) => setNuevoDetalle(e.target.value)}
                    data-testid="input-expense-detail"
                  />
                </div>

                <div className="form-row">
                  <div className="form-group flex-1">
                    <label htmlFor="input-expense-amount">Importe ($):</label>
                    <input
                      id="input-expense-amount"
                      type="number"
                      inputMode="numeric"
                      min="1"
                      step="any"
                      className="form-input highlight-input"
                      placeholder="0"
                      value={nuevoMonto || ""}
                      onChange={(e) => setNuevoMonto(Number(e.target.value))}
                      data-testid="input-expense-amount"
                    />
                  </div>

                  <div className="form-group flex-1">
                    <label htmlFor="select-expense-cat">Categoría:</label>
                    <select
                      id="select-expense-cat"
                      className="form-input"
                      value={nuevaCategoria}
                      onChange={(e) => setNuevaCategoria(e.target.value)}
                      data-testid="select-expense-category"
                    >
                      {CATEGORIAS_GASTOS.map((cat) => (
                        <option key={cat} value={cat}>
                          {cat}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>

                <div className="form-row">
                  <div className="form-group flex-1">
                    <label>Método de Pago:</label>
                    <div className="payment-options-row">
                      <label className={`payment-pill ${nuevoMetodoPago === "EFECTIVO" ? "pill-active" : ""}`}>
                        <input
                          type="radio"
                          name="nuevoMetodoPagoGasto"
                          value="EFECTIVO"
                          checked={nuevoMetodoPago === "EFECTIVO"}
                          onChange={() => {
                            setNuevoMetodoPago("EFECTIVO");
                            setNuevoOrigen("Caja diaria");
                          }}
                          data-testid="radio-expense-payment-efectivo"
                        />
                        <span>💵 Efectivo</span>
                      </label>
                      <label className={`payment-pill ${nuevoMetodoPago === "TRANSFERENCIA" ? "pill-active" : ""}`}>
                        <input
                          type="radio"
                          name="nuevoMetodoPagoGasto"
                          value="TRANSFERENCIA"
                          checked={nuevoMetodoPago === "TRANSFERENCIA"}
                          onChange={() => {
                            setNuevoMetodoPago("TRANSFERENCIA");
                            setNuevoOrigen("Banco");
                          }}
                          data-testid="radio-expense-payment-transferencia"
                        />
                        <span>📱 Transferencia</span>
                      </label>
                    </div>
                  </div>

                  <div className="form-group flex-1">
                    <label htmlFor="input-expense-origin">Origen de Fondos:</label>
                    <input
                      id="input-expense-origin"
                      type="text"
                      className="form-input"
                      value={nuevoOrigen}
                      onChange={(e) => setNuevoOrigen(e.target.value)}
                      data-testid="input-expense-origin"
                    />
                  </div>
                </div>

                <button
                  type="submit"
                  className="btn-register-charge"
                  disabled={isRegisteringGasto}
                  onClick={handleRegistrarGasto}
                  data-testid="btn-register-expense"
                >
                  {isRegisteringGasto ? "Guardando..." : "➕ Registrar Gasto"}
                </button>
              </form>
            </section>

            {/* KPIs de Gastos */}
            <section className="services-kpi-card" data-testid="expense-kpi-summary">
              <div className="card-header">
                <h3>📊 Resumen de Egresos</h3>
                <span className="card-badge-kpi">Caja y Banco</span>
              </div>

              <div className="kpi-grid">
                <div className="kpi-tile highlight-kpi">
                  <span className="kpi-label">Total Gastos</span>
                  <span className="kpi-val text-red" data-testid="kpi-gastos-total">
                    ${(resumenGastos?.gastos_total || 0).toLocaleString("es-AR")}
                  </span>
                  <span className="kpi-sub">{resumenGastos?.total_operaciones || 0} egreso(s)</span>
                </div>

                <div className="kpi-tile">
                  <span className="kpi-label">Salidas Efectivo</span>
                  <span className="kpi-val" data-testid="kpi-gastos-efvo">
                    ${(resumenGastos?.gastos_efvo || 0).toLocaleString("es-AR")}
                  </span>
                  <span className="kpi-sub">Resta de Caja diaria</span>
                </div>

                <div className="kpi-tile">
                  <span className="kpi-label">Salidas Transf</span>
                  <span className="kpi-val" data-testid="kpi-gastos-transf">
                    ${(resumenGastos?.gastos_transf || 0).toLocaleString("es-AR")}
                  </span>
                  <span className="kpi-sub">Resta de Banco</span>
                </div>
              </div>

              {(resumenGastos?.operaciones_anuladas || 0) > 0 && (
                <div className="cancelled-summary-notice" data-testid="kpi-expenses-cancelled-notice">
                  ℹ️ <strong>{resumenGastos?.operaciones_anuladas}</strong> gasto(s) anulado(s) en este período.
                </div>
              )}
            </section>
          </div>

          {/* Historial de Gastos */}
          <section className="services-history-section">
            <div className="history-toolbar">
              <div className="toolbar-left">
                <input
                  type="search"
                  className="admin-search-input"
                  placeholder="🔍 Buscar gasto o concepto..."
                  value={busqueda}
                  onChange={(e) => setBusqueda(e.target.value)}
                  data-testid="input-search-expenses"
                />

                <input
                  type="date"
                  className="services-date-input"
                  value={filtroFecha}
                  onChange={(e) => setFiltroFecha(e.target.value)}
                  data-testid="filter-expense-date"
                />

                {filtroFecha && (
                  <button
                    type="button"
                    className="btn-clear-date"
                    onClick={() => setFiltroFecha("")}
                    data-testid="btn-clear-expense-date"
                  >
                    ✕ Todas
                  </button>
                )}

                <select
                  className="admin-filter-select"
                  value={filtroCategoria}
                  onChange={(e) => setFiltroCategoria(e.target.value)}
                  data-testid="filter-expense-category"
                >
                  <option value="TODAS">Todas las Categorías</option>
                  {CATEGORIAS_GASTOS.map((cat) => (
                    <option key={cat} value={cat}>
                      {cat}
                    </option>
                  ))}
                </select>

                <label className="checkbox-show-cancelled">
                  <input
                    type="checkbox"
                    checked={mostrarAnulados}
                    onChange={(e) => setMostrarAnulados(e.target.checked)}
                    data-testid="checkbox-show-cancelled-expenses"
                  />
                  <span>Mostrar Anulados</span>
                </label>
              </div>

              <div className="toolbar-right">
                <button
                  type="button"
                  className="btn-refresh"
                  onClick={cargarGastos}
                  disabled={isLoadingGastos}
                  data-testid="btn-refresh-expenses"
                >
                  🔄 Refrescar
                </button>
              </div>
            </div>

            {/* Tabla de Escritorio */}
            <div className="admin-table-wrapper">
              <table className="admin-table" data-testid="expenses-table">
                <thead>
                  <tr>
                    <th>#ID</th>
                    <th>Fecha/Hora</th>
                    <th>Categoría</th>
                    <th>Concepto / Detalle</th>
                    <th className="th-number">Importe</th>
                    <th>Medio de Pago</th>
                    <th>Origen</th>
                    <th>Estado</th>
                    <th className="th-actions">Acciones</th>
                  </tr>
                </thead>
                <tbody>
                  {gastosFiltrados.length === 0 ? (
                    <tr>
                      <td colSpan={9} className="td-empty">
                        {isLoadingGastos ? "Cargando gastos..." : "No hay gastos registrados."}
                      </td>
                    </tr>
                  ) : (
                    gastosFiltrados.map((g) => (
                      <tr key={g.id} className={g.anulado ? "row-cancelled" : ""}>
                        <td className="td-id">#{g.id}</td>
                        <td className="td-date">{g.fecha}</td>
                        <td><span className="badge-cat">{g.tipo}</span></td>
                        <td><strong>{g.detalle}</strong></td>
                        <td className="td-number text-red">-${g.monto_salida.toLocaleString("es-AR")}</td>
                        <td>{g.metodo_pago === "EFECTIVO" ? "💵 Efectivo" : "📱 Transferencia"}</td>
                        <td>{g.origen_fondos}</td>
                        <td>
                          <span className={`badge-status ${g.anulado ? "status-inactive" : "status-active"}`}>
                            {g.anulado ? "Anulado" : "Activo"}
                          </span>
                        </td>
                        <td className="td-actions">
                          {!g.anulado && (
                            <>
                              <button
                                type="button"
                                className="btn-action-edit"
                                onClick={() => handleOpenEditGasto(g)}
                                data-testid={`btn-edit-expense-${g.id}`}
                              >
                                ✏️ Editar
                              </button>
                              <button
                                type="button"
                                className="btn-action-cancel"
                                onClick={() => setCancellingGasto(g)}
                                data-testid={`btn-cancel-expense-${g.id}`}
                              >
                                🚫 Anular
                              </button>
                            </>
                          )}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>

            {/* Tarjetas Móviles */}
            <div className="services-cards-grid" data-testid="expenses-cards-grid">
              {gastosFiltrados.map((g) => (
                <div
                  key={g.id}
                  className={`service-card ${g.anulado ? "card-cancelled" : ""}`}
                  data-testid={`expense-card-${g.id}`}
                >
                  <div className="card-top-row">
                    <div className="card-title-group">
                      <span className="card-id">#{g.id}</span>
                      <h4 className="card-service-name">{g.detalle}</h4>
                    </div>
                    <span className={`badge-status ${g.anulado ? "status-inactive" : "status-active"}`}>
                      {g.anulado ? "Anulado" : "Activo"}
                    </span>
                  </div>

                  <div className="card-date-row">
                    <span>📅 {g.fecha}</span>
                    <span className="badge-cat">{g.tipo}</span>
                  </div>

                  <div className="card-financial-row">
                    <div className="financial-box">
                      <span className="box-label">Medio Pago</span>
                      <span className="box-val">{g.metodo_pago}</span>
                    </div>
                    <div className="financial-box highlight-price">
                      <span className="box-label">Importe</span>
                      <span className="box-val text-red">-${g.monto_salida.toLocaleString("es-AR")}</span>
                    </div>
                  </div>

                  {!g.anulado && (
                    <div className="card-actions-row">
                      <button
                        type="button"
                        className="btn-card-edit"
                        onClick={() => handleOpenEditGasto(g)}
                        data-testid={`btn-card-edit-expense-${g.id}`}
                      >
                        ✏️ Editar
                      </button>
                      <button
                        type="button"
                        className="btn-card-cancel"
                        onClick={() => setCancellingGasto(g)}
                        data-testid={`btn-card-cancel-expense-${g.id}`}
                      >
                        🚫 Anular
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </section>
        </div>
      )}

      {/* VISTA 2: ARQUEO Y CIERRE DIARIO */}
      {activeTab === "arqueo" && (
        <div className="arqueo-layout" data-testid="arqueo-layout">
          {/* Selector de Fecha de Arqueo */}
          <div className="arqueo-date-bar">
            <label htmlFor="input-fecha-arqueo">📅 Fecha de Conciliación:</label>
            <input
              id="input-fecha-arqueo"
              type="date"
              className="services-date-input"
              value={fechaArqueo}
              onChange={(e) => setFechaArqueo(e.target.value)}
              data-testid="input-arqueo-date"
            />
            <button
              type="button"
              className="btn-quick-today"
              onClick={() => {
                const now = new Date();
                const pad = (n: number) => String(n).padStart(2, "0");
                setFechaArqueo(`${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`);
              }}
              data-testid="btn-arqueo-today"
            >
              Hoy
            </button>
            <button
              type="button"
              className="btn-refresh"
              onClick={cargarArqueoYCierres}
              disabled={isLoadingArqueo}
              data-testid="btn-refresh-arqueo"
            >
              🔄 {isLoadingArqueo ? "Actualizando..." : "Recalcular Arqueo"}
            </button>
          </div>

          {/* Grilla de Cajas del Visor Ejecutivo (mostrar_visor) */}
          <div className="visor-grid-container" data-testid="visor-grid-container">
            {/* CAJA A: DRUGSTORE */}
            <div className="visor-box caja-a-box" data-testid="box-caja-a">
              <div className="visor-box-header header-blue">
                <h4>CAJA A - DRUGSTORE</h4>
              </div>
              <div className="visor-box-body">
                <div className="visor-row">
                  <span>(+) Efectivo Ventas:</span>
                  <strong data-testid="val-ventas-efvo">
                    ${(arqueo?.ventas_efvo || 0).toLocaleString("es-AR")}
                  </strong>
                </div>
                <div className="visor-sub-row">
                  <span>Efectivo disponible Drugstore:</span>
                  <span className="text-muted" data-testid="val-efvo-disp-drugstore">
                    ${(arqueo?.efectivo_disponible_drugstore || 0).toLocaleString("es-AR")}
                  </span>
                </div>
                <div className="visor-row">
                  <span>(+) Transferencias Ventas:</span>
                  <strong data-testid="val-ventas-transf">
                    ${(arqueo?.ventas_transf || 0).toLocaleString("es-AR")}
                  </strong>
                </div>
                <div className="visor-divider"></div>
                <div className="visor-total-row">
                  <span>TOTAL VENTAS:</span>
                  <strong data-testid="val-total-ventas">
                    ${(arqueo?.total_ventas || 0).toLocaleString("es-AR")}
                  </strong>
                </div>
                <div className="visor-profit-row">
                  <span>Ganancia Estimada (10%):</span>
                  <span className="badge-profit" data-testid="val-ganancia-ventas">
                    +${(arqueo?.ganancia_estimada_ventas || 0).toLocaleString("es-AR")}
                  </span>
                </div>
              </div>
            </div>

            {/* CAJA B: SUBE / PREPAY */}
            <div className="visor-box caja-b-box" data-testid="box-caja-b">
              <div className="visor-box-header header-orange">
                <h4>CAJA B - PREPAY (Solo Capital)</h4>
              </div>
              <div className="visor-box-body">
                <div className="visor-row">
                  <span>Capital Recargado:</span>
                  <strong data-testid="val-capital-prepay">
                    ${(arqueo?.capital_prepay || 0).toLocaleString("es-AR")}
                  </strong>
                </div>
                <span className="box-desc">SUBE, Personal, Movistar, Tuenti, etc.</span>
              </div>
            </div>

            {/* CAJA C: ADICIONALES (Ganancia Servicios) */}
            <div className="visor-box caja-c-box" data-testid="box-caja-c">
              <div className="visor-box-header header-green">
                <h4>CAJA C - ADICIONALES (Ganancia)</h4>
              </div>
              <div className="visor-box-body">
                <div className="visor-total-row">
                  <span>GANANCIA RECARGOS:</span>
                  <strong className="text-green" data-testid="val-adicionales-ganancia">
                    +${(arqueo?.adicionales_ganancia || 0).toLocaleString("es-AR")}
                  </strong>
                </div>
                <span className="box-desc">Ingreso total comisiones recargas</span>
              </div>
            </div>

            {/* CAJA D: TRANSFERENCIAS (Total en Banco) */}
            <div className="visor-box caja-d-box" data-testid="box-caja-d">
              <div className="visor-box-header header-purple">
                <h4>CAJA D - BANCO (Conciliación Digital)</h4>
              </div>
              <div className="visor-box-body">
                <div className="visor-row">
                  <span>Ventas Drugstore Transf:</span>
                  <strong>${(arqueo?.ventas_transf || 0).toLocaleString("es-AR")}</strong>
                </div>
                <div className="visor-row">
                  <span>Recargas por transferencia (+):</span>
                  <strong>${(arqueo?.d_recargas || 0).toLocaleString("es-AR")}</strong>
                </div>
                <div className="visor-row">
                  <span>SUBE (MP) Capital (-):</span>
                  <span className="text-red">-${(arqueo?.capital_sube_mp || 0).toLocaleString("es-AR")}</span>
                </div>
                <div className="visor-row">
                  <span>Gastos Transferencia (-):</span>
                  <span className="text-red">-${(arqueo?.gastos_transf || 0).toLocaleString("es-AR")}</span>
                </div>
                <div className="visor-divider"></div>
                <div className="visor-total-row highlight-bank">
                  <span>TOTAL NETO EN BANCO:</span>
                  <strong data-testid="val-total-banco">
                    ${(arqueo?.total_banco || 0).toLocaleString("es-AR")}
                  </strong>
                </div>
              </div>
            </div>

            {/* CAJA E: CLARO */}
            <div className="visor-box caja-e-box" data-testid="box-caja-e">
              <div className="visor-box-header header-red">
                <h4>CAJA E - CLARO (Solo Capital)</h4>
              </div>
              <div className="visor-box-body">
                <div className="visor-row">
                  <span>Capital Claro:</span>
                  <strong data-testid="val-capital-claro">
                    ${(arqueo?.capital_claro || 0).toLocaleString("es-AR")}
                  </strong>
                </div>
                <span className="box-desc">Caja segregada operadora Claro</span>
              </div>
            </div>

            {/* GASTOS Y RETIROS */}
            <div className="visor-box caja-gastos-box" data-testid="box-caja-gastos">
              <div className="visor-box-header header-dark">
                <h4>GASTOS Y RETIROS</h4>
              </div>
              <div className="visor-box-body">
                <div className="visor-row">
                  <span>(-) Salidas Efectivo:</span>
                  <span className="text-red">-${(arqueo?.gastos_efvo || 0).toLocaleString("es-AR")}</span>
                </div>
                <div className="visor-row">
                  <span>(-) Salidas Transf:</span>
                  <span className="text-red">-${(arqueo?.gastos_transf || 0).toLocaleString("es-AR")}</span>
                </div>
                <div className="visor-divider"></div>
                <div className="visor-total-row">
                  <span>TOTAL EGRESOS:</span>
                  <strong className="text-red" data-testid="val-gastos-total">
                    -${(arqueo?.gastos_total || 0).toLocaleString("es-AR")}
                  </strong>
                </div>
              </div>
            </div>
          </div>

          {/* Panel de Conciliación Física de Efectivo y Cierre */}
          <div className="cash-reconciliation-panel" data-testid="cash-reconciliation-panel">
            <div className="reconciliation-header">
              <h3>🔒 Arqueo Físico y Cierre Diario de Caja</h3>
              {cierrePrevio && (
                <span className="badge-closed" data-testid="badge-closed-notice">
                  ✅ Día Cerrado ({cierrePrevio.cerrado_por})
                </span>
              )}
            </div>

            <div className="reconciliation-grid">
              {/* Resumen de Efectivo Esperado */}
              <div className="reconciliation-box expected-box">
                <span className="recon-label">Efectivo Total Esperado en Cajón:</span>
                <span className="recon-value text-blue" data-testid="val-efectivo-esperado">
                  ${(arqueo?.efectivo_esperado || 0).toLocaleString("es-AR")}
                </span>
                <span className="recon-detail">
                  (Ventas Efvo + Servicios Efvo - Gastos Efvo)
                </span>
              </div>

              {/* Formulario de Conteo Físico */}
              <div className="reconciliation-box counting-box">
                <label htmlFor="input-cash-counted">Efectivo Contado Físicamente ($):</label>
                <input
                  id="input-cash-counted"
                  type="number"
                  inputMode="numeric"
                  min="0"
                  step="any"
                  className="form-input highlight-input"
                  placeholder="Ingresá los billetes contados..."
                  value={efectivoContado}
                  onChange={(e) => setEfectivoContado(e.target.value === "" ? "" : Number(e.target.value))}
                  data-testid="input-cash-counted"
                />

                {efectivoContado !== "" && (
                  <div
                    className={`difference-banner ${
                      diferenciaArqueo === 0
                        ? "diff-zero"
                        : diferenciaArqueo > 0
                        ? "diff-surplus"
                        : "diff-shortage"
                    }`}
                    data-testid="box-difference"
                  >
                    <span>Diferencia:</span>
                    <strong>
                      {diferenciaArqueo === 0
                        ? "✅ Caja Cuadrada ($0)"
                        : diferenciaArqueo > 0
                        ? `🟢 Sobrante: +$${diferenciaArqueo.toLocaleString("es-AR")}`
                        : `🔴 Faltante: -$${Math.abs(diferenciaArqueo).toLocaleString("es-AR")}`}
                    </strong>
                  </div>
                )}
              </div>
            </div>

            <div className="close-form-actions">
              <div className="form-group flex-1">
                <label htmlFor="input-close-notes">Observaciones del Cierre (Opcional):</label>
                <input
                  id="input-close-notes"
                  type="text"
                  className="form-input"
                  placeholder="Ej. Se retiraron $10.000 para el banco, o faltante por redondeo..."
                  value={observacionesCierre}
                  onChange={(e) => setObservacionesCierre(e.target.value)}
                  data-testid="input-close-notes"
                />
              </div>

              <div className="form-group">
                <label htmlFor="select-close-actor">Responsable:</label>
                <select
                  id="select-close-actor"
                  className="form-input"
                  value={cerradoPor}
                  onChange={(e) => setCerradoPor(e.target.value)}
                  data-testid="select-close-actor"
                >
                  <option value="Alan">Alan</option>
                  <option value="Yilda">Yilda</option>
                  <option value="sistema">Sistema</option>
                </select>
              </div>

              <button
                type="button"
                className="btn-confirm-save btn-day-close"
                disabled={isSavingCierre || efectivoContado === ""}
                onClick={handleConfirmCierre}
                data-testid="btn-confirm-day-close"
              >
                {isSavingCierre
                  ? "Guardando Cierre..."
                  : cierrePrevio
                  ? "Actualizar Cierre del Día"
                  : "🔒 Confirmar y Guardar Cierre"}
              </button>
            </div>
          </div>

          {/* Historial de Cierres Anteriores */}
          {historialCierres.length > 0 && (
            <div className="closure-history-section">
              <h4>📜 Historial de Cierres Registrados</h4>
              <div className="admin-table-wrapper">
                <table className="admin-table" data-testid="table-cierres">
                  <thead>
                    <tr>
                      <th>Fecha</th>
                      <th className="th-number">Esperado</th>
                      <th className="th-number">Contado</th>
                      <th className="th-number">Diferencia</th>
                      <th className="th-number">Total Banco</th>
                      <th>Responsable</th>
                      <th>Observaciones</th>
                    </tr>
                  </thead>
                  <tbody>
                    {historialCierres.map((c) => (
                      <tr key={c.fecha}>
                        <td><strong>{c.fecha}</strong></td>
                        <td className="td-number">${c.efectivo_esperado.toLocaleString("es-AR")}</td>
                        <td className="td-number">${c.efectivo_contado.toLocaleString("es-AR")}</td>
                        <td className={`td-number ${c.diferencia < 0 ? "text-red" : c.diferencia > 0 ? "text-green" : ""}`}>
                          {c.diferencia === 0 ? "$0" : `${c.diferencia > 0 ? "+" : ""}$${c.diferencia.toLocaleString("es-AR")}`}
                        </td>
                        <td className="td-number">${c.total_banco.toLocaleString("es-AR")}</td>
                        <td>{c.cerrado_por}</td>
                        <td className="td-desc">{c.observaciones || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Modal Edición de Gasto */}
      {editingGasto && (
        <div className="modal-backdrop" data-testid="modal-edit-expense">
          <div className="modal-card">
            <div className="modal-header">
              <h3>✏️ Editar Gasto #{editingGasto.id}</h3>
              <button
                type="button"
                className="btn-close-modal"
                onClick={() => setEditingGasto(null)}
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleSaveEditGasto} className="admin-product-form" noValidate>
              <div className="modal-body">
                {editError && (
                  <div className="alert-box-error">⚠️ {editError}</div>
                )}

                <div className="form-group">
                  <label htmlFor="edit-exp-detail">Concepto / Detalle:</label>
                  <input
                    id="edit-exp-detail"
                    type="text"
                    className="form-input"
                    value={editDetalle}
                    onChange={(e) => setEditDetalle(e.target.value)}
                    data-testid="input-edit-expense-detail"
                  />
                </div>

                <div className="form-row">
                  <div className="form-group flex-1">
                    <label htmlFor="edit-exp-amount">Importe ($):</label>
                    <input
                      id="edit-exp-amount"
                      type="number"
                      min="1"
                      className="form-input"
                      value={editMonto || ""}
                      onChange={(e) => setEditMonto(Number(e.target.value))}
                      data-testid="input-edit-expense-amount"
                    />
                  </div>

                  <div className="form-group flex-1">
                    <label htmlFor="edit-exp-cat">Categoría:</label>
                    <select
                      id="edit-exp-cat"
                      className="form-input"
                      value={editCategoria}
                      onChange={(e) => setEditCategoria(e.target.value)}
                      data-testid="select-edit-expense-category"
                    >
                      {CATEGORIAS_GASTOS.map((cat) => (
                        <option key={cat} value={cat}>
                          {cat}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>

                <div className="form-group">
                  <label>Método de Pago:</label>
                  <div className="payment-options-row">
                    <label className={`payment-pill ${editMetodoPago === "EFECTIVO" ? "pill-active" : ""}`}>
                      <input
                        type="radio"
                        name="editMetodoPagoGasto"
                        value="EFECTIVO"
                        checked={editMetodoPago === "EFECTIVO"}
                        onChange={() => setEditMetodoPago("EFECTIVO")}
                        data-testid="radio-edit-expense-efectivo"
                      />
                      <span>💵 Efectivo</span>
                    </label>
                    <label className={`payment-pill ${editMetodoPago === "TRANSFERENCIA" ? "pill-active" : ""}`}>
                      <input
                        type="radio"
                        name="editMetodoPagoGasto"
                        value="TRANSFERENCIA"
                        checked={editMetodoPago === "TRANSFERENCIA"}
                        onChange={() => setEditMetodoPago("TRANSFERENCIA")}
                        data-testid="radio-edit-expense-transferencia"
                      />
                      <span>📱 Transferencia</span>
                    </label>
                  </div>
                </div>
              </div>

              <div className="modal-footer">
                <button
                  type="button"
                  className="btn-cancel"
                  onClick={() => setEditingGasto(null)}
                  disabled={isSavingEdit}
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  className="btn-confirm-save"
                  disabled={isSavingEdit}
                  onClick={handleSaveEditGasto}
                  data-testid="btn-save-expense-edit"
                >
                  {isSavingEdit ? "Guardando..." : "Actualizar Gasto"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Modal Confirmación de Anulación */}
      {cancellingGasto && (
        <div className="modal-backdrop" data-testid="modal-confirm-cancel-expense">
          <div className="modal-card">
            <div className="modal-header">
              <h3>⚠️ Confirmar Anulación de Gasto</h3>
              <button
                type="button"
                className="btn-close-modal"
                onClick={() => setCancellingGasto(null)}
              >
                ✕
              </button>
            </div>

            <div className="modal-body">
              <div className="alert-box-warning">
                <strong>¿Anular el gasto #{cancellingGasto.id} ({cancellingGasto.detalle})?</strong>
                <p>
                  Esta anulación es <strong>lógica y auditable</strong>. El egreso no se borrará físicamente,
                  pero se excluirá de los cálculos contables del arqueo y se reintegrará al efectivo/saldo disponible.
                </p>
                <div className="cancel-details-summary">
                  <div><strong>Monto:</strong> ${cancellingGasto.monto_salida.toLocaleString("es-AR")}</div>
                  <div><strong>Medio:</strong> {cancellingGasto.metodo_pago}</div>
                  <div><strong>Fecha:</strong> {cancellingGasto.fecha}</div>
                </div>
              </div>

              <div className="form-group mt-1">
                <label htmlFor="cancel-motivo">Motivo de Anulación (Opcional):</label>
                <input
                  id="cancel-motivo"
                  type="text"
                  className="form-input"
                  placeholder="Ej. Factura duplicada, error en el monto..."
                  value={cancelMotivo}
                  onChange={(e) => setCancelMotivo(e.target.value)}
                  data-testid="input-cancel-expense-reason"
                />
              </div>
            </div>

            <div className="modal-footer">
              <button
                type="button"
                className="btn-cancel"
                onClick={() => setCancellingGasto(null)}
                disabled={isCancelling}
              >
                Volver Atrás
              </button>
              <button
                type="button"
                className="btn-danger-confirm"
                onClick={handleConfirmCancelGasto}
                disabled={isCancelling}
                data-testid="btn-confirm-cancel-expense"
              >
                {isCancelling ? "Anulando..." : "Sí, Anular Gasto"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
