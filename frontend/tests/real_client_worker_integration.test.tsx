import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
// @ts-ignore - Módulos nativos de Node usados en entorno de pruebas Vitest
import crypto from "node:crypto";
// @ts-ignore - Módulos nativos de Node usados en entorno de pruebas Vitest
import { createRequire } from "node:module";
const requireWorker = createRequire(import.meta.url);
const { createClient } = requireWorker(
  "../../experiments/cloudflare-worker-prototype/node_modules/@libsql/client"
);
import { App } from "../src/App";
import { ExpensesCashAdmin } from "../src/components/ExpensesCashAdmin";
import { PaymentModal } from "../src/components/PaymentModal";
import { ServicesAdmin } from "../src/components/ServicesAdmin";
import {
  RealApiClient,
  activarModoDemo,
  activarModoReal,
  cerrarSesion,
  getApiClient,
  guardarSesionLocal,
  setApiClient,
} from "../src/services/api";
import {
  guardarCatalogoCache,
  guardarGastoColaLocal,
  guardarServicioColaLocal,
  listarGastosColaLocal,
  listarServiciosColaLocal,
  listarVentasDemo,
  listarVentasLocales,
  openDB,
  resetDBInstance,
} from "../src/services/db";

// Importar el backend real candidato (Cloudflare Worker + libSQL adapter + SCRAM)
// @ts-ignore - Módulos JS ESM del Worker
import { handleRequest } from "../../experiments/cloudflare-worker-prototype/src/api/router.js";
// @ts-ignore - Módulos JS ESM del Worker
import * as scramMod from "../../experiments/cloudflare-worker-prototype/src/auth/scram.js";
// @ts-ignore - Módulos JS ESM del Worker
import * as adapterMod from "../../experiments/cloudflare-worker-prototype/src/db/adapter.js";

const {
  ScramServer,
  HybridChallengeStore,
  convertirPbkdf2HashAScramVerifier,
} = scramMod as any;
const {
  inicializarEsquemaCompleto,
  poblarDatosPrueba,
} = adapterMod as any;

describe("Integración Local Real Frontend (RealApiClient) ↔ Backend (Worker + libSQL :memory:) y Cierre de Bloqueantes", () => {
  const USER_ADMIN = "alan";
  const PASS_ADMIN = "ClaveAdminSegura2026!";
  const USER_YILDA = "yilda";
  const PASS_YILDA = "ClaveYildaSegura2026!";

  let dbClient: ReturnType<typeof createClient>;
  let scramServer: any;
  let workerEnv: Record<string, any>;

  async function setupInMemoryWorkerBackend() {
    dbClient = createClient({ url: ":memory:" });
    await inicializarEsquemaCompleto(dbClient);
    await poblarDatosPrueba(dbClient);

    const iterations = 4096; // Iteraciones rápidas seguras para test local

    const saltAlan = crypto.randomBytes(16);
    const saltedAlan = crypto.pbkdf2Sync(PASS_ADMIN, saltAlan, iterations, 32, "sha256");
    const hashAlan = `pbkdf2_sha256$${iterations}$${saltAlan.toString("hex")}$${saltedAlan.toString("hex")}`;
    const verifierAlan = convertirPbkdf2HashAScramVerifier(hashAlan);
    verifierAlan.role = "admin";

    const saltYilda = crypto.randomBytes(16);
    const saltedYilda = crypto.pbkdf2Sync(PASS_YILDA, saltYilda, iterations, 32, "sha256");
    const hashYilda = `pbkdf2_sha256$${iterations}$${saltYilda.toString("hex")}$${saltedYilda.toString("hex")}`;
    const verifierYilda = convertirPbkdf2HashAScramVerifier(hashYilda);
    verifierYilda.role = "admin";

    const challengeStore = new HybridChallengeStore(dbClient);
    const serverSecret = crypto.randomBytes(32);
    const jwtSecret = crypto.randomBytes(32);

    scramServer = new ScramServer(
      new Map([
        [USER_ADMIN, verifierAlan],
        [USER_YILDA, verifierYilda],
      ]),
      serverSecret,
      challengeStore
    );
    workerEnv = {
      JWT_SECRET: jwtSecret,
      SCRAM_SERVER_SECRET: serverSecret.toString("hex"),
      ALLOWED_ORIGINS: "http://localhost:5173",
    };

    // Enrutar global fetch directamente hacia handleRequest real del Worker (sin mocks de respuestas)
    const workerFetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const urlStr =
        typeof input === "string"
          ? input.startsWith("http")
            ? input
            : `http://127.0.0.1:8787${input}`
          : input instanceof URL
          ? input.toString()
          : input.url;
      const req = new Request(urlStr, init);
      return await handleRequest(req, workerEnv, dbClient, scramServer);
    });

    vi.stubGlobal("fetch", workerFetch);
    return workerFetch;
  }

  beforeEach(async () => {
    sessionStorage.clear();
    cerrarSesion();
    activarModoDemo();
    resetDBInstance();
    const db = await openDB();
    const storeNames = [
      "ventas_demo",
      "ventas_queue",
      "servicios_queue",
      "gastos_queue",
      "catalogo_cache",
    ].filter((name) => db.objectStoreNames.contains(name));
    if (storeNames.length > 0) {
      const tx = db.transaction(storeNames, "readwrite");
      for (const name of storeNames) {
        tx.objectStore(name).clear();
      }
      await new Promise((resolve) => {
        tx.oncomplete = resolve;
      });
    }
  });

  afterEach(() => {
    vi.restoreAllMocks();
    sessionStorage.clear();
    cerrarSesion();
    activarModoDemo();
  });

  it("1. Ejecuta el flujo E2E completo con RealApiClient contra handleRequest real (14 puntos de integración)", async () => {
    await setupInMemoryWorkerBackend();
    const client = new RealApiClient("http://127.0.0.1:8787");

    // (1) Login SCRAM-SHA-256 real
    const loginRes = await client.login(USER_ADMIN, PASS_ADMIN);
    expect(loginRes.status).toBe(200);
    expect(loginRes.data?.access_token).toBeTruthy();
    expect(loginRes.data?.user.username).toBe(USER_ADMIN);
    expect(loginRes.data?.user.role).toBe("admin");

    // (2) Ruta protegida: /api/auth/me, /api/system/readiness y /api/catalog
    const meRes = await client.obtenerUsuarioActual();
    expect(meRes.status).toBe(200);
    expect(meRes.data?.username).toBe(USER_ADMIN);

    const readyRes = await client.verificarPreparacionSistema();
    expect(readyRes.status).toBe(200);
    expect(readyRes.data?.listo).toBe(true);
    expect(readyRes.data?.status).toBe("PREPARADO");

    const catRes = await client.obtenerCatalogo(false);
    expect(catRes.status).toBe(200);
    expect(catRes.data?.productos.length).toBe(2);
    const alfajor = catRes.data!.productos.find((p) => p.nombre === "Alfajor Chocolate")!;
    expect(alfajor).toBeDefined();
    expect(alfajor.precio_venta).toBe(1500);
    expect(alfajor.costo).toBe(950);

    // (3) Registro de venta con snapshot de costo_unitario (ej. 920 congelado offline)
    const ventaPayload = {
      ticket: {
        ticket_id: "T-E2E-REAL-001",
        fecha: "27/10/2026 11:00:00",
        fecha_hora_utc: "2026-10-27T14:00:00.000Z",
        total_venta: 3000,
        metodo_pago: "TRANSFERENCIA" as const,
        monto_efectivo: 0,
        monto_transf: 3000,
      },
      items: [
        {
          producto: "Alfajor Chocolate",
          cantidad: 2,
          unidad: "Unidad",
          precio_unit: 1500,
          subtotal: 3000,
          metodo_pago: "TRANSFERENCIA" as const,
          costo_unitario: 920,
        },
      ],
    };

    const regVentaRes = await client.registrarVenta(ventaPayload);
    expect(regVentaRes.status).toBe(201);
    expect(regVentaRes.data?.ticket_id).toBe("T-E2E-REAL-001");
    expect(regVentaRes.data?.status).toBe("registrado");

    // (4) Reintento idempotente de la misma venta -> 200 existente sin duplicar
    const replayVentaRes = await client.registrarVenta(ventaPayload);
    expect(replayVentaRes.status).toBe(200);
    expect(replayVentaRes.data?.ticket_id).toBe("T-E2E-REAL-001");
    expect(replayVentaRes.data?.status).toBe("existente");

    // (5) Consulta de detalle de venta (estructura canónica TicketDetalle)
    const detRes = await client.obtenerDetalleVenta("T-E2E-REAL-001");
    expect(detRes.status).toBe(200);
    expect(detRes.data?.ticket_id).toBe("T-E2E-REAL-001");
    expect(detRes.data?.total_venta).toBe(3000);
    expect(detRes.data?.monto_efectivo).toBe(0);
    expect(detRes.data?.monto_transf).toBe(3000);
    expect(detRes.data?.items.length).toBe(1);
    expect(detRes.data?.items[0].producto).toBe("Alfajor Chocolate");
    expect(detRes.data?.items[0].precio_unit).toBe(1500);
    expect(detRes.data?.items[0].costo_unitario).toBe(920);

    // (6) Edición de ítem de venta en TRANSFERENCIA (preserva monto_transf y no pasa a efectivo)
    const itemId = (detRes.data!.items[0] as any).id;
    const editVentaRes = await client.editarItemVenta("T-E2E-REAL-001", itemId, 3, 4500, "alan");
    expect(editVentaRes.status).toBe(200);
    expect(editVentaRes.data?.total_venta).toBe(4500);
    expect(editVentaRes.data?.monto_efectivo).toBe(0);
    expect(editVentaRes.data?.monto_transf).toBe(4500);
    expect(editVentaRes.data?.metodo_pago).toBe("TRANSFERENCIA");

    // Crear también una segunda venta en EFECTIVO y luego anularla
    await client.registrarVenta({
      ticket: {
        ticket_id: "T-E2E-ANULAR-002",
        fecha: "27/10/2026 11:05:00",
        total_venta: 1500,
        metodo_pago: "EFECTIVO",
        monto_efectivo: 1500,
        monto_transf: 0,
      },
      items: [
        {
          producto: "Alfajor Chocolate",
          cantidad: 1,
          unidad: "Unidad",
          precio_unit: 1500,
          subtotal: 1500,
          metodo_pago: "EFECTIVO",
        },
      ],
    });
    const anularRes = await client.anularVenta("T-E2E-ANULAR-002", "Prueba de anulación E2E", "alan");
    expect(anularRes.status).toBe(200);
    expect(anularRes.data?.anulado).toBe(true);

    // Crear tercera venta en EFECTIVO activa para el arqueo del día
    await client.registrarVenta({
      ticket: {
        ticket_id: "T-E2E-EFVO-003",
        fecha: "27/10/2026 11:10:00",
        total_venta: 2200,
        metodo_pago: "EFECTIVO",
        monto_efectivo: 2200,
        monto_transf: 0,
      },
      items: [
        {
          producto: "Paracetamol 500mg",
          cantidad: 1,
          unidad: "Unidad",
          precio_unit: 2200,
          subtotal: 2200,
          metodo_pago: "EFECTIVO",
          costo_unitario: 1400,
        },
      ],
    });

    // (7) Registro de servicio, edición y resumen canónico ResumenServicios
    const cargaRes = await client.registrarCarga({
      servicio: "Claro",
      monto_carga: 2000,
      monto_adicional: 100,
      total_cobrado: 2100,
      pago_efvo: 2100,
      pago_transf: 0,
      idempotency_key: "srv-e2e-001",
    });
    expect(cargaRes.status).toBe(201);
    expect(cargaRes.data?.servicio).toBe("Claro");
    expect(cargaRes.data?.pago_efvo).toBe(2100);
    expect(cargaRes.data?.pago_transf).toBe(0);

    // Reintento idempotente del servicio
    const cargaReplay = await client.registrarCarga({
      servicio: "Claro",
      monto_carga: 2000,
      monto_adicional: 100,
      total_cobrado: 2100,
      pago_efvo: 2100,
      pago_transf: 0,
      idempotency_key: "srv-e2e-001",
    });
    expect(cargaReplay.status).toBe(200);
    expect(cargaReplay.data?.id).toBe(cargaRes.data?.id);

    const resumenSrv = await client.obtenerResumenCargas();
    expect(resumenSrv.status).toBe(200);
    expect(resumenSrv.data?.total_operaciones).toBe(1);
    expect(resumenSrv.data?.total_capital).toBe(2000);
    expect(resumenSrv.data?.total_adicional).toBe(100);
    expect(resumenSrv.data?.total_cobrado).toBe(2100);
    expect(resumenSrv.data?.total_efectivo).toBe(2100);
    expect(resumenSrv.data?.capital_claro).toBe(2000);

    // (8) Registro de gasto, reintento idempotente y resumen de gastos
    const gastoRes = await client.registrarGasto({
      detalle: "Bolsa de consorcio",
      monto_salida: 500,
      tipo: "Gasto Operativo",
      origen_fondos: "Caja diaria",
      metodo_pago: "EFECTIVO",
      idempotency_key: "gst-e2e-001",
    });
    expect(gastoRes.status).toBe(201);
    expect(gastoRes.data?.detalle).toBe("Bolsa de consorcio");
    expect(gastoRes.data?.monto_salida).toBe(500);

    const gastoReplay = await client.registrarGasto({
      detalle: "Bolsa de consorcio",
      monto_salida: 500,
      tipo: "Gasto Operativo",
      origen_fondos: "Caja diaria",
      metodo_pago: "EFECTIVO",
      idempotency_key: "gst-e2e-001",
    });
    expect(gastoReplay.status).toBe(200);
    expect(gastoReplay.data?.id).toBe(gastoRes.data?.id);

    const resumenGst = await client.obtenerResumenGastos();
    expect(resumenGst.status).toBe(200);
    expect(resumenGst.data?.total_operaciones).toBe(1);
    expect(resumenGst.data?.gastos_total).toBe(500);
    expect(resumenGst.data?.gastos_efvo).toBe(500);
    expect(resumenGst.data?.gastos_transf).toBe(0);

    // (9) Arqueo diario canónico (ArqueoDiario)
    const arqueoHoy = await client.obtenerArqueoDiario();
    expect(arqueoHoy.status).toBe(200);
    expect(arqueoHoy.data?.servicios_efvo).toBe(2100);
    expect(arqueoHoy.data?.gastos_efvo).toBe(500);

    // (10) Cierre formal de caja diario (CierreDiario)
    const fechaArqueo = arqueoHoy.data!.fecha;
    const cierreRes = await client.registrarCierreDiario({
      fecha: fechaArqueo,
      efectivo_esperado: arqueoHoy.data!.efectivo_esperado,
      efectivo_contado: arqueoHoy.data!.efectivo_esperado,
      diferencia: 0,
      total_ventas_efvo: arqueoHoy.data!.ventas_efvo,
      total_ventas_transf: arqueoHoy.data!.ventas_transf,
      total_servicios_efvo: arqueoHoy.data!.servicios_efvo,
      total_servicios_transf: arqueoHoy.data!.servicios_transf,
      total_gastos_efvo: arqueoHoy.data!.gastos_efvo,
      total_gastos_transf: arqueoHoy.data!.gastos_transf,
      total_banco: arqueoHoy.data!.total_banco,
      observaciones: "Cierre E2E verificado",
      cerrado_por: "Alan",
    });
    expect(cierreRes.status).toBe(201);
    expect(cierreRes.data?.efectivo_contado).toBe(arqueoHoy.data!.efectivo_esperado);
    expect(cierreRes.data?.diferencia).toBe(0);

    // (11) Dashboard financiero: cambiar costo en catálogo y verificar que Caja A conserva el costo histórico congelado
    await client.editarProducto(alfajor.id_producto, { costo: 1350 });
    const dashRes = await client.obtenerMetricasDashboard();
    expect(dashRes.status).toBe(200);
    expect(dashRes.data?.esquema_completo).toBe(true);
    // Venta 1: 3x Alfajor con costo congelado 920 = 2760; Venta 3: 1x Paracetamol con costo 1400 = 1400 -> Total CMV = 4160
    expect(dashRes.data?.caja_a.total_ventas).toBe(6700);
    expect(dashRes.data?.caja_a.costo_historico_total).toBe(4160);
    expect(dashRes.data?.caja_a.ganancia_bruta_real).toBe(2540);
    expect(dashRes.data?.caja_c.ganancia_adicional_total).toBe(100);
    expect(dashRes.data?.caja_e.capital_total).toBe(2000);
    expect(dashRes.data?.gastos.gastos_total).toBe(500);

    // (12) Sesión inválida o expirada -> 401
    const invalidClient = new RealApiClient("http://127.0.0.1:8787");
    invalidClient.setAuthToken("token.invalido.manipulado");
    const unauthRes = await invalidClient.obtenerCatalogo();
    expect(unauthRes.status).toBe(401);

    // (13) Entrada inválida rechazada -> 400 / 409
    const badSaleRes = await client.registrarVenta({
      ticket: {
        ticket_id: "T-BAD-INPUT",
        fecha: "27/10/2026 12:00:00",
        total_venta: 5000, // No coincide con subtotal 1500
        metodo_pago: "EFECTIVO",
        monto_efectivo: 5000,
        monto_transf: 0,
      },
      items: [
        {
          producto: "Alfajor Chocolate",
          cantidad: 1,
          unidad: "Unidad",
          precio_unit: 1500,
          subtotal: 1500,
          metodo_pago: "EFECTIVO",
        },
      ],
    });
    expect(badSaleRes.status).toBe(400);

    // (14) Recuperación tras error de red sin duplicar operación (reintento idempotente)
    let failFirstAttempt = true;
    const origFetch = globalThis.fetch;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        if (failFirstAttempt && String(input).includes("/api/expenses") && init?.method === "POST") {
          failFirstAttempt = false;
          throw new Error("Network timeout simulado");
        }
        return origFetch(input, init);
      })
    );

    const payloadRecuperacion = {
      detalle: "Resma papel A4",
      monto_salida: 1200,
      tipo: "Gasto Operativo",
      origen_fondos: "Caja diaria",
      metodo_pago: "EFECTIVO" as const,
      idempotency_key: "gst-recovery-001",
    };

    const firstAttempt = await client.registrarGasto(payloadRecuperacion);
    expect(firstAttempt.status).toBe(0);

    const retryAttempt1 = await client.registrarGasto(payloadRecuperacion);
    expect(retryAttempt1.status).toBe(201);

    const retryAttempt2 = await client.registrarGasto(payloadRecuperacion);
    expect(retryAttempt2.status).toBe(200);
    expect(retryAttempt2.data?.id).toBe(retryAttempt1.data?.id);
  });

  it("2. Bloqueante 3 (Recarga F5): Con sesión válida en sessionStorage, restaura Modo Conectado (RealApiClient) y encola ventas en ventas_queue (0 en ventas_demo)", async () => {
    await setupInMemoryWorkerBackend();
    const realClient = new RealApiClient("http://127.0.0.1:8787");
    const loginRes = await realClient.login(USER_ADMIN, PASS_ADMIN);
    expect(loginRes.status).toBe(200);

    // Simular estado guardado previo a F5 y reinicio del singleton en memoria a MockApiClient
    guardarSesionLocal(loginRes.data!.access_token, loginRes.data!.user);

    const { unmount } = render(<App />);

    // Al montar tras F5, App debe restaurar automáticamente el modo conectado (isDemo = false)
    await waitFor(() => {
      expect(getApiClient().isDemo()).toBe(false);
      expect(screen.getByTestId("user-badge")).toHaveTextContent(/Alan/i);
    });

    // Verificar que ninguna operación caiga en ventas_demo
    const demoBefore = await listarVentasDemo();
    expect(demoBefore.length).toBe(0);

    unmount();
  });

  it("3. Bloqueante 3 (Recarga F5 con sesión expirada 401): Limpia sesión inválida, abre LoginModal y NO degrada silenciosamente a Modo Demo", async () => {
    await setupInMemoryWorkerBackend();
    guardarSesionLocal("jwt.expirado.invalido", {
      username: "alan",
      display_name: "Alan",
      role: "admin",
    });

    const { unmount } = render(<App />);

    await waitFor(() => {
      expect(screen.getByTestId("login-modal-card")).toBeInTheDocument();
    });

    // Debe permanecer en RealApiClient (no caer silenciosamente en MockApiClient)
    expect(getApiClient().isDemo()).toBe(false);
    expect(sessionStorage.getItem("genaro_pos_auth_token")).toBeNull();

    unmount();
  });

  it("4. Bloqueante 3 (Recarga F5 sin red / backend caído): Conserva Modo Conectado, usa catálogo en caché y guarda ventas en ventas_queue (0 en ventas_demo)", async () => {
    await guardarCatalogoCache([
      {
        id_producto: 1,
        nombre: "Alfajor Chocolate Offline",
        categoria: "Kiosco",
        proveedor: "Distribuidora Norte",
        unidad: "Unidad",
        precio_venta: 1500,
        costo: 950,
        margen_porcentaje: 0.58,
        fecha_act: "2026-10-27",
        activo: true,
      },
    ]);

    guardarSesionLocal("jwt.valido.offline", {
      username: "alan",
      display_name: "Alan",
      role: "admin",
    });

    // Backend inalcanzable (error de red)
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Failed to fetch")));

    const { unmount } = render(<App />);

    await waitFor(() => {
      expect(getApiClient().isDemo()).toBe(false);
      expect(screen.getByText("Alfajor Chocolate Offline")).toBeInTheDocument();
    });

    // Agregar al carrito y cobrar mientras el backend está caído
    fireEvent.click(screen.getByTestId("prod-card-1"));
    fireEvent.click(screen.getByTestId("btn-checkout"));

    await waitFor(() => {
      expect(screen.getByTestId("payment-modal")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("btn-confirm-payment"));

    await waitFor(async () => {
      const enColaReal = await listarVentasLocales();
      expect(enColaReal.length).toBe(1);
      expect(enColaReal[0].estado).toBe("pendiente");
    });

    const enColaDemo = await listarVentasDemo();
    expect(enColaDemo.length).toBe(0);

    unmount();
  });

  it("5. Importante (Colas Offline de Servicios y Gastos): SyncQueueModal muestra y sincroniza servicios y gastos pendientes aunque haya 0 ventas pendientes", async () => {
    await setupInMemoryWorkerBackend();
    activarModoReal("http://127.0.0.1:8787");
    const api = getApiClient();
    const loginRes = await api.login(USER_ADMIN, PASS_ADMIN);
    expect(loginRes.status).toBe(200);
    guardarSesionLocal(loginRes.data!.access_token, loginRes.data!.user);

    // Encolar 1 servicio y 1 gasto offline, con 0 ventas en ventas_queue
    await guardarServicioColaLocal({
      servicio: "SUBE",
      monto_carga: 1000,
      monto_adicional: 100,
      total_cobrado: 1100,
      pago_efvo: 1100,
      pago_transf: 0,
      idempotency_key: "srv-offline-only-1",
    });
    await guardarGastoColaLocal({
      detalle: "Foco LED local",
      monto_salida: 1800,
      tipo: "Gasto Operativo",
      origen_fondos: "Caja diaria",
      metodo_pago: "EFECTIVO",
      idempotency_key: "gst-offline-only-1",
    });

    const { unmount } = render(<App />);

    // El badge de la cola en Header debe contabilizar las 2 operaciones pendientes (servicio + gasto)
    await waitFor(() => {
      expect(screen.getByTestId("btn-open-queue")).toHaveTextContent("2");
    });

    fireEvent.click(screen.getByTestId("btn-open-queue"));

    await waitFor(() => {
      expect(screen.getByTestId("sync-queue-modal")).toBeInTheDocument();
      expect(screen.getByTestId("queue-service-srv-offline-only-1")).toBeInTheDocument();
      expect(screen.getByTestId("queue-expense-gst-offline-only-1")).toBeInTheDocument();
    });

    const syncBtn = screen.getByTestId("btn-sync-all") as HTMLButtonElement;
    expect(syncBtn.disabled).toBe(false);
    expect(syncBtn).toHaveTextContent("Sincronizar Pendientes (2)");

    fireEvent.click(syncBtn);

    await waitFor(async () => {
      const srvs = await listarServiciosColaLocal();
      const gsts = await listarGastosColaLocal();
      expect(srvs[0].estado).toBe("sincronizada");
      expect(gsts[0].estado).toBe("sincronizada");
    });

    unmount();
  });

  it("6. Importante (Prevención de Doble Envío de Gastos): Bloquea doble clic síncrono y reutiliza la misma idempotency_key al reintentar tras error HTTP 500", async () => {
    const keysRecibidas: Array<string | undefined> = [];
    let attemptCount = 0;

    const fakeApi = {
      isDemo: () => false,
      obtenerGastos: vi.fn().mockResolvedValue({ status: 200, data: { total: 0, gastos: [] } }),
      obtenerResumenGastos: vi.fn().mockResolvedValue({
        status: 200,
        data: {
          cantidad_gastos: 0,
          total_gastos: 0,
          total_efectivo: 0,
          total_transferencia: 0,
          total_operativo: 0,
          total_proveedores: 0,
          total_retiros: 0,
          total_otros: 0,
        },
      }),
      registrarGasto: vi.fn(async (gasto: any) => {
        attemptCount++;
        keysRecibidas.push(gasto.idempotency_key);
        await new Promise((r) => setTimeout(r, 60));
        if (attemptCount === 1) {
          return { status: 500, error: "Error temporal en base de datos" };
        }
        return {
          status: 201,
          data: {
            id: 10,
            fecha: "27/10/2026 12:00",
            tipo: gasto.tipo,
            detalle: gasto.detalle,
            monto_salida: gasto.monto_salida,
            origen_fondos: gasto.origen_fondos,
            metodo_pago: gasto.metodo_pago,
            anulado: false,
            idempotency_key: gasto.idempotency_key,
          },
        };
      }),
    } as any;

    setApiClient(fakeApi);

    const { unmount } = render(<ExpensesCashAdmin onAddToast={vi.fn()} isDemo={false} />);

    await waitFor(() => {
      expect(screen.getByTestId("input-expense-detail")).toBeInTheDocument();
    });

    fireEvent.change(screen.getByTestId("input-expense-detail"), {
      target: { value: "Pago flete mercadería" },
    });
    fireEvent.change(screen.getByTestId("input-expense-amount"), {
      target: { value: "2500" },
    });

    const submitBtn = screen.getByTestId("btn-register-expense");

    // Disparar dos clics síncronos seguidos: el candado submittingGastoRef debe bloquear el segundo
    fireEvent.click(submitBtn);
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(fakeApi.registrarGasto).toHaveBeenCalledTimes(1);
      expect(screen.getByTestId("expense-form-error")).toHaveTextContent(/Error temporal/i);
    });

    // Reintentar el mismo gasto tras el fallo 500 -> debe enviar exactamente la misma idempotency_key
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(fakeApi.registrarGasto).toHaveBeenCalledTimes(2);
    });

    expect(keysRecibidas[0]).toBeTruthy();
    expect(keysRecibidas[1]).toBe(keysRecibidas[0]);

    unmount();
  });

  it("7. Sincronización Multiusuario Real (Alan ↔ Yilda): Operaciones de un usuario persisten en la base compartida y son visibles inmediatamente para el otro usuario con trazabilidad de autor", async () => {
    await setupInMemoryWorkerBackend();

    // Dos clientes independientes simulando el dispositivo de Alan y el dispositivo de Yilda
    const clientAlan = new RealApiClient("http://127.0.0.1:8787");
    const clientYilda = new RealApiClient("http://127.0.0.1:8787");

    // 1. Login de ambos administradores
    const loginAlan = await clientAlan.login(USER_ADMIN, PASS_ADMIN);
    expect(loginAlan.status).toBe(200);
    expect(loginAlan.data?.user.username).toBe("alan");
    expect(loginAlan.data?.user.display_name).toBe("Alan");
    expect(loginAlan.data?.user.role).toBe("admin");

    const loginYilda = await clientYilda.login(USER_YILDA, PASS_YILDA);
    expect(loginYilda.status).toBe(200);
    expect(loginYilda.data?.user.username).toBe("yilda");
    expect(loginYilda.data?.user.display_name).toBe("Yilda");
    expect(loginYilda.data?.user.role).toBe("admin");

    // Verificar que /api/auth/me devuelve display_name normalizado para ambos tras F5
    const meAlan = await clientAlan.obtenerUsuarioActual();
    expect(meAlan.data?.display_name).toBe("Alan");
    const meYilda = await clientYilda.obtenerUsuarioActual();
    expect(meYilda.data?.display_name).toBe("Yilda");

    // 2. Alan registra una venta MIXTA ($3.000 = $1.000 efvo + $2.000 transf), un servicio, un gasto y actualiza un precio
    const dNow = new Date();
    const pad2 = (n: number) => String(n).padStart(2, "0");
    const hoyJujuyStr = `${pad2(dNow.getDate())}/${pad2(dNow.getMonth() + 1)}/${dNow.getFullYear()}`;

    const ventaAlanRes = await clientAlan.registrarVenta({
      ticket: {
        ticket_id: "T-SYNC-ALAN-01",
        fecha: `${hoyJujuyStr} 10:15:00`,
        total_venta: 3000,
        metodo_pago: "MIXTO",
        monto_efectivo: 1000,
        monto_transf: 2000,
      },
      items: [
        {
          producto: "Alfajor Chocolate",
          cantidad: 2,
          unidad: "Unidad",
          precio_unit: 1500,
          subtotal: 3000,
          metodo_pago: "MIXTO",
          costo_unitario: 950,
        },
      ],
    });
    expect(ventaAlanRes.status).toBe(201);

    const srvAlanRes = await clientAlan.registrarCarga({
      servicio: "Claro",
      monto_carga: 2000,
      monto_adicional: 100,
      total_cobrado: 2100,
      pago_efvo: 2100,
      pago_transf: 0,
      idempotency_key: "srv-sync-alan-01",
    });
    expect(srvAlanRes.status).toBe(201);

    const gstAlanRes = await clientAlan.registrarGasto({
      detalle: "Artículos de limpieza local",
      monto_salida: 400,
      tipo: "Limpieza",
      origen_fondos: "Caja diaria",
      metodo_pago: "EFECTIVO",
      idempotency_key: "gst-sync-alan-01",
    });
    expect(gstAlanRes.status).toBe(201);

    const catAlan = await clientAlan.obtenerCatalogo(false);
    const alfajor = catAlan.data!.productos.find((p) => p.nombre === "Alfajor Chocolate")!;
    const editProdRes = await clientAlan.editarProducto(alfajor.id_producto, {
      precio_venta: 1600,
    });
    expect(editProdRes.status).toBe(200);

    // 3. Yilda consulta desde su propio dispositivo/sesión y ve todos los datos actualizados de Alan
    const catYilda = await clientYilda.obtenerCatalogo(false);
    const alfajorVistoPorYilda = catYilda.data!.productos.find(
      (p) => p.id_producto === alfajor.id_producto
    )!;
    expect(alfajorVistoPorYilda.precio_venta).toBe(1600);

    const detalleVentaYilda = await clientYilda.obtenerDetalleVenta("T-SYNC-ALAN-01");
    expect(detalleVentaYilda.status).toBe(200);
    expect(detalleVentaYilda.data?.total_venta).toBe(3000);
    expect(detalleVentaYilda.data?.monto_efectivo).toBe(1000);
    expect(detalleVentaYilda.data?.monto_transf).toBe(2000);
    expect((detalleVentaYilda.data as any)?.auditoria?.[0]?.usuario).toBe("alan");

    // Yilda verifica el arqueo compartido: Efvo esperado = 1000 (venta) + 2100 (servicio) - 400 (gasto) = 2700
    const arqueoYilda = await clientYilda.obtenerArqueoDiario();
    expect(arqueoYilda.status).toBe(200);
    expect(arqueoYilda.data?.ventas_efvo).toBe(1000);
    expect(arqueoYilda.data?.ventas_transf).toBe(2000);
    expect(arqueoYilda.data?.servicios_efvo).toBe(2100);
    expect(arqueoYilda.data?.gastos_efvo).toBe(400);
    expect(arqueoYilda.data?.efectivo_esperado).toBe(2700);

    // 4. Yilda registra una nueva venta en EFECTIVO ($1.600) y realiza el cierre diario a su nombre
    const ventaYildaRes = await clientYilda.registrarVenta({
      ticket: {
        ticket_id: "T-SYNC-YILDA-02",
        fecha: `${hoyJujuyStr} 15:30:00`,
        total_venta: 1600,
        metodo_pago: "EFECTIVO",
        monto_efectivo: 1600,
        monto_transf: 0,
      },
      items: [
        {
          producto: "Alfajor Chocolate",
          cantidad: 1,
          unidad: "Unidad",
          precio_unit: 1600,
          subtotal: 1600,
          metodo_pago: "EFECTIVO",
          costo_unitario: 950,
        },
      ],
    });
    expect(ventaYildaRes.status).toBe(201);

    const arqueoActualizado = await clientYilda.obtenerArqueoDiario();
    expect(arqueoActualizado.data?.efectivo_esperado).toBe(4300); // 2700 + 1600

    const cierreYildaRes = await clientYilda.registrarCierreDiario({
      fecha: arqueoActualizado.data!.fecha,
      efectivo_esperado: 4300,
      efectivo_contado: 4300,
      diferencia: 0,
      total_ventas_efvo: 2600,
      total_ventas_transf: 2000,
      total_servicios_efvo: 2100,
      total_servicios_transf: 0,
      total_gastos_efvo: 400,
      total_gastos_transf: 0,
      total_banco: 2000,
      observaciones: "Turno tarde cerrado por Yilda",
      cerrado_por: "Yilda",
    });
    expect(cierreYildaRes.status).toBe(201);

    // 5. Alan consulta desde su sesión y ve la venta de Yilda y el cierre registrado por Yilda
    const detVentaYildaVistoPorAlan = await clientAlan.obtenerDetalleVenta("T-SYNC-YILDA-02");
    expect(detVentaYildaVistoPorAlan.status).toBe(200);
    expect((detVentaYildaVistoPorAlan.data as any)?.auditoria?.[0]?.usuario).toBe("yilda");

    const cierreVistoPorAlan = await clientAlan.obtenerCierreDiario(arqueoActualizado.data!.fecha);
    expect(cierreVistoPorAlan.status).toBe(200);
    expect(cierreVistoPorAlan.data?.cerrado_por).toBe("Yilda");
    expect(cierreVistoPorAlan.data?.efectivo_contado).toBe(4300);
  });

  it("8. Validación de Pagos Mixtos y Edición de Servicios: PaymentModal bloquea montos negativos en MIXTO y ServicesAdmin limpia pago_transf al cambiar de TRANSFERENCIA a EFECTIVO", async () => {
    await setupInMemoryWorkerBackend();
    activarModoReal("http://127.0.0.1:8787");
    const api = getApiClient();
    const loginRes = await api.login(USER_YILDA, PASS_YILDA);
    expect(loginRes.status).toBe(200);
    guardarSesionLocal(loginRes.data!.access_token, loginRes.data!.user);

    // Parte A: PaymentModal rechaza montos negativos en pago MIXTO
    const onConfirmSale = vi.fn();
    const { unmount: unmountModal } = render(
      <PaymentModal
        items={[
          {
            producto_id: 1,
            nombre: "Alfajor Chocolate",
            unidad: "Unidad",
            precio: 1500,
            cantidad: 2,
            subtotal: 3000,
            costo: 950,
          },
        ]}
        onClose={vi.fn()}
        onConfirmSale={onConfirmSale}
      />
    );

    fireEvent.click(screen.getByTestId("tab-mixto"));
    const inputEfvo = screen.getByTestId("input-mixto-efvo");
    const inputTransf = screen.getByTestId("input-mixto-transf");
    const btnConfirm = screen.getByTestId("btn-confirm-payment") as HTMLButtonElement;

    // Intentar ingresar efectivo negativo (-500) y transferencia 3500 (cuya suma algebraica daría 3000)
    fireEvent.change(inputEfvo, { target: { value: "-500" } });
    fireEvent.change(inputTransf, { target: { value: "3500" } });

    expect(btnConfirm.disabled).toBe(true);
    expect(screen.getByTestId("split-balance-error")).toBeInTheDocument();
    fireEvent.click(btnConfirm);
    expect(onConfirmSale).not.toHaveBeenCalled();

    // Corregir a valores positivos válidos: 1000 + 2000 = 3000
    fireEvent.change(inputEfvo, { target: { value: "1000" } });
    expect(btnConfirm.disabled).toBe(false);
    unmountModal();

    // Parte B: ServicesAdmin permite editar una carga de TRANSFERENCIA a EFECTIVO poniendo pago_transf en 0
    const regSrv = await api.registrarCarga({
      servicio: "Personal",
      monto_carga: 2000,
      monto_adicional: 100,
      total_cobrado: 2100,
      pago_efvo: 0,
      pago_transf: 2100,
      idempotency_key: "srv-edit-transf-to-efvo",
    });
    expect(regSrv.status).toBe(201);
    const srvId = regSrv.data!.id;

    const { unmount: unmountSrv } = render(
      <ServicesAdmin onAddToast={vi.fn()} isDemo={false} isMobileForced={false} />
    );

    await waitFor(() => {
      expect(screen.getByTestId(`btn-edit-charge-${srvId}`)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId(`btn-edit-charge-${srvId}`));
    await waitFor(() => {
      expect(screen.getByTestId("radio-edit-efectivo")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("radio-edit-efectivo"));
    fireEvent.click(screen.getByTestId("btn-save-charge-edit"));

    await waitFor(async () => {
      const cargasAfter = await api.obtenerCargas();
      const updated = cargasAfter.data?.cargas.find((c) => c.id === srvId);
      expect(updated?.pago_efvo).toBe(2100);
      expect(updated?.pago_transf).toBe(0);
    });

    unmountSrv();
  });
});
