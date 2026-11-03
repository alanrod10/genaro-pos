import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "../src/App";
import {
  resetDBInstance,
  guardarVentaDemo,
  obtenerVentaDemo,
  listarVentasHistorialDemo,
  obtenerDetalleTicketDemo,
  anularVentaDemo,
  editarItemVentaDemo,
} from "../src/services/db";
import { activarModoDemo, getApiClient } from "../src/services/api";

describe("GenaroPOS — Módulo de Historial y Auditoría de Ventas (Frontend / IndexedDB)", () => {
  beforeEach(async () => {
    resetDBInstance();
    activarModoDemo();
  });

  // 1. Navegación hacia Historial desde el Header
  it("1. Permite alternar la navegación hacia el módulo de Historial mediante el Header", async () => {
    render(<App />);

    // Inicia en Caja
    expect(screen.getByTestId("search-input")).toBeInTheDocument();
    expect(screen.queryByTestId("sales-history-container")).not.toBeInTheDocument();

    // Clic en pestaña '📜 Historial'
    const btnNavHistorial = screen.getByTestId("nav-btn-historial");
    fireEvent.click(btnNavHistorial);

    // Muestra la vista de Historial
    expect(screen.getByTestId("sales-history-container")).toBeInTheDocument();
    expect(screen.getByTestId("sales-filters-card")).toBeInTheDocument();
    expect(screen.getByTestId("sales-kpi-summary")).toBeInTheDocument();
    expect(screen.queryByTestId("search-input")).not.toBeInTheDocument();

    // Regresar a Caja
    fireEvent.click(screen.getByTestId("nav-btn-caja"));
    expect(screen.getByTestId("search-input")).toBeInTheDocument();
  });

  // 2. Mostrar detalle de un ticket con sus ítems
  it("2. Permite abrir el detalle de una venta y visualizar sus artículos y desglose", async () => {
    await guardarVentaDemo({
      ticket_id: "T-TEST-DETALLE-1",
      creado_en: "2026-10-24T10:00:00Z",
      estado: "sincronizada",
      intentos: 0,
      anulado: false,
      payload: {
        ticket: {
          ticket_id: "T-TEST-DETALLE-1",
          fecha: "24/10/2026 10:00:00",
          total_venta: 4500,
          monto_efectivo: 4500,
          monto_transf: 0,
          metodo_pago: "EFECTIVO",
          anulado: false,
        },
        items: [
          {
            ticket_id: "T-TEST-DETALLE-1",
            producto: "Alfajor de Dulce de Leche",
            cantidad: 3,
            unidad: "Unidad",
            precio_unit: 1500,
            subtotal: 4500,
            metodo_pago: "EFECTIVO",
          },
        ],
      },
    });

    render(<App />);
    fireEvent.click(screen.getByTestId("nav-btn-historial"));

    // Esperar a que cargue la tabla y hacer clic en Detalle
    await waitFor(() => {
      expect(screen.getByTestId("btn-view-ticket-T-TEST-DETALLE-1")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("btn-view-ticket-T-TEST-DETALLE-1"));

    // Modal de detalle visible
    await waitFor(() => {
      expect(screen.getByTestId("modal-ticket-detail")).toBeInTheDocument();
      expect(screen.getByTestId("detail-ticket-total")).toHaveTextContent("$4.500");
      expect(screen.getByTestId("detail-ticket-payment")).toHaveTextContent("EFECTIVO");
      expect(screen.getByText("Alfajor de Dulce de Leche")).toBeInTheDocument();
    });

    // Cerrar modal
    fireEvent.click(screen.getByTestId("btn-close-ticket-detail"));
    await waitFor(() => {
      expect(screen.queryByTestId("modal-ticket-detail")).not.toBeInTheDocument();
    });
  });

  // 3. Búsqueda por identificador de ticket y por nombre de producto
  it("3. Filtra la lista por ID de ticket y por producto contenido en la venta", async () => {
    await guardarVentaDemo({
      ticket_id: "T-SEARCH-ALFAJOR",
      creado_en: "2026-10-24T12:00:00Z",
      estado: "sincronizada",
      intentos: 0,
      anulado: false,
      payload: {
        ticket: {
          ticket_id: "T-SEARCH-ALFAJOR",
          fecha: "24/10/2026 12:00:00",
          total_venta: 2000,
          monto_efectivo: 2000,
          monto_transf: 0,
          metodo_pago: "EFECTIVO",
          anulado: false,
        },
        items: [
          {
            ticket_id: "T-SEARCH-ALFAJOR",
            producto: "Turrón Maní Crunch",
            cantidad: 2,
            unidad: "Unidad",
            precio_unit: 1000,
            subtotal: 2000,
          },
        ],
      },
    });

    render(<App />);
    fireEvent.click(screen.getByTestId("nav-btn-historial"));

    await waitFor(() => {
      expect(screen.getByTestId("input-search-sales")).toBeInTheDocument();
    });

    const inputSearch = screen.getByTestId("input-search-sales");

    // Búsqueda por producto 'Turrón'
    await userEvent.type(inputSearch, "Turrón");
    await waitFor(() => {
      expect(screen.getAllByText("T-SEARCH-ALFAJOR").length).toBeGreaterThan(0);
    });

    // Limpiar búsqueda
    await userEvent.clear(inputSearch);

    // Búsqueda por ID
    await userEvent.type(inputSearch, "SEARCH-ALFAJOR");
    await waitFor(() => {
      expect(screen.getAllByText("T-SEARCH-ALFAJOR").length).toBeGreaterThan(0);
    });
  });

  // 4. Filtrado por medio de pago
  it("4. Filtra ventas por medio de pago (Efectivo vs Transferencia)", async () => {
    await guardarVentaDemo({
      ticket_id: "T-PAY-EFVO",
      creado_en: "2026-10-24T14:00:00Z",
      estado: "sincronizada",
      intentos: 0,
      anulado: false,
      payload: {
        ticket: {
          ticket_id: "T-PAY-EFVO",
          fecha: "24/10/2026 14:00:00",
          total_venta: 1000,
          monto_efectivo: 1000,
          monto_transf: 0,
          metodo_pago: "EFECTIVO",
          anulado: false,
        },
        items: [
          {
            producto: "Agua",
            cantidad: 1,
            unidad: "Unidad",
            precio_unit: 1000,
            subtotal: 1000,
          },
        ],
      },
    });

    await guardarVentaDemo({
      ticket_id: "T-PAY-TRANSF",
      creado_en: "2026-10-24T14:05:00Z",
      estado: "sincronizada",
      intentos: 0,
      anulado: false,
      payload: {
        ticket: {
          ticket_id: "T-PAY-TRANSF",
          fecha: "24/10/2026 14:05:00",
          total_venta: 2000,
          monto_efectivo: 0,
          monto_transf: 2000,
          metodo_pago: "TRANSFERENCIA",
          anulado: false,
        },
        items: [
          {
            producto: "Jugo",
            cantidad: 1,
            unidad: "Unidad",
            precio_unit: 2000,
            subtotal: 2000,
          },
        ],
      },
    });

    render(<App />);
    fireEvent.click(screen.getByTestId("nav-btn-historial"));

    await waitFor(() => {
      expect(screen.getByTestId("select-filter-payment")).toBeInTheDocument();
    });

    // Filtrar solo TRANSFERENCIA
    fireEvent.change(screen.getByTestId("select-filter-payment"), {
      target: { value: "TRANSFERENCIA" },
    });

    await waitFor(() => {
      expect(screen.getAllByText("T-PAY-TRANSF").length).toBeGreaterThan(0);
      expect(screen.queryByText("T-PAY-EFVO")).not.toBeInTheDocument();
    });
  });

  // 5. Preservación estricta de importes históricos
  it("5. Mantiene los importes y precios históricos del ticket intactos", async () => {
    // Venta guardada con precio histórico $750 por alfajor
    await guardarVentaDemo({
      ticket_id: "T-HISTORIC-PRICE",
      creado_en: "2026-10-24T15:00:00Z",
      estado: "sincronizada",
      intentos: 0,
      anulado: false,
      payload: {
        ticket: {
          ticket_id: "T-HISTORIC-PRICE",
          fecha: "24/10/2026 15:00:00",
          total_venta: 1500,
          monto_efectivo: 1500,
          monto_transf: 0,
          metodo_pago: "EFECTIVO",
          anulado: false,
        },
        items: [
          {
            producto: "Alfajor Histórico",
            cantidad: 2,
            unidad: "Unidad",
            precio_unit: 750,
            subtotal: 1500,
          },
        ],
      },
    });

    const detalle = await obtenerDetalleTicketDemo("T-HISTORIC-PRICE");
    expect(detalle?.total_venta).toBe(1500);
    expect(detalle?.items[0].precio_unit).toBe(750);
    expect(detalle?.items[0].subtotal).toBe(1500);
  });

  // 6. Anulación lógica de venta sin eliminación física
  it("6. Anula lógicamente un ticket con motivo, preservándolo en la base y excluyéndolo del arqueo", async () => {
    await guardarVentaDemo({
      ticket_id: "T-PARA-ANULAR",
      creado_en: "2026-10-24T16:00:00Z",
      estado: "sincronizada",
      intentos: 0,
      anulado: false,
      payload: {
        ticket: {
          ticket_id: "T-PARA-ANULAR",
          fecha: "24/10/2026 16:00:00",
          total_venta: 6000,
          monto_efectivo: 6000,
          monto_transf: 0,
          metodo_pago: "EFECTIVO",
          anulado: false,
        },
        items: [
          {
            producto: "Chocolates Varios",
            cantidad: 3,
            unidad: "Unidad",
            precio_unit: 2000,
            subtotal: 6000,
          },
        ],
      },
    });

    render(<App />);
    fireEvent.click(screen.getByTestId("nav-btn-historial"));

    await waitFor(() => {
      expect(screen.getByTestId("btn-cancel-ticket-T-PARA-ANULAR")).toBeInTheDocument();
    });

    // Clic en anular abre modal con advertencia
    fireEvent.click(screen.getByTestId("btn-cancel-ticket-T-PARA-ANULAR"));
    expect(screen.getByTestId("modal-confirm-cancel-sale")).toBeInTheDocument();

    const inputMotivo = screen.getByTestId("input-cancel-sale-reason");
    await userEvent.type(inputMotivo, "Cliente solicitó reembolso");

    fireEvent.click(screen.getByTestId("btn-confirm-cancel-sale"));

    // Verificar en IndexedDB que sigue existiendo pero con anulado = true
    await waitFor(async () => {
      const v = await obtenerVentaDemo("T-PARA-ANULAR");
      expect(v).toBeDefined();
      expect(v?.anulado).toBe(true);
      expect(v?.motivo_anulacion).toBe("Cliente solicitó reembolso");
    });
  });

  // 7. Corrección de ítem individual y recálculo con regla contable legacy
  it("7. Permite corregir un ítem recalculando automáticamente el total del ticket y su desglose", async () => {
    // Ticket de $5.000: $3.000 en Efectivo y $2.000 en Transf (MIXTO)
    // Ítem 0: 2 x $1.500 = $3.000
    // Ítem 1: 1 x $2.000 = $2.000
    await guardarVentaDemo({
      ticket_id: "T-TEST-EDIT-ITEM",
      creado_en: "2026-10-24T17:00:00Z",
      estado: "sincronizada",
      intentos: 0,
      anulado: false,
      payload: {
        ticket: {
          ticket_id: "T-TEST-EDIT-ITEM",
          fecha: "24/10/2026 17:00:00",
          total_venta: 5000,
          monto_efectivo: 3000,
          monto_transf: 2000,
          metodo_pago: "MIXTO",
          anulado: false,
        },
        items: [
          {
            producto: "Galletitas Surtidas",
            cantidad: 2,
            unidad: "Unidad",
            precio_unit: 1500,
            subtotal: 3000,
          },
          {
            producto: "Bombones",
            cantidad: 1,
            unidad: "Unidad",
            precio_unit: 2000,
            subtotal: 2000,
          },
        ],
      },
    });

    // Modificar ítem 0: cantidad pasa a 1 y subtotal pasa a $1.500 (baja $1.500)
    const updated = await editarItemVentaDemo("T-TEST-EDIT-ITEM", 0, 1, 1500, "Alan");

    // Nuevo total debe ser $3.500 ($1.500 + $2.000)
    expect(updated.total_venta).toBe(3500);
    // Siguiendo la regla legacy, la disminución de $1.500 se descuenta del efectivo:
    // new_efvo = 3000 - 1500 = 1500; new_transf = 2000
    expect(updated.monto_efectivo).toBe(1500);
    expect(updated.monto_transferencia).toBe(2000);
    expect(updated.metodo_pago).toBe("MIXTO");
    expect(updated.auditoria?.some((a) => a.accion === "EDICION_ITEM")).toBe(true);
  });

  // 8. Separación entre ventas activas y anuladas con filtro
  it("8. El filtro de mostrar anuladas oculta o incluye tickets cancelados según la selección", async () => {
    await guardarVentaDemo({
      ticket_id: "T-ACTIVE-ONLY",
      creado_en: "2026-10-24T18:00:00Z",
      estado: "sincronizada",
      intentos: 0,
      anulado: false,
      payload: {
        ticket: {
          ticket_id: "T-ACTIVE-ONLY",
          fecha: "24/10/2026 18:00:00",
          total_venta: 1000,
          monto_efectivo: 1000,
          monto_transf: 0,
          metodo_pago: "EFECTIVO",
          anulado: false,
        },
        items: [{ producto: "Caramelo", cantidad: 1, unidad: "Unidad", precio_unit: 1000, subtotal: 1000 }],
      },
    });

    await guardarVentaDemo({
      ticket_id: "T-CANCELLED-ONLY",
      creado_en: "2026-10-24T18:05:00Z",
      estado: "sincronizada",
      intentos: 0,
      anulado: true,
      motivo_anulacion: "Cancelada por prueba",
      payload: {
        ticket: {
          ticket_id: "T-CANCELLED-ONLY",
          fecha: "24/10/2026 18:05:00",
          total_venta: 1000,
          monto_efectivo: 1000,
          monto_transf: 0,
          metodo_pago: "EFECTIVO",
          anulado: true,
        },
        items: [{ producto: "Caramelo", cantidad: 1, unidad: "Unidad", precio_unit: 1000, subtotal: 1000 }],
      },
    });

    // Sin incluir anuladas
    const soloActivas = await listarVentasHistorialDemo({ incluir_anuladas: false });
    expect(soloActivas.some((v) => v.ticket_id === "T-ACTIVE-ONLY")).toBe(true);
    expect(soloActivas.some((v) => v.ticket_id === "T-CANCELLED-ONLY")).toBe(false);

    // Con anuladas
    const todas = await listarVentasHistorialDemo({ incluir_anuladas: true });
    expect(todas.some((v) => v.ticket_id === "T-CANCELLED-ONLY")).toBe(true);
  });

  // 9. Persistencia de ventas y auditoría en IndexedDB tras reiniciar sesión
  it("9. Las ventas persisten íntegramente tras resetear la conexión a la base de datos", async () => {
    await guardarVentaDemo({
      ticket_id: "T-PERSISTENT",
      creado_en: "2026-10-24T19:00:00Z",
      estado: "sincronizada",
      intentos: 0,
      anulado: false,
      payload: {
        ticket: {
          ticket_id: "T-PERSISTENT",
          fecha: "24/10/2026 19:00:00",
          total_venta: 3000,
          monto_efectivo: 3000,
          monto_transf: 0,
          metodo_pago: "EFECTIVO",
          anulado: false,
        },
        items: [{ producto: "Alfajor Glaseado", cantidad: 2, unidad: "Unidad", precio_unit: 1500, subtotal: 3000 }],
      },
    });

    // Reset de conexión simulando recarga
    resetDBInstance();

    const recuperada = await obtenerVentaDemo("T-PERSISTENT");
    expect(recuperada).toBeDefined();
    expect(recuperada?.payload.ticket.total_venta).toBe(3000);
  });

  // 10. Aislamiento en modo demo (0 llamadas fetch de red)
  it("10. Las operaciones de historial y anulación en modo Demo son 100% locales y no realizan fetch", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    await guardarVentaDemo({
      ticket_id: "T-LOCAL-FETCH-TEST",
      creado_en: "2026-10-24T20:00:00Z",
      estado: "sincronizada",
      intentos: 0,
      anulado: false,
      payload: {
        ticket: {
          ticket_id: "T-LOCAL-FETCH-TEST",
          fecha: "24/10/2026 20:00:00",
          total_venta: 1000,
          monto_efectivo: 1000,
          monto_transf: 0,
          metodo_pago: "EFECTIVO",
          anulado: false,
        },
        items: [],
      },
    });

    await listarVentasHistorialDemo();
    await anularVentaDemo("T-LOCAL-FETCH-TEST", "Prueba sin red");

    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  // 11. Manejo seguro de errores ante tickets inexistentes
  it("11. Retorna error controlado y no lanza excepciones ante IDs inexistentes", async () => {
    const api = getApiClient();

    const resDetalle = await api.obtenerDetalleVenta("T-NO-EXISTE-XYZ");
    expect(resDetalle.status).toBe(404);
    expect(resDetalle.error).toBeDefined();

    const resAnular = await api.anularVenta("T-NO-EXISTE-XYZ", "Error");
    expect(resAnular.status).toBe(400);
    expect(resAnular.error).toBeDefined();
  });

  // 12. Regresión P0-02: Modo conectado (RealApiClient) normaliza producto_nombre y precio_unitario sin TypeError
  it("12. En modo conectado (RealApiClient), obtenerDetalleVenta, editarItemVenta y anularVenta normalizan producto_nombre y precio_unitario sin errores en UI", async () => {
    const { RealApiClient, setApiClient } = await import("../src/services/api");
    const { SalesHistoryAdmin } = await import("../src/components/SalesHistoryAdmin");

    const backendTicketInitial = {
      ticket_id: "T-REAL-API-001",
      fecha: "25/10/2026 14:30:00",
      fecha_hora_utc: "2026-10-25T17:30:00Z",
      total_venta: 2400,
      monto_efectivo: 2400,
      monto_transferencia: 0,
      metodo_pago: "EFECTIVO",
      anulado: false,
      cantidad_items: 2,
      resumen_items: "2x Coca Cola 500ml",
      items: [
        {
          id: 55,
          producto_id: 1,
          producto_nombre: "Coca Cola 500ml",
          cantidad: 2,
          unidad: "Unidad",
          precio_unitario: 1200,
          subtotal: 2400,
          metodo_pago: "EFECTIVO",
        },
      ],
      auditoria: [],
    };

    const backendTicketEdited = {
      ...backendTicketInitial,
      total_venta: 3600,
      monto_efectivo: 3600,
      items: [
        {
          id: 55,
          producto_id: 1,
          producto_nombre: "Coca Cola 500ml",
          cantidad: 3,
          unidad: "Unidad",
          precio_unitario: 1200,
          subtotal: 3600,
          metodo_pago: "EFECTIVO",
        },
      ],
      auditoria: [
        {
          id: 1,
          ticket_id: "T-REAL-API-001",
          accion: "EDICION_ITEM",
          usuario: "alan",
          motivo: null,
          creado_en_utc: "2026-10-25T17:35:00Z",
        },
      ],
    };

    const backendTicketCancelled = {
      ...backendTicketEdited,
      anulado: true,
      motivo_anulacion: "Error de cobro en modo real",
    };

    const mockFetch = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      const method = (init?.method || "GET").toUpperCase();
      if (url.includes("/api/sales?") && method === "GET") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            total: 1,
            ventas: [backendTicketInitial],
          }),
        });
      }
      if (url.endsWith("/api/sales/T-REAL-API-001") && method === "GET") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => backendTicketInitial,
        });
      }
      if (url.endsWith("/api/sales/T-REAL-API-001/items/55") && method === "PUT") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => backendTicketEdited,
        });
      }
      if (url.endsWith("/api/sales/T-REAL-API-001/anular") && method === "POST") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => backendTicketCancelled,
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => ({}) });
    });

    vi.stubGlobal("fetch", mockFetch);

    const realClient = new RealApiClient("http://127.0.0.1:8000", false);
    realClient.setAuthToken("jwt_test_token");
    setApiClient(realClient);

    const onAddToast = vi.fn();
    render(<SalesHistoryAdmin onAddToast={onAddToast} isDemo={false} />);

    // Esperar a que cargue la lista y abrir el modal de detalle
    await waitFor(() => {
      expect(screen.getByTestId("btn-view-ticket-T-REAL-API-001")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("btn-view-ticket-T-REAL-API-001"));

    // Debe renderizar el modal con producto_nombre y precio_unitario normalizados sin lanzar TypeError
    await waitFor(() => {
      expect(screen.getByTestId("modal-ticket-detail")).toBeInTheDocument();
      expect(screen.getByText("Coca Cola 500ml")).toBeInTheDocument();
      expect(screen.getByTestId("detail-ticket-total")).toHaveTextContent("$2.400");
    });

    // Probar edición de ítem en modo conectado
    fireEvent.click(screen.getByTestId("btn-edit-item-0"));
    const inputQty = screen.getByTestId("input-edit-item-qty-0");
    fireEvent.change(inputQty, { target: { value: "3" } });
    fireEvent.click(screen.getByTestId("btn-save-item-edit"));

    await waitFor(() => {
      expect(screen.getByTestId("detail-ticket-total")).toHaveTextContent("$3.600");
    });

    // Probar anulación desde el detalle en modo conectado
    fireEvent.click(screen.getByTestId("btn-open-cancel-ticket"));
    const inputReason = screen.getByTestId("input-cancel-sale-reason");
    fireEvent.change(inputReason, { target: { value: "Error de cobro en modo real" } });
    fireEvent.click(screen.getByTestId("btn-confirm-cancel-sale"));

    await waitFor(() => {
      expect(screen.getByTestId("ticket-cancelled-banner")).toHaveTextContent(
        "Error de cobro en modo real"
      );
    });

    vi.unstubAllGlobals();
    activarModoDemo();
  });
});
