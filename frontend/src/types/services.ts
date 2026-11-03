export type MetodoPagoServicio = "EFECTIVO" | "TRANSFERENCIA" | "MIXTO";

export interface CargaServicio {
  id: number;
  fecha: string;
  fecha_hora_utc?: string;
  servicio: string;
  plataforma_origen?: string | null;
  servicio_destino?: string | null;
  monto_carga: number;
  monto_adicional: number;
  total_cobrado: number;
  pago_efvo: number;
  pago_transf: number;
  anulado: boolean;
  idempotency_key?: string;
}

export interface CargaServicioInput {
  servicio?: string;
  plataforma_origen?: string | null;
  servicio_destino?: string | null;
  monto_carga: number;
  monto_adicional: number;
  total_cobrado: number;
  pago_efvo: number;
  pago_transf: number;
  fecha?: string;
  idempotency_key?: string;
}

export interface ResumenServicios {
  total_operaciones: number;
  total_cobrado: number;
  total_capital: number;
  total_adicional: number;
  total_efectivo: number;
  total_transferencia: number;
  capital_prepay: number;
  capital_claro: number;
  capital_sube_mp: number;
  operaciones_anuladas: number;
}
