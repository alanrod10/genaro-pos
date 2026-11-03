import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
// @ts-ignore
import crypto from "node:crypto";
import {
  RealApiClient,
  MockApiClient,
  IS_STATIC_DEMO_ONLY,
  isReadOnlySmokeActive,
} from "../src/services/api";
import { sincronizarCola, sincronizarVentaIndividual } from "../src/services/sync";
import { openDB, guardarVentaLocal, obtenerVentaLocal } from "../src/services/db";
import { SyncQueueModal } from "../src/components/SyncQueueModal";
import { MetodoPago, VentaLocal, VentaPayload } from "../src/types/sales";

function createMockScramFetch(
  user: { username: string; display_name: string; role?: string },
  password = "password_segura",
  token = "token_smoke_test_12345"
) {
  const salt = crypto.randomBytes(16);
  const saltB64 = salt.toString("base64");
  const sNonce = crypto.randomBytes(12).toString("base64");
  const iterations = 4096;
  let clientFirstBare = "";
  let rNonce = "";
  let serverFirst = "";

  return vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    const urlStr = String(url);
    if (urlStr.includes("/api/auth/scram-first")) {
      const body = JSON.parse(String(init?.body || "{}"));
      const clientFirst = body.clientFirstMessage || "";
      const match = clientFirst.match(/^n,,n=(.+),r=(.+)$/);
      clientFirstBare = clientFirst.replace(/^n,,/, "");
      const cNonce = match ? match[2] : "mock_c_nonce";
      rNonce = `${cNonce}${sNonce}`;
      serverFirst = `r=${rNonce},s=${saltB64},i=${iterations}`;
      return {
        ok: true,
        status: 200,
        headers: new Headers({ "content-type": "application/json" }),
        json: async () => ({
          serverFirstMessage: serverFirst,
          stateToken: "mock_state_token",
        }),
      };
    }
    if (urlStr.includes("/api/auth/scram-final")) {
      const saltedPassword = crypto.pbkdf2Sync(
        password.normalize("NFKC"),
        salt,
        iterations,
        32,
        "sha256"
      );
      const serverKey = crypto
        .createHmac("sha256", saltedPassword)
        .update("Server Key")
        .digest();
      const authMessage = `${clientFirstBare},${serverFirst},c=biws,r=${rNonce}`;
      const serverSignature = crypto
        .createHmac("sha256", serverKey)
        .update(authMessage)
        .digest("base64");
      return {
        ok: true,
        status: 200,
        headers: new Headers({ "content-type": "application/json" }),
        json: async () => ({
          serverFinalMessage: `v=${serverSignature}`,
          access_token: token,
          token_type: "bearer",
          user: {
            username: user.username,
            display_name: user.display_name,
            role: user.role || "operador",
          },
        }),
      };
    }
    return {
      ok: true,
      status: 200,
      headers: new Headers({ "content-type": "application/json" }),
      json: async () => ({ ok: true }),
    };
  });
}

describe("Protección y Seguridad: Modo Conectado de Solo Lectura (Smoke DEV)", () => {
  beforeEach(async () => {
    vi.restoreAllMocks();
    await openDB();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  // 1. El login sigue funcionando en modo conectado de solo lectura
  it("1. El login sigue funcionando en modo conectado de solo lectura", async () => {
    const mockFetch = createMockScramFetch(
      { username: "alan", display_name: "Alan", role: "admin" },
      "password_segura",
      "token_smoke_test_12345"
    );
    vi.stubGlobal("fetch", mockFetch);

    // Cliente real con modo solo lectura activo
    const client = new RealApiClient("http://127.0.0.1:8000", true);
    expect(client.isReadOnlySmoke()).toBe(true);

    const resLogin = await client.login("alan", "password_segura");
    expect(resLogin.status).toBe(200);
    expect(resLogin.data?.user.username).toBe("alan");
    expect(client.getAuthToken()).toBe("token_smoke_test_12345");
    expect(mockFetch).toHaveBeenCalled();
  });

  // 2. Las solicitudes GET pueden utilizar la API real
  it("2. Las solicitudes GET pueden consultar la API real normalmente", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ total: 820, productos: [] }),
    });
    vi.stubGlobal("fetch", mockFetch);

    const client = new RealApiClient("http://127.0.0.1:8000", true);
    client.setAuthToken("token_activo");

    const resCat = await client.obtenerCatalogo();
    expect(resCat.status).toBe(200);
    expect(resCat.data?.total).toBe(820);
    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch.mock.calls[0][0]).toBe("http://127.0.0.1:8000/api/catalog");
  });

  // 3. Las solicitudes POST comerciales se bloquean antes de llamar a fetch
  it("3. Las solicitudes POST comerciales se bloquean antes de llamar a fetch", async () => {
    const mockFetch = vi.fn();
    vi.stubGlobal("fetch", mockFetch);

    const client = new RealApiClient("http://127.0.0.1:8000", true);
    client.setAuthToken("token_activo");

    const dummyPayload: VentaPayload = {
      ticket: {
        ticket_id: "T-TEST-SMOKE-001",
        fecha: "2026-10-25",
        total_venta: 500,
        monto_efectivo: 500,
        monto_transf: 0,
        metodo_pago: "EFECTIVO" as MetodoPago,
        es_nocturno: false,
      },
      items: [
        {
          producto: "Alfajor",
          cantidad: 1,
          unidad: "u.",
          precio_unit: 500,
          subtotal: 500,
          metodo_pago: "EFECTIVO" as MetodoPago,
        },
      ],
    };

    const resVenta = await client.registrarVenta(dummyPayload);
    // Debe devolver status 0 o error indicando el bloqueo
    expect(resVenta.status).toBe(0);
    expect(resVenta.error).toContain("READ_ONLY_SMOKE_MODE");

    // Fetch NUNCA debió haber sido invocado
    expect(mockFetch).not.toHaveBeenCalled();
  });

  // 4. Las solicitudes PUT, PATCH y DELETE también se bloquean
  it("4. Las solicitudes PUT, PATCH y DELETE también se bloquean antes de llamar a fetch", async () => {
    const mockFetch = vi.fn();
    vi.stubGlobal("fetch", mockFetch);

    const client = new RealApiClient("http://127.0.0.1:8000", true);
    client.setAuthToken("token_activo");

    // PATCH editar producto
    const resPatch = await client.editarProducto(101, { nombre: "Nuevo Nombre" });
    expect(resPatch.status).toBe(0);
    expect(resPatch.error).toContain("READ_ONLY_SMOKE_MODE");

    // PUT actualizar precios preventista
    const resPut = await client.actualizarPreciosPreventista("Distribuidora", []);
    expect(resPut.status).toBe(0);
    expect(resPut.error).toContain("READ_ONLY_SMOKE_MODE");

    // POST/DELETE anular carga
    const resCancel = await client.anularCarga(55);
    expect(resCancel.status).toBe(0);
    expect(resCancel.error).toContain("READ_ONLY_SMOKE_MODE");

    // Ninguna llamada de red se ejecutó
    expect(mockFetch).not.toHaveBeenCalled();
  });

  // 5. Endpoints de autenticación SCRAM siguen explícitamente permitidos
  it("5. Endpoints de autenticación SCRAM siguen explícitamente permitidos", async () => {
    const mockFetch = createMockScramFetch(
      { username: "alan", display_name: "Alan", role: "admin" },
      "valid_pass",
      "tok_login_ok"
    );
    vi.stubGlobal("fetch", mockFetch);

    const client = new RealApiClient("http://127.0.0.1:8000", true);
    const res = await client.login("alan", "valid_pass");
    expect(res.status).toBe(200);
    expect(mockFetch).toHaveBeenCalled();
    const calls = mockFetch.mock.calls.map((c: any) => String(c[0]));
    expect(calls.some((u: string) => u.includes("/api/auth/scram-first"))).toBe(true);
    expect(calls.some((u: string) => u.includes("/api/auth/scram-final"))).toBe(true);
  });

  // 6. Ninguna venta de ventas_queue se envía automáticamente
  it("6. Ninguna venta de ventas_queue se envía automáticamente con modo solo lectura activo", async () => {
    const mockFetch = vi.fn();
    vi.stubGlobal("fetch", mockFetch);

    const ticketId = `T-QUEUE-TEST-${Date.now()}`;
    const ventaLocal: VentaLocal = {
      ticket_id: ticketId,
      payload: {
        ticket: {
          ticket_id: ticketId,
          fecha: "2026-10-25",
          total_venta: 1000,
          monto_efectivo: 1000,
          monto_transf: 0,
          metodo_pago: "EFECTIVO",
          es_nocturno: false,
        },
        items: [],
      },
      estado: "pendiente",
      intentos: 0,
      creado_en: new Date().toISOString(),
    };

    await guardarVentaLocal(ventaLocal);

    const client = new RealApiClient("http://127.0.0.1:8000", true);
    const resSync = await sincronizarCola(client);

    expect(resSync.procesados).toBe(0);
    expect(resSync.exitosos).toBe(0);
    expect(mockFetch).not.toHaveBeenCalled();

    // Comprobar que en la base local sigue intacta y pendiente
    const ventaPersistida = await obtenerVentaLocal(ticketId);
    expect(ventaPersistida?.estado).toBe("pendiente");
    expect(ventaPersistida?.intentos).toBe(0);
  });

  // 7. Los eventos online no ejecutan sincronizaciones
  it("7. Los eventos online no ejecutan sincronizaciones en modo de solo lectura", async () => {
    const mockFetch = vi.fn();
    vi.stubGlobal("fetch", mockFetch);

    const client = new RealApiClient("http://127.0.0.1:8000", true);
    expect(isReadOnlySmokeActive(client)).toBe(true);

    // Simulación del dispatch de online
    window.dispatchEvent(new Event("online"));

    // No se ejecutan peticiones de red
    expect(mockFetch).not.toHaveBeenCalled();
  });

  // 8. Las ventas pendientes permanecen sin cambios
  it("8. sincronizarVentaIndividual no altera la venta en IndexedDB si el modo solo lectura está activo", async () => {
    const mockFetch = vi.fn();
    vi.stubGlobal("fetch", mockFetch);

    const ticketId = `T-INTACTO-${Date.now()}`;
    await guardarVentaLocal({
      ticket_id: ticketId,
      payload: {
        ticket: {
          ticket_id: ticketId,
          fecha: "2026-10-25",
          total_venta: 250,
          monto_efectivo: 250,
          monto_transf: 0,
          metodo_pago: "EFECTIVO",
          es_nocturno: false,
        },
        items: [],
      },
      estado: "pendiente",
      intentos: 0,
      creado_en: new Date().toISOString(),
    });

    const client = new RealApiClient("http://127.0.0.1:8000", true);
    const res = await sincronizarVentaIndividual(ticketId, client);

    expect(res.estado).toBe("pendiente");
    expect(res.mensaje).toContain("READ_ONLY_SMOKE_MODE");
    expect(mockFetch).not.toHaveBeenCalled();

    const venta = await obtenerVentaLocal(ticketId);
    expect(venta?.estado).toBe("pendiente");
    expect(venta?.intentos).toBe(0);
  });

  // 9. El control de sincronización no permite enviar operaciones
  it("9. En SyncQueueModal el botón de sincronización muestra aviso y no dispara envíos", () => {
    const onSyncAllMock = vi.fn();
    const onSyncOneMock = vi.fn();

    render(
      <SyncQueueModal
        ventas={[
          {
            ticket_id: "T-TEST-001",
            payload: {
              ticket: {
                ticket_id: "T-TEST-001",
                fecha: "2026-10-25",
                total_venta: 500,
                monto_efectivo: 500,
                monto_transf: 0,
                metodo_pago: "EFECTIVO",
                es_nocturno: false,
              },
              items: [],
            },
            estado: "pendiente",
            intentos: 0,
            creado_en: new Date().toISOString(),
          },
        ]}
        isDemo={false}
        isReadOnlySmoke={true}
        onClose={() => {}}
        onSyncAll={onSyncAllMock}
        onSyncOne={onSyncOneMock}
      />
    );

    // Debe mostrar el aviso de modo de solo lectura
    expect(screen.getByTestId("readonly-sync-blocked-notice")).toBeInTheDocument();
    expect(
      screen.getByText(/Modo Solo Lectura activo: Sincronización deshabilitada/i)
    ).toBeInTheDocument();

    // El botón 'btn-sync-all' no debe renderizarse o no permitir acción
    expect(screen.queryByTestId("btn-sync-all")).not.toBeInTheDocument();

    // El botón de reintento individual debe estar deshabilitado
    const btnRetry = screen.getByTestId("btn-retry-T-TEST-001");
    expect(btnRetry).toBeDisabled();
    fireEvent.click(btnRetry);
    expect(onSyncOneMock).not.toHaveBeenCalled();
  });

  // 10. La expiración de sesión no provoca reintentos de escritura
  it("10. La expiración de sesión (HTTP 401) notifica y no reintenta escrituras", async () => {
    const onUnauthorizedMock = vi.fn();
    const mockFetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({ detail: "Token expirado" }),
    });
    vi.stubGlobal("fetch", mockFetch);

    const client = new RealApiClient("http://127.0.0.1:8000", true);
    client.setAuthToken("token_vencido");
    client.setOnUnauthorized(onUnauthorizedMock);

    // Solicitud GET que expira
    const res = await client.obtenerUsuarioActual();
    expect(res.status).toBe(401);
    expect(onUnauthorizedMock).toHaveBeenCalledTimes(1);

    // No se generaron escrituras
    expect(mockFetch).toHaveBeenCalledTimes(1);
    const usedMethod = mockFetch.mock.calls[0][1]?.method || "GET";
    expect(usedMethod).toBe("GET");
  });

  // 11. El modo demo estático sigue utilizando exclusivamente MockApiClient
  it("11. El modo demo estático (IS_STATIC_DEMO_ONLY) mantiene aislamiento total sobre el modo smoke", () => {
    // Si la demo estática está activa, isReadOnlySmokeActive siempre devuelve false
    // garantizando que no intente activar clientes reales ni interferir con la demo.
    const mockClient = new MockApiClient();
    expect(mockClient.isDemo()).toBe(true);

    if (IS_STATIC_DEMO_ONLY) {
      expect(isReadOnlySmokeActive(mockClient)).toBe(false);
    }
  });

  // 12. El comportamiento normal de los clientes no se altera cuando la nueva variable está desactivada
  it("12. El comportamiento normal de RealApiClient no se altera cuando readOnlySmoke está desactivado", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      json: async () => ({
        status: "registrado",
        ticket_id: "T-NORMAL-001",
        mensaje: "Venta registrada exitosamente",
      }),
    });
    vi.stubGlobal("fetch", mockFetch);

    // Cliente con readOnlySmoke explícitamente en false
    const normalClient = new RealApiClient("http://127.0.0.1:8000", false);
    expect(normalClient.isReadOnlySmoke()).toBe(false);

    const res = await normalClient.registrarVenta({
      ticket: {
        ticket_id: "T-NORMAL-001",
        fecha: "2026-10-25",
        total_venta: 100,
        monto_efectivo: 100,
        monto_transf: 0,
        metodo_pago: "EFECTIVO",
        es_nocturno: false,
      },
      items: [],
    });

    expect(res.status).toBe(201);
    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch.mock.calls[0][0]).toBe("http://127.0.0.1:8000/api/sales");
    expect(mockFetch.mock.calls[0][1]?.method).toBe("POST");
  });
});
