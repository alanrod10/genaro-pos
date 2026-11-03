import React, { useState, useEffect, useMemo, useCallback, useRef } from "react";
import { ItemVentaInput, TicketDetalle, TicketResumen } from "../types/sales";
import { getApiClient, obtenerSesionGuardada } from "../services/api";

interface SalesHistoryAdminProps {
  onAddToast: (mensaje: string, tipo?: "info" | "success" | "warning" | "error") => void;
  isDemo: boolean;
}

export const SalesHistoryAdmin: React.FC<SalesHistoryAdminProps> = ({
  onAddToast,
  isDemo,
}) => {
  // Lista de ventas
  const [ventas, setVentas] = useState<TicketResumen[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(false);

  // Filtros
  const [busqueda, setBusqueda] = useState<string>("");
  const [filtroFecha, setFiltroFecha] = useState<string>("");
  const [filtroMetodo, setFiltroMetodo] = useState<string>("TODOS");
  const [mostrarAnuladas, setMostrarAnuladas] = useState<boolean>(true);

  // Modal Detalle
  const [selectedTicketId, setSelectedTicketId] = useState<string | null>(null);
  const [detalle, setDetalle] = useState<TicketDetalle | null>(null);
  const [isLoadingDetalle, setIsLoadingDetalle] = useState<boolean>(false);

  // Modal / Acción Anulación
  const [ticketParaAnular, setTicketParaAnular] = useState<string | null>(null);
  const [motivoAnulacion, setMotivoAnulacion] = useState<string>("");
  const [isAnulando, setIsAnulando] = useState<boolean>(false);

  // Edición de ítem individual
  const [editingItemIndex, setEditingItemIndex] = useState<number | null>(null);
  const [editCantidad, setEditCantidad] = useState<number>(1);
  const [editSubtotal, setEditSubtotal] = useState<number>(0);
  const [isSavingItemEdit, setIsSavingItemEdit] = useState<boolean>(false);

  const isMountedRef = useRef(true);
  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  // Carga de historial desde la API (Real o Demo)
  const cargarHistorial = useCallback(async () => {
    setIsLoading(true);
    const api = getApiClient();
    try {
      const res = await api.obtenerHistorialVentas(
        filtroFecha || undefined,
        filtroMetodo !== "TODOS" ? filtroMetodo : undefined,
        busqueda.trim() || undefined,
        mostrarAnuladas
      );
      if (!isMountedRef.current) return;
      if (res.status === 200 && res.data) {
        setVentas(res.data.ventas);
      } else {
        onAddToast(res.error || "Error al cargar historial de ventas.", "error");
      }
    } catch (err: unknown) {
      if (!isMountedRef.current) return;
      const msg = err instanceof Error ? err.message : "Error de red al consultar ventas.";
      onAddToast(msg, "error");
    } finally {
      if (isMountedRef.current) {
        setIsLoading(false);
      }
    }
  }, [filtroFecha, filtroMetodo, busqueda, mostrarAnuladas, onAddToast]);

  useEffect(() => {
    cargarHistorial();
  }, [cargarHistorial]);

  // Carga de detalle de ticket
  const abrirDetalleTicket = async (ticket_id: string) => {
    setSelectedTicketId(ticket_id);
    setIsLoadingDetalle(true);
    setEditingItemIndex(null);
    const api = getApiClient();
    try {
      const res = await api.obtenerDetalleVenta(ticket_id);
      if (!isMountedRef.current) return;
      if (res.status === 200 && res.data) {
        setDetalle(res.data);
      } else {
        onAddToast(res.error || `No se pudo obtener el ticket ${ticket_id}`, "error");
        setSelectedTicketId(null);
      }
    } catch (err: unknown) {
      if (!isMountedRef.current) return;
      const msg = err instanceof Error ? err.message : "Error al consultar ticket.";
      onAddToast(msg, "error");
      setSelectedTicketId(null);
    } finally {
      if (isMountedRef.current) {
        setIsLoadingDetalle(false);
      }
    }
  };

  const cerrarDetalleTicket = () => {
    setSelectedTicketId(null);
    setDetalle(null);
    setEditingItemIndex(null);
  };

  // Anulación de venta
  const handleConfirmAnulacion = async () => {
    if (!ticketParaAnular) return;
    const motivo = motivoAnulacion.trim();
    if (!motivo) {
      onAddToast("Debes ingresar el motivo de anulación.", "warning");
      return;
    }

    setIsAnulando(true);
    const api = getApiClient();
    const actorActual = obtenerSesionGuardada()?.user?.display_name || "Alan";
    try {
      const res = await api.anularVenta(ticketParaAnular, motivo, actorActual);
      if (res.status === 200 && res.data) {
        onAddToast(`Venta #${ticketParaAnular} anulada correctamente.`, "success");
        setTicketParaAnular(null);
        setMotivoAnulacion("");
        // Si el detalle estaba abierto para este ticket, refrescarlo
        if (selectedTicketId === ticketParaAnular) {
          setDetalle(res.data);
        }
        await cargarHistorial();
      } else {
        onAddToast(res.error || "Error al anular venta.", "error");
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al procesar la anulación.";
      onAddToast(msg, "error");
    } finally {
      setIsAnulando(false);
    }
  };

  // Edición de ítem puntual dentro del detalle
  const iniciarEdicionItem = (idx: number, item: ItemVentaInput) => {
    setEditingItemIndex(idx);
    setEditCantidad(item.cantidad);
    setEditSubtotal(item.subtotal);
  };

  const handleGuardarEdicionItem = async (idx: number) => {
    if (!detalle) return;
    if (editCantidad <= 0) {
      onAddToast("La cantidad debe ser mayor a 0.", "warning");
      return;
    }
    if (editSubtotal < 0) {
      onAddToast("El subtotal no puede ser negativo.", "warning");
      return;
    }

    setIsSavingItemEdit(true);
    const api = getApiClient();
    const actorActual = obtenerSesionGuardada()?.user?.display_name || "Alan";
    try {
      // Si el ítem tiene ID numérico se usa, de lo contrario se usa el índice
      const targetId = (detalle.items[idx] as { id?: number }).id ?? idx;
      const res = await api.editarItemVenta(
        detalle.ticket_id,
        targetId,
        editCantidad,
        editSubtotal,
        actorActual
      );
      if (res.status === 200 && res.data) {
        onAddToast("Ítem corregido y caja recalculada exitosamente.", "success");
        setDetalle(res.data);
        setEditingItemIndex(null);
        await cargarHistorial();
      } else {
        onAddToast(res.error || "Error al editar ítem.", "error");
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al actualizar ítem.";
      onAddToast(msg, "error");
    } finally {
      setIsSavingItemEdit(false);
    }
  };

  // Métricas de ventas filtradas
  const metricas = useMemo(() => {
    const activas = ventas.filter((v) => !v.anulado);
    const anuladas = ventas.filter((v) => v.anulado);

    const totalFacturado = activas.reduce((acc, v) => acc + v.total_venta, 0);
    const totalEfvo = activas.reduce((acc, v) => acc + v.monto_efectivo, 0);
    const totalTransf = activas.reduce((acc, v) => acc + v.monto_transferencia, 0);

    return {
      totalFacturado,
      totalEfvo,
      totalTransf,
      cantActivas: activas.length,
      cantAnuladas: anuladas.length,
    };
  }, [ventas]);

  return (
    <div className="sales-history-container" data-testid="sales-history-container">
      {/* Banner de Modo Demo */}
      {isDemo && (
        <div className="admin-demo-banner" data-testid="sales-demo-banner">
          🧪 <strong>MODO DEMO:</strong> Historial y tickets almacenados localmente en tu
          navegador. Consulta de ítems, cálculo histórico y anulación lógica aislados de
          bases operativas.
        </div>
      )}

      {/* Barra de Filtros y Búsqueda */}
      <section className="services-form-card" data-testid="sales-filters-card">
        <div className="card-header">
          <h3>📜 Historial y Auditoría de Ventas</h3>
          <span className="card-badge-info">Consultas y Correcciones</span>
        </div>

        <div className="history-toolbar">
          <div className="toolbar-left">
            <input
              type="text"
              className="admin-search-input"
              placeholder="🔍 Buscar por N° Ticket o Producto..."
              value={busqueda}
              onChange={(e) => setBusqueda(e.target.value)}
              data-testid="input-search-sales"
            />

            <input
              type="date"
              className="services-date-input"
              value={filtroFecha}
              onChange={(e) => setFiltroFecha(e.target.value)}
              data-testid="input-filter-date"
            />

            <button
              type="button"
              className="btn-quick-today"
              onClick={() => {
                const now = new Date();
                const pad = (n: number) => String(n).padStart(2, "0");
                setFiltroFecha(
                  `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
                );
              }}
              data-testid="btn-sales-today"
            >
              Hoy
            </button>

            {filtroFecha && (
              <button
                type="button"
                className="btn-clear-date"
                onClick={() => setFiltroFecha("")}
                data-testid="btn-clear-sales-date"
              >
                Todas
              </button>
            )}

            <select
              className="admin-filter-select"
              value={filtroMetodo}
              onChange={(e) => setFiltroMetodo(e.target.value)}
              data-testid="select-filter-payment"
            >
              <option value="TODOS">Todos los Medios de Pago</option>
              <option value="EFECTIVO">💵 Solo Efectivo</option>
              <option value="TRANSFERENCIA">📱 Solo Transferencia</option>
              <option value="MIXTO">💳 Pago Mixto</option>
            </select>

            <label className="checkbox-show-cancelled">
              <input
                type="checkbox"
                checked={mostrarAnuladas}
                onChange={(e) => setMostrarAnuladas(e.target.checked)}
                data-testid="checkbox-show-cancelled"
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
              data-testid="btn-refresh-sales"
            >
              🔄 Refrescar
            </button>
          </div>
        </div>
      </section>

      {/* Resumen de Métricas de Ventas */}
      <section className="services-kpi-card" data-testid="sales-kpi-summary">
        <div className="kpi-grid">
          <div className="kpi-tile highlight-kpi">
            <span className="kpi-label">Total Ventas Filtradas</span>
            <span className="kpi-val text-green" data-testid="kpi-total-facturado">
              ${metricas.totalFacturado.toLocaleString("es-AR")}
            </span>
            <span className="kpi-sub">{metricas.cantActivas} tickets activos</span>
          </div>

          <div className="kpi-tile">
            <span className="kpi-label">En Efectivo</span>
            <span className="kpi-val" data-testid="kpi-total-efvo">
              ${metricas.totalEfvo.toLocaleString("es-AR")}
            </span>
            <span className="kpi-sub">Cobrado en cajón</span>
          </div>

          <div className="kpi-tile">
            <span className="kpi-label">En Transferencia</span>
            <span className="kpi-val" data-testid="kpi-total-transf">
              ${metricas.totalTransf.toLocaleString("es-AR")}
            </span>
            <span className="kpi-sub">Banco / Mercado Pago</span>
          </div>

          <div className="kpi-tile">
            <span className="kpi-label">Tickets Anulados</span>
            <span
              className={`kpi-val ${metricas.cantAnuladas > 0 ? "text-red" : ""}`}
              data-testid="kpi-cant-anuladas"
            >
              {metricas.cantAnuladas}
            </span>
            <span className="kpi-sub">Excluidos de saldo</span>
          </div>
        </div>
      </section>

      {/* Tabla de Escritorio */}
      <div className="admin-table-wrapper">
        <table className="admin-table" data-testid="sales-table">
          <thead>
            <tr>
              <th># Ticket</th>
              <th>Fecha/Hora</th>
              <th>Ítems Incluidos</th>
              <th className="th-number">Total Venta</th>
              <th className="th-number">Efectivo</th>
              <th className="th-number">Transf.</th>
              <th>Medio Pago</th>
              <th>Estado</th>
              <th className="th-actions">Acciones</th>
            </tr>
          </thead>
          <tbody>
            {ventas.length === 0 ? (
              <tr>
                <td colSpan={9} className="td-empty">
                  {isLoading ? "Cargando ventas..." : "No hay ventas registradas con los filtros aplicados."}
                </td>
              </tr>
            ) : (
              ventas.map((v) => (
                <tr key={v.ticket_id} className={v.anulado ? "row-cancelled" : ""}>
                  <td className="td-id">
                    <strong>{v.ticket_id}</strong>
                  </td>
                  <td className="td-date">{v.fecha}</td>
                  <td>
                    <span className="td-desc">
                      {v.resumen_items || `${v.cantidad_items} ítems`}
                    </span>
                  </td>
                  <td className="td-number font-bold">
                    ${v.total_venta.toLocaleString("es-AR")}
                  </td>
                  <td className="td-number text-muted">
                    ${v.monto_efectivo.toLocaleString("es-AR")}
                  </td>
                  <td className="td-number text-muted">
                    ${v.monto_transferencia.toLocaleString("es-AR")}
                  </td>
                  <td>
                    <span className="badge-cat">{v.metodo_pago}</span>
                  </td>
                  <td>
                    <span
                      className={`badge-status ${
                        v.anulado ? "status-inactive" : "status-active"
                      }`}
                    >
                      {v.anulado ? "Anulado" : "Activo"}
                    </span>
                  </td>
                  <td className="td-actions">
                    <button
                      type="button"
                      className="btn-action-edit"
                      onClick={() => abrirDetalleTicket(v.ticket_id)}
                      data-testid={`btn-view-ticket-${v.ticket_id}`}
                    >
                      👁️ Detalle
                    </button>
                    {!v.anulado && (
                      <button
                        type="button"
                        className="btn-action-cancel"
                        onClick={() => {
                          setTicketParaAnular(v.ticket_id);
                          setMotivoAnulacion("");
                        }}
                        data-testid={`btn-cancel-ticket-${v.ticket_id}`}
                      >
                        🚫 Anular
                      </button>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Tarjetas Móviles */}
      <div className="services-cards-grid" data-testid="sales-cards-grid">
        {ventas.map((v) => (
          <div
            key={v.ticket_id}
            className={`service-card ${v.anulado ? "card-cancelled" : ""}`}
            data-testid={`sale-card-${v.ticket_id}`}
          >
            <div className="card-top-row">
              <div className="card-title-group">
                <span className="card-id">#{v.ticket_id}</span>
                <span className="badge-cat">{v.metodo_pago}</span>
              </div>
              <span
                className={`badge-status ${
                  v.anulado ? "status-inactive" : "status-active"
                }`}
              >
                {v.anulado ? "Anulado" : "Activo"}
              </span>
            </div>

            <div className="card-date-row">
              <span>📅 {v.fecha}</span>
              <span>{v.cantidad_items} ítem(s)</span>
            </div>

            <div className="card-desc-row">
              <span className="td-desc">
                {v.resumen_items || "Ver detalle para consultar productos."}
              </span>
            </div>

            <div className="card-financial-row">
              <div className="financial-box">
                <span className="box-label">Efectivo / Transf</span>
                <span className="box-val text-muted">
                  ${v.monto_efectivo.toLocaleString("es-AR")} / $
                  {v.monto_transferencia.toLocaleString("es-AR")}
                </span>
              </div>
              <div className="financial-box highlight-price">
                <span className="box-label">Total Venta</span>
                <span className="box-val text-green">
                  ${v.total_venta.toLocaleString("es-AR")}
                </span>
              </div>
            </div>

            <div className="card-actions-row">
              <button
                type="button"
                className="btn-card-edit"
                onClick={() => abrirDetalleTicket(v.ticket_id)}
                data-testid={`btn-card-view-ticket-${v.ticket_id}`}
              >
                👁️ Ver Detalle
              </button>
              {!v.anulado && (
                <button
                  type="button"
                  className="btn-card-cancel"
                  onClick={() => {
                    setTicketParaAnular(v.ticket_id);
                    setMotivoAnulacion("");
                  }}
                  data-testid={`btn-card-cancel-ticket-${v.ticket_id}`}
                >
                  🚫 Anular
                </button>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* Modal Detalle Exhaustivo del Ticket */}
      {selectedTicketId && (
        <div className="modal-backdrop" data-testid="modal-ticket-detail">
          <div className="modal-card modal-large">
            <div className="modal-header">
              <div className="modal-title-row">
                <h3>📜 Detalle de Venta #{selectedTicketId}</h3>
                {detalle && (
                  <span
                    className={`badge-status ${
                      detalle.anulado ? "status-inactive" : "status-active"
                    }`}
                  >
                    {detalle.anulado ? "ANULADO" : "ACTIVO"}
                  </span>
                )}
              </div>
              <button
                type="button"
                className="btn-close-modal"
                onClick={cerrarDetalleTicket}
                data-testid="btn-close-ticket-detail"
              >
                ✕
              </button>
            </div>

            <div className="modal-body">
              {isLoadingDetalle || !detalle ? (
                <div className="loading-spinner">Cargando ítems del ticket...</div>
              ) : (
                <div className="ticket-detail-content">
                  {/* Banner de Anulación si corresponde */}
                  {detalle.anulado && (
                    <div className="alert-box-warning" data-testid="ticket-cancelled-banner">
                      ⚠️ <strong>TICKET ANULADO:</strong> {detalle.motivo_anulacion || "Sin motivo registrado"}
                    </div>
                  )}

                  {/* Resumen Cabecera */}
                  <div className="detail-header-grid">
                    <div className="detail-box">
                      <span className="detail-label">Fecha y Hora:</span>
                      <strong data-testid="detail-ticket-date">{detalle.fecha}</strong>
                    </div>
                    <div className="detail-box">
                      <span className="detail-label">Medio de Pago:</span>
                      <strong data-testid="detail-ticket-payment">{detalle.metodo_pago}</strong>
                    </div>
                    <div className="detail-box">
                      <span className="detail-label">Monto Efectivo:</span>
                      <span data-testid="detail-ticket-efvo">
                        ${detalle.monto_efectivo.toLocaleString("es-AR")}
                      </span>
                    </div>
                    <div className="detail-box">
                      <span className="detail-label">Monto Transferencia:</span>
                      <span data-testid="detail-ticket-transf">
                        ${detalle.monto_transferencia.toLocaleString("es-AR")}
                      </span>
                    </div>
                    <div className="detail-box highlight-box">
                      <span className="detail-label">TOTAL TICKET:</span>
                      <strong className="text-green text-large" data-testid="detail-ticket-total">
                        ${detalle.total_venta.toLocaleString("es-AR")}
                      </strong>
                    </div>
                  </div>

                  {/* Tabla de Ítems */}
                  <div className="items-section">
                    <h4>📦 Artículos del Ticket</h4>
                    <div className="admin-table-wrapper">
                      <table className="admin-table" data-testid="ticket-items-table">
                        <thead>
                          <tr>
                            <th>Producto</th>
                            <th className="th-number">Cantidad</th>
                            <th>Unidad</th>
                            <th className="th-number">Precio Unit.</th>
                            <th className="th-number">Subtotal</th>
                            {!detalle.anulado && <th className="th-actions">Acciones</th>}
                          </tr>
                        </thead>
                        <tbody>
                          {detalle.items.map((item, idx) => {
                            const nombreProducto =
                              item.producto || item.producto_nombre || "Producto sin nombre";
                            const precioUnitario = Number(
                              item.precio_unit ?? item.precio_unitario ?? 0
                            );
                            const subtotalItem = Number(item.subtotal ?? 0);
                            return (
                              <tr key={idx}>
                                <td>
                                  <strong>{nombreProducto}</strong>
                                </td>

                                <td className="td-number">
                                  {editingItemIndex === idx ? (
                                    <input
                                      type="number"
                                      min="0.1"
                                      step="0.5"
                                      className="form-input form-input-sm"
                                      value={editCantidad}
                                      onChange={(e) => {
                                        const c = Number(e.target.value);
                                        setEditCantidad(c);
                                        setEditSubtotal(Math.round(c * precioUnitario));
                                      }}
                                      data-testid={`input-edit-item-qty-${idx}`}
                                    />
                                  ) : (
                                    item.cantidad
                                  )}
                                </td>

                                <td>{item.unidad || "Unidad"}</td>

                                <td className="td-number">
                                  ${precioUnitario.toLocaleString("es-AR")}
                                </td>

                                <td className="td-number font-bold">
                                  {editingItemIndex === idx ? (
                                    <input
                                      type="number"
                                      min="0"
                                      step="100"
                                      className="form-input form-input-sm"
                                      value={editSubtotal}
                                      onChange={(e) => setEditSubtotal(Number(e.target.value))}
                                      data-testid={`input-edit-item-subtotal-${idx}`}
                                    />
                                  ) : (
                                    `$${subtotalItem.toLocaleString("es-AR")}`
                                  )}
                                </td>

                                {!detalle.anulado && (
                                  <td className="td-actions">
                                    {editingItemIndex === idx ? (
                                      <div className="inline-edit-actions">
                                        <button
                                          type="button"
                                          className="btn-confirm-save btn-sm"
                                          disabled={isSavingItemEdit}
                                          onClick={() => handleGuardarEdicionItem(idx)}
                                          data-testid="btn-save-item-edit"
                                        >
                                          💾 Guardar
                                        </button>
                                        <button
                                          type="button"
                                          className="btn-cancel btn-sm"
                                          onClick={() => setEditingItemIndex(null)}
                                        >
                                          ✕
                                        </button>
                                      </div>
                                    ) : (
                                      <button
                                        type="button"
                                        className="btn-action-edit"
                                        onClick={() => iniciarEdicionItem(idx, item)}
                                        data-testid={`btn-edit-item-${idx}`}
                                      >
                                        ✏️ Corregir
                                      </button>
                                    )}
                                  </td>
                                )}
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>

                  {/* Auditoría / Trazabilidad */}
                  {detalle.auditoria && detalle.auditoria.length > 0 && (
                    <div className="audit-section" data-testid="ticket-audit-trail">
                      <h4>📜 Trazabilidad de Cambios</h4>
                      <div className="audit-events-list">
                        {detalle.auditoria.map((ev, i) => (
                          <div key={i} className="audit-event-card">
                            <div className="audit-event-header">
                              <span className="badge-cat">{ev.accion}</span>
                              <span className="audit-actor">👤 {ev.usuario}</span>
                              <span className="audit-date">🕒 {ev.creado_en_utc}</span>
                            </div>
                            {ev.motivo && <p className="audit-reason">Motivo: {ev.motivo}</p>}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>

            <div className="modal-footer">
              {detalle && !detalle.anulado && (
                <button
                  type="button"
                  className="btn-danger-confirm"
                  onClick={() => {
                    setTicketParaAnular(detalle.ticket_id);
                    setMotivoAnulacion("");
                  }}
                  data-testid="btn-open-cancel-ticket"
                >
                  🚫 Anular Ticket
                </button>
              )}
              <button
                type="button"
                className="btn-cancel"
                onClick={cerrarDetalleTicket}
              >
                Cerrar
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Modal de Confirmación para Anular Venta */}
      {ticketParaAnular && (
        <div className="modal-backdrop" data-testid="modal-confirm-cancel-sale">
          <div className="modal-card">
            <div className="modal-header">
              <h3>🚫 Anular Venta #{ticketParaAnular}</h3>
              <button
                type="button"
                className="btn-close-modal"
                onClick={() => setTicketParaAnular(null)}
              >
                ✕
              </button>
            </div>

            <div className="modal-body">
              <div className="alert-box-warning">
                ⚠️ <strong>Acción Importante:</strong> La venta se marcará como <em>ANULADA</em> y
                sus importes serán descontados automáticamente del arqueo de caja. El registro histórico
                se conservará íntegramente con fines de auditoría.
              </div>

              <div className="form-group mt-3">
                <label htmlFor="input-cancel-reason">Motivo de Anulación (Requerido):</label>
                <input
                  id="input-cancel-reason"
                  type="text"
                  className="form-input"
                  placeholder="Ej. Error de cobro, devolución de mercadería, duplicado..."
                  value={motivoAnulacion}
                  onChange={(e) => setMotivoAnulacion(e.target.value)}
                  data-testid="input-cancel-sale-reason"
                />
              </div>
            </div>

            <div className="modal-footer">
              <button
                type="button"
                className="btn-cancel"
                onClick={() => setTicketParaAnular(null)}
                disabled={isAnulando}
              >
                Cancelar
              </button>
              <button
                type="button"
                className="btn-danger-confirm"
                disabled={isAnulando || !motivoAnulacion.trim()}
                onClick={handleConfirmAnulacion}
                data-testid="btn-confirm-cancel-sale"
              >
                {isAnulando ? "Anulando..." : "Confirmar Anulación"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
