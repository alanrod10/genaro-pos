/**
 * Suite de Pruebas: Persistencia y Transacciones con @libsql/client
 */

import test from "node:test";
import assert from "node:assert/strict";
import { createClient } from "@libsql/client";
import {
  inicializarEsquemaSintetico,
  poblarDatosPrueba,
  obtenerCatalogo,
  registrarVentaTransaccional,
  anularVentaTransaccional,
  registrarCargaServicio,
  listarServiciosPorRango,
} from "../src/db/repository.js";

test("Persistencia: Inicialización y consulta de catálogo (solo productos activos)", async () => {
  const client = createClient({ url: ":memory:" });
  await inicializarEsquemaSintetico(client);
  await poblarDatosPrueba(client);

  const catalogo = await obtenerCatalogo(client);
  assert.equal(catalogo.length, 2, "Debe retornar solo los 2 productos activos");
  assert.equal(catalogo[0].nombre, "Alfajor Chocolate");
  assert.equal(catalogo[1].nombre, "Paracetamol 500mg");

  // El producto descontinuado (activo = 0) no debe aparecer
  assert.ok(!catalogo.some((p) => p.nombre === "Producto Descontinuado"));
});

test("Persistencia: Transacción atómica de Venta con Costos Congelados y Auditoría", async () => {
  const client = createClient({ url: ":memory:" });
  await inicializarEsquemaSintetico(client);
  await poblarDatosPrueba(client);

  const ventaPayload = {
    ticket: {
      id: "ticket-1001",
      fecha_hora_utc: "2026-10-27T18:00:00.000Z",
      total_venta: 3700,
      monto_efectivo: 3700,
      monto_transferencia: 0,
      metodo_pago: "EFECTIVO",
    },
    items: [
      { producto_nombre: "Alfajor Chocolate", cantidad: 1, precio_unitario: 1500 },
      { producto_nombre: "Paracetamol 500mg", cantidad: 1, precio_unitario: 2200 },
    ],
    usuario: "alan",
    idempotencyKey: "idem-ticket-1001",
  };

  const res = await registrarVentaTransaccional(client, ventaPayload);
  assert.equal(res.status, "CREATED");
  assert.equal(res.total_venta, 3700);

  // Verificar que el costo unitario quedó congelado en la tabla de ítems
  const itemsRs = await client.execute({
    sql: "SELECT producto_nombre, cantidad, precio_unitario, costo_unitario, subtotal FROM ticket_items WHERE ticket_id = ? ORDER BY id;",
    args: ["ticket-1001"],
  });
  assert.equal(itemsRs.rows.length, 2);
  assert.equal(itemsRs.rows[0].costo_unitario, 950, "Costo de Alfajor congelado");
  assert.equal(itemsRs.rows[1].costo_unitario, 1400, "Costo de Paracetamol congelado");

  // Verificar auditoría inmutable
  const auditRs = await client.execute({
    sql: "SELECT ticket_id, usuario, accion, motivo FROM ventas_auditoria WHERE ticket_id = ?;",
    args: ["ticket-1001"],
  });
  assert.equal(auditRs.rows.length, 1);
  assert.equal(auditRs.rows[0].usuario, "alan");
  assert.equal(auditRs.rows[0].accion, "CREACION");
});

test("Persistencia: Rollback atómico completo ante fallo en cualquier sentencia del lote", async () => {
  const client = createClient({ url: ":memory:" });
  await inicializarEsquemaSintetico(client);
  await poblarDatosPrueba(client);

  const ventaInconsistente = {
    ticket: {
      id: "ticket-fallo",
      fecha_hora_utc: "2026-10-27T18:05:00.000Z",
      total_venta: 5000,
      monto_efectivo: 3000, // Inconsistencia matemática intencional: 3000 + 0 != 5000
      monto_transferencia: 0,
      metodo_pago: "EFECTIVO",
    },
    items: [
      { producto_nombre: "Alfajor Chocolate", cantidad: 2, precio_unitario: 1500 },
    ],
    usuario: "yilda",
  };

  await assert.rejects(
    async () => registrarVentaTransaccional(client, ventaInconsistente),
    /El monto en efectivo más transferencia debe ser exactamente igual al total/
  );

  // Verificar que NADA se insertó (ni ticket, ni ítems, ni auditoría)
  const ticketsRs = await client.execute("SELECT COUNT(*) as c FROM tickets WHERE id = 'ticket-fallo';");
  assert.equal(ticketsRs.rows[0].c, 0);
  const itemsRs = await client.execute("SELECT COUNT(*) as c FROM ticket_items WHERE ticket_id = 'ticket-fallo';");
  assert.equal(itemsRs.rows[0].c, 0);
  const auditRs = await client.execute("SELECT COUNT(*) as c FROM ventas_auditoria WHERE ticket_id = 'ticket-fallo';");
  assert.equal(auditRs.rows[0].c, 0);
});

test("Persistencia: Idempotencia estricta en reintentos de ventas y servicios", async () => {
  const client = createClient({ url: ":memory:" });
  await inicializarEsquemaSintetico(client);
  await poblarDatosPrueba(client);

  const payload = {
    ticket: {
      id: "ticket-idem-test",
      fecha_hora_utc: "2026-10-27T18:10:00.000Z",
      total_venta: 1500,
      monto_efectivo: 1500,
      monto_transferencia: 0,
      metodo_pago: "EFECTIVO",
    },
    items: [{ producto_nombre: "Alfajor Chocolate", cantidad: 1, precio_unitario: 1500 }],
    usuario: "alan",
    idempotencyKey: "key-idem-12345",
  };

  // 1. Primer envío
  const res1 = await registrarVentaTransaccional(client, payload);
  assert.equal(res1.status, "CREATED");

  // 2. Reintento con la misma clave de idempotencia
  const res2 = await registrarVentaTransaccional(client, payload);
  assert.equal(res2.status, "IDEMPOTENT_REPLAY");
  assert.equal(res2.id, "ticket-idem-test");

  // Verificar que la base de datos solo tiene 1 ticket
  const countRs = await client.execute("SELECT COUNT(*) as c FROM tickets;");
  assert.equal(countRs.rows[0].c, 1);

  // Idempotencia en Servicios (índice único uq_cargas_idempotency_key)
  const servicio = {
    id: "serv-001",
    fecha_hora_utc: "2026-10-27T18:15:00.000Z",
    tipo_servicio: "PREPAY",
    monto_capital: 2000,
    cargo_comision: 100,
    monto_total: 2100,
    metodo_pago: "EFECTIVO",
  };

  await registrarCargaServicio(client, { servicio, usuario: "alan", idempotencyKey: "idem-serv-999" });

  // Segundo intento con la misma clave de idempotencia en servicios debe lanzar violación de índice único
  await assert.rejects(
    async () => registrarCargaServicio(client, {
      servicio: { ...servicio, id: "serv-002" },
      usuario: "alan",
      idempotencyKey: "idem-serv-999"
    }),
    /UNIQUE constraint failed/
  );
});

test("Persistencia: Anulación lógica y preservación inmutable en auditoría", async () => {
  const client = createClient({ url: ":memory:" });
  await inicializarEsquemaSintetico(client);
  await poblarDatosPrueba(client);

  const payload = {
    ticket: {
      id: "ticket-anulacion",
      fecha_hora_utc: "2026-10-27T18:20:00.000Z",
      total_venta: 1500,
      monto_efectivo: 1500,
      monto_transferencia: 0,
      metodo_pago: "EFECTIVO",
    },
    items: [{ producto_nombre: "Alfajor Chocolate", cantidad: 1, precio_unitario: 1500 }],
    usuario: "alan",
  };
  await registrarVentaTransaccional(client, payload);

  // Anular la venta
  await anularVentaTransaccional(client, "ticket-anulacion", "yilda", "Error de digitación en caja");

  // Comprobar que el ticket permanece en la tabla pero con anulado = 1
  const tRs = await client.execute("SELECT id, anulado FROM tickets WHERE id = 'ticket-anulacion';");
  assert.equal(tRs.rows[0].anulado, 1);

  // Comprobar que en auditoría existen ambos eventos (CREACION y ANULACION)
  const audRs = await client.execute("SELECT usuario, accion, motivo FROM ventas_auditoria WHERE ticket_id = 'ticket-anulacion' ORDER BY id;");
  assert.equal(audRs.rows.length, 2);
  assert.equal(audRs.rows[0].accion, "CREACION");
  assert.equal(audRs.rows[1].accion, "ANULACION");
  assert.equal(audRs.rows[1].usuario, "yilda");
  assert.equal(audRs.rows[1].motivo, "Error de digitación en caja");
});

test("Persistencia: Filtro de servicios por rango de fechas (Jujuy UTC)", async () => {
  const client = createClient({ url: ":memory:" });
  await inicializarEsquemaSintetico(client);

  // En Jujuy (UTC-3), el día 2026-10-27 comprende desde 2026-10-27T03:00:00Z hasta 2026-10-28T02:59:59Z
  await client.batch([
    {
      sql: `INSERT INTO cargas_servicios (id, fecha_hora_utc, tipo_servicio, monto_capital, cargo_comision, monto_total, metodo_pago, anulado)
            VALUES (?, ?, 'PREPAY', 1000, 100, 1100, 'EFECTIVO', 0);`,
      args: ["s-ayer", "2026-10-27T02:30:00.000Z"], // Corresponde al día anterior en Jujuy (23:30 hs del 26-10)
    },
    {
      sql: `INSERT INTO cargas_servicios (id, fecha_hora_utc, tipo_servicio, monto_capital, cargo_comision, monto_total, metodo_pago, anulado)
            VALUES (?, ?, 'PREPAY', 2000, 100, 2100, 'EFECTIVO', 0);`,
      args: ["s-hoy-1", "2026-10-27T14:00:00.000Z"], // Día de hoy en Jujuy (11:00 hs del 27-10)
    },
    {
      sql: `INSERT INTO cargas_servicios (id, fecha_hora_utc, tipo_servicio, monto_capital, cargo_comision, monto_total, metodo_pago, anulado)
            VALUES (?, ?, 'PREPAY', 3000, 200, 3200, 'EFECTIVO', 0);`,
      args: ["s-hoy-2", "2026-10-27T22:00:00.000Z"], // Día de hoy en Jujuy (19:00 hs del 27-10)
    },
    {
      sql: `INSERT INTO cargas_servicios (id, fecha_hora_utc, tipo_servicio, monto_capital, cargo_comision, monto_total, metodo_pago, anulado)
            VALUES (?, ?, 'PREPAY', 4000, 200, 4200, 'EFECTIVO', 0);`,
      args: ["s-manana", "2026-10-28T04:00:00.000Z"], // Mañana en Jujuy (01:00 hs del 28-10)
    },
  ], "write");

  // Rango para el 27 de octubre en Jujuy:
  const desdeUtc = "2026-10-27T03:00:00.000Z";
  const hastaUtc = "2026-10-28T02:59:59.999Z";

  const serviciosHoy = await listarServiciosPorRango(client, desdeUtc, hastaUtc);
  assert.equal(serviciosHoy.length, 2, "Solo deben ingresar los 2 servicios del día 27 en Jujuy");
  assert.equal(serviciosHoy[0].id, "s-hoy-2");
  assert.equal(serviciosHoy[1].id, "s-hoy-1");
});
