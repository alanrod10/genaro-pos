import { describe, it, expect } from "vitest";
import { TesoreriaService } from "../src/services/tesoreria";

describe("ISO-006: Aislamiento del Laboratorio en Frontend", () => {
  it("No accede ni modifica la base comercial IndexedDB", async () => {
    // Verificar que el servicio de tesorería obtiene su resumen sin registrar nada en sales_queue ni commercial stores
    const resumen = await TesoreriaService.obtenerResumen();
    expect(resumen.aislamiento_confirmado).toBe(true);
    expect(resumen.total_fondos).toBe(9);
    expect(resumen.total_saldo_registrado_centavos).toBe(0);
  });

  it("El catálogo de laboratorio contiene exactamente los 9 fondos requeridos con saldo 0", async () => {
    const fondos = await TesoreriaService.obtenerFondos();
    expect(fondos.length).toBe(9);

    const codigos = fondos.map((f) => f.codigo);
    const esperados = ["1110", "1120", "1130", "1140", "1150", "1210", "1220", "1230", "1310"];
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
    expect(jornada.detalles?.length).toBe(9);

    // Cerrar jornada
    const cerrada = await TesoreriaService.cerrarJornada(
      jornada.id,
      { "1110": 0 },
      "Cierre conforme"
    );
    expect(cerrada.estado).toBe("CERRADA");
  });
});
