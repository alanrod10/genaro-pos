export const CATEGORIAS_CANONICAS = [
  "Golosinas y Chocolates",
  "Galletitas y Alfajores",
  "Snacks y Salados",
  "Bebidas e Infusiones",
  "Bebidas con Alcohol",
  "Cigarrillos y Tabaquería",
  "Panadería y Fiambrería",
  "Almacén y Lácteos",
  "Librería y Regalería",
  "Higiene y Cuidado Personal",
  "Otros",
] as const;

export type CategoriaCanonica = (typeof CATEGORIAS_CANONICAS)[number];

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

export interface ProductoDeleteResponse {
  id_producto: number;
  nombre: string;
  status: string;
  accion: "borrado_fisico" | "baja_logica" | string;
  eliminado_fisico: boolean;
  activo: boolean;
  tiene_ventas: boolean;
  mensaje: string;
}

export interface CatalogoResponse {
  total: number;
  productos: Producto[];
}

