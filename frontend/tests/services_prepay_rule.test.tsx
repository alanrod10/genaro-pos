import { describe, it, expect } from "vitest";
import {
  calcularRecargoPorServicio,
  calcularRecargoAutomatico,
  CARGO_PREPAY_PRIMERA_JORNADA,
} from "../src/components/ServicesAdmin";

describe("GenaroPOS — Reglas Económicas Certificadas de Servicios y Recargos", () => {
  it("PREPAY aplica cargo aprobado de $350 exactamente en la primera recarga de la jornada", () => {
    expect(CARGO_PREPAY_PRIMERA_JORNADA).toBe(350);

    // Primera recarga de la jornada (tieneCargaPrepayPreviaHoy = false)
    expect(calcularRecargoPorServicio("PREPAY", 1000, false)).toBe(350);
    expect(calcularRecargoPorServicio("prepay", 2000, false)).toBe(350);
    expect(calcularRecargoPorServicio("Prepay", 5000, false)).toBe(350);

    // Recargas subsiguientes en la misma jornada (tieneCargaPrepayPreviaHoy = true)
    expect(calcularRecargoPorServicio("PREPAY", 1000, true)).toBe(0);
    expect(calcularRecargoPorServicio("PREPAY", 3000, true)).toBe(0);
  });

  it("Resto de servicios (Personal, Movistar, DIRECTV, etc.) conservan la regla escalonada ($100 cada $2000)", () => {
    // Escalonamiento estándar
    expect(calcularRecargoPorServicio("Personal", 500)).toBe(100);
    expect(calcularRecargoPorServicio("Personal", 2000)).toBe(100);
    expect(calcularRecargoPorServicio("Movistar", 2500)).toBe(200);
    expect(calcularRecargoPorServicio("DIRECTV", 4000)).toBe(200);
    expect(calcularRecargoPorServicio("Personal", 4500)).toBe(300);
    expect(calcularRecargoPorServicio("Personal", 10000)).toBe(500);

    // Mismo comportamiento que calcularRecargoAutomatico
    expect(calcularRecargoAutomatico(2000)).toBe(100);
    expect(calcularRecargoAutomatico(4000)).toBe(200);
  });
});
