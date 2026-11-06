/**
 * Suite de Pruebas: Paridad con las 10 Reglas Financieras de GenaroPOS
 */

import test from "node:test";
import assert from "node:assert/strict";
import {
  calcularCargoServicio,
  clasificarServicio,
  calcularBalanceYArqueo,
} from "../src/finance/financial_engine.js";

test("Reglas Financieras: Escalonamiento exacto de comisiones de servicios", () => {
  // Comisiones auditadas: Math.ceil(monto / 2000) * 100
  assert.equal(calcularCargoServicio(500), 100);
  assert.equal(calcularCargoServicio(1000), 100);
  assert.equal(calcularCargoServicio(2000), 100);
  assert.equal(calcularCargoServicio(2001), 200);
  assert.equal(calcularCargoServicio(3000), 200);
  assert.equal(calcularCargoServicio(4000), 200);
  assert.equal(calcularCargoServicio(4500), 300);
  assert.equal(calcularCargoServicio(5000), 300);
  assert.equal(calcularCargoServicio(5300), 300);
  assert.equal(calcularCargoServicio(6000), 300);
  assert.equal(calcularCargoServicio(6001), 400);
  assert.equal(calcularCargoServicio(0), 0);
});

test("Reglas Financieras: Segregación de capitales PREPAY, Claro y SUBE MP", () => {
  // 1. Recargas genéricas o Personal/Movistar -> PREPAY (Caja B)
  const prepay = clasificarServicio("PREPAY_PERSONAL");
  assert.equal(prepay.categoria, "PREPAY");
  assert.equal(prepay.cajaCapital, "CAJA_B");

  // 2. Recargas Claro -> CLARO (Caja E)
  const claro = clasificarServicio("RECARGA_CLARO");
  assert.equal(claro.categoria, "CLARO");
  assert.equal(claro.cajaCapital, "CAJA_E");

  // 3. SUBE Mercado Pago -> SUBE_MP (Saldo comercial MP, no PREPAY)
  const sube = clasificarServicio("SUBE_MERCADOPAGO");
  assert.equal(sube.categoria, "SUBE_MP");
  assert.equal(sube.cajaCapital, "MERCADO_PAGO");

  // 4. SUBE estándar (sin MP ni plataforma Mercado Pago) -> PREPAY (Caja B)
  const subePrepay = clasificarServicio("SUBE");
  assert.equal(subePrepay.categoria, "PREPAY");
  assert.equal(subePrepay.cajaCapital, "CAJA_B");

  // 5. SUBE con plataforma MERCADO PAGO -> SUBE_MP
  const subePlatMp = clasificarServicio("SUBE", "MERCADO PAGO");
  assert.equal(subePlatMp.categoria, "SUBE_MP");
  assert.equal(subePlatMp.cajaCapital, "MERCADO_PAGO");
});

test("Reglas Financieras: Balances de Cajas A-E y Arqueo Físico de Cajón", () => {
  const ventas = [
    { id: "v1", monto_efectivo: 5000, monto_transferencia: 0, total_venta: 5000, anulado: 0 },
    { id: "v2", monto_efectivo: 0, monto_transferencia: 3000, total_venta: 3000, anulado: 0 },
    { id: "v3_anulada", monto_efectivo: 2000, monto_transferencia: 0, total_venta: 2000, anulado: 1 }, // No debe computar
  ];

  const servicios = [
    // Servicio 1: PREPAY en efectivo ($2000 capital + $100 cargo = $2100 cobrado)
    {
      id: "s1",
      tipo_servicio: "PREPAY",
      monto_capital: 2000,
      cargo_comision: 100,
      monto_total: 2100,
      metodo_pago: "EFECTIVO",
      anulado: 0,
    },
    // Servicio 2: Claro en efectivo ($1000 capital + $100 cargo = $1100 cobrado)
    {
      id: "s2",
      tipo_servicio: "CLARO",
      monto_capital: 1000,
      cargo_comision: 100,
      monto_total: 1100,
      metodo_pago: "EFECTIVO",
      anulado: 0,
    },
    // Servicio 3: PREPAY pagado por Transferencia ($3000 capital + $200 cargo = $3200)
    {
      id: "s3",
      tipo_servicio: "PREPAY",
      monto_capital: 3000,
      cargo_comision: 200,
      monto_total: 3200,
      metodo_pago: "TRANSFERENCIA",
      anulado: 0,
    },
    // Servicio 4: Anulado ($5000 capital + $300 cargo)
    {
      id: "s4_anulado",
      tipo_servicio: "PREPAY",
      monto_capital: 5000,
      cargo_comision: 300,
      monto_total: 5300,
      metodo_pago: "EFECTIVO",
      anulado: 1,
    },
  ];

  const gastos = [
    // Gasto 1: Pago a proveedor en efectivo
    { id: "g1", monto: 1500, metodo_pago: "EFECTIVO", anulado: 0 },
    // Gasto 2: Pago por transferencia
    { id: "g2", monto: 800, metodo_pago: "TRANSFERENCIA", anulado: 0 },
    // Gasto 3: Gasto anulado
    { id: "g3_anulado", monto: 4000, metodo_pago: "EFECTIVO", anulado: 1 },
  ];

  const res = calcularBalanceYArqueo({ ventas, servicios, gastos });

  // 1. Verificación de Cajas Segregadas:
  // Caja A: Ventas Efectivo = 5000
  assert.equal(res.cajas.cajaA_VentasEfectivo, 5000);

  // Caja B: Capital PREPAY activo = s1 ($2000) + s3 ($3000) = 5000 (s4 excluida)
  assert.equal(res.cajas.cajaB_CapitalPrepay, 5000);

  // Caja C: Beneficio por comisiones activo = s1 (100) + s2 (100) + s3 (200) = 400 (s4 excluida)
  assert.equal(res.cajas.cajaC_BeneficioServicios, 400);

  // Caja D: Saldo Transferencias = v2 (3000) + s3 (3200) = 6200
  assert.equal(res.cajas.cajaD_TransferenciasBancarias, 6200);

  // Caja E: Capital Claro = s2 ($1000) = 1000
  assert.equal(res.cajas.cajaE_CapitalClaro, 1000);

  // 2. Verificación de Arqueo Físico de Cajón:
  // Ventas Efectivo: 5000
  // Servicios Efectivo: s1 (2100) + s2 (1100) = 3200
  // Gastos Efectivo: g1 (1500)
  // Efectivo Físico Esperado = 5000 + 3200 - 1500 = 6700
  assert.equal(res.arqueoCajon.ventasEfectivo, 5000);
  assert.equal(res.arqueoCajon.serviciosEfectivo, 3200);
  assert.equal(res.arqueoCajon.gastosEfectivo, 1500);
  assert.equal(res.arqueoCajon.efectivoFisicoEsperado, 6700);

  // Comprobar conteos de exclusión
  assert.equal(res.totales.ventasActivasCount, 2);
  assert.equal(res.totales.ventasAnuladasCount, 1);
  assert.equal(res.totales.serviciosActivosCount, 3);
  assert.equal(res.totales.serviciosAnuladosCount, 1);
});
