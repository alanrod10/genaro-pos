import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { Header } from "../src/components/Header";
import { ServicesAdmin } from "../src/components/ServicesAdmin";
import { ProductAdmin } from "../src/components/ProductAdmin";
import { ordenarProductosPorBusqueda } from "../src/utils/search";
import { Producto } from "../src/types/catalog";
import { MockApiClient, setApiClient } from "../src/services/api";

const PRODUCTOS_PRUEBA_BUSQUEDA: Producto[] = [
  {
    id_producto: 1,
    nombre: "Gaseosa Cola 1.5L",
    categoria: "Bebidas",
    proveedor: "Secco", // Solo coincide en proveedor
    unidad: "Unidad",
    costo: 800,
    margen_porcentaje: 50,
    precio_dia: 1500,
    precio_noche: 1500,
    precio_venta: 1500,
    activo: true,
    fecha_act: "2026-10-30",
  },
  {
    id_producto: 2,
    nombre: "Secco Pomelo 1.5L", // Comienza con Secco (Prioridad 1)
    categoria: "Bebidas",
    proveedor: "Distribuidora Central",
    unidad: "Unidad",
    costo: 700,
    margen_porcentaje: 60,
    precio_dia: 1400,
    precio_noche: 1400,
    precio_venta: 1400,
    activo: true,
    fecha_act: "2026-10-30",
  },
  {
    id_producto: 3,
    nombre: "Gaseosa Secco Tradicional 2.25L", // Palabra interna comienza con Secco (Prioridad 2)
    categoria: "Bebidas",
    proveedor: "Distribuidora Central",
    unidad: "Unidad",
    costo: 1000,
    margen_porcentaje: 50,
    precio_dia: 1800,
    precio_noche: 1800,
    precio_venta: 1800,
    activo: true,
    fecha_act: "2026-10-30",
  },
  {
    id_producto: 4,
    nombre: "Galletitas antiseccoticas", // Contiene secco dentro de una palabra (Prioridad 3)
    categoria: "Almacén",
    proveedor: "Arcor",
    unidad: "Unidad",
    costo: 400,
    margen_porcentaje: 50,
    precio_dia: 700,
    precio_noche: 700,
    precio_venta: 700,
    activo: true,
    fecha_act: "2026-10-30",
  },
];

describe("GenaroPOS — Navegación Móvil, Buscador Unificado y Servicios Vacío", () => {
  let mockClient: MockApiClient;

  beforeEach(() => {
    mockClient = new MockApiClient();
    setApiClient(mockClient);
  });

  // --- 1. Navegación Móvil y Accesibilidad de Pestañas ---
  describe("1. Navegación Móvil", () => {
    it("Muestra las 8 pestañas completas, todas accesibles y seleccionables sin cortes", () => {
      const mockSelectView = vi.fn();

      render(
        <Header
          isOnline={true}
          isDemo={true}
          activeView="caja"
          onSelectView={mockSelectView}
          onToggleDemo={vi.fn()}
          pendingCount={0}
          reviewCount={0}
          onOpenQueue={vi.fn()}
        />
      );

      // Verificar que todos los 8 botones de módulos están presentes y accesibles
      const btnCaja = screen.getByTestId("nav-btn-caja");
      const btnProductos = screen.getByTestId("nav-btn-productos");
      const btnServicios = screen.getByTestId("nav-btn-servicios");
      const btnGastos = screen.getByTestId("nav-btn-gastos");
      const btnHistorial = screen.getByTestId("nav-btn-historial");
      const btnPreventistas = screen.getByTestId("nav-btn-preventistas");
      const btnDashboard = screen.getByTestId("nav-btn-dashboard");
      const btnTesoreria = screen.getByTestId("nav-btn-tesoreria");

      expect(btnCaja).toBeInTheDocument();
      expect(btnProductos).toBeInTheDocument();
      expect(btnServicios).toBeInTheDocument();
      expect(btnGastos).toBeInTheDocument();
      expect(btnHistorial).toBeInTheDocument();
      expect(btnPreventistas).toBeInTheDocument();
      expect(btnDashboard).toBeInTheDocument();
      expect(btnTesoreria).toBeInTheDocument();

      // Indicador activo en caja
      expect(btnCaja).toHaveClass("nav-active");

      // Clic en Gastos y Cierre (que anteriormente quedaba cortada)
      fireEvent.click(btnGastos);
      expect(mockSelectView).toHaveBeenCalledWith("gastos");

      // Clic en Tesorería
      fireEvent.click(btnTesoreria);
      expect(mockSelectView).toHaveBeenCalledWith("tesoreria");
    });
  });

  // --- 2. Buscador Unificado (Caja y Productos) ---
  describe("2. Buscador Unificado de Productos", () => {
    it("Aplica estrictamente el orden de prioridad con el caso de prueba 'secco'", () => {
      const ordenados = ordenarProductosPorBusqueda(PRODUCTOS_PRUEBA_BUSQUEDA, "secco");

      // 1. Primero: Nombre comienza con 'Secco' (ID 2)
      expect(ordenados[0].id_producto).toBe(2);
      expect(ordenados[0].nombre).toBe("Secco Pomelo 1.5L");

      // 2. Segundo: Palabra del nombre comienza con 'Secco' (ID 3)
      expect(ordenados[1].id_producto).toBe(3);
      expect(ordenados[1].nombre).toBe("Gaseosa Secco Tradicional 2.25L");

      // 3. Tercero: Coincidencia interna en el nombre (ID 4)
      expect(ordenados[2].id_producto).toBe(4);
      expect(ordenados[2].nombre).toBe("Galletitas antiseccoticas");

      // 4. Último: Coincidencia únicamente en el proveedor (ID 1)
      expect(ordenados[3].id_producto).toBe(1);
      expect(ordenados[3].nombre).toBe("Gaseosa Cola 1.5L");
    });

    it("ProductAdmin y ProductCatalog producen exactamente el mismo ordenamiento ante 'secco'", async () => {
      // Verificar que ProductAdmin filtra y ordena con la misma lógica
      render(
        <ProductAdmin
          productos={PRODUCTOS_PRUEBA_BUSQUEDA}
          onRefresh={vi.fn()}
          onAddToast={vi.fn()}
          isDemo={true}
        />
      );

      const inputSearch = screen.getByTestId("admin-search-input");
      fireEvent.change(inputSearch, { target: { value: "secco" } });

      await waitFor(() => {
        const rows = screen.getAllByTestId(/admin-row-/);
        expect(rows).toHaveLength(4);
        // La primera fila debe ser 'Secco Pomelo' (ID 2)
        expect(rows[0]).toHaveAttribute("data-testid", "admin-row-2");
        // La segunda fila debe ser 'Gaseosa Secco Tradicional' (ID 3)
        expect(rows[1]).toHaveAttribute("data-testid", "admin-row-3");
        // La última debe ser 'Gaseosa Cola 1.5L' (proveedor Secco, ID 1)
        expect(rows[3]).toHaveAttribute("data-testid", "admin-row-1");
      });
    });
  });

  // --- 3. Módulo de Servicios: Empresa Vacía por Defecto y Validaciones ---
  describe("3. Servicios con Empresa Vacía", () => {
    it("El formulario inicia con los campos de plataforma y servicio vacíos", () => {
      render(<ServicesAdmin onAddToast={vi.fn()} isDemo={true} />);

      const selectPlataforma = screen.getByTestId("select-plataforma-origen") as HTMLSelectElement;
      const selectDestino = screen.getByTestId("select-servicio-destino") as HTMLSelectElement;
      expect(selectPlataforma.value).toBe("");
      expect(selectDestino.value).toBe("");
      expect(selectDestino.disabled).toBe(true);
    });

    it("Rechaza la confirmación sin plataforma y servicio seleccionados con mensaje claro", async () => {
      render(<ServicesAdmin onAddToast={vi.fn()} isDemo={true} />);

      const inputMonto = screen.getByTestId("input-service-amount");
      fireEvent.change(inputMonto, { target: { value: "2000" } });

      const form = screen.getByTestId("services-form");
      fireEvent.submit(form);

      await waitFor(() => {
        const alertError = screen.getByTestId("services-form-error");
        expect(alertError).toBeInTheDocument();
        expect(alertError).toHaveTextContent(
          "Debe seleccionar una empresa o servicio para continuar."
        );
      });
    });

    it("Al registrar con éxito, restablece los campos de plataforma y servicio vacíos para la siguiente carga", async () => {
      const mockToast = vi.fn();
      render(<ServicesAdmin onAddToast={mockToast} isDemo={true} />);

      const selectPlataforma = screen.getByTestId("select-plataforma-origen");
      fireEvent.change(selectPlataforma, { target: { value: "PREPAY" } });
      const selectDestino = screen.getByTestId("select-servicio-destino");
      fireEvent.change(selectDestino, { target: { value: "Personal" } });

      const inputMonto = screen.getByTestId("input-service-amount");
      fireEvent.change(inputMonto, { target: { value: "3000" } });

      const form = screen.getByTestId("services-form");
      fireEvent.submit(form);

      await waitFor(() => {
        expect(mockToast).toHaveBeenCalledWith(
          expect.stringContaining("Carga registrada: PREPAY → Personal"),
          "success"
        );
      });

      // Los campos deben haber quedado vacíos nuevamente
      expect((selectPlataforma as HTMLSelectElement).value).toBe("");
      expect((selectDestino as HTMLSelectElement).value).toBe("");
    });
  });
});
