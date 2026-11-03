/**
 * Tests de endurecimiento y fail-closed de la configuración de SCRAM.
 * Verifica que secretos y verificadores ausentes, malformados o de longitud insuficiente
 * sean rechazados tajantemente, y que la inicialización singleton invalide instancias obsoletas.
 */

import test from "node:test";
import assert from "node:assert/strict";
import crypto from "node:crypto";
import { createClient } from "@libsql/client";
import worker, {
  parseAndValidateScramConfig,
  getScramServer,
  resetWorkerSingletonsForTest,
} from "../src/worker.js";
import { ScramServer } from "../src/auth/scram.js";

// Generador de verificador sintético de prueba (completamente ficticio para testing)
function createSyntheticVerifier() {
  return {
    storedKeyBase64: crypto.randomBytes(32).toString("base64"),
    serverKeyBase64: crypto.randomBytes(32).toString("base64"),
    saltBase64: crypto.randomBytes(16).toString("base64"),
    iterations: 600000,
    role: "admin",
  };
}

test("Configuración SCRAM: Rechazo estricto ante SCRAM_SERVER_SECRET ausente o vacío", () => {
  const dummyVerifiers = JSON.stringify({ test_user: createSyntheticVerifier() });

  assert.throws(
    () => parseAndValidateScramConfig({ SCRAM_VERIFIERS_JSON: dummyVerifiers }),
    /CONFIG_ERROR: SCRAM_SERVER_SECRET es obligatorio/,
    "Debe rechazar si SCRAM_SERVER_SECRET está ausente"
  );

  assert.throws(
    () => parseAndValidateScramConfig({ SCRAM_SERVER_SECRET: "", SCRAM_VERIFIERS_JSON: dummyVerifiers }),
    /CONFIG_ERROR: SCRAM_SERVER_SECRET es obligatorio/,
    "Debe rechazar si SCRAM_SERVER_SECRET es una cadena vacía"
  );

  assert.throws(
    () => parseAndValidateScramConfig({ SCRAM_SERVER_SECRET: "   ", SCRAM_VERIFIERS_JSON: dummyVerifiers }),
    /CONFIG_ERROR: SCRAM_SERVER_SECRET es obligatorio/,
    "Debe rechazar si SCRAM_SERVER_SECRET contiene solo espacios en blanco"
  );
});

test("Configuración SCRAM: Rechazo estricto ante SCRAM_SERVER_SECRET con formato no hexadecimal", () => {
  const dummyVerifiers = JSON.stringify({ test_user: createSyntheticVerifier() });

  // Caracteres no válidos en hex (ej. 'z', 'g', símbolos)
  assert.throws(
    () => parseAndValidateScramConfig({
      SCRAM_SERVER_SECRET: "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",
      SCRAM_VERIFIERS_JSON: dummyVerifiers,
    }),
    /CONFIG_ERROR: SCRAM_SERVER_SECRET tiene un formato hexadecimal inválido/,
    "Debe rechazar caracteres no hexadecimales"
  );

  // Longitud impar (no representable en bytes exactos)
  assert.throws(
    () => parseAndValidateScramConfig({
      SCRAM_SERVER_SECRET: "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890a",
      SCRAM_VERIFIERS_JSON: dummyVerifiers,
    }),
    /CONFIG_ERROR: SCRAM_SERVER_SECRET tiene un formato hexadecimal inválido/,
    "Debe rechazar cadenas hexadecimales con longitud impar"
  );
});

test("Configuración SCRAM: Rechazo estricto ante SCRAM_SERVER_SECRET de longitud menor a 32 bytes (64 caracteres)", () => {
  const dummyVerifiers = JSON.stringify({ test_user: createSyntheticVerifier() });

  // Secreto de solo 16 bytes (32 caracteres hex)
  const shortSecret = crypto.randomBytes(16).toString("hex");
  assert.throws(
    () => parseAndValidateScramConfig({
      SCRAM_SERVER_SECRET: shortSecret,
      SCRAM_VERIFIERS_JSON: dummyVerifiers,
    }),
    /CONFIG_ERROR: SCRAM_SERVER_SECRET debe tener al menos 32 bytes/,
    "Debe rechazar secretos de longitud criptográfica menor a 32 bytes (256 bits)"
  );
});

test("Configuración SCRAM: Rechazo estricto ante SCRAM_VERIFIERS_JSON ausente o vacío", () => {
  const validSecretHex = crypto.randomBytes(32).toString("hex");

  assert.throws(
    () => parseAndValidateScramConfig({ SCRAM_SERVER_SECRET: validSecretHex }),
    /CONFIG_ERROR: SCRAM_VERIFIERS_JSON es obligatorio/,
    "Debe rechazar si SCRAM_VERIFIERS_JSON no está definido"
  );

  assert.throws(
    () => parseAndValidateScramConfig({
      SCRAM_SERVER_SECRET: validSecretHex,
      SCRAM_VERIFIERS_JSON: "",
    }),
    /CONFIG_ERROR: SCRAM_VERIFIERS_JSON/,
    "Debe rechazar si SCRAM_VERIFIERS_JSON está vacío"
  );
});

test("Configuración SCRAM: Rechazo estricto ante SCRAM_VERIFIERS_JSON con JSON malformado", () => {
  const validSecretHex = crypto.randomBytes(32).toString("hex");

  assert.throws(
    () => parseAndValidateScramConfig({
      SCRAM_SERVER_SECRET: validSecretHex,
      SCRAM_VERIFIERS_JSON: "{ usuario: roto, sin comillas",
    }),
    /CONFIG_ERROR: SCRAM_VERIFIERS_JSON no es un JSON válido/,
    "Debe rechazar JSON con sintaxis rota"
  );
});

test("Configuración SCRAM: Rechazo estricto ante SCRAM_VERIFIERS_JSON vacío o estructura no-objeto", () => {
  const validSecretHex = crypto.randomBytes(32).toString("hex");

  // Objeto vacío sin usuarios
  assert.throws(
    () => parseAndValidateScramConfig({
      SCRAM_SERVER_SECRET: validSecretHex,
      SCRAM_VERIFIERS_JSON: "{}",
    }),
    /CONFIG_ERROR: SCRAM_VERIFIERS_JSON debe contener al menos un usuario configurado/,
    "Debe rechazar diccionario de usuarios vacío"
  );

  // Array en lugar de objeto
  assert.throws(
    () => parseAndValidateScramConfig({
      SCRAM_SERVER_SECRET: validSecretHex,
      SCRAM_VERIFIERS_JSON: "[]",
    }),
    /CONFIG_ERROR: SCRAM_VERIFIERS_JSON debe contener al menos un usuario configurado/,
    "Debe rechazar si es un array"
  );
});

test("Configuración SCRAM: Rechazo estricto ante verificadores con campos faltantes o iteraciones insuficientes", () => {
  const validSecretHex = crypto.randomBytes(32).toString("hex");

  // Falta storedKeyBase64
  assert.throws(
    () => parseAndValidateScramConfig({
      SCRAM_SERVER_SECRET: validSecretHex,
      SCRAM_VERIFIERS_JSON: JSON.stringify({
        usuario1: {
          serverKeyBase64: crypto.randomBytes(32).toString("base64"),
          saltBase64: crypto.randomBytes(16).toString("base64"),
          iterations: 600000,
        },
      }),
    }),
    /CONFIG_ERROR: Verificador inválido para 'usuario1': falta 'storedKeyBase64'/,
    "Debe exigir storedKeyBase64"
  );

  // Falta serverKeyBase64
  assert.throws(
    () => parseAndValidateScramConfig({
      SCRAM_SERVER_SECRET: validSecretHex,
      SCRAM_VERIFIERS_JSON: JSON.stringify({
        usuario1: {
          storedKeyBase64: crypto.randomBytes(32).toString("base64"),
          saltBase64: crypto.randomBytes(16).toString("base64"),
          iterations: 600000,
        },
      }),
    }),
    /CONFIG_ERROR: Verificador inválido para 'usuario1': falta 'serverKeyBase64'/,
    "Debe exigir serverKeyBase64"
  );

  // Iteraciones insuficientes (< 4096)
  assert.throws(
    () => parseAndValidateScramConfig({
      SCRAM_SERVER_SECRET: validSecretHex,
      SCRAM_VERIFIERS_JSON: JSON.stringify({
        usuario1: {
          storedKeyBase64: crypto.randomBytes(32).toString("base64"),
          serverKeyBase64: crypto.randomBytes(32).toString("base64"),
          saltBase64: crypto.randomBytes(16).toString("base64"),
          iterations: 100, // Demasiado bajo
        },
      }),
    }),
    /CONFIG_ERROR: Verificador inválido para 'usuario1': 'iterations' debe ser un número entero >= 4096/,
    "Debe exigir iteraciones mínimas seguras"
  );
});

test("Configuración SCRAM: Rechazo estricto ante claves en Base64 con longitud diferente a 32 bytes", () => {
  const validSecretHex = crypto.randomBytes(32).toString("hex");

  // Clave de 16 bytes en vez de 32 bytes
  assert.throws(
    () => parseAndValidateScramConfig({
      SCRAM_SERVER_SECRET: validSecretHex,
      SCRAM_VERIFIERS_JSON: JSON.stringify({
        usuario1: {
          storedKeyBase64: crypto.randomBytes(16).toString("base64"), // 16 bytes
          serverKeyBase64: crypto.randomBytes(32).toString("base64"),
          saltBase64: crypto.randomBytes(16).toString("base64"),
          iterations: 600000,
        },
      }),
    }),
    /CONFIG_ERROR: Verificador inválido para 'usuario1': 'storedKeyBase64' y 'serverKeyBase64' deben decodificar exactamente a 32 bytes/,
    "Debe rechazar claves que no decodifiquen a 32 bytes exactos"
  );
});

test("Configuración SCRAM: Parseo correcto y exitoso con configuración válida", () => {
  const validSecretHex = crypto.randomBytes(32).toString("hex");
  const syntheticUser = createSyntheticVerifier();
  const config = {
    SCRAM_SERVER_SECRET: validSecretHex,
    SCRAM_VERIFIERS_JSON: JSON.stringify({ operador_alan: syntheticUser }),
  };

  const { verifiersMap, serverSecret } = parseAndValidateScramConfig(config);
  assert.ok(verifiersMap instanceof Map, "Debe retornar un Map");
  assert.equal(verifiersMap.size, 1);
  assert.ok(verifiersMap.has("operador_alan"));
  assert.ok(Buffer.isBuffer(serverSecret), "El secreto debe ser un Buffer");
  assert.equal(serverSecret.length, 32, "El secreto debe tener exactamente 32 bytes");
  assert.equal(serverSecret.toString("hex"), validSecretHex.toLowerCase());
});

test("Defecto del Singleton: Invalidación y recreación correcta cuando cambia la configuración o el dbClient", () => {
  resetWorkerSingletonsForTest();

  const dbClient1 = createClient({ url: ":memory:" });
  const dbClient2 = createClient({ url: ":memory:" });

  const secretHex1 = crypto.randomBytes(32).toString("hex");
  const secretHex2 = crypto.randomBytes(32).toString("hex");

  const env1 = {
    SCRAM_SERVER_SECRET: secretHex1,
    SCRAM_VERIFIERS_JSON: JSON.stringify({ user_a: createSyntheticVerifier() }),
  };

  const env2 = {
    SCRAM_SERVER_SECRET: secretHex2,
    SCRAM_VERIFIERS_JSON: JSON.stringify({ user_b: createSyntheticVerifier() }),
  };

  // 1. Primera instanciación
  const srv1 = getScramServer(env1, dbClient1);
  assert.ok(srv1 instanceof ScramServer);
  assert.ok(srv1.verifiersDb.has("user_a"));

  // 2. Mismo env y mismo dbClient: debe devolver la misma instancia singleton
  const srv1Cached = getScramServer(env1, dbClient1);
  assert.equal(srv1Cached, srv1, "Debe reutilizar la instancia singleton si no hay cambios");

  // 3. Cambio de dbClient: DEBE invalidar y crear una nueva instancia
  const srv2 = getScramServer(env1, dbClient2);
  assert.notEqual(srv2, srv1, "Debe crear una nueva instancia si cambia el dbClient");

  // 4. Cambio de configuración (secretHex o verifiers): DEBE invalidar y crear nueva instancia
  const srv3 = getScramServer(env2, dbClient2);
  assert.notEqual(srv3, srv2, "Debe crear una nueva instancia si cambia la configuración SCRAM");
  assert.ok(srv3.verifiersDb.has("user_b"), "La nueva instancia debe reflejar la nueva configuración");
  assert.equal(srv3.verifiersDb.has("user_a"), false, "La nueva instancia no debe retener verifiers viejos");
});

test("Protección HTTP del Worker: worker.fetch responde HTTP 500 sanitizado ante configuración SCRAM inválida", async () => {
  resetWorkerSingletonsForTest();

  // Entorno sin SCRAM_SERVER_SECRET
  const brokenEnv = {
    SCRAM_VERIFIERS_JSON: JSON.stringify({ user: createSyntheticVerifier() }),
    TURSO_DATABASE_URL: "http://127.0.0.1:8080",
  };

  const req = new Request("http://127.0.0.1:8787/api/auth/scram-first", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ clientFirstMessage: "n,,n=user,r=12345678" }),
  });

  const response = await worker.fetch(req, brokenEnv, {});
  assert.equal(response.status, 500, "Debe responder HTTP 500");
  const data = await response.json();
  assert.equal(data.detail, "Error de configuración del servicio de autenticación.");
  // Confirmar que no se filtra ninguna clave, traza o nombre de archivo en la respuesta pública
  assert.equal(JSON.stringify(data).includes("SCRAM_SERVER_SECRET"), false);
  assert.equal(JSON.stringify(data).includes("TURSO_DATABASE_URL"), false);
});

test("ScramServer: Constructor directo exige un Buffer de al menos 32 bytes para serverSecretKey", () => {
  const dummyMap = new Map();

  // No es Buffer (es string)
  assert.throws(
    () => new ScramServer(dummyMap, "cadena_no_buffer_de_32_bytes_12345"),
    /CONFIG_ERROR: serverSecretKey debe ser un Buffer criptográfico de al menos 32 bytes/,
    "Debe rechazar strings"
  );

  // Buffer demasiado corto (16 bytes)
  assert.throws(
    () => new ScramServer(dummyMap, crypto.randomBytes(16)),
    /CONFIG_ERROR: serverSecretKey debe ser un Buffer criptográfico de al menos 32 bytes/,
    "Debe rechazar Buffers con longitud < 32 bytes"
  );

  // Buffer de 32 bytes válido
  assert.doesNotThrow(
    () => new ScramServer(dummyMap, crypto.randomBytes(32)),
    "Debe aceptar un Buffer de 32 bytes válido"
  );
});

test("Seguridad JWT Fail-Closed: Rechazo estricto ante JWT_SECRET ausente o menor a 32 bytes", async () => {
  const { handleRequest, generarJwt, verificarJwt } = await import("../src/api/router.js");
  const db = createClient({ url: ":memory:" });
  const scramServer = new ScramServer(new Map(), crypto.randomBytes(32));

  assert.throws(
    () => generarJwt("alan", "admin", undefined),
    /CONFIG_ERROR: JWT_SECRET es obligatorio/,
    "generarJwt debe fallar cerrado sin JWT_SECRET"
  );

  assert.throws(
    () => generarJwt("alan", "admin", "clave-corta-16b!"),
    /CONFIG_ERROR: JWT_SECRET debe tener al menos 32 bytes/,
    "generarJwt debe rechazar secretos menores a 32 bytes"
  );

  assert.throws(
    () => verificarJwt("a.b.c", ""),
    /CONFIG_ERROR: JWT_SECRET/,
    "verificarJwt debe fallar cerrado sin JWT_SECRET"
  );

  // Ruta protegida sin JWT_SECRET en env -> HTTP 500 fail-closed
  const reqProtected = new Request("http://127.0.0.1:8787/api/catalog", {
    method: "GET",
    headers: { Authorization: "Bearer fake.jwt.token" },
  });
  const respMissing = await handleRequest(reqProtected, {}, db, scramServer);
  assert.equal(respMissing.status, 500);
  const bodyMissing = await respMissing.json();
  assert.equal(bodyMissing.detail, "Error de configuración del servicio de autenticación.");
});

test("Seguridad CORS Worker: Orígenes explícitos permitidos y bloqueo de orígenes terceros no autorizados", async () => {
  const { handleRequest } = await import("../src/api/router.js");
  const db = createClient({ url: ":memory:" });
  const scramServer = new ScramServer(new Map(), crypto.randomBytes(32));
  const env = {
    JWT_SECRET: crypto.randomBytes(32),
    ALLOWED_ORIGINS: "https://pos.genaro.com.ar",
  };

  // 1. Origen loopback permitido en preflight OPTIONS
  const reqAllowedLoopback = new Request("http://127.0.0.1:8787/api/sales", {
    method: "OPTIONS",
    headers: { Origin: "http://localhost:5173" },
  });
  const respLoopback = await handleRequest(reqAllowedLoopback, env, db, scramServer);
  assert.equal(respLoopback.status, 204);
  assert.equal(respLoopback.headers.get("Access-Control-Allow-Origin"), "http://localhost:5173");
  assert.equal(respLoopback.headers.get("Access-Control-Allow-Credentials"), "true");

  // 2. Origen explícito configurado en ALLOWED_ORIGINS
  const reqAllowedProd = new Request("http://127.0.0.1:8787/api/sales", {
    method: "OPTIONS",
    headers: { Origin: "https://pos.genaro.com.ar" },
  });
  const respProd = await handleRequest(reqAllowedProd, env, db, scramServer);
  assert.equal(respProd.status, 204);
  assert.equal(respProd.headers.get("Access-Control-Allow-Origin"), "https://pos.genaro.com.ar");

  // 3. Origen malicioso no autorizado -> rechazado con 403 en preflight y sin cabeceras de concesión CORS
  const reqEvil = new Request("http://127.0.0.1:8787/api/sales", {
    method: "OPTIONS",
    headers: { Origin: "https://evil-attacker.example.com" },
  });
  const respEvil = await handleRequest(reqEvil, env, db, scramServer);
  assert.equal(respEvil.status, 403);
  assert.equal(respEvil.headers.get("Access-Control-Allow-Origin"), null);
  assert.equal(respEvil.headers.get("Access-Control-Allow-Credentials"), null);
});

test("Integridad Contable: editarItemVenta preserva invariante total_venta = monto_efectivo + monto_transferencia en EFECTIVO, TRANSFERENCIA y MIXTO", async () => {
  const {
    inicializarEsquemaCompleto,
    poblarDatosPrueba,
    registrarVentaIdempotente,
    obtenerDetalleVenta,
    editarItemVenta,
  } = await import("../src/db/adapter.js");

  const db = createClient({ url: ":memory:" });
  await inicializarEsquemaCompleto(db);
  await poblarDatosPrueba(db);

  // 1. Venta en TRANSFERENCIA
  await registrarVentaIdempotente(db, {
    ticket: {
      ticket_id: "t-transf-edit-1",
      fecha_hora_utc: "2026-10-27T15:00:00Z",
      total_venta: 1500,
      monto_efectivo: 0,
      monto_transf: 1500,
      metodo_pago: "TRANSFERENCIA",
    },
    items: [{ producto: "Alfajor Chocolate", cantidad: 1, precio_unit: 1500, subtotal: 1500, costo_unitario: 900 }],
    usuario: "alan",
  });

  const detTransf = await obtenerDetalleVenta(db, "t-transf-edit-1");
  assert.equal(detTransf.items[0].costo_unitario, 900, "Debe preservar snapshot de costo_unitario enviado por el cliente");

  const editTransf = await editarItemVenta(
    db,
    "t-transf-edit-1",
    detTransf.items[0].id,
    { nueva_cantidad: 2, nuevo_subtotal: 3000 },
    "alan"
  );
  assert.equal(editTransf.total_venta, 3000);
  assert.equal(editTransf.monto_efectivo, 0, "No debe convertir una venta por TRANSFERENCIA en efectivo");
  assert.equal(editTransf.monto_transf, 3000, "Debe actualizar monto_transferencia en ventas por TRANSFERENCIA");

  // 2. Venta MIXTA
  await registrarVentaIdempotente(db, {
    ticket: {
      ticket_id: "t-mixto-edit-1",
      fecha_hora_utc: "2026-10-27T15:10:00Z",
      total_venta: 3000,
      monto_efectivo: 1000,
      monto_transf: 2000,
      metodo_pago: "MIXTO",
    },
    items: [{ producto: "Alfajor Chocolate", cantidad: 2, precio_unit: 1500, subtotal: 3000 }],
    usuario: "alan",
  });

  const detMixto = await obtenerDetalleVenta(db, "t-mixto-edit-1");

  // Editar MIXTO con desglose desbalanceado debe fallar cerrado (INVALID_INPUT)
  await assert.rejects(
    async () =>
      await editarItemVenta(
        db,
        "t-mixto-edit-1",
        detMixto.items[0].id,
        { nueva_cantidad: 3, nuevo_subtotal: 4500, monto_efectivo: 1000, monto_transf: 2000 },
        "alan"
      ),
    /INVALID_INPUT: Para editar el total de un ticket con pago MIXTO/,
    "Debe rechazar edición de ticket MIXTO si el desglose explícito no suma el nuevo total"
  );

  // Editar MIXTO sin desglose explícito aplica regla contable preservando transferencia y ajustando efectivo
  const editMixtoAuto = await editarItemVenta(
    db,
    "t-mixto-edit-1",
    detMixto.items[0].id,
    { nueva_cantidad: 3, nuevo_subtotal: 4500 },
    "alan"
  );
  assert.equal(editMixtoAuto.total_venta, 4500);
  assert.equal(editMixtoAuto.monto_transf, 2000, "No debe poner monto_transferencia en 0 en pago MIXTO");
  assert.equal(editMixtoAuto.monto_efectivo, 2500);
  assert.equal(editMixtoAuto.metodo_pago, "MIXTO");

  // Editar MIXTO especificando el desglose explícito válido preserva el invariante
  const editMixtoOk = await editarItemVenta(
    db,
    "t-mixto-edit-1",
    detMixto.items[0].id,
    { nueva_cantidad: 3, nuevo_subtotal: 4500, monto_efectivo: 1500, monto_transf: 3000 },
    "alan"
  );
  assert.equal(editMixtoOk.total_venta, 4500);
  assert.equal(editMixtoOk.monto_efectivo, 1500);
  assert.equal(editMixtoOk.monto_transf, 3000);
});

test("Readiness Worker: comprobarReadinessEsquema exige migración 011 y tabla auth_scram_challenges con sus columnas e índices", async () => {
  const { inicializarEsquemaCompleto, comprobarReadinessEsquema } = await import("../src/db/adapter.js");
  const db = createClient({ url: ":memory:" });
  await inicializarEsquemaCompleto(db);

  const readyOk = await comprobarReadinessEsquema(db);
  assert.equal(readyOk.listo, true);
  assert.equal(readyOk.status, "PREPARADO");
  assert.ok(readyOk.migraciones_aplicadas.includes(11));

  // Si se elimina un índice requerido de auth_scram_challenges, readiness debe reportar NO_PREPARADO
  await db.execute("DROP INDEX idx_scram_challenges_exp;");
  const readyMissingIdx = await comprobarReadinessEsquema(db);
  assert.equal(readyMissingIdx.listo, false);
  assert.equal(readyMissingIdx.status, "NO_PREPARADO");
  assert.ok(readyMissingIdx.inconsistencias.some((msg) => msg.includes("idx_scram_challenges_exp")));
});
