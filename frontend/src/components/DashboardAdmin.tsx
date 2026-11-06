import React, { useState, useEffect, useCallback } from "react";
import { PeriodoTipo, MetricasDashboard } from "../types/dashboard";
import {
  SaldosInicialesResponse,
  EstadoCuentaDavidResponse,
  EstadoCuentaEzequielResponse,
  ResumenPreventistasResponse,
} from "../types/tesoreria";
import { getApiClient } from "../services/api";
import { TesoreriaService } from "../services/tesoreria";

interface DashboardAdminProps {
  onAddToast: (mensaje: string, tipo?: "info" | "success" | "warning" | "error") => void;
  isDemo: boolean;
}

const FALLBACK_SALDOS_TESORERIA: SaldosInicialesResponse = {
  fondos_fisicos: [
    { codigo: "1110", nombre: "Drugstore Diario", categoria: "FONDO_FISICO", saldo_inicial_cents: 0, saldo_actual_cents: 0, configurado_en: null, configurado_por: "sistema", estado_configuracion: "INICIAL_CERO", editable: true },
    { codigo: "1120", nombre: "Drugstore Acumulado", categoria: "FONDO_FISICO", saldo_inicial_cents: 0, saldo_actual_cents: 0, configurado_en: null, configurado_por: "sistema", estado_configuracion: "INICIAL_CERO", editable: true },
    { codigo: "1130", nombre: "Prepay Efectivo Acumulado", categoria: "FONDO_FISICO", saldo_inicial_cents: 0, saldo_actual_cents: 0, configurado_en: null, configurado_por: "sistema", estado_configuracion: "INICIAL_CERO", editable: true },
    { codigo: "1140", nombre: "Adicionales Efectivo", categoria: "FONDO_FISICO", saldo_inicial_cents: 0, saldo_actual_cents: 0, configurado_en: null, configurado_por: "sistema", estado_configuracion: "INICIAL_CERO", editable: true },
    { codigo: "1150", nombre: "Claro Efectivo (Custodia)", categoria: "FONDO_FISICO", saldo_inicial_cents: 0, saldo_actual_cents: 0, configurado_en: null, configurado_por: "sistema", estado_configuracion: "INICIAL_CERO", editable: true },
    { codigo: "1160", nombre: "Prepay Diario Efectivo", categoria: "FONDO_FISICO", saldo_inicial_cents: 0, saldo_actual_cents: 0, configurado_en: null, configurado_por: "sistema", estado_configuracion: "INICIAL_CERO", editable: true },
  ],
  cuentas_digitales: [
    { codigo: "1210", nombre: "Mercado Pago Propio", categoria: "CUENTA_DIGITAL", saldo_inicial_cents: 0, saldo_actual_cents: 0, configurado_en: null, configurado_por: "sistema", estado_configuracion: "INICIAL_CERO", editable: true },
    { codigo: "1220", nombre: "Prepay Digital Saldo", categoria: "CUENTA_DIGITAL", saldo_inicial_cents: 0, saldo_actual_cents: 0, configurado_en: null, configurado_por: "sistema", estado_configuracion: "INICIAL_CERO", editable: true },
    { codigo: "1230", nombre: "Claro Comercios Digital Saldo", categoria: "CUENTA_DIGITAL", saldo_inicial_cents: 0, saldo_actual_cents: 0, configurado_en: null, configurado_por: "sistema", estado_configuracion: "INICIAL_CERO", editable: true },
    { codigo: "1310", nombre: "Cobranzas en Tránsito Preventistas", categoria: "CUENTA_DIGITAL", saldo_inicial_cents: 0, saldo_actual_cents: 0, configurado_en: null, configurado_por: "sistema", estado_configuracion: "INICIAL_CERO", editable: true },
  ],
  obligaciones: [
    { codigo: "OBL_DAVID", nombre: "Obligación David (PREPAY)", categoria: "OBLIGACION", saldo_inicial_cents: 0, saldo_actual_cents: 0, configurado_en: null, configurado_por: "sistema", estado_configuracion: "INICIAL_CERO", editable: true },
    { codigo: "OBL_CLARO", nombre: "Obligación Claro / Ezequiel", categoria: "OBLIGACION", saldo_inicial_cents: 0, saldo_actual_cents: 0, configurado_en: null, configurado_por: "sistema", estado_configuracion: "INICIAL_CERO", editable: true },
    { codigo: "OBL_PREVENTISTAS", nombre: "Obligación Preventistas (Reposición)", categoria: "OBLIGACION", saldo_inicial_cents: 0, saldo_actual_cents: 0, configurado_en: null, configurado_por: "sistema", estado_configuracion: "INICIAL_CERO", editable: true },
  ],
  total_fondos_fisicos_cents: 0,
  total_cuentas_digitales_cents: 0,
  total_obligaciones_cents: 0,
};

export const DashboardAdmin: React.FC<DashboardAdminProps> = ({ onAddToast, isDemo }) => {
  const [periodo, setPeriodo] = useState<PeriodoTipo>("hoy");
  const [desdePersonalizado, setDesdePersonalizado] = useState<string>("");
  const [hastaPersonalizado, setHastaPersonalizado] = useState<string>("");
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [metricas, setMetricas] = useState<MetricasDashboard | null>(null);
  const [saldosTesoreria, setSaldosTesoreria] = useState<SaldosInicialesResponse>(FALLBACK_SALDOS_TESORERIA);
  const [ccDavid, setCcDavid] = useState<EstadoCuentaDavidResponse | null>(null);
  const [ccEzequiel, setCcEzequiel] = useState<EstadoCuentaEzequielResponse | null>(null);
  const [resumenPreventistas, setResumenPreventistas] = useState<ResumenPreventistasResponse | null>(null);

  const cargarMetricas = useCallback(async (
    pTipo: PeriodoTipo = periodo,
    desde?: string,
    hasta?: string
  ) => {
    setIsLoading(true);
    try {
      const api = getApiClient();
      const res = await api.obtenerMetricasDashboard(desde, hasta, pTipo);
      if (res.status === 200 && res.data) {
        setMetricas(res.data);
      } else {
        onAddToast(res.error || "Error al cargar visor financiero", "error");
      }

      if (!isDemo) {
        const [saldosRes, davidRes, ezequielRes, prevRes] = await Promise.allSettled([
          TesoreriaService.getSaldosIniciales(),
          TesoreriaService.getCuentaCorrienteDavid(),
          TesoreriaService.getCuentaCorrienteEzequiel(),
          TesoreriaService.getResumenPreventistas(),
        ]);
        if (saldosRes.status === "fulfilled" && saldosRes.value) {
          setSaldosTesoreria(saldosRes.value);
        }
        if (davidRes.status === "fulfilled" && davidRes.value) {
          setCcDavid(davidRes.value);
        }
        if (ezequielRes.status === "fulfilled" && ezequielRes.value) {
          setCcEzequiel(ezequielRes.value);
        }
        if (prevRes.status === "fulfilled" && prevRes.value) {
          setResumenPreventistas(prevRes.value);
        }
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error inesperado";
      onAddToast(`Error: ${msg}`, "error");
    } finally {
      setIsLoading(false);
    }
  }, [periodo, onAddToast, isDemo]);

  useEffect(() => {
    cargarMetricas(periodo);
  }, [periodo, cargarMetricas]);

  const handleCambiarPeriodo = (nuevo: PeriodoTipo) => {
    setPeriodo(nuevo);
    if (nuevo !== "personalizado") {
      cargarMetricas(nuevo);
    }
  };

  const handleAplicarPersonalizado = (e: React.FormEvent) => {
    e.preventDefault();
    if (!desdePersonalizado || !hastaPersonalizado) {
      onAddToast("Debes seleccionar ambas fechas para el rango personalizado", "warning");
      return;
    }
    cargarMetricas("personalizado", desdePersonalizado, hastaPersonalizado);
  };

  const formatoMoneda = (num: number = 0) =>
    `$${num.toLocaleString("es-AR", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;

  const totalOperacionesPeriodo =
    (metricas?.caja_a.cantidad_tickets || 0) +
    (metricas?.caja_b.cantidad_operaciones || 0) +
    (metricas?.caja_c.cantidad_operaciones || 0) +
    (metricas?.caja_e.cantidad_operaciones || 0) +
    (metricas?.gastos.cantidad_gastos || 0);

  const esVacio = totalOperacionesPeriodo === 0;

  // Render SVG Chart for Serie Temporal
  const renderGraficoEvolucion = () => {
    if (!metricas) return null;
    const puntos = metricas.serie_temporal || [];
    if (puntos.length === 0 || esVacio) {
      return (
        <div className="dashboard-chart-container p-6 rounded-2xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 text-center shadow-sm" data-testid="svg-chart-evolucion">
          <div className="py-6 text-center text-gray-400">
            <span className="text-3xl block mb-2">📊</span>
            <p className="font-semibold text-xs text-gray-700 dark:text-gray-300">Sin datos de evolución diaria para este período</p>
            <p className="text-[11px] text-gray-400 mt-1">Realizá operaciones en mostrador o seleccioná otro rango de fechas para visualizar la serie temporal.</p>
          </div>
        </div>
      );
    }

    const maxValor = Math.max(
      1,
      ...puntos.map((p) => Math.max(p.ventas_total, p.servicios_total, p.gastos_total))
    );

    const svgHeight = 230;
    const paddingBottom = 32;
    const paddingTop = 24;
    const paddingLeft = 68;
    const chartHeight = svgHeight - paddingBottom - paddingTop;
    const barGroupWidth = Math.max(28, Math.floor(600 / puntos.length));
    const svgWidth = Math.max(680, puntos.length * barGroupWidth + paddingLeft + 24);
    const mitadValor = Math.round(maxValor / 2);

    return (
      <div className="dashboard-chart-container" data-testid="svg-chart-evolucion">
        <div className="chart-header">
          <h4 className="chart-title">📈 Evolución Diaria (Ventas Comerciales, Servicios y Gastos)</h4>
          <div className="chart-legend">
            <span className="legend-item"><span className="legend-dot dot-ventas"></span> Ventas Comerciales</span>
            <span className="legend-item"><span className="legend-dot dot-servicios"></span> Servicios (Total Cobrado)</span>
            <span className="legend-item"><span className="legend-dot dot-gastos"></span> Gastos Operativos</span>
          </div>
        </div>
        <div className="svg-scroll-wrapper">
          <svg
            viewBox={`0 0 ${svgWidth} ${svgHeight}`}
            className="dashboard-svg-chart"
            preserveAspectRatio="none"
          >
            {/* Líneas de referencia e importes en eje Y (formato argentino) */}
            <line
              x1={paddingLeft}
              y1={paddingTop}
              x2={svgWidth - 16}
              y2={paddingTop}
              stroke="var(--border-subtle)"
              strokeDasharray="4 4"
              strokeWidth="1"
            />
            <text
              x={paddingLeft - 8}
              y={paddingTop + 4}
              textAnchor="end"
              fontSize="10"
              fill="var(--text-secondary)"
            >
              {formatoMoneda(maxValor)}
            </text>

            <line
              x1={paddingLeft}
              y1={paddingTop + chartHeight / 2}
              x2={svgWidth - 16}
              y2={paddingTop + chartHeight / 2}
              stroke="var(--border-subtle)"
              strokeDasharray="4 4"
              strokeWidth="1"
            />
            <text
              x={paddingLeft - 8}
              y={paddingTop + chartHeight / 2 + 4}
              textAnchor="end"
              fontSize="10"
              fill="var(--text-secondary)"
            >
              {formatoMoneda(mitadValor)}
            </text>

            {/* Eje horizontal base ($0) */}
            <line
              x1={paddingLeft}
              y1={svgHeight - paddingBottom}
              x2={svgWidth - 16}
              y2={svgHeight - paddingBottom}
              stroke="var(--border-color)"
              strokeWidth="1.2"
            />
            <text
              x={paddingLeft - 8}
              y={svgHeight - paddingBottom + 4}
              textAnchor="end"
              fontSize="10"
              fill="var(--text-secondary)"
            >
              $0
            </text>

            {puntos.map((p, idx) => {
              const xCenter = paddingLeft + 14 + idx * barGroupWidth + barGroupWidth / 2;
              const subBarW = Math.max(4, Math.floor(barGroupWidth / 4));

              const hVentas = (p.ventas_total / maxValor) * chartHeight;
              const yVentas = svgHeight - paddingBottom - hVentas;

              const hServ = (p.servicios_total / maxValor) * chartHeight;
              const yServ = svgHeight - paddingBottom - hServ;

              const hGastos = (p.gastos_total / maxValor) * chartHeight;
              const yGastos = svgHeight - paddingBottom - hGastos;

              return (
                <g key={p.fecha} className="chart-bar-group">
                  {/* Barra Ventas */}
                  <rect
                    x={xCenter - subBarW * 1.5}
                    y={yVentas}
                    width={subBarW}
                    height={Math.max(1, hVentas)}
                    fill="var(--accent-green)"
                    rx="3"
                  >
                    <title>{`${p.etiqueta} — Ventas Comerciales: ${formatoMoneda(p.ventas_total)}`}</title>
                  </rect>

                  {/* Barra Servicios */}
                  <rect
                    x={xCenter - subBarW * 0.5}
                    y={yServ}
                    width={subBarW}
                    height={Math.max(1, hServ)}
                    fill="var(--accent-blue)"
                    rx="3"
                  >
                    <title>{`${p.etiqueta} — Servicios Cobrados: ${formatoMoneda(p.servicios_total)}`}</title>
                  </rect>

                  {/* Barra Gastos */}
                  <rect
                    x={xCenter + subBarW * 0.5}
                    y={yGastos}
                    width={subBarW}
                    height={Math.max(1, hGastos)}
                    fill="var(--accent-rose)"
                    rx="3"
                  >
                    <title>{`${p.etiqueta} — Gastos Operativos: ${formatoMoneda(p.gastos_total)}`}</title>
                  </rect>

                  {/* Etiqueta Fecha */}
                  <text
                    x={xCenter}
                    y={svgHeight - 10}
                    textAnchor="middle"
                    fontSize="10"
                    fill="var(--text-secondary)"
                  >
                    {p.etiqueta}
                  </text>
                </g>
              );
            })}
          </svg>
        </div>
      </div>
    );
  };

  const formatoCentavos = (cents: number = 0) =>
    formatoMoneda(Math.round((Number(cents) || 0) / 100));

  const capitalServiciosTotalPeriodo =
    (metricas?.caja_b.capital_total || 0) +
    (metricas?.caja_e.capital_total || 0) +
    (metricas?.caja_d.sube_mp_capital || 0);

  return (
    <div className="dashboard-admin-view" data-testid="dashboard-admin">
      {/* Encabezado del Visor */}
      <div className="dashboard-header-panel">
        <div className="dashboard-title-area">
          <h2 className="dashboard-main-title">📊 Visor Financiero y Reportes</h2>
          <p className="dashboard-subtitle">
            Flujos comerciales y de servicios del período ({metricas?.desde || "Hoy"} al {metricas?.hasta || "Hoy"} — Hora Argentina), saldos reales de Tesorería y estado de obligaciones.
            {isDemo && (
              <span className="demo-tag-pill" data-testid="badge-demo-visor">
                🧪 Demostración Simulada
              </span>
            )}
          </p>
        </div>

        {/* Botonera de Período */}
        <div className="dashboard-controls-area">
          <div className="period-pill-selector" data-testid="periodo-selector">
            <button
              type="button"
              className={`btn-period-pill ${periodo === "hoy" ? "active" : ""}`}
              onClick={() => handleCambiarPeriodo("hoy")}
              data-testid="btn-periodo-hoy"
            >
              Hoy
            </button>
            <button
              type="button"
              className={`btn-period-pill ${periodo === "ultimos_7_dias" ? "active" : ""}`}
              onClick={() => handleCambiarPeriodo("ultimos_7_dias")}
              data-testid="btn-periodo-7dias"
            >
              Últimos 7 días
            </button>
            <button
              type="button"
              className={`btn-period-pill ${periodo === "ultimos_30_dias" ? "active" : ""}`}
              onClick={() => handleCambiarPeriodo("ultimos_30_dias")}
              data-testid="btn-periodo-30dias"
            >
              Últimos 30 días
            </button>
            <button
              type="button"
              className={`btn-period-pill ${periodo === "personalizado" ? "active" : ""}`}
              onClick={() => handleCambiarPeriodo("personalizado")}
              data-testid="btn-periodo-personalizado"
            >
              Personalizado
            </button>
          </div>

          <button
            type="button"
            className="btn-refresh-dashboard"
            onClick={() => cargarMetricas(periodo, desdePersonalizado, hastaPersonalizado)}
            disabled={isLoading}
            title="Recargar datos"
          >
            🔄 {isLoading ? "Actualizando..." : "Actualizar"}
          </button>
        </div>
      </div>

      {/* Selector de Rango Personalizado */}
      {periodo === "personalizado" && (
        <form
          className="custom-range-bar"
          data-testid="form-rango-personalizado"
          onSubmit={handleAplicarPersonalizado}
        >
          <div className="range-input-group">
            <label htmlFor="input-desde">Desde:</label>
            <input
              id="input-desde"
              type="date"
              value={desdePersonalizado}
              onChange={(e) => setDesdePersonalizado(e.target.value)}
              className="form-input date-input"
              required
            />
          </div>
          <div className="range-input-group">
            <label htmlFor="input-hasta">Hasta:</label>
            <input
              id="input-hasta"
              type="date"
              value={hastaPersonalizado}
              onChange={(e) => setHastaPersonalizado(e.target.value)}
              className="form-input date-input"
              required
            />
          </div>
          <button type="submit" className="btn-apply-range" disabled={isLoading}>
            Aplicar Rango
          </button>
        </form>
      )}

      {/* Alerta de Esquema Parcial en Base de Datos */}
      {!isLoading && metricas?.esquema_completo === false && (
        <div
          className="incomplete-schema-banner"
          data-testid="banner-esquema-incompleto"
        >
          <span className="incomplete-schema-title">⚠️ Esquema Parcial en Base de Datos:</span>
          <p className="incomplete-schema-desc">
            {metricas.advertencia_esquema ||
              `Faltan tablas requeridas: ${metricas.tablas_faltantes?.join(", ")}. Los valores reflejan datos parciales.`}
          </p>
        </div>
      )}

      {/* Alerta de Período Sin Datos */}
      {!isLoading && esVacio && (
        <div className="empty-dashboard-banner" data-testid="empty-dashboard-notice">
          <span className="banner-icon">ℹ️</span>
          <div>
            <strong>Sin operaciones registradas en el período seleccionado.</strong>
            <p>
              Rango actual: {metricas?.desde} a {metricas?.hasta}. Los valores se muestran en $0.
            </p>
          </div>
        </div>
      )}

      {/* Tira Ejecutiva del Período Seleccionado (Flujos Operativos Separados por Naturaleza) */}
      <div className="visor-kpi-strip-grid" data-testid="dashboard-kpi-strip">
        <div className="visor-kpi-pill" data-testid="kpi-strip-cajon">
          <span className="visor-kpi-tag tag-neutral">Flujo Efectivo del Período</span>
          <span className="visor-kpi-val">
            {formatoMoneda(metricas?.conciliacion_cajon.efectivo_esperado_cajon)}
          </span>
          <span className="visor-kpi-caption">Entradas efvo. − Salidas efvo. ({metricas?.desde})</span>
        </div>
        <div className="visor-kpi-pill" data-testid="kpi-strip-caja-a">
          <span className="visor-kpi-tag tag-emerald">Ventas Comerciales (Drugstore)</span>
          <span className="visor-kpi-val">
            {formatoMoneda(metricas?.caja_a.total_ventas)}
          </span>
          <span className="visor-kpi-caption">{metricas?.caja_a.cantidad_tickets || 0} tickets emitidos en el período</span>
        </div>
        <div className="visor-kpi-pill" data-testid="kpi-strip-caja-b">
          <span className="visor-kpi-tag tag-blue">Capital Servicios (Pasamano)</span>
          <span className="visor-kpi-val">
            {formatoMoneda(capitalServiciosTotalPeriodo)}
          </span>
          <span className="visor-kpi-caption">Recaudado para recargas (No es ganancia)</span>
        </div>
        <div className="visor-kpi-pill" data-testid="kpi-strip-caja-c">
          <span className="visor-kpi-tag tag-terracotta">Adicionales Cobrados (Bruto)</span>
          <span className="visor-kpi-val">
            {formatoMoneda(metricas?.caja_c.ganancia_adicional_total)}
          </span>
          <span className="visor-kpi-caption">{metricas?.caja_c.cantidad_operaciones || 0} recargas cobradas en el período</span>
        </div>
        <div className="visor-kpi-pill" data-testid="kpi-strip-caja-d">
          <span className="visor-kpi-tag tag-purple">Flujo Digital del Período</span>
          <span className="visor-kpi-val">
            {formatoMoneda(metricas?.caja_d.total_banco_neto)}
          </span>
          <span className="visor-kpi-caption">Transferencias netas del período</span>
        </div>
      </div>

      {/* ========================================================================== */}
      {/* SECCIÓN A: ACTIVIDAD COMERCIAL Y CONCILIACIÓN DEL PERÍODO                   */}
      {/* ========================================================================== */}
      <div className="visor-section-block" data-testid="visor-seccion-comercial">
        <div className="visor-section-header">
          <div>
            <span className="visor-section-eyebrow">SECCIÓN A · FLUJO OPERATIVO DEL PERÍODO</span>
            <h3 className="visor-section-title">🏪 Actividad Comercial (Drugstore) y Flujo de Fondos del Período</h3>
          </div>
          <span className="visor-section-badge">
            Período: {metricas?.desde} → {metricas?.hasta}
          </span>
        </div>

        <div className="dashboard-cards-grid">
          {/* CARD CAJA A: ACTIVIDAD COMERCIAL DRUGSTORE */}
          <div className="visor-card card-caja-a" data-testid="card-caja-a">
            <div className="card-top">
              <div className="card-badge badge-caja-a">Ventas Drugstore</div>
              <span className="card-icon">🏪</span>
            </div>
            <h3 className="card-title">Actividad Comercial de Productos</h3>
            <div className="card-main-metric">
              <span className="metric-label">Importe Total Vendido (Período)</span>
              <span className="metric-amount amount-success">
                {formatoMoneda(metricas?.caja_a.total_ventas)}
              </span>
            </div>
            <div className="card-submetrics-list">
              <div className="submetric-row">
                <span>Tickets emitidos:</span>
                <strong>{metricas?.caja_a.cantidad_tickets || 0}</strong>
              </div>
              <div className="submetric-row">
                <span>Cobrado en Efectivo:</span>
                <strong>{formatoMoneda(metricas?.caja_a.ventas_efvo)}</strong>
              </div>
              <div className="submetric-row">
                <span>Cobrado por Transferencia:</span>
                <strong>{formatoMoneda(metricas?.caja_a.ventas_transf)}</strong>
              </div>
              <div className="submetric-row">
                <span>Costo histórico congelado:</span>
                <strong>{formatoMoneda(metricas?.caja_a.costo_historico_total)}</strong>
              </div>
              <div className="submetric-row submetric-highlight">
                <span>Ingreso Efectivo Drugstore (Período):</span>
                <strong className="text-primary">
                  {formatoMoneda(metricas?.caja_a.efectivo_disponible_drugstore)}
                </strong>
              </div>
            </div>

            {/* Ganancia Bruta Real con Costo Histórico Congelado */}
            <div className="real-profit-box" data-testid="box-ganancia-bruta-real">
              <div className="real-profit-header">
                <span className="profit-title">Margen Bruto Verificado (Ventas − Costo Congelado):</span>
                <strong
                  className={`profit-amount ${
                    metricas?.caja_a.ganancia_bruta_real !== null && metricas?.caja_a.ganancia_bruta_real !== undefined
                      ? "profit-amount-calculated"
                      : "profit-amount-uncalculated text-gray-400"
                  }`}
                  data-testid="monto-ganancia-bruta-real"
                >
                  {metricas?.caja_a.ganancia_bruta_real !== null && metricas?.caja_a.ganancia_bruta_real !== undefined
                    ? `${formatoMoneda(metricas.caja_a.ganancia_bruta_real)}${
                        metricas.caja_a.margen_bruto_porcentaje !== null
                          ? ` (${metricas.caja_a.margen_bruto_porcentaje}%)`
                          : ""
                      }`
                    : "No calculable"}
                </strong>
              </div>

              {metricas?.caja_a.ganancia_bruta_real !== null && metricas?.caja_a.ganancia_bruta_real !== undefined ? (
                <p className="profit-formula-note">
                  Calculado sobre el costo unitario histórico congelado en cada ítem vendido.
                  {metricas.caja_a.ventas_con_costo_completo > 0 && (
                    <span> ({metricas.caja_a.ventas_con_costo_completo} ticket(s) con costo verificado).</span>
                  )}
                </p>
              ) : (
                <p className="profit-formula-note text-gray-400">
                  Sin ventas con costo congelado en este período. No se inventan costos pasados.
                </p>
              )}

              {/* Aviso de datos históricos incompletos */}
              {(metricas?.caja_a.ventas_con_costo_incompleto || 0) > 0 && (
                <div className="incomplete-data-pill" data-testid="aviso-datos-incompletos">
                  <span>⚠️ {metricas?.caja_a.ventas_con_costo_incompleto} venta(s) con datos históricos incompletos</span>
                  <span className="incomplete-detail">
                    {" "}({metricas?.caja_a.items_con_costo_desconocido || 0} línea(s) sin costo congelado).
                  </span>
                </div>
              )}
            </div>

            {/* Banner explicativo de margen 10% */}
            <div className="legacy-estimate-box" data-testid="aviso-estimacion-10">
              <div className="estimate-title">
                <span>Referencia histórica (10%):</span>
                <strong>{formatoMoneda(metricas?.caja_a.ganancia_estimada_ventas)}</strong>
              </div>
              <p className="estimate-caption">
                ⚠️ Estimación heredada, no ganancia real. (El catálogo histórico no guardaba costo unitario en el ticket).
              </p>
            </div>
          </div>

          {/* CARD CAJA D: FLUJO DIGITAL / TRANSFERENCIAS DEL PERÍODO */}
          <div className="visor-card card-caja-d" data-testid="card-caja-d">
            <div className="card-top">
              <div className="card-badge badge-caja-d">Flujo Digital</div>
              <span className="card-icon">🏦</span>
            </div>
            <h3 className="card-title">Transferencias y Flujo Digital del Período</h3>
            <div className="card-main-metric">
              <span className="metric-label">Flujo Digital Neto del Período</span>
              <span
                className={`metric-amount ${
                  (metricas?.caja_d.total_banco_neto || 0) >= 0 ? "amount-success" : "amount-danger"
                }`}
              >
                {formatoMoneda(metricas?.caja_d.total_banco_neto)}
              </span>
            </div>
            <div className="card-submetrics-list">
              <div className="submetric-row">
                <span>(+) Ventas cobradas por transf.:</span>
                <strong>{formatoMoneda(metricas?.caja_d.ventas_transf)}</strong>
              </div>
              <div className="submetric-row">
                <span>(+) Servicios cobrados por transf.:</span>
                <strong>{formatoMoneda(metricas?.caja_d.recargas_transf)}</strong>
              </div>
              <div className="submetric-row">
                <span>(−) Capital SUBE vía Mercado Pago:</span>
                <strong className="text-danger">
                  {formatoMoneda(metricas?.caja_d.sube_mp_capital)}
                </strong>
              </div>
              <div className="submetric-row">
                <span>(−) Gastos pagados por transf.:</span>
                <strong className="text-danger">
                  {formatoMoneda(metricas?.caja_d.gastos_transf)}
                </strong>
              </div>
            </div>
            <div className="card-footer-note">
              <span>ℹ️ Flujo digital devengado en el período seleccionado (ver saldos reales de cuentas digitales en Sección D).</span>
            </div>
          </div>

          {/* CARD GASTOS Y RETIROS */}
          <div className="visor-card card-gastos" data-testid="card-gastos">
            <div className="card-top">
              <div className="card-badge badge-gastos">Egresos del Período</div>
              <span className="card-icon">💸</span>
            </div>
            <h3 className="card-title">Gastos Operativos y Retiros</h3>
            <div className="card-main-metric">
              <span className="metric-label">Total Salidas Registradas</span>
              <span className="metric-amount amount-danger">
                {formatoMoneda(metricas?.gastos.gastos_total)}
              </span>
            </div>
            <div className="card-submetrics-list">
              <div className="submetric-row">
                <span>Salidas en Efectivo:</span>
                <strong className="text-danger">{formatoMoneda(metricas?.gastos.gastos_efvo)}</strong>
              </div>
              <div className="submetric-row">
                <span>Salidas por Transferencia:</span>
                <strong className="text-danger">{formatoMoneda(metricas?.gastos.gastos_transf)}</strong>
              </div>
              <div className="submetric-row">
                <span>Movimientos asentados:</span>
                <strong>{metricas?.gastos.cantidad_gastos || 0}</strong>
              </div>
            </div>
            <div className="card-footer-note">
              <span>💸 Egresos comerciales y retiros registrados en el rango consultado.</span>
            </div>
          </div>
        </div>

        {/* CONCILIACIÓN DEL FLUJO DE EFECTIVO DEL PERÍODO */}
        <div className="card-conciliacion-banner" data-testid="card-conciliacion">
          <div className="conciliacion-header">
            <div className="header-left-conciliacion">
              <span className="conciliacion-icon">📦</span>
              <div>
                <h3 className="conciliacion-title">Conciliación de Flujo de Efectivo del Período (Mostrador)</h3>
                <p className="conciliacion-desc">
                  Efectivo neto ingresado por operaciones comerciales y de servicios durante el período ({metricas?.desde} al {metricas?.hasta}), antes de cierres y traslados de Tesorería.
                </p>
              </div>
            </div>
            <div className="conciliacion-total-box">
              <span className="label-esperado">Flujo de Efectivo Operativo</span>
              <span className="amount-esperado" data-testid="total-efectivo-esperado">
                {formatoMoneda(metricas?.conciliacion_cajon.efectivo_esperado_cajon)}
              </span>
            </div>
          </div>

          <div className="conciliacion-breakdown-grid">
            <div className="breakdown-item item-plus">
              <span className="item-operator">(+)</span>
              <span className="item-label">Ventas en Efectivo:</span>
              <strong className="item-value">{formatoMoneda(metricas?.conciliacion_cajon.ventas_efvo)}</strong>
            </div>
            <div className="breakdown-item item-plus">
              <span className="item-operator">(+)</span>
              <span className="item-label">Servicios Cobrados en Efectivo:</span>
              <strong className="item-value">{formatoMoneda(metricas?.conciliacion_cajon.servicios_efvo)}</strong>
            </div>
            <div className="breakdown-item item-minus">
              <span className="item-operator">(−)</span>
              <span className="item-label">Gastos en Efectivo:</span>
              <strong className="item-value text-danger">{formatoMoneda(metricas?.conciliacion_cajon.gastos_efvo)}</strong>
            </div>
          </div>
        </div>
      </div>

      {/* ========================================================================== */}
      {/* SECCIÓN B: ACTIVIDAD DE SERVICIOS Y CARGAS VIRTUALES (CAPITAL VS ADICIONAL) */}
      {/* ========================================================================== */}
      <div className="visor-section-block" data-testid="visor-seccion-servicios">
        <div className="visor-section-header">
          <div>
            <span className="visor-section-eyebrow">SECCIÓN B · SEPARACIÓN ESTRICTA CAPITAL VS. ADICIONALES</span>
            <h3 className="visor-section-title">⚡ Actividad de Servicios y Recargas del Período</h3>
          </div>
          <span className="visor-section-badge">
            {metricas?.caja_c.cantidad_operaciones || 0} operaciones en el período
          </span>
        </div>

        <div className="dashboard-cards-grid">
          {/* CARD CAJA B: CAPITAL PREPAY */}
          <div className="visor-card card-caja-b" data-testid="card-caja-b">
            <div className="card-top">
              <div className="card-badge badge-caja-b">Capital PREPAY</div>
              <span className="card-icon">🚌</span>
            </div>
            <h3 className="card-title">PREPAY — Capital Recaudado (SUBE / Personal / Movistar)</h3>
            <div className="card-main-metric">
              <span className="metric-label">Capital de Recargas (No es ganancia)</span>
              <span className="metric-amount amount-info">
                {formatoMoneda(metricas?.caja_b.capital_total)}
              </span>
            </div>
            <div className="card-submetrics-list">
              <div className="submetric-row">
                <span>Capital cobrado en Efectivo:</span>
                <strong>{formatoMoneda(metricas?.caja_b.capital_efvo)}</strong>
              </div>
              <div className="submetric-row">
                <span>Capital cobrado por Transferencia:</span>
                <strong>{formatoMoneda(metricas?.caja_b.capital_transf)}</strong>
              </div>
              <div className="submetric-row">
                <span>Operaciones PREPAY:</span>
                <strong>{metricas?.caja_b.cantidad_operaciones || 0}</strong>
              </div>
            </div>
            <div className="card-footer-note">
              <span>ℹ️ Pasamano de capital destinado a reposición de saldo PREPAY (no constituye ganancia propia).</span>
            </div>
          </div>

          {/* CARD CAJA E: CAPITAL CLARO */}
          <div className="visor-card card-caja-e" data-testid="card-caja-e">
            <div className="card-top">
              <div className="card-badge badge-caja-e">Capital Claro</div>
              <span className="card-icon">📱</span>
            </div>
            <h3 className="card-title">Claro Comercios — Capital Recaudado</h3>
            <div className="card-main-metric">
              <span className="metric-label">Capital Recargas Claro (No es ganancia)</span>
              <span className="metric-amount amount-claro">
                {formatoMoneda(metricas?.caja_e.capital_total)}
              </span>
            </div>
            <div className="card-submetrics-list">
              <div className="submetric-row">
                <span>Capital cobrado en Efectivo:</span>
                <strong>{formatoMoneda(metricas?.caja_e.capital_efvo)}</strong>
              </div>
              <div className="submetric-row">
                <span>Capital cobrado por Transferencia:</span>
                <strong>{formatoMoneda(metricas?.caja_e.capital_transf)}</strong>
              </div>
              <div className="submetric-row">
                <span>Operaciones Claro:</span>
                <strong>{metricas?.caja_e.cantidad_operaciones || 0}</strong>
              </div>
            </div>
            <div className="card-footer-note">
              <span>📱 Capital de telefonía Claro recaudado en custodia para rendición.</span>
            </div>
          </div>

          {/* CARD CAJA C: ADICIONALES BRUTOS COBRADOS */}
          <div className="visor-card card-caja-c" data-testid="card-caja-c">
            <div className="card-top">
              <div className="card-badge badge-caja-c">Adicionales Servicios</div>
              <span className="card-icon">⚡</span>
            </div>
            <h3 className="card-title">Adicionales Cobrados en Servicios</h3>
            <div className="card-main-metric">
              <span className="metric-label">Total Adicionales Brutos del Período</span>
              <span className="metric-amount amount-accent">
                {formatoMoneda(metricas?.caja_c.ganancia_adicional_total)}
              </span>
            </div>
            <div className="card-submetrics-list">
              <div className="submetric-row">
                <span>Adicionales en Efectivo:</span>
                <strong>{formatoMoneda(metricas?.caja_c.adicional_efvo)}</strong>
              </div>
              <div className="submetric-row">
                <span>Adicionales por Transferencia:</span>
                <strong>{formatoMoneda(metricas?.caja_c.adicional_transf)}</strong>
              </div>
              <div className="submetric-row">
                <span>Total cargas procesadas:</span>
                <strong>{metricas?.caja_c.cantidad_operaciones || 0}</strong>
              </div>
            </div>
            <div className="card-footer-note note-accent">
              <span>ℹ️ Adicionales brutos cobrados por recargas (separados del capital; sujetos a cargos y costos operativos).</span>
            </div>
          </div>
        </div>

        {/* Desglose por Plataforma de Origen y Servicio Destino */}
        {metricas?.desglose_servicios && metricas.desglose_servicios.length > 0 && (
          <div className="visor-subtable-card" data-testid="tabla-desglose-servicios">
            <h4 className="visor-subtable-title">🔎 Desglose de Servicios por Plataforma de Origen, Destino y Circuito</h4>
            <div className="cierres-table-wrapper">
              <table className="cierres-table">
                <thead>
                  <tr>
                    <th>Plataforma Origen</th>
                    <th>Servicio Destino</th>
                    <th>Medio de Pago</th>
                    <th>Cuenta Utilizada</th>
                    <th>Fondo Receptor</th>
                    <th className="th-number">Operaciones</th>
                    <th className="th-number">Capital (Pasamano)</th>
                    <th className="th-number">Adicional Cobrado</th>
                    <th className="th-number">Total Cobrado</th>
                    <th className="th-number">Efectivo</th>
                    <th className="th-number">Transferencia</th>
                  </tr>
                </thead>
                <tbody>
                  {metricas.desglose_servicios.map((item, idx) => {
                    const medioPago =
                      item.medio_pago ||
                      (item.cobrado_efectivo > 0 && item.cobrado_transferencia > 0
                        ? "MIXTO"
                        : item.cobrado_transferencia > 0
                        ? "TRANSFERENCIA"
                        : "EFECTIVO");
                    const cuentaUtilizada =
                      item.cuenta_utilizada ||
                      (item.plataforma_origen === "CLARO COMERCIOS" || item.categoria === "CLARO"
                        ? "1230 — Claro Comercios Digital"
                        : item.plataforma_origen === "MERCADO PAGO" || item.categoria === "SUBE_MP"
                        ? "1210 — Mercado Pago Propio"
                        : item.plataforma_origen.startsWith("Histórico")
                        ? "Histórico (sin cuenta origen registrada)"
                        : "1220 — PREPAY Digital");
                    const fondoReceptor =
                      item.fondo_receptor ||
                      (item.plataforma_origen === "CLARO COMERCIOS" || item.categoria === "CLARO"
                        ? item.cobrado_transferencia > 0 && item.cobrado_efectivo === 0
                          ? "1210 (Mercado Pago Propio)"
                          : "1150 (Claro físico) + 1140 (Adicionales)"
                        : item.plataforma_origen === "MERCADO PAGO" || item.categoria === "SUBE_MP"
                        ? item.cobrado_transferencia > 0 && item.cobrado_efectivo === 0
                          ? "1210 (Mercado Pago Propio)"
                          : "1110 (Drugstore diario) + 1140 (Adicionales)"
                        : item.cobrado_transferencia > 0 && item.cobrado_efectivo === 0
                        ? "1210 (Mercado Pago) + Compensación 1120 → 1130"
                        : "1160 (PREPAY diario) + 1140 (Adicionales)");

                    return (
                      <tr key={`${item.plataforma_origen}-${item.servicio_destino}-${idx}`}>
                        <td><span className="badge-cat">{item.plataforma_origen}</span></td>
                        <td><strong>{item.servicio_destino}</strong></td>
                        <td><span className="badge-cat">{medioPago}</span></td>
                        <td><span className="cell-obs">{cuentaUtilizada}</span></td>
                        <td><span className="cell-obs">{fondoReceptor}</span></td>
                        <td className="td-number">{item.cantidad_operaciones}</td>
                        <td className="td-number">{formatoMoneda(item.capital_total)}</td>
                        <td className="td-number text-green"><strong>{formatoMoneda(item.adicional_total)}</strong></td>
                        <td className="td-number"><strong>{formatoMoneda(item.total_cobrado)}</strong></td>
                        <td className="td-number">{formatoMoneda(item.cobrado_efectivo)}</td>
                        <td className="td-number">{formatoMoneda(item.cobrado_transferencia)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      {/* ========================================================================== */}
      {/* SECCIÓN C: FONDOS FÍSICOS REALES DE TESORERÍA (SALDOS ACTUALES)            */}
      {/* ========================================================================== */}
      <div className="visor-section-block" data-testid="visor-seccion-fondos-fisicos">
        <div className="visor-section-header">
          <div>
            <span className="visor-section-eyebrow">SECCIÓN C · PATRIMONIO FÍSICO AUDITADO EN TESORERÍA</span>
            <h3 className="visor-section-title">💵 Fondos Físicos Reales (Saldos Actuales en Efectivo)</h3>
          </div>
          <span className="visor-section-badge badge-emerald">
            Total Físico: {formatoCentavos(
              saldosTesoreria.fondos_fisicos.reduce((acc, f) => acc + (f.saldo_actual_cents ?? 0), 0)
            )}
          </span>
        </div>

        <div className="visor-treasury-grid">
          {saldosTesoreria.fondos_fisicos.map((fondo) => (
            <div
              key={fondo.codigo}
              className="visor-treasury-card treasury-physical"
              data-testid={`visor-fondo-${fondo.codigo}`}
            >
              <div className="treasury-card-top">
                <span className="treasury-code-pill">{fondo.codigo}</span>
                <span className="treasury-status-pill">
                  {fondo.estado_configuracion === "CONFIGURADO" ? "Apertura Configurada" : "Inicial $0"}
                </span>
              </div>
              <h4 className="treasury-card-name">{fondo.nombre}</h4>
              <div className="treasury-card-balance">
                {formatoCentavos(fondo.saldo_actual_cents)}
              </div>
              <div className="treasury-card-meta">
                <span>Saldo inicial: {formatoCentavos(fondo.saldo_inicial_cents)}</span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* ========================================================================== */}
      {/* SECCIÓN D: CUENTAS DIGITALES REALES DE TESORERÍA (SALDOS ACTUALES)         */}
      {/* ========================================================================== */}
      <div className="visor-section-block" data-testid="visor-seccion-cuentas-digitales">
        <div className="visor-section-header">
          <div>
            <span className="visor-section-eyebrow">SECCIÓN D · SALDOS DIGITALES Y BILLETERAS DE TESORERÍA</span>
            <h3 className="visor-section-title">💳 Cuentas Digitales y Tránsito (Saldos Actuales)</h3>
          </div>
          <span className="visor-section-badge badge-blue">
            Total Digital: {formatoCentavos(
              saldosTesoreria.cuentas_digitales.reduce((acc, c) => acc + (c.saldo_actual_cents ?? 0), 0)
            )}
          </span>
        </div>

        <div className="visor-treasury-grid">
          {saldosTesoreria.cuentas_digitales.map((cuenta) => (
            <div
              key={cuenta.codigo}
              className="visor-treasury-card treasury-digital"
              data-testid={`visor-fondo-${cuenta.codigo}`}
            >
              <div className="treasury-card-top">
                <span className="treasury-code-pill pill-digital">{cuenta.codigo}</span>
                <span className="treasury-status-pill">
                  {cuenta.estado_configuracion === "CONFIGURADO" ? "Apertura Configurada" : "Inicial $0"}
                </span>
              </div>
              <h4 className="treasury-card-name">{cuenta.nombre}</h4>
              <div className="treasury-card-balance">
                {formatoCentavos(cuenta.saldo_actual_cents)}
              </div>
              <div className="treasury-card-meta">
                <span>Saldo inicial: {formatoCentavos(cuenta.saldo_inicial_cents)}</span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* ========================================================================== */}
      {/* SECCIÓN E: OBLIGACIONES Y CUENTAS CORRIENTES (SEPARADAS DE ACTIVOS)        */}
      {/* ========================================================================== */}
      <div className="visor-section-block" data-testid="visor-seccion-obligaciones">
        <div className="visor-section-header">
          <div>
            <span className="visor-section-eyebrow">SECCIÓN E · PASIVOS, RENDICIONES Y REPOSICIONES PENDIENTES</span>
            <h3 className="visor-section-title">⚖️ Estado Actual de Obligaciones (Separadas de los Fondos Propios)</h3>
          </div>
          <span className="visor-section-badge badge-amber">
            Apertura Obligaciones: {formatoCentavos(saldosTesoreria.total_obligaciones_cents)}
          </span>
        </div>

        <div className="visor-treasury-grid">
          {saldosTesoreria.obligaciones.map((obl) => {
            const codigoCan = obl.codigo.startsWith("OBL_") ? obl.codigo : `OBL_${obl.codigo}`;
            const esDavid = codigoCan === "OBL_DAVID";
            const esEzequiel = codigoCan === "OBL_CLARO";
            const esPrev = codigoCan === "OBL_PREVENTISTAS";
            const saldoOperativoCents = esDavid && ccDavid
              ? (ccDavid.obligacion_pendiente_cents ?? (ccDavid as unknown as Record<string, number>).obligacion_pendiente_david_cents ?? obl.saldo_actual_cents)
              : esEzequiel && ccEzequiel
              ? (ccEzequiel.obligacion_pendiente_cents ?? obl.saldo_actual_cents)
              : esPrev && resumenPreventistas
              ? (resumenPreventistas.total_pendiente_reposicion_cents ?? (resumenPreventistas as unknown as Record<string, number>).obligacion_pendiente_reposicion_cents ?? obl.saldo_actual_cents)
              : obl.saldo_actual_cents;

            return (
              <div
                key={codigoCan}
                className="visor-treasury-card treasury-obligation"
                data-testid={`visor-obligacion-${codigoCan}`}
              >
                <div className="treasury-card-top">
                  <span className="treasury-code-pill pill-obligation">{codigoCan}</span>
                  <span className="treasury-status-pill">Pasivo / Compromiso</span>
                </div>
                <h4 className="treasury-card-name">{obl.nombre}</h4>
                <div className="treasury-card-balance text-amber">
                  {formatoCentavos(saldoOperativoCents)}
                </div>
                <div className="treasury-card-meta">
                  <span>Apertura inicial: {formatoCentavos(obl.saldo_inicial_cents)}</span>
                  {esDavid && ccDavid && (
                    <span> · {ccDavid.cantidad_movimientos ?? 0} mov(s) registrados</span>
                  )}
                  {esEzequiel && ccEzequiel && (
                    <span> · {ccEzequiel.cantidad_movimientos ?? 0} mov(s) registrados</span>
                  )}
                  {esPrev && resumenPreventistas && (
                    <span> · {resumenPreventistas.cantidad_anticipos ?? 0} anticipo(s)</span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Gráfico SVG de Serie Temporal */}
      {renderGraficoEvolucion()}

      {/* Tabla de Cierres Diarios del Período */}
      <div className="cierres-historial-section">
        <h4 className="cierres-title">🔒 Arqueos y Cierres Diarios del Período</h4>
        {(!metricas?.cierres || metricas.cierres.length === 0) ? (
          <div className="cierres-vacio-note">
            <span>No se registraron cierres de caja en el período consultado.</span>
          </div>
        ) : (
          <div className="cierres-table-wrapper">
            <table className="cierres-table">
              <thead>
                <tr>
                  <th>Fecha</th>
                  <th>Efectivo Esperado</th>
                  <th>Efectivo Contado</th>
                  <th>Diferencia</th>
                  <th>Responsable</th>
                  <th>Observaciones</th>
                </tr>
              </thead>
              <tbody>
                {metricas.cierres.map((c) => {
                  const dif = c.diferencia;
                  return (
                    <tr key={c.fecha}>
                      <td><strong>{c.fecha}</strong></td>
                      <td>{formatoMoneda(c.efectivo_esperado)}</td>
                      <td>{formatoMoneda(c.efectivo_contado)}</td>
                      <td>
                        <span
                          className={`badge-diferencia ${
                            dif === 0
                              ? "dif-exacta"
                              : dif > 0
                              ? "dif-sobrante"
                              : "dif-faltante"
                          }`}
                        >
                          {dif > 0 ? `+${formatoMoneda(dif)}` : formatoMoneda(dif)}
                        </span>
                      </td>
                      <td>{c.cerrado_por || "Admin"}</td>
                      <td className="cell-obs">{c.observaciones || "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};
