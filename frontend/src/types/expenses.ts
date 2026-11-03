export type MetodoPagoGasto = "EFECTIVO" | "TRANSFERENCIA";

export interface Gasto {
  id: number;
  fecha: string;
  tipo: string;
  detalle: string;
  monto_salida: number;
  origen_fondos: string;
  metodo_pago: MetodoPagoGasto;
  anulado: boolean;
  idempotency_key?: string;
}

export interface GastoInput {
  detalle: string;
  monto_salida: number;
  tipo?: string;
  origen_fondos?: string;
  metodo_pago?: MetodoPagoGasto;
  fecha?: string;
  idempotency_key?: string;
}

export interface ResumenGastos {
  total_operaciones: number;
  gastos_efvo: number;
  gastos_transf: number;
  gastos_total: number;
  operaciones_anuladas: number;
}

export interface ArqueoDiario {
  fecha: string;
  ventas_efvo: number;
  ventas_transf: number;
  total_ventas: number;
  ganancia_estimada_ventas: number;
  servicios_efvo: number;
  servicios_transf: number;
  total_servicios: number;
  capital_prepay: number;
  capital_claro: number;
  capital_sube_mp: number;
  adicionales_ganancia: number;
  d_recargas: number;
  gastos_efvo: number;
  gastos_transf: number;
  gastos_total: number;
  efectivo_disponible_drugstore: number;
  efectivo_esperado: number;
  total_banco: number;
}

export interface CierreDiario {
  id?: number;
  fecha: string;
  efectivo_esperado: number;
  efectivo_contado: number;
  diferencia: number;
  total_ventas_efvo: number;
  total_ventas_transf: number;
  total_servicios_efvo: number;
  total_servicios_transf: number;
  total_gastos_efvo: number;
  total_gastos_transf: number;
  total_banco: number;
  observaciones?: string;
  cerrado_por?: string;
  cerrado_en_utc?: string;
}
