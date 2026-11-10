import {
  CatalogoResponse,
  Producto,
  ProductoDeleteResponse,
  ProductoInput,
} from "../types/catalog";
import {
  TicketDetalle,
  VentaPayload,
  VentaResponse,
  VentasHistorialResponse,
} from "../types/sales";
import { CargaServicio, CargaServicioInput, ResumenServicios } from "../types/services";
import {
  ArqueoDiario,
  CierreDiario,
  Gasto,
  GastoInput,
  ResumenGastos,
} from "../types/expenses";
import {
  PedidoItem,
  PedidoProveedor,
  PedidoProveedorDetalle,
} from "../types/preventistas";
import { PeriodoTipo, MetricasDashboard } from "../types/dashboard";
import { SystemReadiness } from "../types/system";
import {
  HistoricalSummary,
  HistoricalSales,
  HistoricalProducts,
  HistoricalServices,
  HistoricalTicketsSearch,
  HistoricalTicketDetalle,
  HistoricalCompare,
} from "../types/historical";
import {
  cambiarEstadoProductoDemo,
  crearProductoDemo,
  editarProductoDemo,
  eliminarProductoDemo,
  inicializarCatalogoDemoSiVacio,
  listarProductosDemo,
  inicializarServiciosDemoSiVacio,
  listarServiciosDemo,
  guardarCargaDemo,
  editarCargaDemo,
  anularCargaDemo,
  calcularResumenServiciosDemo,
  inicializarGastosDemoSiVacio,
  listarGastosDemo,
  guardarGastoDemo,
  editarGastoDemo,
  anularGastoDemo,
  calcularResumenGastosDemo,
  calcularArqueoDemo,
  guardarCierreDemo,
  obtenerCierreDemo,
  listarCierresDemo,
  inicializarVentasDemoSiVacio,
  listarVentasHistorialDemo,
  obtenerDetalleTicketDemo,
  anularVentaDemo,
  editarItemVentaDemo,
  listarProveedoresDemo,
  listarProductosPorProveedorDemo,
  guardarPreciosLoteDemo,
  listarPedidosDemo,
  obtenerDetallePedidoDemo,
  crearPedidoDemo,
  cambiarEstadoPedidoDemo,
  cancelarPedidoDemo,
  calcularMetricasDashboardDemo,
} from "./db";

export interface ApiResponse<T> {
  status: number;
  data?: T;
  error?: string;
}

export interface AuthUser {
  username: string;
  display_name: string;
  role?: string;
}

export interface LoginResult {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: AuthUser;
}

export type DemoSimulationMode = "exito_201" | "reintento_200" | "conflicto_409" | "timeout";

export interface IApiClient {
  // Autenticación y Sesión
  login(username: string, password: string): Promise<ApiResponse<LoginResult>>;
  obtenerUsuarioActual(): Promise<ApiResponse<AuthUser>>;
  setAuthToken?(token: string | null): void;
  getAuthToken?(): string | null;
  setOnUnauthorized?(callback: () => void): void;

  obtenerCatalogo(includeInactive?: boolean): Promise<ApiResponse<CatalogoResponse>>;
  crearProducto(datos: ProductoInput): Promise<ApiResponse<Producto>>;
  editarProducto(id: number, cambios: Partial<ProductoInput>): Promise<ApiResponse<Producto>>;
  cambiarEstadoProducto(id: number, activo: boolean): Promise<ApiResponse<Producto>>;
  eliminarProducto(id: number): Promise<ApiResponse<ProductoDeleteResponse>>;
  registrarVenta(payload: VentaPayload): Promise<ApiResponse<VentaResponse>>;
  // Servicios y Cargas
  obtenerCargas(
    fecha?: string,
    incluirAnuladas?: boolean,
    plataformaOrigen?: string,
    servicioDestino?: string
  ): Promise<ApiResponse<{ total: number; cargas: CargaServicio[] }>>;
  obtenerResumenCargas(fecha?: string): Promise<ApiResponse<ResumenServicios>>;
  registrarCarga(carga: CargaServicioInput): Promise<ApiResponse<CargaServicio>>;
  editarCarga(id: number, carga: CargaServicioInput): Promise<ApiResponse<CargaServicio>>;
  anularCarga(id: number): Promise<ApiResponse<{ status: string; id: number; mensaje: string }>>;
  // Gastos, Arqueo y Cierres
  obtenerGastos(fecha?: string, tipo?: string, incluirAnulados?: boolean): Promise<ApiResponse<{ total: number; gastos: Gasto[] }>>;
  obtenerResumenGastos(fecha?: string): Promise<ApiResponse<ResumenGastos>>;
  obtenerArqueoDiario(fecha?: string): Promise<ApiResponse<ArqueoDiario>>;
  registrarGasto(gasto: GastoInput): Promise<ApiResponse<Gasto>>;
  editarGasto(id: number, cambios: GastoInput): Promise<ApiResponse<Gasto>>;
  anularGasto(id: number, motivo?: string): Promise<ApiResponse<{ status: string; id: number; mensaje: string }>>;
  obtenerCierreDiario(fecha: string): Promise<ApiResponse<CierreDiario>>;
  registrarCierreDiario(cierre: CierreDiario): Promise<ApiResponse<CierreDiario>>;
  listarCierres(): Promise<ApiResponse<CierreDiario[]>>;
  // Historial de Ventas e Ítems
  obtenerHistorialVentas(
    fecha?: string,
    metodo_pago?: string,
    busqueda?: string,
    incluir_anuladas?: boolean
  ): Promise<ApiResponse<VentasHistorialResponse>>;
  obtenerDetalleVenta(ticket_id: string): Promise<ApiResponse<TicketDetalle>>;
  anularVenta(ticket_id: string, motivo: string, usuario?: string): Promise<ApiResponse<TicketDetalle>>;
  editarItemVenta(
    ticket_id: string,
    item_id: number,
    nueva_cantidad?: number,
    nuevo_subtotal?: number,
    usuario?: string
  ): Promise<ApiResponse<TicketDetalle>>;
  // Preventistas y Pedidos
  obtenerProveedores(): Promise<ApiResponse<string[]>>;
  obtenerProductosPorProveedor(proveedor: string): Promise<ApiResponse<Producto[]>>;
  actualizarPreciosPreventista(
    proveedor: string,
    cambios: Array<{ id_producto: number; costo: number; precio_venta: number }>
  ): Promise<ApiResponse<{ actualizados: number; mensaje: string }>>;
  altaRapidaProductoPreventista(datos: ProductoInput): Promise<ApiResponse<Producto>>;
  obtenerPedidos(filtros?: {
    proveedor?: string;
    fecha?: string;
    estado?: string;
  }): Promise<ApiResponse<PedidoProveedor[]>>;
  obtenerDetallePedido(id: string): Promise<ApiResponse<PedidoProveedorDetalle>>;
  crearPedido(datos: {
    proveedor: string;
    fecha?: string;
    observaciones?: string;
    items: PedidoItem[];
  }): Promise<ApiResponse<PedidoProveedorDetalle>>;
  cambiarEstadoPedido(
    id: string,
    nuevoEstado: "PENDIENTE" | "RECIBIDO" | "CANCELADO",
    observaciones?: string
  ): Promise<ApiResponse<PedidoProveedor>>;
  cancelarPedido(id: string, motivo: string): Promise<ApiResponse<PedidoProveedor>>;
  // Dashboard Financiero (Visor y Reportes)
  obtenerMetricasDashboard(
    desde?: string,
    hasta?: string,
    periodo?: PeriodoTipo
  ): Promise<ApiResponse<MetricasDashboard>>;
  verificarPreparacionSistema(): Promise<ApiResponse<SystemReadiness>>;
  isDemo(): boolean;

  // Módulo Histórico (Auditoría 2025-2026)
  obtenerResumenHistorico(): Promise<ApiResponse<HistoricalSummary>>;
  obtenerVentasHistoricas(params?: {
    desde?: string;
    hasta?: string;
    metodo_pago?: string;
    agrupacion?: string;
    limit?: number;
    offset?: number;
  }): Promise<ApiResponse<HistoricalSales>>;
  obtenerProductosHistoricos(params?: {
    desde?: string;
    hasta?: string;
    depurado?: boolean;
    limit?: number;
    offset?: number;
  }): Promise<ApiResponse<HistoricalProducts>>;
  obtenerServiciosHistoricos(params?: {
    desde?: string;
    hasta?: string;
    excluir_duplicados?: boolean;
  }): Promise<ApiResponse<HistoricalServices>>;
  buscarTicketsHistoricos(params?: {
    query?: string;
    fecha?: string;
    limit?: number;
    offset?: number;
  }): Promise<ApiResponse<HistoricalTicketsSearch>>;
  obtenerTicketDetalleHistorico(ticketId: string): Promise<ApiResponse<HistoricalTicketDetalle>>;
  compararPeriodosHistoricos(params: {
    desde_a: string;
    hasta_a: string;
    desde_b: string;
    hasta_b: string;
  }): Promise<ApiResponse<HistoricalCompare>>;
}

export const CATALOGO_DEMO: Producto[] = [
  {
    id_producto: 1,
    nombre: "Gaseosa Cola 500ml",
    categoria: "Bebidas",
    proveedor: "Distribuidora Central",
    unidad: "Unidad",
    costo: 800,
    margen_porcentaje: 0.5,
    precio_venta: 1200,
    precio_dia: 1200,
    codigo_barras: "7791234567890",
    activo: true,
    fecha_act: "2026-10-24",
  },
  {
    id_producto: 2,
    nombre: "Alfajor Triple Chocolate",
    categoria: "Golosinas",
    proveedor: "Dulces del Norte",
    unidad: "Unidad",
    costo: 500,
    margen_porcentaje: 0.6,
    precio_venta: 800,
    precio_dia: 800,
    codigo_barras: "7791234567891",
    activo: true,
    fecha_act: "2026-10-24",
  },
  {
    id_producto: 3,
    nombre: "Agua Mineral 1.5L",
    categoria: "Bebidas",
    proveedor: "Distribuidora Central",
    unidad: "Unidad",
    costo: 700,
    margen_porcentaje: 0.43,
    precio_venta: 1000,
    precio_dia: 1000,
    codigo_barras: "7791234567893",
    activo: true,
    fecha_act: "2026-10-24",
  },
  {
    id_producto: 4,
    nombre: "Fotocopia B/N simple faz",
    categoria: "Servicios",
    proveedor: "Local",
    unidad: "Hoja",
    costo: 20,
    margen_porcentaje: 4.0,
    precio_venta: 100,
    precio_dia: 100,
    activo: true,
    fecha_act: "2026-10-24",
  },
  {
    id_producto: 5,
    nombre: "Impresión Color A4",
    categoria: "Servicios",
    proveedor: "Local",
    unidad: "Hoja",
    costo: 100,
    margen_porcentaje: 3.0,
    precio_venta: 400,
    precio_dia: 400,
    activo: true,
    fecha_act: "2026-10-24",
  },
  {
    id_producto: 6,
    nombre: "Caramelos surtidos x 100g",
    categoria: "Golosinas",
    proveedor: "Dulces del Norte",
    unidad: "100g",
    costo: 300,
    margen_porcentaje: 1.0,
    precio_venta: 600,
    precio_dia: 600,
    activo: true,
    fecha_act: "2026-10-24",
  },
  {
    id_producto: 7,
    nombre: "Galletitas Rellenas",
    categoria: "Almacén",
    proveedor: "Distribuidora Central",
    unidad: "Paquete",
    costo: 650,
    margen_porcentaje: 0.54,
    precio_venta: 1000,
    precio_dia: 1000,
    codigo_barras: "7791234567892",
    activo: true,
    fecha_act: "2026-10-24",
  },
  {
    id_producto: 8,
    nombre: "Mignon",
    categoria: "Panadería",
    proveedor: "Panadería Maná",
    unidad: "Gramos",
    costo: 5000,
    margen_porcentaje: 0.6,
    precio_venta: 8000,
    precio_dia: 8000,
    activo: true,
    fecha_act: "2026-10-24",
  },
  {
    id_producto: 9,
    nombre: "Bizcochos Rombo",
    categoria: "Panadería",
    proveedor: "Panadería Maná",
    unidad: "Gramos",
    costo: 1800,
    margen_porcentaje: 0.67,
    precio_venta: 3000,
    precio_dia: 3000,
    activo: true,
    fecha_act: "2026-10-24",
  },
  {
    id_producto: 10,
    nombre: "Queso cremoso Nueva Villa x100gr",
    categoria: "Lácteos",
    proveedor: "Loria",
    unidad: "Gramos",
    costo: 800,
    margen_porcentaje: 0.5,
    precio_venta: 1200,
    precio_dia: 1200,
    activo: true,
    fecha_act: "2026-10-24",
  },
];

export class MockApiClient implements IApiClient {
  private simulationMode: DemoSimulationMode = "exito_201";

  setSimulationMode(mode: DemoSimulationMode) {
    this.simulationMode = mode;
  }

  getSimulationMode(): DemoSimulationMode {
    return this.simulationMode;
  }

  isDemo(): boolean {
    return true;
  }

  async login(username: string, _password: string): Promise<ApiResponse<LoginResult>> {
    await new Promise((r) => setTimeout(r, 40));
    const normalized = username.trim().toLowerCase();
    const isYilda = normalized.includes("yilda");
    return {
      status: 200,
      data: {
        access_token: "mock_demo_jwt_token_secure_isolated",
        token_type: "bearer",
        expires_in: 28800,
        user: {
          username: isYilda ? "yilda" : "alan",
          display_name: isYilda ? "Yilda (Demo)" : "Alan (Demo)",
        },
      },
    };
  }

  async obtenerUsuarioActual(): Promise<ApiResponse<AuthUser>> {
    return {
      status: 200,
      data: {
        username: "alan",
        display_name: "Alan (Demo)",
      },
    };
  }

  setAuthToken(_token: string | null): void {}

  getAuthToken(): string | null {
    return "mock_demo_jwt_token_secure_isolated";
  }

  setOnUnauthorized(_cb: () => void): void {}

  async obtenerCatalogo(includeInactive: boolean = false): Promise<ApiResponse<CatalogoResponse>> {
    await new Promise((r) => setTimeout(r, 40));
    try {
      await inicializarCatalogoDemoSiVacio(CATALOGO_DEMO);
      const productos = await listarProductosDemo(!includeInactive);
      return {
        status: 200,
        data: {
          total: productos.length,
          productos,
        },
      };
    } catch {
      const productos = includeInactive
        ? CATALOGO_DEMO
        : CATALOGO_DEMO.filter((p) => p.activo !== false);
      return {
        status: 200,
        data: {
          total: productos.length,
          productos,
        },
      };
    }
  }

  async crearProducto(datos: ProductoInput): Promise<ApiResponse<Producto>> {
    await new Promise((r) => setTimeout(r, 60));
    try {
      const creado = await crearProductoDemo(datos);
      return { status: 201, data: creado };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al crear producto";
      return { status: 400, error: msg };
    }
  }

  async editarProducto(id: number, cambios: Partial<ProductoInput>): Promise<ApiResponse<Producto>> {
    await new Promise((r) => setTimeout(r, 60));
    try {
      const editado = await editarProductoDemo(id, cambios);
      return { status: 200, data: editado };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al editar producto";
      return { status: 400, error: msg };
    }
  }

  async cambiarEstadoProducto(id: number, activo: boolean): Promise<ApiResponse<Producto>> {
    await new Promise((r) => setTimeout(r, 60));
    try {
      const editado = await cambiarEstadoProductoDemo(id, activo);
      return { status: 200, data: editado };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al cambiar estado";
      return { status: 400, error: msg };
    }
  }

  async eliminarProducto(id: number): Promise<ApiResponse<ProductoDeleteResponse>> {
    await new Promise((r) => setTimeout(r, 60));
    try {
      const resultado = await eliminarProductoDemo(id);
      return { status: 200, data: resultado };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al eliminar producto";
      return { status: 404, error: msg };
    }
  }

  async registrarVenta(payload: VentaPayload): Promise<ApiResponse<VentaResponse>> {
    await new Promise((r) => setTimeout(r, 120));

    if (this.simulationMode === "timeout") {
      throw new Error("Error simulado de red / Timeout (Demo)");
    }

    if (this.simulationMode === "conflicto_409") {
      return {
        status: 409,
        error: `Conflicto: el ticket '${payload.ticket.ticket_id}' ya existe con datos discordantes (Simulación 409).`,
      };
    }

    if (this.simulationMode === "reintento_200") {
      return {
        status: 200,
        data: {
          ticket_id: payload.ticket.ticket_id,
          status: "existente",
          mensaje: "Venta previamente registrada (Simulación 200 Idempotente).",
        },
      };
    }

    // Modo por defecto: 201 Created
    return {
      status: 201,
      data: {
        ticket_id: payload.ticket.ticket_id,
        status: "registrado",
        mensaje: "Venta registrada exitosamente (Modo Demo Seguro).",
      },
    };
  }

  // Métodos de Servicios y Cargas (Modo Demo)
  async obtenerCargas(
    fecha?: string,
    incluirAnuladas: boolean = false,
    plataformaOrigen?: string,
    servicioDestino?: string
  ): Promise<ApiResponse<{ total: number; cargas: CargaServicio[] }>> {
    await new Promise((r) => setTimeout(r, 40));
    try {
      await inicializarServiciosDemoSiVacio();
      const todas = await listarServiciosDemo(incluirAnuladas);
      let filtradas = todas;
      if (fecha) {
        const ff = fecha.trim();
        let target = ff;
        if (ff.includes("/")) {
          const [d, m, y] = ff.split("/").map(Number);
          target = `${String(y).padStart(4, "0")}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
        }
        filtradas = todas.filter((c) => {
          try {
            const [d, m, y] = c.fecha.split(" ")[0].split("/").map(Number);
            const cDate = `${String(y).padStart(4, "0")}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
            return cDate === target;
          } catch {
            return false;
          }
        });
      }
      if (plataformaOrigen) {
        const po = plataformaOrigen.trim().toUpperCase();
        if (po === "HISTORICO" || po === "SIN_ORIGEN") {
          filtradas = filtradas.filter((c) => !c.plataforma_origen);
        } else {
          filtradas = filtradas.filter((c) => (c.plataforma_origen || "").toUpperCase() === po);
        }
      }
      if (servicioDestino) {
        const sd = servicioDestino.trim().toLowerCase();
        filtradas = filtradas.filter((c) => (c.servicio_destino || c.servicio || "").toLowerCase() === sd);
      }
      return {
        status: 200,
        data: {
          total: filtradas.length,
          cargas: filtradas,
        },
      };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al obtener cargas demo";
      return { status: 400, error: msg };
    }
  }

  async obtenerResumenCargas(fecha?: string): Promise<ApiResponse<ResumenServicios>> {
    await new Promise((r) => setTimeout(r, 30));
    try {
      await inicializarServiciosDemoSiVacio();
      const resumen = await calcularResumenServiciosDemo(fecha);
      return { status: 200, data: resumen };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al obtener resumen de cargas demo";
      return { status: 400, error: msg };
    }
  }

  async registrarCarga(carga: CargaServicioInput): Promise<ApiResponse<CargaServicio>> {
    await new Promise((r) => setTimeout(r, 60));
    try {
      const nueva = await guardarCargaDemo(carga);
      return { status: 201, data: nueva };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al registrar carga demo";
      return { status: 400, error: msg };
    }
  }

  async editarCarga(id: number, carga: CargaServicioInput): Promise<ApiResponse<CargaServicio>> {
    await new Promise((r) => setTimeout(r, 60));
    try {
      const editada = await editarCargaDemo(id, carga);
      return { status: 200, data: editada };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al editar carga demo";
      return { status: 400, error: msg };
    }
  }

  async anularCarga(id: number): Promise<ApiResponse<{ status: string; id: number; mensaje: string }>> {
    await new Promise((r) => setTimeout(r, 50));
    try {
      await anularCargaDemo(id);
      return {
        status: 200,
        data: {
          status: "anulado",
          id,
          mensaje: `Carga #${id} anulada en demo localmente.`,
        },
      };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al anular carga demo";
      return { status: 400, error: msg };
    }
  }

  // Métodos de Gastos, Arqueo y Cierres (Modo Demo)
  async obtenerGastos(
    fecha?: string,
    tipo?: string,
    incluirAnulados: boolean = false
  ): Promise<ApiResponse<{ total: number; gastos: Gasto[] }>> {
    await new Promise((r) => setTimeout(r, 40));
    try {
      await inicializarGastosDemoSiVacio();
      const todos = await listarGastosDemo(incluirAnulados);
      let filtrados = todos;

      if (fecha) {
        const ff = fecha.trim();
        let target = ff;
        if (ff.includes("/")) {
          const [d, m, y] = ff.split("/").map(Number);
          target = `${String(y).padStart(4, "0")}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
        }
        filtrados = filtrados.filter((g) => {
          try {
            const [d, m, y] = g.fecha.split(" ")[0].split("/").map(Number);
            const gDate = `${String(y).padStart(4, "0")}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
            return gDate === target;
          } catch {
            return false;
          }
        });
      }

      if (tipo && tipo !== "TODAS") {
        filtrados = filtrados.filter((g) => g.tipo.toLowerCase() === tipo.toLowerCase());
      }

      return {
        status: 200,
        data: {
          total: filtrados.length,
          gastos: filtrados,
        },
      };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al obtener gastos demo";
      return { status: 400, error: msg };
    }
  }

  async obtenerResumenGastos(fecha?: string): Promise<ApiResponse<ResumenGastos>> {
    await new Promise((r) => setTimeout(r, 30));
    try {
      await inicializarGastosDemoSiVacio();
      const resumen = await calcularResumenGastosDemo(fecha);
      return { status: 200, data: resumen };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al obtener resumen de gastos demo";
      return { status: 400, error: msg };
    }
  }

  async obtenerArqueoDiario(fecha?: string): Promise<ApiResponse<ArqueoDiario>> {
    await new Promise((r) => setTimeout(r, 50));
    try {
      await inicializarGastosDemoSiVacio();
      await inicializarServiciosDemoSiVacio();
      const arqueo = await calcularArqueoDemo(fecha);
      return { status: 200, data: arqueo };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al calcular arqueo demo";
      return { status: 400, error: msg };
    }
  }

  async registrarGasto(gasto: GastoInput): Promise<ApiResponse<Gasto>> {
    await new Promise((r) => setTimeout(r, 60));
    try {
      const nuevo = await guardarGastoDemo(gasto);
      return { status: 201, data: nuevo };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al registrar gasto demo";
      return { status: 400, error: msg };
    }
  }

  async editarGasto(id: number, cambios: GastoInput): Promise<ApiResponse<Gasto>> {
    await new Promise((r) => setTimeout(r, 60));
    try {
      const editado = await editarGastoDemo(id, cambios);
      return { status: 200, data: editado };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al editar gasto demo";
      return { status: 400, error: msg };
    }
  }

  async anularGasto(id: number, _motivo?: string): Promise<ApiResponse<{ status: string; id: number; mensaje: string }>> {
    await new Promise((r) => setTimeout(r, 50));
    try {
      await anularGastoDemo(id);
      return {
        status: 200,
        data: {
          status: "anulado",
          id,
          mensaje: `Gasto #${id} anulado lógicamente en demo.`,
        },
      };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al anular gasto demo";
      return { status: 400, error: msg };
    }
  }

  async obtenerCierreDiario(fecha: string): Promise<ApiResponse<CierreDiario>> {
    await new Promise((r) => setTimeout(r, 40));
    try {
      const cierre = await obtenerCierreDemo(fecha);
      if (!cierre) {
        return { status: 404, error: `No hay cierre registrado para la fecha ${fecha}.` };
      }
      return { status: 200, data: cierre };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al obtener cierre demo";
      return { status: 400, error: msg };
    }
  }

  async registrarCierreDiario(cierre: CierreDiario): Promise<ApiResponse<CierreDiario>> {
    await new Promise((r) => setTimeout(r, 60));
    try {
      const guardado = await guardarCierreDemo(cierre);
      return { status: 201, data: guardado };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al guardar cierre demo";
      return { status: 400, error: msg };
    }
  }

  async listarCierres(): Promise<ApiResponse<CierreDiario[]>> {
    await new Promise((r) => setTimeout(r, 40));
    try {
      const cierres = await listarCierresDemo();
      return { status: 200, data: cierres };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al listar cierres demo";
      return { status: 400, error: msg };
    }
  }

  // Historial de Ventas (Modo Demo)
  async obtenerHistorialVentas(
    fecha?: string,
    metodo_pago?: string,
    busqueda?: string,
    incluir_anuladas: boolean = true
  ): Promise<ApiResponse<VentasHistorialResponse>> {
    await new Promise((r) => setTimeout(r, 50));
    try {
      await inicializarVentasDemoSiVacio();
      const ventas = await listarVentasHistorialDemo({
        fecha,
        metodo_pago,
        busqueda,
        incluir_anuladas,
      });
      return {
        status: 200,
        data: {
          total: ventas.length,
          ventas,
        },
      };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al consultar historial demo";
      return { status: 400, error: msg };
    }
  }

  async obtenerDetalleVenta(ticket_id: string): Promise<ApiResponse<TicketDetalle>> {
    await new Promise((r) => setTimeout(r, 40));
    try {
      const detalle = await obtenerDetalleTicketDemo(ticket_id);
      if (!detalle) {
        return { status: 404, error: `Ticket '${ticket_id}' no encontrado.` };
      }
      return { status: 200, data: detalle };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al obtener detalle de venta";
      return { status: 400, error: msg };
    }
  }

  async anularVenta(
    ticket_id: string,
    motivo: string,
    usuario?: string
  ): Promise<ApiResponse<TicketDetalle>> {
    await new Promise((r) => setTimeout(r, 60));
    try {
      const anulada = await anularVentaDemo(ticket_id, motivo, usuario);
      return { status: 200, data: anulada };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al anular venta demo";
      return { status: 400, error: msg };
    }
  }

  async editarItemVenta(
    ticket_id: string,
    item_id: number,
    nueva_cantidad?: number,
    nuevo_subtotal?: number,
    usuario?: string
  ): Promise<ApiResponse<TicketDetalle>> {
    await new Promise((r) => setTimeout(r, 60));
    try {
      const editada = await editarItemVentaDemo(
        ticket_id,
        item_id,
        nueva_cantidad,
        nuevo_subtotal,
        usuario
      );
      return { status: 200, data: editada };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al editar ítem de venta demo";
      return { status: 400, error: msg };
    }
  }

  // Preventistas y Pedidos (Mock)
  async obtenerProveedores(): Promise<ApiResponse<string[]>> {
    await new Promise((r) => setTimeout(r, 40));
    try {
      await inicializarCatalogoDemoSiVacio(CATALOGO_DEMO);
      const lista = await listarProveedoresDemo();
      return { status: 200, data: lista };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al listar proveedores demo";
      return { status: 500, error: msg };
    }
  }

  async obtenerProductosPorProveedor(proveedor: string): Promise<ApiResponse<Producto[]>> {
    await new Promise((r) => setTimeout(r, 40));
    try {
      await inicializarCatalogoDemoSiVacio(CATALOGO_DEMO);
      const lista = await listarProductosPorProveedorDemo(proveedor);
      return { status: 200, data: lista };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al listar productos de proveedor demo";
      return { status: 500, error: msg };
    }
  }

  async actualizarPreciosPreventista(
    proveedor: string,
    cambios: Array<{ id_producto: number; costo: number; precio_venta: number }>
  ): Promise<ApiResponse<{ actualizados: number; mensaje: string }>> {
    await new Promise((r) => setTimeout(r, 80));
    try {
      const res = await guardarPreciosLoteDemo(proveedor, cambios);
      return { status: 200, data: res };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al actualizar precios demo";
      return { status: 400, error: msg };
    }
  }

  async altaRapidaProductoPreventista(datos: ProductoInput): Promise<ApiResponse<Producto>> {
    await new Promise((r) => setTimeout(r, 80));
    try {
      const nuevo = await crearProductoDemo(datos);
      return { status: 201, data: nuevo };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al crear producto demo";
      return { status: 400, error: msg };
    }
  }

  async obtenerPedidos(filtros?: {
    proveedor?: string;
    fecha?: string;
    estado?: string;
  }): Promise<ApiResponse<PedidoProveedor[]>> {
    await new Promise((r) => setTimeout(r, 50));
    try {
      const lista = await listarPedidosDemo(filtros);
      return { status: 200, data: lista };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al consultar pedidos demo";
      return { status: 500, error: msg };
    }
  }

  async obtenerDetallePedido(id: string): Promise<ApiResponse<PedidoProveedorDetalle>> {
    await new Promise((r) => setTimeout(r, 50));
    try {
      const detalle = await obtenerDetallePedidoDemo(id);
      if (!detalle) {
        return { status: 404, error: `No se encontró el pedido ${id}.` };
      }
      return { status: 200, data: detalle };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al consultar detalle del pedido demo";
      return { status: 500, error: msg };
    }
  }

  async crearPedido(datos: {
    proveedor: string;
    fecha?: string;
    observaciones?: string;
    items: PedidoItem[];
  }): Promise<ApiResponse<PedidoProveedorDetalle>> {
    await new Promise((r) => setTimeout(r, 80));
    try {
      const nuevo = await crearPedidoDemo(datos);
      return { status: 201, data: nuevo };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al registrar pedido demo";
      return { status: 400, error: msg };
    }
  }

  async cambiarEstadoPedido(
    id: string,
    nuevoEstado: "PENDIENTE" | "RECIBIDO" | "CANCELADO",
    observaciones?: string
  ): Promise<ApiResponse<PedidoProveedor>> {
    await new Promise((r) => setTimeout(r, 60));
    try {
      const mod = await cambiarEstadoPedidoDemo(id, nuevoEstado, observaciones);
      return { status: 200, data: mod };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al actualizar estado del pedido demo";
      return { status: 400, error: msg };
    }
  }

  async cancelarPedido(id: string, motivo: string): Promise<ApiResponse<PedidoProveedor>> {
    await new Promise((r) => setTimeout(r, 60));
    try {
      const canc = await cancelarPedidoDemo(id, motivo);
      return { status: 200, data: canc };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al cancelar pedido demo";
      return { status: 400, error: msg };
    }
  }

  async obtenerMetricasDashboard(
    desde?: string,
    hasta?: string,
    periodo?: PeriodoTipo
  ): Promise<ApiResponse<MetricasDashboard>> {
    await new Promise((r) => setTimeout(r, 60));
    try {
      const metricas = await calcularMetricasDashboardDemo(desde, hasta, periodo);
      return { status: 200, data: metricas };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error al calcular métricas de dashboard demo";
      return { status: 500, error: msg };
    }
  }

  async verificarPreparacionSistema(): Promise<ApiResponse<SystemReadiness>> {
    return {
      status: 200,
      data: {
        status: "PREPARADO",
        listo: true,
        versiones_aplicadas: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
        migraciones_pendientes: [],
        tablas_existentes: [
          "productos",
          "tickets",
          "ticket_items",
          "cargas_servicios",
          "gastos_retiros",
          "cierres_diarios",
          "productos_auditoria",
          "cargas_servicios_auditoria",
          "gastos_auditoria",
          "ventas_auditoria",
          "pedidos_proveedor",
          "pedido_items",
          "pedidos_auditoria",
        ],
        tablas_faltantes: [],
        columnas_faltantes: [],
        inconsistencias: [],
        mensaje: "Modo demo aislado: esquema simulado 100% preparado.",
      },
    };
  }

  async obtenerResumenHistorico(): Promise<ApiResponse<HistoricalSummary>> {
    await new Promise((r) => setTimeout(r, 60));
    return {
      status: 200,
      data: {
        total_ventas_pesos: 23623890.0,
        total_tickets: 7246,
        ticket_promedio: 3260.27,
        total_ventas_efectivo: 17467640.0,
        total_ventas_transferencia: 6156250.0,
        fecha_inicio: "2025-05-18",
        fecha_fin: "2026-03-01",
        periodo_meses: 10,
        total_items_filas: 11566,
        total_items_subtotal: 23522590.0,
        total_cargas_cobrado: 9556400.0,
        total_cargas_capital: 9140400.0,
        total_cargas_adicional: 416000.0,
        total_operaciones_cargas: 1741,
        total_cargas_efectivo: 6500000.0,
        total_cargas_transferencia: 3056400.0,
        advertencia_pagos: "4 tickets presentan discrepancias entre total y medios de pago (-$6.900 neto).",
        advertencia_items: "31 tickets no registran ítems detallados ($101.300 total).",
      },
    };
  }

  async obtenerVentasHistoricas(params?: {
    desde?: string;
    hasta?: string;
    metodo_pago?: string;
    agrupacion?: string;
    limit?: number;
    offset?: number;
  }): Promise<ApiResponse<HistoricalSales>> {
    await new Promise((r) => setTimeout(r, 60));
    return {
      status: 200,
      data: {
        desde: params?.desde || null,
        hasta: params?.hasta || null,
        metodo_pago: params?.metodo_pago || null,
        total_ventas: 23623890.0,
        cantidad_tickets: 7246,
        ticket_promedio: 3260.27,
        total_efectivo: 17467640.0,
        total_transferencia: 6156250.0,
        serie_temporal: [
          { periodo: "2025-05", tickets: 680, ventas_total: 1850000, monto_efectivo: 1400000, monto_transferencia: 450000 },
          { periodo: "2025-06", tickets: 710, ventas_total: 2100000, monto_efectivo: 1600000, monto_transferencia: 500000 },
          { periodo: "2025-07", tickets: 740, ventas_total: 2350000, monto_efectivo: 1750000, monto_transferencia: 600000 },
          { periodo: "2025-08", tickets: 730, ventas_total: 2400000, monto_efectivo: 1800000, monto_transferencia: 600000 },
          { periodo: "2025-09", tickets: 750, ventas_total: 2500000, monto_efectivo: 1850000, monto_transferencia: 650000 },
          { periodo: "2025-10", tickets: 760, ventas_total: 2600000, monto_efectivo: 1900000, monto_transferencia: 700000 },
          { periodo: "2025-11", tickets: 780, ventas_total: 2750000, monto_efectivo: 2000000, monto_transferencia: 750000 },
          { periodo: "2025-12", tickets: 820, ventas_total: 3100000, monto_efectivo: 2250000, monto_transferencia: 850000 },
          { periodo: "2026-01", tickets: 690, ventas_total: 2150000, monto_efectivo: 1550000, monto_transferencia: 600000 },
          { periodo: "2026-02", tickets: 650, ventas_total: 1823890, monto_efectivo: 1367640, monto_transferencia: 456250 },
        ],
        tickets: [
          { id: "T-20250518-001", fecha_hora_local: "2025-05-18 09:15:00", total_venta: 3500, monto_efectivo: 3500, monto_transferencia: 0, metodo_pago: "EFECTIVO", tiene_items: 1, balance_pago_diferencia: 0 },
          { id: "T-20250518-002", fecha_hora_local: "2025-05-18 10:20:00", total_venta: 5200, monto_efectivo: 0, monto_transferencia: 5200, metodo_pago: "TRANSFERENCIA", tiene_items: 1, balance_pago_diferencia: 0 },
        ],
        total_registros: 7246,
        limit: params?.limit || 50,
        offset: params?.offset || 0,
      },
    };
  }

  async obtenerProductosHistoricos(params?: {
    desde?: string;
    hasta?: string;
    depurado?: boolean;
    limit?: number;
    offset?: number;
  }): Promise<ApiResponse<HistoricalProducts>> {
    await new Promise((r) => setTimeout(r, 60));
    return {
      status: 200,
      data: {
        desde: params?.desde || null,
        hasta: params?.hasta || null,
        modo_depurado: params?.depurado ?? true,
        total_productos_distintos: 350,
        productos: [
          { posicion: 1, producto_nombre: "Cigarrillos Philip Morris Box 20", operaciones: 840, unidades_cuantificables: 910, ventas_pesables_monto: 0, total_recaudado: 2150000 },
          { posicion: 2, producto_nombre: "Coca Cola 2.25L Retornable", operaciones: 620, unidades_cuantificables: 650, ventas_pesables_monto: 0, total_recaudado: 1450000 },
          { posicion: 3, producto_nombre: "Cerveza Quilmes Clásica 1L", operaciones: 510, unidades_cuantificables: 530, ventas_pesables_monto: 0, total_recaudado: 1200000 },
          { posicion: 4, producto_nombre: "Queso Cremoso La Paulina (kg)", operaciones: 320, unidades_cuantificables: 0, ventas_pesables_monto: 980000, total_recaudado: 980000 },
        ],
        total_registros: 350,
        limit: params?.limit || 50,
        offset: params?.offset || 0,
        advertencia_pesables: "Los productos vendidos por peso no reportan cantidad en piezas; su volumen se expresa en monto monetario acumulado.",
      },
    };
  }

  async obtenerServiciosHistoricos(params?: {
    desde?: string;
    hasta?: string;
    excluir_duplicados?: boolean;
  }): Promise<ApiResponse<HistoricalServices>> {
    await new Promise((r) => setTimeout(r, 60));
    return {
      status: 200,
      data: {
        desde: params?.desde || null,
        hasta: params?.hasta || null,
        excluir_duplicados: params?.excluir_duplicados ?? false,
        total_operaciones: 1741,
        capital_recargado: 9140400.0,
        adicional_ganancia: 416000.0,
        total_cobrado: 9556400.0,
        pago_efectivo: 6500000.0,
        pago_transferencia: 3056400.0,
        servicios: [
          { servicio: "SUBE", total_operaciones: 920, capital_recargado: 4800000, adicional_ganancia: 210000, total_cobrado: 5010000, pago_efectivo: 3500000, pago_transferencia: 1510000 },
          { servicio: "CLARO", total_operaciones: 410, capital_recargado: 2200000, adicional_ganancia: 105000, total_cobrado: 2305000, pago_efectivo: 1600000, pago_transferencia: 705000 },
          { servicio: "PERSONAL", total_operaciones: 260, capital_recargado: 1350000, adicional_ganancia: 65000, total_cobrado: 1415000, pago_efectivo: 950000, pago_transferencia: 465000 },
          { servicio: "MOVISTAR", total_operaciones: 151, capital_recargado: 790400, adicional_ganancia: 36000, total_cobrado: 826400, pago_efectivo: 450000, pago_transferencia: 376400 },
        ],
        serie_temporal: [
          { periodo: "2025-05", operaciones: 160, total_cobrado: 850000, adicional_ganancia: 38000 },
          { periodo: "2025-06", operaciones: 175, total_cobrado: 920000, adicional_ganancia: 41000 },
        ],
        advertencia_duplicados: "Existen 24 filas (12 pares) con estampa de tiempo idéntica al segundo ($117.000 cobrado).",
      },
    };
  }

  async buscarTicketsHistoricos(params?: {
    query?: string;
    fecha?: string;
    limit?: number;
    offset?: number;
  }): Promise<ApiResponse<HistoricalTicketsSearch>> {
    await new Promise((r) => setTimeout(r, 60));
    return {
      status: 200,
      data: {
        total_encontrados: 2,
        tickets: [
          { id: "T-20250518-001", fecha_hora_local: "2025-05-18 09:15:00", total_venta: 3500, monto_efectivo: 3500, monto_transferencia: 0, metodo_pago: "EFECTIVO", tiene_items: 1, balance_pago_diferencia: 0 },
          { id: "T-20250518-002", fecha_hora_local: "2025-05-18 10:20:00", total_venta: 5200, monto_efectivo: 0, monto_transferencia: 5200, metodo_pago: "TRANSFERENCIA", tiene_items: 1, balance_pago_diferencia: 0 },
        ],
        limit: params?.limit || 20,
        offset: params?.offset || 0,
      },
    };
  }

  async obtenerTicketDetalleHistorico(ticketId: string): Promise<ApiResponse<HistoricalTicketDetalle>> {
    await new Promise((r) => setTimeout(r, 60));
    return {
      status: 200,
      data: {
        id: ticketId,
        fecha_hora_local: "2025-05-18 09:15:00",
        fecha_hora_utc: "2025-05-18 12:15:00",
        total_venta: 3500,
        monto_efectivo: 3500,
        monto_transferencia: 0,
        metodo_pago: "EFECTIVO",
        es_nocturno: 0,
        balance_pago_diferencia: 0,
        tiene_items: 1,
        origen_fila_excel: 45,
        items: [
          { id: 1, ticket_id: ticketId, producto_nombre: "Alfajor Triple Chocolate", cantidad: 2, unidad: "Unidad", precio_unitario: 800, subtotal: 1600, metodo_pago: "EFECTIVO", calculo_subtotal_coincide: 1, es_duplicado_deriva: 0, ticket_id_correlacionado: null },
          { id: 2, ticket_id: ticketId, producto_nombre: "Gaseosa Cola 500ml", cantidad: 1, unidad: "Unidad", precio_unitario: 1900, subtotal: 1900, metodo_pago: "EFECTIVO", calculo_subtotal_coincide: 1, es_duplicado_deriva: 0, ticket_id_correlacionado: null },
        ],
        alertas: [],
      },
    };
  }

  async compararPeriodosHistoricos(params: {
    desde_a: string;
    hasta_a: string;
    desde_b: string;
    hasta_b: string;
  }): Promise<ApiResponse<HistoricalCompare>> {
    await new Promise((r) => setTimeout(r, 60));
    return {
      status: 200,
      data: {
        periodo_a: {
          desde: params.desde_a,
          hasta: params.hasta_a,
          facturacion_ventas: 7500000,
          cantidad_tickets: 2300,
          ticket_promedio: 3260.87,
          cargas_cobrado: 3000000,
          cargas_adicional: 130000,
          operaciones_cargas: 550,
          top_productos: [
            { nombre: "Cigarrillos Philip Morris", subtotal: 650000 },
            { nombre: "Coca Cola 2.25L", subtotal: 450000 },
          ],
        },
        periodo_b: {
          desde: params.desde_b,
          hasta: params.hasta_b,
          facturacion_ventas: 8200000,
          cantidad_tickets: 2450,
          ticket_promedio: 3346.94,
          cargas_cobrado: 3300000,
          cargas_adicional: 145000,
          operaciones_cargas: 600,
          top_productos: [
            { nombre: "Cigarrillos Philip Morris", subtotal: 720000 },
            { nombre: "Coca Cola 2.25L", subtotal: 490000 },
          ],
        },
        variaciones: {
          facturacion_ventas_pct: 9.33,
          cantidad_tickets_pct: 6.52,
          ticket_promedio_pct: 2.64,
          cargas_cobrado_pct: 10.0,
        },
      },
    };
  }
}

export function normalizarTicketDetalle(raw: any): TicketDetalle {
  const itemsRaw = Array.isArray(raw?.items) ? raw.items : [];
  const items = itemsRaw.map((it: any) => ({
    ...it,
    id: typeof it?.id === "number" ? it.id : undefined,
    producto_id: typeof it?.producto_id === "number" ? it.producto_id : undefined,
    producto: it?.producto || it?.producto_nombre || "Producto sin nombre",
    producto_nombre: it?.producto_nombre || it?.producto || "Producto sin nombre",
    cantidad: Number(it?.cantidad ?? 0),
    unidad: it?.unidad || "Unidad",
    precio_unit: Number(it?.precio_unit ?? it?.precio_unitario ?? 0),
    precio_unitario: Number(it?.precio_unitario ?? it?.precio_unit ?? 0),
    subtotal: Number(it?.subtotal ?? 0),
    metodo_pago: it?.metodo_pago,
  }));
  return {
    ...raw,
    ticket_id: String(raw?.ticket_id ?? ""),
    fecha: String(raw?.fecha ?? ""),
    total_venta: Number(raw?.total_venta ?? 0),
    monto_efectivo: Number(raw?.monto_efectivo ?? 0),
    monto_transferencia: Number(raw?.monto_transferencia ?? raw?.monto_transf ?? 0),
    metodo_pago: raw?.metodo_pago || "EFECTIVO",
    anulado: Boolean(raw?.anulado),
    items,
    auditoria: Array.isArray(raw?.auditoria) ? raw.auditoria : [],
  };
}

export class RealApiClient implements IApiClient {
  private baseUrl: string;
  private token: string | null = null;
  private onUnauthorized?: () => void;
  private readOnlySmoke: boolean;

  constructor(
    baseUrl: string = "",
    readOnlySmoke: boolean = IS_READ_ONLY_SMOKE_MODE
  ) {
    this.baseUrl = baseUrl ? baseUrl.replace(/\/+$/, "") : "";
    this.readOnlySmoke = readOnlySmoke;
  }

  isDemo(): boolean {
    return false;
  }

  isReadOnlySmoke(): boolean {
    return this.readOnlySmoke;
  }

  setReadOnlySmoke(enabled: boolean): void {
    this.readOnlySmoke = enabled;
  }

  setAuthToken(token: string | null): void {
    this.token = token;
  }

  getAuthToken(): string | null {
    return this.token;
  }

  setOnUnauthorized(cb: () => void): void {
    this.onUnauthorized = cb;
  }

  private async fetchWithAuth(url: string, init: RequestInit = {}): Promise<Response> {
    const method = (init.method || "GET").toUpperCase();
    const isLogin = url.includes("/api/auth/scram-");

    // Bloqueo estricto de cualquier mutación si el modo de prueba conectado de solo lectura está activo
    if (this.readOnlySmoke && method !== "GET" && !isLogin) {
      throw new Error(
        `READ_ONLY_SMOKE_MODE: Operación '${method} ${url}' bloqueada por modo de prueba de solo lectura.`
      );
    }

    const headers = new Headers(init.headers || {});
    if (this.token && !headers.has("Authorization")) {
      headers.set("Authorization", `Bearer ${this.token}`);
    }
    const resp = await fetch(url, { ...init, headers });
    if (resp.status === 401) {
      this.onUnauthorized?.();
    }
    return resp;
  }

  async login(username: string, password: string): Promise<ApiResponse<LoginResult>> {
    try {
      const subtle =
        (typeof window !== "undefined" && window.crypto?.subtle) ||
        (typeof globalThis !== "undefined" && globalThis.crypto?.subtle);

      if (!subtle) {
        return {
          status: 500,
          error: "API de Criptografía Web (crypto.subtle) no disponible en este entorno.",
        };
      }

      const encoder = new TextEncoder();
      const bytesToBase64 = (bytes: Uint8Array): string => {
        let binary = "";
        for (let i = 0; i < bytes.byteLength; i++) {
          binary += String.fromCharCode(bytes[i]);
        }
        return btoa(binary);
      };
      const base64ToBytes = (b64: string): Uint8Array => {
        const binary = atob(b64);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) {
          bytes[i] = binary.charCodeAt(i);
        }
        return bytes;
      };
      const xorBytes = (a: Uint8Array, b: Uint8Array): Uint8Array => {
        const out = new Uint8Array(a.length);
        for (let i = 0; i < a.length; i++) {
          out[i] = a[i] ^ b[i];
        }
        return out;
      };

      const hmacSha256 = async (keyBytes: Uint8Array, dataBytes: Uint8Array): Promise<Uint8Array> => {
        const cryptoKey = await subtle.importKey(
          "raw",
          keyBytes as unknown as BufferSource,
          { name: "HMAC", hash: "SHA-256" },
          false,
          ["sign"]
        );
        const sig = await subtle.sign("HMAC", cryptoKey, dataBytes as unknown as BufferSource);
        return new Uint8Array(sig);
      };

      const sha256 = async (dataBytes: Uint8Array): Promise<Uint8Array> => {
        const hash = await subtle.digest("SHA-256", dataBytes as unknown as BufferSource);
        return new Uint8Array(hash);
      };

      // Helper to inspect HTTP errors and network/proxy anomalies
      const handleHttpError = async (resp: Response): Promise<{ status: number; error: string }> => {
        if (resp.redirected) {
          return {
            status: resp.status || 302,
            error: "La petición de login fue redirigida por el túnel o proxy. Verifique la conexión.",
          };
        }
        const contentType = resp.headers?.get("content-type") || "";
        if (contentType.includes("text/html")) {
          return {
            status: resp.status || 502,
            error: "El servidor devolvió una página HTML en lugar de una respuesta JSON válida del backend. Verifique si el túnel o proxy requiere autenticación.",
          };
        }
        if (resp.status === 403) {
          return { status: 403, error: "Acceso denegado (HTTP 403). Verifique los permisos del túnel o backend." };
        }
        if (resp.status === 502) {
          return { status: 502, error: "Error de puerta de enlace (HTTP 502). El servidor backend no responde a través del túnel." };
        }
        if (resp.status === 429) {
          return { status: 429, error: "Demasiados intentos fallidos. Por favor, aguardá 15 minutos." };
        }
        if (resp.status === 423) {
          return { status: 423, error: "Operación bloqueada por modo de solo lectura en el servidor." };
        }

        let errData: any = null;
        try {
          errData = await resp.json();
        } catch {
          errData = null;
        }

        if (resp.status === 401) {
          if (typeof errData?.detail === "string") {
            return { status: 401, error: errData.detail };
          }
          return {
            status: 401,
            error: "Respuesta 401 sin formato JSON de la API. Verificá si la ruta o el túnel están bloqueando el acceso.",
          };
        }

        return {
          status: resp.status,
          error:
            (typeof errData?.detail === "string" && errData.detail) ||
            `Error al autenticar (HTTP ${resp.status}).`,
        };
      };

      // 1. Client First
      const cryptoObj = (typeof window !== "undefined" && window.crypto) || globalThis.crypto;
      const randomBytes = new Uint8Array(18);
      cryptoObj.getRandomValues(randomBytes);
      const cNonce = bytesToBase64(randomBytes);
      const normalizedUser = username.normalize("NFKC").trim();
      const clientFirstBare = `n=${normalizedUser},r=${cNonce}`;
      const clientFirstMessage = `n,,${clientFirstBare}`;

      const firstResp = await fetch(`${this.baseUrl}/api/auth/scram-first`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ clientFirstMessage }),
      });

      if (firstResp.status === 404) {
        // Fallback transparente al endpoint estándar FastAPI /api/auth/login
        const directResp = await fetch(`${this.baseUrl}/api/auth/login`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username, password }),
        });
        if (directResp.redirected || (directResp.headers?.get("content-type") || "").includes("text/html")) {
          return await handleHttpError(directResp);
        }
        if (!directResp.ok) {
          return await handleHttpError(directResp);
        }
        const directData = await directResp.json().catch(() => ({}));
        if (directData && directData.access_token) {
          const result: LoginResult = {
            access_token: directData.access_token,
            token_type: directData.token_type || "bearer",
            expires_in: directData.expires_in || 28800,
            user: {
              username: directData.user?.username || username,
              display_name: directData.user?.display_name || username,
              role: directData.user?.role || "administrador",
            },
          };
          this.token = result.access_token;
          return { status: 200, data: result };
        }
        return { status: 502, error: "Respuesta de login sin access_token." };
      }

      if (firstResp.redirected || (firstResp.headers?.get("content-type") || "").includes("text/html")) {
        return await handleHttpError(firstResp);
      }
      if (!firstResp.ok) {
        return await handleHttpError(firstResp);
      }

      let firstData: any = null;
      try {
        firstData = await firstResp.json();
      } catch {
        return { status: 502, error: "Respuesta inválida del servidor (formato no JSON)." };
      }
      if (!firstData || typeof firstData.serverFirstMessage !== "string") {
        return { status: 502, error: "Respuesta inválida del servidor (desafío SCRAM ausente)." };
      }

      const serverFirstMessage: string = firstData.serverFirstMessage;
      const stateToken: string = firstData.stateToken;

      // Parse server-first
      const params = new Map<string, string>();
      for (const part of serverFirstMessage.split(",")) {
        const idx = part.indexOf("=");
        if (idx !== -1) {
          params.set(part.slice(0, idx), part.slice(idx + 1));
        }
      }

      const rNonce = params.get("r");
      const saltB64 = params.get("s");
      const iterations = parseInt(params.get("i") || "0", 10);

      if (!rNonce || !rNonce.startsWith(cNonce)) {
        return { status: 400, error: "Nonce del servidor inválido o discordante." };
      }
      if (!saltB64 || isNaN(iterations) || iterations < 4096) {
        return { status: 400, error: "Parámetros de sal o iteraciones insuficientes del servidor." };
      }

      const saltBytes = base64ToBytes(saltB64);

      // 2. PBKDF2 Client-Side
      const passKeyMaterial = await subtle.importKey(
        "raw",
        encoder.encode(password.normalize("NFKC")),
        "PBKDF2",
        false,
        ["deriveBits"]
      );
      const derivedBits = await subtle.deriveBits(
        {
          name: "PBKDF2",
          salt: saltBytes as unknown as BufferSource,
          iterations: iterations,
          hash: "SHA-256",
        },
        passKeyMaterial,
        256
      );
      const saltedPassword = new Uint8Array(derivedBits);

      // 3. Client Proof Calculation
      const clientKey = await hmacSha256(saltedPassword, encoder.encode("Client Key"));
      const storedKey = await sha256(clientKey);
      const serverKey = await hmacSha256(saltedPassword, encoder.encode("Server Key"));

      const channelBinding = "c=biws"; // base64("n,,")
      const clientFinalWithoutProof = `${channelBinding},r=${rNonce}`;
      const authMessage = `${clientFirstBare},${serverFirstMessage},${clientFinalWithoutProof}`;

      const clientSignature = await hmacSha256(storedKey, encoder.encode(authMessage));
      const clientProof = xorBytes(clientKey, clientSignature);
      const clientFinalMessage = `${clientFinalWithoutProof},p=${bytesToBase64(clientProof)}`;

      // 4. Client Final Request
      const finalResp = await fetch(`${this.baseUrl}/api/auth/scram-final`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ clientFinalMessage, stateToken }),
      });

      if (finalResp.redirected || (finalResp.headers?.get("content-type") || "").includes("text/html")) {
        return await handleHttpError(finalResp);
      }
      if (!finalResp.ok) {
        return await handleHttpError(finalResp);
      }

      let finalData: any = null;
      try {
        finalData = await finalResp.json();
      } catch {
        return { status: 502, error: "Respuesta inválida del servidor (formato no JSON)." };
      }
      if (!finalData || !finalData.access_token) {
        return { status: 502, error: "Respuesta inválida del servidor (falta access_token)." };
      }

      const serverFinalMessage: string = finalData.serverFinalMessage;

      // 5. Autenticación Mutua: Verificación de Firma del Servidor
      const match = serverFinalMessage?.match?.(/^v=(.+)$/);
      if (!match) {
        return { status: 401, error: "Firma mutua del servidor ausente o inválida." };
      }
      const serverSignatureBytes = base64ToBytes(match[1]);
      const expectedServerSignature = await hmacSha256(serverKey, encoder.encode(authMessage));

      if (serverSignatureBytes.length !== expectedServerSignature.length) {
        return { status: 401, error: "Firma del servidor no coincide. Posible servidor no fidedigno." };
      }
      let diff = 0;
      for (let i = 0; i < serverSignatureBytes.length; i++) {
        diff |= serverSignatureBytes[i] ^ expectedServerSignature[i];
      }
      if (diff !== 0) {
        return { status: 401, error: "Firma del servidor no coincide. Posible servidor no fidedigno." };
      }

      const rawUsername = finalData.user?.username || normalizedUser;
      const formatDisplayName = (u: string, d?: string) => {
        if (d && d.trim()) return d.trim();
        const lower = (u || "").trim().toLowerCase();
        if (lower === "alan") return "Alan";
        if (lower === "yilda") return "Yilda";
        return u ? u.charAt(0).toUpperCase() + u.slice(1) : "Usuario";
      };

      const result: LoginResult = {
        access_token: finalData.access_token,
        token_type: finalData.token_type || "bearer",
        expires_in: 86400,
        user: {
          username: rawUsername,
          display_name: formatDisplayName(rawUsername, finalData.user?.display_name),
          role: finalData.user?.role || "operador",
        },
      };

      this.token = result.access_token;
      return { status: 200, data: result };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async obtenerUsuarioActual(): Promise<ApiResponse<AuthUser>> {
    try {
      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/auth/me`, {
        method: "GET",
        headers: { Accept: "application/json" },
      });
      if (!resp.ok) {
        return { status: resp.status, error: "Sesión no válida o expirada" };
      }
      const rawData: Partial<AuthUser> = await resp.json();
      const uname = rawData.username || "";
      const lower = uname.toLowerCase();
      const displayName =
        rawData.display_name && rawData.display_name.trim()
          ? rawData.display_name.trim()
          : lower === "alan"
          ? "Alan"
          : lower === "yilda"
          ? "Yilda"
          : uname
          ? uname.charAt(0).toUpperCase() + uname.slice(1)
          : "Usuario";
      const data: AuthUser = {
        username: uname,
        display_name: displayName,
        role: rawData.role,
      };
      return { status: 200, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error al consultar sesión: ${message}` };
    }
  }

  async obtenerCatalogo(includeInactive: boolean = false): Promise<ApiResponse<CatalogoResponse>> {
    try {
      const q = includeInactive ? "?include_inactive=true" : "";
      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/catalog${q}`, {
        method: "GET",
        headers: { Accept: "application/json" },
      });
      if (!resp.ok) {
        const errorData = await resp.json().catch(() => ({}));
        return {
          status: resp.status,
          error: errorData.detail || `Error HTTP ${resp.status} al consultar catálogo.`,
        };
      }
      const data: CatalogoResponse = await resp.json();
      if (Array.isArray(data.productos)) {
        data.productos = data.productos.map((p: any) => ({
          ...p,
          id_producto: Number(p.id_producto ?? p.id),
          costo: Number(p.costo ?? p.costo_unitario ?? 0),
          precio_venta: Number(p.precio_venta ?? p.precio_dia ?? p.precio ?? 0),
          activo: p.activo !== 0 && p.activo !== false,
        }));
      }
      return { status: 200, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red desconocido";
      return { status: 0, error: `Error de conexión con el backend: ${message}` };
    }
  }

  async crearProducto(datos: ProductoInput): Promise<ApiResponse<Producto>> {
    try {
      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/catalog`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify(datos),
      });
      if (!resp.ok) {
        const errorData = await resp.json().catch(() => ({}));
        return {
          status: resp.status,
          error: errorData.detail || `Error HTTP ${resp.status} al crear producto.`,
        };
      }
      const raw: any = await resp.json();
      const data: Producto = {
        ...raw,
        id_producto: Number(raw.id_producto ?? raw.id),
        costo: Number(raw.costo ?? raw.costo_unitario ?? 0),
        precio_venta: Number(raw.precio_venta ?? raw.precio_dia ?? raw.precio ?? 0),
        activo: raw.activo !== 0 && raw.activo !== false,
      };
      return { status: resp.status, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async editarProducto(id: number, cambios: Partial<ProductoInput>): Promise<ApiResponse<Producto>> {
    try {
      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/catalog/${id}`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify(cambios),
      });
      if (!resp.ok) {
        const errorData = await resp.json().catch(() => ({}));
        return {
          status: resp.status,
          error: errorData.detail || `Error HTTP ${resp.status} al editar producto.`,
        };
      }
      const raw: any = await resp.json();
      const data: Producto = {
        ...raw,
        id_producto: Number(raw.id_producto ?? raw.id),
        costo: Number(raw.costo ?? raw.costo_unitario ?? 0),
        precio_venta: Number(raw.precio_venta ?? raw.precio_dia ?? raw.precio ?? 0),
        activo: raw.activo !== 0 && raw.activo !== false,
      };
      return { status: resp.status, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async cambiarEstadoProducto(id: number, activo: boolean): Promise<ApiResponse<Producto>> {
    try {
      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/catalog/${id}/status`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify({ activo }),
      });
      if (!resp.ok) {
        const errorData = await resp.json().catch(() => ({}));
        return {
          status: resp.status,
          error: errorData.detail || `Error HTTP ${resp.status} al cambiar estado.`,
        };
      }
      const raw: any = await resp.json();
      const data: Producto = {
        ...raw,
        id_producto: Number(raw.id_producto ?? raw.id),
        activo: raw.activo !== 0 && raw.activo !== false,
      };
      return { status: resp.status, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async eliminarProducto(id: number): Promise<ApiResponse<ProductoDeleteResponse>> {
    try {
      const cleanBase = this.baseUrl ? this.baseUrl.replace(/\/+$/, "") : "";
      const resp = await this.fetchWithAuth(`${cleanBase}/api/catalog/${id}`, {
        method: "DELETE",
        headers: {
          Accept: "application/json",
        },
      });
      if (!resp.ok) {
        const errorData = await resp.json().catch(() => ({}));
        return {
          status: resp.status,
          error: errorData.detail || `Error HTTP ${resp.status} al eliminar producto.`,
        };
      }
      const data: ProductoDeleteResponse = await resp.json();
      return { status: resp.status, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async registrarVenta(payload: VentaPayload): Promise<ApiResponse<VentaResponse>> {
    try {
      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/sales`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify(payload),
      });

      if (!resp.ok) {
        const errorData = await resp.json().catch(() => ({}));
        return {
          status: resp.status,
          error: errorData.detail || `Error HTTP ${resp.status} al registrar venta.`,
        };
      }

      const data: VentaResponse = await resp.json();
      return { status: resp.status, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async obtenerCargas(
    fecha?: string,
    incluirAnuladas: boolean = false,
    plataformaOrigen?: string,
    servicioDestino?: string
  ): Promise<ApiResponse<{ total: number; cargas: CargaServicio[] }>> {
    try {
      const params = new URLSearchParams();
      if (fecha) params.set("fecha", fecha);
      if (incluirAnuladas) params.set("incluir_anuladas", "true");
      if (plataformaOrigen) params.set("plataforma_origen", plataformaOrigen);
      if (servicioDestino) params.set("servicio_destino", servicioDestino);
      const qs = params.toString() ? `?${params.toString()}` : "";

      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/services${qs}`, {
        method: "GET",
        headers: { Accept: "application/json" },
      });
      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        return {
          status: resp.status,
          error: errData.detail || `Error HTTP ${resp.status} al obtener cargas.`,
        };
      }
      const data = await resp.json();
      return { status: 200, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async obtenerResumenCargas(fecha?: string): Promise<ApiResponse<ResumenServicios>> {
    try {
      const qs = fecha ? `?fecha=${encodeURIComponent(fecha)}` : "";
      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/services/summary${qs}`, {
        method: "GET",
        headers: { Accept: "application/json" },
      });
      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        return {
          status: resp.status,
          error: errData.detail || `Error HTTP ${resp.status} al obtener resumen.`,
        };
      }
      const data: ResumenServicios = await resp.json();
      return { status: 200, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async registrarCarga(carga: CargaServicioInput): Promise<ApiResponse<CargaServicio>> {
    try {
      const headers: Record<string, string> = {
        "Content-Type": "application/json",
        Accept: "application/json",
      };
      if (carga.idempotency_key) {
        headers["Idempotency-Key"] = carga.idempotency_key;
      }
      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/services`, {
        method: "POST",
        headers,
        body: JSON.stringify(carga),
      });
      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        return {
          status: resp.status,
          error: errData.detail || `Error HTTP ${resp.status} al registrar carga.`,
        };
      }
      const data: CargaServicio = await resp.json();
      return { status: resp.status, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async editarCarga(id: number, carga: CargaServicioInput): Promise<ApiResponse<CargaServicio>> {
    try {
      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/services/${id}`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify(carga),
      });
      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        return {
          status: resp.status,
          error: errData.detail || `Error HTTP ${resp.status} al editar carga.`,
        };
      }
      const data: CargaServicio = await resp.json();
      return { status: 200, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async anularCarga(id: number): Promise<ApiResponse<{ status: string; id: number; mensaje: string }>> {
    try {
      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/services/${id}/cancel`, {
        method: "POST",
        headers: { Accept: "application/json" },
      });
      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        return {
          status: resp.status,
          error: errData.detail || `Error HTTP ${resp.status} al anular carga.`,
        };
      }
      const data = await resp.json();
      return { status: 200, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async obtenerGastos(
    fecha?: string,
    tipo?: string,
    incluirAnulados: boolean = false
  ): Promise<ApiResponse<{ total: number; gastos: Gasto[] }>> {
    try {
      const params = new URLSearchParams();
      if (fecha) params.set("fecha", fecha);
      if (tipo && tipo !== "TODAS") params.set("tipo", tipo);
      if (incluirAnulados) params.set("incluir_anulados", "true");
      const qs = params.toString() ? `?${params.toString()}` : "";

      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/expenses${qs}`, {
        method: "GET",
        headers: { Accept: "application/json" },
      });
      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        return {
          status: resp.status,
          error: errData.detail || `Error HTTP ${resp.status} al obtener gastos.`,
        };
      }
      const data = await resp.json();
      return { status: 200, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async obtenerResumenGastos(fecha?: string): Promise<ApiResponse<ResumenGastos>> {
    try {
      const qs = fecha ? `?fecha=${encodeURIComponent(fecha)}` : "";
      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/expenses/summary${qs}`, {
        method: "GET",
        headers: { Accept: "application/json" },
      });
      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        return {
          status: resp.status,
          error: errData.detail || `Error HTTP ${resp.status} al obtener resumen de gastos.`,
        };
      }
      const data: ResumenGastos = await resp.json();
      return { status: 200, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async obtenerArqueoDiario(fecha?: string): Promise<ApiResponse<ArqueoDiario>> {
    try {
      const qs = fecha ? `?fecha=${encodeURIComponent(fecha)}` : "";
      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/expenses/arqueo${qs}`, {
        method: "GET",
        headers: { Accept: "application/json" },
      });
      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        return {
          status: resp.status,
          error: errData.detail || `Error HTTP ${resp.status} al obtener arqueo diario.`,
        };
      }
      const data: ArqueoDiario = await resp.json();
      return { status: 200, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async registrarGasto(gasto: GastoInput): Promise<ApiResponse<Gasto>> {
    try {
      const headers: Record<string, string> = {
        "Content-Type": "application/json",
        Accept: "application/json",
      };
      if (gasto.idempotency_key) {
        headers["Idempotency-Key"] = gasto.idempotency_key;
      }
      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/expenses`, {
        method: "POST",
        headers,
        body: JSON.stringify(gasto),
      });
      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        return {
          status: resp.status,
          error: errData.detail || `Error HTTP ${resp.status} al registrar gasto.`,
        };
      }
      const data: Gasto = await resp.json();
      return { status: resp.status, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async editarGasto(id: number, cambios: GastoInput): Promise<ApiResponse<Gasto>> {
    try {
      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/expenses/${id}`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify(cambios),
      });
      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        return {
          status: resp.status,
          error: errData.detail || `Error HTTP ${resp.status} al editar gasto.`,
        };
      }
      const data: Gasto = await resp.json();
      return { status: 200, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async anularGasto(id: number, motivo?: string): Promise<ApiResponse<{ status: string; id: number; mensaje: string }>> {
    try {
      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/expenses/${id}/cancel`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify({ motivo }),
      });
      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        return {
          status: resp.status,
          error: errData.detail || `Error HTTP ${resp.status} al anular gasto.`,
        };
      }
      const data = await resp.json();
      return { status: 200, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async obtenerCierreDiario(fecha: string): Promise<ApiResponse<CierreDiario>> {
    try {
      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/expenses/cierres/${encodeURIComponent(fecha)}`, {
        method: "GET",
        headers: { Accept: "application/json" },
      });
      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        return {
          status: resp.status,
          error: errData.detail || `Error HTTP ${resp.status} al obtener cierre diario.`,
        };
      }
      const data: CierreDiario = await resp.json();
      return { status: 200, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async registrarCierreDiario(cierre: CierreDiario): Promise<ApiResponse<CierreDiario>> {
    try {
      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/expenses/cierres`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify(cierre),
      });
      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        return {
          status: resp.status,
          error: errData.detail || `Error HTTP ${resp.status} al registrar cierre diario.`,
        };
      }
      const data: CierreDiario = await resp.json();
      return { status: 201, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async listarCierres(): Promise<ApiResponse<CierreDiario[]>> {
    try {
      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/expenses/cierres`, {
        method: "GET",
        headers: { Accept: "application/json" },
      });
      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        return {
          status: resp.status,
          error: errData.detail || `Error HTTP ${resp.status} al listar cierres.`,
        };
      }
      const data: CierreDiario[] = await resp.json();
      return { status: 200, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  // Historial de Ventas e Ítems (API Real)
  async obtenerHistorialVentas(
    fecha?: string,
    metodo_pago?: string,
    busqueda?: string,
    incluir_anuladas: boolean = true
  ): Promise<ApiResponse<VentasHistorialResponse>> {
    try {
      const params = new URLSearchParams();
      if (fecha) params.append("fecha", fecha);
      if (metodo_pago && metodo_pago !== "TODOS") params.append("metodo_pago", metodo_pago);
      if (busqueda && busqueda.trim()) params.append("busqueda", busqueda.trim());
      params.append("incluir_anuladas", String(incluir_anuladas));

      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/sales?${params.toString()}`);
      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        return {
          status: resp.status,
          error: errData.detail || `Error HTTP ${resp.status} al consultar ventas.`,
        };
      }
      const data: VentasHistorialResponse = await resp.json();
      return { status: 200, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async obtenerDetalleVenta(ticket_id: string): Promise<ApiResponse<TicketDetalle>> {
    try {
      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/sales/${encodeURIComponent(ticket_id)}`);
      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        return {
          status: resp.status,
          error: errData.detail || `Error HTTP ${resp.status} al consultar ticket.`,
        };
      }
      const raw = await resp.json();
      return { status: 200, data: normalizarTicketDetalle(raw) };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async anularVenta(
    ticket_id: string,
    motivo: string,
    usuario: string = "sistema"
  ): Promise<ApiResponse<TicketDetalle>> {
    try {
      const resp = await this.fetchWithAuth(
        `${this.baseUrl}/api/sales/${encodeURIComponent(ticket_id)}/anular`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ motivo, usuario }),
        }
      );
      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        return {
          status: resp.status,
          error: errData.detail || `Error HTTP ${resp.status} al anular venta.`,
        };
      }
      const raw = await resp.json();
      return { status: 200, data: normalizarTicketDetalle(raw) };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async editarItemVenta(
    ticket_id: string,
    item_id: number,
    nueva_cantidad?: number,
    nuevo_subtotal?: number,
    usuario: string = "sistema"
  ): Promise<ApiResponse<TicketDetalle>> {
    try {
      const resp = await this.fetchWithAuth(
        `${this.baseUrl}/api/sales/${encodeURIComponent(ticket_id)}/items/${item_id}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ nueva_cantidad, nuevo_subtotal, usuario }),
        }
      );
      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        return {
          status: resp.status,
          error: errData.detail || `Error HTTP ${resp.status} al editar ítem.`,
        };
      }
      const raw = await resp.json();
      return { status: 200, data: normalizarTicketDetalle(raw) };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  // Preventistas y Pedidos (Real API)
  async obtenerProveedores(): Promise<ApiResponse<string[]>> {
    try {
      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/preventistas/proveedores`);
      if (!resp.ok) {
        return { status: resp.status, error: "Error al consultar proveedores" };
      }
      const raw = await resp.json();
      const lista = Array.isArray(raw)
        ? raw
        : (Array.isArray(raw?.proveedores) ? raw.proveedores : []);
      const data: string[] = lista
        .map((p: unknown) =>
          typeof p === "string" ? p : String((p as { nombre?: string })?.nombre || "")
        )
        .filter(Boolean);
      return { status: 200, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async obtenerProductosPorProveedor(proveedor: string): Promise<ApiResponse<Producto[]>> {
    try {
      const resp = await this.fetchWithAuth(
        `${this.baseUrl}/api/preventistas/productos?proveedor=${encodeURIComponent(proveedor)}`
      );
      if (!resp.ok) {
        return { status: resp.status, error: "Error al consultar productos de proveedor" };
      }
      const rawList: any[] = await resp.json();
      const data: Producto[] = (Array.isArray(rawList) ? rawList : []).map((p: any) => ({
        ...p,
        id_producto: Number(p.id_producto ?? p.id),
        costo: Number(p.costo ?? p.costo_unitario ?? 0),
        precio_venta: Number(p.precio_venta ?? p.precio_dia ?? p.precio ?? 0),
        activo: p.activo !== 0 && p.activo !== false,
      }));
      return { status: 200, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async actualizarPreciosPreventista(
    proveedor: string,
    cambios: Array<{ id_producto: number; costo: number; precio_venta: number }>
  ): Promise<ApiResponse<{ actualizados: number; mensaje: string }>> {
    try {
      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/preventistas/precios`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ proveedor, cambios }),
      });
      if (!resp.ok) {
        const errorData = await resp.json().catch(() => ({ detail: "Error" }));
        return { status: resp.status, error: errorData.detail || "Error al actualizar precios" };
      }
      const data = await resp.json();
      return { status: 200, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async altaRapidaProductoPreventista(datos: ProductoInput): Promise<ApiResponse<Producto>> {
    try {
      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/preventistas/productos/alta-rapida`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(datos),
      });
      if (!resp.ok) {
        const errorData = await resp.json().catch(() => ({ detail: "Error" }));
        return { status: resp.status, error: errorData.detail || "Error al dar de alta producto" };
      }
      const raw: any = await resp.json();
      const data: Producto = {
        ...raw,
        id_producto: Number(raw.id_producto ?? raw.id),
        costo: Number(raw.costo ?? raw.costo_unitario ?? 0),
        precio_venta: Number(raw.precio_venta ?? raw.precio_dia ?? raw.precio ?? 0),
        activo: raw.activo !== 0 && raw.activo !== false,
      };
      return { status: 201, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async obtenerPedidos(filtros?: {
    proveedor?: string;
    fecha?: string;
    estado?: string;
  }): Promise<ApiResponse<PedidoProveedor[]>> {
    try {
      const params = new URLSearchParams();
      if (filtros?.proveedor) params.append("proveedor", filtros.proveedor);
      if (filtros?.fecha) params.append("fecha", filtros.fecha);
      if (filtros?.estado && filtros.estado !== "TODOS") params.append("estado", filtros.estado);

      const query = params.toString() ? `?${params.toString()}` : "";
      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/preventistas/pedidos${query}`);
      if (!resp.ok) {
        return { status: resp.status, error: "Error al consultar pedidos" };
      }
      const data: PedidoProveedor[] = await resp.json();
      return { status: 200, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async obtenerDetallePedido(id: string): Promise<ApiResponse<PedidoProveedorDetalle>> {
    try {
      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/preventistas/pedidos/${encodeURIComponent(id)}`);
      if (!resp.ok) {
        return { status: resp.status, error: "Error al consultar detalle del pedido" };
      }
      const data: PedidoProveedorDetalle = await resp.json();
      return { status: 200, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async crearPedido(datos: {
    proveedor: string;
    fecha?: string;
    observaciones?: string;
    items: PedidoItem[];
  }): Promise<ApiResponse<PedidoProveedorDetalle>> {
    try {
      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/preventistas/pedidos`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(datos),
      });
      if (!resp.ok) {
        const errorData = await resp.json().catch(() => ({ detail: "Error" }));
        return { status: resp.status, error: errorData.detail || "Error al crear pedido" };
      }
      const data: PedidoProveedorDetalle = await resp.json();
      return { status: 201, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async cambiarEstadoPedido(
    id: string,
    nuevoEstado: "PENDIENTE" | "RECIBIDO" | "CANCELADO",
    observaciones?: string
  ): Promise<ApiResponse<PedidoProveedor>> {
    try {
      const resp = await this.fetchWithAuth(
        `${this.baseUrl}/api/preventistas/pedidos/${encodeURIComponent(id)}/estado`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ estado: nuevoEstado, observaciones }),
        }
      );
      if (!resp.ok) {
        const errorData = await resp.json().catch(() => ({ detail: "Error" }));
        return { status: resp.status, error: errorData.detail || "Error al cambiar estado" };
      }
      const data: PedidoProveedor = await resp.json();
      return { status: 200, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async cancelarPedido(id: string, motivo: string): Promise<ApiResponse<PedidoProveedor>> {
    try {
      const resp = await this.fetchWithAuth(
        `${this.baseUrl}/api/preventistas/pedidos/${encodeURIComponent(id)}/cancelar`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ motivo }),
        }
      );
      if (!resp.ok) {
        const errorData = await resp.json().catch(() => ({ detail: "Error" }));
        return { status: resp.status, error: errorData.detail || "Error al cancelar pedido" };
      }
      const data: PedidoProveedor = await resp.json();
      return { status: 200, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async obtenerMetricasDashboard(
    desde?: string,
    hasta?: string,
    periodo?: PeriodoTipo
  ): Promise<ApiResponse<MetricasDashboard>> {
    try {
      const params = new URLSearchParams();
      if (desde) params.append("desde", desde);
      if (hasta) params.append("hasta", hasta);
      if (periodo) params.append("periodo", periodo);
      const q = params.toString() ? `?${params.toString()}` : "";
      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/reports/dashboard${q}`, {
        method: "GET",
        headers: { Accept: "application/json" },
      });
      if (!resp.ok) {
        const errorData = await resp.json().catch(() => ({ detail: "Error" }));
        return { status: resp.status, error: errorData.detail || "Error al obtener métricas de dashboard" };
      }
      const data: MetricasDashboard = await resp.json();
      return { status: 200, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async verificarPreparacionSistema(): Promise<ApiResponse<SystemReadiness>> {
    try {
      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/system/readiness`, {
        method: "GET",
        headers: { Accept: "application/json" },
      });
      if (!resp.ok) {
        const errorData = await resp.json().catch(() => ({ detail: "Error" }));
        return {
          status: resp.status,
          error: errorData.detail || `Error HTTP ${resp.status} al verificar preparación del sistema`,
        };
      }
      const data: SystemReadiness = await resp.json();
      return { status: 200, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async obtenerResumenHistorico(): Promise<ApiResponse<HistoricalSummary>> {
    try {
      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/historical/summary`, {
        method: "GET",
        headers: { Accept: "application/json" },
      });
      if (!resp.ok) {
        const errorData = await resp.json().catch(() => ({ detail: "Error" }));
        return {
          status: resp.status,
          error: errorData.detail || `Error HTTP ${resp.status} al obtener resumen histórico`,
        };
      }
      const data: HistoricalSummary = await resp.json();
      return { status: 200, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async obtenerVentasHistoricas(params?: {
    desde?: string;
    hasta?: string;
    metodo_pago?: string;
    agrupacion?: string;
    limit?: number;
    offset?: number;
  }): Promise<ApiResponse<HistoricalSales>> {
    try {
      const q = new URLSearchParams();
      if (params?.desde) q.append("desde", params.desde);
      if (params?.hasta) q.append("hasta", params.hasta);
      if (params?.metodo_pago) q.append("metodo_pago", params.metodo_pago);
      if (params?.agrupacion) q.append("agrupacion", params.agrupacion);
      if (params?.limit !== undefined) q.append("limit", String(params.limit));
      if (params?.offset !== undefined) q.append("offset", String(params.offset));
      const qs = q.toString() ? `?${q.toString()}` : "";

      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/historical/sales${qs}`, {
        method: "GET",
        headers: { Accept: "application/json" },
      });
      if (!resp.ok) {
        const errorData = await resp.json().catch(() => ({ detail: "Error" }));
        return {
          status: resp.status,
          error: errorData.detail || `Error HTTP ${resp.status} al obtener ventas históricas`,
        };
      }
      const data: HistoricalSales = await resp.json();
      return { status: 200, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async obtenerProductosHistoricos(params?: {
    desde?: string;
    hasta?: string;
    depurado?: boolean;
    limit?: number;
    offset?: number;
  }): Promise<ApiResponse<HistoricalProducts>> {
    try {
      const q = new URLSearchParams();
      if (params?.desde) q.append("desde", params.desde);
      if (params?.hasta) q.append("hasta", params.hasta);
      if (params?.depurado !== undefined) q.append("depurado", String(params.depurado));
      if (params?.limit !== undefined) q.append("limit", String(params.limit));
      if (params?.offset !== undefined) q.append("offset", String(params.offset));
      const qs = q.toString() ? `?${q.toString()}` : "";

      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/historical/products${qs}`, {
        method: "GET",
        headers: { Accept: "application/json" },
      });
      if (!resp.ok) {
        const errorData = await resp.json().catch(() => ({ detail: "Error" }));
        return {
          status: resp.status,
          error: errorData.detail || `Error HTTP ${resp.status} al consultar productos históricos`,
        };
      }
      const data: HistoricalProducts = await resp.json();
      return { status: 200, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async obtenerServiciosHistoricos(params?: {
    desde?: string;
    hasta?: string;
    excluir_duplicados?: boolean;
  }): Promise<ApiResponse<HistoricalServices>> {
    try {
      const q = new URLSearchParams();
      if (params?.desde) q.append("desde", params.desde);
      if (params?.hasta) q.append("hasta", params.hasta);
      if (params?.excluir_duplicados !== undefined) q.append("excluir_duplicados", String(params.excluir_duplicados));
      const qs = q.toString() ? `?${q.toString()}` : "";

      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/historical/services${qs}`, {
        method: "GET",
        headers: { Accept: "application/json" },
      });
      if (!resp.ok) {
        const errorData = await resp.json().catch(() => ({ detail: "Error" }));
        return {
          status: resp.status,
          error: errorData.detail || `Error HTTP ${resp.status} al consultar servicios históricos`,
        };
      }
      const data: HistoricalServices = await resp.json();
      return { status: 200, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async buscarTicketsHistoricos(params?: {
    query?: string;
    fecha?: string;
    limit?: number;
    offset?: number;
  }): Promise<ApiResponse<HistoricalTicketsSearch>> {
    try {
      const q = new URLSearchParams();
      if (params?.query) q.append("query", params.query);
      if (params?.fecha) q.append("fecha", params.fecha);
      if (params?.limit !== undefined) q.append("limit", String(params.limit));
      if (params?.offset !== undefined) q.append("offset", String(params.offset));
      const qs = q.toString() ? `?${q.toString()}` : "";

      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/historical/tickets${qs}`, {
        method: "GET",
        headers: { Accept: "application/json" },
      });
      if (!resp.ok) {
        const errorData = await resp.json().catch(() => ({ detail: "Error" }));
        return {
          status: resp.status,
          error: errorData.detail || `Error HTTP ${resp.status} al buscar tickets históricos`,
        };
      }
      const data: HistoricalTicketsSearch = await resp.json();
      return { status: 200, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async obtenerTicketDetalleHistorico(ticketId: string): Promise<ApiResponse<HistoricalTicketDetalle>> {
    try {
      const resp = await this.fetchWithAuth(
        `${this.baseUrl}/api/historical/tickets/${encodeURIComponent(ticketId)}`,
        {
          method: "GET",
          headers: { Accept: "application/json" },
        }
      );
      if (!resp.ok) {
        const errorData = await resp.json().catch(() => ({ detail: "Error" }));
        return {
          status: resp.status,
          error: errorData.detail || `Error HTTP ${resp.status} al obtener detalle del ticket histórico`,
        };
      }
      const data: HistoricalTicketDetalle = await resp.json();
      return { status: 200, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }

  async compararPeriodosHistoricos(params: {
    desde_a: string;
    hasta_a: string;
    desde_b: string;
    hasta_b: string;
  }): Promise<ApiResponse<HistoricalCompare>> {
    try {
      const q = new URLSearchParams({
        desde_a: params.desde_a,
        hasta_a: params.hasta_a,
        desde_b: params.desde_b,
        hasta_b: params.hasta_b,
      });
      const resp = await this.fetchWithAuth(`${this.baseUrl}/api/historical/compare?${q.toString()}`, {
        method: "GET",
        headers: { Accept: "application/json" },
      });
      if (!resp.ok) {
        const errorData = await resp.json().catch(() => ({ detail: "Error" }));
        return {
          status: resp.status,
          error: errorData.detail || `Error HTTP ${resp.status} al comparar períodos históricos`,
        };
      }
      const data: HistoricalCompare = await resp.json();
      return { status: 200, data };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error de red";
      return { status: 0, error: `Error de conexión: ${message}` };
    }
  }
}

// Configuración global del cliente API
// Habilitado por defecto en MODO DEMO seguro
export const IS_STATIC_DEMO_ONLY = import.meta.env.VITE_STATIC_DEMO_ONLY === "true";

// Modo protegido de prueba conectada de solo lectura (smoke testing DEV)
// Dominado estrictamente por IS_STATIC_DEMO_ONLY (si la demo estática está activa, esto es siempre false)
export const IS_READ_ONLY_SMOKE_MODE =
  !IS_STATIC_DEMO_ONLY && import.meta.env.VITE_READ_ONLY_CONNECTED_SMOKE === "true";

export function isReadOnlySmokeActive(client: IApiClient = getApiClient()): boolean {
  if (IS_STATIC_DEMO_ONLY) {
    return false;
  }
  if (client instanceof RealApiClient) {
    return client.isReadOnlySmoke();
  }
  return IS_READ_ONLY_SMOKE_MODE;
}

let currentClient: IApiClient = new MockApiClient();

export function getApiClient(): IApiClient {
  return currentClient;
}

export function setApiClient(client: IApiClient): void {
  if (IS_STATIC_DEMO_ONLY && !(client instanceof MockApiClient)) {
    console.warn("Modo Demo Estricto activo: No se permite modificar el cliente API simulado.");
    return;
  }
  currentClient = client;
}

export const AUTH_STORAGE_KEY = "genaro_pos_auth_token";
export const AUTH_USER_KEY = "genaro_pos_auth_user";
export const CLIENT_MODE_STORAGE_KEY = "genaro_pos_client_mode";

export function guardarSesionLocal(token: string, user: AuthUser): void {
  if (typeof window !== "undefined" && window.sessionStorage) {
    sessionStorage.setItem(AUTH_STORAGE_KEY, token);
    sessionStorage.setItem(AUTH_USER_KEY, JSON.stringify(user));
    if (!IS_STATIC_DEMO_ONLY) {
      sessionStorage.setItem(CLIENT_MODE_STORAGE_KEY, "real");
    }
  }
  if (currentClient.setAuthToken) {
    currentClient.setAuthToken(token);
  }
}

export function obtenerSesionGuardada(): { token: string; user: AuthUser } | null {
  if (typeof window === "undefined" || !window.sessionStorage) {
    return null;
  }
  const token = sessionStorage.getItem(AUTH_STORAGE_KEY);
  const userJson = sessionStorage.getItem(AUTH_USER_KEY);
  if (!token || !userJson) {
    return null;
  }
  try {
    const user = JSON.parse(userJson) as AuthUser;
    return { token, user };
  } catch {
    return null;
  }
}

export function debeIniciarEnModoConectado(): boolean {
  if (IS_STATIC_DEMO_ONLY) {
    return false;
  }
  const sesion = obtenerSesionGuardada();
  if (sesion?.token) {
    return true;
  }
  if (typeof window !== "undefined" && window.sessionStorage) {
    return sessionStorage.getItem(CLIENT_MODE_STORAGE_KEY) === "real";
  }
  return false;
}

export function cerrarSesion(): void {
  if (typeof window !== "undefined" && window.sessionStorage) {
    sessionStorage.removeItem(AUTH_STORAGE_KEY);
    sessionStorage.removeItem(AUTH_USER_KEY);
  }
  if (currentClient.setAuthToken) {
    currentClient.setAuthToken(null);
  }
}

export const DEFAULT_API_BASE_URL = (import.meta.env.VITE_API_URL as string) || "";

export function activarModoReal(baseUrl: string = DEFAULT_API_BASE_URL): void {
  const targetUrl = baseUrl || DEFAULT_API_BASE_URL;
  if (IS_STATIC_DEMO_ONLY) {
    console.warn("Modo Demo Estricto activo: No se permite activar la API real.");
    return;
  }
  if (typeof window !== "undefined" && window.sessionStorage) {
    sessionStorage.setItem(CLIENT_MODE_STORAGE_KEY, "real");
  }
  const realClient = new RealApiClient(targetUrl);
  const sesion = obtenerSesionGuardada();
  if (sesion?.token) {
    realClient.setAuthToken(sesion.token);
  }
  currentClient = realClient;
}

export function activarModoDemo(): void {
  if (typeof window !== "undefined" && window.sessionStorage) {
    sessionStorage.setItem(CLIENT_MODE_STORAGE_KEY, "demo");
  }
  currentClient = new MockApiClient();
}
