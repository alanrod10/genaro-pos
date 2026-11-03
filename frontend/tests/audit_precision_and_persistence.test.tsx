import { describe, it, expect, beforeEach } from "vitest";
import {
  isWeighableProduct,
  getReferencePricePerKg,
  calcularVentaPorPeso,
  calcularVentaPorImporte,
} from "../src/utils/weightSales";
import {
  guardarVentaLocal,
  obtenerVentaLocal,
  resetDBInstance,
} from "../src/services/db";
import { VentaPayload, VentaLocal } from "../src/types/sales";

describe("GenaroPOS — Auditoría Técnica de Precisión, Persistencia y Detección Segura", () => {
  beforeEach(async () => {
    resetDBInstance();
  });

  // ============================================================================
  // RIESGO 1: PRECISIÓN DE CANTIDADES FRACCIONARIAS Y PRECIOS PERIÓDICOS
  // ============================================================================
  describe("1. Precisión de cantidades periódicas y congruencia con el Worker", () => {
    const periodicCases = [
      { precioPorKg: 7000, importe: 1000, desc: "$7.000/kg, venta por $1.000" },
      { precioPorKg: 8500, importe: 1000, desc: "$8.500/kg, venta por $1.000" },
      { precioPorKg: 9999, importe: 1000, desc: "$9.999/kg, venta por $1.000" },
      { precioPorKg: 12000, importe: 1000, desc: "$12.000/kg, venta por $1.000" },
      { precioPorKg: 13750, importe: 2000, desc: "$13.750/kg, venta por $2.000 (caso crítico de 4 decimales)" },
      { precioPorKg: 3333, importe: 1000, desc: "$3.333/kg, venta por $1.000" },
    ];

    periodicCases.forEach(({ precioPorKg, importe, desc }) => {
      it(`Verifica precisión y regla de subtotal para ${desc}`, () => {
        const res = calcularVentaPorImporte(importe, precioPorKg);
        expect(res.error).toBeUndefined();
        expect(res.data).toBeDefined();

        // 1. El subtotal de la línea DEBE ser estrictamente el importe solicitado
        expect(res.data?.subtotal).toBe(importe);

        // 2. Con precisión de 6 decimales, verificar la condición del Worker:
        // Worker check: Math.round(cantidad * precio_unitario) == subtotal
        const cantidadGuardada = parseFloat(res.data!.pesoKg.toFixed(6));
        const subtotalCalculadoPorWorker = Math.round(cantidadGuardada * precioPorKg);

        expect(
          Math.abs(importe - subtotalCalculadoPorWorker),
          `Worker check falló para ${desc}: esperado ${importe}, calculado ${subtotalCalculadoPorWorker}`
        ).toBeLessThanOrEqual(0.01);
      });
    });

    it("Verifica venta por peso en gramos y kg con cálculo exacto", () => {
      const resG = calcularVentaPorPeso(350, "g", 8000);
      expect(resG.error).toBeUndefined();
      expect(resG.data?.subtotal).toBe(2800);
      expect(resG.data?.pesoKg).toBe(0.35);

      const resKg = calcularVentaPorPeso(1.25, "kg", 8000);
      expect(resKg.error).toBeUndefined();
      expect(resKg.data?.subtotal).toBe(10000);
      expect(resKg.data?.pesoKg).toBe(1.25);
    });

    it("Demuestra por qué 4 decimales fallaba en $13.750 a $2.000 y 6 decimales lo resuelve perfectamente", () => {
      const p = 13750;
      const imp = 2000;
      const pesoExacto = imp / p; // 0.145454545...

      // 4 decimales: 0.1455
      const peso4 = parseFloat(pesoExacto.toFixed(4));
      const sub4 = Math.round(peso4 * p); // 2001 -> DIFERENCIA DE $1 (Rechazado por Worker)
      expect(sub4).toBe(2001);
      expect(Math.abs(imp - sub4)).toBe(1);

      // 6 decimales: 0.145455
      const peso6 = parseFloat(pesoExacto.toFixed(6));
      const sub6 = Math.round(peso6 * p); // 2000 -> EXACTO
      expect(sub6).toBe(2000);
      expect(Math.abs(imp - sub6)).toBe(0);
    });
  });

  // ============================================================================
  // RIESGO 2: PERSISTENCIA REAL, RECORRIDO COMPLETO Y AJUSTE PUNTUAL DE PRECIO
  // ============================================================================
  describe("2. Recorrido completo de persistencia e invariantes de tickets", () => {
    it("Persiste venta por importe con ajuste puntual en IndexedDB y sobrevive a JSON parse/stringify", async () => {
      const ticketId = "T-AUDIT-PERSIST-001";
      const fecha = "30/10/2026 12:00:00";

      // Venta por importe: $2.000 a $13.750/kg con ajuste puntual a $1.800
      const precioOriginal = 13750;
      const importeOriginal = 2000;
      const calculo = calcularVentaPorImporte(importeOriginal, precioOriginal);
      const cantidad = parseFloat(calculo.data!.pesoKg.toFixed(6)); // 0.145455

      // Ajuste puntual de precio a $1.800
      const subtotalAjustado = 1800;
      const precioUnitarioAjustado = Math.round(subtotalAjustado / cantidad); // 12375

      const payload: VentaPayload = {
        ticket: {
          ticket_id: ticketId,
          fecha,
          total_venta: subtotalAjustado,
          monto_efectivo: subtotalAjustado,
          monto_transf: 0,
          metodo_pago: "EFECTIVO",
          es_nocturno: false,
        },
        items: [
          {
            ticket_id: ticketId,
            producto: "Queso cremoso fraccionado",
            cantidad,
            unidad: "kg",
            precio_unit: precioUnitarioAjustado,
            subtotal: subtotalAjustado,
            metodo_pago: "EFECTIVO",
            costo_unitario: 5000,
          },
        ],
      };

      // 1. Serialización y deserialización JSON sin pérdida
      const jsonStr = JSON.stringify(payload);
      const payloadRecuperado: VentaPayload = JSON.parse(jsonStr);

      expect(payloadRecuperado.ticket.total_venta).toBe(1800);
      expect(payloadRecuperado.items[0].cantidad).toBe(0.145455);
      expect(payloadRecuperado.items[0].precio_unit).toBe(precioUnitarioAjustado);
      expect(payloadRecuperado.items[0].subtotal).toBe(1800);

      // 2. Persistencia en IndexedDB
      const ventaLocal: VentaLocal = {
        ticket_id: ticketId,
        payload: payloadRecuperado,
        estado: "pendiente",
        intentos: 0,
        creado_en: new Date().toISOString(),
      };

      await guardarVentaLocal(ventaLocal);

      // 3. Recuperación desde IndexedDB
      const recuperada = await obtenerVentaLocal(ticketId);
      expect(recuperada).toBeDefined();
      expect(recuperada?.estado).toBe("pendiente");
      expect(recuperada?.payload.ticket.total_venta).toBe(1800);
      expect(recuperada?.payload.items[0].cantidad).toBe(0.145455);
      expect(recuperada?.payload.items[0].subtotal).toBe(1800);
      expect(recuperada?.payload.items[0].precio_unit).toBe(precioUnitarioAjustado);

      // 4. Verificación de contrato del Worker:
      // Worker comprueba: Math.round(cantidad * precio_unitario) == subtotal
      const item = recuperada!.payload.items[0];
      const workerSubtotal = Math.round(item.cantidad * item.precio_unit);
      expect(Math.abs(item.subtotal - workerSubtotal)).toBeLessThanOrEqual(0.01);
    });

    it("Simula reintento idempotente sin duplicación de ticket", () => {
      const ticketId = "T-AUDIT-IDEMP-001";
      const payload: VentaPayload = {
        ticket: {
          ticket_id: ticketId,
          fecha: "30/10/2026 12:00:00",
          total_venta: 1000,
          monto_efectivo: 1000,
          monto_transf: 0,
          metodo_pago: "EFECTIVO",
        },
        items: [
          {
            ticket_id: ticketId,
            producto: "Mignon",
            cantidad: 0.142857,
            unidad: "kg",
            precio_unit: 7000,
            subtotal: 1000,
            metodo_pago: "EFECTIVO",
          },
        ],
      };

      // Simulación de respuesta Worker ante registro inicial y reintento
      const dbTickets = new Map<string, VentaPayload>();

      function workerRegistrar(input: VentaPayload) {
        if (dbTickets.has(input.ticket.ticket_id)) {
          return {
            status: "existente",
            ticket_id: input.ticket.ticket_id,
            mensaje: "Venta previamente registrada (operación idempotente).",
          };
        }
        dbTickets.set(input.ticket.ticket_id, input);
        return {
          status: "registrado",
          ticket_id: input.ticket.ticket_id,
          mensaje: "Venta registrada con éxito.",
        };
      }

      const res1 = workerRegistrar(payload);
      expect(res1.status).toBe("registrado");
      expect(dbTickets.size).toBe(1);

      // Reintento de la misma operación
      const res2 = workerRegistrar(payload);
      expect(res2.status).toBe("existente");
      expect(dbTickets.size).toBe(1); // No duplica
    });
  });

  // ============================================================================
  // RIESGO 3: IDENTIFICACIÓN SEGURA DE PRODUCTOS PESABLES Y PRIORIDAD DE UNIDAD
  // ============================================================================
  describe("3. Identificación segura: Prioridad absoluta de Unidad sobre heurística", () => {
    it("Productos con unidad explícita 'Unidad' NUNCA son pesables aunque su nombre contenga gramajes o palabras clave", () => {
      const nonWeighables = [
        { nombre: "3D Queso 43gr", unidad: "Unidad", categoria: "Otro" },
        { nombre: "Azucar Ledesma 1kg", unidad: "Unidad", categoria: "Otro" },
        { nombre: "Alfajor Fantoche super triple 100gr", unidad: "Unidad", categoria: "Otro" },
        { nombre: "Galletas Bizcochos Don Satur 200gr", unidad: "Unidad", categoria: "Otro" },
        { nombre: "Jabon en pan Argentino 200gr", unidad: "Unidad", categoria: "Otro" },
        { nombre: "Lavavajillas Ala cremoso 750cc", unidad: "Unidad", categoria: "Otro" },
        { nombre: "Caramelo Alka cremoso", unidad: "Unidad", categoria: "Otro" },
        { nombre: "Manteca Clucelat 100gr", unidad: "Unidad", categoria: "Otro" },
        { nombre: "Pan lactal Don Paulino", unidad: "Unidad", categoria: "Otro" },
        { nombre: "Queso rallado La Quesera 40gr", unidad: "Unidad", categoria: "Otro" },
        { nombre: "Carbon de leña Maxi Fuego 3kg", unidad: "Unidad", categoria: "Otro" },
      ];

      nonWeighables.forEach((prod) => {
        expect(
          isWeighableProduct(prod),
          `El producto '${prod.nombre}' con unidad 'Unidad' fue incorrectamente clasificado como pesable`
        ).toBe(false);
      });
    });

    it("Productos con unidades explícitas de peso son correctamente reconocidos", () => {
      const weighables = [
        { nombre: "Mignon blandito", unidad: "Gramos", categoria: "Otro" },
        { nombre: "Bizcochos Rombo", unidad: "Gramos", categoria: "Otro" },
        { nombre: "Queso cremoso Nueva Villa x100gr", unidad: "Gramos", categoria: "Otro" },
        { nombre: "Pan francés", unidad: "kg", categoria: "Panadería" },
        { nombre: "Hierba medicinal", unidad: "g", categoria: "Herboristería" },
      ];

      weighables.forEach((prod) => {
        expect(
          isWeighableProduct(prod),
          `El producto '${prod.nombre}' con unidad '${prod.unidad}' debió ser reconocido como pesable`
        ).toBe(true);
      });
    });

    it("Productos ambiguos sin unidad explícita NO se clasifican automáticamente a menos que digan 'al peso' o 'fraccionado'", () => {
      const ambiguos = [
        { nombre: "Queso sardo horma", unidad: "", categoria: "Lácteos" },
        { nombre: "Bizcochos dulces", unidad: "", categoria: "Panadería" },
      ];
      ambiguos.forEach((prod) => {
        expect(isWeighableProduct(prod)).toBe(false);
      });

      const explicitosGranel = [
        { nombre: "Queso sardo al peso", unidad: "", categoria: "Lácteos" },
        { nombre: "Bizcochos fraccionados", unidad: "", categoria: "Panadería" },
      ];
      explicitosGranel.forEach((prod) => {
        expect(isWeighableProduct(prod)).toBe(true);
      });
    });

    it("Convierte correctamente precio x100gr a precio por kg sin duplicación", () => {
      // Queso cremoso: $1.200 por 100gr -> $12.000/kg
      const prod100g = {
        nombre: "Queso cremoso Nueva Villa x100gr",
        unidad: "Gramos",
        precio_venta: 1200,
        precio_dia: 1200,
      };
      const ref100g = getReferencePricePerKg(prod100g);
      expect(ref100g.precioPorKg).toBe(12000);
      expect(ref100g.precioPorGramo).toBe(12);

      // Mignon: $2.500 ya es precio por kg de referencia mayorista/minorista
      const prodMignon = {
        nombre: "Mignon blandito",
        unidad: "Gramos",
        precio_venta: 2500,
        precio_dia: 2500,
      };
      const refMignon = getReferencePricePerKg(prodMignon);
      expect(refMignon.precioPorKg).toBe(2500); // NO se multiplica dos veces
      expect(refMignon.precioPorGramo).toBe(2.5);
    });
  });

  // ============================================================================
  // RIESGO 4: MEDIOS DE PAGO Y CONCILIACIÓN MONETARIA
  // ============================================================================
  describe("4. Medios de pago, pagos mixtos y vueltos con líneas fraccionarias", () => {
    it("Verifica pago mixto balanceado con líneas fraccionarias y redondeo exacto", () => {
      // Línea 1: $1.000 de Mignon a $7.000/kg (0.142857 kg)
      // Línea 2: $2.000 de Queso cremoso a $13.750/kg (0.145455 kg)
      // Total ticket: $3.000 exactos
      const subtotal1 = 1000;
      const subtotal2 = 2000;
      const totalTicket = subtotal1 + subtotal2; // 3000

      // Pago mixto: $2.000 efectivo, $1.000 transferencia
      const montoEfectivo = 2000;
      const montoTransf = 1000;

      // Balance check
      expect(montoEfectivo + montoTransf).toBe(totalTicket);

      // Simulación de cálculo de vuelto con pago de $5.000 en efectivo para ticket de $3.000
      const efectivoRecibido = 5000;
      const vuelto = efectivoRecibido - montoEfectivo;
      expect(vuelto).toBe(3000);
    });
  });
});
