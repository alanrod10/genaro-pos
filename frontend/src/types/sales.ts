export type MetodoPago = "EFECTIVO" | "TRANSFERENCIA" | "MIXTO";

export interface ItemVentaInput {
  id?: number;
  ticket_id?: string;
  producto: string;
  producto_nombre?: string;
  cantidad: number;
  unidad: string;
  precio_unit: number;
  precio_unitario?: number;
  subtotal: number;
  metodo_pago?: string;
  costo_unitario?: number | null;
}

export interface TicketVentaInput {
  ticket_id: string;
  fecha: string;
  total_venta: number;
  monto_efectivo: number;
  monto_transf: number;
  metodo_pago: MetodoPago;
  es_nocturno?: boolean;
  anulado?: boolean;
  motivo_anulacion?: string;
}

export interface VentaPayload {
  ticket: TicketVentaInput;
  items: ItemVentaInput[];
}

export interface VentaResponse {
  ticket_id: string;
  status: "registrado" | "existente";
  mensaje: string;
}

export type EstadoSincronizacion =
  | "pendiente"
  | "sincronizando"
  | "sincronizada"
  | "requiere_revision";

export interface VentaLocal {
  ticket_id: string;
  payload: VentaPayload;
  estado: EstadoSincronizacion;
  intentos: number;
  ultimo_error?: string;
  codigo_http?: number;
  creado_en: string;
  sincronizado_en?: string;
  anulado?: boolean;
  motivo_anulacion?: string;
  auditoria?: VentaAuditoria[];
}

export interface ItemCarrito {
  producto_id: number;
  nombre: string;
  unidad: string;
  precio: number;
  cantidad: number;
  subtotal: number;
  costo?: number;
}

export interface VentaAuditoria {
  id: number;
  ticket_id: string;
  item_id?: number;
  accion: string;
  motivo?: string;
  usuario: string;
  datos_previos_json?: string;
  datos_nuevos_json?: string;
  creado_en_utc: string;
}

export interface TicketResumen {
  ticket_id: string;
  fecha: string;
  fecha_hora_utc?: string;
  total_venta: number;
  monto_efectivo: number;
  monto_transferencia: number;
  monto_transf?: number;
  metodo_pago: MetodoPago;
  cantidad_items: number;
  anulado: boolean;
  motivo_anulacion?: string;
  resumen_items?: string;
}

export interface TicketDetalle {
  ticket_id: string;
  fecha: string;
  fecha_hora_utc?: string;
  total_venta: number;
  monto_efectivo: number;
  monto_transferencia: number;
  monto_transf?: number;
  metodo_pago: MetodoPago;
  anulado: boolean;
  motivo_anulacion?: string;
  items: ItemVentaInput[];
  auditoria?: VentaAuditoria[];
}

export interface VentasHistorialResponse {
  total: number;
  ventas: TicketResumen[];
}
