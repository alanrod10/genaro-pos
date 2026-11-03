import React, { useCallback, useEffect, useMemo, useState } from "react";
import { ProductoInput } from "../types/catalog";
import {
  PedidoItem,
  PedidoProveedor,
  PedidoProveedorDetalle,
  ProductoEdicionPreventista,
} from "../types/preventistas";
import { getApiClient } from "../services/api";
import { fechaHoyJujuy } from "./ServicesAdmin";

interface PreventistasAdminProps {
  onAddToast: (mensaje: string, tipo?: "info" | "success" | "warning" | "error") => void;
  isDemo: boolean;
  onRefreshCatalog?: () => Promise<void>;
}

export const PreventistasAdmin: React.FC<PreventistasAdminProps> = ({
  onAddToast,
  isDemo,
  onRefreshCatalog,
}) => {
  // Pestaña activa: "precios" (equivalente app.py) vs "pedidos"
  const [subTab, setSubTab] = useState<"precios" | "pedidos">("precios");

  // ============================================================================
  // ESTADO: PRECIOS POR PREVENTISTA
  // ============================================================================
  const [proveedores, setProveedores] = useState<string[]>([]);
  const [proveedorSeleccionado, setProveedorSeleccionado] = useState<string>("");
  const [productosProveedor, setProductosProveedor] = useState<ProductoEdicionPreventista[]>([]);
  const [isLoadingProds, setIsLoadingProds] = useState<boolean>(false);
  const [isSavingPrecios, setIsSavingPrecios] = useState<boolean>(false);

  // Alta rápida de producto para el proveedor
  const [isAltaRapidaOpen, setIsAltaRapidaOpen] = useState<boolean>(false);
  const [nuevoNombre, setNuevoNombre] = useState<string>("");
  const [nuevaCategoria, setNuevaCategoria] = useState<string>("Almacén");
  const [nuevaUnidad, setNuevaUnidad] = useState<string>("Unidad");
  const [nuevoCosto, setNuevoCosto] = useState<number>(0);
  const [nuevoPrecio, setNuevoPrecio] = useState<number>(0);
  const [isSavingAltaRapida, setIsSavingAltaRapida] = useState<boolean>(false);

  // ============================================================================
  // ESTADO: TOMA Y CONSULTA DE PEDIDOS
  // ============================================================================
  const [pedidos, setPedidos] = useState<PedidoProveedor[]>([]);
  const [filtroProvPedido, setFiltroProvPedido] = useState<string>("TODOS");
  const [filtroEstadoPedido, setFiltroEstadoPedido] = useState<string>("TODOS");
  const [filtroFechaPedido, setFiltroFechaPedido] = useState<string>("");

  // Modal Nuevo Pedido
  const [isNuevoPedidoOpen, setIsNuevoPedidoOpen] = useState<boolean>(false);
  const [pedidoProvElegido, setPedidoProvElegido] = useState<string>("");
  const [pedidoFecha, setPedidoFecha] = useState<string>(() => fechaHoyJujuy());
  const [pedidoObservaciones, setPedidoObservaciones] = useState<string>("");
  const [pedidoItems, setPedidoItems] = useState<PedidoItem[]>([]);
  const [isSavingPedido, setIsSavingPedido] = useState<boolean>(false);

  // Formulario de ítem para el pedido
  const [prodSelectId, setProdSelectId] = useState<number | "">("");
  const [prodSelectNombre, setProdSelectNombre] = useState<string>("");
  const [prodSelectCant, setProdSelectCant] = useState<number>(1);
  const [prodSelectCosto, setProdSelectCosto] = useState<number>(0);

  // Modal Detalle Pedido
  const [detallePedido, setDetallePedido] = useState<PedidoProveedorDetalle | null>(null);
  const [isModalDetalleOpen, setIsModalDetalleOpen] = useState<boolean>(false);
  const [isUpdatingEstado, setIsUpdatingEstado] = useState<boolean>(false);

  // Modal Cancelar Pedido
  const [isModalCancelarOpen, setIsModalCancelarOpen] = useState<boolean>(false);
  const [motivoCancelacion, setMotivoCancelacion] = useState<string>("");
  const [pedidoACancelarId, setPedidoACancelarId] = useState<string | null>(null);

  // ============================================================================
  // CARGA DE DATOS
  // ============================================================================

  const cargarProveedores = useCallback(async () => {
    const api = getApiClient();
    try {
      const res = await api.obtenerProveedores();
      if (res.status === 200 && res.data) {
        setProveedores(res.data);
        if (!proveedorSeleccionado && res.data.length > 0) {
          setProveedorSeleccionado(res.data[0]);
        }
      }
    } catch {
      onAddToast("Error al cargar la lista de proveedores.", "error");
    }
  }, [onAddToast, proveedorSeleccionado]);

  const cargarProductosDeProveedor = useCallback(
    async (prov: string) => {
      if (!prov) {
        setProductosProveedor([]);
        return;
      }
      setIsLoadingProds(true);
      const api = getApiClient();
      try {
        const res = await api.obtenerProductosPorProveedor(prov);
        if (res.status === 200 && res.data) {
          const mapeados: ProductoEdicionPreventista[] = res.data.map((p) => {
            const c = Number(p.costo) || 0;
            const pr = Number(p.precio_venta ?? p.precio_dia) || 0;
            const m = c > 0 ? parseFloat((((pr - c) / c) * 100).toFixed(1)) : 0.0;
            return {
              id_producto: p.id_producto,
              nombre: p.nombre,
              categoria: p.categoria || "Otro",
              proveedor: p.proveedor || prov,
              unidad: p.unidad || "Unidad",
              costo: c,
              precio_venta: pr,
              margen_porcentaje: m,
              costo_editado: c,
              precio_editado: pr,
              margen_editado: m,
              editado: false,
            };
          });
          setProductosProveedor(mapeados);
        }
      } catch {
        onAddToast("Error al cargar productos del preventista.", "error");
      } finally {
        setIsLoadingProds(false);
      }
    },
    [onAddToast]
  );

  const cargarPedidos = useCallback(async () => {
    const api = getApiClient();
    try {
      const filtros: { proveedor?: string; fecha?: string; estado?: string } = {};
      if (filtroProvPedido !== "TODOS") filtros.proveedor = filtroProvPedido;
      if (filtroFechaPedido) filtros.fecha = filtroFechaPedido;
      if (filtroEstadoPedido !== "TODOS") filtros.estado = filtroEstadoPedido;

      const res = await api.obtenerPedidos(filtros);
      if (res.status === 200 && res.data) {
        setPedidos(res.data);
      }
    } catch {
      onAddToast("Error al cargar pedidos.", "error");
    }
  }, [filtroProvPedido, filtroFechaPedido, filtroEstadoPedido, onAddToast]);

  useEffect(() => {
    cargarProveedores();
  }, [cargarProveedores]);

  useEffect(() => {
    if (proveedorSeleccionado) {
      cargarProductosDeProveedor(proveedorSeleccionado);
    }
  }, [proveedorSeleccionado, cargarProductosDeProveedor]);

  useEffect(() => {
    if (subTab === "pedidos") {
      cargarPedidos();
    }
  }, [subTab, cargarPedidos]);

  // ============================================================================
  // EDICIÓN EN VIVO DE PRECIOS POR PREVENTISTA (Equivalente exacto app.py)
  // ============================================================================

  const handleCellChange = (
    id_producto: number,
    campo: "costo" | "precio",
    valorStr: string
  ) => {
    const val = Math.max(0, parseFloat(valorStr) || 0);

    setProductosProveedor((prev) =>
      prev.map((p) => {
        if (p.id_producto !== id_producto) return p;

        const nuevoCosto = campo === "costo" ? val : (p.costo_editado ?? p.costo);
        const nuevoPrecio = campo === "precio" ? val : (p.precio_editado ?? p.precio_venta);

        const nuevoMargen =
          nuevoCosto > 0
            ? parseFloat((((nuevoPrecio - nuevoCosto) / nuevoCosto) * 100).toFixed(1))
            : 0.0;

        const modificado =
          nuevoCosto !== p.costo || nuevoPrecio !== p.precio_venta;

        return {
          ...p,
          costo_editado: nuevoCosto,
          precio_editado: nuevoPrecio,
          margen_editado: nuevoMargen,
          editado: modificado,
        };
      })
    );
  };

  const handleGuardarPrecios = async () => {
    const modificados = productosProveedor.filter((p) => p.editado);

    if (modificados.length === 0) {
      onAddToast("No detecté ninguna modificación en los números.", "warning");
      return;
    }

    setIsSavingPrecios(true);
    const api = getApiClient();
    try {
      const cambios = modificados.map((p) => ({
        id_producto: p.id_producto,
        costo: p.costo_editado ?? p.costo,
        precio_venta: p.precio_editado ?? p.precio_venta,
      }));

      const res = await api.actualizarPreciosPreventista(proveedorSeleccionado, cambios);
      if (res.status === 200) {
        onAddToast(
          `✅ ¡Los precios de este proveedor fueron actualizados! (${res.data?.actualizados || modificados.length} productos)`,
          "success"
        );
        if (onRefreshCatalog) {
          await onRefreshCatalog();
        }
        await cargarProductosDeProveedor(proveedorSeleccionado);
      } else {
        onAddToast(`Error: ${res.error || "No se pudieron actualizar los precios."}`, "error");
      }
    } catch {
      onAddToast("Error inesperado al guardar precios.", "error");
    } finally {
      setIsSavingPrecios(false);
    }
  };

  // ============================================================================
  // ALTA RÁPIDA DE PRODUCTO PARA EL PREVENTISTA ELEGIDO
  // ============================================================================

  const margenEstimadoAlta = useMemo(() => {
    if (nuevoCosto > 0) {
      return (((nuevoPrecio - nuevoCosto) / nuevoCosto) * 100).toFixed(2);
    }
    return "0.00";
  }, [nuevoCosto, nuevoPrecio]);

  const handleGuardarAltaRapida = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!nuevoNombre.trim()) {
      onAddToast("⚠️ El nombre es obligatorio.", "warning");
      return;
    }
    if (nuevoPrecio <= 0) {
      onAddToast("⚠️ El precio debe ser mayor a 0.", "warning");
      return;
    }

    setIsSavingAltaRapida(true);
    const api = getApiClient();
    try {
      const datos: ProductoInput = {
        nombre: nuevoNombre.trim(),
        categoria: nuevaCategoria,
        proveedor: proveedorSeleccionado,
        unidad: nuevaUnidad,
        costo: nuevoCosto,
        precio_venta: nuevoPrecio,
        activo: true,
      };

      const res = await api.altaRapidaProductoPreventista(datos);
      if (res.status === 201) {
        onAddToast(
          `✅ ¡${nuevoNombre} añadido al catálogo de ${proveedorSeleccionado}!`,
          "success"
        );
        setNuevoNombre("");
        setNuevoCosto(0);
        setNuevoPrecio(0);
        setIsAltaRapidaOpen(false);
        if (onRefreshCatalog) {
          await onRefreshCatalog();
        }
        await cargarProductosDeProveedor(proveedorSeleccionado);
      } else {
        onAddToast(`Error al crear producto: ${res.error}`, "error");
      }
    } catch {
      onAddToast("Error inesperado al registrar nuevo producto.", "error");
    } finally {
      setIsSavingAltaRapida(false);
    }
  };

  // ============================================================================
  // TOMA DE PEDIDOS: CREACIÓN Y MANEJO DE ÍTEMS
  // ============================================================================

  const handleAbrirNuevoPedido = () => {
    const provInicial = proveedorSeleccionado || proveedores[0] || "";
    setPedidoProvElegido(provInicial);
    if (provInicial) {
      cargarProductosDeProveedor(provInicial);
    }
    setPedidoFecha(fechaHoyJujuy());
    setPedidoObservaciones("");
    setPedidoItems([]);
    setProdSelectId("");
    setProdSelectNombre("");
    setProdSelectCant(1);
    setProdSelectCosto(0);
    setIsNuevoPedidoOpen(true);
  };

  const handleSelectProdForPedido = (prodIdStr: string) => {
    const id = parseInt(prodIdStr, 10);
    if (isNaN(id)) {
      setProdSelectId("");
      setProdSelectNombre("");
      setProdSelectCosto(0);
      return;
    }
    setProdSelectId(id);
    const p = productosProveedor.find((it) => it.id_producto === id);
    if (p) {
      setProdSelectNombre(p.nombre);
      setProdSelectCosto(p.costo);
    }
  };

  const handleAgregarItemPedido = () => {
    if (!prodSelectNombre.trim()) {
      onAddToast("Selecciona o ingresa un producto.", "warning");
      return;
    }
    if (prodSelectCant <= 0) {
      onAddToast("La cantidad debe ser mayor a 0.", "warning");
      return;
    }

    const subtotal = Math.round(prodSelectCant * prodSelectCosto);
    const nuevoItem: PedidoItem = {
      producto_id: prodSelectId ? Number(prodSelectId) : undefined,
      producto_nombre: prodSelectNombre.trim(),
      cantidad: prodSelectCant,
      costo_unitario: prodSelectCosto,
      subtotal,
    };

    setPedidoItems((prev) => [...prev, nuevoItem]);
    setProdSelectId("");
    setProdSelectNombre("");
    setProdSelectCant(1);
    setProdSelectCosto(0);
  };

  const handleRemoverItemPedido = (idx: number) => {
    setPedidoItems((prev) => prev.filter((_, i) => i !== idx));
  };

  const totalCalculadoNuevoPedido = useMemo(() => {
    return pedidoItems.reduce((acc, it) => acc + it.subtotal, 0);
  }, [pedidoItems]);

  const handleConfirmarPedido = async () => {
    const prov = (pedidoProvElegido || proveedorSeleccionado || proveedores[0] || "").trim();
    if (!prov) {
      onAddToast("El proveedor es obligatorio para registrar un pedido.", "warning");
      return;
    }
    if (pedidoItems.length === 0) {
      onAddToast("Debes agregar al menos un producto al pedido.", "warning");
      return;
    }

    setIsSavingPedido(true);
    const api = getApiClient();
    try {
      const res = await api.crearPedido({
        proveedor: prov,
        fecha: pedidoFecha,
        observaciones: pedidoObservaciones,
        items: pedidoItems,
      });

      if (res.status === 201) {
        onAddToast(`✅ Pedido ${res.data?.id} registrado exitosamente.`, "success");
        setIsNuevoPedidoOpen(false);
        await cargarPedidos();
      } else {
        onAddToast(`Error al registrar pedido: ${res.error}`, "error");
      }
    } catch {
      onAddToast("Error inesperado al guardar el pedido.", "error");
    } finally {
      setIsSavingPedido(false);
    }
  };

  // ============================================================================
  // CONSULTA Y CAMBIO DE ESTADO DE PEDIDOS
  // ============================================================================

  const handleVerDetallePedido = async (id: string) => {
    const api = getApiClient();
    try {
      const res = await api.obtenerDetallePedido(id);
      if (res.status === 200 && res.data) {
        setDetallePedido(res.data);
        setIsModalDetalleOpen(true);
      } else {
        onAddToast(`Error al consultar detalle: ${res.error}`, "error");
      }
    } catch {
      onAddToast("Error de conexión al cargar detalle del pedido.", "error");
    }
  };

  const handleMarcarRecibido = async (id: string) => {
    setIsUpdatingEstado(true);
    const api = getApiClient();
    try {
      const res = await api.cambiarEstadoPedido(id, "RECIBIDO", "Mercadería recibida conforme");
      if (res.status === 200) {
        onAddToast(`✅ Pedido ${id} marcado como RECIBIDO.`, "success");
        setIsModalDetalleOpen(false);
        await cargarPedidos();
      } else {
        onAddToast(`Error: ${res.error}`, "error");
      }
    } catch {
      onAddToast("Error al actualizar estado del pedido.", "error");
    } finally {
      setIsUpdatingEstado(false);
    }
  };

  const handleAbrirCancelarPedido = (id: string) => {
    setPedidoACancelarId(id);
    setMotivoCancelacion("");
    setIsModalCancelarOpen(true);
  };

  const handleConfirmarCancelar = async () => {
    if (!pedidoACancelarId) return;
    if (!motivoCancelacion.trim()) {
      onAddToast("El motivo de cancelación es obligatorio.", "warning");
      return;
    }

    setIsUpdatingEstado(true);
    const api = getApiClient();
    try {
      const res = await api.cancelarPedido(pedidoACancelarId, motivoCancelacion.trim());
      if (res.status === 200) {
        onAddToast(`🚫 Pedido ${pedidoACancelarId} cancelado.`, "info");
        setIsModalCancelarOpen(false);
        setIsModalDetalleOpen(false);
        await cargarPedidos();
      } else {
        onAddToast(`Error: ${res.error}`, "error");
      }
    } catch {
      onAddToast("Error al cancelar el pedido.", "error");
    } finally {
      setIsUpdatingEstado(false);
    }
  };

  return (
    <div className="preventistas-admin-container" data-testid="preventistas-admin-container">
      {/* Cabecera del Módulo */}
      <div className="module-header">
        <div>
          <h2>🚚 Preventistas y Pedidos</h2>
          <p className="module-subtitle">
            Actualiza costos y precios por proveedor directamente desde la tabla y gestiona pedidos de reposición.
          </p>
        </div>
        {isDemo && (
          <span className="demo-badge-subtle" data-testid="preventistas-demo-badge">
            🧪 Demo Local (IndexedDB)
          </span>
        )}
      </div>

      {/* Sub-Pestañas de Navegación */}
      <div className="subtabs-nav" data-testid="preventistas-subtabs">
        <button
          type="button"
          className={`btn-subtab ${subTab === "precios" ? "subtab-active" : ""}`}
          onClick={() => setSubTab("precios")}
          data-testid="subtab-precios"
        >
          🚚 Precios por Preventista
        </button>
        <button
          type="button"
          className={`btn-subtab ${subTab === "pedidos" ? "subtab-active" : ""}`}
          onClick={() => setSubTab("pedidos")}
          data-testid="subtab-pedidos"
        >
          📋 Toma de Pedidos
        </button>
      </div>

      {/* ====================================================================== */}
      {/* PESTAÑA 1: PRECIOS POR PREVENTISTA (Equivalente exacto app.py)          */}
      {/* ====================================================================== */}
      {subTab === "precios" && (
        <div className="precios-preventista-section" data-testid="precios-preventista-section">
          {/* Selector de Preventista / Proveedor */}
          <div className="selector-proveedor-card">
            <label htmlFor="select-proveedor" className="selector-label">
              👤 <strong>Seleccionar Preventista / Proveedor:</strong>
            </label>
            <div className="selector-row">
              <select
                id="select-proveedor"
                value={proveedorSeleccionado}
                onChange={(e) => setProveedorSeleccionado(e.target.value)}
                className="select-proveedor"
                data-testid="select-proveedor"
              >
                {proveedores.map((prov) => (
                  <option key={prov} value={prov}>
                    {prov}
                  </option>
                ))}
              </select>

              <button
                type="button"
                className="btn-alta-rapida-toggle"
                onClick={() => setIsAltaRapidaOpen(!isAltaRapidaOpen)}
                data-testid="btn-toggle-alta-rapida"
              >
                {isAltaRapidaOpen ? "✖ Cerrar Alta" : "➕ Alta Rápida de Producto"}
              </button>
            </div>
          </div>

          {/* Formulario colapsable: Alta rápida de producto */}
          {isAltaRapidaOpen && (
            <div className="alta-rapida-card" data-testid="alta-rapida-card">
              <h4>➕ Alta rápida de producto para {proveedorSeleccionado}</h4>
              <form onSubmit={handleGuardarAltaRapida} className="alta-rapida-form">
                <div className="form-grid-alta">
                  <div className="form-field">
                    <label>NOMBRE DEL PRODUCTO:</label>
                    <input
                      type="text"
                      value={nuevoNombre}
                      onChange={(e) => setNuevoNombre(e.target.value)}
                      placeholder="Ej: Galletitas de Vainilla"
                      required
                      data-testid="input-alta-nombre"
                    />
                  </div>

                  <div className="form-field">
                    <label>CATEGORÍA:</label>
                    <select
                      value={nuevaCategoria}
                      onChange={(e) => setNuevaCategoria(e.target.value)}
                      data-testid="select-alta-categoria"
                    >
                      <option value="Almacén">Almacén</option>
                      <option value="Bebidas">Bebidas</option>
                      <option value="Cervezas">Cervezas</option>
                      <option value="Golosinas">Golosinas</option>
                      <option value="Lácteos">Lácteos</option>
                      <option value="Limpieza">Limpieza</option>
                      <option value="Cigarrillos">Cigarrillos</option>
                      <option value="Otro">Otro...</option>
                    </select>
                  </div>

                  <div className="form-field">
                    <label>UNIDAD:</label>
                    <select
                      value={nuevaUnidad}
                      onChange={(e) => setNuevaUnidad(e.target.value)}
                      data-testid="select-alta-unidad"
                    >
                      <option value="Unidad">Unidad</option>
                      <option value="Kg">Kg</option>
                      <option value="Litro">Litro</option>
                      <option value="Paquete">Paquete</option>
                    </select>
                  </div>

                  <div className="form-field">
                    <label>COSTO ($):</label>
                    <input
                      type="number"
                      min="0"
                      step="100"
                      value={nuevoCosto}
                      onChange={(e) => setNuevoCosto(parseFloat(e.target.value) || 0)}
                      data-testid="input-alta-costo"
                    />
                  </div>

                  <div className="form-field">
                    <label>PRECIO VENTA ($):</label>
                    <input
                      type="number"
                      min="0"
                      step="100"
                      value={nuevoPrecio}
                      onChange={(e) => setNuevoPrecio(parseFloat(e.target.value) || 0)}
                      required
                      data-testid="input-alta-precio"
                    />
                  </div>

                  <div className="form-field margen-indicator-field">
                    <label>GANANCIA ESTIMADA:</label>
                    <div className="margen-indicator-value" data-testid="alta-margen-estimado">
                      <strong>{margenEstimadoAlta}%</strong>
                    </div>
                  </div>
                </div>

                <div className="alta-rapida-actions">
                  <button
                    type="submit"
                    className="btn-guardar-alta"
                    disabled={isSavingAltaRapida}
                    data-testid="btn-guardar-alta"
                  >
                    {isSavingAltaRapida ? "Guardando..." : "➕ GUARDAR NUEVO PRODUCTO"}
                  </button>
                </div>
              </form>
            </div>
          )}

          {/* Grilla de Productos y Edición en Vivo */}
          <div className="productos-proveedor-header">
            <h3>
              Productos de: <strong>{proveedorSeleccionado}</strong> ({productosProveedor.length} ítems)
            </h3>
            <div className="tip-info-box">
              💡 <strong>Tip:</strong> Edita el Costo o el Precio y presiona Enter (o toca afuera de la celda).
              Verás cómo el porcentaje de Ganancia se recalcula <strong>en vivo</strong> en la tabla.
            </div>
          </div>

          {isLoadingProds ? (
            <div className="loading-state">Cargando productos del proveedor...</div>
          ) : productosProveedor.length === 0 ? (
            <div className="empty-state">No hay productos registrados para este proveedor.</div>
          ) : (
            <>
              {/* Tabla de escritorio */}
              <div className="table-responsive-wrapper">
                <table className="tabla-precios-preventista" data-testid="tabla-precios-preventista">
                  <thead>
                    <tr>
                      <th className="col-w-40">PRODUCTO</th>
                      <th className="col-w-20">COSTO ($)</th>
                      <th className="col-w-20">PRECIO VENTA ($)</th>
                      <th className="col-w-20">GANANCIA (%)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {productosProveedor.map((prod) => (
                      <tr
                        key={prod.id_producto}
                        className={prod.editado ? "row-edited" : ""}
                        data-testid={`row-prod-${prod.id_producto}`}
                      >
                        <td className="cell-producto-nombre">
                          <strong>{prod.nombre}</strong>
                          <span className="categoria-pill">{prod.categoria}</span>
                          {prod.editado && <span className="edited-badge">Modificado</span>}
                        </td>
                        <td className="cell-input">
                          <input
                            type="number"
                            min="0"
                            step="100"
                            className="input-table-precio"
                            value={prod.costo_editado ?? prod.costo}
                            onChange={(e) =>
                              handleCellChange(prod.id_producto, "costo", e.target.value)
                            }
                            data-testid={`input-costo-${prod.id_producto}`}
                          />
                        </td>
                        <td className="cell-input">
                          <input
                            type="number"
                            min="0"
                            step="100"
                            className="input-table-precio"
                            value={prod.precio_editado ?? prod.precio_venta}
                            onChange={(e) =>
                              handleCellChange(prod.id_producto, "precio", e.target.value)
                            }
                            data-testid={`input-precio-${prod.id_producto}`}
                          />
                        </td>
                        <td className="cell-margen">
                          <span
                            className={`badge-margen ${
                              (prod.margen_editado ?? prod.margen_porcentaje) >= 40
                                ? "margen-alto"
                                : (prod.margen_editado ?? prod.margen_porcentaje) > 0
                                ? "margen-normal"
                                : "margen-bajo"
                            }`}
                            data-testid={`badge-margen-${prod.id_producto}`}
                          >
                            {(prod.margen_editado ?? prod.margen_porcentaje).toFixed(1)} %
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Botón Guardar Cambios */}
              <div className="guardar-precios-bar">
                <button
                  type="button"
                  className="btn-guardar-precios-lote"
                  onClick={handleGuardarPrecios}
                  disabled={isSavingPrecios}
                  data-testid="btn-guardar-precios"
                >
                  {isSavingPrecios ? "Guardando en catálogo..." : "💾 Guardar Nuevos Precios"}
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {/* ====================================================================== */}
      {/* PESTAÑA 2: TOMA Y CONSULTA DE PEDIDOS                                   */}
      {/* ====================================================================== */}
      {subTab === "pedidos" && (
        <div className="pedidos-section" data-testid="pedidos-section">
          {/* Barra de Filtros y Acción Nuevo Pedido */}
          <div className="pedidos-toolbar">
            <div className="pedidos-filtros">
              <div className="filtro-item">
                <label>Proveedor:</label>
                <select
                  value={filtroProvPedido}
                  onChange={(e) => setFiltroProvPedido(e.target.value)}
                  data-testid="filtro-pedido-prov"
                >
                  <option value="TODOS">Todos los proveedores</option>
                  {proveedores.map((pr) => (
                    <option key={pr} value={pr}>
                      {pr}
                    </option>
                  ))}
                </select>
              </div>

              <div className="filtro-item">
                <label>Estado:</label>
                <select
                  value={filtroEstadoPedido}
                  onChange={(e) => setFiltroEstadoPedido(e.target.value)}
                  data-testid="filtro-pedido-estado"
                >
                  <option value="TODOS">Todos los estados</option>
                  <option value="PENDIENTE">PENDIENTE</option>
                  <option value="RECIBIDO">RECIBIDO</option>
                  <option value="CANCELADO">CANCELADO</option>
                </select>
              </div>

              <div className="filtro-item">
                <label>Fecha:</label>
                <input
                  type="date"
                  value={filtroFechaPedido}
                  onChange={(e) => setFiltroFechaPedido(e.target.value)}
                  data-testid="filtro-pedido-fecha"
                />
              </div>
            </div>

            <button
              type="button"
              className="btn-nuevo-pedido"
              onClick={handleAbrirNuevoPedido}
              data-testid="btn-abrir-nuevo-pedido"
            >
              ➕ Nuevo Pedido
            </button>
          </div>

          {/* Listado de Pedidos */}
          {pedidos.length === 0 ? (
            <div className="empty-state">No hay pedidos registrados con los filtros seleccionados.</div>
          ) : (
            <>
              {/* Tabla para computadoras */}
              <div className="table-responsive-wrapper desktop-only">
                <table className="tabla-pedidos" data-testid="tabla-pedidos">
                  <thead>
                    <tr>
                      <th>ID PEDIDO</th>
                      <th>FECHA</th>
                      <th>PROVEEDOR</th>
                      <th>ÍTEMS</th>
                      <th>TOTAL ($)</th>
                      <th>ESTADO</th>
                      <th>ACCIONES</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pedidos.map((ped) => (
                      <tr key={ped.id} data-testid={`row-pedido-${ped.id}`}>
                        <td>
                          <strong>{ped.id}</strong>
                        </td>
                        <td>{ped.fecha}</td>
                        <td>{ped.proveedor}</td>
                        <td>{ped.items_count || 0} ítems</td>
                        <td className="cell-monto">
                          ${ped.total.toLocaleString("es-AR")}
                        </td>
                        <td>
                          <span
                            className={`badge-estado badge-estado-${ped.estado.toLowerCase()}`}
                            data-testid={`badge-estado-${ped.id}`}
                          >
                            {ped.estado}
                          </span>
                        </td>
                        <td>
                          <button
                            type="button"
                            className="btn-ver-detalle"
                            onClick={() => handleVerDetallePedido(ped.id)}
                            data-testid={`btn-ver-detalle-${ped.id}`}
                          >
                            👁️ Ver Detalle
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Tarjetas táctiles para celulares */}
              <div className="cards-pedidos mobile-only" data-testid="cards-pedidos">
                {pedidos.map((ped) => (
                  <div key={ped.id} className="card-pedido-item">
                    <div className="card-pedido-header">
                      <strong>{ped.id}</strong>
                      <span
                        className={`badge-estado badge-estado-${ped.estado.toLowerCase()}`}
                      >
                        {ped.estado}
                      </span>
                    </div>
                    <div className="card-pedido-body">
                      <div>
                        <span>Proveedor:</span> <strong>{ped.proveedor}</strong>
                      </div>
                      <div>
                        <span>Fecha:</span> {ped.fecha}
                      </div>
                      <div>
                        <span>Ítems:</span> {ped.items_count || 0}
                      </div>
                      <div className="card-pedido-total">
                        Total: <strong>${ped.total.toLocaleString("es-AR")}</strong>
                      </div>
                    </div>
                    <button
                      type="button"
                      className="btn-ver-detalle-mobile"
                      onClick={() => handleVerDetallePedido(ped.id)}
                    >
                      👁️ Ver Detalle
                    </button>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}

      {/* ====================================================================== */}
      {/* MODAL: NUEVO PEDIDO                                                    */}
      {/* ====================================================================== */}
      {isNuevoPedidoOpen && (
        <div className="modal-overlay" data-testid="modal-nuevo-pedido">
          <div className="modal-content modal-pedido-content">
            <div className="modal-header">
              <h3>📝 Registrar Nuevo Pedido a Proveedor</h3>
              <button
                type="button"
                className="btn-close-modal"
                onClick={() => setIsNuevoPedidoOpen(false)}
              >
                ✕
              </button>
            </div>

            <div className="modal-body">
              <div className="form-row-pedido">
                <div className="form-field">
                  <label>Proveedor:</label>
                  <select
                    value={pedidoProvElegido}
                    onChange={(e) => {
                      setPedidoProvElegido(e.target.value);
                      cargarProductosDeProveedor(e.target.value);
                    }}
                    data-testid="select-pedido-prov"
                  >
                    {proveedores.map((p) => (
                      <option key={p} value={p}>
                        {p}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="form-field">
                  <label>Fecha:</label>
                  <input
                    type="date"
                    value={pedidoFecha}
                    onChange={(e) => setPedidoFecha(e.target.value)}
                    data-testid="input-pedido-fecha"
                  />
                </div>
              </div>

              <div className="form-field">
                <label>Observaciones (opcional):</label>
                <input
                  type="text"
                  value={pedidoObservaciones}
                  onChange={(e) => setPedidoObservaciones(e.target.value)}
                  placeholder="Ej: Entrega urgente el lunes a primera hora"
                  data-testid="input-pedido-obs"
                />
              </div>

              <hr className="modal-divider" />
              <h4>Ítems del Pedido</h4>

              {/* Agregar ítem al pedido */}
              <div className="agregar-item-row">
                <div className="item-field item-field-prod">
                  <label>Producto:</label>
                  <select
                    value={prodSelectId}
                    onChange={(e) => handleSelectProdForPedido(e.target.value)}
                    data-testid="select-item-producto"
                  >
                    <option value="">Selecciona un producto...</option>
                    {productosProveedor.map((p) => (
                      <option key={p.id_producto} value={p.id_producto}>
                        {p.nombre} (${p.costo})
                      </option>
                    ))}
                  </select>
                </div>

                <div className="item-field item-field-cant">
                  <label>Cant:</label>
                  <input
                    type="number"
                    min="1"
                    step="1"
                    value={prodSelectCant}
                    onChange={(e) => setProdSelectCant(Math.max(1, parseInt(e.target.value, 10) || 1))}
                    data-testid="input-item-cantidad"
                  />
                </div>

                <div className="item-field item-field-costo">
                  <label>Costo ($):</label>
                  <input
                    type="number"
                    min="0"
                    step="100"
                    value={prodSelectCosto}
                    onChange={(e) => setProdSelectCosto(Math.max(0, parseFloat(e.target.value) || 0))}
                    data-testid="input-item-costo"
                  />
                </div>

                <button
                  type="button"
                  className="btn-agregar-item-pedido"
                  onClick={handleAgregarItemPedido}
                  data-testid="btn-agregar-item-pedido"
                >
                  ➕ Agregar
                </button>
              </div>

              {/* Lista de ítems cargados */}
              <div className="pedido-items-list-wrapper">
                {pedidoItems.length === 0 ? (
                  <div className="empty-items-notice">No se han agregado productos a este pedido.</div>
                ) : (
                  <table className="tabla-items-pedido" data-testid="tabla-items-pedido">
                    <thead>
                      <tr>
                        <th>Producto</th>
                        <th>Cant</th>
                        <th>Costo Unit</th>
                        <th>Subtotal</th>
                        <th>Quitar</th>
                      </tr>
                    </thead>
                    <tbody>
                      {pedidoItems.map((it, idx) => (
                        <tr key={idx} data-testid={`item-pedido-${idx}`}>
                          <td>{it.producto_nombre}</td>
                          <td>{it.cantidad}</td>
                          <td>${it.costo_unitario.toLocaleString("es-AR")}</td>
                          <td><strong>${it.subtotal.toLocaleString("es-AR")}</strong></td>
                          <td>
                            <button
                              type="button"
                              className="btn-remover-item"
                              onClick={() => handleRemoverItemPedido(idx)}
                              data-testid={`btn-remover-item-${idx}`}
                            >
                              🗑️
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>

              {/* Total del pedido */}
              <div className="pedido-total-box" data-testid="pedido-total-box">
                <span>Total Estimado del Pedido:</span>
                <strong>${totalCalculadoNuevoPedido.toLocaleString("es-AR")}</strong>
              </div>
            </div>

            <div className="modal-footer">
              <button
                type="button"
                className="btn-cancelar-modal"
                onClick={() => setIsNuevoPedidoOpen(false)}
              >
                Cancelar
              </button>
              <button
                type="button"
                className="btn-confirmar-pedido"
                onClick={handleConfirmarPedido}
                disabled={isSavingPedido || pedidoItems.length === 0}
                data-testid="btn-confirmar-pedido"
              >
                {isSavingPedido ? "Guardando..." : "💾 Registrar Pedido"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ====================================================================== */}
      {/* MODAL: DETALLE DE PEDIDO                                               */}
      {/* ====================================================================== */}
      {isModalDetalleOpen && detallePedido && (
        <div className="modal-overlay" data-testid="modal-detalle-pedido">
          <div className="modal-content modal-detalle-content">
            <div className="modal-header">
              <h3>📦 Detalle de Pedido: {detallePedido.id}</h3>
              <button
                type="button"
                className="btn-close-modal"
                onClick={() => setIsModalDetalleOpen(false)}
              >
                ✕
              </button>
            </div>

            <div className="modal-body">
              <div className="detalle-meta-grid">
                <div>
                  <span>Proveedor:</span> <strong>{detallePedido.proveedor}</strong>
                </div>
                <div>
                  <span>Fecha:</span> {detallePedido.fecha}
                </div>
                <div>
                  <span>Estado:</span>{" "}
                  <span
                    className={`badge-estado badge-estado-${detallePedido.estado.toLowerCase()}`}
                  >
                    {detallePedido.estado}
                  </span>
                </div>
                <div>
                  <span>Total:</span> <strong>${detallePedido.total.toLocaleString("es-AR")}</strong>
                </div>
              </div>

              {detallePedido.observaciones && (
                <div className="detalle-observaciones">
                  <span>Observaciones:</span> {detallePedido.observaciones}
                </div>
              )}

              <h4>Ítems del Pedido ({detallePedido.items.length})</h4>
              <table className="tabla-items-detalle" data-testid="tabla-items-detalle">
                <thead>
                  <tr>
                    <th>Producto</th>
                    <th>Cantidad</th>
                    <th>Costo Unitario</th>
                    <th>Subtotal</th>
                  </tr>
                </thead>
                <tbody>
                  {detallePedido.items.map((it, idx) => (
                    <tr key={it.id || idx}>
                      <td><strong>{it.producto_nombre}</strong></td>
                      <td>{it.cantidad}</td>
                      <td>${it.costo_unitario.toLocaleString("es-AR")}</td>
                      <td>${it.subtotal.toLocaleString("es-AR")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="modal-footer modal-footer-actions">
              {detallePedido.estado === "PENDIENTE" && (
                <>
                  <button
                    type="button"
                    className="btn-recibir-pedido"
                    onClick={() => handleMarcarRecibido(detallePedido.id)}
                    disabled={isUpdatingEstado}
                    data-testid="btn-recibir-pedido"
                  >
                    ✅ Marcar como Recibido
                  </button>
                  <button
                    type="button"
                    className="btn-cancelar-pedido-action"
                    onClick={() => handleAbrirCancelarPedido(detallePedido.id)}
                    disabled={isUpdatingEstado}
                    data-testid="btn-cancelar-pedido-action"
                  >
                    🚫 Cancelar Pedido
                  </button>
                </>
              )}
              <button
                type="button"
                className="btn-cerrar-modal"
                onClick={() => setIsModalDetalleOpen(false)}
              >
                Cerrar
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ====================================================================== */}
      {/* MODAL: MOTIVO DE CANCELACIÓN                                           */}
      {/* ====================================================================== */}
      {isModalCancelarOpen && (
        <div className="modal-overlay" data-testid="modal-motivo-cancelar">
          <div className="modal-content modal-cancelar-content">
            <div className="modal-header">
              <h3>🚫 Cancelar Pedido {pedidoACancelarId}</h3>
              <button
                type="button"
                className="btn-close-modal"
                onClick={() => setIsModalCancelarOpen(false)}
              >
                ✕
              </button>
            </div>

            <div className="modal-body">
              <label>Motivo de cancelación (obligatorio):</label>
              <textarea
                value={motivoCancelacion}
                onChange={(e) => setMotivoCancelacion(e.target.value)}
                placeholder="Ej: Proveedor sin stock, pedido duplicado, etc."
                rows={3}
                data-testid="input-motivo-cancelacion"
              />
            </div>

            <div className="modal-footer">
              <button
                type="button"
                className="btn-cancelar-modal"
                onClick={() => setIsModalCancelarOpen(false)}
              >
                Volver
              </button>
              <button
                type="button"
                className="btn-confirmar-cancelacion"
                onClick={handleConfirmarCancelar}
                disabled={isUpdatingEstado || !motivoCancelacion.trim()}
                data-testid="btn-confirmar-cancelacion"
              >
                {isUpdatingEstado ? "Cancelando..." : "Confirmar Cancelación"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
