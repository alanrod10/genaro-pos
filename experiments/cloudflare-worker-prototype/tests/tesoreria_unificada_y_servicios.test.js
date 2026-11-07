import test from "node:test";
import assert from "node:assert/strict";
import { createClient } from "@libsql/client";
import { ScramServer } from "../src/auth/scram.js";
import { inicializarEsquemaCompleto } from "../src/db/adapter.js";
import { handleRequest, generarJwt } from "../src/api/router.js";

const JWT_SECRET = "super_secret_jwt_key_that_is_at_least_32_bytes_long_for_security!";
const SCRAM_SERVER_SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

function buildReq(path, method, token, body = null, idemKey = null) {
  return new Request(`https://genaro-pos-live-api.genaro-pos.workers.dev${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      ...(idemKey ? { "Idempotency-Key": idemKey } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
}

test("Worker Unificado: Matriz Estricta de Servicios y Regla MERCADO PAGO -> PREPAY", async () => {
  const dbClient = createClient({ url: ":memory:" });
  await inicializarEsquemaCompleto(dbClient);

  const env = {
    JWT_SECRET,
    SCRAM_SERVER_SECRET,
    CORS_ALLOWED_ORIGINS: "https://genaro-pos-live.pages.dev",
  };
  const scramServer = new ScramServer(new Map(), Buffer.from(SCRAM_SERVER_SECRET, "hex"));
  const tokenAlan = generarJwt("alan", "admin", JWT_SECRET);

  // 1. Combinaciones válidas
  const validCases = [
    { origen: "PREPAY", destino: "SUBE" },
    { origen: "PREPAY", destino: "Personal" },
    { origen: "PREPAY", destino: "Movistar" },
    { origen: "PREPAY", destino: "Claro" },
    { origen: "CLARO COMERCIOS", destino: "Claro" },
    { origen: "MERCADO PAGO", destino: "PREPAY" },
    { origen: "MERCADO PAGO", destino: "SUBE" },
  ];

  let idx = 1;
  for (const c of validCases) {
    const res = await handleRequest(
      buildReq("/api/services", "POST", tokenAlan, {
        idempotency_key: `srv-valid-${idx++}`,
        servicio: c.destino,
        plataforma_origen: c.origen,
        servicio_destino: c.destino,
        monto_carga: 1000,
        monto_adicional: 100,
        total_cobrado: 1100,
        pago_efvo: 1100,
        pago_transf: 0,
      }),
      env,
      dbClient,
      scramServer
    );
    assert.equal(res.status, 201, `Esperado 201 para ${c.origen} -> ${c.destino}`);
  }

  // 2. Verificar que MERCADO PAGO -> PREPAY no altera CC David ni fondo 1220 automáticamente
  const resDavid = await handleRequest(buildReq("/api/tesoreria/cuenta-corriente/david", "GET", tokenAlan), env, dbClient, scramServer);
  assert.equal(resDavid.status, 200);
  const dataDavid = await resDavid.json();
  assert.equal(dataDavid.obligacion_pendiente_david_cents, 0);
  assert.equal(dataDavid.saldo_1220_prepay_digital_cents, 0);

  // 3. Rechazo estricto (422) de DIRECTV, TUENTI, OTRO, RECARGA VIRTUAL y combinaciones no autorizadas
  const invalidCases = [
    { origen: "PREPAY", destino: "DIRECTV" },
    { origen: "PREPAY", destino: "TUENTI" },
    { origen: "PREPAY", destino: "OTRO" },
    { origen: "RECARGA VIRTUAL", destino: "SUBE" },
    { origen: "CLARO COMERCIOS", destino: "SUBE" },
    { origen: "PREPAY", destino: "PREPAY" },
  ];

  for (const c of invalidCases) {
    const res = await handleRequest(
      buildReq("/api/services", "POST", tokenAlan, {
        idempotency_key: `srv-invalid-${idx++}`,
        servicio: c.destino,
        plataforma_origen: c.origen,
        servicio_destino: c.destino,
        monto_carga: 1000,
        monto_adicional: 100,
        total_cobrado: 1100,
        pago_efvo: 1100,
        pago_transf: 0,
      }),
      env,
      dbClient,
      scramServer
    );
    assert.equal(res.status, 422, `Esperado 422 para ${c.origen} -> ${c.destino}`);
  }
});

test("Worker Unificado: Tesorería Integral (David, Preventistas, Arqueos y Cierre Diario 1110->1120 / 1160->1130)", async () => {
  const dbClient = createClient({ url: ":memory:" });
  await inicializarEsquemaCompleto(dbClient);

  const env = {
    JWT_SECRET,
    SCRAM_SERVER_SECRET,
    CORS_ALLOWED_ORIGINS: "https://genaro-pos-live.pages.dev",
  };
  const scramServer = new ScramServer(new Map(), Buffer.from(SCRAM_SERVER_SECRET, "hex"));
  const tokenYilda = generarJwt("yilda", "admin", JWT_SECRET);

  // 1. Verificar 10 fondos iniciales en $0
  const resFondos = await handleRequest(buildReq("/api/tesoreria/fondos", "GET", tokenYilda), env, dbClient, scramServer);
  assert.equal(resFondos.status, 200);
  const fondos = await resFondos.json();
  assert.equal(fondos.length, 10);
  for (const f of fondos) {
    assert.equal(f.saldo_centavos, 0);
  }

  // 2. Configurar aperturas iniciales con Yilda: 1110 ($50.000), 1120 ($30.000), 1130 ($100.000), 1160 ($20.000)
  for (const [cod, monto] of [["1110", 5000000], ["1120", 3000000], ["1130", 10000000], ["1160", 2000000]]) {
    const resAp = await handleRequest(
      buildReq("/api/tesoreria/apertura/saldos-iniciales", "POST", tokenYilda, {
        codigo: cod,
        saldo_nuevo_cents: monto,
        motivo: `Apertura inicial ${cod}`,
      }),
      env,
      dbClient,
      scramServer
    );
    assert.equal(resAp.status, 200);
  }

  // 3. Cuenta Corriente David: Reconocimiento histórico ($10.000) + Acreditación ($40.000) - Pago desde 1130 ($25.000) = $25.000
  const resRec = await handleRequest(
    buildReq(
      "/api/tesoreria/cuenta-corriente/david/reconocimiento-historico",
      "POST",
      tokenYilda,
      {
        monto_centavos: 1000000,
        motivo: "Saldo histórico inicial reconocido",
        confirmacion_explicita: true,
      },
      "idem-david-rec-1"
    ),
    env,
    dbClient,
    scramServer
  );
  assert.equal(resRec.status, 200);

  const resAcred = await handleRequest(
    buildReq(
      "/api/tesoreria/cuenta-corriente/david/acreditacion",
      "POST",
      tokenYilda,
      {
        monto_centavos: 4000000,
        referencia: "ACRED-001",
        motivo: "Habilitación de saldo Prepay",
      },
      "idem-david-acred-1"
    ),
    env,
    dbClient,
    scramServer
  );
  assert.equal(resAcred.status, 200);

  const resPago = await handleRequest(
    buildReq(
      "/api/tesoreria/cuenta-corriente/david/pago",
      "POST",
      tokenYilda,
      {
        monto_centavos: 2500000,
        referencia: "REC-001",
        motivo: "Pago parcial con efectivo 1130",
      },
      "idem-david-pago-1"
    ),
    env,
    dbClient,
    scramServer
  );
  assert.equal(resPago.status, 200);

  const resEstadoDavid = await handleRequest(buildReq("/api/tesoreria/cuenta-corriente/david", "GET", tokenYilda), env, dbClient, scramServer);
  const estadoDavid = await resEstadoDavid.json();
  assert.equal(estadoDavid.obligacion_pendiente_david_cents, 2500000);
  assert.equal(estadoDavid.saldo_1220_prepay_digital_cents, 4000000);
  assert.equal(estadoDavid.saldo_1130_prepay_efectivo_cents, 7500000);

  // 4. Preventistas: Anticipo de $40.000 desde 1130 -> Reposición jerárquica (usa $30.000 de 1120 + $10.000 de 1110)
  const resAnt = await handleRequest(
    buildReq(
      "/api/tesoreria/preventistas/anticipos",
      "POST",
      tokenYilda,
      {
        proveedor: "Distribuidora Norte",
        motivo: "Pago pedido gaseosas",
        monto_centavos: 4000000,
      },
      "idem-prev-ant-1"
    ),
    env,
    dbClient,
    scramServer
  );
  assert.equal(resAnt.status, 200);
  const anticipo = await resAnt.json();
  assert.equal(anticipo.estado, "PENDIENTE_REPOSICION");

  const resRep = await handleRequest(
    buildReq(
      `/api/tesoreria/preventistas/anticipos/${anticipo.id}/reposicion`,
      "POST",
      tokenYilda,
      {
        monto_centavos: 4000000,
        motivo: "Reposición completa jerárquica",
      },
      "idem-prev-rep-1"
    ),
    env,
    dbClient,
    scramServer
  );
  assert.equal(resRep.status, 200);
  const repData = await resRep.json();
  assert.equal(repData.anticipo.estado, "REPUESTO_COMPLETO");
  assert.equal(repData.desglose_jerarquico.monto_desde_1120_cents, 3000000);
  assert.equal(repData.desglose_jerarquico.monto_desde_1110_cents, 1000000);

  // 5. Cierre Diario: Jornada actual -> Arqueos -> Iniciar cierre -> Vaciado 1110->1120 -> Vaciado 1160->1130 -> Confirmar Cierre
  const resJornada = await handleRequest(buildReq("/api/tesoreria/jornada/actual", "GET", tokenYilda), env, dbClient, scramServer);
  assert.equal(resJornada.status, 200);
  const jornada = await resJornada.json();

  // Saldos esperados actuales: 1110 = 4000000, 1120 = 0, 1130 = 7500000, 1140 = 0, 1150 = 0, 1160 = 2000000, 1210..1310 = 0 (excepto 1220 = 4000000)
  const saldosEsperados = {
    "1110": 4000000,
    "1120": 0,
    "1130": 7500000,
    "1140": 0,
    "1150": 0,
    "1160": 2000000,
    "1210": 0,
    "1220": 4000000,
    "1230": 0,
    "1310": 0,
  };
  for (const [cod, monto] of Object.entries(saldosEsperados)) {
    const resArq = await handleRequest(
      buildReq("/api/tesoreria/arqueos", "POST", tokenYilda, {
        codigo_fondo: cod,
        saldo_contado_centavos: monto,
        jornada_id: jornada.id,
        observaciones: `Arqueo conforme ${cod}`,
        realizado_por: "yilda",
      }),
      env,
      dbClient,
      scramServer
    );
    assert.equal(resArq.status, 200);
  }

  const resIniCierre = await handleRequest(
    buildReq("/api/tesoreria/cierres/iniciar", "POST", tokenYilda, {
      journal_id: jornada.id,
      iniciado_por: "yilda",
      observaciones: "Inicio cierre diario",
    }),
    env,
    dbClient,
    scramServer
  );
  assert.equal(resIniCierre.status, 200);

  const resVac1110 = await handleRequest(
    buildReq("/api/tesoreria/cierres/vaciado-1110", "POST", tokenYilda, {
      journal_id: jornada.id,
      ejecutado_por: "yilda",
    }),
    env,
    dbClient,
    scramServer
  );
  assert.equal(resVac1110.status, 200);

  const resVac1160 = await handleRequest(
    buildReq("/api/tesoreria/cierres/vaciado-1160", "POST", tokenYilda, {
      journal_id: jornada.id,
      ejecutado_por: "yilda",
    }),
    env,
    dbClient,
    scramServer
  );
  assert.equal(resVac1160.status, 200);

  // Registrar arqueos post-vaciado para 1110, 1120, 1160, 1130
  for (const [cod, monto] of [["1110", 0], ["1120", 4000000], ["1160", 0], ["1130", 9500000]]) {
    await handleRequest(
      buildReq("/api/tesoreria/arqueos", "POST", tokenYilda, {
        codigo_fondo: cod,
        saldo_contado_centavos: monto,
        jornada_id: jornada.id,
        observaciones: `Arqueo post-vaciado ${cod}`,
        realizado_por: "yilda",
      }),
      env,
      dbClient,
      scramServer
    );
  }

  const resConfCierre = await handleRequest(
    buildReq("/api/tesoreria/cierres/confirmar", "POST", tokenYilda, {
      journal_id: jornada.id,
      cerrado_por: "yilda",
      observaciones: "Cierre diario confirmado",
    }),
    env,
    dbClient,
    scramServer
  );
  assert.equal(resConfCierre.status, 200);
  const cierreFinal = await resConfCierre.json();
  assert.equal(cierreFinal.status, "CONFIRMED");
  assert.equal(cierreFinal.sweep_amount_cents, 4000000);
  assert.equal(cierreFinal.prepay_sweep_amount_cents, 2000000);

  // Verificar que tras el cierre: 1110 = 0, 1160 = 0, 1120 = 4000000, 1130 = 9500000
  const resFondosPost = await handleRequest(buildReq("/api/tesoreria/fondos", "GET", tokenYilda), env, dbClient, scramServer);
  const fondosPost = await resFondosPost.json();
  const mapPost = Object.fromEntries(fondosPost.map((f) => [f.codigo, f.saldo_centavos]));
  assert.equal(mapPost["1110"], 0);
  assert.equal(mapPost["1160"], 0);
  assert.equal(mapPost["1120"], 4000000);
  assert.equal(mapPost["1130"], 9500000);

  // 6. Abrir siguiente jornada y verificar apertura en $0 para 1110 y 1160
  const resSigJornada = await handleRequest(
    buildReq("/api/tesoreria/jornada/siguiente", "POST", tokenYilda, {
      observaciones: "Apertura día siguiente con 1110 y 1160 en $0",
    }),
    env,
    dbClient,
    scramServer
  );
  assert.equal(resSigJornada.status, 200);
  const sigJornada = await resSigJornada.json();
  assert.equal(sigJornada.estado, "ABIERTA");
  const det1110 = sigJornada.detalles.find((d) => d.codigo_fondo === "1110");
  const det1160 = sigJornada.detalles.find((d) => d.codigo_fondo === "1160");
  assert.equal(det1110.saldo_apertura_centavos, 0);
  assert.equal(det1160.saldo_apertura_centavos, 0);
});

test("Worker Unificado: Regla Económica PREPAY (Efectivo 1160+1140, Transferencia 1210 + Comp. 1120->1130, Cargo Diario $350 en 1220 y Bloqueo sin 1110 fallback)", async () => {
  const dbClient = createClient({ url: ":memory:" });
  await inicializarEsquemaCompleto(dbClient);

  const env = {
    JWT_SECRET,
    SCRAM_SERVER_SECRET,
    CORS_ALLOWED_ORIGINS: "https://genaro-pos-live.pages.dev",
  };
  const scramServer = new ScramServer(new Map(), Buffer.from(SCRAM_SERVER_SECRET, "hex"));
  const tokenAlan = generarJwt("alan", "admin", JWT_SECRET);

  // Configurar 1110 = $50.000 y 1120 = $3.000; 1220 inicia en $0
  await handleRequest(
    buildReq("/api/tesoreria/apertura/saldos-iniciales", "POST", tokenAlan, {
      codigo: "1110",
      saldo_nuevo_cents: 5000000,
      motivo: "Apertura 1110",
    }),
    env,
    dbClient,
    scramServer
  );
  await handleRequest(
    buildReq("/api/tesoreria/apertura/saldos-iniciales", "POST", tokenAlan, {
      codigo: "1120",
      saldo_nuevo_cents: 300000,
      motivo: "Apertura 1120 ($3.000)",
    }),
    env,
    dbClient,
    scramServer
  );

  // 1. Intento de recarga PREPAY con 1220 en $0 -> Debe rechazarse con 422
  const resSin1220 = await handleRequest(
    buildReq("/api/services", "POST", tokenAlan, {
      idempotency_key: "prepay-no-1220",
      servicio: "Personal",
      plataforma_origen: "PREPAY",
      servicio_destino: "Personal",
      monto_carga: 2000,
      monto_adicional: 100,
      total_cobrado: 2100,
      pago_efvo: 2100,
      pago_transf: 0,
    }),
    env,
    dbClient,
    scramServer
  );
  assert.equal(resSin1220.status, 422);

  // 2. Acreditar $10.000 (1000000 centavos) de David en 1220
  const resAcred = await handleRequest(
    buildReq(
      "/api/tesoreria/cuenta-corriente/david/acreditacion",
      "POST",
      tokenAlan,
      {
        monto_centavos: 1000000,
        referencia: "ACRED-PREPAY-1",
        motivo: "Habilitación saldo digital PREPAY",
      },
      "idem-acred-prepay-1"
    ),
    env,
    dbClient,
    scramServer
  );
  assert.equal(resAcred.status, 200);

  // 3. Primera recarga PREPAY en efectivo ($2.000 capital + $100 adicional)
  // Debe descontar de 1220: $2.000 + $350 cargo diario = $2.350 (quedan $7.650 = 765000 cents)
  // Debe ingresar $2.000 a 1160 y $100 a 1140
  const resEfvo1 = await handleRequest(
    buildReq("/api/services", "POST", tokenAlan, {
      idempotency_key: "prepay-efvo-1",
      servicio: "Personal",
      plataforma_origen: "PREPAY",
      servicio_destino: "Personal",
      monto_carga: 2000,
      monto_adicional: 100,
      total_cobrado: 2100,
      pago_efvo: 2100,
      pago_transf: 0,
    }),
    env,
    dbClient,
    scramServer
  );
  assert.equal(resEfvo1.status, 201);

  // 4. Segunda recarga PREPAY en efectivo el mismo día ($1.000 capital + $100 adicional)
  // No debe volver a cobrar los $350 diarios (idempotencia diaria). 1220 baja $1.000 -> quedan $6.650 (665000 cents)
  const resEfvo2 = await handleRequest(
    buildReq("/api/services", "POST", tokenAlan, {
      idempotency_key: "prepay-efvo-2",
      servicio: "SUBE",
      plataforma_origen: "PREPAY",
      servicio_destino: "SUBE",
      monto_carga: 1000,
      monto_adicional: 100,
      total_cobrado: 1100,
      pago_efvo: 1100,
      pago_transf: 0,
    }),
    env,
    dbClient,
    scramServer
  );
  assert.equal(resEfvo2.status, 201);

  // 5. Recarga PREPAY por transferencia ($2.000 capital + $100 adicional = $2.100 transferencia)
  // 1210 sube $2.100 (210000 cents), 1220 baja $2.000 (quedan 465000 cents),
  // 1120 ($3.000) transfiere $2.000 a 1130 -> 1120 queda en $1.000 (100000 cents) y 1130 sube a $2.000 (200000 cents)
  const resTransfOk = await handleRequest(
    buildReq("/api/services", "POST", tokenAlan, {
      idempotency_key: "prepay-transf-ok",
      servicio: "Movistar",
      plataforma_origen: "PREPAY",
      servicio_destino: "Movistar",
      monto_carga: 2000,
      monto_adicional: 100,
      total_cobrado: 2100,
      pago_efvo: 0,
      pago_transf: 2100,
    }),
    env,
    dbClient,
    scramServer
  );
  assert.equal(resTransfOk.status, 201);

  // 6. Nueva recarga PREPAY por transferencia de $2.000 cuando 1120 sólo tiene $1.000 (aunque 1110 tenga $50.000)
  // Debe rechazarse con 422 sin usar 1110 como fallback
  const resTransfFail = await handleRequest(
    buildReq("/api/services", "POST", tokenAlan, {
      idempotency_key: "prepay-transf-sin-1120",
      servicio: "Claro",
      plataforma_origen: "PREPAY",
      servicio_destino: "Claro",
      monto_carga: 2000,
      monto_adicional: 100,
      total_cobrado: 2100,
      pago_efvo: 0,
      pago_transf: 2100,
    }),
    env,
    dbClient,
    scramServer
  );
  assert.equal(resTransfFail.status, 422);

  // Verificar saldos finales exactos y que la deuda con David sigue intacta en $10.000 (el cargo de $350 no suma deuda a David)
  const resFondos = await handleRequest(buildReq("/api/tesoreria/fondos", "GET", tokenAlan), env, dbClient, scramServer);
  const fondos = await resFondos.json();
  const mapFondos = Object.fromEntries(fondos.map((f) => [f.codigo, f.saldo_centavos]));

  assert.equal(mapFondos["1110"], 5000000); // Intacto
  assert.equal(mapFondos["1120"], 100000); // $3.000 - $2.000 compensados a 1160
  assert.equal(mapFondos["1130"], 0); // 1130 en $0 hasta cierre diario
  assert.equal(mapFondos["1140"], 20000); // $100 + $100 adicionales efectivo
  assert.equal(mapFondos["1160"], 500000); // $2.000 + $1.000 capital efectivo + $2.000 compensación transf. (barre a 1130 al cierre)
  assert.equal(mapFondos["1210"], 210000); // $2.100 cobro transferencia PREPAY
  assert.equal(mapFondos["1220"], 465000); // $10.000 - $350 cargo diario - $2.000 - $1.000 - $2.000 = $4.650

  const resDavid = await handleRequest(buildReq("/api/tesoreria/cuenta-corriente/david", "GET", tokenAlan), env, dbClient, scramServer);
  const dataDavid = await resDavid.json();
  assert.equal(dataDavid.obligacion_pendiente_david_cents, 1000000);
});

test("Worker Unificado: Sección 8.6 — Las 12 Pruebas Obligatorias del Circuito PREPAY (Reservas Síncronas, Concurrencia, Outbox, Fallos y Reconciliación)", async () => {
  const dbClient = createClient({ url: ":memory:" });
  await inicializarEsquemaCompleto(dbClient);

  const env = {
    JWT_SECRET,
    SCRAM_SERVER_SECRET,
    CORS_ALLOWED_ORIGINS: "https://genaro-pos-live.pages.dev",
  };
  const scramServer = new ScramServer(new Map(), Buffer.from(SCRAM_SERVER_SECRET, "hex"));
  const tokenAlan = generarJwt("alan", "admin", JWT_SECRET);

  // Configurar 1110 = $50.000, 1120 = $5.000 y acreditar $20.000 de David en 1220
  await handleRequest(
    buildReq("/api/tesoreria/apertura/saldos-iniciales", "POST", tokenAlan, {
      codigo: "1110",
      saldo_nuevo_cents: 5000000,
      motivo: "Apertura 1110",
    }),
    env,
    dbClient,
    scramServer
  );
  await handleRequest(
    buildReq("/api/tesoreria/apertura/saldos-iniciales", "POST", tokenAlan, {
      codigo: "1120",
      saldo_nuevo_cents: 500000,
      motivo: "Apertura 1120 ($5.000)",
    }),
    env,
    dbClient,
    scramServer
  );
  await handleRequest(
    buildReq(
      "/api/tesoreria/cuenta-corriente/david/acreditacion",
      "POST",
      tokenAlan,
      {
        monto_centavos: 2000000,
        referencia: "ACRED-86",
        motivo: "Acreditación inicial $20.000 en 1220",
      },
      "idem-86-acred"
    ),
    env,
    dbClient,
    scramServer
  );

  // Prueba 7 y 11 (Parte A): Interrupción después de reservar (huérfana antes del commit comercial) y reconciliación de reservas
  const resOrphan = await handleRequest(
    buildReq("/api/services", "POST", tokenAlan, {
      idempotency_key: "p86-orphan-1",
      servicio: "SUBE",
      plataforma_origen: "PREPAY",
      servicio_destino: "SUBE",
      monto_carga: 1000,
      monto_adicional: 100,
      total_cobrado: 1100,
      pago_efvo: 1100,
      pago_transf: 0,
      _simulate_interrupt_after_reservation: true,
      _keep_orphan_reservation: true,
    }),
    env,
    dbClient,
    scramServer
  );
  assert.equal(resOrphan.status, 500);

  // Verificar que la reserva huérfana quedó en ACTIVE y bloqueó $1.350 en 1220, y al reconciliar se libera a RELEASED
  const coordOrphanBefore = await dbClient.execute("SELECT estado, asume_cargo_diario FROM tesoreria_prepay_coordinacion WHERE idempotency_key = 'p86-orphan-1';");
  assert.equal(coordOrphanBefore.rows[0].estado, "ACTIVE");
  assert.equal(Number(coordOrphanBefore.rows[0].asume_cargo_diario), 1);

  const resDrainOrphan = await handleRequest(buildReq("/api/tesoreria/commercial-bridge/drain", "POST", tokenAlan), env, dbClient, scramServer);
  assert.equal(resDrainOrphan.status, 200);

  const coordOrphanAfter = await dbClient.execute("SELECT estado FROM tesoreria_prepay_coordinacion WHERE idempotency_key = 'p86-orphan-1';");
  assert.equal(coordOrphanAfter.rows[0].estado, "RELEASED");
  const cargoAfterRelease = await dbClient.execute("SELECT estado FROM tesoreria_prepay_cargo_diario;");
  assert.equal(cargoAfterRelease.rows[0].estado, "RELEASED");

  // Prueba 1: Primera operación exitosa del día (asume el cargo diario de $350 luego de liberarse la huérfana)
  const resOp1 = await handleRequest(
    buildReq("/api/services", "POST", tokenAlan, {
      idempotency_key: "p86-op1",
      servicio: "Personal",
      plataforma_origen: "PREPAY",
      servicio_destino: "Personal",
      monto_carga: 2000,
      monto_adicional: 100,
      total_cobrado: 2100,
      pago_efvo: 2100,
      pago_transf: 0,
    }),
    env,
    dbClient,
    scramServer
  );
  assert.equal(resOp1.status, 201);

  // Prueba 2: Segunda operación del día (no vuelve a cobrar $350)
  const resOp2 = await handleRequest(
    buildReq("/api/services", "POST", tokenAlan, {
      idempotency_key: "p86-op2",
      servicio: "Movistar",
      plataforma_origen: "PREPAY",
      servicio_destino: "Movistar",
      monto_carga: 1000,
      monto_adicional: 100,
      total_cobrado: 1100,
      pago_efvo: 1100,
      pago_transf: 0,
    }),
    env,
    dbClient,
    scramServer
  );
  assert.equal(resOp2.status, 201);

  // Prueba 3: Dos operaciones concurrentes (Efectivo $1.000 y Transferencia $2.000)
  const [resConcA, resConcB] = await Promise.all([
    handleRequest(
      buildReq("/api/services", "POST", tokenAlan, {
        idempotency_key: "p86-conc-a",
        servicio: "SUBE",
        plataforma_origen: "PREPAY",
        servicio_destino: "SUBE",
        monto_carga: 1000,
        monto_adicional: 100,
        total_cobrado: 1100,
        pago_efvo: 1100,
        pago_transf: 0,
      }),
      env,
      dbClient,
      scramServer
    ),
    handleRequest(
      buildReq("/api/services", "POST", tokenAlan, {
        idempotency_key: "p86-conc-b",
        servicio: "Claro",
        plataforma_origen: "PREPAY",
        servicio_destino: "Claro",
        monto_carga: 2000,
        monto_adicional: 100,
        total_cobrado: 2100,
        pago_efvo: 0,
        pago_transf: 2100,
      }),
      env,
      dbClient,
      scramServer
    ),
  ]);
  assert.equal(resConcA.status, 201);
  assert.equal(resConcB.status, 201);

  // Prueba 4: Insuficiencia de 1220 (quedan $20.000 - $350 - $6.000 = $13.650; intentamos $15.000)
  const resInsuf1220 = await handleRequest(
    buildReq("/api/services", "POST", tokenAlan, {
      idempotency_key: "p86-insuf-1220",
      servicio: "Personal",
      plataforma_origen: "PREPAY",
      servicio_destino: "Personal",
      monto_carga: 15000,
      monto_adicional: 800,
      total_cobrado: 15800,
      pago_efvo: 15800,
      pago_transf: 0,
    }),
    env,
    dbClient,
    scramServer
  );
  assert.equal(resInsuf1220.status, 422);

  // Prueba 5: Insuficiencia de 1120 (quedan $5.000 - $2.000 = $3.000 en 1120; intentamos transferencia de $4.000)
  const resInsuf1120 = await handleRequest(
    buildReq("/api/services", "POST", tokenAlan, {
      idempotency_key: "p86-insuf-1120",
      servicio: "Claro",
      plataforma_origen: "PREPAY",
      servicio_destino: "Claro",
      monto_carga: 4000,
      monto_adicional: 200,
      total_cobrado: 4200,
      pago_efvo: 0,
      pago_transf: 4200,
    }),
    env,
    dbClient,
    scramServer
  );
  assert.equal(resInsuf1120.status, 422);

  // Prueba 6: Reintento con la misma clave idempotente
  const resRetry = await handleRequest(
    buildReq("/api/services", "POST", tokenAlan, {
      idempotency_key: "p86-op1",
      servicio: "Personal",
      plataforma_origen: "PREPAY",
      servicio_destino: "Personal",
      monto_carga: 2000,
      monto_adicional: 100,
      total_cobrado: 2100,
      pago_efvo: 2100,
      pago_transf: 0,
    }),
    env,
    dbClient,
    scramServer
  );
  assert.equal(resRetry.status, 200);
  const retryData = await resRetry.json();
  assert.equal(retryData.status, "existente");

  // Prueba 8: Interrupción después del commit comercial (queda en COMMERCIAL_COMMITTED y outbox PENDING)
  const resIntCommit = await handleRequest(
    buildReq("/api/services", "POST", tokenAlan, {
      idempotency_key: "p86-int-commit",
      servicio: "SUBE",
      plataforma_origen: "PREPAY",
      servicio_destino: "SUBE",
      monto_carga: 1000,
      monto_adicional: 100,
      total_cobrado: 1100,
      pago_efvo: 1100,
      pago_transf: 0,
      _simulate_interrupt_after_commercial_commit: true,
    }),
    env,
    dbClient,
    scramServer
  );
  assert.equal(resIntCommit.status, 201);
  const stCommit = await dbClient.execute("SELECT estado FROM tesoreria_prepay_coordinacion WHERE idempotency_key = 'p86-int-commit';");
  assert.equal(stCommit.rows[0].estado, "COMMERCIAL_COMMITTED");

  // Prueba 9: Error durante el procesamiento del outbox (queda en RECONCILIATION_REQUIRED y outbox FAILED)
  const resOutboxErr = await handleRequest(
    buildReq("/api/services", "POST", tokenAlan, {
      idempotency_key: "p86-outbox-err",
      servicio: "Movistar",
      plataforma_origen: "PREPAY",
      servicio_destino: "Movistar",
      monto_carga: 1000,
      monto_adicional: 100,
      total_cobrado: 1100,
      pago_efvo: 0,
      pago_transf: 1100,
      _simulate_outbox_error: true,
    }),
    env,
    dbClient,
    scramServer
  );
  assert.equal(resOutboxErr.status, 201);
  const stErr = await dbClient.execute("SELECT estado FROM tesoreria_prepay_coordinacion WHERE idempotency_key = 'p86-outbox-err';");
  assert.equal(stErr.rows[0].estado, "RECONCILIATION_REQUIRED");

  // Prueba 10: Recuperación posterior mediante drain / reconciliación
  const resDrainRecovery = await handleRequest(buildReq("/api/tesoreria/commercial-bridge/drain", "POST", tokenAlan), env, dbClient, scramServer);
  assert.equal(resDrainRecovery.status, 200);

  const stRecovered = await dbClient.execute(
    "SELECT idempotency_key, estado FROM tesoreria_prepay_coordinacion WHERE idempotency_key IN ('p86-int-commit', 'p86-outbox-err') ORDER BY idempotency_key;"
  );
  assert.equal(stRecovered.rows[0].estado, "FULLY_APPLIED");
  assert.equal(stRecovered.rows[1].estado, "FULLY_APPLIED");

  // Prueba 12: Verificación de que el cargo diario ($350) no se duplicó en toda la jornada
  const feeCountRes = await dbClient.execute(
    "SELECT COUNT(*) as cnt, COALESCE(SUM(amount_cents), 0) as total_fee FROM tesoreria_movimientos WHERE counterpart = 'COSTO_OPERATIVO_PREPAY';"
  );
  assert.equal(Number(feeCountRes.rows[0].cnt), 1);
  assert.equal(Number(feeCountRes.rows[0].total_fee), 35000);
});
