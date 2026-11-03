import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { TesoreriaLab } from "../src/components/TesoreriaLab";
import { TesoreriaService } from "../src/services/tesoreria";
import {
  FondoTesoreria,
  TrasladoTesoreria,
  ArqueoTesoreria,
  CierreTesoreria,
  JornadaTesoreria,
  ValidacionCierre,
} from "../src/types/tesoreria";

describe("GENARO TESORERÍA — Arqueos Físicos, Conciliación Digital y Cierres UI", () => {
  const dummyFondos: FondoTesoreria[] = [
    {
      id: 1,
      codigo: "1110",
      nombre: "Drugstore Diario",
      naturaleza: "EFECTIVO",
      titularidad: "PROPIA",
      moneda: "ARS",
      estado: "ACTIVO",
      saldo_centavos: 5000000,
      saldo_formateado: "$50.000,00",
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
      saldo_centavos: 12000000,
      saldo_formateado: "$120.000,00",
      disponibilidad_inmediata: true,
      permite_sobregiro: 0,
      version: 1,
      created_at: "2026-11-01T00:00:00Z",
      updated_at: "2026-11-01T00:00:00Z",
      creado_por: "sistema",
      actualizado_por: "sistema",
    },
    {
      id: 6,
      codigo: "1210",
      nombre: "Mercado Pago Propio",
      naturaleza: "BILLETERA_VIRTUAL",
      titularidad: "PROPIA",
      moneda: "ARS",
      estado: "ACTIVO",
      saldo_centavos: 8000000,
      saldo_formateado: "$80.000,00",
      disponibilidad_inmediata: true,
      permite_sobregiro: 0,
      version: 1,
      created_at: "2026-11-01T00:00:00Z",
      updated_at: "2026-11-01T00:00:00Z",
      creado_por: "sistema",
      actualizado_por: "sistema",
    },
    {
      id: 9,
      codigo: "1310",
      nombre: "Tránsito Preventistas",
      naturaleza: "TRANSITO_PREVENTISTAS",
      titularidad: "TRANSITO",
      moneda: "ARS",
      estado: "ACTIVO",
      saldo_centavos: 1500000,
      saldo_formateado: "$15.000,00",
      disponibilidad_inmediata: false,
      permite_sobregiro: 0,
      version: 1,
      created_at: "2026-11-01T00:00:00Z",
      updated_at: "2026-11-01T00:00:00Z",
      creado_por: "sistema",
      actualizado_por: "sistema",
    },
  ];

  const dummyJornada: JornadaTesoreria = {
    id: 1,
    numero_jornada: 1,
    fecha_operativa: "2026-11-01",
    estado: "EN_CIERRE",
    apertura_timestamp_utc: "2026-11-01T08:00:00Z",
    cierre_timestamp_utc: null,
    usuario_apertura: "alan",
    usuario_cierre: null,
    total_apertura_centavos: 0,
    total_cierre_centavos: null,
    observaciones: null,
    version: 1,
  };

  const dummyTransfers: TrasladoTesoreria[] = [
    {
      id: 1,
      transfer_id: "trf-transito-999",
      idempotency_key: "idemp-999",
      journal_id: 1,
      amount_cents: 1000000,
      amount_formatted: "$10.000,00",
      currency: "ARS",
      status: "PENDING_RECEIPT",
      dispatched_by: "alan",
      dispatched_at: "2026-11-01T11:00:00Z",
      received_by: null,
      received_at: null,
      reason: "Traslado en tránsito de prueba",
      evidence_reference: null,
      out_movement_id: "mov-out-1110-99",
      in_movement_id: null,
      origin_fund_code: "1110",
      origin_fund_name: "Drugstore Diario",
      destination_fund_code: "1120",
      destination_fund_name: "Drugstore Acumulado",
    },
  ];

  const dummyArqueos: ArqueoTesoreria[] = [
    {
      id: 1,
      reconciliation_id: "rec-fis-001",
      journal_id: 1,
      fund_id: 1,
      fund_code: "1110",
      fund_name: "Drugstore Diario",
      naturaleza: "FISICO",
      is_digital: false,
      estado_revision: "CONCILIADO",
      reconciliation_type: "PARTIAL",
      saldo_registrado_cents: 5000000,
      registered_amount_cents: 5000000,
      saldo_registrado_formatted: "$50.000,00",
      registered_amount_formatted: "$50.000,00",
      saldo_verificado_cents: 5000000,
      verified_amount_cents: 5000000,
      saldo_verificado_formatted: "$50.000,00",
      verified_amount_formatted: "$50.000,00",
      diferencia_cents: 0,
      difference_cents: 0,
      diferencia_formatted: "$0,00",
      difference_formatted: "$0,00",
      notes: "Arqueo de turno mañana sin diferencia",
      turno: "MAÑANA",
      performed_by: "alan",
      created_at: "2026-11-01T12:00:00Z",
    },
  ];

  const dummyCierresHist: CierreTesoreria[] = [
    {
      id: 1,
      closure_id: "cls-uuid-historical-1",
      journal_id: 1,
      numero_jornada: 1,
      closed_by: "alan",
      closed_at: "2026-11-01T18:00:00Z",
      snapshot_hash: "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
      snapshot_data: {
        journal_id: 1,
        closed_by: "alan",
        canonical_funds: [
          { fund_code: "1110", balance_cents: 0 },
          { fund_code: "1120", balance_cents: 17000000 },
        ],
        warnings: [],
      },
      observations: "Cierre exitoso verificado en auditoría",
    },
  ];

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(window, "alert").mockImplementation(() => {});

    vi.spyOn(TesoreriaService, "obtenerResumen").mockResolvedValue({
      total_fondos: 4,
      fondos_activos: 4,
      total_saldo_registrado_centavos: 26500000,
      total_saldo_registrado_formateado: "$265.000,00",
      total_saldo_disponible_centavos: 25000000,
      total_saldo_disponible_formateado: "$250.000,00",
      jornada_activa: dummyJornada,
      aislamiento_confirmado: true,
      db_path: "staging/tesoreria_staging.db",
    });

    vi.spyOn(TesoreriaService, "obtenerFondos").mockResolvedValue(dummyFondos);
    vi.spyOn(TesoreriaService, "obtenerJornadaActiva").mockResolvedValue(dummyJornada);
    vi.spyOn(TesoreriaService, "obtenerResumenDisponibilidad").mockResolvedValue(null as any);
    vi.spyOn(TesoreriaService, "obtenerMovimientos").mockResolvedValue([]);
    vi.spyOn(TesoreriaService, "obtenerTraslados").mockResolvedValue(dummyTransfers);
    vi.spyOn(TesoreriaService, "obtenerReservas").mockResolvedValue([]);
    vi.spyOn(TesoreriaService, "obtenerHistorialArqueos").mockResolvedValue(dummyArqueos);
    vi.spyOn(TesoreriaService, "obtenerExpedientesDiferencias").mockResolvedValue([]);
    vi.spyOn(TesoreriaService, "obtenerReversiones").mockResolvedValue([]);
    vi.spyOn(TesoreriaService, "obtenerEstadoOutbox").mockResolvedValue(null as any);
    vi.spyOn(TesoreriaService, "obtenerCadenaAuditoria").mockResolvedValue([]);
    vi.spyOn(TesoreriaService, "obtenerHistorialCierres").mockResolvedValue(dummyCierresHist);
  });

  it("1. Arqueo físico: renderiza selector de turno, cálculo dinámico de diferencia y alerta de traslados en tránsito", async () => {
    const spyCrearArqueo = vi.spyOn(TesoreriaService, "crearArqueo").mockResolvedValue({
      ...dummyArqueos[0],
      reconciliation_id: "rec-fis-new",
    });

    render(<TesoreriaLab currentUser={{ username: "alan", display_name: "Alan Rodrigo" }} />);

    // Navegar a la pestaña Arqueos
    const tabBtn = await screen.findByRole("button", { name: /arqueos/i });
    fireEvent.click(tabBtn);

    // Debe mostrar la subpestaña de recuento físico activa
    expect(screen.getByTestId("subtab-arqueos-fisicos")).toBeDefined();
    expect(screen.getByText(/Recuento de Fondos Físicos/i)).toBeDefined();

    // Debe mostrar la alerta de traslados en tránsito para 1110
    expect(screen.getByText(/TRASLADOS EN TRÁNSITO DETECTADOS/i)).toBeDefined();
    expect(screen.getByText(/El dinero en tránsito NO está disponible ni acreditado en destino/i)).toBeDefined();

    // Seleccionar turno TARDE
    const selectTurno = screen.getByTestId("select-arq-turno");
    fireEvent.change(selectTurno, { target: { value: "TARDE" } });

    // Ingresar saldo contado coincidente ($50.000,00 = 50000)
    const inputContado = screen.getByTestId("input-arq-contado");
    fireEvent.change(inputContado, { target: { value: "50000" } });

    // Debe calcular en tiempo real CONCILIADO
    expect(screen.getAllByText("CONCILIADO").length).toBeGreaterThan(0);

    // Confirmar arqueo físico
    const btnConfirmar = screen.getByTestId("btn-confirmar-arqueo-fisico");
    fireEvent.click(btnConfirmar);

    await waitFor(() => {
      expect(spyCrearArqueo).toHaveBeenCalledWith(
        "1110",
        "PARTIAL",
        5000000,
        "",
        false,
        "",
        "TARDE",
        false
      );
    });
  });

  it("2. Conciliación digital: muestra disclaimer de declaración manual y registra saldo informado con evidencia", async () => {
    const spyConciliarDigital = vi.spyOn(TesoreriaService, "conciliarCuentaDigital").mockResolvedValue({
      id: 2,
      reconciliation_id: "rec-dig-001",
      journal_id: 1,
      fund_id: 6,
      fund_code: "1210",
      fund_name: "Mercado Pago Propio",
      naturaleza: "DIGITAL",
      is_digital: true,
      estado_revision: "CONCILIADO",
      reconciliation_type: "TOTAL",
      saldo_registrado_cents: 8000000,
      registered_amount_cents: 8000000,
      saldo_registrado_formatted: "$80.000,00",
      registered_amount_formatted: "$80.000,00",
      saldo_verificado_cents: 8000000,
      verified_amount_cents: 8000000,
      saldo_verificado_formatted: "$80.000,00",
      verified_amount_formatted: "$80.000,00",
      diferencia_cents: 0,
      difference_cents: 0,
      diferencia_formatted: "$0,00",
      difference_formatted: "$0,00",
      evidencia_referencia: "EXTRACTO-MP-987654",
      performed_by: "alan",
    });

    render(<TesoreriaLab currentUser={{ username: "alan", display_name: "Alan Rodrigo" }} />);

    // Ir a pestaña Arqueos y cambiar a subpestaña Digital
    const tabBtn = await screen.findByRole("button", { name: /arqueos/i });
    fireEvent.click(tabBtn);

    const subTabDig = screen.getByTestId("subtab-conciliacion-digital");
    fireEvent.click(subTabDig);

    // Debe mostrar el banner obligatorio de Declaración Manual
    expect(screen.getByText(/DECLARACIÓN MANUAL DE SALDO DIGITAL/i)).toBeDefined();
    expect(
      screen.getByText(/Esta conciliación no realiza consultas automáticas vía API externa ni altera saldos contables/i)
    ).toBeDefined();

    // Completar saldo informado y evidencia
    const inputMonto = screen.getByTestId("input-dig-monto");
    fireEvent.change(inputMonto, { target: { value: "80000" } });

    const inputEvidencia = screen.getByTestId("input-dig-evidencia");
    fireEvent.change(inputEvidencia, { target: { value: "EXTRACTO-MP-987654" } });

    // Confirmar conciliación digital
    const btnConfirmar = screen.getByTestId("btn-confirmar-conciliacion-digital");
    fireEvent.click(btnConfirmar);

    await waitFor(() => {
      expect(spyConciliarDigital).toHaveBeenCalledWith(
        "1210",
        8000000,
        "EXTRACTO-MP-987654",
        "",
        expect.any(String),
        false,
        ""
      );
    });
  });

  it("3. Pestaña de Cierres: muestra matriz de 9 fondos, alerta de traslados en tránsito y checkboxes de autorización", async () => {
    const mockVal: ValidacionCierre = {
      can_close: true,
      blockers: [],
      warnings: ["Existen traslados en tránsito autorizados"],
      pending_movements_count: 0,
      transfers_in_transit_count: 1,
      cash_drawer_balance_formatted: "$50.000,00",
      open_differences_count: 0,
    };
    const spyValidar = vi.spyOn(TesoreriaService, "validarCierre").mockResolvedValue(mockVal);

    render(<TesoreriaLab currentUser={{ username: "alan", display_name: "Alan Rodrigo" }} />);

    // Ir a pestaña Cierres
    const tabCierres = await screen.findByTestId("tab-cierres");
    fireEvent.click(tabCierres);

    // Debe mostrar la tabla de fondos al cierre
    expect(screen.getByText(/Estado de Fondos y Conciliaciones al Cierre/i)).toBeDefined();

    // Debe mostrar la sección de traslados en tránsito con advertencia explícita
    expect(screen.getByText(/El dinero en tránsito NO está acreditado en destino/i)).toBeDefined();
    expect(screen.getByText("trf-transito-999".substring(0, 8) + "...")).toBeDefined();

    // Checkboxes de autorización
    const chkPendingTrf = screen.getByTestId("chk-allow-pending-transfers");
    const chkNonzero1110 = screen.getByTestId("chk-allow-nonzero-1110");
    fireEvent.click(chkPendingTrf);
    fireEvent.click(chkNonzero1110);

    // Validar precondiciones
    const btnValidar = screen.getByTestId("btn-validar-cierre");
    fireEvent.click(btnValidar);

    await waitFor(() => {
      expect(spyValidar).toHaveBeenCalledWith(1, true, true);
    });
  });

  it("4. Historial de Cierres: visualiza hash SHA-256 canónico y abre modal de snapshot criptográfico", async () => {
    render(<TesoreriaLab currentUser={{ username: "alan", display_name: "Alan Rodrigo" }} />);

    // Ir a pestaña Cierres
    const tabCierres = await screen.findByTestId("tab-cierres");
    fireEvent.click(tabCierres);

    // Debe listar el cierre en el historial
    expect(screen.getByText("Historial de Cierres de Jornada (1)")).toBeDefined();
    expect(screen.getByText(/abcdef1234567890/i)).toBeDefined();

    // Botón para ver snapshot
    const btnVerSnap = screen.getByTestId("btn-ver-snapshot-1");
    fireEvent.click(btnVerSnap);

    // Modal de Snapshot debe abrirse
    expect(screen.getByText(/Fotografía Criptográfica de Cierre — Jornada #1/i)).toBeDefined();
    expect(screen.getByText(/Hash SHA-256 Canónico \(Sellado Inmutable\)/i)).toBeDefined();
    expect(screen.getByText("abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890")).toBeDefined();
  });
});
