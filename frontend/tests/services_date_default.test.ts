import { describe, it, expect } from "vitest";
import { fechaHoyJujuy } from "../src/components/ServicesAdmin";

describe("fechaHoyJujuy", () => {
  it("usa el día local de Jujuy (UTC-3), no el día UTC", () => {
    expect(fechaHoyJujuy(new Date("2026-10-27T02:30:00Z"))).toBe("2026-10-26");
    expect(fechaHoyJujuy(new Date("2026-10-27T03:00:00Z"))).toBe("2026-10-27");
    expect(fechaHoyJujuy(new Date("2026-10-27T23:59:59Z"))).toBe("2026-10-27");
  });
});
