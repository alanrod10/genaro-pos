/**
 * Suite de Pruebas: Autenticación SCRAM-SHA-256 en Cloudflare Workers
 */

import test from "node:test";
import assert from "node:assert/strict";
import crypto from "node:crypto";
import {
  ScramClient,
  ScramServer,
  convertirPbkdf2HashAScramVerifier,
  InMemoryChallengeStore,
  DurableObjectSqliteChallengeStore,
} from "../src/auth/scram.js";

test("SCRAM-SHA-256: Autenticación correcta y conversión de hash PBKDF2 sin conocer contraseña", () => {
  const SINTETICO_USER = "alan_sintetico";
  const SINTETICO_PASS = "ClaveSegura2026!";
  const SINTETICO_SALT = crypto.randomBytes(16);
  const SINTETICO_ITERS = 600000;

  // Hash sintético original
  const rawSalted = crypto.pbkdf2Sync(
    SINTETICO_PASS,
    SINTETICO_SALT,
    SINTETICO_ITERS,
    32,
    "sha256"
  );
  const storedPbkdf2Hash = `pbkdf2_sha256$${SINTETICO_ITERS}$${SINTETICO_SALT.toString("hex")}$${rawSalted.toString("hex")}`;

  // Conversión matemática directa
  const verifier = convertirPbkdf2HashAScramVerifier(storedPbkdf2Hash);
  assert.ok(verifier.storedKeyBase64);
  assert.ok(verifier.serverKeyBase64);

  const verifiersDb = new Map([[SINTETICO_USER, verifier]]);
  const server = new ScramServer(verifiersDb, crypto.randomBytes(32));

  // Handshake SCRAM
  const client = new ScramClient(SINTETICO_USER, SINTETICO_PASS);
  const cFirst = client.createClientFirstMessage();
  const sFirst = server.processClientFirst(cFirst);

  const cFinal = client.processServerFirstAndCreateFinal(sFirst.serverFirstMessage);
  const sFinal = server.processClientFinal(cFinal, sFirst.stateToken);

  assert.equal(sFinal.authenticated, true);
  assert.equal(sFinal.username, SINTETICO_USER);

  // Cliente valida firma del servidor
  const serverVerified = client.verifyServerFinalMessage(sFinal.serverFinalMessage);
  assert.equal(serverVerified, true);
  assert.ok(sFinal.cpuTimeUs < 10000, "CPU debe ser razonablemente bajo (< 10 ms)");
});

test("SCRAM-SHA-256: Rechazo estricto de contraseña errónea", () => {
  const verifier = convertirPbkdf2HashAScramVerifier(
    `pbkdf2_sha256$600000$${crypto.randomBytes(16).toString("hex")}$${crypto.randomBytes(32).toString("hex")}`
  );
  const verifiersDb = new Map([["usuario_test", verifier]]);
  const server = new ScramServer(verifiersDb, crypto.randomBytes(32));

  const client = new ScramClient("usuario_test", "ContraseñaIncorrecta");
  const cFirst = client.createClientFirstMessage();
  const sFirst = server.processClientFirst(cFirst);
  const cFinal = client.processServerFirstAndCreateFinal(sFirst.serverFirstMessage);

  assert.throws(
    () => server.processClientFinal(cFinal, sFirst.stateToken),
    /Credenciales inválidas/
  );
});

test("SCRAM-SHA-256: Rechazo de prueba alterada (Man-in-the-Middle)", () => {
  const user = "operador";
  const pass = "Pass123!";
  const salt = crypto.randomBytes(16);
  const salted = crypto.pbkdf2Sync(pass, salt, 600000, 32, "sha256");
  const verifier = convertirPbkdf2HashAScramVerifier(`pbkdf2_sha256$600000$${salt.toString("hex")}$${salted.toString("hex")}`);

  const server = new ScramServer(new Map([[user, verifier]]), crypto.randomBytes(32));
  const client = new ScramClient(user, pass);

  const cFirst = client.createClientFirstMessage();
  const sFirst = server.processClientFirst(cFirst);
  let cFinal = client.processServerFirstAndCreateFinal(sFirst.serverFirstMessage);

  // Alterar último caracter de la prueba
  const lastChar = cFinal.slice(-1) === "A" ? "B" : "A";
  cFinal = cFinal.slice(0, -1) + lastChar;

  assert.throws(
    () => server.processClientFinal(cFinal, sFirst.stateToken),
    /Credenciales inválidas/
  );
});

test("SCRAM-SHA-256: Detección y rechazo de ataque de Replay", () => {
  const user = "operador";
  const pass = "Pass123!";
  const salt = crypto.randomBytes(16);
  const salted = crypto.pbkdf2Sync(pass, salt, 600000, 32, "sha256");
  const verifier = convertirPbkdf2HashAScramVerifier(`pbkdf2_sha256$600000$${salt.toString("hex")}$${salted.toString("hex")}`);

  const server = new ScramServer(new Map([[user, verifier]]), crypto.randomBytes(32));
  const client = new ScramClient(user, pass);

  const cFirst = client.createClientFirstMessage();
  const sFirst = server.processClientFirst(cFirst);
  const cFinal = client.processServerFirstAndCreateFinal(sFirst.serverFirstMessage);

  // Primera ejecución válida
  const res1 = server.processClientFinal(cFinal, sFirst.stateToken);
  assert.equal(res1.authenticated, true);

  // Reintento idéntico (Replay Attack)
  assert.throws(
    () => server.processClientFinal(cFinal, sFirst.stateToken),
    /Ataque de Replay detectado/
  );
});

test("SCRAM-SHA-256: Prevención de Replay entre múltiples Workers usando Durable Objects SQLite Store", () => {
  const user = "operador";
  const pass = "Pass123!";
  const salt = crypto.randomBytes(16);
  const salted = crypto.pbkdf2Sync(pass, salt, 600000, 32, "sha256");
  const verifier = convertirPbkdf2HashAScramVerifier(`pbkdf2_sha256$600000$${salt.toString("hex")}$${salted.toString("hex")}`);

  // Simulación de almacenamiento coordinado Durable Object
  const sharedDoStore = new DurableObjectSqliteChallengeStore();
  const serverSecret = crypto.randomBytes(32);

  // Dos instancias de Worker separadas en distintas regiones
  const workerInstanceSaoPaulo = new ScramServer(new Map([[user, verifier]]), serverSecret, sharedDoStore);
  const workerInstanceBuenosAires = new ScramServer(new Map([[user, verifier]]), serverSecret, sharedDoStore);

  const client = new ScramClient(user, pass);
  // Paso 1 procesado por Worker en São Paulo
  const cFirst = client.createClientFirstMessage();
  const sFirst = workerInstanceSaoPaulo.processClientFirst(cFirst);

  const cFinal = client.processServerFirstAndCreateFinal(sFirst.serverFirstMessage);

  // Paso 2 atendido por Worker en Buenos Aires
  const sFinal = workerInstanceBuenosAires.processClientFinal(cFinal, sFirst.stateToken);
  assert.equal(sFinal.authenticated, true);

  // Reintento de replay hacia el Worker de São Paulo
  assert.throws(
    () => workerInstanceSaoPaulo.processClientFinal(cFinal, sFirst.stateToken),
    /Ataque de Replay detectado/
  );
});

test("SCRAM-SHA-256: Rechazo de desafío expirado (> 60 segundos)", () => {
  const server = new ScramServer(new Map(), crypto.randomBytes(32));
  const expiredToken = server._sealToken({
    jti: "expired_jti",
    userExists: true,
    username: "user",
    cNonce: "test",
    rNonce: "test",
    clientFirstBare: "n=user,r=test",
    serverFirst: "r=test,s=test,i=4096",
    exp: Date.now() - 5000, // Expirado hace 5s
  });

  assert.throws(
    () => server.processClientFinal("c=biws,r=test,p=AA==", expiredToken),
    /Desafío expirado/
  );
});

test("SCRAM-SHA-256: Anti-enumeración y protección frente a fuerza bruta", () => {
  const server = new ScramServer(new Map(), crypto.randomBytes(32));
  const clientIp = "192.168.1.100";

  // 1. Usuario inexistente recibe desafío sintético verosímil
  const cFirst = new ScramClient("usuario_que_no_existe", "pass").createClientFirstMessage();
  const sFirst = server.processClientFirst(cFirst, clientIp);
  assert.ok(sFirst.serverFirstMessage.includes("s="));
  assert.ok(sFirst.serverFirstMessage.includes("i=600000"));

  // 2. Simular 5 intentos fallidos
  for (let i = 0; i < 5; i++) {
    server._registerFailure(clientIp);
  }

  // 3. Sexto intento bloqueado por rate limiter
  assert.throws(
    () => server.processClientFirst(cFirst, clientIp),
    /Demasiados intentos fallidos/
  );
});
