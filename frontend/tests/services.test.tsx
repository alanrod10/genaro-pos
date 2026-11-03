import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "../src/App";
import {
  openDB,
  resetDBInstance,
  listarServiciosDemo,
  guardarCargaDemo,
  calcularResumenServiciosDemo,
  guardarVentaLocal,
  listarVentasLocales,
  guardarCatalogoCache,
  obtenerCatalogoCache,
} from "../src/services/db";
import { activarModoDemo } from "../src/services/api";
import { calcularRecargoAutomatico } from "../src/components/ServicesAdmin";

describe("GenaroPOS — Módulo de Servicios y Cargas (Frontend / IndexedDB)", () => {
  beforeEach(async () => {
    resetDBInstance();
    activarModoDemo();
  });

  // 1. Navegación a Servicios desde el Header
  it("1. Permite alternar la navegación hacia el módulo de Servicios mediante el Header", async () => {
    render(<App />);

    // Inicia en Caja
    expect(screen.getByTestId("search-input")).toBeInTheDocument();
    expect(screen.queryByTestId("services-container")).not.toBeInTheDocument();

    // Clic en pestaña '⚡ Servicios'
    const btnNavServicios = screen.getByTestId("nav-btn-servicios");
    fireEvent.click(btnNavServicios);

    // Muestra la vista de Servicios
    expect(screen.getByTestId("services-container")).toBeInTheDocument();
    expect(screen.getByTestId("services-form-card")).toBeInTheDocument();
    expect(screen.getByTestId("services-kpi-summary")).toBeInTheDocument();
    expect(screen.queryByTestId("search-input")).not.toBeInTheDocument();

    // Regresar a Caja
    fireEvent.click(screen.getByTestId("nav-btn-caja"));
    expect(screen.getByTestId("search-input")).toBeInTheDocument();
  });

  // 2. Cálculo automático de recargos según reglas legacy
  it("2. Calcula automáticamente el adicional según la regla escalonada ($100 cada $2000)", () => {
    expect(calcularRecargoAutomatico(0)).toBe(0);
    expect(calcularRecargoAutomatico(500)).toBe(100);
    expect(calcularRecargoAutomatico(2000)).toBe(100);
    expect(calcularRecargoAutomatico(2500)).toBe(200);
    expect(calcularRecargoAutomatico(4000)).toBe(200);
    expect(calcularRecargoAutomatico(4500)).toBe(300);
    expect(calcularRecargoAutomatico(10000)).toBe(500);
  });

  // 3. Registro válido de carga (Efectivo) y reflejo en historial y totales
  it("3. Registra una carga válida en efectivo y actualiza los totales y el historial", async () => {
    render(<App />);
    fireEvent.click(screen.getByTestId("nav-btn-servicios"));

    await waitFor(() => {
      expect(screen.getByTestId("btn-register-charge")).toBeInTheDocument();
    });

    // Seleccionar plataforma y servicio destino
    const selectPlataforma = screen.getByTestId("select-plataforma-origen");
    fireEvent.change(selectPlataforma, { target: { value: "PREPAY" } });
    const selectDestino = screen.getByTestId("select-servicio-destino");
    fireEvent.change(selectDestino, { target: { value: "Movistar" } });

    const inputMonto = screen.getByTestId("input-service-amount");
    fireEvent.change(inputMonto, { target: { value: "3000" } });

    // Recargo automático para 3000 = 200 -> Total a cobrar = 3200
    expect(screen.getByTestId("charge-total-banner")).toHaveTextContent("$3.200");

    // Registrar carga
    fireEvent.click(screen.getByTestId("btn-register-charge"));

    // Debe persistir Movistar en IndexedDB
    let movistar: any;
    await waitFor(async () => {
      const cargas = await listarServiciosDemo(false);
      movistar = cargas.find((c) => c.servicio === "Movistar");
      expect(movistar).toBeDefined();
    });
    expect(movistar?.monto_carga).toBe(3000);
    expect(movistar?.monto_adicional).toBe(200);
    expect(movistar?.total_cobrado).toBe(3200);
    expect(movistar?.pago_efvo).toBe(3200);
    expect(movistar?.pago_transf).toBe(0);
  });

  // 4. Registro con pago MIXTO (Transferencia + Efectivo)
  it("4. Admite registrar una carga con pago mixto distribuyendo montos correctamente", async () => {
    render(<App />);
    fireEvent.click(screen.getByTestId("nav-btn-servicios"));

    await waitFor(() => {
      expect(screen.getByTestId("btn-register-charge")).toBeInTheDocument();
    });

    // Claro por 5000 + 300 recargo = 5300
    const selectPlataforma = screen.getByTestId("select-plataforma-origen");
    fireEvent.change(selectPlataforma, { target: { value: "CLARO COMERCIOS" } });
    const selectDestino = screen.getByTestId("select-servicio-destino");
    fireEvent.change(selectDestino, { target: { value: "Claro" } });

    const inputMonto = screen.getByTestId("input-service-amount");
    fireEvent.change(inputMonto, { target: { value: "5000" } });

    // Seleccionar pago Mixto
    fireEvent.click(screen.getByTestId("radio-payment-mixto"));
    expect(screen.getByTestId("split-payment-box")).toBeInTheDocument();

    // 2000 por transferencia, restante 3300 en efectivo
    const inputSplitTransf = screen.getByTestId("input-split-transf");
    fireEvent.change(inputSplitTransf, { target: { value: "2000" } });

    expect(screen.getByText(/Restante en Efectivo:/)).toHaveTextContent("$3.300");

    fireEvent.click(screen.getByTestId("btn-register-charge"));

    await waitFor(async () => {
      const cargas = await listarServiciosDemo(false);
      const claro = cargas.find((c) => c.monto_carga === 5000 && c.servicio === "Claro");
      expect(claro).toBeDefined();
      expect(claro?.pago_transf).toBe(2000);
      expect(claro?.pago_efvo).toBe(3300);
      expect(claro?.total_cobrado).toBe(5300);
    });
  });

  // 5. Validación de montos inválidos en el formulario
  it("5. Rechaza el registro si el monto de la carga es menor o igual a cero", async () => {
    render(<App />);
    fireEvent.click(screen.getByTestId("nav-btn-servicios"));

    await waitFor(() => {
      expect(screen.getByTestId("btn-register-charge")).toBeInTheDocument();
    });

    // El botón debe estar deshabilitado con monto 0
    const btn = screen.getByTestId("btn-register-charge");
    expect(btn).toBeDisabled();
  });

  // 6. Búsqueda y filtrado de historial
  it("6. Permite buscar operaciones por nombre de servicio y filtrar por medio de pago", async () => {
    render(<App />);
    fireEvent.click(screen.getByTestId("nav-btn-servicios"));

    // Esperar a que carguen las operaciones iniciales sembradas
    await waitFor(() => {
      expect(screen.getAllByText("Personal").length).toBeGreaterThan(0);
    });

    // Búsqueda por texto: "SUBE"
    const searchInput = screen.getByTestId("services-search-input");
    await userEvent.type(searchInput, "Personal");

    expect(screen.getAllByText("Personal").length).toBeGreaterThan(0);
    const historyTable = screen.getByRole("table");
    expect(within(historyTable).queryByText("Claro")).toBeNull();

    // Limpiar búsqueda
    await userEvent.clear(searchInput);

    // Filtrar solo transferencias
    const paymentFilter = screen.getByTestId("services-payment-filter");
    fireEvent.change(paymentFilter, { target: { value: "TRANSFERENCIA" } });

    // Personal fue cargada con transferencia en los datos iniciales
    expect(screen.getAllByText("Personal").length).toBeGreaterThan(0);
  });

  // 7. Edición puntual de una carga sin alterar las demás
  it("7. Permite editar el servicio y montos de una carga puntual conservando el resto intacto", async () => {
    render(<App />);
    fireEvent.click(screen.getByTestId("nav-btn-servicios"));

    await waitFor(() => {
      expect(screen.getByTestId("btn-edit-charge-1")).toBeInTheDocument();
    });

    // Abrir edición de la carga #1
    fireEvent.click(screen.getByTestId("btn-edit-charge-1"));
    expect(screen.getByTestId("modal-edit-charge")).toBeInTheDocument();

    // Modificar monto a 3500 y recargo a 250
    const inputMonto = screen.getByTestId("input-edit-amount");
    const inputAdic = screen.getByTestId("input-edit-surcharge");
    fireEvent.change(inputMonto, { target: { value: "3500" } });
    fireEvent.change(inputAdic, { target: { value: "250" } });

    fireEvent.click(screen.getByTestId("btn-save-charge-edit"));

    await waitFor(() => {
      expect(screen.queryByTestId("modal-edit-charge")).not.toBeInTheDocument();
    });

    const cargas = await listarServiciosDemo(false);
    const c1 = cargas.find((c) => c.id === 1);
    expect(c1?.monto_carga).toBe(3500);
    expect(c1?.monto_adicional).toBe(250);
    expect(c1?.total_cobrado).toBe(3750);

    // Carga #2 (SUBE $2.000) debe seguir idéntica
    const c2 = cargas.find((c) => c.id === 2);
    expect(c2?.monto_carga).toBe(2000);
  });

  // 8. Anulación lógica con explicación contable
  it("8. Anula lógicamente una carga previa con confirmación explícita y la excluye de los totales", async () => {
    render(<App />);
    fireEvent.click(screen.getByTestId("nav-btn-servicios"));

    await waitFor(() => {
      expect(screen.getByTestId("btn-cancel-charge-2")).toBeInTheDocument();
    });

    // Abrir modal de anulación para carga #2 (SUBE $2.000)
    fireEvent.click(screen.getByTestId("btn-cancel-charge-2"));
    expect(screen.getByTestId("modal-confirm-cancel-charge")).toBeInTheDocument();
    expect(screen.getByText(/Esta anulación es/)).toBeInTheDocument();

    // Confirmar anulación
    fireEvent.click(screen.getByTestId("btn-confirm-cancel"));

    await waitFor(() => {
      expect(screen.queryByTestId("modal-confirm-cancel-charge")).not.toBeInTheDocument();
      expect(screen.queryByTestId("btn-cancel-charge-2")).not.toBeInTheDocument();
    });

    // Activar 'Mostrar Anuladas'
    fireEvent.click(screen.getByTestId("services-show-cancelled"));
    await waitFor(() => {
      expect(screen.getAllByText("Anulada").length).toBeGreaterThan(0);
    });

    // En IndexedDB el registro sigue existiendo con anulado = true
    const todas = await listarServiciosDemo(true);
    const c2 = todas.find((c) => c.id === 2);
    expect(c2).toBeDefined();
    expect(c2?.anulado).toBe(true);
  });

  // 9. Resumen contable segrega PREPAY, Claro y SUBE MP
  it("9. Calcula la segregación contable de capital para Prepay, Claro y SUBE MP", async () => {
    const resumen = await calcularResumenServiciosDemo();
    expect(resumen.total_operaciones).toBeGreaterThan(0);
    expect(resumen.capital_claro).toBeGreaterThan(0);
    expect(resumen.capital_sube_mp).toBeGreaterThan(0);
    expect(resumen.capital_prepay).toBeGreaterThan(0);
  });

  // 10. Persistencia local e independencia de stores en IndexedDB v3
  it("10. La tienda servicios_demo no altera las ventas locales ni la caché de catálogo", async () => {
    // 1. Guardar una venta local
    await guardarVentaLocal({
      ticket_id: "T-SERV-TEST",
      payload: {
        ticket: {
          ticket_id: "T-SERV-TEST",
          fecha: "24/10/2026 15:00:00",
          total_venta: 1000,
          monto_efectivo: 1000,
          monto_transf: 0,
          metodo_pago: "EFECTIVO",
        },
        items: [],
      },
      estado: "pendiente",
      intentos: 0,
      creado_en: new Date().toISOString(),
    });

    // 2. Guardar catálogo cache
    await guardarCatalogoCache([
      {
        id_producto: 99,
        nombre: "Test Prod",
        categoria: "Varios",
        proveedor: "Local",
        unidad: "Unidad",
        costo: 100,
        margen_porcentaje: 0.5,
        precio_venta: 150,
        precio_dia: 150,
        activo: true,
        fecha_act: "2026-10-24",
      },
    ]);

    // 3. Guardar una carga en servicios_demo
    await guardarCargaDemo({
      servicio: "Tuenti",
      monto_carga: 1500,
      monto_adicional: 100,
      total_cobrado: 1600,
      pago_efvo: 1600,
      pago_transf: 0,
    });

    // 4. Verificar integridad de los stores independientes
    const ventas = await listarVentasLocales();
    expect(ventas.some((v) => v.ticket_id === "T-SERV-TEST")).toBe(true);

    const cat = await obtenerCatalogoCache();
    expect(cat.productos.some((p) => p.id_producto === 99)).toBe(true);

    const cargas = await listarServiciosDemo(true);
    expect(cargas.some((c) => c.servicio === "Tuenti")).toBe(true);
  });

  // 11. Apertura de base preserva todos los Object Stores existentes
  it("11. La base IndexedDB v3/v4 preserva íntegras todas las tiendas previas y añade servicios_demo", async () => {
    const db = await openDB();
    expect(db.version).toBeGreaterThanOrEqual(3);
    expect(db.objectStoreNames.contains("ventas_queue")).toBe(true);
    expect(db.objectStoreNames.contains("catalogo_cache")).toBe(true);
    expect(db.objectStoreNames.contains("catalogo_demo")).toBe(true);
    expect(db.objectStoreNames.contains("meta")).toBe(true);
    expect(db.objectStoreNames.contains("servicios_demo")).toBe(true);
  });

  // 12. Modo Demo no realiza llamadas fetch de red
  it("12. Las operaciones de servicios en Modo Demo son 100% locales y no realizan fetch", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    await guardarCargaDemo({
      servicio: "Movistar",
      monto_carga: 2000,
      monto_adicional: 100,
      total_cobrado: 2100,
      pago_efvo: 2100,
      pago_transf: 0,
    });

    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });
});
