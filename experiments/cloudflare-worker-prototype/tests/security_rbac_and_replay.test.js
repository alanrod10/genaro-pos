/**
 * Suite de Pruebas de Seguridad: RBAC, Anti-Replay Distribuido y Mitigación de Login Inseguro.
 * Verifica formalmente las tres prioridades de remediación de seguridad.
 */

import test from "node:test";
import assert from "node:assert/strict";
import crypto from "node:crypto";
import { createClient } from "@libsql/client";
import { inicializarEsquemaCompleto, poblarDatosPrueba } from "../src/db/adapter.js";
import {
  ScramServer,
  convertirPbkdf2HashAScramVerifier,
  LibsqlChallengeStore,
  HybridChallengeStore,
} from "../src/auth/scram.js";
import { handleRequest, generarJwt } from "../src/api/router.js";

test("Seguridad: Inhabilitación definitiva del endpoint legado /api/auth/login", async () => {
  const dbClient = createClient({ url: ":memory:" });
  await inicializarEsquemaCompleto(dbClient);

  const scramServer = new ScramServer(new Map(), crypto.randomBytes(32));
  const env = { JWT_SECRET: crypto.randomBytes(32) };

  const req = new Request("http://127.0.0.1:8787/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: "alan", password: "cualquier_password" }),
  });

  const resp = await handleRequest(req, env, dbClient, scramServer);
  const data = await resp.json();

  assert.equal(resp.status, 410, "El endpoint debe retornar HTTP 410 Gone");
  assert.equal(data.error, "AUTH_ENDPOINT_DEPRECATED");
  assert.equal(data.access_token, undefined, "Nunca debe entregar un token JWT");
});

test("Seguridad: Integridad y validación estricta de JWT (algoritmo, expiración y firma)", async () => {
  const dbClient = createClient({ url: ":memory:" });
  await inicializarEsquemaCompleto(dbClient);

  const scramServer = new ScramServer(new Map(), crypto.randomBytes(32));
  const jwtSecret = crypto.randomBytes(32);
  const env = { JWT_SECRET: jwtSecret };

  const dispatch = async (token) => {
    const req = new Request("http://127.0.0.1:8787/api/auth/me", {
      method: "GET",
      headers: { Authorization: `Bearer ${token}` },
    });
    const resp = await handleRequest(req, env, dbClient, scramServer);
    return { status: resp.status, data: await resp.json().catch(() => ({})) };
  };

  // 1. Token legítimo emitido para operador
  const validToken = generarJwt("cajero_1", "operador", jwtSecret, 3600);
  const okRes = await dispatch(validToken);
  assert.equal(okRes.status, 200);
  assert.equal(okRes.data.username, "cajero_1");
  assert.equal(okRes.data.role, "operador");

  // 2. Token expirado
  const expiredToken = generarJwt("cajero_1", "operador", jwtSecret, -10);
  const expRes = await dispatch(expiredToken);
  assert.equal(expRes.status, 401, "Token expirado debe rechazarse con 401");

  // 3. Ataque de manipulación: Modificar rol a 'admin' sin poseer la clave secreta
  const parts = validToken.split(".");
  const payload = JSON.parse(Buffer.from(parts[1], "base64url").toString());
  payload.role = "admin";
  const tamperedPayloadB64 = Buffer.from(JSON.stringify(payload)).toString("base64url");
  const tamperedToken = `${parts[0]}.${tamperedPayloadB64}.${parts[2]}`;
  const tamperedRes = await dispatch(tamperedToken);
  assert.equal(tamperedRes.status, 401, "Token con payload manipulado debe rechazarse con 401");

  // 4. Ataque de algoritmo 'none'
  const noneHeaderB64 = Buffer.from(JSON.stringify({ alg: "none", typ: "JWT" })).toString("base64url");
  const noneToken = `${noneHeaderB64}.${parts[1]}.`;
  const noneRes = await dispatch(noneToken);
  assert.equal(noneRes.status, 401, "Token con alg: none debe rechazarse con 401");

  // 5. Token firmado con clave secreta diferente
  const wrongKeyToken = generarJwt("cajero_1", "operador", crypto.randomBytes(32), 3600);
  const wrongKeyRes = await dispatch(wrongKeyToken);
  assert.equal(wrongKeyRes.status, 401, "Token con clave foránea debe rechazarse con 401");
});

test("Seguridad: Matriz de Control de Acceso por Roles (RBAC) en Backend", async () => {
  const dbClient = createClient({ url: ":memory:" });
  await inicializarEsquemaCompleto(dbClient);
  await poblarDatosPrueba(dbClient);

  const jwtSecret = crypto.randomBytes(32);
  const serverSecret = crypto.randomBytes(32);
  const env = { JWT_SECRET: jwtSecret, SCRAM_SERVER_SECRET: serverSecret };

  // Tokens de prueba con roles de confianza firmados por el servidor
  const adminToken = generarJwt("alan_admin", "admin", jwtSecret, 3600);
  const operadorToken = generarJwt("yilda_operador", "operador", jwtSecret, 3600);

  const scramServer = new ScramServer(new Map(), serverSecret);

  const apiCall = async (method, path, body = null, token = operadorToken) => {
    const init = {
      method,
      headers: new Headers({
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      }),
    };
    if (body) init.body = JSON.stringify(body);
    const req = new Request(`http://127.0.0.1:8787${path}`, init);
    const resp = await handleRequest(req, env, dbClient, scramServer);
    return { status: resp.status, data: await resp.json().catch(() => ({})) };
  };

  // --- A. Operaciones permitidas para OPERADOR (Flujo diario normal) ---
  const catRes = await apiCall("GET", "/api/catalog", null, operadorToken);
  assert.equal(catRes.status, 200, "Operador puede leer catálogo");

  const saleRes = await apiCall("POST", "/api/sales", {
    ticket: {
      id: "ticket-op-test-1",
      fecha_hora_utc: new Date().toISOString(),
      total_venta: 1000,
      monto_efectivo: 1000,
      monto_transferencia: 0,
      metodo_pago: "EFECTIVO",
    },
    items: [{ producto_nombre: "Gaseosa Cola", cantidad: 1, precio_unitario: 1000 }],
  }, operadorToken);
  assert.equal(saleRes.status, 201, "Operador puede cobrar ventas");

  const servRes = await apiCall("POST", "/api/services", {
    fecha_hora_utc: new Date().toISOString(),
    servicio: "PREPAY",
    monto_carga: 500,
    monto_adicional: 50,
    total_cobrado: 550,
    pago_efectivo: 550,
    pago_transferencia: 0,
  }, operadorToken);
  assert.equal(servRes.status, 201, "Operador puede registrar cargas");

  const gastoRes = await apiCall("POST", "/api/expenses", {
    fecha_hora_utc: new Date().toISOString(),
    monto: 300,
    concepto: "Artículos de limpieza turno tarde",
    categoria: "LIMPIEZA",
    metodo_pago: "EFECTIVO",
  }, operadorToken);
  assert.equal(gastoRes.status, 201, "Operador puede asentar gastos habituales");

  // --- B. Operaciones denegadas para OPERADOR (HTTP 403 Forbidden) ---
  const sensitiveChecks = [
    { method: "POST", path: "/api/catalog", body: { nombre: "Nuevo", precio_dia: 100 }, name: "Alta catálogo" },
    { method: "PATCH", path: "/api/catalog/1/status", body: { activo: false }, name: "Desactivar producto" },
    { method: "PATCH", path: "/api/catalog/1", body: { precio_dia: 9999 }, name: "Editar precio producto" },
    { method: "POST", path: "/api/sales/ticket-op-test-1/anular", body: { motivo: "anulacion" }, name: "Anular venta" },
    { method: "PUT", path: "/api/sales/ticket-op-test-1/items/1", body: { nueva_cantidad: 5 }, name: "Editar item venta" },
    { method: "POST", path: "/api/services/1/cancel", body: { motivo: "anular" }, name: "Anular servicio" },
    { method: "DELETE", path: "/api/services/1", body: null, name: "Delete servicio" },
    { method: "PATCH", path: "/api/services/1", body: { monto_carga: 999 }, name: "Editar servicio" },
    { method: "GET", path: "/api/expenses/arqueo", body: null, name: "Arqueo ciego" },
    { method: "GET", path: "/api/expenses/dashboard", body: null, name: "Dashboard gastos" },
    { method: "GET", path: "/api/reports/dashboard", body: null, name: "Dashboard reportes" },
    { method: "GET", path: "/api/expenses/cierres", body: null, name: "Historial cierres" },
    { method: "POST", path: "/api/expenses/cierres", body: { fecha: "2026-10-27", efectivo_real: 1000 }, name: "Cierre de caja" },
    { method: "POST", path: "/api/expenses/1/cancel", body: { motivo: "anular" }, name: "Anular gasto" },
    { method: "DELETE", path: "/api/expenses/1", body: null, name: "Delete gasto" },
    { method: "PATCH", path: "/api/expenses/1", body: { monto: 999 }, name: "Editar gasto" },
    { method: "POST", path: "/api/preventistas/precios", body: { proveedor: "Secco", cambios: [] }, name: "Actualizar precios preventista" },
    { method: "POST", path: "/api/preventistas/productos/alta-rapida", body: { nombre: "Prod Prov", costo: 100, precio_dia: 150, proveedor: "Secco" }, name: "Alta rápida preventista" },
    { method: "POST", path: "/api/preventistas/pedidos/ped-1/cancelar", body: {}, name: "Cancelar pedido preventista" },
    { method: "PATCH", path: "/api/preventistas/pedidos/ped-1/estado", body: { estado: "RECIBIDO" }, name: "Estado pedido preventista" },
  ];

  for (const op of sensitiveChecks) {
    const res = await apiCall(op.method, op.path, op.body, operadorToken);
    assert.equal(
      res.status,
      403,
      `Operador debe ser rechazado con 403 Forbidden en ${op.name} (${op.method} ${op.path})`
    );
    assert.ok(
      res.data.detail.includes("Acceso denegado"),
      `Mensaje debe indicar denegación clara en ${op.name}`
    );
  }

  // --- C. Mismas operaciones permitidas para ADMINISTRADOR (HTTP 200 / 201) ---
  const adminAnularVenta = await apiCall("POST", "/api/sales/ticket-op-test-1/anular", { motivo: "Anulación legítima admin" }, adminToken);
  assert.equal(adminAnularVenta.status, 200, "Admin puede anular ventas");

  const adminArqueo = await apiCall("GET", "/api/expenses/arqueo", null, adminToken);
  assert.equal(adminArqueo.status, 200, "Admin puede consultar arqueo");

  const adminCierres = await apiCall("GET", "/api/expenses/cierres", null, adminToken);
  assert.equal(adminCierres.status, 200, "Admin puede consultar cierres");

  const adminAnularGasto = await apiCall("POST", "/api/expenses/1/cancel", { motivo: "Anulación legítima admin" }, adminToken);
  assert.equal(adminAnularGasto.status, 200, "Admin puede anular gastos");
});

test("Seguridad: Almacén Anti-Replay Distribuido con Turso libSQL (Consistencia Atómica)", async () => {
  const dbClient = createClient({ url: ":memory:" });

  // Crear tabla local de desafíos SCRAM (Migración 011)
  await dbClient.execute(`
    CREATE TABLE auth_scram_challenges (
        jti TEXT PRIMARY KEY,
        exp INTEGER NOT NULL,
        used INTEGER NOT NULL DEFAULT 0,
        creado_en_utc TEXT NOT NULL DEFAULT (datetime('now'))
    );
  `);

  const challengeStore = new LibsqlChallengeStore(dbClient);

  const jti = "desafio-atomico-prueba-123456";
  const exp = Date.now() + 60000;

  // 1. Registro del desafío
  await challengeStore.registerChallenge(jti, exp);

  // 2. Simulación de carrera concurrente entre dos Workers paralelos intentando consumir el mismo desafío
  const [res1, res2] = await Promise.all([
    challengeStore.consumeChallenge(jti),
    challengeStore.consumeChallenge(jti),
  ]);

  // Exactamente uno debe tener éxito (true) y el otro debe fallar (false)
  const exitos = (res1 ? 1 : 0) + (res2 ? 1 : 0);
  assert.equal(exitos, 1, "Exactamente una de las dos peticiones concurrentes debe tener éxito");

  // 3. Tercer intento posterior también debe fallar de inmediato
  const res3 = await challengeStore.consumeChallenge(jti);
  assert.equal(res3, false, "Reintento posterior debe ser rechazado como desafío ya consumido");

  // 4. Desafío expirado
  const expiredJti = "desafio-expirado-999999";
  await challengeStore.registerChallenge(expiredJti, Date.now() - 5000); // 5s en el pasado
  const resExpired = await challengeStore.consumeChallenge(expiredJti);
  assert.equal(resExpired, false, "Desafío expirado debe ser rechazado atómicamente");
});

// =========================================================================
// --- TESTS DE REGRESIÓN: COMPORTAMIENTO FAIL-CLOSED DEL ANTI-REPLAY ---
// =========================================================================

test("Anti-Replay Fail-Closed 1: dbClient ausente provoca rechazo explícito y fallo de inicialización", () => {
  assert.throws(
    () => new HybridChallengeStore(null),
    /PERSISTENT_STORE_REQUIRED/,
    "HybridChallengeStore debe rechazar la inicialización si no se provee dbClient"
  );
  assert.throws(
    () => new LibsqlChallengeStore(undefined),
    /PERSISTENT_STORE_REQUIRED/,
    "LibsqlChallengeStore debe rechazar la inicialización si no se provee dbClient"
  );
});

test("Anti-Replay Fail-Closed 2: Tabla inexistente provoca rechazo explícito (no degrada a memoria)", async () => {
  const emptyDb = createClient({ url: ":memory:" }); // Sin tabla auth_scram_challenges
  const store = new HybridChallengeStore(emptyDb);

  await assert.rejects(
    async () => await store.registerChallenge("jti-sin-tabla", Date.now() + 60000),
    /PERSISTENT_STORE_NOT_INITIALIZED/,
    "registerChallenge debe fallar de forma cerrada si la tabla no existe"
  );

  await assert.rejects(
    async () => await store.consumeChallenge("jti-sin-tabla"),
    /PERSISTENT_STORE_NOT_INITIALIZED/,
    "consumeChallenge debe fallar de forma cerrada si la tabla no existe"
  );

  // Integración con handleRequest: el endpoint debe devolver HTTP 503 sin emitir token
  const scramServer = new ScramServer(new Map(), crypto.randomBytes(32), store);
  const req = new Request("http://127.0.0.1:8787/api/auth/scram-first", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ clientFirstMessage: "n,,n=alan,r=cNonce12345678" }),
  });
  const resp = await handleRequest(req, {}, emptyDb, scramServer);
  assert.equal(resp.status, 503, "Debe responder HTTP 503 ante ausencia de tabla persistente");
  const data = await resp.json();
  assert.equal(data.access_token, undefined);
  assert.ok(data.detail.includes("no disponible temporalmente"));
});

test("Anti-Replay Fail-Closed 3: Error de conexión al registrar desafío provoca rechazo", async () => {
  const failingDb = {
    execute: async () => {
      throw new Error("ECONNREFUSED: Fallo de red hacia Turso");
    },
  };
  const store = new HybridChallengeStore(failingDb);

  await assert.rejects(
    async () => await store.registerChallenge("jti-net-err", Date.now() + 60000),
    /PERSISTENT_STORE_ERROR/,
    "registerChallenge debe rechazar el flujo ante error de conexión"
  );

  const scramServer = new ScramServer(new Map(), crypto.randomBytes(32), store);
  const req = new Request("http://127.0.0.1:8787/api/auth/scram-first", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ clientFirstMessage: "n,,n=alan,r=cNonce12345678" }),
  });
  const resp = await handleRequest(req, {}, failingDb, scramServer);
  assert.equal(resp.status, 503);
  const data = await resp.json();
  assert.equal(data.access_token, undefined);
});

test("Anti-Replay Fail-Closed 4: Error de conexión al consumir desafío rechaza la autenticación", async () => {
  const failingDb = {
    execute: async (stmt) => {
      if (typeof stmt.sql === "string" && stmt.sql.startsWith("INSERT")) {
        return { rows: [] };
      }
      throw new Error("ETIMEDOUT: Timeout de red al contactar Turso primario");
    },
  };
  const store = new HybridChallengeStore(failingDb);

  await assert.rejects(
    async () => await store.consumeChallenge("jti-consume-err"),
    /PERSISTENT_STORE_ERROR/,
    "consumeChallenge debe rechazar la autenticación ante error de conexión en UPDATE"
  );
});

test("Anti-Replay Fail-Closed 5: Resultado ambiguo del consumo no emite JWT ni acepta el login", async () => {
  const ambiguousDb = {
    execute: async () => ({ rows: [] }),
  };
  const store = new HybridChallengeStore(ambiguousDb);
  const res = await store.consumeChallenge("jti-ambiguo");
  assert.equal(res, false, "Cero filas debe resultar en false");

  const malformedDb = {
    execute: async () => ({ rows: null }),
  };
  const storeMalformed = new HybridChallengeStore(malformedDb);
  const resMal = await storeMalformed.consumeChallenge("jti-corrupto");
  assert.equal(resMal, false, "Respuesta malformada debe resultar en false");

  const multiRowDb = {
    execute: async () => ({ rows: [{ jti: "a" }, { jti: "b" }] }),
  };
  const storeMulti = new HybridChallengeStore(multiRowDb);
  const resMulti = await storeMulti.consumeChallenge("jti-multi");
  assert.equal(resMulti, false, "Más de una fila debe ser tratada como anomalía y retornar false");
});

test("Anti-Replay Fail-Closed 6: Desafío expirado es rechazado atómicamente", async () => {
  const db = createClient({ url: ":memory:" });
  await db.execute(`
    CREATE TABLE auth_scram_challenges (
        jti TEXT PRIMARY KEY,
        exp INTEGER NOT NULL,
        used INTEGER NOT NULL DEFAULT 0,
        creado_en_utc TEXT NOT NULL DEFAULT (datetime('now'))
    );
  `);
  const store = new HybridChallengeStore(db);
  const expiredJti = "jti-expirado-test-6";
  await store.registerChallenge(expiredJti, Date.now() - 10000);

  const consumed = await store.consumeChallenge(expiredJti);
  assert.equal(consumed, false, "Desafío con exp en el pasado debe retornar false");
});

test("Anti-Replay Fail-Closed 7: Desafío consumido previamente es rechazado en reintento (Replay Attack)", async () => {
  const db = createClient({ url: ":memory:" });
  await db.execute(`
    CREATE TABLE auth_scram_challenges (
        jti TEXT PRIMARY KEY,
        exp INTEGER NOT NULL,
        used INTEGER NOT NULL DEFAULT 0,
        creado_en_utc TEXT NOT NULL DEFAULT (datetime('now'))
    );
  `);
  const store = new HybridChallengeStore(db);
  const jti = "jti-consumo-previo-test-7";
  await store.registerChallenge(jti, Date.now() + 60000);

  const firstConsume = await store.consumeChallenge(jti);
  assert.equal(firstConsume, true, "Primer consumo legítimo debe retornar true");

  const replayConsume = await store.consumeChallenge(jti);
  assert.equal(replayConsume, false, "Segundo consumo idéntico debe retornar false");
});

test("Anti-Replay Fail-Closed 8: Dos consumos concurrentes del mismo desafío: exactamente uno tiene éxito", async () => {
  const db = createClient({ url: ":memory:" });
  await db.execute(`
    CREATE TABLE auth_scram_challenges (
        jti TEXT PRIMARY KEY,
        exp INTEGER NOT NULL,
        used INTEGER NOT NULL DEFAULT 0,
        creado_en_utc TEXT NOT NULL DEFAULT (datetime('now'))
    );
  `);
  const store = new HybridChallengeStore(db);
  const jti = "jti-concurrente-test-8";
  await store.registerChallenge(jti, Date.now() + 60000);

  const [c1, c2] = await Promise.all([
    store.consumeChallenge(jti),
    store.consumeChallenge(jti),
  ]);

  const totalExitos = (c1 ? 1 : 0) + (c2 ? 1 : 0);
  assert.equal(totalExitos, 1, "Bajo concurrencia estricta, como máximo y exactamente uno puede tener éxito");
});

test("Anti-Replay Fail-Closed 9: Memoria local nunca autoriza un desafío cuando falla el almacén persistente", async () => {
  let dbFails = false;
  const flakeyDb = {
    execute: async (stmt) => {
      if (typeof stmt.sql === "string" && stmt.sql.startsWith("INSERT")) {
        return { rows: [] };
      }
      if (dbFails) {
        throw new Error("TURSO_UNREACHABLE: Base de datos primaria caída");
      }
      return { rows: [{ jti: "jti-cache-test" }] };
    },
  };

  const localRejectSet = new Set();
  const store = new HybridChallengeStore(flakeyDb, localRejectSet);
  const jti = "jti-cache-test";

  await store.registerChallenge(jti, Date.now() + 60000);

  // La base primaria cae
  dbFails = true;

  // El consumo DEBE fallar y lanzar excepción, NUNCA autorizar por memoria local
  await assert.rejects(
    async () => await store.consumeChallenge(jti),
    /PERSISTENT_STORE_ERROR/,
    "La memoria local no puede habilitar un login si la base primaria falla"
  );
});

test("Anti-Replay Fail-Closed 10: Flujo SCRAM completo y válido funciona correctamente cuando el store persistente está operativo", async () => {
  const db = createClient({ url: ":memory:" });
  await db.execute(`
    CREATE TABLE auth_scram_challenges (
        jti TEXT PRIMARY KEY,
        exp INTEGER NOT NULL,
        used INTEGER NOT NULL DEFAULT 0,
        creado_en_utc TEXT NOT NULL DEFAULT (datetime('now'))
    );
  `);

  const USER = "alan";
  const PASS = "ClaveSecreta123!";
  const salt = crypto.randomBytes(16);
  const salted = crypto.pbkdf2Sync(PASS, salt, 600000, 32, "sha256");
  const storedHash = `pbkdf2_sha256$600000$${salt.toString("hex")}$${salted.toString("hex")}`;
  const verifier = convertirPbkdf2HashAScramVerifier(storedHash);
  verifier.role = "admin";

  const store = new HybridChallengeStore(db);
  const serverSecret = crypto.randomBytes(32);
  const scramServer = new ScramServer(new Map([[USER, verifier]]), serverSecret, store);
  const jwtSecret = crypto.randomBytes(32);
  const env = { JWT_SECRET: jwtSecret, SCRAM_SERVER_SECRET: serverSecret };

  // 1. Client First
  const cNonce = crypto.randomBytes(18).toString("base64");
  const cFirst = `n,,n=${USER},r=${cNonce}`;
  const firstReq = new Request("http://127.0.0.1:8787/api/auth/scram-first", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ clientFirstMessage: cFirst }),
  });
  const firstResp = await handleRequest(firstReq, env, db, scramServer);
  assert.equal(firstResp.status, 200);
  const firstData = await firstResp.json();
  assert.ok(firstData.serverFirstMessage);
  assert.ok(firstData.stateToken);

  // 2. Client PBKDF2 & Proof
  const serverFirst = firstData.serverFirstMessage;
  const sNonce = serverFirst.match(/r=([^,]+)/)[1];
  const clientKey = crypto.createHmac("sha256", salted).update("Client Key").digest();
  const storedKey = crypto.createHash("sha256").update(clientKey).digest();
  const serverKey = crypto.createHmac("sha256", salted).update("Server Key").digest();

  const clientFinalWithoutProof = `c=biws,r=${sNonce}`;
  const authMessage = `n=${USER},r=${cNonce},${serverFirst},${clientFinalWithoutProof}`;
  const clientSig = crypto.createHmac("sha256", storedKey).update(authMessage).digest();
  const clientProof = Buffer.alloc(32);
  for (let i = 0; i < 32; i++) clientProof[i] = clientKey[i] ^ clientSig[i];
  const cFinal = `${clientFinalWithoutProof},p=${clientProof.toString("base64")}`;

  // 3. Client Final
  const finalReq = new Request("http://127.0.0.1:8787/api/auth/scram-final", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ clientFinalMessage: cFinal, stateToken: firstData.stateToken }),
  });
  const finalResp = await handleRequest(finalReq, env, db, scramServer);
  assert.equal(finalResp.status, 200);
  const finalData = await finalResp.json();
  assert.ok(finalData.access_token, "Debe emitir token JWT");
  assert.equal(finalData.user.username, USER);
  assert.equal(finalData.user.role, "admin");

  // Validar firma del servidor
  const serverSigExpected = crypto.createHmac("sha256", serverKey).update(authMessage).digest("base64");
  assert.equal(finalData.serverFinalMessage, `v=${serverSigExpected}`);
});
