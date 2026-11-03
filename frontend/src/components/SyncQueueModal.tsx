import React, { useState } from "react";
import { DemoSimulationMode, MockApiClient } from "../services/api";
import { GastoColaLocal, ServicioColaLocal } from "../services/db";
import { EstadoSincronizacion, VentaLocal } from "../types/sales";

interface SyncQueueModalProps {
  ventas: VentaLocal[];
  serviciosCola?: ServicioColaLocal[];
  gastosCola?: GastoColaLocal[];
  isDemo: boolean;
  isReadOnlySmoke?: boolean;
  mockClient?: MockApiClient;
  onClose: () => void;
  onSyncAll: () => Promise<void>;
  onSyncOne: (ticketId: string) => Promise<void>;
}

export const SyncQueueModal: React.FC<SyncQueueModalProps> = ({
  ventas,
  serviciosCola = [],
  gastosCola = [],
  isDemo,
  isReadOnlySmoke = false,
  mockClient,
  onClose,
  onSyncAll,
  onSyncOne,
}) => {
  const [filtro, setFiltro] = useState<EstadoSincronizacion | "TODAS">("TODAS");
  const [isSyncing, setIsSyncing] = useState<boolean>(false);
  const [demoMode, setDemoMode] = useState<DemoSimulationMode>(
    mockClient?.getSimulationMode() || "exito_201"
  );

  const pendientesVentas = ventas.filter((v) => v.estado === "pendiente");
  const sincronizadasVentas = ventas.filter((v) => v.estado === "sincronizada");
  const revisionVentas = ventas.filter((v) => v.estado === "requiere_revision");

  const pendientesServicios = serviciosCola.filter((s) => s.estado === "pendiente");
  const sincronizadasServicios = serviciosCola.filter((s) => s.estado === "sincronizada");
  const revisionServicios = serviciosCola.filter((s) => s.estado === "requiere_revision");

  const pendientesGastos = gastosCola.filter((g) => g.estado === "pendiente");
  const sincronizadasGastos = gastosCola.filter((g) => g.estado === "sincronizada");
  const revisionGastos = gastosCola.filter((g) => g.estado === "requiere_revision");

  const totalCount = ventas.length + serviciosCola.length + gastosCola.length;
  const totalPendientesCount =
    pendientesVentas.length + pendientesServicios.length + pendientesGastos.length;
  const totalSincronizadasCount =
    sincronizadasVentas.length + sincronizadasServicios.length + sincronizadasGastos.length;
  const totalRevisionCount =
    revisionVentas.length + revisionServicios.length + revisionGastos.length;

  const filtradas = ventas.filter((v) => {
    if (filtro === "TODAS") return true;
    return v.estado === filtro;
  });

  const serviciosFiltrados = serviciosCola.filter((s) => {
    if (filtro === "TODAS") return true;
    return s.estado === filtro;
  });

  const gastosFiltrados = gastosCola.filter((g) => {
    if (filtro === "TODAS") return true;
    return g.estado === filtro;
  });

  const handleSyncAll = async () => {
    if (isReadOnlySmoke) return;
    setIsSyncing(true);
    try {
      await onSyncAll();
    } finally {
      setIsSyncing(false);
    }
  };

  const handleSyncOne = async (ticketId: string) => {
    if (isReadOnlySmoke) return;
    setIsSyncing(true);
    try {
      await onSyncOne(ticketId);
    } finally {
      setIsSyncing(false);
    }
  };

  const handleSetDemoMode = (mode: DemoSimulationMode) => {
    setDemoMode(mode);
    if (mockClient) {
      mockClient.setSimulationMode(mode);
    }
  };

  return (
    <div className="modal-backdrop" data-testid="sync-queue-modal">
      <div className="modal-card modal-card-large">
        <div className="modal-header">
          <div className="modal-title-row">
            <span className="queue-title-icon">📤</span>
            <h3 className="modal-title">
              {isDemo
                ? "Cola Local de Ventas — Almacén Demo (Simulado)"
                : "Cola Operativa de Ventas (IndexedDB)"}
            </h3>
          </div>
          <button
            type="button"
            className="btn-modal-close"
            onClick={onClose}
            data-testid="btn-close-queue"
          >
            ✕
          </button>
        </div>

        <div className="modal-body">
          {/* Banner de aclaración de modo Demo vs Operativo */}
          {isDemo && (
            <div
              className="demo-simulation-notice"
              data-testid="demo-simulation-notice"
            >
              <span>ℹ️</span>
              <span>
                <strong>Almacén Demo Exclusivo:</strong> Los tickets y el estado{" "}
                <code>SINCRONIZADA</code> aquí listados son simulados localmente en{" "}
                <code>ventas_demo</code>; no representan confirmaciones del servidor ni
                escrituras en Turso DEV/PROD.
              </span>
            </div>
          )}
          {/* Panel de Control de Simulación en Modo Demo */}
          {isDemo && mockClient && (
            <div className="demo-control-panel" data-testid="demo-control-panel">
              <div className="demo-panel-title">
                🧪 <strong>Simulador de Respuestas del Backend (Modo Demo Seguro):</strong>
              </div>
              <p className="demo-panel-desc">
                Permite verificar el comportamiento de la cola ante respuestas 201, 200, 409 y
                caídas de red sin conectarse a Turso ni escribir en producción.
              </p>
              <div className="demo-sim-buttons">
                <button
                  type="button"
                  className={`btn-sim ${demoMode === "exito_201" ? "sim-active" : ""}`}
                  onClick={() => handleSetDemoMode("exito_201")}
                  data-testid="sim-201"
                >
                  🟢 Éxito (HTTP 201)
                </button>
                <button
                  type="button"
                  className={`btn-sim ${demoMode === "reintento_200" ? "sim-active" : ""}`}
                  onClick={() => handleSetDemoMode("reintento_200")}
                  data-testid="sim-200"
                >
                  🔵 Reintento Idempotente (HTTP 200)
                </button>
                <button
                  type="button"
                  className={`btn-sim ${demoMode === "conflicto_409" ? "sim-active" : ""}`}
                  onClick={() => handleSetDemoMode("conflicto_409")}
                  data-testid="sim-409"
                >
                  🔴 Conflicto Datos (HTTP 409)
                </button>
                <button
                  type="button"
                  className={`btn-sim ${demoMode === "timeout" ? "sim-active" : ""}`}
                  onClick={() => handleSetDemoMode("timeout")}
                  data-testid="sim-timeout"
                >
                  ⚠️ Error de Red / Timeout
                </button>
              </div>
            </div>
          )}

          {/* Filtros de la Cola */}
          <div className="queue-filters">
            <button
              type="button"
              className={`queue-filter-tab ${filtro === "TODAS" ? "tab-active" : ""}`}
              onClick={() => setFiltro("TODAS")}
            >
              Todas ({totalCount})
            </button>
            <button
              type="button"
              className={`queue-filter-tab ${filtro === "pendiente" ? "tab-active" : ""}`}
              onClick={() => setFiltro("pendiente")}
              data-testid="tab-pendientes"
            >
              ⏳ Pendientes ({totalPendientesCount})
            </button>
            <button
              type="button"
              className={`queue-filter-tab ${filtro === "sincronizada" ? "tab-active" : ""}`}
              onClick={() => setFiltro("sincronizada")}
              data-testid="tab-sincronizadas"
            >
              ✅ Sincronizadas ({totalSincronizadasCount})
            </button>
            <button
              type="button"
              className={`queue-filter-tab ${filtro === "requiere_revision" ? "tab-active" : ""}`}
              onClick={() => setFiltro("requiere_revision")}
              data-testid="tab-revision"
            >
              ⚠️ Requiere Revisión ({totalRevisionCount})
            </button>
          </div>

          {/* Lista de Operaciones en Cola (Ventas, Servicios y Gastos) */}
          <div className="queue-list" data-testid="queue-list">
            {filtradas.length === 0 && serviciosFiltrados.length === 0 && gastosFiltrados.length === 0 ? (
              <div className="queue-empty-message">
                <p>No hay ventas en esta sección de la cola local.</p>
              </div>
            ) : (
              <>
                {filtradas.map((venta) => {
                  const badgeClass =
                    venta.estado === "sincronizada"
                      ? "badge-status-synced"
                      : venta.estado === "pendiente"
                      ? "badge-status-pending"
                      : venta.estado === "sincronizando"
                      ? "badge-status-syncing"
                      : "badge-status-review";

                  return (
                    <div
                      key={venta.ticket_id}
                      className="queue-item-card"
                      data-testid={`queue-item-${venta.ticket_id}`}
                    >
                      <div className="queue-item-header">
                        <div className="queue-ticket-id">
                          <strong>Ticket:</strong> {venta.ticket_id}
                        </div>
                        <span className={`badge-status ${badgeClass}`} data-testid={`status-${venta.ticket_id}`}>
                          {venta.estado.toUpperCase()}
                        </span>
                      </div>

                      <div className="queue-item-details">
                        <div>
                          <span>Fecha:</span> {venta.payload.ticket.fecha}
                        </div>
                        <div>
                          <span>Total:</span> ${venta.payload.ticket.total_venta.toLocaleString("es-AR")}{" "}
                          ({venta.payload.ticket.metodo_pago})
                        </div>
                        <div>
                          <span>Ítems ({venta.payload.items.length}):</span>{" "}
                          {venta.payload.items.map((i) => `${i.cantidad}x ${i.producto}`).join(", ")}
                        </div>
                        {venta.intentos > 0 && (
                          <div>
                            <span>Reintentos:</span> {venta.intentos}
                          </div>
                        )}
                        {venta.sincronizado_en && (
                          <div className="queue-synced-at">
                            <span>
                              {isDemo ? "Simulación local completada:" : "Confirmado en server:"}
                            </span>{" "}
                            {new Date(venta.sincronizado_en).toLocaleTimeString()}
                            {isDemo && " (Local / Sin Turso)"}
                          </div>
                        )}
                        {venta.ultimo_error && (
                          <div className="queue-item-error" data-testid={`error-${venta.ticket_id}`}>
                            <span>Detalle del Fallo:</span> {venta.ultimo_error}
                          </div>
                        )}
                      </div>

                      <div className="queue-item-actions">
                        {venta.estado !== "sincronizada" && (
                          <button
                            type="button"
                            className="btn-retry-single"
                            onClick={() => handleSyncOne(venta.ticket_id)}
                            disabled={isSyncing || isReadOnlySmoke}
                            title={isReadOnlySmoke ? "Sincronización deshabilitada en modo solo lectura" : "Reintentar"}
                            data-testid={`btn-retry-${venta.ticket_id}`}
                          >
                            🔄 Reintentar
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })}

                {serviciosFiltrados.map((srv) => {
                  const badgeClass =
                    srv.estado === "sincronizada"
                      ? "badge-status-synced"
                      : srv.estado === "pendiente"
                      ? "badge-status-pending"
                      : srv.estado === "sincronizando"
                      ? "badge-status-syncing"
                      : "badge-status-review";

                  return (
                    <div
                      key={srv.idempotency_key}
                      className="queue-item-card"
                      data-testid={`queue-service-${srv.idempotency_key}`}
                    >
                      <div className="queue-item-header">
                        <div className="queue-ticket-id">
                          <strong>Servicio:</strong> {srv.payload.servicio} ({srv.idempotency_key})
                        </div>
                        <span
                          className={`badge-status ${badgeClass}`}
                          data-testid={`status-${srv.idempotency_key}`}
                        >
                          {srv.estado.toUpperCase()}
                        </span>
                      </div>
                      <div className="queue-item-details">
                        <div>
                          <span>Total Cobrado:</span> ${srv.payload.total_cobrado.toLocaleString("es-AR")}
                        </div>
                        {srv.ultimo_error && (
                          <div className="queue-item-error" data-testid={`error-${srv.idempotency_key}`}>
                            <span>Detalle del Fallo:</span> {srv.ultimo_error}
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}

                {gastosFiltrados.map((gst) => {
                  const badgeClass =
                    gst.estado === "sincronizada"
                      ? "badge-status-synced"
                      : gst.estado === "pendiente"
                      ? "badge-status-pending"
                      : gst.estado === "sincronizando"
                      ? "badge-status-syncing"
                      : "badge-status-review";

                  return (
                    <div
                      key={gst.idempotency_key}
                      className="queue-item-card"
                      data-testid={`queue-expense-${gst.idempotency_key}`}
                    >
                      <div className="queue-item-header">
                        <div className="queue-ticket-id">
                          <strong>Gasto:</strong> {gst.payload.detalle} ({gst.idempotency_key})
                        </div>
                        <span
                          className={`badge-status ${badgeClass}`}
                          data-testid={`status-${gst.idempotency_key}`}
                        >
                          {gst.estado.toUpperCase()}
                        </span>
                      </div>
                      <div className="queue-item-details">
                        <div>
                          <span>Monto:</span> ${gst.payload.monto_salida.toLocaleString("es-AR")} ({gst.payload.metodo_pago})
                        </div>
                        {gst.ultimo_error && (
                          <div className="queue-item-error" data-testid={`error-${gst.idempotency_key}`}>
                            <span>Detalle del Fallo:</span> {gst.ultimo_error}
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </>
            )}
          </div>
        </div>

        <div className="modal-footer">
          {isReadOnlySmoke ? (
            <div
              className="readonly-sync-blocked-notice"
              data-testid="readonly-sync-blocked-notice"
            >
              <span>🔒</span>
              <span>Modo Solo Lectura activo: Sincronización deshabilitada para proteger registros remotos.</span>
            </div>
          ) : (
            <button
              type="button"
              className="btn-sync-all"
              onClick={handleSyncAll}
              disabled={isSyncing || totalPendientesCount === 0}
              data-testid="btn-sync-all"
            >
              {isSyncing ? "Sincronizando..." : `Sincronizar Pendientes (${totalPendientesCount})`}
            </button>
          )}
        </div>
      </div>
    </div>
  );
};
