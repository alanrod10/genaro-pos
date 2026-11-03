/**
 * Tipos TypeScript para el módulo HISTÓRICO de GenaroPOS.
 * Datos de solo lectura de la auditoría 2025-2026.
 */

export interface HistoricalSummary {
  total_ventas_pesos: number;
  total_tickets: number;
  ticket_promedio: number;
  total_ventas_efectivo: number;
  total_ventas_transferencia: number;
  fecha_inicio: string;
  fecha_fin: string;
  periodo_meses: number;
  total_items_filas: number;
  total_items_subtotal: number;
  total_cargas_cobrado: number;
  total_cargas_capital: number;
  total_cargas_adicional: number;
  total_operaciones_cargas: number;
  total_cargas_efectivo: number;
  total_cargas_transferencia: number;
  advertencia_pagos: string;
  advertencia_items: string;
}

export interface HistoricalTicketResumen {
  id: string;
  fecha_hora_local: string;
  total_venta: number;
  monto_efectivo: number;
  monto_transferencia: number;
  metodo_pago: string;
  tiene_items: number;
  balance_pago_diferencia: number;
}

export interface HistoricalSeriePunto {
  periodo: string;
  tickets: number;
  ventas_total: number;
  monto_efectivo: number;
  monto_transferencia: number;
}

export interface HistoricalSales {
  desde?: string | null;
  hasta?: string | null;
  metodo_pago?: string | null;
  total_ventas: number;
  cantidad_tickets: number;
  ticket_promedio: number;
  total_efectivo: number;
  total_transferencia: number;
  serie_temporal: HistoricalSeriePunto[];
  tickets: HistoricalTicketResumen[];
  total_registros: number;
  limit: number;
  offset: number;
}

export interface HistoricalProductItem {
  posicion: number;
  producto_nombre: string;
  operaciones: number;
  unidades_cuantificables: number;
  ventas_pesables_monto: number;
  total_recaudado: number;
}

export interface HistoricalProducts {
  desde?: string | null;
  hasta?: string | null;
  modo_depurado: boolean;
  total_productos_distintos: number;
  productos: HistoricalProductItem[];
  total_registros: number;
  limit: number;
  offset: number;
  advertencia_pesables: string;
}

export interface HistoricalServiceBreakdown {
  servicio: string;
  total_operaciones: number;
  capital_recargado: number;
  adicional_ganancia: number;
  total_cobrado: number;
  pago_efectivo: number;
  pago_transferencia: number;
}

export interface HistoricalServiceSerie {
  periodo: string;
  operaciones: number;
  total_cobrado: number;
  adicional_ganancia: number;
}

export interface HistoricalServices {
  desde?: string | null;
  hasta?: string | null;
  excluir_duplicados: boolean;
  total_operaciones: number;
  capital_recargado: number;
  adicional_ganancia: number;
  total_cobrado: number;
  pago_efectivo: number;
  pago_transferencia: number;
  servicios: HistoricalServiceBreakdown[];
  serie_temporal: HistoricalServiceSerie[];
  advertencia_duplicados: string;
}

export interface HistoricalItemDetalle {
  id: number;
  ticket_id: string;
  producto_nombre: string;
  cantidad: number | null;
  unidad: string;
  precio_unitario: number;
  subtotal: number;
  metodo_pago: string;
  calculo_subtotal_coincide: number;
  es_duplicado_deriva: number;
  ticket_id_correlacionado: string | null;
}

export interface HistoricalTicketDetalle {
  id: string;
  fecha_hora_local: string;
  fecha_hora_utc: string;
  total_venta: number;
  monto_efectivo: number;
  monto_transferencia: number;
  metodo_pago: string;
  es_nocturno: number;
  balance_pago_diferencia: number;
  tiene_items: number;
  origen_fila_excel: number;
  items: HistoricalItemDetalle[];
  alertas: string[];
}

export interface HistoricalTicketsSearch {
  total_encontrados: number;
  tickets: HistoricalTicketResumen[];
  limit: number;
  offset: number;
}

export interface HistoricalPeriodoMetricas {
  desde: string;
  hasta: string;
  facturacion_ventas: number;
  cantidad_tickets: number;
  ticket_promedio: number;
  cargas_cobrado: number;
  cargas_adicional: number;
  operaciones_cargas: number;
  top_productos: Array<{ nombre: string; subtotal: number }>;
}

export interface HistoricalCompare {
  periodo_a: HistoricalPeriodoMetricas;
  periodo_b: HistoricalPeriodoMetricas;
  variaciones: {
    facturacion_ventas_pct: number;
    cantidad_tickets_pct: number;
    ticket_promedio_pct: number;
    cargas_cobrado_pct: number;
  };
}
