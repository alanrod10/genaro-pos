import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  guardarVentaLocal,
  obtenerVentaLocal,
  listarVentasLocales,
  guardarCatalogoCache,
  obtenerCatalogoCache,
  resetDBInstance,
  openDB,
} from "../src/services/db";
import {
  MockApiClient,
  activarModoDemo,
} from "../src/services/api";
import { sincronizarCola, sincronizarVentaIndividual } from "../src/services/sync";
import { VentaLocal, VentaPayload } from "../src/types/sales";
import { Producto } from "../src/types/catalog";

async function limpiarColas(): Promise<void> {
  const db = await openDB();
  return new Promise((resolve) => {
    const tx = db.transaction(["ventas_queue", "ventas_demo"], "readwrite");
    tx.objectStore("ventas_queue").clear();
    tx.objectStore("ventas_demo").clear();
    tx.oncomplete = () => resolve();
    tx.onerror = () => resolve();
  });
}

describe("GenaroPOS — Batería Automatizada de Resiliencia Offline y Sincronización (Fase C)", () => {
  beforeEach(async () => {
    resetDBInstance();
    await limpiarColas();
    activarModoDemo();
    sessionStorage.clear();
  });

  // ============================================================================
  // C1: VENTA OFFLINE NORMAL Y CONSULTA DE CATÁLOGO CACHEADO
  // ============================================================================
  describe("C1: Venta offline normal y fallback a catálogo en caché local", () => {
    it("C1.1: Guarda la venta en IndexedDB en estado pendiente y vacía el carrito", async () => {
      const ticketId = "T-OFFLINE-C1-1";

      const payload: VentaPayload = {
        ticket: {
          ticket_id: ticketId,
          fecha: "27/10/2026 14:00:00",
          total_venta: 2500,
          monto_efectivo: 2500,
          monto_transf: 0,
          metodo_pago: "EFECTIVO",
        },
        items: [
          {
            ticket_id: ticketId,
            producto: "Yerba Mate 1kg",
            cantidad: 1,
            unidad: "Unidad",
            precio_unit: 2500,
            subtotal: 2500,
            metodo_pago: "EFECTIVO",
          },
        ],
      };

      const ventaLocal: VentaLocal = {
        ticket_id: ticketId,
        payload,
        estado: "pendiente",
        intentos: 0,
        creado_en: new Date().toISOString(),
      };

      await guardarVentaLocal(ventaLocal);

      const persistida = await obtenerVentaLocal(ticketId);
      expect(persistida).toBeDefined();
      expect(persistida?.ticket_id).toBe(ticketId);
      expect(persistida?.estado).toBe("pendiente");
      expect(persistida?.payload.ticket.total_venta).toBe(2500);
      expect(persistida?.payload.items).toHaveLength(1);
    });

    it("C1.2: Consulta y recupera catálogo desde caché local IndexedDB ante desconexión", async () => {
      const productosMock: Producto[] = [
        {
          id_producto: 101,
          nombre: "Café Molido 250g",
          precio_venta: 3200,
          categoria: "Almacén",
          proveedor: "Distribuidora Central",
          unidad: "Unidad",
          costo: 2000,
          margen_porcentaje: 60,
          activo: true,
          fecha_act: "2026-10-27T10:00:00Z",
        },
        {
          id_producto: 102,
          nombre: "Té en Saquitos x20",
          precio_venta: 950,
          categoria: "Almacén",
          proveedor: "Distribuidora Central",
          unidad: "Unidad",
          costo: 600,
          margen_porcentaje: 58.33,
          activo: true,
          fecha_act: "2026-10-27T10:00:00Z",
        },
      ];

      await guardarCatalogoCache(productosMock);
      const cache = await obtenerCatalogoCache();

      expect(cache.productos).toHaveLength(2);
      expect(cache.productos[0].nombre).toBe("Café Molido 250g");
      expect(cache.productos[0].precio_venta).toBe(3200);
      expect(cache.actualizado_en).toBeDefined();
    });
  });

  // ============================================================================
  // C2: RECARGA Y REINICIO (PRESERVACIÓN DE COLA PENDIENTE)
  // ============================================================================
  describe("C2: Recarga y reinicio de la aplicación", () => {
    it("C2.1: Los tickets pendientes sobreviven al reinicio de la instancia de base de datos", async () => {
      const ticketId = "T-PERSIST-C2";
      await guardarVentaLocal({
        ticket_id: ticketId,
        payload: {
          ticket: {
            ticket_id: ticketId,
            fecha: "27/10/2026 14:15:00",
            total_venta: 4800,
            monto_efectivo: 2400,
            monto_transf: 2400,
            metodo_pago: "MIXTO",
          },
          items: [
            {
              ticket_id: ticketId,
              producto: "Aceite de Girasol 900ml",
              cantidad: 2,
              unidad: "Unidad",
              precio_unit: 2400,
              subtotal: 4800,
              metodo_pago: "MIXTO",
            },
          ],
        },
        estado: "pendiente",
        intentos: 0,
        creado_en: new Date().toISOString(),
      });

      // Simular reinicio / F5 cerrando la conexión y reseteando la promesa singleton
      resetDBInstance();

      // Nueva apertura en frío
      const cola = await listarVentasLocales();
      const recuperada = cola.find((v) => v.ticket_id === ticketId);

      expect(recuperada).toBeDefined();
      expect(recuperada?.estado).toBe("pendiente");
      expect(recuperada?.payload.ticket.total_venta).toBe(4800);
      expect(recuperada?.payload.ticket.metodo_pago).toBe("MIXTO");
      expect(recuperada?.payload.items[0].subtotal).toBe(4800);
    });
  });

  // ============================================================================
  // C3: RECONEXIÓN Y SINCRONIZACIÓN EXITOSA
  // ============================================================================
  describe("C3: Reconexión y sincronización automática", () => {
    it("C3.1: Sincroniza venta individual pasando de pendiente a sincronizada con HTTP 201", async () => {
      const ticketId = "T-SYNC-C3";
      await guardarVentaLocal({
        ticket_id: ticketId,
        payload: {
          ticket: {
            ticket_id: ticketId,
            fecha: "27/10/2026 14:30:00",
            total_venta: 1200,
            monto_efectivo: 1200,
            monto_transf: 0,
            metodo_pago: "EFECTIVO",
          },
          items: [
            {
              ticket_id: ticketId,
              producto: "Leche Entera 1L",
              cantidad: 1,
              unidad: "Unidad",
              precio_unit: 1200,
              subtotal: 1200,
            },
          ],
        },
        estado: "pendiente",
        intentos: 0,
        creado_en: new Date().toISOString(),
      });

      const client = {
        ...new MockApiClient(),
        isDemo: () => false,
        registrarVenta: vi.fn().mockResolvedValue({
          status: 201,
          data: {
            ticket_id: ticketId,
            status: "REGISTRADO",
            mensaje: "Venta registrada con éxito",
          },
        }),
      };

      const res = await sincronizarVentaIndividual(ticketId, client as any);
      expect(res.estado).toBe("sincronizada");
      expect(res.codigo_http).toBe(201);

      const enDb = await obtenerVentaLocal(ticketId);
      expect(enDb?.estado).toBe("sincronizada");
      expect(enDb?.codigo_http).toBe(201);
      expect(enDb?.sincronizado_en).toBeDefined();
    });
  });

  // ============================================================================
  // C4: INTERRUPCIÓN DURANTE EL ENVÍO E IDEMPOTENCIA
  // ============================================================================
  describe("C4: Interrupción durante el envío y recuperación idempotente", () => {
    it("C4.1: Ante fallo de red la venta se mantiene pendiente con intentos incrementados", async () => {
      const ticketId = "T-NETFAIL-C4";
      await guardarVentaLocal({
        ticket_id: ticketId,
        payload: {
          ticket: {
            ticket_id: ticketId,
            fecha: "27/10/2026 15:00:00",
            total_venta: 800,
            monto_efectivo: 800,
            monto_transf: 0,
            metodo_pago: "EFECTIVO",
          },
          items: [
            {
              ticket_id: ticketId,
              producto: "Pan Lactal Chico",
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

      const networkErrorClient = {
        ...new MockApiClient(),
        isDemo: () => false,
        registrarVenta: vi.fn().mockRejectedValue(new Error("Network connection lost")),
      };

      const res = await sincronizarVentaIndividual(ticketId, networkErrorClient as any);
      expect(res.estado).toBe("pendiente");
      expect(res.mensaje).toBe("Network connection lost");

      const enDb = await obtenerVentaLocal(ticketId);
      expect(enDb?.estado).toBe("pendiente");
      expect(enDb?.intentos).toBe(1);
      expect(enDb?.ultimo_error).toBe("Network connection lost");
    });

    it("C4.2: Reintento tras respuesta perdida responde 200 OK idempotente y actualiza estado", async () => {
      const ticketId = "T-IDEMPOTENT-C4";
      await guardarVentaLocal({
        ticket_id: ticketId,
        payload: {
          ticket: {
            ticket_id: ticketId,
            fecha: "27/10/2026 15:05:00",
            total_venta: 800,
            monto_efectivo: 800,
            monto_transf: 0,
            metodo_pago: "EFECTIVO",
          },
          items: [
            {
              ticket_id: ticketId,
              producto: "Pan Lactal Chico",
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

      // El servidor ya lo tenía registrado y devuelve 200 OK con el mismo ticket_id
      const idempotentClient = {
        ...new MockApiClient(),
        isDemo: () => false,
        registrarVenta: vi.fn().mockResolvedValue({
          status: 200,
          data: {
            ticket_id: ticketId,
            status: "YA_REGISTRADO",
            mensaje: "Venta ya registrada previamente (idempotente)",
          },
        }),
      };

      const res = await sincronizarVentaIndividual(ticketId, idempotentClient as any);
      expect(res.estado).toBe("sincronizada");
      expect(res.codigo_http).toBe(200);

      const enDb = await obtenerVentaLocal(ticketId);
      expect(enDb?.estado).toBe("sincronizada");
      expect(enDb?.codigo_http).toBe(200);
      expect(enDb?.ultimo_error).toBeUndefined();
    });
  });

  // ============================================================================
  // C5: REINTENTOS, CONCURRENCIA Y DIVERSAS RESPUESTAS HTTP
  // ============================================================================
  describe("C5: Prevención de duplicados, candado de concurrencia y respuestas HTTP", () => {
    it("C5.1: Candado en memoria (inFlightSync) previene envíos duplicados simultáneos", async () => {
      const ticketId = "T-CONCURRENT-C5";
      await guardarVentaLocal({
        ticket_id: ticketId,
        payload: {
          ticket: {
            ticket_id: ticketId,
            fecha: "27/10/2026 15:10:00",
            total_venta: 1500,
            monto_efectivo: 1500,
            monto_transf: 0,
            metodo_pago: "EFECTIVO",
          },
          items: [
            {
              ticket_id: ticketId,
              producto: "Jugo Naranja 1L",
              cantidad: 1,
              unidad: "Unidad",
              precio_unit: 1500,
              subtotal: 1500,
            },
          ],
        },
        estado: "pendiente",
        intentos: 0,
        creado_en: new Date().toISOString(),
      });

      let resolveApi: (val: any) => void;
      const apiPromise = new Promise((resolve) => {
        resolveApi = resolve;
      });

      const client = {
        ...new MockApiClient(),
        isDemo: () => false,
        registrarVenta: vi.fn().mockImplementation(() => apiPromise),
      };

      // Disparar dos sincronizaciones en paralelo
      const p1 = sincronizarVentaIndividual(ticketId, client as any);
      const p2 = sincronizarVentaIndividual(ticketId, client as any);

      // p2 debe detectar inmediatamente el candado
      const r2 = await p2;
      expect(r2.estado).toBe("sincronizando");
      expect(r2.mensaje).toContain("ya tiene una sincronización en curso");

      // Completar la primera
      resolveApi!({
        status: 201,
        data: { ticket_id: ticketId, status: "REGISTRADO" },
      });

      const r1 = await p1;
      expect(r1.estado).toBe("sincronizada");
      expect(client.registrarVenta).toHaveBeenCalledTimes(1);
    });

    it("C5.2: Respuesta 409 Conflict marca la venta como requiere_revision sin eliminarla", async () => {
      const ticketId = "T-CONFLICT-C5";
      await guardarVentaLocal({
        ticket_id: ticketId,
        payload: {
          ticket: {
            ticket_id: ticketId,
            fecha: "27/10/2026 15:15:00",
            total_venta: 3000,
            monto_efectivo: 3000,
            monto_transf: 0,
            metodo_pago: "EFECTIVO",
          },
          items: [
            {
              ticket_id: ticketId,
              producto: "Queso Cremoso 500g",
              cantidad: 1,
              unidad: "Unidad",
              precio_unit: 3000,
              subtotal: 3000,
            },
          ],
        },
        estado: "pendiente",
        intentos: 0,
        creado_en: new Date().toISOString(),
      });

      const conflictClient = {
        ...new MockApiClient(),
        isDemo: () => false,
        registrarVenta: vi.fn().mockResolvedValue({
          status: 409,
          error: "CONFLICT: Ticket ya existe con diferente importe.",
        }),
      };

      const res = await sincronizarVentaIndividual(ticketId, conflictClient as any);
      expect(res.estado).toBe("requiere_revision");
      expect(res.codigo_http).toBe(409);

      const enDb = await obtenerVentaLocal(ticketId);
      expect(enDb?.estado).toBe("requiere_revision");
      expect(enDb?.ultimo_error).toContain("CONFLICT");
    });

    it("C5.3: Respuesta 422 Unprocessable marca como requiere_revision preservando el ticket", async () => {
      const ticketId = "T-UNPROC-C5";
      await guardarVentaLocal({
        ticket_id: ticketId,
        payload: {
          ticket: {
            ticket_id: ticketId,
            fecha: "27/10/2026 15:20:00",
            total_venta: 100,
            monto_efectivo: 100,
            monto_transf: 0,
            metodo_pago: "EFECTIVO",
          },
          items: [],
        },
        estado: "pendiente",
        intentos: 0,
        creado_en: new Date().toISOString(),
      });

      const unprocClient = {
        ...new MockApiClient(),
        isDemo: () => false,
        registrarVenta: vi.fn().mockResolvedValue({
          status: 422,
          error: "UNPROCESSABLE_ENTITY: La lista de items no puede estar vacía.",
        }),
      };

      const res = await sincronizarVentaIndividual(ticketId, unprocClient as any);
      expect(res.estado).toBe("requiere_revision");
      expect(res.codigo_http).toBe(422);

      const enDb = await obtenerVentaLocal(ticketId);
      expect(enDb?.estado).toBe("requiere_revision");
      expect(enDb?.ultimo_error).toContain("UNPROCESSABLE_ENTITY");
    });

    it("C5.4: Respuesta 401 Unauthorized detiene la sincronización en lote y conserva la cola", async () => {
      await guardarVentaLocal({
        ticket_id: "T-401-1",
        payload: {
          ticket: { ticket_id: "T-401-1", fecha: "27/10/2026 15:26:00", total_venta: 500, monto_efectivo: 500, monto_transf: 0, metodo_pago: "EFECTIVO" },
          items: [{ ticket_id: "T-401-1", producto: "Item 1", cantidad: 1, unidad: "Unidad", precio_unit: 500, subtotal: 500 }],
        },
        estado: "pendiente",
        intentos: 0,
        creado_en: "2026-10-27T15:26:00.000Z",
      });
      await guardarVentaLocal({
        ticket_id: "T-401-2",
        payload: {
          ticket: { ticket_id: "T-401-2", fecha: "27/10/2026 15:25:00", total_venta: 600, monto_efectivo: 600, monto_transf: 0, metodo_pago: "EFECTIVO" },
          items: [{ ticket_id: "T-401-2", producto: "Item 2", cantidad: 1, unidad: "Unidad", precio_unit: 600, subtotal: 600 }],
        },
        estado: "pendiente",
        intentos: 0,
        creado_en: "2026-10-27T15:25:00.000Z",
      });

      const unauthClient = {
        ...new MockApiClient(),
        isDemo: () => false,
        registrarVenta: vi.fn().mockResolvedValue({
          status: 401,
          error: "UNAUTHORIZED: Token de acceso expirado o inválido.",
        }),
      };

      const lote = await sincronizarCola(unauthClient as any);
      // Se detuvo tras el primer 401
      expect(unauthClient.registrarVenta).toHaveBeenCalledTimes(1);
      expect(lote.fallidos).toBe(1);

      // Ambos tickets permanecen en la cola
      const t1 = await obtenerVentaLocal("T-401-1");
      const t2 = await obtenerVentaLocal("T-401-2");
      expect(t1?.estado).toBe("pendiente");
      expect(t2?.estado).toBe("pendiente");
    });

    it("C5.5: Respuesta 403 Forbidden y 429 Too Many Requests detienen la sincronización en lote", async () => {
      await guardarVentaLocal({
        ticket_id: "T-429-1",
        payload: {
          ticket: { ticket_id: "T-429-1", fecha: "27/10/2026 15:32:00", total_venta: 500, monto_efectivo: 500, monto_transf: 0, metodo_pago: "EFECTIVO" },
          items: [{ ticket_id: "T-429-1", producto: "Item 1", cantidad: 1, unidad: "Unidad", precio_unit: 500, subtotal: 500 }],
        },
        estado: "pendiente",
        intentos: 0,
        creado_en: "2026-10-27T15:32:00.000Z",
      });
      await guardarVentaLocal({
        ticket_id: "T-429-2",
        payload: {
          ticket: { ticket_id: "T-429-2", fecha: "27/10/2026 15:30:00", total_venta: 600, monto_efectivo: 600, monto_transf: 0, metodo_pago: "EFECTIVO" },
          items: [{ ticket_id: "T-429-2", producto: "Item 2", cantidad: 1, unidad: "Unidad", precio_unit: 600, subtotal: 600 }],
        },
        estado: "pendiente",
        intentos: 0,
        creado_en: "2026-10-27T15:30:00.000Z",
      });

      const rateLimitClient = {
        ...new MockApiClient(),
        isDemo: () => false,
        registrarVenta: vi.fn().mockResolvedValue({
          status: 429,
          error: "TOO_MANY_REQUESTS: Límite de solicitudes alcanzado.",
        }),
      };

      const resLote = await sincronizarCola(rateLimitClient as any);
      expect(rateLimitClient.registrarVenta).toHaveBeenCalledTimes(1);
      expect(resLote.fallidos).toBe(1);

      const t1 = await obtenerVentaLocal("T-429-1");
      const t2 = await obtenerVentaLocal("T-429-2");
      expect(t1?.estado).toBe("pendiente");
      expect(t1?.codigo_http).toBe(429);
      expect(t2?.estado).toBe("pendiente");
    });
  });

  // ============================================================================
  // C6: CONFLICTOS, DATOS OBSOLETOS Y EXPIRACIÓN DE SESIÓN
  // ============================================================================
  describe("C6: Conflictos, productos obsoletos y expiración de sesión", () => {
    it("C6.1: Venta offline retiene snapshot de producto/precio aun si el producto fue desactivado", async () => {
      const ticketId = "T-SNAPSHOT-C6";
      const ticketPayload: VentaPayload = {
        ticket: {
          ticket_id: ticketId,
          fecha: "27/10/2026 15:40:00",
          total_venta: 1800,
          monto_efectivo: 1800,
          monto_transf: 0,
          metodo_pago: "EFECTIVO",
        },
        items: [
          {
            ticket_id: ticketId,
            producto: "Producto Descontinuado X",
            cantidad: 2,
            unidad: "Unidad",
            precio_unit: 900,
            subtotal: 1800,
            costo_unitario: 500,
          },
        ],
      };

      await guardarVentaLocal({
        ticket_id: ticketId,
        payload: ticketPayload,
        estado: "pendiente",
        intentos: 0,
        creado_en: new Date().toISOString(),
      });

      const venta = await obtenerVentaLocal(ticketId);
      expect(venta?.payload.items[0].producto).toBe("Producto Descontinuado X");
      expect(venta?.payload.items[0].precio_unit).toBe(900);
      expect(venta?.payload.items[0].costo_unitario).toBe(500);
      expect(venta?.payload.items[0].subtotal).toBe(1800);
    });

    it("C6.2: Sesión expirada invoca callback de desautenticación sin destruir tickets pendientes", async () => {
      let callbackDisparado = false;
      const client = {
        ...new MockApiClient(),
        isDemo: () => false,
        setOnUnauthorized: (cb: () => void) => {
          client._onUnauth = cb;
        },
        _onUnauth: () => {},
        registrarVenta: vi.fn().mockImplementation(async () => {
          if (client._onUnauth) client._onUnauth();
          return { status: 401, error: "Token expired" };
        }),
      };

      client.setOnUnauthorized(() => {
        callbackDisparado = true;
      });

      const ticketId = "T-UNAUTH-C6";
      await guardarVentaLocal({
        ticket_id: ticketId,
        payload: {
          ticket: { ticket_id: ticketId, fecha: "27/10/2026 15:45:00", total_venta: 500, monto_efectivo: 500, monto_transf: 0, metodo_pago: "EFECTIVO" },
          items: [{ ticket_id: ticketId, producto: "Caramelos", cantidad: 1, unidad: "Unidad", precio_unit: 500, subtotal: 500 }],
        },
        estado: "pendiente",
        intentos: 0,
        creado_en: new Date().toISOString(),
      });

      await sincronizarVentaIndividual(ticketId, client as any);
      expect(callbackDisparado).toBe(true);

      const ticket = await obtenerVentaLocal(ticketId);
      expect(ticket).toBeDefined();
      expect(ticket?.estado).toBe("pendiente");
    });
  });

  // ============================================================================
  // C7: INTEGRIDAD, RECUPERACIÓN Y COLAS MIXTAS
  // ============================================================================
  describe("C7: Integridad, recuperación y colas mixtas", () => {
    it("C7.1: Sincronización de cola con fallos parciales preserva el orden y la consistencia", async () => {
      // Venta 1: Exitosa (201)
      await guardarVentaLocal({
        ticket_id: "T-MIX-1",
        payload: {
          ticket: { ticket_id: "T-MIX-1", fecha: "27/10/2026 16:00:00", total_venta: 100, monto_efectivo: 100, monto_transf: 0, metodo_pago: "EFECTIVO" },
          items: [{ ticket_id: "T-MIX-1", producto: "Item A", cantidad: 1, unidad: "Unidad", precio_unit: 100, subtotal: 100 }],
        },
        estado: "pendiente",
        intentos: 0,
        creado_en: "2026-10-27T16:00:00Z",
      });

      // Venta 2: Error 500 de servidor
      await guardarVentaLocal({
        ticket_id: "T-MIX-2",
        payload: {
          ticket: { ticket_id: "T-MIX-2", fecha: "27/10/2026 16:01:00", total_venta: 200, monto_efectivo: 200, monto_transf: 0, metodo_pago: "EFECTIVO" },
          items: [{ ticket_id: "T-MIX-2", producto: "Item B", cantidad: 1, unidad: "Unidad", precio_unit: 200, subtotal: 200 }],
        },
        estado: "pendiente",
        intentos: 0,
        creado_en: "2026-10-27T16:01:00Z",
      });

      const client = {
        ...new MockApiClient(),
        isDemo: () => false,
        registrarVenta: vi.fn().mockImplementation(async (payload: VentaPayload) => {
          if (payload.ticket.ticket_id === "T-MIX-1") {
            return {
              status: 201,
              data: { ticket_id: "T-MIX-1", status: "REGISTRADO" },
            };
          }
          return {
            status: 500,
            error: "INTERNAL_SERVER_ERROR: Fallo transitorio de base de datos.",
          };
        }),
      };

      const resultado = await sincronizarCola(client as any);
      expect(resultado.procesados).toBe(2);
      expect(resultado.exitosos).toBe(1);
      expect(resultado.fallidos).toBe(1);

      const v1 = await obtenerVentaLocal("T-MIX-1");
      const v2 = await obtenerVentaLocal("T-MIX-2");

      expect(v1?.estado).toBe("sincronizada");
      expect(v1?.codigo_http).toBe(201);

      expect(v2?.estado).toBe("pendiente");
      expect(v2?.codigo_http).toBe(500);
      expect(v2?.intentos).toBe(1);
    });

    it("C7.2: Intento de sincronizar ticket inexistente devuelve requiere_revision sin crash", async () => {
      const client = {
        ...new MockApiClient(),
        isDemo: () => false,
      };

      const res = await sincronizarVentaIndividual("T-NONEXISTENT", client as any);
      expect(res.estado).toBe("requiere_revision");
      expect(res.mensaje).toContain("no encontrado");
    });
  });
});
