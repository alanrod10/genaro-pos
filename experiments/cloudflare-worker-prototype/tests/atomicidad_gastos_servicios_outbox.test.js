import test from "node:test";
import assert from "node:assert/strict";
import { createClient } from "@libsql/client";
import {
  inicializarEsquemaCompleto,
  registrarGastoIdempotente,
  registrarCargaIdempotente,
} from "../src/db/adapter.js";
import {
  obtenerBridgeStatusWorker,
  obtenerSyncStatusBridgeWorker,
  listarCommercialEventsWorker,
} from "../src/db/tesoreria_worker.js";

test("A.1: registrarGastoIdempotente registra gasto, auditoría y outbox en lote atómico", async () => {
  const client = createClient({ url: ":memory:" });
  await inicializarEsquemaCompleto(client);

  const res = await registrarGastoIdempotente(client, {
    gasto: {
      monto: 1500,
      tipo: "Librería",
      concepto: "Hojas y lapiceras",
      origen_fondos: "Caja diaria",
      metodo_pago: "EFECTIVO",
    },
    usuario: "cajero1",
    idempotencyKey: "KEY-EXP-TEST-001",
  });

  assert.equal(res.status, "registrado");
  assert.equal(res.monto_salida, 1500);

  // 1. Verificar gasto en base
  const gastoRs = await client.execute("SELECT * FROM gastos_retiros WHERE idempotency_key = 'KEY-EXP-TEST-001';");
  assert.equal(gastoRs.rows.length, 1);
  const gastoId = gastoRs.rows[0].id;

  // 2. Verificar auditoría vinculada
  const auditRs = await client.execute({
    sql: "SELECT * FROM gastos_auditoria WHERE gasto_id = ?;",
    args: [gastoId],
  });
  assert.equal(auditRs.rows.length, 1);
  assert.equal(auditRs.rows[0].accion, "CREAR");
  assert.equal(auditRs.rows[0].actor, "cajero1");

  // 3. Verificar evento Outbox
  const outboxRs = await client.execute({
    sql: "SELECT * FROM commercial_events_outbox WHERE idempotency_key = 'COMM_EXPENSE_KEY-EXP-TEST-001_CONFIRM';",
  });
  assert.equal(outboxRs.rows.length, 1);
  assert.equal(outboxRs.rows[0].event_type, "EXPENSE_CONFIRMED");
  assert.equal(outboxRs.rows[0].entity_id, String(gastoId));
  const payload = JSON.parse(outboxRs.rows[0].payload);
  assert.equal(payload.gasto_id, gastoId);
  assert.equal(payload.monto_salida_cents, 150000);

  // 4. Reintento idempotente con misma clave
  const replay = await registrarGastoIdempotente(client, {
    gasto: {
      monto: 1500,
      tipo: "Librería",
      concepto: "Hojas y lapiceras",
      metodo_pago: "EFECTIVO",
    },
    usuario: "cajero1",
    idempotencyKey: "KEY-EXP-TEST-001",
  });
  assert.equal(replay.status, "existente");
  assert.equal(replay.id, res.id);

  // Conteo no se incrementa
  const countRs = await client.execute("SELECT COUNT(*) as c FROM gastos_retiros;");
  assert.equal(Number(countRs.rows[0].c), 1);
});

test("A.2: registrarCargaIdempotente registra servicio, auditoría y outbox en lote atómico", async () => {
  const client = createClient({ url: ":memory:" });
  await inicializarEsquemaCompleto(client);

  const res = await registrarCargaIdempotente(client, {
    servicio: {
      servicio: "Claro",
      plataforma_origen: "CLARO COMERCIOS",
      servicio_destino: "Claro",
      monto_carga: 2000,
      monto_adicional: 150,
      total_cobrado: 2150,
      pago_efvo: 2150,
      pago_transf: 0,
      idempotency_key: "KEY-SRV-TEST-001",
    },
    usuario: "cajero2",
    idempotencyKey: "KEY-SRV-TEST-001",
  });

  assert.equal(res.status, "registrado");
  assert.equal(res.monto_carga, 2000);

  // 1. Verificar carga en base
  const srvRs = await client.execute("SELECT * FROM cargas_servicios WHERE idempotency_key = 'KEY-SRV-TEST-001';");
  assert.equal(srvRs.rows.length, 1);
  const cargaId = srvRs.rows[0].id;

  // 2. Verificar auditoría vinculada
  const auditRs = await client.execute({
    sql: "SELECT * FROM cargas_servicios_auditoria WHERE carga_id = ?;",
    args: [cargaId],
  });
  assert.equal(auditRs.rows.length, 1);
  assert.equal(auditRs.rows[0].accion, "CREAR");

  // 3. Verificar outbox
  const outboxRs = await client.execute({
    sql: "SELECT * FROM commercial_events_outbox WHERE idempotency_key = 'KEY-SRV-TEST-001';",
  });
  assert.equal(outboxRs.rows.length, 1);
  assert.equal(outboxRs.rows[0].event_type, "SERVICE_CREATED");
  assert.equal(outboxRs.rows[0].entity_id, String(cargaId));
});

test("D.1: Métricas de Outbox distinguen eventos contabilizados limpios vs retenidos (HELD)", async () => {
  const client = createClient({ url: ":memory:" });
  await inicializarEsquemaCompleto(client);

  // Insertar evento 1: Limpio publicado
  await client.execute({
    sql: `INSERT INTO commercial_events_outbox (
            event_id, event_type, entity_type, entity_id, action_type,
            idempotency_key, payload_canonical_hash, payload, status,
            tesoreria_movement_id, created_at, updated_at
          ) VALUES ('evt-1', 'SALE_CONFIRMED', 'TICKET', 'T-1', 'CREATE', 'KEY-1', 'hash1', '{}', 'PUBLISHED', 'MOV-100', datetime('now'), datetime('now'));`,
  });

  // Insertar evento 2: Retenido ambiguo (SUBE sin plataforma)
  await client.execute({
    sql: `INSERT INTO commercial_events_outbox (
            event_id, event_type, entity_type, entity_id, action_type,
            idempotency_key, payload_canonical_hash, payload, status,
            tesoreria_movement_id, created_at, updated_at
          ) VALUES ('evt-2', 'SERVICE_CONFIRMED', 'SERVICE_LOAD', 'S-1', 'CREATE', 'KEY-2', 'hash2', '{}', 'PUBLISHED', 'UNSPECIFIED_PLATFORM_AMBIGUOUS_SERVICE_SUBE_1', datetime('now'), datetime('now'));`,
  });

  // Insertar evento 3: Retenido pendiente de definición
  await client.execute({
    sql: `INSERT INTO commercial_events_outbox (
            event_id, event_type, entity_type, entity_id, action_type,
            idempotency_key, payload_canonical_hash, payload, status,
            tesoreria_movement_id, created_at, updated_at
          ) VALUES ('evt-3', 'SERVICE_CONFIRMED', 'SERVICE_LOAD', 'S-2', 'CREATE', 'KEY-3', 'hash3', '{}', 'PENDING', 'PENDING_DEFINITION_SUBE_PLATFORM', datetime('now'), datetime('now'));`,
  });

  // Insertar evento 4: Pendiente normal sin procesar
  await client.execute({
    sql: `INSERT INTO commercial_events_outbox (
            event_id, event_type, entity_type, entity_id, action_type,
            idempotency_key, payload_canonical_hash, payload, status,
            tesoreria_movement_id, created_at, updated_at
          ) VALUES ('evt-4', 'EXPENSE_CONFIRMED', 'GASTO', 'G-1', 'CREATE', 'KEY-4', 'hash4', '{}', 'PENDING', NULL, datetime('now'), datetime('now'));`,
  });

  const stats = await obtenerBridgeStatusWorker(client);
  assert.equal(stats.total, 4);
  assert.equal(stats.published, 1, "Solo 1 evento publicado limpio (no los retenidos)");
  assert.equal(stats.held_pending_definition, 2, "Exactamente 2 eventos retenidos por falta de definición");
  assert.equal(stats.pending, 1, "Exactamente 1 evento pendiente real");

  const sync = await obtenerSyncStatusBridgeWorker(client);
  assert.equal(sync.sincronizadas, 1);
  assert.equal(sync.held_pending_definition, 2);
  assert.equal(sync.requiere_atencion, true, "Debe requerir atención al haber eventos retenidos");

  // Filtro HELD en listado
  const heldEvents = await listarCommercialEventsWorker(client, { status: "HELD_PENDING_DEFINITION" });
  assert.equal(heldEvents.events.length, 2);

  // Filtro PUBLISHED puro
  const pubEvents = await listarCommercialEventsWorker(client, { status: "PUBLISHED" });
  assert.equal(pubEvents.events.length, 1);
  assert.equal(pubEvents.events[0].event_id, "evt-1");
});
