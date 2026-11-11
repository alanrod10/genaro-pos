import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import {
  openDB,
  resetDBInstance,
  guardarVentaLocal,
  obtenerVentaLocal,
  listarVentasLocales,
  eliminarVentaLocal,
  guardarVentaDemo,
  obtenerVentaDemo,
} from "../src/services/db";
import { SyncQueueModal } from "../src/components/SyncQueueModal";
import { VentaLocal, VentaPayload } from "../src/types/sales";

function createMockTicket(ticketId: string, estado: "pendiente" | "requiere_revision" | "sincronizada", ultimo_error?: string): VentaLocal {
  const payload: VentaPayload = {
    ticket: {
      ticket_id: ticketId,
      fecha: "11/11/2026 10:00:00",
      total_venta: 1500,
      monto_efectivo: 1500,
      monto_transf: 0,
      metodo_pago: "EFECTIVO",
    },
    items: [
      {
        ticket_id: ticketId,
        producto: "Alfajor Chocolate",
        cantidad: 1,
        unidad: "Unidad",
        precio_unit: 1500,
        subtotal: 1500,
        metodo_pago: "EFECTIVO",
      },
    ],
  };

  return {
    ticket_id: ticketId,
    payload,
    estado,
    intentos: estado === "requiere_revision" ? 3 : 0,
    creado_en: new Date().toISOString(),
    ultimo_error,
  };
}

describe("SyncQueueModal — Botón Descartar en Cola IndexedDB", () => {
  beforeEach(async () => {
    resetDBInstance();
    const db = await openDB();
    const tx = db.transaction(["ventas_queue", "ventas_demo"], "readwrite");
    tx.objectStore("ventas_queue").clear();
    tx.objectStore("ventas_demo").clear();
    await new Promise((resolve) => {
      tx.oncomplete = resolve;
    });
  });

  it("1. eliminarVentaLocal borra físicamente el registro de la cola IndexedDB", async () => {
    const ticket = createMockTicket("T-FAIL-001", "requiere_revision", "HTTP 400: INVALID_INPUT");
    await guardarVentaLocal(ticket);

    // Comprobar que existe
    const guardada = await obtenerVentaLocal("T-FAIL-001");
    expect(guardada).toBeDefined();
    expect(guardada?.ticket_id).toBe("T-FAIL-001");

    // Borrar físicamente
    await eliminarVentaLocal("T-FAIL-001");

    // Comprobar que ya no existe
    const borrada = await obtenerVentaLocal("T-FAIL-001");
    expect(borrada).toBeUndefined();

    const lista = await listarVentasLocales();
    expect(lista.find((v) => v.ticket_id === "T-FAIL-001")).toBeUndefined();
  });

  it("2. eliminarVentaLocal borra también de ventas_demo si se encuentra allí", async () => {
    const ticketDemo = createMockTicket("T-DEMO-FAIL", "requiere_revision", "Simulated permanent error");
    await guardarVentaDemo(ticketDemo);

    const guardada = await obtenerVentaDemo("T-DEMO-FAIL");
    expect(guardada).toBeDefined();

    await eliminarVentaLocal("T-DEMO-FAIL");

    const borrada = await obtenerVentaDemo("T-DEMO-FAIL");
    expect(borrada).toBeUndefined();
  });

  it("3. SyncQueueModal muestra botón rojo 'Descartar' con ícono de papelera en tickets en REQUIERE_REVISION", () => {
    const ticket = createMockTicket("T-REV-100", "requiere_revision", "HTTP 400: INVALID_INPUT");

    render(
      <SyncQueueModal
        ventas={[ticket]}
        isDemo={false}
        onClose={() => {}}
        onSyncAll={async () => {}}
        onSyncOne={async () => {}}
      />
    );

    // Debe mostrar botón Reintentar y botón Descartar
    const btnRetry = screen.getByTestId("btn-retry-T-REV-100");
    const btnDiscard = screen.getByTestId("btn-discard-T-REV-100");

    expect(btnRetry).toBeInTheDocument();
    expect(btnDiscard).toBeInTheDocument();
    expect(btnDiscard).toHaveTextContent(/Descartar/i);
    expect(btnDiscard).toHaveClass("btn-discard-single");
  });

  it("4. No muestra botón 'Descartar' en tickets pendientes normales sin fallas", () => {
    const ticketOk = createMockTicket("T-PENDING-OK", "pendiente");

    render(
      <SyncQueueModal
        ventas={[ticketOk]}
        isDemo={false}
        onClose={() => {}}
        onSyncAll={async () => {}}
        onSyncOne={async () => {}}
      />
    );

    expect(screen.getByTestId("btn-retry-T-PENDING-OK")).toBeInTheDocument();
    expect(screen.queryByTestId("btn-discard-T-PENDING-OK")).not.toBeInTheDocument();
  });

  it("5. Al hacer clic en 'Descartar', elimina el ticket de IndexedDB, actualiza la lista del modal y dispara callback", async () => {
    const ticket = createMockTicket("T-REV-DISCARD", "requiere_revision", "HTTP 400: INVALID_INPUT");
    await guardarVentaLocal(ticket);

    const onDiscardMock = vi.fn();

    render(
      <SyncQueueModal
        ventas={[ticket]}
        isDemo={false}
        onClose={() => {}}
        onSyncAll={async () => {}}
        onSyncOne={async () => {}}
        onDiscard={onDiscardMock}
      />
    );

    expect(screen.getByTestId("queue-item-T-REV-DISCARD")).toBeInTheDocument();
    expect(screen.getByTestId("tab-revision")).toHaveTextContent("⚠️ Requiere Revisión (1)");

    const btnDiscard = screen.getByTestId("btn-discard-T-REV-DISCARD");
    fireEvent.click(btnDiscard);

    // Esperar a que se actualice la vista
    await waitFor(() => {
      expect(screen.queryByTestId("queue-item-T-REV-DISCARD")).not.toBeInTheDocument();
    });

    // Contador actualizado a 0
    expect(screen.getByTestId("tab-revision")).toHaveTextContent("⚠️ Requiere Revisión (0)");
    expect(screen.getByText("No hay ventas en esta sección de la cola local.")).toBeInTheDocument();

    // Verificación de callback
    expect(onDiscardMock).toHaveBeenCalledWith("T-REV-DISCARD");

    // Verificación en IndexedDB
    const enDB = await obtenerVentaLocal("T-REV-DISCARD");
    expect(enDB).toBeUndefined();
  });
});
