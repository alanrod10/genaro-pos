import { describe, it, expect } from "vitest";
import {
  parseMoney,
  addMoney,
  subtractMoney,
  compareMoney,
  allocateMoney,
  formatMoney,
  assertValidMoney,
  ErrorMonetario,
  MAX_SAFE_MONEY,
  MIN_SAFE_MONEY,
} from "../src/utils/money";

describe("LAB-002: Biblioteca Monetaria en Frontend (TypeScript)", () => {
  it("MONEY-001: parseMoney parsea correctamente cadenas enteras, decimales y números sin punto flotante", () => {
    expect(parseMoney("100")).toBe(10000);
    expect(parseMoney("100.50")).toBe(10050);
    expect(parseMoney("100,50")).toBe(10050);
    expect(parseMoney("1234.56")).toBe(123456);
    expect(parseMoney("1.234,56")).toBe(123456);
    expect(parseMoney("$ 1.234,56")).toBe(123456);
    expect(parseMoney(100.5)).toBe(10050);
    expect(parseMoney({ centavos: 7500 })).toBe(7500);
  });

  it("MONEY-002: parseMoney rechaza strings malformados, valores no numéricos, infinitos o NaN", () => {
    expect(() => parseMoney(null)).toThrow(ErrorMonetario);
    expect(() => parseMoney(undefined)).toThrow(ErrorMonetario);
    expect(() => parseMoney("")).toThrow(ErrorMonetario);
    expect(() => parseMoney("abc")).toThrow(ErrorMonetario);
    expect(() => parseMoney(NaN)).toThrow(ErrorMonetario);
    expect(() => parseMoney(Infinity)).toThrow(ErrorMonetario);
    expect(() => parseMoney(true)).toThrow(ErrorMonetario);
  });

  it("MONEY-003: assertValidMoney valida enteros seguros en centavos", () => {
    expect(() => assertValidMoney(0)).not.toThrow();
    expect(() => assertValidMoney(10000)).not.toThrow();
    expect(() => assertValidMoney(-500)).not.toThrow();
    expect(() => assertValidMoney(MAX_SAFE_MONEY)).not.toThrow();
    expect(() => assertValidMoney(MIN_SAFE_MONEY)).not.toThrow();

    expect(() => assertValidMoney(10.5)).toThrow(ErrorMonetario);
    expect(() => assertValidMoney("100")).toThrow(ErrorMonetario);
    expect(() => assertValidMoney(false)).toThrow(ErrorMonetario);
    expect(() => assertValidMoney(MAX_SAFE_MONEY + 1)).toThrow(ErrorMonetario);
  });

  it("MONEY-004: addMoney suma exactamente centavos enteros", () => {
    expect(addMoney(10050, 20025)).toBe(30075);
    expect(addMoney(0, 500)).toBe(500);
    expect(addMoney(-100, 100)).toBe(0);
    expect(addMoney(-100, -200)).toBe(-300);
  });

  it("MONEY-005: subtractMoney resta exactamente centavos enteros", () => {
    expect(subtractMoney(30075, 10050)).toBe(20025);
    expect(subtractMoney(10000, 10000)).toBe(0);
    expect(subtractMoney(500, 1000)).toBe(-500);
  });

  it("MONEY-006: compareMoney compara montos determinísticamente", () => {
    expect(compareMoney(10000, 20000)).toBe(-1);
    expect(compareMoney(20000, 10000)).toBe(1);
    expect(compareMoney(15000, 15000)).toBe(0);
  });

  it("MONEY-007: allocateMoney distribuye montos indivisibles con preservación de suma exacta", () => {
    const res = allocateMoney(10000, [1, 1, 1]);
    expect(res).toEqual([3334, 3333, 3333]);
    expect(res.reduce((a, b) => a + b, 0)).toBe(10000);
  });

  it("MONEY-008: allocateMoney distribuye según ratios arbitrarios sin perder centavos", () => {
    const res = allocateMoney(10001, [1, 2, 1]);
    expect(res).toEqual([2500, 5001, 2500]);
    expect(res.reduce((a, b) => a + b, 0)).toBe(10001);
  });

  it("MONEY-009: formatMoney formatea correctamente centavos a representación contable ARS", () => {
    expect(formatMoney(123456)).toBe("$1.234,56");
    expect(formatMoney(0)).toBe("$0,00");
    expect(formatMoney(5)).toBe("$0,05");
    expect(formatMoney(-5000)).toBe("-$50,00");
  });

  it("MONEY-010: Inmunidad total contra el error clásico 0.1 + 0.2 != 0.3 de IEEE 754", () => {
    const a = parseMoney("0.10"); // 10 centavos
    const b = parseMoney("0.20"); // 20 centavos
    const c = parseMoney("0.30"); // 30 centavos
    expect(addMoney(a, b)).toBe(c);

    // Caso típico de parseFloat * 100
    expect(parseMoney("1.14")).toBe(114);
    expect(parseMoney(1.14)).toBe(114);
  });

  it("MONEY-011: Manejo consistente de negativos y cero", () => {
    expect(addMoney(0, 0)).toBe(0);
    expect(subtractMoney(0, 0)).toBe(0);
    expect(compareMoney(0, 0)).toBe(0);
    expect(formatMoney(0)).toBe("$0,00");

    const neg = parseMoney("-5.00");
    expect(neg).toBe(-500);
    expect(addMoney(neg, 500)).toBe(0);
    expect(subtractMoney(0, neg)).toBe(500);
  });

  it("MONEY-012: Operaciones con montos muy altos dentro de límites seguros", () => {
    const m1 = 500_000_000_000_00;
    const m2 = 250_000_000_000_00;
    const suma = addMoney(m1, m2);
    expect(suma).toBe(750_000_000_000_00);
    expect(subtractMoney(suma, m2)).toBe(m1);
  });
});
