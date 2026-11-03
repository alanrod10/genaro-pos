/**
 * Enrutador HTTP unificado para Cloudflare Workers.
 * Mapea y atiende la totalidad de los 42 endpoints de la API de GenaroPOS más autenticación SCRAM.
 */

import crypto from "node:crypto";
import { ScramServer } from "../auth/scram.js";
import {
  obtenerResumenHistorico,
  obtenerVentasHistoricas,
  obtenerRankingProductosHistorico,
  obtenerServiciosHistoricos,
  obtenerTicketDetalleHistorico,
  buscarTicketsHistoricos,
  compararPeriodosHistoricos,
} from "../db/historical.js";
import {
  listarProductos,
  crearProducto,
  actualizarProducto,
  cambiarEstadoProducto,
  registrarVentaIdempotente,
  listarVentas,
  obtenerDetalleVenta,
  anularVenta,
  editarItemVenta,
  registrarCargaIdempotente,
  listarServicios,
  obtenerResumenServicios,
  obtenerCargaPorId,
  editarCargaServicio,
  anularCargaServicio,
  registrarGastoIdempotente,
  listarGastos,
  obtenerResumenGastos,
  obtenerGastoPorId,
  editarGasto,
  anularGasto,
  calcularArqueoDiario,
  listarCierres,
  obtenerCierrePorFecha,
  registrarCierreDiario,
  listarProveedores,
  listarProductosPorProveedor,
  altaRapidaProductoPreventista,
  actualizarPreciosPreventista,
  crearPedidoPreventista,
  listarPedidosPreventista,
  obtenerDetallePedido,
  cambiarEstadoPedido,
  calcularMetricasDashboard,
  comprobarReadinessEsquema,
} from "../db/adapter.js";

// --- Validación Fail-Closed de JWT_SECRET y Orígenes CORS ---

export function validarSecretKeyJwt(secretKey) {
  if (!secretKey) {
    throw new Error("CONFIG_ERROR: JWT_SECRET es obligatorio y no fue provisto en las variables de entorno.");
  }
  let buf;
  if (Buffer.isBuffer(secretKey)) {
    buf = secretKey;
  } else if (typeof secretKey === "string") {
    const trimmed = secretKey.trim();
    if (!trimmed) {
      throw new Error("CONFIG_ERROR: JWT_SECRET no puede estar vacío.");
    }
    buf = Buffer.from(trimmed, "utf8");
  } else if (secretKey instanceof Uint8Array) {
    buf = Buffer.from(secretKey);
  } else {
    throw new Error("CONFIG_ERROR: JWT_SECRET tiene un formato inválido.");
  }
  if (buf.length < 32) {
    throw new Error(
      `CONFIG_ERROR: JWT_SECRET debe tener al menos 32 bytes de longitud (recibido: ${buf.length} bytes).`
    );
  }
  return buf;
}

export function obtenerJwtSecretValidado(env) {
  return validarSecretKeyJwt(env?.JWT_SECRET);
}

const DEFAULT_LOOPBACK_ORIGINS = new Set([
  "http://localhost:5173",
  "http://127.0.0.1:5173",
  "http://localhost:4173",
  "http://127.0.0.1:4173",
  "http://localhost:3000",
  "http://127.0.0.1:3000",
  "http://localhost:8787",
  "http://127.0.0.1:8787",
]);

export function esOrigenCorsPermitido(origin, requestUrl, env) {
  if (!origin) return true;
  const normalized = String(origin).trim().replace(/\/+$/, "");
  if (DEFAULT_LOOPBACK_ORIGINS.has(normalized)) {
    return true;
  }
  if (/^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(normalized)) {
    return true;
  }
  try {
    const reqOrigin = new URL(requestUrl).origin;
    if (normalized === reqOrigin) {
      return true;
    }
  } catch {
    // ignore URL parse error
  }
  const extraRaw = env?.ALLOWED_ORIGINS || "";
  if (typeof extraRaw === "string" && extraRaw.trim()) {
    const permitidos = extraRaw
      .split(",")
      .map((s) => s.trim().replace(/\/+$/, ""))
      .filter(Boolean);
    if (permitidos.includes(normalized)) {
      return true;
    }
  }
  return false;
}

export function construirCabecerasCors(request, env) {
  const origin = request.headers.get("origin");
  const baseHeaders = {
    "Access-Control-Allow-Methods": "GET, POST, PATCH, PUT, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization, Idempotency-Key, Accept, X-Requested-With",
    Vary: "Origin",
  };
  if (!origin) {
    return baseHeaders;
  }
  if (esOrigenCorsPermitido(origin, request.url, env)) {
    return {
      ...baseHeaders,
      "Access-Control-Allow-Origin": origin,
      "Access-Control-Allow-Credentials": "true",
    };
  }
  // Origen no autorizado: no emitir Access-Control-Allow-Origin ni Access-Control-Allow-Credentials
  return baseHeaders;
}

// --- Emisión y Verificación de JWT con Web Crypto y Claims de Rol (RBAC) ---

export function generarJwt(username, role = "operador", secretKey, expiresInSeconds = 86400) {
  const keyBuf = validarSecretKeyJwt(secretKey);
  const header = { alg: "HS256", typ: "JWT" };
  const now = Math.floor(Date.now() / 1000);
  const payload = {
    sub: username,
    username,
    role: role || "operador",
    iat: now,
    exp: now + expiresInSeconds,
  };

  const b64Url = (obj) => Buffer.from(JSON.stringify(obj)).toString("base64url");
  const data = `${b64Url(header)}.${b64Url(payload)}`;
  const sig = crypto.createHmac("sha256", keyBuf).update(data).digest("base64url");
  return `${data}.${sig}`;
}

export function verificarJwt(token, secretKey) {
  const keyBuf = validarSecretKeyJwt(secretKey);
  if (!token) return null;
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  const [hB64, pB64, sig] = parts;

  // Validación estricta de encabezado (bloqueo de ataque alg: "none" o confusión de algoritmos)
  try {
    const header = JSON.parse(Buffer.from(hB64, "base64url").toString());
    if (header.alg !== "HS256" || header.typ !== "JWT") {
      return null;
    }
  } catch {
    return null;
  }

  const data = `${hB64}.${pB64}`;
  const expectedSig = crypto.createHmac("sha256", keyBuf).update(data).digest("base64url");

  const sigBuf = Buffer.from(sig);
  const expBuf = Buffer.from(expectedSig);
  if (sigBuf.length !== expBuf.length || !crypto.timingSafeEqual(sigBuf, expBuf)) {
    return null;
  }
  let payload;
  try {
    payload = JSON.parse(Buffer.from(pB64, "base64url").toString());
  } catch {
    return null;
  }
  if (!payload.exp || Math.floor(Date.now() / 1000) > payload.exp) {
    return null;
  }
  payload.role = payload.role || "operador";
  return payload;
}

// --- Manejador Principal de Peticiones del Worker ---

export async function handleRequest(request, env, dbClient, scramServer) {
  const url = new URL(request.url);
  const method = request.method.toUpperCase();
  const path = url.pathname.replace(/\/+$/, "") || "/";

  // 1. Cabeceras CORS seguras (restringidas a orígenes explícitos autorizados)
  const originHeader = request.headers.get("origin");
  const corsHeaders = construirCabecerasCors(request, env);

  if (method === "OPTIONS") {
    if (originHeader && !esOrigenCorsPermitido(originHeader, request.url, env)) {
      return new Response(JSON.stringify({ detail: "Origen CORS no autorizado." }), {
        status: 403,
        headers: { "Content-Type": "application/json", ...corsHeaders },
      });
    }
    return new Response(null, { status: 204, headers: corsHeaders });
  }

  const jsonResponse = (data, status = 200) =>
    new Response(JSON.stringify(data), {
      status,
      headers: { "Content-Type": "application/json", ...corsHeaders },
    });

  const errorResponse = (detail, status = 400) =>
    jsonResponse({ detail }, status);

  try {
    // --- 01. /health o / (Público) ---
    if (method === "GET" && (path === "/health" || path === "/")) {
      return jsonResponse({
        status: "ok",
        service: "genaro-pos-api",
        version: "0.2.0-candidate-workers",
        db_connectivity: "libsql_ready",
      });
    }

    // --- Autenticación SCRAM-SHA-256 (RFC 5802 / RFC 7677) ---
    if (method === "POST" && path === "/api/auth/scram-first") {
      const body = await request.json();
      const clientIp = request.headers.get("cf-connecting-ip") || "127.0.0.1";
      const res = await scramServer.processClientFirst(body.clientFirstMessage, clientIp);
      return jsonResponse(res);
    }

    if (method === "POST" && path === "/api/auth/scram-final") {
      const jwtSecret = obtenerJwtSecretValidado(env);
      const body = await request.json();
      const clientIp = request.headers.get("cf-connecting-ip") || "127.0.0.1";
      const res = await scramServer.processClientFinal(body.clientFinalMessage, body.stateToken, clientIp);

      const token = generarJwt(res.username, res.role, jwtSecret);
      return jsonResponse({
        serverFinalMessage: res.serverFinalMessage,
        access_token: token,
        token_type: "bearer",
        user: { username: res.username, role: res.role },
      });
    }

    // --- 02. POST /api/auth/login (Inhabilitado definitivamente por razones de seguridad) ---
    if (method === "POST" && path === "/api/auth/login") {
      return jsonResponse(
        {
          error: "AUTH_ENDPOINT_DEPRECATED",
          detail:
            "El endpoint legado /api/auth/login ha sido inhabilitado definitivamente por razones de seguridad. Debe autenticarse exclusivamente mediante SCRAM-SHA-256.",
        },
        410
      );
    }

    // --- Middleware de Autenticación y Autorización RBAC (Fail-Closed en JWT_SECRET) ---
    const jwtSecret = obtenerJwtSecretValidado(env);
    const authHeader = request.headers.get("Authorization");
    const token = authHeader && authHeader.startsWith("Bearer ") ? authHeader.slice(7) : null;
    const userPayload = verificarJwt(token, jwtSecret);

    if (!userPayload) {
      return errorResponse("No autenticado. Se requiere cabecera 'Authorization: Bearer <token>'.", 401);
    }
    const currentUser = userPayload.username;
    const currentRole = userPayload.role || "operador";

    // Función centralizada para verificación de permisos y roles (RBAC)
    const requireRole = (allowedRoles, operacionDescripcion = "esta operación") => {
      if (!allowedRoles.includes(currentRole)) {
        return errorResponse(
          `Acceso denegado: El rol '${currentRole}' no tiene autorización para ${operacionDescripcion}. Se requiere rol de Administrador.`,
          403
        );
      }
      return null;
    };

    // --- 03. GET /api/auth/me ---
    if (method === "GET" && path === "/api/auth/me") {
      return jsonResponse({ username: currentUser, role: currentRole });
    }

    // --- 04. GET /api/system/readiness ---
    if (method === "GET" && path === "/api/system/readiness") {
      const res = await comprobarReadinessEsquema(dbClient);
      return jsonResponse(res);
    }

    // ==========================================
    // --- CATÁLOGO ---
    // ==========================================

    // 05. GET /api/catalog
    if (method === "GET" && path === "/api/catalog") {
      const includeInactive = url.searchParams.get("include_inactive") === "true";
      const search = url.searchParams.get("search");
      const category = url.searchParams.get("category");
      const prods = await listarProductos(dbClient, { includeInactive, search, category });
      return jsonResponse({ total: prods.length, productos: prods });
    }

    // 06. POST /api/catalog
    if (method === "POST" && path === "/api/catalog") {
      const authErr = requireRole(["admin"], "dar de alta productos en el catálogo");
      if (authErr) return authErr;
      const body = await request.json();
      const res = await crearProducto(dbClient, { ...body, usuario: currentUser });
      return jsonResponse(res, 201);
    }

    // 08. PATCH /api/catalog/:id/status
    const matchCatalogStatus = path.match(/^\/api\/catalog\/([^/]+)\/status$/);
    if (method === "PATCH" && matchCatalogStatus) {
      const authErr = requireRole(["admin"], "modificar el estado activo/inactivo de productos");
      if (authErr) return authErr;
      const id = matchCatalogStatus[1];
      const body = await request.json();
      const res = await cambiarEstadoProducto(dbClient, id, body.activo, currentUser);
      return jsonResponse(res);
    }

    // 07. PATCH /api/catalog/:id
    const matchCatalogEdit = path.match(/^\/api\/catalog\/([^/]+)$/);
    if (method === "PATCH" && matchCatalogEdit) {
      const authErr = requireRole(["admin"], "editar datos de productos en el catálogo");
      if (authErr) return authErr;
      const id = matchCatalogEdit[1];
      const body = await request.json();
      const res = await actualizarProducto(dbClient, id, body, currentUser);
      if (!res) return errorResponse(`No se encontró el producto con ID ${id}.`, 404);
      return jsonResponse(res);
    }

    // ==========================================
    // --- VENTAS POS ---
    // ==========================================

    // 09. POST /api/sales
    if (method === "POST" && path === "/api/sales") {
      const body = await request.json();
      const idempotencyKey = request.headers.get("idempotency-key") || body.idempotency_key || null;
      const res = await registrarVentaIdempotente(dbClient, {
        ticket: body.ticket,
        items: body.items,
        usuario: currentUser,
        idempotencyKey,
      });
      return jsonResponse(res, res.status === "registrado" ? 201 : 200);
    }

    // 10. GET /api/sales
    if (method === "GET" && path === "/api/sales") {
      const fecha = url.searchParams.get("fecha");
      const ticketId = url.searchParams.get("ticket_id");
      const metodoPago = url.searchParams.get("metodo_pago");
      const busqueda = url.searchParams.get("busqueda");
      const incluirAnuladasParam = url.searchParams.get("incluir_anuladas");
      const incluirAnuladas = incluirAnuladasParam === null ? true : incluirAnuladasParam === "true";
      const limit = parseInt(url.searchParams.get("limit") || "50", 10);
      const ventas = await listarVentas(dbClient, {
        fecha,
        ticket_id: ticketId,
        metodo_pago: metodoPago,
        busqueda,
        incluir_anuladas: incluirAnuladas,
        limit,
      });
      return jsonResponse({ total: ventas.length, ventas });
    }

    // 12. POST /api/sales/:ticket_id/anular
    const matchSaleAnular = path.match(/^\/api\/sales\/([^/]+)\/anular$/);
    if (method === "POST" && matchSaleAnular) {
      const authErr = requireRole(["admin"], "anular tickets de venta");
      if (authErr) return authErr;
      const ticketId = decodeURIComponent(matchSaleAnular[1]);
      const body = await request.json();
      const res = await anularVenta(dbClient, ticketId, currentUser, body.motivo || "Anulación manual");
      return jsonResponse(res);
    }

    // 13. PUT / PATCH /api/sales/:ticket_id/items/:item_id
    const matchSaleItemEdit = path.match(/^\/api\/sales\/([^/]+)\/items\/([^/]+)$/);
    if ((method === "PUT" || method === "PATCH") && matchSaleItemEdit) {
      const authErr = requireRole(["admin"], "modificar ítems de ventas ya registradas");
      if (authErr) return authErr;
      const ticketId = decodeURIComponent(matchSaleItemEdit[1]);
      const itemId = matchSaleItemEdit[2];
      const body = await request.json();
      const res = await editarItemVenta(dbClient, ticketId, itemId, body, currentUser);
      return jsonResponse(res);
    }

    // 11. GET /api/sales/:ticket_id
    const matchSaleDetail = path.match(/^\/api\/sales\/([^/]+)$/);
    if (method === "GET" && matchSaleDetail) {
      const ticketId = decodeURIComponent(matchSaleDetail[1]);
      const res = await obtenerDetalleVenta(dbClient, ticketId);
      if (!res) return errorResponse("Venta no encontrada", 404);
      return jsonResponse(res);
    }

    // ==========================================
    // --- SERVICIOS ---
    // ==========================================

    // 15. GET /api/services/summary
    if (method === "GET" && path === "/api/services/summary") {
      const fecha = url.searchParams.get("fecha");
      const desde = url.searchParams.get("desde");
      const hasta = url.searchParams.get("hasta");
      const resumen = await obtenerResumenServicios(dbClient, { fecha, desde, hasta });
      return jsonResponse(resumen);
    }

    // 14. GET /api/services
    if (method === "GET" && path === "/api/services") {
      const fecha = url.searchParams.get("fecha");
      const tipo = url.searchParams.get("tipo_servicio") || url.searchParams.get("servicio");
      const incAnul = url.searchParams.get("incluir_anuladas") === "true";
      const cargas = await listarServicios(dbClient, { fecha, tipo, incluir_anuladas: incAnul });
      return jsonResponse({ total: cargas.length, cargas });
    }

    // 17. POST /api/services
    if (method === "POST" && path === "/api/services") {
      const body = await request.json();
      const idempotencyKey = request.headers.get("idempotency-key") || body.idempotency_key || null;
      const res = await registrarCargaIdempotente(dbClient, {
        servicio: body,
        usuario: currentUser,
        idempotencyKey,
      });
      return jsonResponse(res, res.status === "registrado" ? 201 : 200);
    }

    // 20. POST /api/services/:id/cancel y 19. DELETE /api/services/:id
    const matchServiceCancel = path.match(/^\/api\/services\/([^/]+)\/cancel$/);
    if (method === "POST" && matchServiceCancel) {
      const authErr = requireRole(["admin"], "anular cargas de servicios");
      if (authErr) return authErr;
      const cargaId = matchServiceCancel[1];
      const body = await request.json().catch(() => ({}));
      const res = await anularCargaServicio(dbClient, cargaId, currentUser, body.motivo || "Anulación manual");
      return jsonResponse(res);
    }

    const matchServiceDelete = path.match(/^\/api\/services\/([^/]+)$/);
    if (method === "DELETE" && matchServiceDelete) {
      const authErr = requireRole(["admin"], "anular cargas de servicios");
      if (authErr) return authErr;
      const cargaId = matchServiceDelete[1];
      const res = await anularCargaServicio(dbClient, cargaId, currentUser, "Anulación DELETE");
      return jsonResponse(res);
    }

    // 18. PATCH / PUT /api/services/:id
    if ((method === "PATCH" || method === "PUT") && matchServiceDelete) {
      const authErr = requireRole(["admin"], "editar cargas de servicios");
      if (authErr) return authErr;
      const cargaId = matchServiceDelete[1];
      const body = await request.json();
      const res = await editarCargaServicio(dbClient, cargaId, body, currentUser);
      if (!res) return errorResponse(`Carga con ID ${cargaId} no encontrada.`, 404);
      return jsonResponse(res);
    }

    // 16. GET /api/services/:id
    if (method === "GET" && matchServiceDelete) {
      const cargaId = matchServiceDelete[1];
      const res = await obtenerCargaPorId(dbClient, cargaId);
      if (!res) return errorResponse(`Carga con ID ${cargaId} no encontrada.`, 404);
      return jsonResponse(res);
    }

    // ==========================================
    // --- GASTOS Y ARQUEO ---
    // ==========================================

    // 22. GET /api/expenses/summary
    if (method === "GET" && path === "/api/expenses/summary") {
      const fecha = url.searchParams.get("fecha");
      const desde = url.searchParams.get("desde");
      const hasta = url.searchParams.get("hasta");
      const resumen = await obtenerResumenGastos(dbClient, { fecha, desde, hasta });
      return jsonResponse(resumen);
    }

    // 23. GET /api/expenses/arqueo
    if (method === "GET" && path === "/api/expenses/arqueo") {
      const authErr = requireRole(["admin"], "consultar el arqueo de caja");
      if (authErr) return authErr;
      const fecha = url.searchParams.get("fecha");
      const arqueo = await calcularArqueoDiario(dbClient, fecha);
      return jsonResponse(arqueo);
    }

    // 24. GET /api/expenses/dashboard
    if (method === "GET" && path === "/api/expenses/dashboard") {
      const authErr = requireRole(["admin"], "consultar el dashboard financiero");
      if (authErr) return authErr;
      const desde = url.searchParams.get("desde");
      const hasta = url.searchParams.get("hasta");
      const metricas = await calcularMetricasDashboard(dbClient, { desde, hasta });
      return jsonResponse(metricas);
    }

    // 25. GET /api/expenses/cierres
    if (method === "GET" && path === "/api/expenses/cierres") {
      const authErr = requireRole(["admin"], "consultar el historial de cierres de caja");
      if (authErr) return authErr;
      const cierres = await listarCierres(dbClient);
      return jsonResponse(cierres);
    }

    // 26. GET /api/expenses/cierres/:fecha
    const matchCierreFecha = path.match(/^\/api\/expenses\/cierres\/([^/]+)$/);
    if (method === "GET" && matchCierreFecha) {
      const authErr = requireRole(["admin"], "consultar detalles de cierres de caja");
      if (authErr) return authErr;
      const fecha = decodeURIComponent(matchCierreFecha[1]);
      const cierre = await obtenerCierrePorFecha(dbClient, fecha);
      if (!cierre) return errorResponse(`No hay cierre registrado para la fecha ${fecha}.`, 404);
      return jsonResponse(cierre);
    }

    // 27. POST /api/expenses/cierres
    if (method === "POST" && path === "/api/expenses/cierres") {
      const authErr = requireRole(["admin"], "ejecutar el cierre formal de caja");
      if (authErr) return authErr;
      const body = await request.json();
      const res = await registrarCierreDiario(dbClient, {
        ...body,
        cerrado_por: body.cerrado_por || currentUser,
      });
      return jsonResponse(res, 201);
    }

    // 21. GET /api/expenses
    if (method === "GET" && path === "/api/expenses") {
      const fecha = url.searchParams.get("fecha");
      const cat = url.searchParams.get("tipo");
      const metodoPago = url.searchParams.get("metodo_pago");
      const incAnul = url.searchParams.get("incluir_anulados") === "true";
      const gastos = await listarGastos(dbClient, {
        fecha,
        categoria: cat,
        metodo_pago: metodoPago,
        incluir_anulados: incAnul,
      });
      return jsonResponse({ total: gastos.length, gastos });
    }

    // 29. POST /api/expenses
    if (method === "POST" && path === "/api/expenses") {
      const body = await request.json();
      const idempotencyKey = request.headers.get("idempotency-key") || body.idempotency_key || null;
      const res = await registrarGastoIdempotente(dbClient, {
        gasto: body,
        usuario: currentUser,
        idempotencyKey,
      });
      return jsonResponse(res, res.status === "registrado" ? 201 : 200);
    }

    // 32. POST /api/expenses/:id/cancel
    const matchGastoCancel = path.match(/^\/api\/expenses\/([^/]+)\/cancel$/);
    if (method === "POST" && matchGastoCancel) {
      const authErr = requireRole(["admin"], "anular gastos");
      if (authErr) return authErr;
      const gastoId = matchGastoCancel[1];
      const body = await request.json().catch(() => ({}));
      const res = await anularGasto(dbClient, gastoId, currentUser, body.motivo || "Anulación manual");
      return jsonResponse(res);
    }

    // 31. DELETE /api/expenses/:id
    const matchGastoParam = path.match(/^\/api\/expenses\/([^/]+)$/);
    if (method === "DELETE" && matchGastoParam) {
      const authErr = requireRole(["admin"], "anular gastos");
      if (authErr) return authErr;
      const gastoId = matchGastoParam[1];
      const res = await anularGasto(dbClient, gastoId, currentUser, "Anulación DELETE");
      return jsonResponse(res);
    }

    // 30. PATCH / PUT /api/expenses/:id
    if ((method === "PATCH" || method === "PUT") && matchGastoParam) {
      const authErr = requireRole(["admin"], "editar gastos");
      if (authErr) return authErr;
      const gastoId = matchGastoParam[1];
      const body = await request.json();
      const res = await editarGasto(dbClient, gastoId, body, currentUser);
      if (!res) return errorResponse(`Gasto #${gastoId} no encontrado.`, 404);
      return jsonResponse(res);
    }

    // 28. GET /api/expenses/:id
    if (method === "GET" && matchGastoParam) {
      const gastoId = matchGastoParam[1];
      const res = await obtenerGastoPorId(dbClient, gastoId);
      if (!res) return errorResponse(`Gasto #${gastoId} no encontrado.`, 404);
      return jsonResponse(res);
    }

    // ==========================================
    // --- PREVENTISTAS Y PEDIDOS ---
    // ==========================================

    // 33. GET /api/preventistas/proveedores
    if (method === "GET" && path === "/api/preventistas/proveedores") {
      const provs = await listarProveedores(dbClient);
      return jsonResponse({ proveedores: provs });
    }

    // 34. GET /api/preventistas/productos
    if (method === "GET" && path === "/api/preventistas/productos") {
      const proveedor = url.searchParams.get("proveedor");
      if (!proveedor) return errorResponse("Parámetro 'proveedor' requerido", 400);
      const incluirInactivos = url.searchParams.get("incluir_inactivos") === "true";
      const prods = await listarProductosPorProveedor(dbClient, proveedor, incluirInactivos);
      return jsonResponse(prods);
    }

    // 35. POST /api/preventistas/precios
    if (method === "POST" && path === "/api/preventistas/precios") {
      const authErr = requireRole(["admin"], "actualizar masivamente precios de preventistas");
      if (authErr) return authErr;
      const body = await request.json();
      const res = await actualizarPreciosPreventista(dbClient, body.proveedor, body.cambios || [], currentUser);
      return jsonResponse(res);
    }

    // 36. POST /api/preventistas/productos/alta-rapida
    if (method === "POST" && path === "/api/preventistas/productos/alta-rapida") {
      const authErr = requireRole(["admin"], "dar de alta productos desde preventistas");
      if (authErr) return authErr;
      const body = await request.json();
      const res = await altaRapidaProductoPreventista(dbClient, body, currentUser);
      return jsonResponse(res, 201);
    }

    // 37. GET /api/preventistas/pedidos
    if (method === "GET" && path === "/api/preventistas/pedidos") {
      const proveedor = url.searchParams.get("proveedor");
      const estado = url.searchParams.get("estado");
      const pedidos = await listarPedidosPreventista(dbClient, { proveedor, estado });
      return jsonResponse(pedidos);
    }

    // 38. POST /api/preventistas/pedidos
    if (method === "POST" && path === "/api/preventistas/pedidos") {
      const body = await request.json();
      const res = await crearPedidoPreventista(dbClient, { ...body, usuario: currentUser });
      return jsonResponse(res, 201);
    }

    // 41. POST /api/preventistas/pedidos/:id/cancelar
    const matchPedidoCancel = path.match(/^\/api\/preventistas\/pedidos\/([^/]+)\/cancelar$/);
    if (method === "POST" && matchPedidoCancel) {
      const authErr = requireRole(["admin"], "cancelar pedidos a preventistas");
      if (authErr) return authErr;
      const pedidoId = decodeURIComponent(matchPedidoCancel[1]);
      const body = await request.json().catch(() => ({}));
      const res = await cambiarEstadoPedido(dbClient, pedidoId, "CANCELADO", body.motivo || "Cancelación manual", currentUser);
      return jsonResponse(res);
    }

    // 40. PATCH /api/preventistas/pedidos/:id/estado
    const matchPedidoEstado = path.match(/^\/api\/preventistas\/pedidos\/([^/]+)\/estado$/);
    if (method === "PATCH" && matchPedidoEstado) {
      const authErr = requireRole(["admin"], "modificar el estado de pedidos a preventistas");
      if (authErr) return authErr;
      const pedidoId = decodeURIComponent(matchPedidoEstado[1]);
      const body = await request.json();
      const res = await cambiarEstadoPedido(dbClient, pedidoId, body.estado, body.observaciones || "", currentUser);
      return jsonResponse(res);
    }

    // 39. GET /api/preventistas/pedidos/:id
    const matchPedidoDetail = path.match(/^\/api\/preventistas\/pedidos\/([^/]+)$/);
    if (method === "GET" && matchPedidoDetail) {
      const pedidoId = decodeURIComponent(matchPedidoDetail[1]);
      const res = await obtenerDetallePedido(dbClient, pedidoId);
      if (!res) return errorResponse(`No se encontró el pedido ${pedidoId}.`, 404);
      return jsonResponse(res);
    }

    // ==========================================
    // --- REPORTES Y DASHBOARD ---
    // ==========================================

    // 42. GET /api/reports/dashboard
    if (method === "GET" && path === "/api/reports/dashboard") {
      const authErr = requireRole(["admin"], "consultar el dashboard financiero y reportes gerenciales");
      if (authErr) return authErr;
      const desde = url.searchParams.get("desde");
      const hasta = url.searchParams.get("hasta");
      const metricas = await calcularMetricasDashboard(dbClient, { desde, hasta });
      return jsonResponse(metricas);
    }

    // ==========================================
    // --- MÓDULO HISTÓRICO (AUDITORÍA 2025-2026) ---
    // ==========================================

    // 43. GET /api/historical/summary
    if (method === "GET" && path === "/api/historical/summary") {
      const data = await obtenerResumenHistorico(dbClient);
      return jsonResponse(data);
    }

    // 44. GET /api/historical/sales
    if (method === "GET" && path === "/api/historical/sales") {
      const desde = url.searchParams.get("desde");
      const hasta = url.searchParams.get("hasta");
      const metodo_pago = url.searchParams.get("metodo_pago");
      const agrupacion = url.searchParams.get("agrupacion") || "mes";
      const limit = url.searchParams.get("limit") ? parseInt(url.searchParams.get("limit"), 10) : 50;
      const offset = url.searchParams.get("offset") ? parseInt(url.searchParams.get("offset"), 10) : 0;
      const data = await obtenerVentasHistoricas(dbClient, { desde, hasta, metodo_pago, agrupacion, limit, offset });
      return jsonResponse(data);
    }

    // 45. GET /api/historical/products
    if (method === "GET" && path === "/api/historical/products") {
      const desde = url.searchParams.get("desde");
      const hasta = url.searchParams.get("hasta");
      const depuradoStr = url.searchParams.get("depurado");
      const depurado = depuradoStr !== null ? depuradoStr === "true" || depuradoStr === "1" : true;
      const limit = url.searchParams.get("limit") ? parseInt(url.searchParams.get("limit"), 10) : 50;
      const offset = url.searchParams.get("offset") ? parseInt(url.searchParams.get("offset"), 10) : 0;
      const data = await obtenerRankingProductosHistorico(dbClient, { desde, hasta, depurado, limit, offset });
      return jsonResponse(data);
    }

    // 46. GET /api/historical/services
    if (method === "GET" && path === "/api/historical/services") {
      const desde = url.searchParams.get("desde");
      const hasta = url.searchParams.get("hasta");
      const excDupStr = url.searchParams.get("excluir_duplicados");
      const excluir_duplicados = excDupStr === "true" || excDupStr === "1";
      const data = await obtenerServiciosHistoricos(dbClient, { desde, hasta, excluir_duplicados });
      return jsonResponse(data);
    }

    // 47. GET /api/historical/tickets
    if (method === "GET" && path === "/api/historical/tickets") {
      const query = url.searchParams.get("query") || url.searchParams.get("q");
      const fecha = url.searchParams.get("fecha");
      const limit = url.searchParams.get("limit") ? parseInt(url.searchParams.get("limit"), 10) : 20;
      const offset = url.searchParams.get("offset") ? parseInt(url.searchParams.get("offset"), 10) : 0;
      const data = await buscarTicketsHistoricos(dbClient, { query, fecha, limit, offset });
      return jsonResponse(data);
    }

    // 48. GET /api/historical/tickets/:ticket_id
    const matchHistTicketDetail = path.match(/^\/api\/historical\/tickets\/([^/]+)$/);
    if (method === "GET" && matchHistTicketDetail) {
      const ticketId = decodeURIComponent(matchHistTicketDetail[1]);
      const data = await obtenerTicketDetalleHistorico(dbClient, ticketId);
      if (!data) return errorResponse(`Ticket histórico '${ticketId}' no encontrado.`, 404);
      return jsonResponse(data);
    }

    // 49. GET /api/historical/compare
    if (method === "GET" && path === "/api/historical/compare") {
      const desde_a = url.searchParams.get("desde_a");
      const hasta_a = url.searchParams.get("hasta_a");
      const desde_b = url.searchParams.get("desde_b");
      const hasta_b = url.searchParams.get("hasta_b");
      if (!desde_a || !hasta_a || !desde_b || !hasta_b) {
        return errorResponse("Parámetros 'desde_a', 'hasta_a', 'desde_b' y 'hasta_b' son obligatorios.", 400);
      }
      const data = await compararPeriodosHistoricos(dbClient, { desde_a, hasta_a, desde_b, hasta_b });
      return jsonResponse(data);
    }

    return errorResponse(`Ruta no encontrada: ${method} ${path}`, 404);
  } catch (err) {
    const msg = err?.message || "Error interno";
    const isConflict = msg.includes("CONFLICT") || msg.includes("UNIQUE constraint");
    const isInvalidInput = msg.includes("INVALID_INPUT") || msg.includes("CHECK constraint");
    const isNotFound = msg.includes("NOT_FOUND");
    const isAuth =
      msg.includes("Credenciales") ||
      msg.includes("Replay") ||
      msg.includes("expirado") ||
      msg.includes("Nonce mismatch");
    const isStoreError = msg.includes("PERSISTENT_STORE_") || msg.includes("DATABASE_CLIENT_REQUIRED");
    const isConfigError = msg.includes("CONFIG_ERROR") || msg.includes("CONFIGURATION_ERROR");

    if (isConfigError) {
      return errorResponse("Error de configuración del servicio de autenticación.", 500);
    }
    if (isStoreError) {
      return errorResponse("Servicio de autenticación no disponible temporalmente. Intente nuevamente más tarde.", 503);
    }
    if (isNotFound) {
      return errorResponse(msg, 404);
    }
    if (isInvalidInput) {
      return errorResponse(msg, 400);
    }
    const status = isConflict ? 409 : (isAuth ? 401 : 500);
    return errorResponse(msg, status);
  }
}
