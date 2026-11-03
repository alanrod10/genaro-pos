export interface Producto {
  id_producto: number;
  nombre: string;
  categoria: string;
  proveedor: string;
  unidad: string;
  costo: number;
  margen_porcentaje: number;
  precio_venta: number;
  precio_dia?: number;
  precio_noche?: number;
  codigo_barras?: string;
  activo: boolean;
  fecha_act: string;
}

export interface ProductoInput {
  nombre: string;
  categoria: string;
  proveedor: string;
  unidad: string;
  costo: number;
  precio_venta: number;
  activo?: boolean;
}

export interface CatalogoResponse {
  total: number;
  productos: Producto[];
}
