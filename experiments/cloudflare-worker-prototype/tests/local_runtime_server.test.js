/**
 * Validación del Runtime Local del Cloudflare Worker (Fase Preproducción Segura).
 * 
 * Ejecuta el Worker localmente mediante un servidor HTTP loopback (127.0.0.1)
 * conectado exclusivamente a una base de datos libSQL en memoria (:memory:),
 * con variables y secretos sintéticos, sin ningún acceso ni dependencia remota.
 */

import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import crypto from "node:crypto";
import { createClient } from "@libsql/client";
import worker, { resetWorkerSingletonsForTest } from "../src/worker.js";
import { inicializarEsquemaCompleto, poblarDatosPrueba } from "../src/db/adapter.js";
import { convertirPbkdf2HashAScramVerifier } from "../src/auth/scram.js";

// Helper para convertir peticiones de node:http a Web Request estándar
async function createWebRequestFromNode(req, port) {
  const url = `http://127.0.0.1:${port}${req.url}`;
  const headers = new Headers();
  for (const [k, v] of Object.entries(req.headers)) {
    if (v !== undefined) {
      if (Array.isArray(v)) {
        for (const item of v) headers.append(k, item);
      } else {
        headers.set(k, v);
      }
    }
  }

  const method = (req.method || "GET").toUpperCase();
  let body = undefined;
  if (method !== "GET" && method !== "HEAD") {
    const chunks = [];
    for await (const chunk of req) {
      chunks.push(chunk);
    }
    body = Buffer.concat(chunks);
  }

  return new Request(url, {
    method,
    headers,
    body,
    duplex: body ? "half" : undefined,
  });
}

test("Validación de Runtime Local: Servidor Loopback, Health, Readiness, SCRAM, CORS, RBAC e Idempotencia", async () => {
  resetWorkerSingletonsForTest();

  // 1. Base de datos sintética en memoria (:memory:)
  const dbClient = createClient({ url: ":memory:" });
  await inicializarEsquemaCompleto(dbClient);
  await poblarDatosPrueba(dbClient);

  // 2. Credenciales sintéticas de prueba para SCRAM
  const USER_ADMIN = "admin_sintetico";
  const PASS_ADMIN = "ClaveAdminSintetica2026!";
  const saltAdmin = crypto.randomBytes(16);
  const saltedAdmin = crypto.pbkdf2Sync(PASS_ADMIN, saltAdmin, 4096, 32, "sha256");
  const storedHashAdmin = `pbkdf2_sha256$4096$${saltAdmin.toString("hex")}$${saltedAdmin.toString("hex")}`;
  const verifierAdmin = convertirPbkdf2HashAScramVerifier(storedHashAdmin);
  verifierAdmin.role = "admin";

  const USER_OPERADOR = "cajero_sintetico";
  const PASS_OPERADOR = "ClaveCajeroSintetica2026!";
  const saltOp = crypto.randomBytes(16);
  const saltedOp = crypto.pbkdf2Sync(PASS_OPERADOR, saltOp, 4096, 32, "sha256");
  const storedHashOp = `pbkdf2_sha256$4096$${saltOp.toString("hex")}$${saltedOp.toString("hex")}`;
  const verifierOp = convertirPbkdf2HashAScramVerifier(storedHashOp);
  verifierOp.role = "operador";

  const serverSecret = crypto.randomBytes(32);
  const jwtSecret = crypto.randomBytes(32);

  let workerEnv = {
    JWT_SECRET: jwtSecret,
    SCRAM_SERVER_SECRET: serverSecret.toString("hex"),
    SCRAM_VERIFIERS_JSON: JSON.stringify({
      [USER_ADMIN]: verifierAdmin,
      [USER_OPERADOR]: verifierOp,
    }),
    ALLOWED_ORIGINS: "http://localhost:5173",
    DB_CLIENT: dbClient,
  };

  // 3. Servidor HTTP loopback real en 127.0.0.1 (puerto dinámico 0)
  const server = http.createServer(async (req, res) => {
    try {
      const port = server.address().port;
      const webReq = await createWebRequestFromNode(req, port);
      const webRes = await worker.fetch(webReq, workerEnv, {});

      res.statusCode = webRes.status;
      for (const [k, v] of webRes.headers.entries()) {
        res.setHeader(k, v);
      }
      const arrayBuf = await webRes.arrayBuffer();
      res.end(Buffer.from(arrayBuf));
    } catch (err) {
      res.statusCode = 500;
      res.setHeader("Content-Type", "application/json");
      res.end(JSON.stringify({ detail: "Error no controlado en runtime local." }));
    }
  });

  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const serverPort = server.address().port;
  const baseUrl = `http://127.0.0.1:${serverPort}`;

  try {
    // --- 01. Arranque sin errores y endpoint de health ---
    const healthRes = await fetch(`${baseUrl}/health`);
    assert.equal(healthRes.status, 200, "GET /health debe responder 200");
    const healthData = await healthRes.json();
    assert.equal(healthData.status, "ok");
    assert.equal(healthData.service, "genaro-pos-api");
    assert.equal(healthData.db_connectivity, "libsql_ready");

    // --- 02. Error seguro ante ausencia de secretos obligatorios (Fail-Closed) ---
    const envSinSecret = { ...workerEnv, JWT_SECRET: undefined };
    const savedEnv = workerEnv;
    workerEnv = envSinSecret;
    resetWorkerSingletonsForTest();

    const missingSecretRes = await fetch(`${baseUrl}/api/catalog`);
    assert.equal(missingSecretRes.status, 500, "Debe fallar 500 sanitizado ante JWT_SECRET faltante");
    const missingSecretData = await missingSecretRes.json();
    assert.equal(missingSecretData.detail, "Error de configuración del servicio de autenticación.");
    // Verificar que no se filtren nombres de variables ni rutas
    assert.equal(JSON.stringify(missingSecretData).includes("JWT_SECRET"), false);

    // --- 02b. Error seguro ante ausencia de TURSO_DATABASE_URL (Sin fallback a staging remoto) ---
    const { getDbClient } = await import("../src/worker.js");
    assert.throws(
      () => getDbClient({}),
      /CONFIG_ERROR: TURSO_DATABASE_URL es obligatoria/,
      "getDbClient sin DB_CLIENT ni TURSO_DATABASE_URL debe fallar cerrado arrojando CONFIG_ERROR"
    );

    const envSinDb = { ...workerEnv, DB_CLIENT: undefined, TURSO_DATABASE_URL: undefined };
    workerEnv = envSinDb;
    resetWorkerSingletonsForTest();

    const missingDbRes = await fetch(`${baseUrl}/health`);
    assert.equal(missingDbRes.status, 500, "Debe fallar 500 sanitizado ante TURSO_DATABASE_URL faltante");
    const missingDbData = await missingDbRes.json();
    assert.equal(missingDbData.detail, "Error de configuración del servicio de base de datos.");
    assert.equal(JSON.stringify(missingDbData).includes("TURSO_DATABASE_URL"), false);

    // Restaurar env completo
    workerEnv = savedEnv;
    resetWorkerSingletonsForTest();

    // --- 03. Rechazo de orígenes CORS no autorizados ---
    const corsPreflightReject = await fetch(`${baseUrl}/api/sales`, {
      method: "OPTIONS",
      headers: {
        Origin: "https://sitio-malicioso-externo.com",
        "Access-Control-Request-Method": "POST",
      },
    });
    assert.equal(corsPreflightReject.status, 403, "OPTIONS desde origen no autorizado debe retornar 403");
    assert.equal(corsPreflightReject.headers.get("access-control-allow-origin"), null);

    const corsPreflightAllow = await fetch(`${baseUrl}/api/sales`, {
      method: "OPTIONS",
      headers: {
        Origin: "http://localhost:5173",
        "Access-Control-Request-Method": "POST",
      },
    });
    assert.equal(corsPreflightAllow.status, 204, "OPTIONS desde origen permitido debe retornar 204");
    assert.equal(corsPreflightAllow.headers.get("access-control-allow-origin"), "http://localhost:5173");

    // --- 04. Respuesta controlada ante base de datos no disponible ---
    const envBrokenDb = {
      ...workerEnv,
      DB_CLIENT: {
        execute: async () => {
          throw new Error("DATABASE_CLIENT_REQUIRED: Conexión rechazada por base de datos");
        },
        batch: async () => {
          throw new Error("DATABASE_CLIENT_REQUIRED: Conexión rechazada por base de datos");
        },
      },
    };
    workerEnv = envBrokenDb;
    resetWorkerSingletonsForTest();

    const dbDownRes = await fetch(`${baseUrl}/api/auth/scram-first`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clientFirstMessage: "n,,n=admin_sintetico,r=cNonceTest12345678" }),
    });
    assert.equal(dbDownRes.status, 503, "Base de datos inalcanzable debe retornar 503 sanitizado");
    const dbDownData = await dbDownRes.json();
    assert.ok(dbDownData.detail.includes("Servicio de autenticación no disponible temporalmente"));

    // Restaurar env operativo
    workerEnv = savedEnv;
    resetWorkerSingletonsForTest();

    // --- 05. Autenticación SCRAM-SHA-256 completa de usuario sintético ---
    // Paso 1: Client First
    const cNonce = crypto.randomBytes(16).toString("base64");
    const clientFirstBare = `n=${USER_ADMIN},r=${cNonce}`;
    const clientFirstMessage = `n,,${clientFirstBare}`;

    const firstRes = await fetch(`${baseUrl}/api/auth/scram-first`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clientFirstMessage }),
    });
    assert.equal(firstRes.status, 200, "POST /api/auth/scram-first debe responder 200");
    const firstData = await firstRes.json();
    assert.ok(firstData.serverFirstMessage);
    assert.ok(firstData.stateToken);

    // Extraer parámetros server-first
    const sfParams = new Map();
    for (const part of firstData.serverFirstMessage.split(",")) {
      const idx = part.indexOf("=");
      if (idx !== -1) sfParams.set(part.slice(0, idx), part.slice(idx + 1));
    }
    const rNonce = sfParams.get("r");
    const saltB64 = sfParams.get("s");
    const iter = parseInt(sfParams.get("i"), 10);

    assert.ok(rNonce.startsWith(cNonce), "rNonce debe comenzar con cNonce");

    // Paso 2: PBKDF2 del cliente y cálculo de prueba
    const saltedPassword = crypto.pbkdf2Sync(PASS_ADMIN, Buffer.from(saltB64, "base64"), iter, 32, "sha256");
    const clientKey = crypto.createHmac("sha256", saltedPassword).update("Client Key").digest();
    const storedKey = crypto.createHash("sha256").update(clientKey).digest();
    const serverKey = crypto.createHmac("sha256", saltedPassword).update("Server Key").digest();

    const clientFinalWithoutProof = `c=biws,r=${rNonce}`;
    const authMessage = `${clientFirstBare},${firstData.serverFirstMessage},${clientFinalWithoutProof}`;
    const clientSig = crypto.createHmac("sha256", storedKey).update(authMessage).digest();

    const clientProof = Buffer.alloc(32);
    for (let i = 0; i < 32; i++) {
      clientProof[i] = clientKey[i] ^ clientSig[i];
    }
    const clientFinalMessage = `${clientFinalWithoutProof},p=${clientProof.toString("base64")}`;

    // Paso 3: Client Final y obtención de JWT
    const finalRes = await fetch(`${baseUrl}/api/auth/scram-final`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clientFinalMessage, stateToken: firstData.stateToken }),
    });
    assert.equal(finalRes.status, 200, "POST /api/auth/scram-final debe responder 200");
    const finalData = await finalRes.json();
    assert.ok(finalData.access_token, "Debe retornar un access_token");
    assert.equal(finalData.user.username, USER_ADMIN);
    assert.equal(finalData.user.role, "admin");

    // Verificación de firma mutua del servidor (Server Signature)
    const expectedServerSig = crypto.createHmac("sha256", serverKey).update(authMessage).digest("base64");
    assert.equal(finalData.serverFinalMessage, `v=${expectedServerSig}`, "Firma mutua del servidor debe ser válida");

    const tokenAdmin = finalData.access_token;

    // --- 06. Rechazo de tokens manipulados / inválidos ---
    const badTokenRes = await fetch(`${baseUrl}/api/catalog`, {
      headers: { Authorization: "Bearer token.manipulado.invalido" },
    });
    assert.equal(badTokenRes.status, 401, "Token manipulado debe ser rechazado con 401");

    // --- 07. Endpoint de readiness (comprueba migraciones 001..011 y auth_scram_challenges) ---
    const readinessRes = await fetch(`${baseUrl}/api/system/readiness`, {
      headers: { Authorization: `Bearer ${tokenAdmin}` },
    });
    assert.equal(readinessRes.status, 200, "GET /api/system/readiness debe responder 200");
    const readinessData = await readinessRes.json();
    assert.equal(readinessData.status, "PREPARADO");
    assert.equal(readinessData.listo, true);
    assert.deepEqual(readinessData.versiones_aplicadas, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]);
    assert.equal(readinessData.tablas_faltantes.length, 0);

    // --- 08. Autorización por rol (RBAC) ---
    // Generar token para rol "operador"
    const { generarJwt } = await import("../src/api/router.js");
    const tokenOperador = generarJwt(USER_OPERADOR, "operador", jwtSecret);

    const rbacDeniedRes = await fetch(`${baseUrl}/api/reports/dashboard`, {
      headers: { Authorization: `Bearer ${tokenOperador}` },
    });
    assert.equal(rbacDeniedRes.status, 403, "Rol operador no debe tener acceso a /api/reports/dashboard (403)");

    const rbacAllowedRes = await fetch(`${baseUrl}/api/reports/dashboard`, {
      headers: { Authorization: `Bearer ${tokenAdmin}` },
    });
    assert.equal(rbacAllowedRes.status, 200, "Rol admin debe tener acceso a /api/reports/dashboard (200)");

    // --- 09. Registro idempotente de una venta sintética y consistencia contable ---
    const ticketId = "T-SYNTH-LOCAL-001";
    const ventaPayload = {
      ticket: {
        ticket_id: ticketId,
        fecha: "29/10/2026 12:00:00",
        fecha_hora_utc: new Date().toISOString(),
        total_venta: 3700,
        metodo_pago: "MIXTO",
        monto_efectivo: 2000,
        monto_transf: 1700,
      },
      items: [
        {
          producto: "Alfajor Chocolate",
          cantidad: 1,
          unidad: "Unidad",
          precio_unit: 1500,
          subtotal: 1500,
          costo_unitario: 920,
        },
        {
          producto: "Paracetamol 500mg",
          cantidad: 1,
          unidad: "Unidad",
          precio_unit: 2200,
          subtotal: 2200,
          costo_unitario: 1400,
        },
      ],
      idempotency_key: "idem-synth-sale-001",
    };

    const primeraVentaRes = await fetch(`${baseUrl}/api/sales`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${tokenAdmin}`,
        "Idempotency-Key": "idem-synth-sale-001",
      },
      body: JSON.stringify(ventaPayload),
    });
    assert.equal(primeraVentaRes.status, 201, "Primera emisión de venta debe retornar 201");
    const primeraVentaData = await primeraVentaRes.json();
    assert.equal(primeraVentaData.ticket_id, ticketId);
    assert.equal(primeraVentaData.total_venta, 3700);
    assert.equal(primeraVentaData.monto_efectivo, 2000);
    assert.equal(primeraVentaData.monto_transferencia, 1700);
    assert.equal(primeraVentaData.total_venta, primeraVentaData.monto_efectivo + primeraVentaData.monto_transferencia);

    // Reintento idempotente con la misma clave
    const replayVentaRes = await fetch(`${baseUrl}/api/sales`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${tokenAdmin}`,
        "Idempotency-Key": "idem-synth-sale-001",
      },
      body: JSON.stringify(ventaPayload),
    });
    assert.equal(replayVentaRes.status, 200, "Reintento de venta idempotente debe retornar 200");
    const replayVentaData = await replayVentaRes.json();
    assert.equal(replayVentaData.ticket_id, ticketId);

    // --- 10. Consulta de la venta registrada ---
    const getVentaRes = await fetch(`${baseUrl}/api/sales/${ticketId}`, {
      headers: { Authorization: `Bearer ${tokenAdmin}` },
    });
    assert.equal(getVentaRes.status, 200, "GET /api/sales/:id debe responder 200");
    const getVentaData = await getVentaRes.json();
    assert.equal(getVentaData.ticket_id, ticketId);
    assert.equal(getVentaData.items.length, 2);
    assert.equal(getVentaData.items[0].producto_nombre, "Alfajor Chocolate");
    assert.equal(getVentaData.items[0].costo_unitario, 920, "Debe preservar el snapshot de costo histórico");

    // --- 11. Respuestas sanitizadas ante errores de validación ---
    const badInputRes = await fetch(`${baseUrl}/api/sales`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${tokenAdmin}`,
      },
      body: JSON.stringify({
        ticket: {
          ticket_id: "T-BAD-SUBTOTAL",
          total_venta: 5000,
          metodo_pago: "EFECTIVO",
          monto_efectivo: 5000,
          monto_transf: 0,
        },
        items: [{ producto: "Alfajor Chocolate", cantidad: 1, precio_unit: 1500, subtotal: 1500 }],
      }),
    });
    assert.equal(badInputRes.status, 400, "Entrada inválida debe retornar 400");
    const badInputData = await badInputRes.json();
    assert.ok(badInputData.detail.includes("INVALID_INPUT"));
    // Sin fugas de stack trace
    assert.equal("stack" in badInputData, false);
  } finally {
    // 4. Cierre garantizado del servidor loopback
    await new Promise((resolve) => server.close(resolve));
    dbClient.close();
  }
});
