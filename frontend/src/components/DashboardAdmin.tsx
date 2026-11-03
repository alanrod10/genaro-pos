import React, { useState, useEffect, useCallback } from "react";
import { PeriodoTipo, MetricasDashboard } from "../types/dashboard";
import { getApiClient } from "../services/api";

interface DashboardAdminProps {
  onAddToast: (mensaje: string, tipo?: "info" | "success" | "warning" | "error") => void;
  isDemo: boolean;
}

export const DashboardAdmin: React.FC<DashboardAdminProps> = ({ onAddToast, isDemo }) => {
  const [periodo, setPeriodo] = useState<PeriodoTipo>("hoy");
  const [desdePersonalizado, setDesdePersonalizado] = useState<string>("");
  const [hastaPersonalizado, setHastaPersonalizado] = useState<string>("");
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [metricas, setMetricas] = useState<MetricasDashboard | null>(null);

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
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error inesperado";
      onAddToast(`Error: ${msg}`, "error");
    } finally {
      setIsLoading(false);
    }
  }, [periodo, onAddToast]);

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

    const svgHeight = 220;
    const paddingBottom = 30;
    const paddingTop = 20;
    const chartHeight = svgHeight - paddingBottom - paddingTop;
    const barGroupWidth = Math.max(24, Math.floor(600 / puntos.length));
    const svgWidth = Math.max(650, puntos.length * barGroupWidth + 40);

    return (
      <div className="dashboard-chart-container" data-testid="svg-chart-evolucion">
        <div className="chart-header">
          <h4 className="chart-title">📈 Evolución Diaria (Ventas, Servicios y Gastos)</h4>
          <div className="chart-legend">
            <span className="legend-item"><span className="legend-dot dot-ventas"></span> Ventas</span>
            <span className="legend-item"><span className="legend-dot dot-servicios"></span> Servicios</span>
            <span className="legend-item"><span className="legend-dot dot-gastos"></span> Gastos</span>
          </div>
        </div>
        <div className="svg-scroll-wrapper">
          <svg
            viewBox={`0 0 ${svgWidth} ${svgHeight}`}
            className="dashboard-svg-chart"
            preserveAspectRatio="none"
          >
            {/* Eje horizontal */}
            <line
              x1="20"
              y1={svgHeight - paddingBottom}
              x2={svgWidth - 20}
              y2={svgHeight - paddingBottom}
              stroke="var(--border-color)"
              strokeWidth="1"
            />

            {puntos.map((p, idx) => {
              const xCenter = 30 + idx * barGroupWidth + barGroupWidth / 2;
              const subBarW = Math.max(3, Math.floor(barGroupWidth / 4));

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
                    <title>{`${p.etiqueta} - Ventas: ${formatoMoneda(p.ventas_total)}`}</title>
                  </rect>

                  {/* Barra Servicios */}
                  <rect
                    x={xCenter - subBarW * 0.5}
                    y={yServ}
                    width={subBarW}
                    height={Math.max(1, hServ)}
                    fill="var(--terracotta)"
                    rx="3"
                  >
                    <title>{`${p.etiqueta} - Servicios: ${formatoMoneda(p.servicios_total)}`}</title>
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
                    <title>{`${p.etiqueta} - Gastos: ${formatoMoneda(p.gastos_total)}`}</title>
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

  return (
    <div className="dashboard-admin-view" data-testid="dashboard-admin">
      {/* Encabezado del Visor */}
      <div className="dashboard-header-panel">
        <div className="dashboard-title-area">
          <h2 className="dashboard-main-title">📊 Visor Financiero y Reportes</h2>
          <p className="dashboard-subtitle">
            Segregación contable integral por cajas, conciliación física de cajón y arqueo heredado.
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

      {/* Tira Ejecutiva de Métricas Clave: Las Cuatro Cajas Comerciales (A, B, C, D) + Cajón Efectivo */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2.5 sm:gap-3 my-4" data-testid="dashboard-kpi-strip">
        <div className="p-3.5 bg-white dark:bg-gray-900 rounded-2xl border border-gray-200 dark:border-gray-800 shadow-sm" data-testid="kpi-strip-cajon">
          <span className="text-[10px] uppercase font-bold text-gray-500 dark:text-gray-400 tracking-wider block">Cajón Efectivo</span>
          <span className="text-lg sm:text-xl font-black font-mono text-gray-900 dark:text-gray-100 block mt-1" style={{ fontVariantNumeric: "tabular-nums" }}>
            {formatoMoneda(metricas?.conciliacion_cajon.efectivo_esperado_cajon)}
          </span>
          <span className="text-[10px] text-gray-400">Físico en mostrador</span>
        </div>
        <div className="p-3.5 bg-white dark:bg-gray-900 rounded-2xl border border-gray-200 dark:border-gray-800 shadow-sm" data-testid="kpi-strip-caja-a">
          <span className="text-[10px] uppercase font-bold text-emerald-600 dark:text-emerald-400 tracking-wider block">Caja A (Ventas)</span>
          <span className="text-lg sm:text-xl font-black font-mono text-gray-900 dark:text-gray-100 block mt-1" style={{ fontVariantNumeric: "tabular-nums" }}>
            {formatoMoneda(metricas?.caja_a.total_ventas)}
          </span>
          <span className="text-[10px] text-gray-400">{metricas?.caja_a.cantidad_tickets || 0} tickets emitidos</span>
        </div>
        <div className="p-3.5 bg-white dark:bg-gray-900 rounded-2xl border border-gray-200 dark:border-gray-800 shadow-sm" data-testid="kpi-strip-caja-b">
          <span className="text-[10px] uppercase font-bold text-blue-600 dark:text-blue-400 tracking-wider block">Caja B (SUBE/Prepay)</span>
          <span className="text-lg sm:text-xl font-black font-mono text-gray-900 dark:text-gray-100 block mt-1" style={{ fontVariantNumeric: "tabular-nums" }}>
            {formatoMoneda(metricas?.caja_b.capital_total)}
          </span>
          <span className="text-[10px] text-gray-400">Capital a rendir</span>
        </div>
        <div className="p-3.5 bg-white dark:bg-gray-900 rounded-2xl border border-gray-200 dark:border-gray-800 shadow-sm" data-testid="kpi-strip-caja-c">
          <span className="text-[10px] uppercase font-bold text-[#C97B5A] dark:text-[#E09070] tracking-wider block">Caja C (Ganancia Serv.)</span>
          <span className="text-lg sm:text-xl font-black font-mono text-gray-900 dark:text-gray-100 block mt-1" style={{ fontVariantNumeric: "tabular-nums" }}>
            {formatoMoneda(metricas?.caja_c.ganancia_adicional_total)}
          </span>
          <span className="text-[10px] text-gray-400">{metricas?.caja_c.cantidad_operaciones || 0} recargas cobradas</span>
        </div>
        <div className="p-3.5 bg-white dark:bg-gray-900 rounded-2xl border border-gray-200 dark:border-gray-800 shadow-sm col-span-2 sm:col-span-1" data-testid="kpi-strip-caja-d">
          <span className="text-[10px] uppercase font-bold text-indigo-600 dark:text-indigo-400 tracking-wider block">Caja D (Banco Neto)</span>
          <span className="text-lg sm:text-xl font-black font-mono text-gray-900 dark:text-gray-100 block mt-1" style={{ fontVariantNumeric: "tabular-nums" }}>
            {formatoMoneda(metricas?.caja_d.total_banco_neto)}
          </span>
          <span className="text-[10px] text-gray-400">Transferencias conciliadas</span>
        </div>
      </div>

      <div className="mt-6 mb-2">
        <h3 className="text-xs font-bold uppercase tracking-wider text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
          <span>🏛️</span> Las Cuatro Cajas Comerciales (A, B, C, D)
        </h3>
      </div>

      {/* Grid de Tarjetas Principales (Cajas A, B, C, D, E, Gastos, Conciliación) */}
      <div className="dashboard-cards-grid">
        {/* CARD CAJA A: DRUGSTORE */}
        <div className="visor-card card-caja-a" data-testid="card-caja-a">
          <div className="card-top">
            <div className="card-badge badge-caja-a">Caja A</div>
            <span className="card-icon">🏪</span>
          </div>
          <h3 className="card-title">Drugstore (Ventas)</h3>
          <div className="card-main-metric">
            <span className="metric-label">Total Ventas</span>
            <span className="metric-amount amount-success">
              {formatoMoneda(metricas?.caja_a.total_ventas)}
            </span>
          </div>
          <div className="card-submetrics-list">
            <div className="submetric-row">
              <span>Efectivo cobrado:</span>
              <strong>{formatoMoneda(metricas?.caja_a.ventas_efvo)}</strong>
            </div>
            <div className="submetric-row">
              <span>Transferencias:</span>
              <strong>{formatoMoneda(metricas?.caja_a.ventas_transf)}</strong>
            </div>
            <div className="submetric-row submetric-highlight">
              <span>Efectivo disponible Drugstore:</span>
              <strong className="text-primary">
                {formatoMoneda(metricas?.caja_a.efectivo_disponible_drugstore)}
              </strong>
            </div>
            <div className="submetric-row">
              <span>Tickets emitidos:</span>
              <strong>{metricas?.caja_a.cantidad_tickets || 0}</strong>
            </div>
          </div>

          {/* Ganancia Bruta Real con Costo Histórico Congelado */}
          <div className="real-profit-box" data-testid="box-ganancia-bruta-real">
            <div className="real-profit-header">
              <span className="profit-title">Ganancia Bruta Real:</span>
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
                Fórmula: Subtotal de ventas con costo congelado menos costo histórico de artículos.
                {metricas.caja_a.ventas_con_costo_completo > 0 && (
                  <span> ({metricas.caja_a.ventas_con_costo_completo} venta(s) con costo verificado).</span>
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
              <span>Estimación heredada (10%):</span>
              <strong>{formatoMoneda(metricas?.caja_a.ganancia_estimada_ventas)}</strong>
            </div>
            <p className="estimate-caption">
              ⚠️ Estimación heredada, no ganancia real. (El catálogo histórico no guardaba costo unitario en el ticket).
            </p>
          </div>
        </div>

        {/* CARD CAJA B: SUBE / PREPAY */}
        <div className="visor-card card-caja-b" data-testid="card-caja-b">
          <div className="card-top">
            <div className="card-badge badge-caja-b">Caja B</div>
            <span className="card-icon">🚌</span>
          </div>
          <h3 className="card-title">SUBE / Prepay (Solo Capital)</h3>
          <div className="card-main-metric">
            <span className="metric-label">Capital Recaudado</span>
            <span className="metric-amount amount-info">
              {formatoMoneda(metricas?.caja_b.capital_total)}
            </span>
          </div>
          <div className="card-submetrics-list">
            <div className="submetric-row">
              <span>Capital en Efectivo:</span>
              <strong>{formatoMoneda(metricas?.caja_b.capital_efvo)}</strong>
            </div>
            <div className="submetric-row">
              <span>Capital Transferencias:</span>
              <strong>{formatoMoneda(metricas?.caja_b.capital_transf)}</strong>
            </div>
            <div className="submetric-row">
              <span>Operaciones:</span>
              <strong>{metricas?.caja_b.cantidad_operaciones || 0}</strong>
            </div>
          </div>
          <div className="card-footer-note">
            <span>ℹ️ Capital de recargas a rendir (no constituye ganancia propia).</span>
          </div>
        </div>

        {/* CARD CAJA C: ADICIONALES (GANANCIA SERVICIOS) */}
        <div className="visor-card card-caja-c" data-testid="card-caja-c">
          <div className="card-top">
            <div className="card-badge badge-caja-c">Caja C</div>
            <span className="card-icon">⚡</span>
          </div>
          <h3 className="card-title">Adicionales (Ganancia Servicios)</h3>
          <div className="card-main-metric">
            <span className="metric-label">Ganancia Servicios</span>
            <span className="metric-amount amount-accent">
              {formatoMoneda(metricas?.caja_c.ganancia_adicional_total)}
            </span>
          </div>
          <div className="card-submetrics-list">
            <div className="submetric-row">
              <span>Recargos en Efectivo:</span>
              <strong>{formatoMoneda(metricas?.caja_c.adicional_efvo)}</strong>
            </div>
            <div className="submetric-row">
              <span>Recargos por Transferencia:</span>
              <strong>{formatoMoneda(metricas?.caja_c.adicional_transf)}</strong>
            </div>
            <div className="submetric-row">
              <span>Servicios prestados:</span>
              <strong>{metricas?.caja_c.cantidad_operaciones || 0}</strong>
            </div>
          </div>
          <div className="card-footer-note note-accent">
            <span>✅ Ganancia neta directa por comisiones y recargos al cliente.</span>
          </div>
        </div>

        {/* CARD CAJA D: BANCO / TRANSFERENCIAS */}
        <div className="visor-card card-caja-d" data-testid="card-caja-d">
          <div className="card-top">
            <div className="card-badge badge-caja-d">Caja D</div>
            <span className="card-icon">🏦</span>
          </div>
          <h3 className="card-title">Transferencias (Total en Banco)</h3>
          <div className="card-main-metric">
            <span className="metric-label">Total Banco Neto</span>
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
              <span>(+) Ventas por transf.:</span>
              <strong>{formatoMoneda(metricas?.caja_d.ventas_transf)}</strong>
            </div>
            <div className="submetric-row">
              <span>(+) Recargas por transferencia:</span>
              <strong>{formatoMoneda(metricas?.caja_d.recargas_transf)}</strong>
            </div>
            <div className="submetric-row">
              <span>(-) SUBE (MP) Capital:</span>
              <strong className="text-danger">
                {formatoMoneda(metricas?.caja_d.sube_mp_capital)}
              </strong>
            </div>
            <div className="submetric-row">
              <span>(-) Gastos por transf.:</span>
              <strong className="text-danger">
                {formatoMoneda(metricas?.caja_d.gastos_transf)}
              </strong>
            </div>
          </div>
          <div className="card-footer-note">
            <span>🏦 Saldo bancario/MP conciliado según movimientos del período.</span>
          </div>
        </div>

        {/* CARD CAJA E: CLARO */}
        <div className="visor-card card-caja-e" data-testid="card-caja-e">
          <div className="card-top">
            <div className="card-badge badge-caja-e">Caja E</div>
            <span className="card-icon">📱</span>
          </div>
          <h3 className="card-title">Claro (Solo Capital)</h3>
          <div className="card-main-metric">
            <span className="metric-label">Capital Telefonía</span>
            <span className="metric-amount amount-claro">
              {formatoMoneda(metricas?.caja_e.capital_total)}
            </span>
          </div>
          <div className="card-submetrics-list">
            <div className="submetric-row">
              <span>Capital en Efectivo:</span>
              <strong>{formatoMoneda(metricas?.caja_e.capital_efvo)}</strong>
            </div>
            <div className="submetric-row">
              <span>Capital Transferencias:</span>
              <strong>{formatoMoneda(metricas?.caja_e.capital_transf)}</strong>
            </div>
            <div className="submetric-row">
              <span>Recargas Claro:</span>
              <strong>{metricas?.caja_e.cantidad_operaciones || 0}</strong>
            </div>
          </div>
          <div className="card-footer-note">
            <span>📱 Capital de telefonía prepaga a rendir.</span>
          </div>
        </div>

        {/* CARD GASTOS Y RETIROS */}
        <div className="visor-card card-gastos" data-testid="card-gastos">
          <div className="card-top">
            <div className="card-badge badge-gastos">Egresos</div>
            <span className="card-icon">💸</span>
          </div>
          <h3 className="card-title">Gastos y Retiros</h3>
          <div className="card-main-metric">
            <span className="metric-label">Total Egresos</span>
            <span className="metric-amount amount-danger">
              {formatoMoneda(metricas?.gastos.gastos_total)}
            </span>
          </div>
          <div className="card-submetrics-list">
            <div className="submetric-row">
              <span>Gastos en Efectivo:</span>
              <strong className="text-danger">{formatoMoneda(metricas?.gastos.gastos_efvo)}</strong>
            </div>
            <div className="submetric-row">
              <span>Gastos por Transferencia:</span>
              <strong className="text-danger">{formatoMoneda(metricas?.gastos.gastos_transf)}</strong>
            </div>
            <div className="submetric-row">
              <span>Operaciones registradas:</span>
              <strong>{metricas?.gastos.cantidad_gastos || 0}</strong>
            </div>
          </div>
          <div className="card-footer-note">
            <span>💸 Salidas operativas y retiros pagados desde caja o banco.</span>
          </div>
        </div>
      </div>

      {/* TARJETA DESTACADA: CONCILIACIÓN FÍSICA DE CAJÓN */}
      <div className="card-conciliacion-banner" data-testid="card-conciliacion">
        <div className="conciliacion-header">
          <div className="header-left-conciliacion">
            <span className="conciliacion-icon">📦</span>
            <div>
              <h3 className="conciliacion-title">Conciliación Física de Cajón (Efectivo Esperado)</h3>
              <p className="conciliacion-desc">
                Cálculo de billetes que deben existir físicamente en el cajón de dinero.
              </p>
            </div>
          </div>
          <div className="conciliacion-total-box">
            <span className="label-esperado">Efectivo Físico Esperado</span>
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
            <span className="item-label">Servicios en Efectivo:</span>
            <strong className="item-value">{formatoMoneda(metricas?.conciliacion_cajon.servicios_efvo)}</strong>
          </div>
          <div className="breakdown-item item-minus">
            <span className="item-operator">(-)</span>
            <span className="item-label">Gastos en Efectivo:</span>
            <strong className="item-value text-danger">{formatoMoneda(metricas?.conciliacion_cajon.gastos_efvo)}</strong>
          </div>
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
