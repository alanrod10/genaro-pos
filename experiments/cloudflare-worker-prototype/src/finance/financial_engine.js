/**
 * Motor de Reglas Financieras y Auditoría de GenaroPOS en TypeScript/JS.
 *
 * Preserva exactamente las 10 reglas financieras auditadas del proyecto:
 * 1. Segregación Cajas A-E.
 * 2. Arqueo físico de cajón sin transferencias bancarias.
 * 3. Comisiones escalonadas de servicios: Math.ceil(monto / 2000) * 100.
 * 4. Segregación estricta de capital PREPAY, Claro y SUBE MP.
 * 5. Congelamiento inmutable de costos históricos.
 * 6. Exclusión de operaciones anuladas lógicamente de los balances operativos.
 */

export function calcularCargoServicio(montoCapital, tipoServicio = "PREPAY") {
  if (montoCapital <= 0) return 0;
  const normalizado = (tipoServicio || "").trim().toUpperCase();

  // Regla auditada de comisión GenaroPOS:
  // Escalonado por tramos de 2000 pesos: 100 pesos por cada 2000 o fracción
  // 1.000 -> 100
  // 2.000 -> 100
  // 3.000 -> 200
  // 4.000 -> 200
  // 5.000 -> 300
  return Math.ceil(montoCapital / 2000) * 100;
}

export function clasificarServicio(tipoServicio) {
  const t = (tipoServicio || "").trim().toUpperCase();
  if (t.includes("CLARO")) {
    return { categoria: "CLARO", cajaCapital: "CAJA_E" };
  }
  if (t.includes("SUBE")) {
    return { categoria: "SUBE_MP", cajaCapital: "MERCADO_PAGO" };
  }
  // Por defecto recargas PREPAY (Personal, Movistar, Tuenti, etc.)
  return { categoria: "PREPAY", cajaCapital: "CAJA_B" };
}

/**
 * Calcula el balance contable y arqueo de caja a partir de los registros de la base.
 * Excluye rigurosamente las filas con anulado == 1.
 */
export function calcularBalanceYArqueo({ ventas = [], servicios = [], gastos = [] }) {
  // 1. Filtrar filas activas (excluir anuladas)
  const ventasActivas = ventas.filter((v) => !v.anulado);
  const serviciosActivos = servicios.filter((s) => !s.anulado);
  const gastosActivos = gastos.filter((g) => !g.anulado);

  // 2. Acumuladores de Cajas
  let cajaA_VentasEfectivo = 0;
  let cajaB_CapitalPrepay = 0;
  let cajaC_BeneficioServicios = 0;
  let cajaD_TransferenciasBancarias = 0;
  let cajaE_CapitalClaro = 0;

  let serviciosEfectivo = 0;
  let gastosEfectivo = 0;

  // Procesar Ventas
  for (const v of ventasActivas) {
    cajaA_VentasEfectivo += Number(v.monto_efectivo || 0);
    cajaD_TransferenciasBancarias += Number(v.monto_transferencia || 0);
  }

  // Procesar Servicios
  for (const s of serviciosActivos) {
    const clasif = clasificarServicio(s.tipo_servicio);
    const capital = Number(s.monto_capital || 0);
    const cargo = Number(s.cargo_comision || 0);
    const total = Number(s.monto_total || (capital + cargo));

    // Segregación de capitales
    if (clasif.cajaCapital === "CAJA_B") {
      cajaB_CapitalPrepay += capital;
    } else if (clasif.cajaCapital === "CAJA_E") {
      cajaE_CapitalClaro += capital;
    }
    // SUBE MP no consume capital PREPAY ni Claro

    // Cargos / ganancia por comisiones va íntegramente a Caja C
    cajaC_BeneficioServicios += cargo;

    // Si el cliente pagó en efectivo:
    if ((s.metodo_pago || "").toUpperCase() === "EFECTIVO") {
      serviciosEfectivo += total;
    } else {
      cajaD_TransferenciasBancarias += total;
    }
  }

  // Procesar Gastos / Retiros
  for (const g of gastosActivos) {
    if ((g.metodo_pago || "").toUpperCase() === "EFECTIVO") {
      gastosEfectivo += Number(g.monto || 0);
    }
  }

  // Arqueo físico de cajón:
  // Efectivo Esperado = Ventas Efectivo + Servicios Efectivo - Gastos Efectivo
  const efectivoFisicoEsperado = cajaA_VentasEfectivo + serviciosEfectivo - gastosEfectivo;

  return {
    cajas: {
      cajaA_VentasEfectivo,
      cajaB_CapitalPrepay,
      cajaC_BeneficioServicios,
      cajaD_TransferenciasBancarias,
      cajaE_CapitalClaro,
    },
    arqueoCajon: {
      ventasEfectivo: cajaA_VentasEfectivo,
      serviciosEfectivo,
      gastosEfectivo,
      efectivoFisicoEsperado,
    },
    totales: {
      ventasActivasCount: ventasActivas.length,
      ventasAnuladasCount: ventas.length - ventasActivas.length,
      serviciosActivosCount: serviciosActivos.length,
      serviciosAnuladosCount: servicios.length - serviciosActivos.length,
    },
  };
}
