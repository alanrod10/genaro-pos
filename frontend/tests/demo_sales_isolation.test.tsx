import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  openDB,
  resetDBInstance,
  inicializarVentasDemoSiVacio,
  listarVentasDemo,
  listarVentasLocales,
  guardarVentaLocal,
  guardarVentaDemo,
  obtenerVentaLocal,
  obtenerVentaDemo,
  migrarTicketsDemoDeColaOperativa,
  VENTAS_DEMO_INICIALES,
} from "../src/services/db";
import { SyncQueueModal } from "../src/components/SyncQueueModal";
import { MockApiClient } from "../src/services/api";
import { sincronizarCola } from "../src/services/sync";
import { VentaLocal } from "../src/types/sales";

describe("Aislamiento Estricto: Ventas Demo (ventas_demo) vs Cola Operativa (ventas_queue)", () => {
  beforeEach(async () => {
    resetDBInstance();
    const db = await openDB();
    const tx = db.transaction(["ventas_demo", "ventas_queue"], "readwrite");
    tx.objectStore("ventas_demo").clear();
    tx.objectStore("ventas_queue").clear();
    await new Promise((resolve) => {
      tx.oncomplete = resolve;
    });
  });

  // 1. Sembrado demo solo en ventas_demo
  it("1. inicializarVentasDemoSiVacio siembra exclusivamente en ventas_demo y deja ventas_queue vacía", async () => {
    await inicializarVentasDemoSiVacio();

    const demoSales = await listarVentasDemo();
    const queueSales = await listarVentasLocales();

    expect(demoSales.length).toBe(3);
    expect(demoSales.map((v) => v.ticket_id)).toEqual(
      expect.arrayContaining(["T-20261024-001", "T-20261024-002", "T-20261024-003"])
    );

    // La cola operativa para ventas reales debe permanecer 100% vacía
    expect(queueSales).toEqual([]);
    expect(queueSales.length).toBe(0);
  });

  // 2. Migración transaccional desde versión anterior
  it("2. Migra los 3 tickets demo conocidos de ventas_queue a ventas_demo preservando ventas operativas", async () => {
    const db = await openDB();

    // Simular estado previo: ventas_queue contiene los 3 tickets demo + un ticket operativo real
    const ticketReal: VentaLocal = {
      ticket_id: "T-OPERATIVO-REAL-777",
      creado_en: "2026-10-25T14:00:00Z",
      estado: "pendiente",
      intentos: 0,
      payload: {
        ticket: {
          ticket_id: "T-OPERATIVO-REAL-777",
          fecha: "25/10/2026 14:00:00",
          total_venta: 8500,
          monto_efectivo: 8500,
          monto_transf: 0,
          metodo_pago: "EFECTIVO",
        },
        items: [
          {
            ticket_id: "T-OPERATIVO-REAL-777",
            producto: "Producto Real Vendido",
            cantidad: 1,
            unidad: "Unidad",
            precio_unit: 8500,
            subtotal: 8500,
            metodo_pago: "EFECTIVO",
          },
        ],
      },
    };

    // Insertar directamente en ventas_queue los tickets demo antiguos y el ticket operativo
    for (const vDemo of VENTAS_DEMO_INICIALES) {
      await guardarVentaLocal(vDemo);
    }
    await guardarVentaLocal(ticketReal);

    // Verificar estado antes de migrar
    const antesQueue = await listarVentasLocales();
    expect(antesQueue.length).toBe(4);
    const antesDemo = await listarVentasDemo();
    expect(antesDemo.length).toBe(0);

    // Ejecutar migración transaccional
    const resultado = await migrarTicketsDemoDeColaOperativa(db);
    expect(resultado.migrados).toBe(3);
    expect(resultado.conservados).toBe(1);

    // Verificar estado después de migrar
    const despuesDemo = await listarVentasDemo();
    expect(despuesDemo.length).toBe(3);
    expect(despuesDemo.map((v) => v.ticket_id)).toEqual(
      expect.arrayContaining(["T-20261024-001", "T-20261024-002", "T-20261024-003"])
    );

    const despuesQueue = await listarVentasLocales();
    expect(despuesQueue.length).toBe(1);
    expect(despuesQueue[0].ticket_id).toBe("T-OPERATIVO-REAL-777");
    expect(despuesQueue[0].payload.ticket.total_venta).toBe(8500);

    // Verificar que los tickets demo ya NO existen en ventas_queue
    const tDemoEnQueue = await obtenerVentaLocal("T-20261024-001");
    expect(tDemoEnQueue).toBeUndefined();

    // Idempotencia: una segunda ejecución no migra nada adicional
    const segundaMigracion = await migrarTicketsDemoDeColaOperativa(db);
    expect(segundaMigracion.migrados).toBe(0);
    expect(segundaMigracion.conservados).toBe(1);
  });

  // 3. SyncQueueModal visualización y aclaración según el modo
  it("3. SyncQueueModal muestra aclaración de simulación local en Modo Demo y título específico", () => {
    const mockDemo = new MockApiClient();
    const { unmount } = render(
      <SyncQueueModal
        ventas={VENTAS_DEMO_INICIALES}
        isDemo={true}
        mockClient={mockDemo}
        onClose={() => {}}
        onSyncAll={async () => {}}
        onSyncOne={async () => {}}
      />
    );

    // En Modo Demo: título y advertencia de simulación presentes
    expect(
      screen.getByText(/Cola Local de Ventas — Almacén Demo \(Simulado\)/i)
    ).toBeInTheDocument();
    expect(screen.getByTestId("demo-simulation-notice")).toBeInTheDocument();
    expect(screen.getByTestId("demo-simulation-notice")).toHaveTextContent(
      /Los tickets y el estado.*SINCRONIZADA.*son simulados localmente/i
    );

    unmount();

    // En Modo Real Conectado: título operativo y sin banner de simulación
    render(
      <SyncQueueModal
        ventas={[]}
        isDemo={false}
        isReadOnlySmoke={false}
        onClose={() => {}}
        onSyncAll={async () => {}}
        onSyncOne={async () => {}}
      />
    );

    expect(screen.getByText(/Cola Operativa de Ventas \(IndexedDB\)/i)).toBeInTheDocument();
    expect(screen.queryByTestId("demo-simulation-notice")).not.toBeInTheDocument();
  });

  // 4. SyncQueueModal conserva bloqueo de sincronización en modo de solo lectura
  it("4. SyncQueueModal conserva banner de bloqueo de sincronización en modo de solo lectura", () => {
    render(
      <SyncQueueModal
        ventas={[]}
        isDemo={false}
        isReadOnlySmoke={true}
        onClose={() => {}}
        onSyncAll={async () => {}}
        onSyncOne={async () => {}}
      />
    );

    expect(screen.getByTestId("readonly-sync-blocked-notice")).toBeInTheDocument();
    expect(screen.queryByTestId("btn-sync-all")).not.toBeInTheDocument();
  });

  // 5. sincronizarCola opera contra ventas_demo cuando el cliente es demo
  it("5. sincronizarCola en modo Demo opera exclusivamente contra ventas_demo sin alterar ventas_queue", async () => {
    const mockDemo = new MockApiClient();

    // Guardar una venta pendiente en ventas_demo
    await guardarVentaDemo({
      ticket_id: "T-PEND-DEMO-01",
      estado: "pendiente",
      intentos: 0,
      creado_en: new Date().toISOString(),
      payload: {
        ticket: {
          ticket_id: "T-PEND-DEMO-01",
          fecha: "25/10/2026 15:00:00",
          total_venta: 1200,
          monto_efectivo: 1200,
          monto_transf: 0,
          metodo_pago: "EFECTIVO",
        },
        items: [
          {
            ticket_id: "T-PEND-DEMO-01",
            producto: "Demo Item",
            cantidad: 1,
            unidad: "Unidad",
            precio_unit: 1200,
            subtotal: 1200,
            metodo_pago: "EFECTIVO",
          },
        ],
      },
    });

    // Guardar una venta operativa en ventas_queue que no debe ser alterada
    await guardarVentaLocal({
      ticket_id: "T-QUEUE-STAY-PEND",
      estado: "pendiente",
      intentos: 0,
      creado_en: new Date().toISOString(),
      payload: {
        ticket: {
          ticket_id: "T-QUEUE-STAY-PEND",
          fecha: "25/10/2026 15:00:00",
          total_venta: 3000,
          monto_efectivo: 3000,
          monto_transf: 0,
          metodo_pago: "EFECTIVO",
        },
        items: [],
      },
    });

    const res = await sincronizarCola(mockDemo);
    expect(res.procesados).toBe(1);
    expect(res.exitosos).toBe(1);

    // ventas_demo quedó sincronizada
    const ventaDemo = await obtenerVentaDemo("T-PEND-DEMO-01");
    expect(ventaDemo?.estado).toBe("sincronizada");

    // ventas_queue permaneció intacta en pendiente
    const ventaQueue = await obtenerVentaLocal("T-QUEUE-STAY-PEND");
    expect(ventaQueue?.estado).toBe("pendiente");
    expect(ventaQueue?.intentos).toBe(0);
  });
});
