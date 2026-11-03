import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "../src/App";
import {
  guardarVentaLocal,
  obtenerVentaLocal,
  listarVentasLocales,
  listarVentasDemo,
  resetDBInstance,
} from "../src/services/db";
import {
  MockApiClient,
  activarModoDemo,
  getApiClient,
} from "../src/services/api";
import { sincronizarCola, sincronizarVentaIndividual } from "../src/services/sync";
import { VentaLocal, VentaPayload } from "../src/types/sales";

describe("GenaroPOS Caja Web / PWA — Pruebas de Interfaz y Cola Local", () => {
  beforeEach(async () => {
    resetDBInstance();
    activarModoDemo();
  });

  // 1. Renderizado responsive y componentes principales
  it("1. Renderiza los componentes principales de Caja (Header, Catálogo y Carrito)", async () => {
    render(<App />);

    // Header y marca
    expect(screen.getByText("GenaroPOS")).toBeInTheDocument();
    expect(screen.getByTestId("toggle-demo")).toBeInTheDocument();
    expect(screen.getByTestId("network-status")).toBeInTheDocument();
    expect(screen.getByTestId("btn-open-queue")).toBeInTheDocument();

    // Catálogo y buscador
    expect(screen.getByTestId("search-input")).toBeInTheDocument();

    // Carrito vacío inicial
    await waitFor(() => {
      expect(screen.getByTestId("cart-empty")).toBeInTheDocument();
    });
  });

  // 2. Búsqueda, selección y modificación de cantidades
  it("2. Permite buscar productos, agregarlos al carrito y modificar cantidades", async () => {
    render(<App />);

    // Esperar a que cargue el catálogo demo
    await waitFor(() => {
      expect(screen.getByText("Gaseosa Cola 500ml")).toBeInTheDocument();
    });

    // Buscar "Alfajor"
    const searchInput = screen.getByTestId("search-input");
    await userEvent.type(searchInput, "Alfajor");

    // Verificar que solo aparece el Alfajor
    expect(screen.getByText("Alfajor Triple Chocolate")).toBeInTheDocument();
    expect(screen.queryByText("Agua Mineral 1.5L")).not.toBeInTheDocument();

    // Seleccionar Alfajor para agregar al carrito
    const prodCard = screen.getByTestId("prod-card-2");
    fireEvent.click(prodCard);

    // Debe aparecer en el carrito
    await waitFor(() => {
      expect(screen.getByTestId("cart-items-count")).toHaveTextContent("1 ítem");
    });
    expect(screen.getByTestId("cart-total-amount")).toHaveTextContent("$800");

    // Incrementar cantidad a 2 con el botón '+'
    const btnPlus = screen.getByTestId("btn-qty-plus-2");
    fireEvent.click(btnPlus);

    // El subtotal y total deben ser $1.600
    await waitFor(() => {
      expect(screen.getByTestId("cart-total-amount")).toHaveTextContent("$1.600");
    });
  });

  // 3. Cálculo del total y del desglose de medios de pago
  it("3. Calcula correctamente el total y valida los medios de pago (Efectivo, Transferencia, Mixto)", async () => {
    render(<App />);

    // Agregar producto de $1.200 (Gaseosa)
    await waitFor(() => {
      expect(screen.getByTestId("prod-card-1")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("prod-card-1"));

    // Abrir modal de cobro
    const btnCheckout = screen.getByTestId("btn-checkout");
    fireEvent.click(btnCheckout);

    // Verificar modal abierto
    expect(screen.getByTestId("payment-modal")).toBeInTheDocument();
    expect(screen.getByTestId("payment-total-banner")).toHaveTextContent("$1.200");

    // Modo Efectivo: cálculo de vuelto
    const inputEfvo = screen.getByTestId("input-efectivo-recibido");
    fireEvent.change(inputEfvo, { target: { value: "2000" } });
    expect(screen.getByTestId("change-amount")).toHaveTextContent("$800");

    // Modo Transferencia
    fireEvent.click(screen.getByTestId("tab-transferencia"));
    expect(screen.getByText(/Verificá la acreditación/i)).toBeInTheDocument();

    // Modo Mixto: validación estricta de balance
    fireEvent.click(screen.getByTestId("tab-mixto"));
    const inputMixtoEfvo = screen.getByTestId("input-mixto-efvo");
    const inputMixtoTransf = screen.getByTestId("input-mixto-transf");

    // Desbalance: 500 + 500 = 1000 (!= 1200)
    fireEvent.change(inputMixtoEfvo, { target: { value: "500" } });
    fireEvent.change(inputMixtoTransf, { target: { value: "500" } });
    expect(screen.getByTestId("split-balance-error")).toBeInTheDocument();
    expect(screen.getByTestId("btn-confirm-payment")).toBeDisabled();

    // Balance correcto: 700 + 500 = 1200
    fireEvent.change(inputMixtoEfvo, { target: { value: "700" } });
    expect(screen.queryByTestId("split-balance-error")).not.toBeInTheDocument();
    expect(screen.getByTestId("btn-confirm-payment")).not.toBeDisabled();
  });

  // 4. Persistencia y recuperación de la cola después de recargar
  it("4. Persiste la cola en IndexedDB y la recupera intacta tras reiniciar la instancia", async () => {
    const payload: VentaPayload = {
      ticket: {
        ticket_id: "T-RELOAD-001",
        fecha: "24/10/2026 15:00:00",
        total_venta: 2500,
        monto_efectivo: 2500,
        monto_transf: 0,
        metodo_pago: "EFECTIVO",
        es_nocturno: false,
      },
      items: [
        {
          ticket_id: "T-RELOAD-001",
          producto: "Gaseosa Cola 500ml",
          cantidad: 2,
          unidad: "Unidad",
          precio_unit: 1250,
          subtotal: 2500,
        },
      ],
    };

    const venta: VentaLocal = {
      ticket_id: "T-RELOAD-001",
      payload,
      estado: "pendiente",
      intentos: 0,
      creado_en: new Date().toISOString(),
    };

    // Guardar en IndexedDB
    await guardarVentaLocal(venta);

    // Simular recarga cerrando la conexión
    resetDBInstance();

    // Recuperar
    const recuperada = await obtenerVentaLocal("T-RELOAD-001");
    expect(recuperada).toBeDefined();
    expect(recuperada?.ticket_id).toBe("T-RELOAD-001");
    expect(recuperada?.estado).toBe("pendiente");
    expect(recuperada?.payload.ticket.total_venta).toBe(2500);
    expect(recuperada?.payload.items.length).toBe(1);
    expect(recuperada?.payload.items[0].producto).toBe("Gaseosa Cola 500ml");
  });

  // 5. Preparación de una venta sin conexión
  it("5. Permite preparar y confirmar una venta sin conexión guardándola como pendiente", async () => {
    render(<App />);

    // Agregar producto
    await waitFor(() => {
      expect(screen.getByTestId("prod-card-1")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("prod-card-1"));

    // Simular desconexión
    fireEvent(window, new Event("offline"));
    expect(screen.getByTestId("network-status")).toHaveTextContent("Desconectado");

    // Cobrar
    fireEvent.click(screen.getByTestId("btn-checkout"));
    fireEvent.click(screen.getByTestId("btn-confirm-payment"));

    // El carrito se limpia
    await waitFor(() => {
      expect(screen.getByTestId("cart-empty")).toBeInTheDocument();
    });

    // La cola local tiene una venta registrada
    const ventas = (await listarVentasDemo()).length > 0 ? await listarVentasDemo() : await listarVentasLocales();
    expect(ventas.length).toBeGreaterThanOrEqual(1);
    const ultima = ventas[0];
    expect(ultima.ticket_id).toMatch(/^T-\d+-[a-z0-9]+$/);
    expect(ultima.payload.ticket.total_venta).toBe(1200);
  });

  // 6. Reintento usando exactamente el mismo ticket_id y payload
  it("6. Sincroniza y reintenta utilizando exactamente el mismo ticket_id y payload estable", async () => {
    const mockApi = new MockApiClient();
    const spy = vi.spyOn(mockApi, "registrarVenta");

    const payload: VentaPayload = {
      ticket: {
        ticket_id: "T-SAME-ID-999",
        fecha: "24/10/2026 15:30:00",
        total_venta: 1000,
        monto_efectivo: 1000,
        monto_transf: 0,
        metodo_pago: "EFECTIVO",
        es_nocturno: false,
      },
      items: [
        {
          ticket_id: "T-SAME-ID-999",
          producto: "Agua Mineral 1.5L",
          cantidad: 1,
          unidad: "Unidad",
          precio_unit: 1000,
          subtotal: 1000,
        },
      ],
    };

    await guardarVentaLocal({
      ticket_id: "T-SAME-ID-999",
      payload,
      estado: "pendiente",
      intentos: 0,
      creado_en: new Date().toISOString(),
    });

    // Primer intento
    await sincronizarVentaIndividual("T-SAME-ID-999", mockApi);
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy.mock.calls[0][0].ticket.ticket_id).toBe("T-SAME-ID-999");

    // La venta se marcó como sincronizada
    const v1 = await obtenerVentaLocal("T-SAME-ID-999");
    expect(v1?.estado).toBe("sincronizada");

    // Si se reintenta (forzando estado pendiente), el ticket_id y payload deben ser idénticos
    await guardarVentaLocal({ ...v1!, estado: "pendiente" });
    await sincronizarVentaIndividual("T-SAME-ID-999", mockApi);
    expect(spy).toHaveBeenCalledTimes(2);
    expect(spy.mock.calls[1][0].ticket.ticket_id).toBe("T-SAME-ID-999");
    expect(spy.mock.calls[1][0]).toEqual(payload);
  });

  // 7. Transición de pendiente a sincronizada con respuesta HTTP 201
  it("7. Transiciona de pendiente a sincronizada con respuesta HTTP 201", async () => {
    const mockApi = new MockApiClient();
    mockApi.setSimulationMode("exito_201");

    const ticketId = "T-STATUS-201";
    await guardarVentaLocal({
      ticket_id: ticketId,
      payload: {
        ticket: {
          ticket_id: ticketId,
          fecha: "24/10/2026 15:00:00",
          total_venta: 800,
          monto_efectivo: 800,
          monto_transf: 0,
          metodo_pago: "EFECTIVO",
          es_nocturno: false,
        },
        items: [
          {
            ticket_id: ticketId,
            producto: "Alfajor Triple Chocolate",
            cantidad: 1,
            unidad: "Unidad",
            precio_unit: 800,
            subtotal: 800,
          },
        ],
      },
      estado: "pendiente",
      intentos: 0,
      creado_en: new Date().toISOString(),
    });

    const res = await sincronizarVentaIndividual(ticketId, mockApi);
    expect(res.estado).toBe("sincronizada");
    expect(res.codigo_http).toBe(201);

    const guardada = await obtenerVentaLocal(ticketId);
    expect(guardada?.estado).toBe("sincronizada");
    expect(guardada?.sincronizado_en).toBeDefined();
    expect(guardada?.codigo_http).toBe(201);
  });

  // 8. Recuperación con respuesta HTTP 200 idempotente
  it("8. Recupera venta con respuesta HTTP 200 idempotente y la marca sincronizada", async () => {
    const mockApi = new MockApiClient();
    mockApi.setSimulationMode("reintento_200");

    const ticketId = "T-IDEMP-200";
    await guardarVentaLocal({
      ticket_id: ticketId,
      payload: {
        ticket: {
          ticket_id: ticketId,
          fecha: "24/10/2026 15:00:00",
          total_venta: 800,
          monto_efectivo: 800,
          monto_transf: 0,
          metodo_pago: "EFECTIVO",
          es_nocturno: false,
        },
        items: [
          {
            ticket_id: ticketId,
            producto: "Alfajor Triple Chocolate",
            cantidad: 1,
            unidad: "Unidad",
            precio_unit: 800,
            subtotal: 800,
          },
        ],
      },
      estado: "pendiente",
      intentos: 1,
      creado_en: new Date().toISOString(),
    });

    const res = await sincronizarVentaIndividual(ticketId, mockApi);
    expect(res.estado).toBe("sincronizada");
    expect(res.codigo_http).toBe(200);

    const guardada = await obtenerVentaLocal(ticketId);
    expect(guardada?.estado).toBe("sincronizada");
    expect(guardada?.codigo_http).toBe(200);
  });

  // 9. Conservación de la cola ante timeout y error de red
  it("9. Conserva la venta en estado pendiente ante timeout o fallo de red", async () => {
    const mockApi = new MockApiClient();
    mockApi.setSimulationMode("timeout");

    const ticketId = "T-TIMEOUT-ERR";
    await guardarVentaLocal({
      ticket_id: ticketId,
      payload: {
        ticket: {
          ticket_id: ticketId,
          fecha: "24/10/2026 15:00:00",
          total_venta: 1000,
          monto_efectivo: 1000,
          monto_transf: 0,
          metodo_pago: "EFECTIVO",
          es_nocturno: false,
        },
        items: [
          {
            ticket_id: ticketId,
            producto: "Agua Mineral 1.5L",
            cantidad: 1,
            unidad: "Unidad",
            precio_unit: 1000,
            subtotal: 1000,
          },
        ],
      },
      estado: "pendiente",
      intentos: 0,
      creado_en: new Date().toISOString(),
    });

    const res = await sincronizarVentaIndividual(ticketId, mockApi);
    expect(res.estado).toBe("pendiente");

    const guardada = await obtenerVentaLocal(ticketId);
    expect(guardada?.estado).toBe("pendiente");
    expect(guardada?.intentos).toBe(1);
    expect(guardada?.ultimo_error).toContain("Timeout");
  });

  // 10. Tratamiento de HTTP 409 sin borrar ni alterar silenciosamente la venta
  it("10. Marca como 'requiere_revision' ante HTTP 409 sin eliminar la venta", async () => {
    const mockApi = new MockApiClient();
    mockApi.setSimulationMode("conflicto_409");

    const ticketId = "T-CONFLICT-409";
    await guardarVentaLocal({
      ticket_id: ticketId,
      payload: {
        ticket: {
          ticket_id: ticketId,
          fecha: "24/10/2026 15:00:00",
          total_venta: 1000,
          monto_efectivo: 1000,
          monto_transf: 0,
          metodo_pago: "EFECTIVO",
          es_nocturno: false,
        },
        items: [
          {
            ticket_id: ticketId,
            producto: "Agua Mineral 1.5L",
            cantidad: 1,
            unidad: "Unidad",
            precio_unit: 1000,
            subtotal: 1000,
          },
        ],
      },
      estado: "pendiente",
      intentos: 0,
      creado_en: new Date().toISOString(),
    });

    const res = await sincronizarVentaIndividual(ticketId, mockApi);
    expect(res.estado).toBe("requiere_revision");
    expect(res.codigo_http).toBe(409);

    // La venta NO fue eliminada y conserva su payload intacto
    const guardada = await obtenerVentaLocal(ticketId);
    expect(guardada).toBeDefined();
    expect(guardada?.estado).toBe("requiere_revision");
    expect(guardada?.codigo_http).toBe(409);
    expect(guardada?.ultimo_error).toContain("Conflicto");
    expect(guardada?.payload.ticket.total_venta).toBe(1000);
  });

  // 11. Ausencia de llamadas de escritura a la API real durante el modo de demostración
  it("11. Confirma que el Modo Demo seguro por defecto no realiza escrituras de red", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    const client = getApiClient();
    expect(client.isDemo()).toBe(true);

    const payload: VentaPayload = {
      ticket: {
        ticket_id: "T-DEMO-CHECK",
        fecha: "24/10/2026 16:00:00",
        total_venta: 1200,
        monto_efectivo: 1200,
        monto_transf: 0,
        metodo_pago: "EFECTIVO",
        es_nocturno: false,
      },
      items: [
        {
          ticket_id: "T-DEMO-CHECK",
          producto: "Gaseosa Cola 500ml",
          cantidad: 1,
          unidad: "Unidad",
          precio_unit: 1200,
          subtotal: 1200,
        },
      ],
    };

    const resp = await client.registrarVenta(payload);
    expect(resp.status).toBe(201);
    expect(resp.data?.mensaje).toContain("Modo Demo Seguro");

    // Verificar que fetch jamás fue invocado
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  // 12. Conservación exacta de importes y cantidades almacenados
  it("12. Preserva fielmente cantidades fraccionarias e importes numéricos en el almacenamiento", async () => {
    const ticketId = "T-EXACT-DECIMAL";
    const payload: VentaPayload = {
      ticket: {
        ticket_id: ticketId,
        fecha: "24/10/2026 17:00:00",
        total_venta: 1875,
        monto_efectivo: 1000,
        monto_transf: 875,
        metodo_pago: "MIXTO",
        es_nocturno: true,
      },
      items: [
        {
          ticket_id: ticketId,
          producto: "Caramelos surtidos x 100g",
          cantidad: 2.5, // 2.5 * 750 = 1875
          unidad: "100g",
          precio_unit: 750,
          subtotal: 1875,
          metodo_pago: "MIXTO",
        },
      ],
    };

    await guardarVentaLocal({
      ticket_id: ticketId,
      payload,
      estado: "pendiente",
      intentos: 0,
      creado_en: new Date().toISOString(),
    });

    const guardada = await obtenerVentaLocal(ticketId);
    expect(guardada).toBeDefined();
    expect(guardada?.payload.ticket.total_venta).toBe(1875);
    expect(guardada?.payload.ticket.monto_efectivo).toBe(1000);
    expect(guardada?.payload.ticket.monto_transf).toBe(875);
    expect(guardada?.payload.ticket.es_nocturno).toBe(true);
    expect(guardada?.payload.items[0].cantidad).toBe(2.5);
    expect(guardada?.payload.items[0].precio_unit).toBe(750);
    expect(guardada?.payload.items[0].subtotal).toBe(1875);
  });

  // 13. Precio único de venta y eliminación definitiva del selector diurno/nocturno
  it("13. Cada producto tiene un único precio visible y ya no existe selector diurno/nocturno", async () => {
    render(<App />);

    // 1. Verificar que NO existe selector diurno/nocturno en la interfaz
    expect(screen.queryByTestId("toggle-nocturno")).not.toBeInTheDocument();

    // 2. Esperar a que cargue catálogo
    await waitFor(() => {
      expect(screen.getByTestId("prod-card-1")).toBeInTheDocument();
    });

    // 3. Verificar que el producto exhibe su precio canónico único ($1.200)
    const cardGaseosa = screen.getByTestId("prod-card-1");
    expect(cardGaseosa).toHaveTextContent("$1.200");

    // 4. Agregar producto al carrito
    fireEvent.click(cardGaseosa);

    // 5. El carrito utiliza el precio canónico único
    expect(screen.getByTestId("cart-total-amount")).toHaveTextContent("$1.200");

    // 6. Cobrar ticket
    fireEvent.click(screen.getByTestId("btn-checkout"));
    fireEvent.click(screen.getByTestId("btn-confirm-payment"));

    // 7. Esperar que se guarde en cola local IndexedDB
    await waitFor(async () => {
      const lista = (await listarVentasDemo()).length > 0 ? await listarVentasDemo() : await listarVentasLocales();
      expect(lista.length).toBeGreaterThan(0);
      expect(lista[0].payload.ticket.total_venta).toBe(1200);
    });

    const lista = (await listarVentasDemo()).length > 0 ? await listarVentasDemo() : await listarVentasLocales();
    const ventaGuardada = lista[0];
    expect(ventaGuardada.payload.ticket.total_venta).toBe(1200);
    expect(ventaGuardada.payload.items[0].precio_unit).toBe(1200);
    expect(ventaGuardada.payload.items[0].subtotal).toBe(1200);
  });

  // 14. Navegación móvil entre catálogo y carrito
  it("14. Permite alternar entre catálogo y carrito en vista móvil sin perder el estado del carrito", async () => {
    render(<App />);

    // Esperar catálogo y agregar producto
    await waitFor(() => {
      expect(screen.getByTestId("prod-card-1")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("prod-card-1"));

    // Barra flotante móvil debe aparecer
    const mobileBar = await screen.findByTestId("mobile-cart-bar");
    expect(mobileBar).toBeInTheDocument();

    // Click en "Ver Carrito" en móvil
    const btnOpenMobileCart = screen.getByTestId("btn-open-cart-mobile");
    fireEvent.click(btnOpenMobileCart);

    // El botón para volver al catálogo debe estar presente
    const btnBack = screen.getByTestId("btn-back-to-catalog");
    expect(btnBack).toBeInTheDocument();

    // Volver al catálogo
    fireEvent.click(btnBack);

    // El catálogo vuelve a estar visible y el carrito conserva su ítem
    expect(screen.getByTestId("mobile-cart-bar")).toBeInTheDocument();
  });

  // 15. Modo Demo Estricto e Inmunidad de Red
  it("15. En Modo Demo seguro el cliente nunca realiza llamadas de red fetch ni activa backend real", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    render(<App />);

    // Esperar catálogo y agregar producto
    await waitFor(() => {
      expect(screen.getByTestId("prod-card-1")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("prod-card-1"));

    // Cobrar ticket simulado
    fireEvent.click(screen.getByTestId("btn-checkout"));
    fireEvent.click(screen.getByTestId("btn-confirm-payment"));

    // Esperar que la venta se guarde en IndexedDB
    await waitFor(async () => {
      const lista = (await listarVentasDemo()).length > 0 ? await listarVentasDemo() : await listarVentasLocales();
      expect(lista.length).toBeGreaterThan(0);
    });

    // Ninguna llamada HTTP fetch debió haber sido realizada para la venta
    const salesFetchCalls = fetchSpy.mock.calls.filter((call) => {
      const url = typeof call[0] === "string" ? call[0] : (call[0] as Request).url;
      return url.includes("/api/sales");
    });
    expect(salesFetchCalls.length).toBe(0);

    fetchSpy.mockRestore();
  });

  // 16. Protección de cola IndexedDB ante HTTP 503 SCHEMA_NOT_READY
  it("16. Conserva la venta en 'pendiente' sin perder datos ante error 503 SCHEMA_NOT_READY de la API", async () => {
    const fakeClient = {
      ...new MockApiClient(),
      registrarVenta: vi.fn().mockResolvedValue({
        status: 503,
        error: "SCHEMA_NOT_READY: La base de datos requiere migración 009 antes de registrar ventas.",
      }),
    };

    const ticketId = "T-SCHEMA-503";
    await guardarVentaLocal({
      ticket_id: ticketId,
      payload: {
        ticket: {
          ticket_id: ticketId,
          fecha: "24/10/2026 18:00:00",
          total_venta: 2500,
          monto_efectivo: 2500,
          monto_transf: 0,
          metodo_pago: "EFECTIVO",
        },
        items: [
          {
            ticket_id: ticketId,
            producto: "Gaseosa Cola 500ml",
            cantidad: 2,
            unidad: "Unidad",
            precio_unit: 1250,
            subtotal: 2500,
          },
        ],
      },
      estado: "pendiente",
      intentos: 0,
      creado_en: new Date().toISOString(),
    });

    const res = await sincronizarVentaIndividual(ticketId, fakeClient as any);
    expect(res.estado).toBe("pendiente");
    expect(res.codigo_http).toBe(503);
    expect(res.mensaje).toContain("SCHEMA_NOT_READY");

    // Verificar en IndexedDB: la venta sigue existiendo intacta y en estado pendiente
    const persistida = await obtenerVentaLocal(ticketId);
    expect(persistida).toBeDefined();
    expect(persistida?.estado).toBe("pendiente");
    expect(persistida?.intentos).toBe(1);
    expect(persistida?.codigo_http).toBe(503);
    expect(persistida?.ultimo_error).toContain("SCHEMA_NOT_READY");
    expect(persistida?.payload.ticket.total_venta).toBe(2500);
  });

  // 17. Sincronización en lote se detiene inmediatamente ante HTTP 503
  it("17. sincronizarCola se detiene ante HTTP 503 sin reintentar a ciegas ventas adicionales", async () => {
    const fakeClient = {
      ...new MockApiClient(),
      registrarVenta: vi.fn().mockResolvedValue({
        status: 503,
        error: "SCHEMA_NOT_READY: Base de datos en mantenimiento.",
      }),
    };

    await guardarVentaLocal({
      ticket_id: "T-BATCH-1",
      payload: {
        ticket: { ticket_id: "T-BATCH-1", fecha: "24/10/2026 18:00:00", total_venta: 100, monto_efectivo: 100, monto_transf: 0, metodo_pago: "EFECTIVO" },
        items: [{ ticket_id: "T-BATCH-1", producto: "Caramelo", cantidad: 1, unidad: "Unidad", precio_unit: 100, subtotal: 100 }],
      },
      estado: "pendiente",
      intentos: 0,
      creado_en: new Date().toISOString(),
    });
    await guardarVentaLocal({
      ticket_id: "T-BATCH-2",
      payload: {
        ticket: { ticket_id: "T-BATCH-2", fecha: "24/10/2026 18:01:00", total_venta: 200, monto_efectivo: 200, monto_transf: 0, metodo_pago: "EFECTIVO" },
        items: [{ ticket_id: "T-BATCH-2", producto: "Alfajor", cantidad: 1, unidad: "Unidad", precio_unit: 200, subtotal: 200 }],
      },
      estado: "pendiente",
      intentos: 0,
      creado_en: new Date().toISOString(),
    });

    const resultadoLote = await sincronizarCola(fakeClient as any);
    expect(resultadoLote.fallidos).toBe(1);
    // registrarVenta debe haberse invocado exactamente 1 vez (se detuvo el lote de inmediato)
    expect(fakeClient.registrarVenta).toHaveBeenCalledTimes(1);

    // Ambas ventas continúan pendientes y seguras en IndexedDB
    const v1 = await obtenerVentaLocal("T-BATCH-1");
    const v2 = await obtenerVentaLocal("T-BATCH-2");
    expect(v1?.estado).toBe("pendiente");
    expect(v2?.estado).toBe("pendiente");
  });

  // 18. Modo Demo aislado garantiza esquema preparado sin depender de Turso
  it("18. verificarPreparacionSistema en MockApiClient reporta esquema 100% preparado garantizando aislamiento", async () => {
    const mock = new MockApiClient();
    const readiness = await mock.verificarPreparacionSistema();
    expect(readiness.status).toBe(200);
    expect(readiness.data?.status).toBe("PREPARADO");
    expect(readiness.data?.listo).toBe(true);
    expect(readiness.data?.migraciones_pendientes).toEqual([]);
    expect(readiness.data?.inconsistencias).toEqual([]);
  });
});
