import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { Header } from "../src/components/Header";

describe("GenaroPOS — Retiro del Módulo Histórico Antiguo (Auditoría 2025-2026)", () => {
  it("1. Header NO incluye el botón de navegación al módulo HISTÓRICO antiguo 🏛️", () => {
    const handleSelect = vi.fn();
    render(
      <Header
        isOnline={true}
        isDemo={true}
        activeView="caja"
        onSelectView={handleSelect}
        onToggleDemo={() => {}}
        pendingCount={0}
        reviewCount={0}
        onOpenQueue={() => {}}
      />
    );

    const btn = screen.queryByTestId("nav-btn-historico");
    expect(btn).toBeNull();
    expect(screen.queryByText(/🏛️ Histórico/i)).toBeNull();
  });

  it("2. Los módulos operativos esenciales permanecen en la navegación", () => {
    const handleSelect = vi.fn();
    render(
      <Header
        isOnline={true}
        isDemo={true}
        activeView="caja"
        onSelectView={handleSelect}
        onToggleDemo={() => {}}
        pendingCount={0}
        reviewCount={0}
        onOpenQueue={() => {}}
      />
    );

    expect(screen.getByTestId("nav-btn-caja")).toBeInTheDocument();
    expect(screen.getByTestId("nav-btn-servicios")).toBeInTheDocument();
    expect(screen.getByTestId("nav-btn-gastos")).toBeInTheDocument();
    expect(screen.getByTestId("nav-btn-historial")).toBeInTheDocument();
    expect(screen.getByTestId("nav-btn-tesoreria")).toBeInTheDocument();
    expect(screen.getByTestId("nav-btn-radar")).toBeInTheDocument();
  });
});
