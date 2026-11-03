import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Header } from "../src/components/Header";
import {
  THEME_STORAGE_KEY,
  getSavedThemeMode,
  resolveTheme,
  applyThemeToDOM,
} from "../src/services/theme";

describe("GenaroPOS — Sistema de Temas Visuales (Midnight Terracotta & Warm Ivory)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
    document.documentElement.removeAttribute("data-theme-mode");
  });

  it("1. Usa 'dark' (Midnight Terracotta) como preferencia segura por defecto si no hay valor guardado", () => {
    expect(getSavedThemeMode()).toBe("dark");
    expect(resolveTheme("dark")).toBe("dark");
    expect(resolveTheme("light")).toBe("light");
  });

  it("2. Aplica correctamente data-theme y data-theme-mode en document.documentElement", () => {
    applyThemeToDOM("light");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(document.documentElement.getAttribute("data-theme-mode")).toBe("light");

    applyThemeToDOM("system");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(document.documentElement.getAttribute("data-theme-mode")).toBe("system");
  });

  it("3. Renderiza el selector de temas en el Header y persiste la elección en localStorage", () => {
    render(
      <Header
        isOnline={true}
        isDemo={true}
        activeView="caja"
        onSelectView={vi.fn()}
        onToggleDemo={vi.fn()}
        pendingCount={0}
        reviewCount={0}
        onOpenQueue={vi.fn()}
      />
    );

    const selector = screen.getByTestId("theme-selector");
    expect(selector).toBeInTheDocument();

    const btnLight = screen.getByTestId("theme-btn-light");
    const btnDark = screen.getByTestId("theme-btn-dark");
    const btnSystem = screen.getByTestId("theme-btn-system");

    fireEvent.click(btnLight);
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(btnLight).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(btnDark);
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(btnDark).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(btnSystem);
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("system");
    expect(document.documentElement.getAttribute("data-theme-mode")).toBe("system");
    expect(btnSystem).toHaveAttribute("aria-pressed", "true");
  });
});
