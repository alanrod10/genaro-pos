import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { TesoreriaLab } from "../src/components/TesoreriaLab";
import { TesoreriaService } from "../src/services/tesoreria";

describe("GENARO TESORERÍA — Módulo de Cuenta Corriente PREPAY / David", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("1. Navega a la pestaña de Cuenta Corriente PREPAY / David y muestra indicadores en $0,00", async () => {
    vi.spyOn(TesoreriaService, "getCuentaCorrienteDavid").mockResolvedValue({
      obligacion_pendiente_cents: 0,
      obligacion_pendiente_formatted: "$0,00",
      total_acreditaciones_cents: 0,
      total_acreditaciones_formatted: "$0,00",
      total_pagos_cents: 0,
      total_pagos_formatted: "$0,00",
      total_deuda_historica_cents: 0,
      total_deuda_historica_formatted: "$0,00",
      saldo_disponible_1130_cents: 0,
      saldo_disponible_1130_formatted: "$0,00",
      cantidad_movimientos: 0,
      movimientos: [],
    });

    render(<TesoreriaLab currentUser={{ username: "alan", display_name: "Alan" }} />);

    // Click en la pestaña Cuenta Corriente David
    const tabBtn = screen.getByTestId("tab-cuenta-corriente-david");
    expect(tabBtn).toBeInTheDocument();
    fireEvent.click(tabBtn);

    await waitFor(() => {
      expect(screen.getByTestId("seccion-cuenta-corriente-david")).toBeInTheDocument();
      expect(screen.getByTestId("kpi-obligacion-pendiente")).toHaveTextContent("$0,00");
      expect(screen.getByTestId("kpi-total-acreditaciones")).toHaveTextContent("$0,00");
      expect(screen.getByTestId("kpi-total-pagos")).toHaveTextContent("$0,00");
      expect(screen.getByTestId("kpi-deuda-historica")).toHaveTextContent("$0,00");
      expect(screen.getByTestId("kpi-disponible-1130")).toHaveTextContent("$0,00");
    });

    expect(
      screen.getByText(/La cuenta inicia formalmente en \$0,00. No se ha reconocido deuda histórica previa/i)
    ).toBeInTheDocument();

    expect(
      screen.getByText(/No hay movimientos registrados en la cuenta corriente. La obligación actual es de \$0,00/i)
    ).toBeInTheDocument();
  });

  it("2. Abre modal de Acreditación de Saldo y valida campos requeridos", async () => {
    render(<TesoreriaLab currentUser={{ username: "alan", display_name: "Alan" }} />);
    fireEvent.click(screen.getByTestId("tab-cuenta-corriente-david"));

    await waitFor(() => {
      expect(screen.getByTestId("btn-abrir-acreditacion")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("btn-abrir-acreditacion"));

    expect(screen.getByTestId("modal-acreditacion-david")).toBeInTheDocument();
    expect(screen.getByTestId("input-acr-monto")).toBeInTheDocument();
    expect(screen.getByTestId("input-acr-ref")).toBeInTheDocument();
    expect(screen.getByTestId("btn-confirmar-acreditacion")).toBeInTheDocument();
  });

  it("3. Abre modal de Pago a David y visualiza disponibilidad en fondo 1130", async () => {
    vi.spyOn(TesoreriaService, "getCuentaCorrienteDavid").mockResolvedValue({
      obligacion_pendiente_cents: 1000000,
      obligacion_pendiente_formatted: "$10.000,00",
      total_acreditaciones_cents: 1000000,
      total_acreditaciones_formatted: "$10.000,00",
      total_pagos_cents: 0,
      total_pagos_formatted: "$0,00",
      total_deuda_historica_cents: 0,
      total_deuda_historica_formatted: "$0,00",
      saldo_disponible_1130_cents: 500000,
      saldo_disponible_1130_formatted: "$5.000,00",
      cantidad_movimientos: 1,
      movimientos: [],
    });

    render(<TesoreriaLab currentUser={{ username: "alan", display_name: "Alan" }} />);
    fireEvent.click(screen.getByTestId("tab-cuenta-corriente-david"));

    await waitFor(() => {
      expect(screen.getByTestId("btn-abrir-pago")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("btn-abrir-pago"));

    expect(screen.getByTestId("modal-pago-david")).toBeInTheDocument();
    expect(screen.getAllByText(/\$5\.000,00/i).length).toBeGreaterThan(0); // saldo disponible 1130
    expect(screen.getAllByText(/\$10\.000,00/i).length).toBeGreaterThan(0); // obligacion pendiente
    expect(screen.getByTestId("btn-confirmar-pago")).toBeInTheDocument();
  });

  it("4. Modal de Reconocimiento Histórico contiene advertencia innegociable y exige checkbox de confirmación", async () => {
    render(<TesoreriaLab currentUser={{ username: "alan", display_name: "Alan" }} />);
    fireEvent.click(screen.getByTestId("tab-cuenta-corriente-david"));

    await waitFor(() => {
      expect(screen.getByTestId("btn-abrir-historico")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("btn-abrir-historico"));

    expect(screen.getByTestId("modal-historico-david")).toBeInTheDocument();

    // Verificación de la advertencia innegociable
    const alerta = screen.getByTestId("alerta-reconocimiento-historico");
    expect(alerta).toBeInTheDocument();
    expect(alerta).toHaveTextContent(
      "Esta operación reconoce una obligación anterior. No acredita saldo PREPAY ni modifica los fondos 1130 o 1220."
    );

    // El botón de submit debe estar deshabilitado hasta marcar la confirmación
    const submitBtn = screen.getByTestId("btn-confirmar-historico") as HTMLButtonElement;
    expect(submitBtn.disabled).toBe(true);

    const checkbox = screen.getByTestId("checkbox-his-confirmacion") as HTMLInputElement;
    expect(checkbox.checked).toBe(false);

    fireEvent.click(checkbox);
    expect(checkbox.checked).toBe(true);
    expect(submitBtn.disabled).toBe(false);
  });

  it("5. Muestra historial cronológico con badges diferenciados de cada tipo de movimiento", async () => {
    vi.spyOn(TesoreriaService, "getCuentaCorrienteDavid").mockResolvedValue({
      obligacion_pendiente_cents: 2300000,
      obligacion_pendiente_formatted: "$23.000,00",
      total_acreditaciones_cents: 1500000,
      total_acreditaciones_formatted: "$15.000,00",
      total_pagos_cents: 1200000,
      total_pagos_formatted: "$12.000,00",
      total_deuda_historica_cents: 2000000,
      total_deuda_historica_formatted: "$20.000,00",
      saldo_disponible_1130_cents: 300000,
      saldo_disponible_1130_formatted: "$3.000,00",
      cantidad_movimientos: 3,
      movimientos: [
        {
          id: 1,
          operation_id: "OP-HIS-001",
          idempotency_key: "idem_his_1",
          tipo_movimiento: "RECONOCIMIENTO_DEUDA_HISTORICA",
          monto_cents: 2000000,
          monto_formatted: "$20.000,00",
          fecha_referencia: "2026-10-01",
          recorded_at_utc: "2026-10-01T12:00:00Z",
          movement_id: null,
          fondo_afectado: null,
          referencia_externa: "ACTA-001",
          motivo: "Deuda histórica inicial",
          actor: "alan_socio",
          jornada_id: null,
          created_at: "2026-10-01 12:00:00",
          saldo_obligacion_resultante_cents: 2000000,
          saldo_obligacion_resultante_formatted: "$20.000,00",
        },
        {
          id: 2,
          operation_id: "OP-ACR-001",
          idempotency_key: "idem_acr_1",
          tipo_movimiento: "ACREDITACION_SALDO",
          monto_cents: 1500000,
          monto_formatted: "$15.000,00",
          fecha_referencia: "2026-10-15",
          recorded_at_utc: "2026-10-15T14:00:00Z",
          movement_id: "mov-acr-1234",
          fondo_afectado: "1220",
          referencia_externa: "LOTE-DAVID-45",
          motivo: "Carga mayorista",
          actor: "alan_socio",
          jornada_id: 1,
          created_at: "2026-10-15 14:00:00",
          saldo_obligacion_resultante_cents: 3500000,
          saldo_obligacion_resultante_formatted: "$35.000,00",
        },
        {
          id: 3,
          operation_id: "OP-PAG-001",
          idempotency_key: "idem_pag_1",
          tipo_movimiento: "PAGO_EFECTIVO",
          monto_cents: 1200000,
          monto_formatted: "$12.000,00",
          fecha_referencia: "2026-10-20",
          recorded_at_utc: "2026-10-20T18:00:00Z",
          movement_id: "mov-pag-5678",
          fondo_afectado: "1130",
          referencia_externa: "REC-PAG-01",
          motivo: "Pago en mano",
          actor: "alan_socio",
          jornada_id: 1,
          created_at: "2026-10-20 18:00:00",
          saldo_obligacion_resultante_cents: 2300000,
          saldo_obligacion_resultante_formatted: "$23.000,00",
        },
      ],
    });

    render(<TesoreriaLab currentUser={{ username: "alan", display_name: "Alan" }} />);
    fireEvent.click(screen.getByTestId("tab-cuenta-corriente-david"));

    await waitFor(() => {
      expect(screen.getByTestId("tabla-cc-david")).toBeInTheDocument();
      expect(screen.getByText("➕ ACREDITACIÓN EFECTIVA")).toBeInTheDocument();
      expect(screen.getByText("💵 PAGO EFECTIVO")).toBeInTheDocument();
      expect(screen.getByText("📜 DEUDA HISTÓRICA")).toBeInTheDocument();
      expect(screen.getByText("1220 (Prepay Digital)")).toBeInTheDocument();
      expect(screen.getByText("1130 (Prepay Físico)")).toBeInTheDocument();
      expect(screen.getByText("Sin impacto en fondos")).toBeInTheDocument();
    });
  });
});
