import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { TesoreriaLab } from "../src/components/TesoreriaLab";
import { Header } from "../src/components/Header";

describe("GENARO TESORERÍA — Interfaz de Laboratorio (Sprint 1 & Sprint 2)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("1. Renderiza encabezado, badges de aislamiento y resumen general de saldos", async () => {
    render(<TesoreriaLab currentUser={{ username: "alan", display_name: "Alan" }} />);

    expect(screen.getByText("GENARO TESORERÍA")).toBeInTheDocument();
    expect(screen.getByText(/LABORATORIO — SPRINT/i)).toBeInTheDocument();
    expect(screen.getByText(/Aislamiento Estricto Activo/i)).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByTestId("saldo-registrado-total")).toHaveTextContent("$0,00");
      expect(screen.getByTestId("saldo-disponible-total")).toHaveTextContent("$0,00");
    });
  });

  it("2. Renderiza las tarjetas de los 9 fondos canónicos", async () => {
    render(<TesoreriaLab currentUser={{ username: "alan", display_name: "Alan" }} />);

    await waitFor(() => {
      expect(screen.getByTestId("card-fondo-1110")).toBeInTheDocument();
    });

    const codigosEsperados = ["1110", "1120", "1130", "1140", "1150", "1210", "1220", "1230", "1310"];
    for (const cod of codigosEsperados) {
      expect(screen.getByTestId(`card-fondo-${cod}`)).toBeInTheDocument();
    }

    expect(screen.getByText("Drugstore Diario")).toBeInTheDocument();
    expect(screen.getByText("Mercado Pago Propio")).toBeInTheDocument();
    expect(screen.getByText("Cobranzas en Tránsito Preventistas")).toBeInTheDocument();
  });

  it("3. Abre el modal de detalle de fondo y muestra su auditoría", async () => {
    render(<TesoreriaLab currentUser={{ username: "alan", display_name: "Alan" }} />);

    await waitFor(() => {
      expect(screen.getByTestId("btn-detalle-1110")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("btn-detalle-1110"));

    await waitFor(() => {
      expect(screen.getByTestId("modal-detalle-fondo")).toBeInTheDocument();
      expect(screen.getByText(/Pista de Auditoría/i)).toBeInTheDocument();
      expect(screen.getByText(/Alta inicial en catálogo/i)).toBeInTheDocument();
    });
  });

  it("4. El Header incluye el botón de navegación a Tesorería Lab", () => {
    const handleSelectView = vi.fn();
    render(
      <Header
        isOnline={true}
        isDemo={false}
        activeView="caja"
        onSelectView={handleSelectView}
        onToggleDemo={() => {}}
        pendingCount={0}
        reviewCount={0}
        onOpenQueue={() => {}}
      />
    );

    const btnTesoreria = screen.getByTestId("nav-btn-tesoreria");
    expect(btnTesoreria).toBeInTheDocument();
    fireEvent.click(btnTesoreria);
    expect(handleSelectView).toHaveBeenCalledWith("tesoreria");
  });

  it("5. Alterna entre las 4 pestañas de Sprint 2 (Disponibilidad, Movimientos, Traslados, Reservas)", async () => {
    render(<TesoreriaLab currentUser={{ username: "alan", display_name: "Alan" }} />);

    // Pestaña Movimientos
    const tabMovs = screen.getByTestId("tab-movimientos");
    fireEvent.click(tabMovs);
    expect(screen.getByText(/Movimientos Reales de Tesorería/i)).toBeInTheDocument();
    expect(screen.getByTestId("btn-nueva-entrada")).toBeInTheDocument();
    expect(screen.getByTestId("btn-nueva-salida")).toBeInTheDocument();

    // Pestaña Traslados
    const tabTrfs = screen.getByTestId("tab-traslados");
    fireEvent.click(tabTrfs);
    expect(screen.getByText(/Traslados de Fondos/i)).toBeInTheDocument();
    expect(screen.getByTestId("btn-nuevo-traslado")).toBeInTheDocument();

    // Pestaña Reservas
    const tabRsvs = screen.getByTestId("tab-reservas");
    fireEvent.click(tabRsvs);
    expect(screen.getByText(/Reservas Preventivas de Fondos/i)).toBeInTheDocument();
    expect(screen.getByTestId("btn-nueva-reserva")).toBeInTheDocument();

    // Volver a Disponibilidad
    const tabDisp = screen.getByTestId("tab-disponibilidad");
    fireEvent.click(tabDisp);
    expect(screen.getByText(/Catálogo y Disponibilidad de Fondos/i)).toBeInTheDocument();
  });

  it("6. Abre los modales de entrada, salida, traslado y reserva", async () => {
    render(<TesoreriaLab currentUser={{ username: "alan", display_name: "Alan" }} />);

    // Movimientos -> Entrada
    fireEvent.click(screen.getByTestId("tab-movimientos"));
    fireEvent.click(screen.getByTestId("btn-nueva-entrada"));
    expect(screen.getByText(/Registrar ENTRADA de Fondos/i)).toBeInTheDocument();
    expect(screen.getByTestId("input-mov-monto")).toBeInTheDocument();
    expect(screen.getByTestId("input-mov-contrapartida")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));

    // Movimientos -> Salida
    fireEvent.click(screen.getByTestId("btn-nueva-salida"));
    expect(screen.getByText(/Registrar SALIDA de Fondos/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));

    // Traslados -> Despachar Traslado
    fireEvent.click(screen.getByTestId("tab-traslados"));
    fireEvent.click(screen.getByTestId("btn-nuevo-traslado"));
    expect(screen.getByText(/Despachar Traslado de Fondos/i)).toBeInTheDocument();
    expect(screen.getByTestId("select-trf-origen")).toBeInTheDocument();
    expect(screen.getByTestId("select-trf-destino")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));

    // Reservas -> Crear Reserva
    fireEvent.click(screen.getByTestId("tab-reservas"));
    fireEvent.click(screen.getByTestId("btn-nueva-reserva"));
    expect(screen.getByText(/Crear Reserva Preventiva/i)).toBeInTheDocument();
    expect(screen.getByTestId("input-res-motivo")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
  });

  it("7. Navega a Arqueos y permite abrir el modal de Nuevo Arqueo", async () => {
    render(<TesoreriaLab currentUser={{ username: "alan", display_name: "Alan" }} />);

    const tabArqueos = screen.getByTestId("tab-arqueos");
    fireEvent.click(tabArqueos);

    expect(screen.getByText(/Arqueos de Fondos y Expedientes de Diferencias/i)).toBeInTheDocument();
    const btnNuevoArqueo = screen.getByTestId("btn-nuevo-arqueo");
    expect(btnNuevoArqueo).toBeInTheDocument();

    fireEvent.click(btnNuevoArqueo);
    expect(screen.getByText(/Registrar Arqueo de Fondo/i)).toBeInTheDocument();
    expect(screen.getByTestId("select-arq-fondo")).toBeInTheDocument();
    expect(screen.getByTestId("select-arq-tipo")).toBeInTheDocument();
    expect(screen.getByTestId("input-arq-verificado")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
  });

  it("8. Navega a Cierres y muestra el asistente de cierre diario", async () => {
    render(<TesoreriaLab currentUser={{ username: "alan", display_name: "Alan" }} />);

    const tabCierres = screen.getByTestId("tab-cierres");
    fireEvent.click(tabCierres);

    expect(screen.getByText(/Protocolo de Cierre Diario Definitivo/i)).toBeInTheDocument();
    expect(screen.getByText(/Jornada Activa/i)).toBeInTheDocument();
  });

  it("9. Navega a Reversiones y permite abrir el modal de Reversión", async () => {
    render(<TesoreriaLab currentUser={{ username: "alan", display_name: "Alan" }} />);

    const tabReversiones = screen.getByTestId("tab-reversiones");
    fireEvent.click(tabReversiones);

    expect(screen.getByText(/Reversiones Inmutables y Contra-Asientos/i)).toBeInTheDocument();
    expect(screen.getByTestId("btn-nueva-reversion")).toBeInTheDocument();
  });

  it("10. Navega a Auditoría y muestra el botón de verificación criptográfica", async () => {
    render(<TesoreriaLab currentUser={{ username: "alan", display_name: "Alan" }} />);

    const tabAuditoria = screen.getByTestId("tab-auditoria");
    fireEvent.click(tabAuditoria);

    expect(screen.getByText(/Pista de Auditoría Encadenada \(Hash Chain\)/i)).toBeInTheDocument();
    expect(screen.getByTestId("btn-verificar-auditoria")).toBeInTheDocument();
  });

  it("11. Navega a Outbox y muestra métricas y botón de procesamiento", async () => {
    render(<TesoreriaLab currentUser={{ username: "alan", display_name: "Alan" }} />);

    const tabOutbox = screen.getByTestId("tab-outbox");
    fireEvent.click(tabOutbox);

    expect(screen.getByText(/Bandeja Transaccional de Salida \(Outbox Pattern\)/i)).toBeInTheDocument();
    expect(screen.getByTestId("btn-procesar-outbox")).toBeInTheDocument();
    expect(screen.getByText(/Total Eventos/i)).toBeInTheDocument();
  });
});
