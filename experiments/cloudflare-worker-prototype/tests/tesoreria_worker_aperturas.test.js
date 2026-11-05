import test from "node:test";
import assert from "node:assert/strict";
import { createClient } from "@libsql/client";
import { ScramServer } from "../src/auth/scram.js";
import {
  inicializarEsquemaCompleto,
  obtenerSaldosInicialesWorker,
  modificarSaldoInicialWorker,
  obtenerHistorialAperturasWorker,
} from "../src/db/adapter.js";
import { handleRequest, generarJwt } from "../src/api/router.js";

const JWT_SECRET = "super_secret_jwt_key_that_is_at_least_32_bytes_long_for_security!";
const SCRAM_SERVER_SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

test("Worker: Configuración de Saldos Iniciales en $0 y Auditoría Append-Only", async () => {
  const dbClient = createClient({ url: ":memory:" });
  await inicializarEsquemaCompleto(dbClient);

  const env = {
    JWT_SECRET,
    SCRAM_SERVER_SECRET,
    CORS_ALLOWED_ORIGINS: "https://genaro-pos-live.pages.dev",
  };
  const scramServer = new ScramServer(
    new Map(),
    Buffer.from(SCRAM_SERVER_SECRET, "hex")
  );

  const tokenAlan = generarJwt("alan", "socio", JWT_SECRET);
  const tokenOperador = generarJwt("cajero1", "operador", JWT_SECRET);

  // 1. Consultar saldos iniciales: todos deben estar en $0
  const reqGet = new Request("https://genaro-pos-live-api.genaro-pos.workers.dev/api/tesoreria/apertura/saldos-iniciales", {
    method: "GET",
    headers: { Authorization: `Bearer ${tokenAlan}` },
  });
  const resGet = await handleRequest(reqGet, env, dbClient, scramServer);
  assert.equal(resGet.status, 200);
  const dataGet = await resGet.json();

  assert.equal(dataGet.fondos_fisicos.length, 6);
  assert.equal(dataGet.cuentas_digitales.length, 4);
  assert.equal(dataGet.obligaciones.length, 3);
  assert.equal(dataGet.total_fondos_fisicos_cents, 0);
  assert.equal(dataGet.total_cuentas_digitales_cents, 0);
  assert.equal(dataGet.total_obligaciones_cents, 0);

  // 2. Operador no autorizado intenta modificar saldo inicial -> HTTP 403
  const reqOp = new Request("https://genaro-pos-live-api.genaro-pos.workers.dev/api/tesoreria/apertura/saldos-iniciales", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${tokenOperador}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      codigo: "1110",
      saldo_nuevo_cents: 5000000,
      motivo: "Intento no autorizado",
    }),
  });
  const resOp = await handleRequest(reqOp, env, dbClient, scramServer);
  assert.equal(resOp.status, 403);

  // 3. Socio Alan configura apertura de 1110 -> HTTP 200
  const reqAlan = new Request("https://genaro-pos-live-api.genaro-pos.workers.dev/api/tesoreria/apertura/saldos-iniciales", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${tokenAlan}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      codigo: "1110",
      saldo_nuevo_cents: 5000000,
      motivo: "Apertura de caja de cambio inicial",
      referencia_autorizacion: "Acta 001",
    }),
  });
  const resAlan = await handleRequest(reqAlan, env, dbClient, scramServer);
  assert.equal(resAlan.status, 200);
  const dataAlan = await resAlan.json();
  assert.equal(dataAlan.status, "SUCCESS");
  assert.equal(dataAlan.item_codigo, "1110");
  assert.equal(dataAlan.saldo_nuevo_cents, 5000000);

  // 4. Consultar historial de aperturas -> Append-only verificado
  const reqHist = new Request("https://genaro-pos-live-api.genaro-pos.workers.dev/api/tesoreria/apertura/historial", {
    method: "GET",
    headers: { Authorization: `Bearer ${tokenAlan}` },
  });
  const resHist = await handleRequest(reqHist, env, dbClient, scramServer);
  assert.equal(resHist.status, 200);
  const dataHist = await resHist.json();
  assert.ok(dataHist.length >= 1);
  const ultimo = dataHist[0];
  assert.equal(ultimo.item_codigo, "1110");
  assert.equal(ultimo.saldo_nuevo_cents, 5000000);
  assert.equal(ultimo.usuario_responsable, "alan");
  assert.equal(ultimo.motivo, "Apertura de caja de cambio inicial");
});
