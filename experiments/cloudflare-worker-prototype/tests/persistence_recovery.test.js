/**
 * Suite de Pruebas: Persistencia en Archivo, Transacciones, Concurrencia y Recuperación de Proceso.
 */

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { createClient } from "@libsql/client";
import {
  inicializarEsquemaCompleto,
  poblarDatosPrueba,
  registrarVentaIdempotente,
  anularVenta,
  obtenerDetalleVenta,
  calcularArqueoDiario,
} from "../src/db/adapter.js";

const DB_FILE = "prototype_candidate_test.db";
const DB_URL = `file:${DB_FILE}`;

function cleanupDb() {
  if (fs.existsSync(DB_FILE)) {
    try {
      fs.unlinkSync(DB_FILE);
    } catch (_) {}
  }
}

test("Recuperación de Proceso y Persistencia: Los datos sobreviven al cierre y recreación del cliente", async () => {
  cleanupDb();

  // 1. Instancia inicial del proceso
  let client = createClient({ url: DB_URL });
  await inicializarEsquemaCompleto(client);
  await poblarDatosPrueba(client);

  // Registrar venta
  const resVenta = await registrarVentaIdempotente(client, {
    ticket: {
      id: "ticket-persistente-001",
      fecha_hora_utc: new Date().toISOString(),
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
    idempotencyKey: "idem-persist-001",
  });
  assert.equal(resVenta.status, "registrado");

  // 2. Simular "Muerte / Reinicio del Proceso"
  client.close();

  // 3. Recrear un nuevo cliente libSQL sobre el mismo archivo persistente
  const newClient = createClient({ url: DB_URL });

  // Verificar que el ticket y sus ítems siguen intactos
  const detalle = await obtenerDetalleVenta(newClient, "ticket-persistente-001");
  assert.ok(detalle, "El ticket debe existir tras el reinicio del proceso");
  assert.equal(detalle.ticket.total_venta, 3700);
  assert.equal(detalle.items.length, 2);
  assert.equal(detalle.items[0].costo_unitario, 950);
  assert.equal(detalle.auditoria.length, 1);
  assert.equal(detalle.auditoria[0].usuario, "alan");

  // 4. Concurrencia controlada: Dos ventas simultáneas
  const ventaA = registrarVentaIdempotente(newClient, {
    ticket: {
      id: "ticket-concurrente-A",
      fecha_hora_utc: new Date().toISOString(),
      total_venta: 1500,
      monto_efectivo: 1500,
      monto_transferencia: 0,
      metodo_pago: "EFECTIVO",
    },
    items: [{ producto_nombre: "Alfajor Chocolate", cantidad: 1, precio_unitario: 1500 }],
    usuario: "alan",
  });

  const ventaB = registrarVentaIdempotente(newClient, {
    ticket: {
      id: "ticket-concurrente-B",
      fecha_hora_utc: new Date().toISOString(),
      total_venta: 2200,
      monto_efectivo: 2200,
      monto_transferencia: 0,
      metodo_pago: "EFECTIVO",
    },
    items: [{ producto_nombre: "Paracetamol 500mg", cantidad: 1, precio_unitario: 2200 }],
    usuario: "yilda",
  });

  const [resA, resB] = await Promise.all([ventaA, ventaB]);
  assert.equal(resA.status, "registrado");
  assert.equal(resB.status, "registrado");

  // Verificar arqueo
  const arqueo = await calcularArqueoDiario(newClient, null);
  // Total ventas efectivo: 3700 + 1500 + 2200 = 7400
  assert.equal(arqueo.ventas_efectivo, 7400);

  // 5. Anulación y recálculo
  await anularVenta(newClient, "ticket-concurrente-A", "alan", "Cliente se arrepintió");
  const arqueoPostAnulacion = await calcularArqueoDiario(newClient, null);
  assert.equal(arqueoPostAnulacion.ventas_efectivo, 5900, "Venta anulada debe excluirse del arqueo");

  newClient.close();
  cleanupDb();
});
