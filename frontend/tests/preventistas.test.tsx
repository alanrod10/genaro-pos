import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { PreventistasAdmin } from "../src/components/PreventistasAdmin";
import {
  openDB,
  resetDBInstance,
  listarProductosPorProveedorDemo,
  crearProductoDemo,
  crearPedidoDemo,
  obtenerDetallePedidoDemo,
} from "../src/services/db";
import { MockApiClient, activarModoDemo } from "../src/services/api";

describe("GenaroPOS — Módulo de Preventistas y Pedidos (Frontend / IndexedDB)", () => {
  beforeEach(async () => {
    resetDBInstance();
    activarModoDemo();
  });

  it("1. Renderiza el módulo con selector de proveedor, tip y sub-pestañas", async () => {
    const handleToast = vi.fn();
    render(<PreventistasAdmin onAddToast={handleToast} isDemo={true} />);

    expect(screen.getByText("🚚 Preventistas y Pedidos")).toBeInTheDocument();
    expect(screen.getByTestId("subtab-precios")).toBeInTheDocument();
    expect(screen.getByTestId("subtab-pedidos")).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByTestId("select-proveedor")).toBeInTheDocument();
    });

    expect(screen.getByText(/Edita el Costo o el Precio y presiona Enter/i)).toBeInTheDocument();
  });

  it("2. Lista los productos del proveedor seleccionado con cálculo de ganancia", async () => {
    const handleToast = vi.fn();
    render(<PreventistasAdmin onAddToast={handleToast} isDemo={true} />);

    await waitFor(() => {
      expect(screen.getByTestId("tabla-precios-preventista")).toBeInTheDocument();
    });

    // Debe haber filas en la tabla
    const tabla = screen.getByTestId("tabla-precios-preventista");
    expect(tabla.querySelectorAll("tbody tr").length).toBeGreaterThan(0);
  });

  it("3. Recalcula el margen de ganancia en vivo al editar costo o precio en la tabla", async () => {
    // Inicializar producto demo
    await crearProductoDemo({
      nombre: "Agua Saborizada 1.5L",
      categoria: "Bebidas",
      proveedor: "Distribuidora Central",
      unidad: "Unidad",
      costo: 1000,
      precio_venta: 1500,
    });

    const handleToast = vi.fn();
    render(<PreventistasAdmin onAddToast={handleToast} isDemo={true} />);

    await waitFor(() => {
      expect(screen.getByText("Agua Saborizada 1.5L")).toBeInTheDocument();
    });

    // Encontrar inputs para este producto
    const select = screen.getByTestId("select-proveedor") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "Distribuidora Central" } });

    await waitFor(() => {
      expect(screen.getByText("Agua Saborizada 1.5L")).toBeInTheDocument();
    });

    // Modificar costo a 2000 y precio a 3000 -> margen: (3000-2000)/2000 = 50.0%
    const inputsCosto = screen.getAllByTestId(/^input-costo-/);
    const inputCosto = inputsCosto[0] as HTMLInputElement;

    fireEvent.change(inputCosto, { target: { value: "2000" } });

    const badges = screen.getAllByTestId(/^badge-margen-/);
    // Margen recalculado en vivo
    expect(badges[0].textContent).toContain("%");
  });

  it("4. Guarda selectivamente los precios modificados en IndexedDB", async () => {
    const handleToast = vi.fn();
    render(<PreventistasAdmin onAddToast={handleToast} isDemo={true} />);

    await waitFor(() => {
      expect(screen.getByTestId("tabla-precios-preventista")).toBeInTheDocument();
    });

    const inputCosto = screen.getAllByTestId(/^input-costo-/)[0];
    const inputPrecio = screen.getAllByTestId(/^input-precio-/)[0];

    fireEvent.change(inputCosto, { target: { value: "600" } });
    fireEvent.change(inputPrecio, { target: { value: "1000" } });

    const btnGuardar = screen.getByTestId("btn-guardar-precios");
    fireEvent.click(btnGuardar);

    await waitFor(() => {
      expect(handleToast).toHaveBeenCalledWith(
        expect.stringContaining("¡Los precios de este proveedor fueron actualizados!"),
        "success"
      );
    });

    // Verificar persistencia en IndexedDB
    const prods = await listarProductosPorProveedorDemo("Distribuidora Central");
    expect(prods[0].costo).toBe(600);
    expect(prods[0].precio_venta).toBe(1000);
  });

  it("5. Muestra advertencia si se pulsa guardar sin haber modificado ningún número", async () => {
    const handleToast = vi.fn();
    render(<PreventistasAdmin onAddToast={handleToast} isDemo={true} />);

    await waitFor(() => {
      expect(screen.getByTestId("btn-guardar-precios")).toBeInTheDocument();
    });

    const btnGuardar = screen.getByTestId("btn-guardar-precios");
    fireEvent.click(btnGuardar);

    expect(handleToast).toHaveBeenCalledWith(
      "No detecté ninguna modificación en los números.",
      "warning"
    );
  });

  it("6. Permite alta rápida de producto para el proveedor seleccionado", async () => {
    const handleToast = vi.fn();
    render(<PreventistasAdmin onAddToast={handleToast} isDemo={true} />);

    await waitFor(() => {
      expect(screen.getByTestId("btn-toggle-alta-rapida")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("btn-toggle-alta-rapida"));

    expect(screen.getByTestId("alta-rapida-card")).toBeInTheDocument();

    fireEvent.change(screen.getByTestId("input-alta-nombre"), {
      target: { value: "Alfajor de Coco Nuevo" },
    });
    fireEvent.change(screen.getByTestId("input-alta-costo"), {
      target: { value: "700" },
    });
    fireEvent.change(screen.getByTestId("input-alta-precio"), {
      target: { value: "1200" },
    });

    // Indicador de margen estimado en vivo
    expect(screen.getByTestId("alta-margen-estimado").textContent).toContain("71.43%");

    fireEvent.click(screen.getByTestId("btn-guardar-alta"));

    await waitFor(() => {
      expect(handleToast).toHaveBeenCalledWith(
        expect.stringContaining("añadido al catálogo"),
        "success"
      );
    });
  });

  it("7. Rechaza alta rápida con nombre vacío o precio menor o igual a cero", async () => {
    const handleToast = vi.fn();
    render(<PreventistasAdmin onAddToast={handleToast} isDemo={true} />);

    await waitFor(() => {
      expect(screen.getByTestId("btn-toggle-alta-rapida")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("btn-toggle-alta-rapida"));

    // Intento con precio 0
    fireEvent.change(screen.getByTestId("input-alta-nombre"), {
      target: { value: "Producto Sin Precio" },
    });
    fireEvent.change(screen.getByTestId("input-alta-precio"), {
      target: { value: "0" },
    });

    fireEvent.click(screen.getByTestId("btn-guardar-alta"));

    expect(handleToast).toHaveBeenCalledWith(
      expect.stringContaining("El precio debe ser mayor a 0"),
      "warning"
    );
  });

  it("8. Cambia a pestaña Toma de Pedidos, crea un nuevo pedido y calcula su total", async () => {
    const handleToast = vi.fn();
    render(<PreventistasAdmin onAddToast={handleToast} isDemo={true} />);

    // Esperar a que los proveedores se carguen inicialmente
    await waitFor(() => {
      expect(screen.getByTestId("select-proveedor")).toBeInTheDocument();
    });

    // Ir a pestaña pedidos
    fireEvent.click(screen.getByTestId("subtab-pedidos"));

    await waitFor(() => {
      expect(screen.getByTestId("btn-abrir-nuevo-pedido")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("btn-abrir-nuevo-pedido"));

    expect(screen.getByTestId("modal-nuevo-pedido")).toBeInTheDocument();

    // Seleccionar proveedor explícitamente en el modal
    fireEvent.change(screen.getByTestId("select-pedido-prov"), {
      target: { value: "Distribuidora Central" },
    });

    // Esperar carga de productos del proveedor
    await waitFor(() => {
      expect(screen.getByTestId("select-item-producto")).toBeInTheDocument();
    });

    const selectProd = screen.getByTestId("select-item-producto") as HTMLSelectElement;
    await waitFor(() => {
      expect(selectProd.options.length).toBeGreaterThan(1);
    });

    fireEvent.change(selectProd, { target: { value: selectProd.options[1].value } });

    fireEvent.change(screen.getByTestId("input-item-cantidad"), {
      target: { value: "10" },
    });
    fireEvent.change(screen.getByTestId("input-item-costo"), {
      target: { value: "1500" },
    });

    fireEvent.click(screen.getByTestId("btn-agregar-item-pedido"));

    // Total estimado: 10 * 1500 = $15.000
    await waitFor(() => {
      expect(screen.getByTestId("pedido-total-box").textContent).toContain("15.000");
    });

    // Guardar pedido
    fireEvent.click(screen.getByTestId("btn-confirmar-pedido"));

    await waitFor(() => {
      expect(handleToast).toHaveBeenCalledWith(
        expect.stringContaining("registrado exitosamente"),
        "success"
      );
    });
  });

  it("9. Consulta el detalle de un pedido y permite marcarlo como RECIBIDO", async () => {
    const pedidoCreado = await crearPedidoDemo({
      proveedor: "Cervecería Quilmes",
      observaciones: "Controlar vencimientos",
      items: [
        {
          producto_nombre: "Quilmes Clásica 1L",
          cantidad: 12,
          costo_unitario: 1400,
          subtotal: 16800,
        },
      ],
    });

    const handleToast = vi.fn();
    render(<PreventistasAdmin onAddToast={handleToast} isDemo={true} />);

    fireEvent.click(screen.getByTestId("subtab-pedidos"));

    await waitFor(() => {
      expect(screen.getByTestId(`btn-ver-detalle-${pedidoCreado.id}`)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId(`btn-ver-detalle-${pedidoCreado.id}`));

    await waitFor(() => {
      expect(screen.getByTestId("modal-detalle-pedido")).toBeInTheDocument();
    });
    expect(screen.getByText("Quilmes Clásica 1L")).toBeInTheDocument();

    // Marcar como recibido
    fireEvent.click(screen.getByTestId("btn-recibir-pedido"));

    await waitFor(() => {
      expect(handleToast).toHaveBeenCalledWith(
        expect.stringContaining("marcado como RECIBIDO"),
        "success"
      );
    });

    const detalleActualizado = await obtenerDetallePedidoDemo(pedidoCreado.id);
    expect(detalleActualizado?.estado).toBe("RECIBIDO");
  });

  it("10. Permite cancelar un pedido registrando el motivo obligatorio", async () => {
    const pedidoCreado = await crearPedidoDemo({
      proveedor: "Distribuidora Dulce",
      items: [
        {
          producto_nombre: "Golosinas Varias",
          cantidad: 5,
          costo_unitario: 2000,
          subtotal: 10000,
        },
      ],
    });

    const handleToast = vi.fn();
    render(<PreventistasAdmin onAddToast={handleToast} isDemo={true} />);

    fireEvent.click(screen.getByTestId("subtab-pedidos"));

    await waitFor(() => {
      expect(screen.getByTestId(`btn-ver-detalle-${pedidoCreado.id}`)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId(`btn-ver-detalle-${pedidoCreado.id}`));

    await waitFor(() => {
      expect(screen.getByTestId("modal-detalle-pedido")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("btn-cancelar-pedido-action"));

    await waitFor(() => {
      expect(screen.getByTestId("modal-motivo-cancelar")).toBeInTheDocument();
    });

    fireEvent.change(screen.getByTestId("input-motivo-cancelacion"), {
      target: { value: "Mercadería no disponible en distribuidora" },
    });

    fireEvent.click(screen.getByTestId("btn-confirmar-cancelacion"));

    await waitFor(() => {
      expect(handleToast).toHaveBeenCalledWith(
        expect.stringContaining("cancelado"),
        "info"
      );
    });

    const detalle = await obtenerDetallePedidoDemo(pedidoCreado.id);
    expect(detalle?.estado).toBe("CANCELADO");
    expect(detalle?.observaciones).toContain("Mercadería no disponible en distribuidora");
  });

  it("11. Preserva todas las tiendas anteriores de IndexedDB al inicializar versión 5", async () => {
    const db = await openDB();
    const storeNames = Array.from(db.objectStoreNames);

    expect(storeNames).toContain("ventas_queue");
    expect(storeNames).toContain("catalogo_cache");
    expect(storeNames).toContain("meta");
    expect(storeNames).toContain("catalogo_demo");
    expect(storeNames).toContain("servicios_demo");
    expect(storeNames).toContain("gastos_demo");
    expect(storeNames).toContain("cierres_demo");
    expect(storeNames).toContain("pedidos_demo");
  });

  it("12. Modo Demo estricto: MockApiClient nunca ejecuta peticiones fetch de red", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const mockClient = new MockApiClient();

    await mockClient.obtenerProveedores();
    await mockClient.obtenerPedidos();
    await mockClient.obtenerProductosPorProveedor("Coca Cola");

    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });
});
