/**
 * Tipos de datos para el Dashboard Financiero (Visor y Reportes).
 */

import { CierreDiario } from "./expenses";

export type PeriodoTipo = "hoy" | "ultimos_7_dias" | "ultimos_30_dias" | "personalizado";

export interface PuntoSerieTemporal {
  fecha: string;
  etiqueta: string;
  ventas_total: number;
  servicios_total: number;
  gastos_total: number;
  adicionales_ganancia: number;
  banco_neto: number;
}

export interface MetricasCajaA {
  total_ventas: number;
  ventas_efvo: number;
  ventas_transf: number;
  efectivo_disponible_drugstore: number;
  ganancia_estimada_ventas: number;
  es_estimacion_heredada: boolean;
  cantidad_tickets: number;

  // Ganancia bruta real calculada con costo histórico congelado
  ganancia_bruta_real: number | null;
  costo_historico_total: number;
  ventas_con_costo_completo: number;
  ventas_con_costo_incompleto: number;
  items_con_costo_desconocido: number;
  margen_bruto_porcentaje: number | null;
  tiene_costos_completos: boolean;
}

export interface MetricasCajaB {
  capital_total: number;
  capital_efvo: number;
  capital_transf: number;
  cantidad_operaciones: number;
}

export interface MetricasCajaC {
  ganancia_adicional_total: number;
  adicional_efvo: number;
  adicional_transf: number;
  cantidad_operaciones: number;
}

export interface MetricasCajaD {
  ventas_transf: number;
  recargas_transf: number;
  sube_mp_capital: number;
  gastos_transf: number;
  total_banco_neto: number;
}

export interface MetricasCajaE {
  capital_total: number;
  capital_efvo: number;
  capital_transf: number;
  cantidad_operaciones: number;
}

export interface MetricasGastos {
  gastos_total: number;
  gastos_efvo: number;
  gastos_transf: number;
  cantidad_gastos: number;
}

export interface ConciliacionCajon {
  ventas_efvo: number;
  servicios_efvo: number;
  gastos_efvo: number;
  d_recargas: number;
  efectivo_esperado_cajon: number;
}

export interface DesgloseServicioItem {
  plataforma_origen: string;
  servicio_destino: string;
  categoria: string;
  cantidad_operaciones: number;
  capital_total: number;
  adicional_total: number;
  total_cobrado: number;
  cobrado_efectivo: number;
  cobrado_transferencia: number;
  medio_pago?: string;
  fondo_receptor?: string;
  cuenta_utilizada?: string;
}

export interface MetricasDashboard {
  periodo_tipo: PeriodoTipo;
  desde: string;
  hasta: string;
  desglose_servicios?: DesgloseServicioItem[];

  // 1. Caja A - Drugstore (Ventas)
  caja_a: MetricasCajaA;

  // 2. Caja B - SUBE / Prepay (Capital de servicios)
  caja_b: MetricasCajaB;

  // 3. Caja C - Adicionales (Ganancia directa de servicios)
  caja_c: MetricasCajaC;

  // 4. Caja D - Banco / Transferencias conciliadas
  caja_d: MetricasCajaD;

  // 5. Caja E - Claro (Capital telefonía)
  caja_e: MetricasCajaE;

  // 6. Gastos y Retiros
  gastos: MetricasGastos;

  // 7. Conciliación física de cajón (Arqueo)
  conciliacion_cajon: ConciliacionCajon;

  // 8. Evolución temporal diaria (para gráficos)
  serie_temporal: PuntoSerieTemporal[];

  // 9. Cierres en el período
  cierres: CierreDiario[];

  // Metadatos de integridad de esquema
  esquema_completo?: boolean;
  tablas_faltantes?: string[];
  advertencia_esquema?: string | null;
}
