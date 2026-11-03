import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { TesoreriaLab } from "../src/components/TesoreriaLab";
import { TesoreriaService } from "../src/services/tesoreria";
import { FondoTesoreria, TrasladoTesoreria } from "../src/types/tesoreria";

describe("GENARO TESORERÍA — Traslados Operativos UI", () => {
  const dummyFondos: FondoTesoreria[] = [
    {
      id: 1,
      codigo: "1110",
      nombre: "Drugstore Diario",
      naturaleza: "EFECTIVO",
      titularidad: "PROPIA",
      moneda: "ARS",
      estado: "ACTIVO",
      saldo_centavos: 10000000,
      saldo_formateado: "$100.000,00",
      disponibilidad_inmediata: true,
      permite_sobregiro: 0,
      version: 1,
      created_at: "2026-11-01T00:00:00Z",
      updated_at: "2026-11-01T00:00:00Z",
      creado_por: "sistema",
      actualizado_por: "sistema",
    },
    {
      id: 2,
      codigo: "1120",
      nombre: "Drugstore Acumulado",
      naturaleza: "FONDO_FIJO",
      titularidad: "PROPIA",
      moneda: "ARS",
      estado: "ACTIVO",
      saldo_centavos: 0,
      saldo_formateado: "$0,00",
      disponibilidad_inmediata: true,
      permite_sobregiro: 0,
      version: 1,
      created_at: "2026-11-01T00:00:00Z",
      updated_at: "2026-11-01T00:00:00Z",
      creado_por: "sistema",
      actualizado_por: "sistema",
    },
    {
      id: 5,
      codigo: "1150",
      nombre: "Claro Físico",
      naturaleza: "RECAUDACION_TERCEROS",
      titularidad: "CUSTODIA",
      moneda: "ARS",
      estado: "ACTIVO",
      saldo_centavos: 0,
      saldo_formateado: "$0,00",
      disponibilidad_inmediata: false,
      permite_sobregiro: 0,
      version: 1,
      created_at: "2026-11-01T00:00:00Z",
      updated_at: "2026-11-01T00:00:00Z",
      creado_por: "sistema",
      actualizado_por: "sistema",
    },
  ];

  const dummyPendingTransfer: TrasladoTesoreria = {
    id: 1,
    transfer_id: "trf-uuid-1234",
    idempotency_key: "idemp-trf-1234",
    journal_id: 1,
    amount_cents: 2500000,
    amount_formatted: "$25.000,00",
    currency: "ARS",
    status: "PENDING_RECEIPT",
    dispatched_by: "alan",
    dispatched_at: "2026-11-01T10:00:00Z",
    received_by: null,
    received_at: null,
    reason: "Envío para cambio en caja 2",
    evidence_reference: "REMITO-789",
    out_movement_id: "mov-out-1110-1",
    in_movement_id: null,
    origin_fund_code: "1110",
    origin_fund_name: "Drugstore Diario",
    destination_fund_code: "1120",
    destination_fund_name: "Drugstore Acumulado",
  };

  const dummyConfirmedTransfer: TrasladoTesoreria = {
    id: 2,
    transfer_id: "trf-uuid-5678",
    idempotency_key: "idemp-trf-5678",
    journal_id: 1,
    amount_cents: 1000000,
    amount_formatted: "$10.000,00",
    currency: "ARS",
    status: "CONFIRMED",
    dispatched_by: "alan",
    dispatched_at: "2026-11-01T09:00:00Z",
    received_by: "cajero",
    received_at: "2026-11-01T09:15:00Z",
    reason: "Traslado inicial",
    evidence_reference: null,
    out_movement_id: "mov-out-1110-2",
    in_movement_id: "mov-in-1120-2",
    origin_fund_code: "1110",
    origin_fund_name: "Drugstore Diario",
    destination_fund_code: "1120",
    destination_fund_name: "Drugstore Acumulado",
  };

  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(window, "alert").mockImplementation(() => {});
    vi.spyOn(TesoreriaService, "obtenerFondos").mockResolvedValue(dummyFondos);
    vi.spyOn(TesoreriaService, "obtenerTraslados").mockResolvedValue([
      dummyPendingTransfer,
      dummyConfirmedTransfer,
    ]);
  });

  it("1. Visualiza sección TRASLADOS EN TRÁNSITO con badge de pendientes y acciones operativas", async () => {
    render(<TesoreriaLab currentUser={{ username: "alan", display_name: "Alan" }} />);

    fireEvent.click(screen.getByTestId("tab-traslados"));

    await waitFor(() => {
      expect(screen.getByTestId("seccion-traslados-transito")).toBeInTheDocument();
      expect(screen.getByText(/TRASLADOS EN TRÁNSITO/i)).toBeInTheDocument();
      expect(screen.getByTestId("badge-traslados-transito")).toHaveTextContent("1 pendiente(s)");
    });

    // Fila del traslado pendiente
    expect(screen.getAllByText("$25.000,00").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Envío para cambio en caja 2").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByTestId("btn-confirmar-trf-trf-uuid-1234")).toBeInTheDocument();
    expect(screen.getByTestId("btn-anular-trf-trf-uuid-1234")).toBeInTheDocument();

    // Historial general muestra ambos
    expect(screen.getByText("Historial de Traslados")).toBeInTheDocument();
    expect(screen.getAllByText("$10.000,00").length).toBeGreaterThanOrEqual(1);
  });

  it("2. Formulario de despacho: valida matriz de políticas y fondos autorizados", async () => {
    const trasladarSpy = vi.spyOn(TesoreriaService, "trasladarFondos").mockResolvedValue(dummyPendingTransfer);

    render(<TesoreriaLab currentUser={{ username: "alan", display_name: "Alan" }} />);

    // Esperar a que los fondos se hayan cargado en el estado
    await waitFor(() => {
      expect(screen.getByTestId("card-fondo-1110")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("tab-traslados"));

    await waitFor(() => {
      expect(screen.getByTestId("btn-nuevo-traslado")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("btn-nuevo-traslado"));

    // Modal abierto
    expect(screen.getByText("Despachar Traslado de Fondos")).toBeInTheDocument();

    // Seleccionar combinación prohibida: 1150 (Claro Físico) -> 1110
    const selectOrigen = screen.getByTestId("select-trf-origen");
    const selectDestino = screen.getByTestId("select-trf-destino");

    fireEvent.change(selectOrigen, { target: { value: "1150" } });
    fireEvent.change(selectDestino, { target: { value: "1110" } });

    // Advertencia de política en pantalla
    await waitFor(() => {
      expect(screen.getByText(/Fondo 1150 \(Claro Efectivo\) es custodia de terceros/i)).toBeInTheDocument();
    });

    const submitBtn = screen.getByTestId("btn-submit-despachar-trf");
    expect(submitBtn).toBeDisabled();

    // Cambiar a combinación válida: 1110 -> 1120
    fireEvent.change(selectOrigen, { target: { value: "1110" } });
    fireEvent.change(selectDestino, { target: { value: "1120" } });

    fireEvent.change(screen.getByTestId("input-trf-monto"), { target: { value: "15000" } });
    fireEvent.change(screen.getByTestId("input-trf-motivo"), { target: { value: "Traslado autorizado" } });

    expect(submitBtn).not.toBeDisabled();
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(trasladarSpy).toHaveBeenCalledWith(
        "1110",
        "1120",
        1500000,
        "Traslado autorizado",
        expect.stringMatching(/^trf_init_/),
        undefined
      );
    });
  });

  it("3. Modal Confirmar Recepción: abre resumen y llama al servicio", async () => {
    const confirmarSpy = vi.spyOn(TesoreriaService, "confirmarTraslado").mockResolvedValue({
      ...dummyPendingTransfer,
      status: "CONFIRMED",
      received_by: "alan",
      received_at: "2026-11-01T10:30:00Z",
      in_movement_id: "in-mov-1234",
    });

    render(<TesoreriaLab currentUser={{ username: "alan", display_name: "Alan" }} />);
    fireEvent.click(screen.getByTestId("tab-traslados"));

    await waitFor(() => {
      expect(screen.getByTestId("btn-confirmar-trf-trf-uuid-1234")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("btn-confirmar-trf-trf-uuid-1234"));

    expect(screen.getByText("Confirmar Recepción de Traslado")).toBeInTheDocument();
    expect(screen.getByText(/Al confirmar la recepción/i)).toBeInTheDocument();

    const btnConfirmar = screen.getByTestId("btn-submit-confirmar-recepcion");
    fireEvent.click(btnConfirmar);

    await waitFor(() => {
      expect(confirmarSpy).toHaveBeenCalledWith(
        "trf-uuid-1234",
        expect.stringMatching(/^trf_conf_/),
        undefined
      );
    });
  });

  it("4. Modal Anular Traslado: requiere motivo de anulación y llama al servicio", async () => {
    const anularSpy = vi.spyOn(TesoreriaService, "cancelarTraslado").mockResolvedValue({
      ...dummyPendingTransfer,
      status: "CANCELLED",
    });

    render(<TesoreriaLab currentUser={{ username: "alan", display_name: "Alan" }} />);
    fireEvent.click(screen.getByTestId("tab-traslados"));

    await waitFor(() => {
      expect(screen.getByTestId("btn-anular-trf-trf-uuid-1234")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("btn-anular-trf-trf-uuid-1234"));

    expect(screen.getByText("Anular Traslado en Tránsito")).toBeInTheDocument();
    expect(screen.getByText(/restituirán íntegramente al fondo origen/i)).toBeInTheDocument();

    const submitAnular = screen.getByTestId("btn-submit-anular-trf");
    expect(submitAnular).toBeDisabled();

    // Ingresar motivo de anulación
    fireEvent.change(screen.getByTestId("input-motivo-anulacion-trf"), {
      target: { value: "Caja cerrada por fuerza mayor" },
    });

    expect(submitAnular).not.toBeDisabled();
    fireEvent.click(submitAnular);

    await waitFor(() => {
      expect(anularSpy).toHaveBeenCalledWith(
        "trf-uuid-1234",
        "Caja cerrada por fuerza mayor",
        expect.stringMatching(/^trf_canc_/)
      );
    });
  });

  it("5. Filtros de traslados: reacciona a cambios de filtros de estado y búsqueda", async () => {
    const obtenerSpy = vi.spyOn(TesoreriaService, "obtenerTraslados");

    render(<TesoreriaLab currentUser={{ username: "alan", display_name: "Alan" }} />);
    fireEvent.click(screen.getByTestId("tab-traslados"));

    await waitFor(() => {
      expect(screen.getByTestId("filtro-trf-estado")).toBeInTheDocument();
    });

    // Cambiar filtro de estado a CONFIRMED
    fireEvent.change(screen.getByTestId("filtro-trf-estado"), { target: { value: "CONFIRMED" } });

    await waitFor(() => {
      expect(obtenerSpy).toHaveBeenCalledWith(50, "CONFIRMED", undefined, undefined, undefined);
    });

    // Cambiar búsqueda textual
    fireEvent.change(screen.getByTestId("input-filtro-trf-busqueda"), { target: { value: "trf-uuid" } });

    await waitFor(() => {
      expect(obtenerSpy).toHaveBeenCalledWith(50, "CONFIRMED", undefined, undefined, "trf-uuid");
    });
  });
});
