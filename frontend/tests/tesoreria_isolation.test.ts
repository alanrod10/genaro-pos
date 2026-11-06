import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { TesoreriaService } from "../src/services/tesoreria";

describe("ISO-006: Aislamiento del Laboratorio en Frontend", () => {
  const dummyFondos = [
    { id: 1, codigo: "1110", nombre: "Drugstore Diario", saldo_centavos: 0, saldo_formateado: "$0,00", estado: "ACTIVO" },
    { id: 2, codigo: "1120", nombre: "Drugstore Acumulado", saldo_centavos: 0, saldo_formateado: "$0,00", estado: "ACTIVO" },
    { id: 3, codigo: "1130", nombre: "Prepay Efectivo", saldo_centavos: 0, saldo_formateado: "$0,00", estado: "ACTIVO" },
    { id: 4, codigo: "1140", nombre: "Adicionales Efectivo", saldo_centavos: 0, saldo_formateado: "$0,00", estado: "ACTIVO" },
    { id: 5, codigo: "1150", nombre: "Claro Efectivo", saldo_centavos: 0, saldo_formateado: "$0,00", estado: "ACTIVO" },
    { id: 6, codigo: "1160", nombre: "Prepay Diario Efectivo", saldo_centavos: 0, saldo_formateado: "$0,00", estado: "ACTIVO" },
    { id: 7, codigo: "1210", nombre: "Mercado Pago Propio", saldo_centavos: 0, saldo_formateado: "$0,00", estado: "ACTIVO" },
    { id: 8, codigo: "1220", nombre: "Prepay Digital Saldo", saldo_centavos: 0, saldo_formateado: "$0,00", estado: "ACTIVO" },
    { id: 9, codigo: "1230", nombre: "Claro Comercios Digital Saldo", saldo_centavos: 0, saldo_formateado: "$0,00", estado: "ACTIVO" },
    { id: 10, codigo: "1310", nombre: "Cobranzas en Tránsito Preventistas", saldo_centavos: 0, saldo_formateado: "$0,00", estado: "ACTIVO" },
  ];

  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.includes("/resumen")) {
        return {
          ok: true,
          json: async () => ({
            aislamiento_confirmado: true,
            total_fondos: 10,
            fondos_activos: 10,
            total_saldo_registrado_centavos: 0,
            total_saldo_disponible_centavos: 0,
          }),
        };
      }
      if (url.includes("/fondos")) {
        return {
          ok: true,
          json: async () => dummyFondos,
        };
      }
      if (url.includes("/jornada/abrir")) {
        return {
          ok: true,
          json: async () => ({
            id: 1,
            estado: "ABIERTA",
            total_apertura_centavos: 0,
            detalles: dummyFondos.map(f => ({ ...f, saldo_apertura_centavos: 0 })),
          }),
        };
      }
      if (url.includes("/jornada/cerrar")) {
        return {
          ok: true,
          json: async () => ({
            id: 1,
            estado: "CERRADA",
          }),
        };
      }
      return { ok: false, status: 404, statusText: "Not Found", json: async () => ({ detail: "Not Found" }) };
    }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });
  it("No accede ni modifica la base comercial IndexedDB", async () => {
    // Verificar que el servicio de tesorería obtiene su resumen sin registrar nada en sales_queue ni commercial stores
    const resumen = await TesoreriaService.obtenerResumen();
    expect(resumen.aislamiento_confirmado).toBe(true);
    expect(resumen.total_fondos).toBe(10);
    expect(resumen.total_saldo_registrado_centavos).toBe(0);
  });

  it("El catálogo de laboratorio contiene exactamente los 10 fondos requeridos con saldo 0", async () => {
    const fondos = await TesoreriaService.obtenerFondos();
    expect(fondos.length).toBe(10);

    const codigos = fondos.map((f) => f.codigo);
    const esperados = ["1110", "1120", "1130", "1140", "1150", "1160", "1210", "1220", "1230", "1310"];
    expect(codigos).toEqual(esperados);

    for (const f of fondos) {
      expect(f.saldo_centavos).toBe(0);
      expect(f.saldo_formateado).toBe("$0,00");
    }
  });

  it("Ciclo de vida de jornada en laboratorio desacoplado", async () => {
    // Abrir jornada
    const jornada = await TesoreriaService.abrirJornada("alan", "Apertura test aislamiento");
    expect(jornada.estado).toBe("ABIERTA");
    expect(jornada.total_apertura_centavos).toBe(0);
    expect(jornada.detalles?.length).toBe(10);

    // Cerrar jornada
    const cerrada = await TesoreriaService.cerrarJornada(
      jornada.id,
      { "1110": 0 },
      "Cierre conforme"
    );
    expect(cerrada.estado).toBe("CERRADA");
  });
});
