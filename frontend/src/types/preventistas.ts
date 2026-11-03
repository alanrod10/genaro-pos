/**
 * Tipos de datos para el módulo de Preventistas y Pedidos a Proveedores.
 */

export interface ProductoEdicionPreventista {
  id_producto: number;
  nombre: string;
  categoria: string;
  proveedor: string;
  unidad: string;
  costo: number;
  precio_venta: number;
  margen_porcentaje: number;
  costo_editado?: number;
  precio_editado?: number;
  margen_editado?: number;
  editado?: boolean;
}

export interface CambioPrecioLoteItem {
  id_producto: number;
  costo: number;
  precio_venta: number;
}

export interface PedidoItem {
  id?: number;
  producto_id?: number;
  producto_nombre: string;
  cantidad: number;
  costo_unitario: number;
  subtotal: number;
}

export interface PedidoProveedor {
  id: string;
  fecha: string;
  proveedor: string;
  estado: "PENDIENTE" | "RECIBIDO" | "CANCELADO";
  total: number;
  observaciones?: string;
  creado_en_utc?: string;
  actualizado_en_utc?: string;
  items_count?: number;
}

export interface PedidoProveedorDetalle extends PedidoProveedor {
  items: PedidoItem[];
}

export interface PedidoAuditoria {
  id: number;
  pedido_id: string;
  accion: "CREAR_PEDIDO" | "CAMBIAR_ESTADO" | "CANCELAR_PEDIDO" | "EDITAR_PEDIDO";
  valores_anteriores?: string;
  valores_nuevos: string;
  fecha_utc: string;
  actor: string;
}
