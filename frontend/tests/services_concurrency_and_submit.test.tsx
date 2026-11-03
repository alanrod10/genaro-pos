import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ServicesAdmin, fechaHoyJujuy } from "../src/components/ServicesAdmin";
import { MockApiClient, setApiClient } from "../src/services/api";
import { CargaServicio, ResumenServicios } from "../src/types/services";

describe("GenaroPOS — Prioridad 2: Validación de Concurrencia y Envíos en Servicios", () => {
  let mockClient: MockApiClient;
  const mockOnAddToast = vi.fn();

  const dummyCargas: CargaServicio[] = [
    {
      id: 101,
      servicio: "Claro",
      monto_carga: 2000,
      monto_adicional: 100,
      total_cobrado: 2100,
      pago_efvo: 2100,
      pago_transf: 0,
      fecha: "2026-10-27",
      anulado: false,
    },
    {
      id: 102,
      servicio: "Personal",
      monto_carga: 4000,
      monto_adicional: 200,
      total_cobrado: 4200,
      pago_efvo: 0,
      pago_transf: 4200,
      fecha: "2026-10-27",
      anulado: false,
    },
  ];

  const dummyResumen: ResumenServicios = {
    total_operaciones: 2,
    total_capital: 6000,
    total_adicional: 300,
    total_cobrado: 6300,
    capital_prepay: 0,
    capital_claro: 2000,
    capital_sube_mp: 0,
    total_efectivo: 2100,
    total_transferencia: 4200,
    operaciones_anuladas: 0,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    mockClient = new MockApiClient();
    setApiClient(mockClient);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // 1. Concurrencia: listado y resumen se solicitan concurrentemente sin esperar uno al otro
  it("1. Solicita concurrentemente el listado y el resumen al cargar el historial", async () => {
    let resolverListado!: (val: any) => void;
    let resolverResumen!: (val: any) => void;

    const listadoPromise = new Promise((resolve) => {
      resolverListado = resolve;
    });
    const resumenPromise = new Promise((resolve) => {
      resolverResumen = resolve;
    });

    const spyObtenerCargas = vi
      .spyOn(mockClient, "obtenerCargas")
      .mockImplementation(() => listadoPromise as any);
    const spyObtenerResumen = vi
      .spyOn(mockClient, "obtenerResumenCargas")
      .mockImplementation(() => resumenPromise as any);

    render(<ServicesAdmin onAddToast={mockOnAddToast} isDemo={false} />);

    // Ambos endpoints deben haberse invocado inmediatamente sin esperar que el otro termine
    expect(spyObtenerCargas).toHaveBeenCalledTimes(1);
    expect(spyObtenerResumen).toHaveBeenCalledTimes(1);

    // Resolver ambas promesas
    resolverListado({ status: 200, data: { total: 2, cargas: dummyCargas } });
    resolverResumen({ status: 200, data: dummyResumen });

    await waitFor(() => {
      expect(screen.getByTestId("kpi-total-cobrado")).toHaveTextContent("$6.300");
    });
  });

  // 2. Errores: si falla el listado o el resumen, se notifica y el estado de carga no queda bloqueado
  it("2a. Error en el resumen no impide mostrar el listado y no deja el estado de carga bloqueado", async () => {
    vi.spyOn(mockClient, "obtenerCargas").mockResolvedValue({
      status: 200,
      data: { total: 2, cargas: dummyCargas },
    });
    vi.spyOn(mockClient, "obtenerResumenCargas").mockResolvedValue({
      status: 500,
      error: "Error interno al calcular resumen",
    });

    render(<ServicesAdmin onAddToast={mockOnAddToast} isDemo={false} />);

    await waitFor(() => {
      expect(mockOnAddToast).toHaveBeenCalledWith("Error interno al calcular resumen", "error");
    });

    // El listado sí se cargó (se renderiza en tabla y tarjetas)
    expect(screen.getAllByText("Claro").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Personal").length).toBeGreaterThan(0);

    // El botón de registro no quedó atascado
    const btnSubmit = screen.getByTestId("btn-register-charge");
    expect(btnSubmit).not.toHaveTextContent("Cargando...");
  });

  it("2b. Error en el listado no impide mostrar el resumen y no deja el estado de carga bloqueado", async () => {
    vi.spyOn(mockClient, "obtenerCargas").mockResolvedValue({
      status: 500,
      error: "Error de conexión con la base",
    });
    vi.spyOn(mockClient, "obtenerResumenCargas").mockResolvedValue({
      status: 200,
      data: dummyResumen,
    });

    render(<ServicesAdmin onAddToast={mockOnAddToast} isDemo={false} />);

    await waitFor(() => {
      expect(mockOnAddToast).toHaveBeenCalledWith("Error de conexión con la base", "error");
    });

    // El resumen sí se muestra
    expect(screen.getByTestId("kpi-total-cobrado")).toHaveTextContent("$6.300");

    // El botón no quedó atascado
    const btnSubmit = screen.getByTestId("btn-register-charge");
    expect(btnSubmit).not.toHaveTextContent("Cargando...");
  });

  // 3. Un único envío: el formulario registra una sola vez en un submit normal
  it("3. Registra exactamente una vez ante un envío normal del formulario", async () => {
    vi.spyOn(mockClient, "obtenerCargas").mockResolvedValue({
      status: 200,
      data: { total: 0, cargas: [] },
    });
    vi.spyOn(mockClient, "obtenerResumenCargas").mockResolvedValue({
      status: 200,
      data: { ...dummyResumen, total_operaciones: 0, total_cobrado: 0 },
    });

    const spyRegistrar = vi.spyOn(mockClient, "registrarCarga").mockResolvedValue({
      status: 201,
      data: {
        id: 201,
        servicio: "Personal",
        monto_carga: 2000,
        monto_adicional: 100,
        total_cobrado: 2100,
        pago_efvo: 2100,
        pago_transf: 0,
        fecha: "2026-10-27",
        anulado: false,
      },
    });

    render(<ServicesAdmin onAddToast={mockOnAddToast} isDemo={false} />);

    await waitFor(() => {
      expect(screen.getByTestId("input-service-amount")).toBeInTheDocument();
    });

    // Rellenar servicio y monto
    fireEvent.change(screen.getByTestId("select-plataforma-origen"), { target: { value: "PREPAY" } });
    fireEvent.change(screen.getByTestId("select-servicio-destino"), { target: { value: "Personal" } });
    const inputMonto = screen.getByTestId("input-service-amount");
    fireEvent.change(inputMonto, { target: { value: "2000" } });

    // Submit del formulario
    const form = screen.getByTestId("services-form");
    fireEvent.submit(form);

    await waitFor(() => {
      expect(spyRegistrar).toHaveBeenCalledTimes(1);
    });

    expect(mockOnAddToast).toHaveBeenCalledWith(
      expect.stringContaining("Carga registrada: PREPAY → Personal por $2.100"),
      "success"
    );
  });

  // 4. Envíos repetidos: doble submit o reenvío mientras está pendiente no duplica llamadas
  it("4. Protege contra envíos duplicados ante doble submit mientras la operación está pendiente", async () => {
    vi.spyOn(mockClient, "obtenerCargas").mockResolvedValue({
      status: 200,
      data: { total: 0, cargas: [] },
    });
    vi.spyOn(mockClient, "obtenerResumenCargas").mockResolvedValue({
      status: 200,
      data: dummyResumen,
    });

    let resolverRegistro!: (val: any) => void;
    const pendingPromise = new Promise((resolve) => {
      resolverRegistro = resolve;
    });

    const spyRegistrar = vi
      .spyOn(mockClient, "registrarCarga")
      .mockImplementation(() => pendingPromise as any);

    render(<ServicesAdmin onAddToast={mockOnAddToast} isDemo={false} />);

    await waitFor(() => {
      expect(screen.getByTestId("input-service-amount")).toBeInTheDocument();
    });

    fireEvent.change(screen.getByTestId("select-plataforma-origen"), { target: { value: "PREPAY" } });
    fireEvent.change(screen.getByTestId("select-servicio-destino"), { target: { value: "Personal" } });
    const inputMonto = screen.getByTestId("input-service-amount");
    fireEvent.change(inputMonto, { target: { value: "2000" } });

    const form = screen.getByTestId("services-form");
    const submitBtn = screen.getByTestId("btn-register-charge");

    // Primer submit
    fireEvent.submit(form);
    expect(spyRegistrar).toHaveBeenCalledTimes(1);

    // El botón debe estar deshabilitado con texto 'Guardando...'
    expect(submitBtn).toBeDisabled();
    expect(submitBtn).toHaveTextContent("Guardando...");

    // Intentar segundo y tercer submit mientras sigue pendiente
    fireEvent.submit(form);
    fireEvent.click(submitBtn);

    // No debe haberse llamado de nuevo
    expect(spyRegistrar).toHaveBeenCalledTimes(1);

    // Resolver la llamada
    resolverRegistro({
      status: 201,
      data: {
        id: 202,
        servicio: "Personal",
        monto_carga: 2000,
        monto_adicional: 100,
        total_cobrado: 2100,
        pago_efvo: 2100,
        pago_transf: 0,
        fecha: "2026-10-27",
        anulado: false,
      },
    });

    await waitFor(() => {
      expect(mockOnAddToast).toHaveBeenCalledWith(
        expect.stringContaining("Carga registrada"),
        "success"
      );
    });

    // Ahora que terminó, el botón vuelve a su estado normal (deshabilitado sólo si monto <= 0 por reset)
    expect(submitBtn).toHaveTextContent("🚀 Registrar Carga");
  });

  // 5. Resultado del registro: manejo de 200 (idempotente), 201 (nuevo) y errores del servidor
  it("5a. Muestra mensaje explícito de reintento idempotente cuando el backend responde 200", async () => {
    vi.spyOn(mockClient, "obtenerCargas").mockResolvedValue({
      status: 200,
      data: { total: 0, cargas: [] },
    });
    vi.spyOn(mockClient, "obtenerResumenCargas").mockResolvedValue({
      status: 200,
      data: dummyResumen,
    });

    vi.spyOn(mockClient, "registrarCarga").mockResolvedValue({
      status: 200,
      data: {
        id: 203,
        servicio: "Tuenti",
        monto_carga: 1500,
        monto_adicional: 100,
        total_cobrado: 1600,
        pago_efvo: 1600,
        pago_transf: 0,
        fecha: "2026-10-27",
        anulado: false,
      },
    });

    render(<ServicesAdmin onAddToast={mockOnAddToast} isDemo={false} />);

    await waitFor(() => {
      expect(screen.getByTestId("input-service-amount")).toBeInTheDocument();
    });

    fireEvent.change(screen.getByTestId("select-plataforma-origen"), { target: { value: "PREPAY" } });
    fireEvent.change(screen.getByTestId("select-servicio-destino"), { target: { value: "SUBE" } });
    const inputMonto = screen.getByTestId("input-service-amount");
    fireEvent.change(inputMonto, { target: { value: "1500" } });

    fireEvent.submit(screen.getByTestId("services-form"));

    await waitFor(() => {
      expect(mockOnAddToast).toHaveBeenCalledWith(
        expect.stringContaining("Carga confirmada (reintento idempotente)"),
        "success"
      );
    });
  });

  it("5b. Muestra error del formulario si el backend rechaza el registro", async () => {
    vi.spyOn(mockClient, "obtenerCargas").mockResolvedValue({
      status: 200,
      data: { total: 0, cargas: [] },
    });
    vi.spyOn(mockClient, "obtenerResumenCargas").mockResolvedValue({
      status: 200,
      data: dummyResumen,
    });

    vi.spyOn(mockClient, "registrarCarga").mockResolvedValue({
      status: 400,
      error: "Monto inválido para recargas SUBE",
    });

    render(<ServicesAdmin onAddToast={mockOnAddToast} isDemo={false} />);

    await waitFor(() => {
      expect(screen.getByTestId("input-service-amount")).toBeInTheDocument();
    });

    fireEvent.change(screen.getByTestId("select-plataforma-origen"), { target: { value: "PREPAY" } });
    fireEvent.change(screen.getByTestId("select-servicio-destino"), { target: { value: "SUBE" } });
    const inputMonto = screen.getByTestId("input-service-amount");
    fireEvent.change(inputMonto, { target: { value: "1500" } });

    fireEvent.submit(screen.getByTestId("services-form"));

    await waitFor(() => {
      expect(screen.getByTestId("services-form-error")).toHaveTextContent(
        "Monto inválido para recargas SUBE"
      );
    });
  });

  // 6. Anulación: actualización de lista y resumen tras anular, y protección contra doble anulación
  it("6. Actualiza lista y resumen tras anulación y previene doble confirmación de anulación", async () => {
    const spyCargas = vi.spyOn(mockClient, "obtenerCargas").mockResolvedValue({
      status: 200,
      data: { total: 2, cargas: dummyCargas },
    });
    const spyResumen = vi.spyOn(mockClient, "obtenerResumenCargas").mockResolvedValue({
      status: 200,
      data: dummyResumen,
    });

    let resolverAnular!: (val: any) => void;
    const anularPromise = new Promise((resolve) => {
      resolverAnular = resolve;
    });

    const spyAnular = vi
      .spyOn(mockClient, "anularCarga")
      .mockImplementation(() => anularPromise as any);

    render(<ServicesAdmin onAddToast={mockOnAddToast} isDemo={false} />);

    await waitFor(() => {
      expect(screen.getByTestId("btn-cancel-charge-101")).toBeInTheDocument();
    });

    // Abrir modal de anulación para la carga 101
    fireEvent.click(screen.getByTestId("btn-cancel-charge-101"));

    expect(screen.getByTestId("modal-confirm-cancel-charge")).toBeInTheDocument();
    const btnConfirm = screen.getByTestId("btn-confirm-cancel");

    // Primer clic en confirmar
    fireEvent.click(btnConfirm);
    expect(spyAnular).toHaveBeenCalledTimes(1);
    expect(btnConfirm).toBeDisabled();
    expect(btnConfirm).toHaveTextContent("Anulando...");

    // Clic repetido mientras la anulación está en curso
    fireEvent.click(btnConfirm);
    expect(spyAnular).toHaveBeenCalledTimes(1);

    // Resolver la anulación
    resolverAnular({ status: 200, data: { status: "success", id: 101, mensaje: "Anulada" } });

    await waitFor(() => {
      expect(mockOnAddToast).toHaveBeenCalledWith(
        expect.stringContaining("Carga #101 (Claro) anulada lógicamente"),
        "warning"
      );
    });

    // Debe haberse solicitado la recarga concurrente de listado y resumen
    expect(spyCargas).toHaveBeenCalledTimes(2);
    expect(spyResumen).toHaveBeenCalledTimes(2);
  });

  // 7. Filtros: conserva fecha inicial de Jujuy y permite consultar el historial completo
  it("7. Inicializa con la fecha de Jujuy y permite consultar historial completo y fechas anteriores", async () => {
    const spyCargas = vi.spyOn(mockClient, "obtenerCargas").mockResolvedValue({
      status: 200,
      data: { total: 0, cargas: [] },
    });
    const spyResumen = vi.spyOn(mockClient, "obtenerResumenCargas").mockResolvedValue({
      status: 200,
      data: dummyResumen,
    });

    render(<ServicesAdmin onAddToast={mockOnAddToast} isDemo={false} />);

    const hoyJujuy = fechaHoyJujuy();

    // Invocación inicial con fecha local de Jujuy
    expect(spyCargas).toHaveBeenCalledWith(hoyJujuy, false, undefined, undefined);
    expect(spyResumen).toHaveBeenCalledWith(hoyJujuy);

    // Cambiar a fecha anterior: 2026-10-15
    const dateInput = screen.getByTestId("services-date-filter");
    fireEvent.change(dateInput, { target: { value: "2026-10-15" } });

    await waitFor(() => {
      expect(spyCargas).toHaveBeenCalledWith("2026-10-15", false, undefined, undefined);
      expect(spyResumen).toHaveBeenCalledWith("2026-10-15");
    });

    // Limpiar fecha con botón '✕ Todas' para ver todo el historial
    const btnClearDate = screen.getByTestId("btn-clear-date");
    fireEvent.click(btnClearDate);

    await waitFor(() => {
      expect(spyCargas).toHaveBeenCalledWith(undefined, false, undefined, undefined);
      expect(spyResumen).toHaveBeenCalledWith(undefined);
    });
  });
});
