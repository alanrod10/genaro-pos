import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { ServicesAdmin } from "../src/components/ServicesAdmin";
import { MockApiClient, setApiClient } from "../src/services/api";
import { CargaServicio, ResumenServicios } from "../src/types/services";

describe("GenaroPOS — Prioridad 4: Renderizado Adaptativo de Servicios", () => {
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
      fecha: "27/10/2026 10:00:00",
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
      fecha: "27/10/2026 11:00:00",
      anulado: false,
    },
  ];

  const dummyResumen: ResumenServicios = {
    total_operaciones: 2,
    total_capital: 6000,
    total_adicional: 300,
    total_cobrado: 6300,
    capital_prepay: 4000,
    capital_claro: 2000,
    capital_sube_mp: 0,
    total_efectivo: 2100,
    total_transferencia: 4200,
    operaciones_anuladas: 0,
  };

  const originalInnerWidth = window.innerWidth;
  const originalMatchMedia = window.matchMedia;

  beforeEach(() => {
    vi.clearAllMocks();
    mockClient = new MockApiClient();
    setApiClient(mockClient);

    vi.spyOn(mockClient, "obtenerCargas").mockResolvedValue({
      status: 200,
      data: { total: 2, cargas: dummyCargas },
    });
    vi.spyOn(mockClient, "obtenerResumenCargas").mockResolvedValue({
      status: 200,
      data: dummyResumen,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    Object.defineProperty(window, "innerWidth", {
      writable: true,
      configurable: true,
      value: originalInnerWidth,
    });
    window.matchMedia = originalMatchMedia;
  });

  // 1. En viewport móvil solo se monta .services-cards-grid
  it("1. En viewport móvil (<= 800px) solo se monta la cuadrícula de tarjetas y no la tabla", async () => {
    Object.defineProperty(window, "innerWidth", {
      writable: true,
      configurable: true,
      value: 600,
    });

    render(<ServicesAdmin onAddToast={mockOnAddToast} isDemo={false} />);

    await waitFor(() => {
      expect(screen.getByTestId("services-cards-grid")).toBeInTheDocument();
    });

    // La tabla de escritorio NO debe estar en el DOM
    expect(screen.queryByTestId("services-table")).not.toBeInTheDocument();
    expect(screen.queryByTestId("services-table-wrapper")).not.toBeInTheDocument();

    // Las tarjetas deben existir
    expect(screen.getByTestId("service-card-101")).toBeInTheDocument();
    expect(screen.getByTestId("service-card-102")).toBeInTheDocument();
  });

  // 2. En viewport de escritorio solo se monta la tabla
  it("2. En viewport de escritorio (> 800px) solo se monta la tabla y no las tarjetas", async () => {
    Object.defineProperty(window, "innerWidth", {
      writable: true,
      configurable: true,
      value: 1200,
    });

    render(<ServicesAdmin onAddToast={mockOnAddToast} isDemo={false} />);

    await waitFor(() => {
      expect(screen.getByTestId("services-table")).toBeInTheDocument();
    });

    // La cuadrícula de tarjetas móviles NO debe estar en el DOM
    expect(screen.queryByTestId("services-cards-grid")).not.toBeInTheDocument();
    expect(screen.queryByTestId("service-card-101")).not.toBeInTheDocument();

    // La tabla de escritorio sí existe
    expect(screen.getByTestId("services-table-wrapper")).toBeInTheDocument();
    expect(screen.getByTestId("btn-edit-charge-101")).toBeInTheDocument();
  });

  // 3. Exclusión mutua estricta: nunca están montadas ambas representaciones simultáneamente
  it("3. Nunca están montadas ambas representaciones al mismo tiempo", async () => {
    // Caso móvil forzado
    const { unmount } = render(
      <ServicesAdmin onAddToast={mockOnAddToast} isDemo={false} isMobileForced={true} />
    );

    await waitFor(() => {
      expect(screen.getByTestId("services-cards-grid")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("services-table")).not.toBeInTheDocument();
    unmount();

    // Caso escritorio forzado
    render(
      <ServicesAdmin onAddToast={mockOnAddToast} isDemo={false} isMobileForced={false} />
    );

    await waitFor(() => {
      expect(screen.getByTestId("services-table")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("services-cards-grid")).not.toBeInTheDocument();
  });

  // 4. Al cambiar el viewport de escritorio a móvil y viceversa, la representación conmuta dinámicamente
  it("4. Conmuta dinámicamente de escritorio a móvil y viceversa ante eventos de resize", async () => {
    Object.defineProperty(window, "innerWidth", {
      writable: true,
      configurable: true,
      value: 1024,
    });

    render(<ServicesAdmin onAddToast={mockOnAddToast} isDemo={false} />);

    await waitFor(() => {
      expect(screen.getByTestId("services-table")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("services-cards-grid")).not.toBeInTheDocument();

    // Redimensionar a móvil (500px)
    Object.defineProperty(window, "innerWidth", {
      writable: true,
      configurable: true,
      value: 500,
    });
    fireEvent(window, new Event("resize"));

    await waitFor(() => {
      expect(screen.getByTestId("services-cards-grid")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("services-table")).not.toBeInTheDocument();

    // Regresar a escritorio (900px)
    Object.defineProperty(window, "innerWidth", {
      writable: true,
      configurable: true,
      value: 900,
    });
    fireEvent(window, new Event("resize"));

    await waitFor(() => {
      expect(screen.getByTestId("services-table")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("services-cards-grid")).not.toBeInTheDocument();
  });

  // 5. Los filtros y el contenido no se pierden al conmutar de representación
  it("5. Conserva el texto de búsqueda, filtros seleccionados y datos al conmutar", async () => {
    Object.defineProperty(window, "innerWidth", {
      writable: true,
      configurable: true,
      value: 1200,
    });

    render(<ServicesAdmin onAddToast={mockOnAddToast} isDemo={false} />);

    await waitFor(() => {
      expect(screen.getByTestId("services-table")).toBeInTheDocument();
    });

    // Aplicar filtro de búsqueda
    const inputSearch = screen.getByTestId("services-search-input");
    fireEvent.change(inputSearch, { target: { value: "Personal" } });

    // En tabla solo aparece Personal
    const table = screen.getByTestId("services-table");
    expect(within(table).getByText("Personal")).toBeInTheDocument();
    expect(within(table).queryByText("Claro")).not.toBeInTheDocument();

    // Redimensionar a móvil (400px)
    Object.defineProperty(window, "innerWidth", {
      writable: true,
      configurable: true,
      value: 400,
    });
    fireEvent(window, new Event("resize"));

    await waitFor(() => {
      expect(screen.getByTestId("services-cards-grid")).toBeInTheDocument();
    });

    // El input conserva el valor 'Personal' y las tarjetas reflejan el mismo filtro
    expect(screen.getByTestId("services-search-input")).toHaveValue("Personal");
    expect(screen.getByTestId("service-card-102")).toBeInTheDocument();
    expect(screen.queryByTestId("service-card-101")).not.toBeInTheDocument();
  });

  // 6. Las acciones disponibles en móvil y escritorio abren los modales correspondientes
  it("6. Las acciones de edición y anulación funcionan correctamente en móvil y escritorio", async () => {
    // Vista móvil
    const { unmount } = render(
      <ServicesAdmin onAddToast={mockOnAddToast} isDemo={false} isMobileForced={true} />
    );

    await waitFor(() => {
      expect(screen.getByTestId("btn-card-edit-101")).toBeInTheDocument();
    });

    // Abrir edición desde tarjeta móvil
    fireEvent.click(screen.getByTestId("btn-card-edit-101"));
    expect(screen.getByTestId("modal-edit-charge")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("btn-close-edit-modal"));
    expect(screen.queryByTestId("modal-edit-charge")).not.toBeInTheDocument();

    // Abrir anulación desde tarjeta móvil
    fireEvent.click(screen.getByTestId("btn-card-cancel-101"));
    expect(screen.getByTestId("modal-confirm-cancel-charge")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("btn-abort-cancel"));
    expect(screen.queryByTestId("modal-confirm-cancel-charge")).not.toBeInTheDocument();

    unmount();

    // Vista escritorio
    render(
      <ServicesAdmin onAddToast={mockOnAddToast} isDemo={false} isMobileForced={false} />
    );

    await waitFor(() => {
      expect(screen.getByTestId("btn-edit-charge-101")).toBeInTheDocument();
    });

    // Abrir edición desde tabla escritorio
    fireEvent.click(screen.getByTestId("btn-edit-charge-101"));
    expect(screen.getByTestId("modal-edit-charge")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("btn-close-edit-modal"));

    // Abrir anulación desde tabla escritorio
    fireEvent.click(screen.getByTestId("btn-cancel-charge-101"));
    expect(screen.getByTestId("modal-confirm-cancel-charge")).toBeInTheDocument();
  });

  // 7. Listado vacío y estados de carga en ambas representaciones
  it("7. Maneja correctamente el estado vacío en móvil y escritorio", async () => {
    vi.spyOn(mockClient, "obtenerCargas").mockResolvedValue({
      status: 200,
      data: { total: 0, cargas: [] },
    });

    // En móvil
    const { unmount } = render(
      <ServicesAdmin onAddToast={mockOnAddToast} isDemo={false} isMobileForced={true} />
    );
    await waitFor(() => {
      expect(screen.getByTestId("services-empty-cards")).toBeInTheDocument();
    });
    unmount();

    // En escritorio
    render(
      <ServicesAdmin onAddToast={mockOnAddToast} isDemo={false} isMobileForced={false} />
    );
    await waitFor(() => {
      expect(screen.getByTestId("services-empty-table")).toBeInTheDocument();
    });
  });

  // 8. Soporte robusto cuando matchMedia no está disponible
  it("8. useIsMobile funciona limpiamente cuando window.matchMedia es undefined", async () => {
    // Simular entorno sin matchMedia
    // @ts-expect-error test matchMedia missing
    delete window.matchMedia;

    Object.defineProperty(window, "innerWidth", {
      writable: true,
      configurable: true,
      value: 700,
    });

    render(<ServicesAdmin onAddToast={mockOnAddToast} isDemo={false} />);

    // Con 700px y sin matchMedia debe identificar móvil
    await waitFor(() => {
      expect(screen.getByTestId("services-cards-grid")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("services-table")).not.toBeInTheDocument();
  });
});
