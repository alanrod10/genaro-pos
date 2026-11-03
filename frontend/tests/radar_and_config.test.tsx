import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { RadarMonitor } from "../src/components/RadarMonitor";
import { ConfigAdmin } from "../src/components/ConfigAdmin";
import { Header } from "../src/components/Header";

describe("GENARO RADAR y Configuración — Demostración Integral (Etapa 8.1)", () => {
  it("1. Renderiza el monitor GENARO RADAR con KPIs, estado del worker y métricas", () => {
    const onAddToast = vi.fn();
    render(
      <RadarMonitor
        onAddToast={onAddToast}
        isDemo={true}
      />
    );

    expect(screen.getByText("GENARO RADAR")).toBeInTheDocument();
    expect(screen.getByText(/Worker Activo/i)).toBeInTheDocument();
    expect(screen.getByText("Total Emitidos")).toBeInTheDocument();
    expect(screen.getByText("Pendientes Cola")).toBeInTheDocument();
    expect(screen.getAllByText("Publicados (OK)")[0]).toBeInTheDocument();
    expect(screen.getAllByText("Letra Muerta")[0]).toBeInTheDocument();
  });

  it("2. Permite simular una venta Outbox en RADAR y emite notificación toast", () => {
    const onAddToast = vi.fn();
    render(
      <RadarMonitor
        onAddToast={onAddToast}
        isDemo={true}
      />
    );

    const btnSimular = screen.getByText(/Simular Venta/i);
    fireEvent.click(btnSimular);

    expect(onAddToast).toHaveBeenCalledWith(
      expect.stringContaining("Evento comercial"),
      "success"
    );
  });

  it("3. Renderiza ConfigAdmin con sus 5 secciones y matriz RBAC de Alan y Yilda", () => {
    const onAddToast = vi.fn();
    const onSelectRole = vi.fn();

    render(
      <ConfigAdmin
        onAddToast={onAddToast}
        isDemo={true}
        demoRole="admin"
        onSelectDemoRole={onSelectRole}
      />
    );

    expect(screen.getByText("Configuración & Administración")).toBeInTheDocument();
    expect(screen.getByText(/🏢 Entorno & Bases/i)).toBeInTheDocument();
    expect(screen.getByText(/⚡ Bridge Worker/i)).toBeInTheDocument();
    expect(screen.getByText(/👥 Control de Acceso/i)).toBeInTheDocument();
    expect(screen.getByText(/📜 9 Reglas Económicas/i)).toBeInTheDocument();

    // Cambiar a la pestaña de usuarios/RBAC
    fireEvent.click(screen.getByText(/👥 Control de Acceso/i));
    expect(screen.getByText(/Alan \(Socio\)/i)).toBeInTheDocument();
    expect(screen.getByText(/Yilda \(Socia\)/i)).toBeInTheDocument();
    expect(screen.getByText(/Cajero Mostrador/i)).toBeInTheDocument();

    // Cambiar a la pestaña de 9 reglas económicas
    fireEvent.click(screen.getByText(/📜 9 Reglas Económicas/i));
    expect(screen.getByText(/Adicionales Físicos \(1140\)/i)).toBeInTheDocument();
    expect(screen.getByText(/Claro en Custodia \(1150\)/i)).toBeInTheDocument();
  });

  it("4. Permite alternar la perspectiva de demostración entre Administrador y Cajero", () => {
    const onAddToast = vi.fn();
    const onSelectRole = vi.fn();

    render(
      <ConfigAdmin
        onAddToast={onAddToast}
        isDemo={true}
        demoRole="admin"
        onSelectDemoRole={onSelectRole}
      />
    );

    const btnCajero = screen.getByRole("button", { name: /🏪 Cajero/i });
    fireEvent.click(btnCajero);

    expect(onSelectRole).toHaveBeenCalledWith("cajero");
    expect(onAddToast).toHaveBeenCalledWith(
      expect.stringContaining("Cajero Mostrador"),
      "info"
    );
  });

  it("5. Header incluye los botones de navegación directa hacia RADAR y Configuración", () => {
    const onSelectView = vi.fn();

    render(
      <Header
        isOnline={true}
        isDemo={true}
        activeView="caja"
        onSelectView={onSelectView}
        onToggleDemo={vi.fn()}
        pendingCount={0}
        reviewCount={0}
        onOpenQueue={vi.fn()}
      />
    );

    const btnRadar = screen.getByTestId("nav-btn-radar");
    const btnConfig = screen.getByTestId("nav-btn-configuracion");

    expect(btnRadar).toBeInTheDocument();
    expect(btnConfig).toBeInTheDocument();

    fireEvent.click(btnRadar);
    expect(onSelectView).toHaveBeenCalledWith("radar");

    fireEvent.click(btnConfig);
    expect(onSelectView).toHaveBeenCalledWith("configuracion");
  });
});
