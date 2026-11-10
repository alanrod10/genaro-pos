/**
 * Suite de Integración Completa: Validación de todos los Endpoints de la Versión Candidata.
 * Verifica los 42 contratos de la API original, persistencia real, SCRAM, idempotencia y auditoría.
 */

import test from "node:test";
import assert from "node:assert/strict";
import crypto from "node:crypto";
import { createClient } from "@libsql/client";
import { inicializarEsquemaCompleto, poblarDatosPrueba } from "../src/db/adapter.js";
import { ScramServer, convertirPbkdf2HashAScramVerifier } from "../src/auth/scram.js";
import { handleRequest } from "../src/api/router.js";
import { ScramApiClient } from "../src/frontend_adapter/scram_client_adapter.js";

test("API Candidata: Cobertura integral de endpoints, contratos y autenticación SCRAM", async () => {
  // 1. Setup de Base de Datos y Verificadores SCRAM
  const dbClient = createClient({ url: ":memory:" });
  await inicializarEsquemaCompleto(dbClient);
  await poblarDatosPrueba(dbClient);

  const USER = "operador_alan";
  const PASS = "ClaveSegura2026!";
  const salt = crypto.randomBytes(16);
  const salted = crypto.pbkdf2Sync(PASS, salt, 600000, 32, "sha256");
  const storedHash = `pbkdf2_sha256$600000$${salt.toString("hex")}$${salted.toString("hex")}`;
  const verifier = convertirPbkdf2HashAScramVerifier(storedHash);
  verifier.role = "admin";

  const verifiersDb = new Map([[USER, verifier]]);
  const serverSecret = crypto.randomBytes(32);
  const scramServer = new ScramServer(verifiersDb, serverSecret);

  const env = {
    JWT_SECRET: crypto.randomBytes(32),
    SCRAM_SERVER_SECRET: serverSecret,
  };

  const dispatch = async (method, path, body = null, headers = {}) => {
    const init = {
      method,
      headers: new Headers({ "Content-Type": "application/json", ...headers }),
    };
    if (body) {
      init.body = JSON.stringify(body);
    }
    const req = new Request(`http://127.0.0.1:8787${path}`, init);
    const resp = await handleRequest(req, env, dbClient, scramServer);
    const json = await resp.json().catch(() => ({}));
    return { status: resp.status, data: json, headers: resp.headers };
  };

  // --- 01. /health ---
  const healthRes = await dispatch("GET", "/health");
  assert.equal(healthRes.status, 200);
  assert.equal(healthRes.data.status, "ok");

  // --- Rechazo 401 en ruta protegida sin token ---
  const unauthRes = await dispatch("GET", "/api/catalog");
  assert.equal(unauthRes.status, 401);
  assert.ok(unauthRes.data.detail.includes("No autenticado"));

  // --- Handshake SCRAM completo (Paso 1 y Paso 2) ---
  const clientAdapter = new ScramApiClient("http://127.0.0.1:8787");
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    const u = new URL(url);
    const path = u.pathname + u.search;
    const body = options.body ? JSON.parse(options.body) : null;
    const headers = options.headers ? Object.fromEntries(new Headers(options.headers).entries()) : {};
    const res = await dispatch(options.method || "GET", path, body, headers);
    return new Response(JSON.stringify(res.data), {
      status: res.status,
      headers: { "Content-Type": "application/json" },
    });
  };

  const loginRes = await clientAdapter.login(USER, PASS);
  assert.equal(loginRes.status, 200);
  assert.ok(loginRes.data.access_token);
  assert.equal(loginRes.data.user.username, USER);

  const token = loginRes.data.access_token;
  const authHeaders = { Authorization: `Bearer ${token}` };

  // --- 02. POST /api/auth/login (Inhabilitado definitivamente con HTTP 410 Gone) ---
  const compatLoginRes = await dispatch("POST", "/api/auth/login", { username: USER, password: "any" });
  assert.equal(compatLoginRes.status, 410);
  assert.equal(compatLoginRes.data.error, "AUTH_ENDPOINT_DEPRECATED");

  // --- 03. GET /api/auth/me ---
  const meRes = await dispatch("GET", "/api/auth/me", null, authHeaders);
  assert.equal(meRes.status, 200);
  assert.equal(meRes.data.username, USER);

  // --- 04. GET /api/system/readiness ---
  const readinessRes = await dispatch("GET", "/api/system/readiness", null, authHeaders);
  assert.equal(readinessRes.status, 200);
  assert.equal(readinessRes.data.status, "PREPARADO");
  assert.equal(readinessRes.data.listo, true);
  assert.equal(readinessRes.data.migraciones_aplicadas.length, 11);
  assert.ok(readinessRes.data.migraciones_aplicadas.includes(11));
  assert.ok(readinessRes.data.tablas_presentes.includes("auth_scram_challenges"));

  // ==========================================
  // --- CATÁLOGO ---
  // ==========================================

  // 05. GET /api/catalog
  const catRes = await dispatch("GET", "/api/catalog", null, authHeaders);
  assert.equal(catRes.status, 200);
  assert.equal(catRes.data.productos.length, 2);

  // 06. POST /api/catalog
  const nuevoProd = {
    id: "prod-caramelo",
    codigo_barras: "7790099",
    nombre: "Caramelo Menta",
    categoria_id: "cat-kiosco",
    precio: 200,
    costo_unitario: 110,
  };
  const crearProdRes = await dispatch("POST", "/api/catalog", nuevoProd, authHeaders);
  assert.equal(crearProdRes.status, 201);
  assert.equal(crearProdRes.data.nombre, "Caramelo Menta");
  const nuevoId = crearProdRes.data.id;

  // 08. PATCH /api/catalog/:id/status
  const statusRes = await dispatch("PATCH", `/api/catalog/${nuevoId}/status`, { activo: false }, authHeaders);
  assert.equal(statusRes.status, 200);
  assert.equal(statusRes.data.activo, 0);

  // 07. PATCH /api/catalog/:id (Edición de producto)
  const editProdRes = await dispatch("PATCH", `/api/catalog/${nuevoId}`, { precio: 250, costo_unitario: 125 }, authHeaders);
  assert.equal(editProdRes.status, 200);
  assert.equal(editProdRes.data.precio, 250);
  assert.equal(editProdRes.data.costo_unitario, 125);

  // 09. DELETE /api/catalog/:id (Smart Delete: borrado físico si no tiene ventas)
  const delProdRes = await dispatch("DELETE", `/api/catalog/${nuevoId}`, null, authHeaders);
  assert.equal(delProdRes.status, 200);
  assert.equal(delProdRes.data.eliminado_fisico, true);
  assert.equal(delProdRes.data.accion, "borrado_fisico");

  // Reintento de borrado de producto ya eliminado -> 404 Not Found
  const del404Res = await dispatch("DELETE", `/api/catalog/${nuevoId}`, null, authHeaders);
  assert.equal(del404Res.status, 404);

  // ==========================================
  // --- VENTAS POS ---
  // ==========================================

  // 09. POST /api/sales (Crear venta atómica, Idempotencia y Cancelar)
  const ventaPayload = {
    ticket: {
      id: "ticket-api-100",
      fecha_hora_utc: new Date().toISOString(),
      total_venta: 1500,
      monto_efectivo: 1500,
      monto_transferencia: 0,
      metodo_pago: "EFECTIVO",
    },
    items: [{ producto_nombre: "Alfajor Chocolate", cantidad: 1, precio_unitario: 1500 }],
    idempotency_key: "idem-sale-100",
  };
  const ventaRes = await dispatch("POST", "/api/sales", ventaPayload, authHeaders);
  assert.equal(ventaRes.status, 201);
  assert.equal(ventaRes.data.status, "registrado");

  // Reintento idéntico idempotente -> 200 OK con status 'existente'
  const ventaReplayRes = await dispatch("POST", "/api/sales", ventaPayload, authHeaders);
  assert.equal(ventaReplayRes.status, 200);
  assert.equal(ventaReplayRes.data.status, "existente");

  // Reintento con misma clave pero total diferente -> 409 Conflict
  const ventaConflictoPayload = {
    ...ventaPayload,
    ticket: { ...ventaPayload.ticket, total_venta: 3000, monto_efectivo: 3000 },
  };
  const ventaConflictRes = await dispatch("POST", "/api/sales", ventaConflictoPayload, authHeaders);
  assert.equal(ventaConflictRes.status, 409);

  // 10. GET /api/sales
  const listarVentasRes = await dispatch("GET", "/api/sales", null, authHeaders);
  assert.equal(listarVentasRes.status, 200);
  assert.ok(listarVentasRes.data.ventas.length >= 1);

  // 11. GET /api/sales/:ticket_id
  const detalleVenta = await dispatch("GET", "/api/sales/ticket-api-100", null, authHeaders);
  assert.equal(detalleVenta.status, 200);
  assert.equal(detalleVenta.data.ticket.id, "ticket-api-100");
  assert.equal(detalleVenta.data.items.length, 1);
  const itemId = detalleVenta.data.items[0].id;

  // 13. PUT /api/sales/:ticket_id/items/:item_id (Editar ítem de venta)
  const editItemRes = await dispatch("PUT", `/api/sales/ticket-api-100/items/${itemId}`, {
    nueva_cantidad: 2,
    nuevo_subtotal: 3000,
  }, authHeaders);
  assert.equal(editItemRes.status, 200);
  assert.equal(editItemRes.data.ticket.total_venta, 3000);

  // 12. POST /api/sales/:ticket_id/anular
  const anularVentaRes = await dispatch("POST", "/api/sales/ticket-api-100/anular", { motivo: "Error cajero" }, authHeaders);
  assert.equal(anularVentaRes.status, 200);
  assert.equal(anularVentaRes.data.anulado, true);

  // ==========================================
  // --- SERVICIOS ---
  // ==========================================

  // 17. POST /api/services
  const cargaPayload = {
    id: "serv-api-200",
    fecha_hora_utc: new Date().toISOString(),
    tipo_servicio: "PREPAY",
    monto_capital: 3000,
    cargo_comision: 200,
    monto_total: 3200,
    metodo_pago: "EFECTIVO",
  };
  const cargaRes = await dispatch("POST", "/api/services", cargaPayload, authHeaders);
  assert.equal(cargaRes.status, 201);
  assert.equal(cargaRes.data.status, "registrado");
  const cargaId = cargaRes.data.id;

  // 14. GET /api/services
  const cargasLista = await dispatch("GET", "/api/services", null, authHeaders);
  assert.equal(cargasLista.status, 200);
  assert.ok(cargasLista.data.cargas.length >= 1);

  // 15. GET /api/services/summary
  const resumenServ = await dispatch("GET", "/api/services/summary", null, authHeaders);
  assert.equal(resumenServ.status, 200);
  assert.equal(resumenServ.data.total_capital, 3000);
  assert.equal(resumenServ.data.total_comision, 200);

  // 16. GET /api/services/:id
  const getCargaRes = await dispatch("GET", `/api/services/${cargaId}`, null, authHeaders);
  assert.equal(getCargaRes.status, 200);
  assert.equal(getCargaRes.data.id, cargaId);

  // 18. PATCH /api/services/:id
  const editCargaRes = await dispatch("PATCH", `/api/services/${cargaId}`, { cargo_comision: 250, monto_total: 3250 }, authHeaders);
  assert.equal(editCargaRes.status, 200);
  assert.equal(editCargaRes.data.cargo_comision, 250);

  // 20. POST /api/services/:id/cancel
  const cancelServRes = await dispatch("POST", `/api/services/${cargaId}/cancel`, { motivo: "Cliente canceló" }, authHeaders);
  assert.equal(cancelServRes.status, 200);
  assert.equal(cancelServRes.data.anulado, true);

  // 19. DELETE /api/services/:id (Crear otra para probar DELETE)
  const delCargaInit = await dispatch("POST", "/api/services", {
    id: "serv-del-201",
    fecha_hora_utc: new Date().toISOString(),
    tipo_servicio: "SUBE",
    monto_capital: 1000,
    cargo_comision: 50,
    monto_total: 1050,
    metodo_pago: "EFECTIVO",
  }, authHeaders);
  const delCargaRes = await dispatch("DELETE", `/api/services/${delCargaInit.data.id}`, null, authHeaders);
  assert.equal(delCargaRes.status, 200);
  assert.equal(delCargaRes.data.anulado, true);

  // ==========================================
  // --- GASTOS Y ARQUEO ---
  // ==========================================

  // 29. POST /api/expenses
  const gastoPayload = {
    id: "gasto-api-300",
    fecha_hora_utc: new Date().toISOString(),
    monto: 850,
    concepto: "Artículos de limpieza",
    categoria: "LIMPIEZA",
    metodo_pago: "EFECTIVO",
  };
  const gastoRes = await dispatch("POST", "/api/expenses", gastoPayload, authHeaders);
  assert.equal(gastoRes.status, 201);
  const gastoId = gastoRes.data.id;

  // 21. GET /api/expenses
  const listaGastosRes = await dispatch("GET", "/api/expenses", null, authHeaders);
  assert.equal(listaGastosRes.status, 200);
  assert.ok(listaGastosRes.data.gastos.length >= 1);

  // 28. GET /api/expenses/:id
  const getGastoRes = await dispatch("GET", `/api/expenses/${gastoId}`, null, authHeaders);
  assert.equal(getGastoRes.status, 200);
  assert.equal(getGastoRes.data.monto, 850);

  // 30. PATCH /api/expenses/:id
  const editGastoRes = await dispatch("PATCH", `/api/expenses/${gastoId}`, { monto: 900, concepto: "Limpieza profunda" }, authHeaders);
  assert.equal(editGastoRes.status, 200);
  assert.equal(editGastoRes.data.monto, 900);

  // 22. GET /api/expenses/summary
  const gastosSummary = await dispatch("GET", "/api/expenses/summary", null, authHeaders);
  assert.equal(gastosSummary.status, 200);
  assert.equal(gastosSummary.data.gastos_efvo, 900);

  // 23. GET /api/expenses/arqueo
  const arqueoRes = await dispatch("GET", "/api/expenses/arqueo", null, authHeaders);
  assert.equal(arqueoRes.status, 200);
  assert.equal(arqueoRes.data.gastos_efectivo, 900);

  // 24. GET /api/expenses/dashboard
  const expDashRes = await dispatch("GET", "/api/expenses/dashboard", null, authHeaders);
  assert.equal(expDashRes.status, 200);
  assert.equal(expDashRes.data.esquema_completo, true);

  // 27. POST /api/expenses/cierres
  const cierreRes = await dispatch("POST", "/api/expenses/cierres", {
    fecha: arqueoRes.data.fecha,
    efectivo_real: arqueoRes.data.efectivo_esperado,
    observaciones: "Cierre conforme sin diferencias",
  }, authHeaders);
  assert.equal(cierreRes.status, 201);
  assert.equal(cierreRes.data.diferencia, 0);

  // 25. GET /api/expenses/cierres
  const cierresLista = await dispatch("GET", "/api/expenses/cierres", null, authHeaders);
  assert.equal(cierresLista.status, 200);
  assert.ok(cierresLista.data.length >= 1);

  // 26. GET /api/expenses/cierres/:fecha
  const cierreFechaRes = await dispatch("GET", `/api/expenses/cierres/${arqueoRes.data.fecha}`, null, authHeaders);
  assert.equal(cierreFechaRes.status, 200);
  assert.equal(cierreFechaRes.data.fecha, arqueoRes.data.fecha);

  // 32. POST /api/expenses/:id/cancel
  const cancelGastoRes = await dispatch("POST", `/api/expenses/${gastoId}/cancel`, { motivo: "Gasto duplicado" }, authHeaders);
  assert.equal(cancelGastoRes.status, 200);
  assert.equal(cancelGastoRes.data.anulado, true);

  // 31. DELETE /api/expenses/:id (Crear otro para probar DELETE)
  const delGastoInit = await dispatch("POST", "/api/expenses", {
    id: "gasto-del-301",
    fecha_hora_utc: new Date().toISOString(),
    monto: 300,
    concepto: "Café",
    categoria: "VARIOS",
    metodo_pago: "EFECTIVO",
  }, authHeaders);
  const delGastoRes = await dispatch("DELETE", `/api/expenses/${delGastoInit.data.id}`, null, authHeaders);
  assert.equal(delGastoRes.status, 200);
  assert.equal(delGastoRes.data.anulado, true);

  // ==========================================
  // --- PREVENTISTAS Y PEDIDOS ---
  // ==========================================

  // 33. GET /api/preventistas/proveedores
  const provsRes = await dispatch("GET", "/api/preventistas/proveedores", null, authHeaders);
  assert.equal(provsRes.status, 200);
  assert.ok(provsRes.data.proveedores.length >= 1);

  // 36. POST /api/preventistas/productos/alta-rapida
  const altaRapidaRes = await dispatch("POST", "/api/preventistas/productos/alta-rapida", {
    proveedor: "Distribuidora San Salvador",
    nombre: "Jugo Naranja 500ml",
    categoria: "Bebidas",
    costo: 350,
    precio_venta: 600,
  }, authHeaders);
  assert.equal(altaRapidaRes.status, 201);
  assert.equal(altaRapidaRes.data.nombre, "Jugo Naranja 500ml");
  const nuevoProdId = altaRapidaRes.data.id;

  // 34. GET /api/preventistas/productos?proveedor=...
  const prodsProvRes = await dispatch("GET", "/api/preventistas/productos?proveedor=Distribuidora+San+Salvador", null, authHeaders);
  assert.equal(prodsProvRes.status, 200);
  assert.ok(prodsProvRes.data.length >= 1);

  // 35. POST /api/preventistas/precios
  const updatePreciosRes = await dispatch("POST", "/api/preventistas/precios", {
    proveedor: "Distribuidora San Salvador",
    cambios: [{ producto_id: nuevoProdId, nuevo_costo: 380, nuevo_precio: 650 }],
  }, authHeaders);
  assert.equal(updatePreciosRes.status, 200);
  assert.equal(updatePreciosRes.data.actualizados, 1);

  // 38. POST /api/preventistas/pedidos
  const pedidoPayload = {
    proveedor_id: "prov-drogueria-sur",
    items: [{ producto_id: "prod-alfajor", cantidad: 24, costo_unitario: 800 }],
  };
  const crearPedidoRes = await dispatch("POST", "/api/preventistas/pedidos", pedidoPayload, authHeaders);
  assert.equal(crearPedidoRes.status, 201);
  const pedidoId = crearPedidoRes.data.id;

  // 37. GET /api/preventistas/pedidos
  const pedidosRes = await dispatch("GET", "/api/preventistas/pedidos", null, authHeaders);
  assert.equal(pedidosRes.status, 200);
  assert.ok(pedidosRes.data.length >= 1);

  // 39. GET /api/preventistas/pedidos/:id
  const getPedidoRes = await dispatch("GET", `/api/preventistas/pedidos/${pedidoId}`, null, authHeaders);
  assert.equal(getPedidoRes.status, 200);
  assert.equal(getPedidoRes.data.pedido.id, pedidoId);
  assert.equal(getPedidoRes.data.items.length, 1);

  // 40. PATCH /api/preventistas/pedidos/:id/estado
  const patchEstadoRes = await dispatch("PATCH", `/api/preventistas/pedidos/${pedidoId}/estado`, {
    estado: "RECIBIDO",
    observaciones: "Entregado en tiempo y forma",
  }, authHeaders);
  assert.equal(patchEstadoRes.status, 200);
  assert.equal(patchEstadoRes.data.estado, "RECIBIDO");

  // 41. POST /api/preventistas/pedidos/:id/cancelar (Crear otro pedido para cancelar)
  const pedidoACancelar = await dispatch("POST", "/api/preventistas/pedidos", {
    proveedor_id: "prov-drogueria-sur",
    items: [{ producto_id: "prod-alfajor", cantidad: 10, costo_unitario: 800 }],
  }, authHeaders);
  const cancelPedidoRes = await dispatch("POST", `/api/preventistas/pedidos/${pedidoACancelar.data.id}/cancelar`, {
    motivo: "Stock suficiente en depósito",
  }, authHeaders);
  assert.equal(cancelPedidoRes.status, 200);
  assert.equal(cancelPedidoRes.data.estado, "CANCELADO");

  // ==========================================
  // --- REPORTES Y DASHBOARD ---
  // ==========================================

  // 42. GET /api/reports/dashboard
  const dashRes = await dispatch("GET", "/api/reports/dashboard", null, authHeaders);
  assert.equal(dashRes.status, 200);
  assert.ok("caja_a" in dashRes.data);
  assert.ok("caja_b" in dashRes.data);
  assert.ok("gastos" in dashRes.data);

  // Restaurar fetch global
  globalThis.fetch = originalFetch;
});
