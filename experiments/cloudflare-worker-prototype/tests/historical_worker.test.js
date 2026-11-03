import test from "node:test";
import assert from "node:assert/strict";
import { handleRequest } from "../src/api/router.js";
import { generarJwt } from "../src/api/router.js";

const JWT_SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
const TEST_ENV = {
  JWT_SECRET: JWT_SECRET,
  ALLOWED_ORIGINS: "https://genaro-pos-live.pages.dev",
};

// Mock de dbClient con respuestas simuladas para tablas históricas
function createMockDbClient() {
  return {
    async execute({ sql, args }) {
      const sqlUpper = sql.toUpperCase();

      if (sqlUpper.includes("COUNT(*)") && sqlUpper.includes("HISTORICO_TICKETS") && !sqlUpper.includes("GROUP BY")) {
        return {
          rows: [
            {
              cnt: 7246,
              tot_ventas: 23623890,
              tot_efectivo: 17467640,
              tot_transferencia: 6156250,
              min_fecha: "2025-05-18 09:15:00",
              max_fecha: "2026-03-01 22:30:00",
            },
          ],
        };
      }

      if (sqlUpper.includes("COUNT(*)") && sqlUpper.includes("HISTORICO_TICKET_ITEMS") && !sqlUpper.includes("GROUP BY")) {
        return {
          rows: [{ cnt: 11566, tot_subtotal: 23522590 }],
        };
      }

      if (sqlUpper.includes("COUNT(*)") && sqlUpper.includes("HISTORICO_CARGAS_SERVICIOS") && !sqlUpper.includes("GROUP BY")) {
        return {
          rows: [
            {
              cnt: 1741,
              tot_cobrado: 9556400,
              tot_carga: 9140400,
              tot_adicional: 416000,
              tot_efectivo: 6500000,
              tot_transferencia: 3056400,
            },
          ],
        };
      }

      if (sqlUpper.includes("GROUP BY") && sqlUpper.includes("HISTORICO_TICKETS")) {
        return {
          rows: [
            { periodo: "2025-05", tickets: 680, ventas_total: 1850000, efvo: 1400000, transf: 450000 },
            { periodo: "2025-06", tickets: 710, ventas_total: 2100000, efvo: 1600000, transf: 500000 },
          ],
        };
      }

      if (sqlUpper.includes("LIMIT ? OFFSET ?") && sqlUpper.includes("HISTORICO_TICKETS")) {
        return {
          rows: [
            {
              id: "T-20250518-001",
              fecha_hora_local: "2025-05-18 09:15:00",
              total_venta: 3500,
              monto_efectivo: 3500,
              monto_transferencia: 0,
              metodo_pago: "EFECTIVO",
              tiene_items: 1,
              balance_pago_diferencia: 0,
            },
          ],
        };
      }

      if (sqlUpper.includes("COUNT(DISTINCT PRODUCTO_NOMBRE)")) {
        return { rows: [{ cnt: 350 }] };
      }

      if (sqlUpper.includes("HISTORICO_TICKET_ITEMS") && sqlUpper.includes("ORDER BY TOTAL_RECAUDADO DESC")) {
        return {
          rows: [
            {
              producto_nombre: "Cigarrillos Philip Morris Box 20",
              operaciones: 840,
              unidades_cuantificables: 910,
              ventas_pesables_monto: 0,
              total_recaudado: 2150000,
            },
          ],
        };
      }

      if (sqlUpper.includes("HISTORICO_CARGAS_SERVICIOS") && sqlUpper.includes("GROUP BY SERVICIO")) {
        return {
          rows: [
            {
              servicio: "SUBE",
              total_operaciones: 920,
              capital: 4800000,
              ganancia: 210000,
              cobrado: 5010000,
              efvo: 3500000,
              transf: 1510000,
            },
          ],
        };
      }

      if (sqlUpper.includes("WHERE ID = ?") && sqlUpper.includes("HISTORICO_TICKETS")) {
        const id = args[0];
        if (id === "T-20250518-001") {
          return {
            rows: [
              {
                id: "T-20250518-001",
                fecha_hora_local: "2025-05-18 09:15:00",
                fecha_hora_utc: "2025-05-18 12:15:00",
                total_venta: 3500,
                monto_efectivo: 3500,
                monto_transferencia: 0,
                metodo_pago: "EFECTIVO",
                es_nocturno: 0,
                balance_pago_diferencia: 0,
                tiene_items: 1,
                origen_fila_excel: 45,
              },
            ],
          };
        }
        return { rows: [] };
      }

      if (sqlUpper.includes("HISTORICO_TICKET_ITEMS") && sqlUpper.includes("WHERE TICKET_ID = ?")) {
        return {
          rows: [
            {
              id: 1,
              ticket_id: "T-20250518-001",
              producto_nombre: "Alfajor Triple Chocolate",
              cantidad: 2,
              unidad: "Unidad",
              precio_unitario: 800,
              subtotal: 1600,
              metodo_pago: "EFECTIVO",
              calculo_subtotal_coincide: 1,
              es_duplicado_deriva: 0,
              ticket_id_correlacionado: null,
            },
          ],
        };
      }

      return { rows: [] };
    },
  };
}

test("Worker Router: Endpoints Históricos requieren autenticación Bearer", async () => {
  const dbClient = createMockDbClient();
  const req = new Request("https://genaro-pos-live-api.genaro-pos.workers.dev/api/historical/summary", {
    method: "GET",
  });
  const res = await handleRequest(req, TEST_ENV, dbClient, null);
  assert.equal(res.status, 401);
});

test("Worker Router: GET /api/historical/summary retorna métricas consolidadas", async () => {
  const dbClient = createMockDbClient();
  const token = generarJwt("alan", "admin", JWT_SECRET);
  const req = new Request("https://genaro-pos-live-api.genaro-pos.workers.dev/api/historical/summary", {
    method: "GET",
    headers: { Authorization: `Bearer ${token}` },
  });
  const res = await handleRequest(req, TEST_ENV, dbClient, null);
  assert.equal(res.status, 200);
  const data = await res.json();
  assert.equal(data.total_ventas_pesos, 23623890);
  assert.equal(data.total_tickets, 7246);
  assert.equal(data.ticket_promedio, 3260.27);
  assert.equal(data.total_items_filas, 11566);
  assert.equal(data.total_operaciones_cargas, 1741);
  assert.equal(data.total_cargas_cobrado, 9556400);
});

test("Worker Router: GET /api/historical/sales retorna ventas y serie temporal", async () => {
  const dbClient = createMockDbClient();
  const token = generarJwt("alan", "admin", JWT_SECRET);
  const req = new Request("https://genaro-pos-live-api.genaro-pos.workers.dev/api/historical/sales?desde=2025-05-18&hasta=2025-10-31", {
    method: "GET",
    headers: { Authorization: `Bearer ${token}` },
  });
  const res = await handleRequest(req, TEST_ENV, dbClient, null);
  assert.equal(res.status, 200);
  const data = await res.json();
  assert.equal(data.total_ventas, 23623890);
  assert.equal(data.tickets.length, 1);
  assert.equal(data.tickets[0].id, "T-20250518-001");
});

test("Worker Router: GET /api/historical/products respeta toggle depurado", async () => {
  const dbClient = createMockDbClient();
  const token = generarJwt("alan", "admin", JWT_SECRET);
  const req = new Request("https://genaro-pos-live-api.genaro-pos.workers.dev/api/historical/products?depurado=true", {
    method: "GET",
    headers: { Authorization: `Bearer ${token}` },
  });
  const res = await handleRequest(req, TEST_ENV, dbClient, null);
  assert.equal(res.status, 200);
  const data = await res.json();
  assert.equal(data.modo_depurado, true);
  assert.equal(data.productos.length, 1);
  assert.equal(data.productos[0].producto_nombre, "Cigarrillos Philip Morris Box 20");
});

test("Worker Router: GET /api/historical/services desglosa operaciones", async () => {
  const dbClient = createMockDbClient();
  const token = generarJwt("alan", "admin", JWT_SECRET);
  const req = new Request("https://genaro-pos-live-api.genaro-pos.workers.dev/api/historical/services", {
    method: "GET",
    headers: { Authorization: `Bearer ${token}` },
  });
  const res = await handleRequest(req, TEST_ENV, dbClient, null);
  assert.equal(res.status, 200);
  const data = await res.json();
  assert.equal(data.servicios.length, 1);
  assert.equal(data.servicios[0].servicio, "SUBE");
});

test("Worker Router: GET /api/historical/tickets/:ticket_id y 404", async () => {
  const dbClient = createMockDbClient();
  const token = generarJwt("alan", "admin", JWT_SECRET);

  // Existente
  const req1 = new Request("https://genaro-pos-live-api.genaro-pos.workers.dev/api/historical/tickets/T-20250518-001", {
    method: "GET",
    headers: { Authorization: `Bearer ${token}` },
  });
  const res1 = await handleRequest(req1, TEST_ENV, dbClient, null);
  assert.equal(res1.status, 200);
  const data1 = await res1.json();
  assert.equal(data1.id, "T-20250518-001");
  assert.equal(data1.items.length, 1);

  // Inexistente
  const req2 = new Request("https://genaro-pos-live-api.genaro-pos.workers.dev/api/historical/tickets/T-INEXISTENTE", {
    method: "GET",
    headers: { Authorization: `Bearer ${token}` },
  });
  const res2 = await handleRequest(req2, TEST_ENV, dbClient, null);
  assert.equal(res2.status, 404);
});

test("Worker Router: GET /api/historical/compare calcula variaciones", async () => {
  const dbClient = createMockDbClient();
  const token = generarJwt("alan", "admin", JWT_SECRET);
  const req = new Request(
    "https://genaro-pos-live-api.genaro-pos.workers.dev/api/historical/compare?desde_a=2025-05-18&hasta_a=2025-09-30&desde_b=2025-10-01&hasta_b=2026-03-01",
    {
      method: "GET",
      headers: { Authorization: `Bearer ${token}` },
    }
  );
  const res = await handleRequest(req, TEST_ENV, dbClient, null);
  assert.equal(res.status, 200);
  const data = await res.json();
  assert.ok(data.periodo_a);
  assert.ok(data.periodo_b);
  assert.ok(data.variaciones);
});
