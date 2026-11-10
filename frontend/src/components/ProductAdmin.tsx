import React, { useMemo, useState, useRef, useEffect } from "react";
import {
  CATEGORIAS_CANONICAS,
  Producto,
  ProductoInput,
} from "../types/catalog";
import { getApiClient } from "../services/api";
import {
  coincideBusquedaProducto,
  ordenarProductosPorBusqueda,
  renderHighlightedText,
} from "../utils/search";

export function normalizarCategoriaCanonica(categoria?: string): string {
  const raw = (categoria || "").trim();
  if ((CATEGORIAS_CANONICAS as readonly string[]).includes(raw)) {
    return raw;
  }
  const lower = raw.toLowerCase();
  if (lower.includes("golosina") || lower.includes("chocolate") || lower.includes("caramelo") || lower.includes("chicle")) {
    return "Golosinas y Chocolates";
  }
  if (lower.includes("galletita") || lower.includes("alfajor") || lower.includes("oblea")) {
    return "Galletitas y Alfajores";
  }
  if (lower.includes("snack") || lower.includes("salado") || lower.includes("papas")) {
    return "Snacks y Salados";
  }
  if (lower.includes("alcohol") || lower.includes("cerveza") || lower.includes("vino") || lower.includes("fernet")) {
    return "Bebidas con Alcohol";
  }
  if (lower.includes("bebida") || lower.includes("gaseosa") || lower.includes("agua") || lower.includes("jugo") || lower.includes("infusi")) {
    return "Bebidas e Infusiones";
  }
  if (lower.includes("cigarr") || lower.includes("tabac") || lower.includes("encendedor")) {
    return "Cigarrillos y Tabaquería";
  }
  if (lower.includes("panad") || lower.includes("fiambr") || lower.includes("queso") || lower.includes("jamon")) {
    return "Panadería y Fiambrería";
  }
  if (lower.includes("almac") || lower.includes("lácteo") || lower.includes("lacteo") || lower.includes("leche") || lower.includes("yerba")) {
    return "Almacén y Lácteos";
  }
  if (lower.includes("librer") || lower.includes("regal") || lower.includes("servicio") || lower.includes("fotocopia") || lower.includes("impresi")) {
    return "Librería y Regalería";
  }
  if (lower.includes("higien") || lower.includes("cuidado") || lower.includes("limpieza") || lower.includes("perfum")) {
    return "Higiene y Cuidado Personal";
  }
  return "Otros";
}

interface ProductAdminProps {
  productos: Producto[];
  onRefresh: () => Promise<void>;
  onAddToast: (mensaje: string, tipo?: "info" | "success" | "warning" | "error") => void;
  isDemo: boolean;
}

export const ProductAdmin: React.FC<ProductAdminProps> = ({
  productos,
  onRefresh,
  onAddToast,
  isDemo,
}) => {
  // Filtros
  const [busqueda, setBusqueda] = useState<string>("");
  const [categoriaFiltro, setCategoriaFiltro] = useState<string>("TODAS");
  const [estadoFiltro, setEstadoFiltro] = useState<"TODOS" | "ACTIVOS" | "INACTIVOS">("TODOS");

  // Estado del modal de creación / edición / duplicación
  const [isModalOpen, setIsModalOpen] = useState<boolean>(false);
  const [editingProduct, setEditingProduct] = useState<Producto | null>(null);
  const [isDuplicating, setIsDuplicating] = useState<boolean>(false);

  // Campos del formulario
  const [formNombre, setFormNombre] = useState<string>("");
  const [formCategoria, setFormCategoria] = useState<string>("Golosinas y Chocolates");
  const [formProveedor, setFormProveedor] = useState<string>("General");
  const [formUnidad, setFormUnidad] = useState<string>("Unidad");
  const [formCosto, setFormCosto] = useState<number>(0);
  const [formPrecio, setFormPrecio] = useState<number>(0);
  const [formActivo, setFormActivo] = useState<boolean>(true);
  const [formError, setFormError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState<boolean>(false);
  const savingRef = useRef<boolean>(false);
  const inputNombreRef = useRef<HTMLInputElement | null>(null);

  // Focalizar y seleccionar automáticamente el campo Nombre al abrir el modal
  useEffect(() => {
    if (isModalOpen && inputNombreRef.current) {
      inputNombreRef.current.focus();
      if (isDuplicating || inputNombreRef.current.value) {
        inputNombreRef.current.select();
      }
    }
  }, [isModalOpen, isDuplicating]);

  // Categorías presentes en los productos
  const categoriasUnicas = useMemo(() => {
    const setCat = new Set(productos.map((p) => p.categoria || "Otro"));
    return ["TODAS", ...Array.from(setCat).sort()];
  }, [productos]);

  // Filtrado de productos
  const productosFiltrados = useMemo(() => {
    const q = busqueda.trim();
    const filtrados = productos.filter((p) => {
      // Filtro de estado
      if (estadoFiltro === "ACTIVOS" && p.activo === false) return false;
      if (estadoFiltro === "INACTIVOS" && p.activo !== false) return false;

      // Filtro de categoría
      if (categoriaFiltro !== "TODAS" && p.categoria !== categoriaFiltro) return false;

      // Filtro de búsqueda unificado
      if (q) {
        return coincideBusquedaProducto(p, q);
      }
      return true;
    });

    return ordenarProductosPorBusqueda(filtrados, q);
  }, [productos, estadoFiltro, categoriaFiltro, busqueda]);

  // Abrir modal para nuevo producto
  const handleOpenCreate = () => {
    setEditingProduct(null);
    setIsDuplicating(false);
    setFormNombre("");
    setFormCategoria("Golosinas y Chocolates");
    setFormProveedor("General");
    setFormUnidad("Unidad");
    setFormCosto(0);
    setFormPrecio(0);
    setFormActivo(true);
    setFormError(null);
    setIsModalOpen(true);
  };

  // Abrir modal para duplicar producto (Clonado Rápido)
  const handleOpenDuplicate = (prod: Producto) => {
    setEditingProduct(null);
    setIsDuplicating(true);
    setFormNombre(`${prod.nombre} (Copia)`);
    setFormCategoria(normalizarCategoriaCanonica(prod.categoria));
    setFormProveedor(prod.proveedor || "General");
    setFormUnidad(prod.unidad || "Unidad");
    setFormCosto(prod.costo);
    setFormPrecio(prod.precio_venta ?? prod.precio_dia ?? 0);
    setFormActivo(prod.activo !== false);
    setFormError(null);
    setIsModalOpen(true);
  };

  // Abrir modal para editar producto
  const handleOpenEdit = (prod: Producto) => {
    setEditingProduct(prod);
    setIsDuplicating(false);
    setFormNombre(prod.nombre);
    setFormCategoria(normalizarCategoriaCanonica(prod.categoria));
    setFormProveedor(prod.proveedor);
    setFormUnidad(prod.unidad);
    setFormCosto(prod.costo);
    setFormPrecio(prod.precio_venta ?? prod.precio_dia ?? 0);
    setFormActivo(prod.activo !== false);
    setFormError(null);
    setIsModalOpen(true);
  };

  // Guardar formulario
  const handleSave = async (e?: React.FormEvent | React.MouseEvent) => {
    if (e) e.preventDefault();
    if (isSaving || savingRef.current) return;
    setFormError(null);

    const nomLimpio = formNombre.trim();
    if (!nomLimpio) {
      setFormError("El nombre del producto es obligatorio.");
      return;
    }

    if (formCosto < 0) {
      setFormError("El costo no puede ser un número negativo.");
      return;
    }

    if (formPrecio <= 0) {
      setFormError("El precio de venta debe ser un número mayor a cero.");
      return;
    }

    savingRef.current = true;
    setIsSaving(true);
    const api = getApiClient();

    const payload: ProductoInput = {
      nombre: nomLimpio,
      categoria: formCategoria.trim() || "Otro",
      proveedor: formProveedor.trim() || "General",
      unidad: formUnidad.trim() || "Unidad",
      costo: Math.round(formCosto),
      precio_venta: Math.round(formPrecio),
      activo: formActivo,
    };

    try {
      if (editingProduct) {
        // Modificación puntual por ID
        const res = await api.editarProducto(editingProduct.id_producto, payload);
        if (res.status === 200 && res.data) {
          onAddToast(`Producto "${res.data.nombre}" actualizado correctamente.`, "success");
          setIsSaving(false);
          setIsModalOpen(false);
          await onRefresh();
        } else {
          setFormError(res.error || "Error al actualizar el producto.");
        }
      } else {
        // Alta de producto nuevo
        const res = await api.crearProducto(payload);
        if (res.status === 201 && res.data) {
          onAddToast(`Producto "${res.data.nombre}" añadido al catálogo.`, "success");
          setIsSaving(false);
          setIsModalOpen(false);
          await onRefresh();
        } else {
          setFormError(res.error || "Error al crear el producto.");
        }
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error inesperado al guardar.";
      if (typeof window !== "undefined") setFormError(msg);
    } finally {
      savingRef.current = false;
      if (typeof window !== "undefined") setIsSaving(false);
    }
  };

  // Activar o desactivar producto rápidamente
  const handleToggleStatus = async (prod: Producto) => {
    const nuevoEstado = !prod.activo;
    const api = getApiClient();
    try {
      const res = await api.cambiarEstadoProducto(prod.id_producto, nuevoEstado);
      if (res.status === 200 && res.data) {
        onAddToast(
          `Producto "${prod.nombre}" ${nuevoEstado ? "activado" : "desactivado"}.`,
          "info"
        );
        await onRefresh();
      } else {
        onAddToast(res.error || "Error al cambiar estado.", "error");
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al cambiar estado.";
      onAddToast(msg, "error");
    }
  };

  // Eliminación inteligente (Smart Delete) con confirmación rápida
  const handleDelete = async (prod: Producto) => {
    const confirmado =
      typeof window !== "undefined" && typeof window.confirm === "function"
        ? window.confirm(
            `¿Estás seguro de eliminar "${prod.nombre}"?\n\nSi tiene ventas registradas se desactivará (baja lógica); si no tiene ventas se borrará definitivamente.`
          )
        : true;

    if (!confirmado) return;

    const api = getApiClient();
    try {
      const res = await api.eliminarProducto(prod.id_producto);
      if (res.status === 200 && res.data) {
        onAddToast(
          res.data.mensaje ||
            (res.data.eliminado_fisico
              ? `Producto "${prod.nombre}" eliminado definitivamente.`
              : `Producto "${prod.nombre}" desactivado lógicamente (posee ventas).`),
          res.data.eliminado_fisico ? "success" : "info"
        );
        await onRefresh();
      } else {
        onAddToast(res.error || "Error al eliminar el producto.", "error");
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al eliminar el producto.";
      onAddToast(msg, "error");
    }
  };

  // Margen estimado proyectado en modal
  const margenEstimado = useMemo(() => {
    if (formCosto <= 0) return 0;
    return Math.round(((formPrecio - formCosto) / formCosto) * 10000) / 100;
  }, [formCosto, formPrecio]);

  return (
    <div className="product-admin-container" data-testid="product-admin-container">
      {/* Aviso informativo en Modo Demo */}
      {isDemo && (
        <div className="admin-demo-banner" data-testid="admin-demo-banner">
          🧪 <strong>MODO DEMO:</strong> Podés crear, editar, activar y desactivar productos de prueba.
          Los cambios se guardan localmente en tu navegador y no alteran la base de datos real.
        </div>
      )}

      {/* Barra de Controles y Filtros */}
      <div className="admin-toolbar">
        <div className="admin-toolbar-left">
          <input
            type="search"
            className="admin-search-input"
            placeholder="🔍 Buscar por nombre o proveedor..."
            value={busqueda}
            onChange={(e) => setBusqueda(e.target.value)}
            data-testid="admin-search-input"
          />

          <select
            className="admin-filter-select"
            value={categoriaFiltro}
            onChange={(e) => setCategoriaFiltro(e.target.value)}
            data-testid="admin-category-filter"
          >
            {categoriasUnicas.map((cat) => (
              <option key={cat} value={cat}>
                {cat === "TODAS" ? "Todas las Categorías" : cat}
              </option>
            ))}
          </select>

          <select
            className="admin-filter-select"
            value={estadoFiltro}
            onChange={(e) => setEstadoFiltro(e.target.value as any)}
            data-testid="admin-status-filter"
          >
            <option value="TODOS">Todos los Estados</option>
            <option value="ACTIVOS">Solo Activos</option>
            <option value="INACTIVOS">Solo Inactivos</option>
          </select>
        </div>

        <div className="admin-toolbar-right">
          <button
            type="button"
            className="btn-create-product"
            onClick={handleOpenCreate}
            data-testid="btn-new-product"
          >
            ➕ Nuevo Producto
          </button>
        </div>
      </div>

      {/* Resumen de contadores */}
      <div className="admin-stats-bar">
        <span>Total: <strong>{productos.length}</strong></span>
        <span>Mostrando: <strong>{productosFiltrados.length}</strong></span>
        <span>Activos: <strong className="text-green">{productos.filter((p) => p.activo !== false).length}</strong></span>
        <span>Inactivos: <strong className="text-red">{productos.filter((p) => p.activo === false).length}</strong></span>
      </div>

      {/* Vista de Tabla para Pantallas Grandes */}
      <div className="admin-table-wrapper">
        <table className="admin-table" data-testid="admin-table">
          <thead>
            <tr>
              <th className="sticky left-0 z-20 w-[80px] min-w-[80px] max-w-[80px] bg-[#F5EFE7] dark:bg-[#1D2026]">
                ID
              </th>
              <th className="sticky left-[80px] z-20 min-w-[200px] max-w-[320px] bg-[#F5EFE7] dark:bg-[#1D2026] border-r border-[#E5D9CF] dark:border-[#3A3F48]">
                Producto
              </th>
              <th>Categoría</th>
              <th>Proveedor</th>
              <th>Unidad</th>
              <th className="th-number">Costo</th>
              <th className="th-number">Precio Venta</th>
              <th className="th-number">Margen</th>
              <th>Estado</th>
              <th className="th-actions">Acciones</th>
            </tr>
          </thead>
          <tbody>
            {productosFiltrados.length === 0 ? (
              <tr>
                <td colSpan={10} className="td-empty">
                  No se encontraron productos con los filtros seleccionados.
                </td>
              </tr>
            ) : (
              productosFiltrados.map((prod) => {
                const precio = prod.precio_venta ?? prod.precio_dia ?? 0;
                const margenPct =
                  prod.costo > 0
                    ? Math.round(((precio - prod.costo) / prod.costo) * 10000) / 100
                    : 0;

                return (
                  <tr
                    key={prod.id_producto}
                    className={`group ${prod.activo === false ? "row-inactive" : ""}`}
                    data-testid={`admin-row-${prod.id_producto}`}
                  >
                    <td
                      className={`td-id sticky left-0 z-10 w-[80px] min-w-[80px] max-w-[80px] ${
                        prod.activo === false
                          ? "bg-[#F5EEE7] dark:bg-[#22252B]"
                          : "bg-white dark:bg-[#25282E]"
                      } group-hover:bg-[#F9F5EF] dark:group-hover:bg-[#2E333C]`}
                    >
                      #{prod.id_producto}
                    </td>
                    <td
                      className={`td-name sticky left-[80px] z-10 min-w-[200px] max-w-[320px] ${
                        prod.activo === false
                          ? "bg-[#F5EEE7] dark:bg-[#22252B]"
                          : "bg-white dark:bg-[#25282E]"
                      } group-hover:bg-[#F9F5EF] dark:group-hover:bg-[#2E333C] border-r border-[#E5D9CF] dark:border-[#3A3F48]`}
                    >
                      <strong>
                        {busqueda.trim() ? (
                          <>
                            <span className="sr-only">{prod.nombre}</span>
                            <span aria-hidden="true">{renderHighlightedText(prod.nombre, busqueda)}</span>
                          </>
                        ) : (
                          prod.nombre
                        )}
                      </strong>
                    </td>
                    <td><span className="badge-cat">{prod.categoria}</span></td>
                    <td className="td-muted">{prod.proveedor}</td>
                    <td>{prod.unidad}</td>
                    <td className="td-number">${prod.costo.toLocaleString("es-AR")}</td>
                    <td className="td-number td-price">
                      ${precio.toLocaleString("es-AR")}
                    </td>
                    <td className="td-number">
                      <span className={`badge-margin ${margenPct >= 30 ? "margin-high" : "margin-low"}`}>
                        {margenPct > 0 ? `+${margenPct}%` : `${margenPct}%`}
                      </span>
                    </td>
                    <td>
                      <span
                        className={`badge-status ${prod.activo !== false ? "status-active" : "status-inactive"}`}
                      >
                        {prod.activo !== false ? "Activo" : "Inactivo"}
                      </span>
                    </td>
                    <td className="td-actions">
                      <button
                        type="button"
                        className="btn-action-edit"
                        onClick={() => handleOpenEdit(prod)}
                        title="Modificar producto"
                        data-testid={`btn-edit-${prod.id_producto}`}
                      >
                        ✏️ Editar
                      </button>
                      <button
                        type="button"
                        className="btn-action-duplicate"
                        onClick={() => handleOpenDuplicate(prod)}
                        title="Duplicar producto (Clonado rápido)"
                        data-testid={`btn-duplicate-${prod.id_producto}`}
                      >
                        📋 Duplicar
                      </button>
                      <button
                        type="button"
                        className={`btn-action-status ${prod.activo !== false ? "btn-deactivate" : "btn-activate"}`}
                        onClick={() => handleToggleStatus(prod)}
                        title={prod.activo !== false ? "Desactivar de Caja" : "Activar en Caja"}
                        data-testid={`btn-toggle-status-${prod.id_producto}`}
                      >
                        {prod.activo !== false ? "Desactivar" : "Activar"}
                      </button>
                      <button
                        type="button"
                        className="btn-action-delete"
                        onClick={() => handleDelete(prod)}
                        title="Eliminar producto (Smart Delete)"
                        data-testid={`btn-delete-${prod.id_producto}`}
                      >
                        🗑️ Eliminar
                      </button>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* Vista de Tarjetas para Celulares */}
      <div className="admin-cards-grid" data-testid="admin-cards-grid">
        {productosFiltrados.length === 0 ? (
          <div className="empty-catalog-message">
            <p>No se encontraron productos con los filtros seleccionados.</p>
          </div>
        ) : (
          productosFiltrados.map((prod) => {
            const precio = prod.precio_venta ?? prod.precio_dia ?? 0;
            const margenPct =
              prod.costo > 0
                ? Math.round(((precio - prod.costo) / prod.costo) * 10000) / 100
                : 0;

            return (
              <div
                key={prod.id_producto}
                className={`admin-mobile-card ${prod.activo === false ? "card-inactive" : ""}`}
                data-testid={`admin-card-${prod.id_producto}`}
              >
                <div className="card-top-row">
                  <div className="card-title-group">
                    <span className="card-id">#{prod.id_producto}</span>
                    <h4 className="card-product-name">
                      {busqueda.trim() ? (
                        <>
                          <span className="sr-only">{prod.nombre}</span>
                          <span aria-hidden="true">{renderHighlightedText(prod.nombre, busqueda)}</span>
                        </>
                      ) : (
                        prod.nombre
                      )}
                    </h4>
                  </div>
                  <span
                    className={`badge-status ${prod.activo !== false ? "status-active" : "status-inactive"}`}
                  >
                    {prod.activo !== false ? "Activo" : "Inactivo"}
                  </span>
                </div>

                <div className="card-meta-row">
                  <span className="badge-cat">{prod.categoria}</span>
                  <span className="card-unit">{prod.unidad}</span>
                  <span className="card-prov">{prod.proveedor}</span>
                </div>

                <div className="card-financial-row">
                  <div className="financial-box">
                    <span className="box-label">Costo</span>
                    <span className="box-val">${prod.costo.toLocaleString("es-AR")}</span>
                  </div>
                  <div className="financial-box highlight-price">
                    <span className="box-label">Precio Venta</span>
                    <span className="box-val">${precio.toLocaleString("es-AR")}</span>
                  </div>
                  <div className="financial-box">
                    <span className="box-label">Margen</span>
                    <span className={`box-val ${margenPct >= 30 ? "text-green" : "text-amber"}`}>
                      {margenPct > 0 ? `+${margenPct}%` : `${margenPct}%`}
                    </span>
                  </div>
                </div>

                <div className="card-actions-row">
                  <button
                    type="button"
                    className="btn-card-edit"
                    onClick={() => handleOpenEdit(prod)}
                    data-testid={`btn-card-edit-${prod.id_producto}`}
                  >
                    ✏️ Editar
                  </button>
                  <button
                    type="button"
                    className="btn-card-duplicate"
                    onClick={() => handleOpenDuplicate(prod)}
                    data-testid={`btn-card-duplicate-${prod.id_producto}`}
                  >
                    📋 Duplicar
                  </button>
                  <button
                    type="button"
                    className={`btn-card-status ${prod.activo !== false ? "btn-deactivate" : "btn-activate"}`}
                    onClick={() => handleToggleStatus(prod)}
                    data-testid={`btn-card-status-${prod.id_producto}`}
                  >
                    {prod.activo !== false ? "Desactivar" : "Activar"}
                  </button>
                  <button
                    type="button"
                    className="btn-card-delete"
                    onClick={() => handleDelete(prod)}
                    data-testid={`btn-card-delete-${prod.id_producto}`}
                  >
                    🗑️ Eliminar
                  </button>
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* Modal de Creación / Edición */}
      {isModalOpen && (
        <div className="modal-backdrop" data-testid="product-modal">
          <div className="modal-card admin-form-card">
            <div className="modal-header">
              <h3 className="modal-title">
                {editingProduct ? `Editar Producto #${editingProduct.id_producto}` : "Alta de Producto"}
              </h3>
              <button
                type="button"
                className="btn-modal-close"
                onClick={() => setIsModalOpen(false)}
                disabled={isSaving}
                data-testid="btn-close-modal"
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleSave} className="admin-product-form" data-testid="admin-product-form">
              <div className="modal-body">
                {formError && (
                  <div className="alert-box-error" data-testid="form-error-banner">
                    ⚠️ {formError}
                  </div>
                )}

                {/* Nombre */}
                <div className="form-group">
                  <label htmlFor="prod-nombre">
                    Nombre del Producto: <span className="req">*</span>
                  </label>
                  <input
                    ref={inputNombreRef}
                    id="prod-nombre"
                    type="text"
                    required
                    autoFocus
                    className="form-input"
                    placeholder="Ej: Gaseosa Coca Cola Original 500ml"
                    value={formNombre}
                    onChange={(e) => setFormNombre(e.target.value)}
                    data-testid="input-prod-nombre"
                  />
                  <small className="form-helper-text" data-testid="nombre-helper-text">
                    Estructura sugerida: <strong>[TIPO] + [MARCA] + [VARIEDAD/SABOR] + [CONTENIDO/PESO]</strong> (Ej: <em>Gaseosa Coca Cola Original 500ml</em>)
                  </small>
                </div>

                {/* Categoría y Proveedor */}
                <div className="form-row">
                  <div className="form-group flex-1">
                    <label htmlFor="prod-cat">Categoría:</label>
                    <select
                      id="prod-cat"
                      className="form-input"
                      value={formCategoria}
                      onChange={(e) => setFormCategoria(e.target.value)}
                      data-testid="input-prod-categoria"
                    >
                      {CATEGORIAS_CANONICAS.map((cat) => (
                        <option key={cat} value={cat}>
                          {cat}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div className="form-group flex-1">
                    <label htmlFor="prod-prov">Proveedor:</label>
                    <input
                      id="prod-prov"
                      type="text"
                      list="proveedores-list"
                      className="form-input"
                      value={formProveedor}
                      onChange={(e) => setFormProveedor(e.target.value)}
                      data-testid="input-prod-proveedor"
                    />
                    <datalist id="proveedores-list">
                      <option value="Distribuidora Central" />
                      <option value="Dulces del Norte" />
                      <option value="Local" />
                      <option value="General" />
                    </datalist>
                  </div>
                </div>

                {/* Unidad */}
                <div className="form-group">
                  <label htmlFor="prod-uni">Unidad de Venta:</label>
                  <select
                    id="prod-uni"
                    className="form-input"
                    value={formUnidad}
                    onChange={(e) => setFormUnidad(e.target.value)}
                    data-testid="input-prod-unidad"
                  >
                    <option value="Unidad">Unidad</option>
                    <option value="Kg">Kilogramo (Kg)</option>
                    <option value="100g">100 gramos (100g)</option>
                    <option value="Litro">Litro</option>
                    <option value="Hoja">Hoja</option>
                    <option value="Paquete">Paquete</option>
                  </select>
                </div>

                {/* Costo y Precio de Venta */}
                <div className="form-row">
                  <div className="form-group flex-1">
                    <label htmlFor="prod-costo">Costo ($):</label>
                    <input
                      id="prod-costo"
                      type="number"
                      min="0"
                      step="50"
                      className="form-input"
                      value={formCosto || ""}
                      onChange={(e) => setFormCosto(Number(e.target.value))}
                      data-testid="input-prod-costo"
                    />
                  </div>

                  <div className="form-group flex-1">
                    <label htmlFor="prod-precio">
                      Precio de Venta ($): <span className="req">*</span>
                    </label>
                    <input
                      id="prod-precio"
                      type="number"
                      min="1"
                      step="50"
                      required
                      className="form-input highlight-input"
                      value={formPrecio || ""}
                      onChange={(e) => setFormPrecio(Number(e.target.value))}
                      data-testid="input-prod-precio"
                    />
                  </div>
                </div>

                {/* Margen Proyectado en Tiempo Real */}
                <div className="margin-preview-banner" data-testid="margin-preview-banner">
                  <span>Margen Proyectado:</span>
                  <strong className={margenEstimado >= 30 ? "text-green" : "text-amber"}>
                    {margenEstimado > 0 ? `+${margenEstimado}%` : `${margenEstimado}%`}
                  </strong>
                  <span className="preview-diff">
                    Ganancia bruta: ${(formPrecio - formCosto).toLocaleString("es-AR")}
                  </span>
                </div>

                {/* Estado Activo */}
                <div className="form-checkbox-group">
                  <label className="checkbox-label">
                    <input
                      type="checkbox"
                      checked={formActivo}
                      onChange={(e) => setFormActivo(e.target.checked)}
                      data-testid="input-prod-activo"
                    />
                    <span>Producto activo (disponible para la venta en Caja)</span>
                  </label>
                </div>
              </div>

              <div className="modal-footer">
                <button
                  type="button"
                  className="btn-cancel"
                  onClick={() => setIsModalOpen(false)}
                  disabled={isSaving}
                  data-testid="btn-cancel-product"
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  className="btn-confirm-save"
                  disabled={isSaving}
                  onClick={handleSave}
                  data-testid="btn-save-product"
                >
                  {isSaving ? "Guardando..." : editingProduct ? "Actualizar Producto" : "Crear Producto"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
