import { Producto, ProductoDeleteResponse, ProductoInput } from "../types/catalog";
import {
  TicketDetalle,
  TicketResumen,
  VentaAuditoria,
  VentaLocal,
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
import {
  PeriodoTipo,
  MetricasDashboard,
  PuntoSerieTemporal,
} from "../types/dashboard";

const DB_NAME = "genaro_pos_db";
const DB_VERSION = 7;

const STORE_VENTAS = "ventas_queue";
const STORE_VENTAS_DEMO = "ventas_demo";
const STORE_CATALOGO = "catalogo_cache";
const STORE_META = "meta";
const STORE_CATALOGO_DEMO = "catalogo_demo";
const STORE_SERVICIOS_DEMO = "servicios_demo";
const STORE_GASTOS_DEMO = "gastos_demo";
const STORE_CIERRES_DEMO = "cierres_demo";
const STORE_PEDIDOS_DEMO = "pedidos_demo";
const STORE_SERVICIOS_QUEUE = "servicios_queue";
const STORE_GASTOS_QUEUE = "gastos_queue";

// ==============================================================================
// REGISTROS ESTÁTICOS DE DEMOSTRACIÓN Y MIGRACIÓN SEGURA
// ==============================================================================

export const VENTAS_DEMO_INICIALES: VentaLocal[] = [
  {
    ticket_id: "T-20261024-001",
    creado_en: "2026-10-24T10:15:00Z",
    estado: "sincronizada",
    intentos: 0,
    anulado: false,
    payload: {
      ticket: {
        ticket_id: "T-20261024-001",
        fecha: "24/10/2026 10:15:00",
        total_venta: 3500,
        monto_efectivo: 3500,
        monto_transf: 0,
        metodo_pago: "EFECTIVO",
        anulado: false,
      },
      items: [
        {
          ticket_id: "T-20261024-001",
          producto: "Gaseosa Cola 500ml",
          cantidad: 2,
          unidad: "Unidad",
          precio_unit: 1000,
          subtotal: 2000,
          metodo_pago: "EFECTIVO",
        },
        {
          ticket_id: "T-20261024-001",
          producto: "Alfajor Triple Chocolate",
          cantidad: 1,
          unidad: "Unidad",
          precio_unit: 1500,
          subtotal: 1500,
          metodo_pago: "EFECTIVO",
        },
      ],
    },
    auditoria: [],
  },
  {
    ticket_id: "T-20261024-002",
    creado_en: "2026-10-24T11:45:00Z",
    estado: "sincronizada",
    intentos: 0,
    anulado: false,
    payload: {
      ticket: {
        ticket_id: "T-20261024-002",
        fecha: "24/10/2026 11:45:00",
        total_venta: 5000,
        monto_efectivo: 0,
        monto_transf: 5000,
        metodo_pago: "TRANSFERENCIA",
        anulado: false,
      },
      items: [
        {
          ticket_id: "T-20261024-002",
          producto: "Cigarrillos Box 20",
          cantidad: 1,
          unidad: "Unidad",
          precio_unit: 4000,
          subtotal: 4000,
          metodo_pago: "TRANSFERENCIA",
        },
        {
          ticket_id: "T-20261024-002",
          producto: "Chicle Menta",
          cantidad: 2,
          unidad: "Unidad",
          precio_unit: 500,
          subtotal: 1000,
          metodo_pago: "TRANSFERENCIA",
        },
      ],
    },
    auditoria: [],
  },
  {
    ticket_id: "T-20261024-003",
    creado_en: "2026-10-24T13:20:00Z",
    estado: "sincronizada",
    intentos: 0,
    anulado: false,
    payload: {
      ticket: {
        ticket_id: "T-20261024-003",
        fecha: "24/10/2026 13:20:00",
        total_venta: 4200,
        monto_efectivo: 2200,
        monto_transf: 2000,
        metodo_pago: "MIXTO",
        anulado: false,
      },
      items: [
        {
          ticket_id: "T-20261024-003",
          producto: "Galletitas Dulces",
          cantidad: 2,
          unidad: "Unidad",
          precio_unit: 1100,
          subtotal: 2200,
          metodo_pago: "MIXTO",
        },
        {
          ticket_id: "T-20261024-003",
          producto: "Agua Mineral 1.5L",
          cantidad: 2,
          unidad: "Unidad",
          precio_unit: 1000,
          subtotal: 2000,
          metodo_pago: "MIXTO",
        },
      ],
    },
    auditoria: [],
  },
];

/**
 * Valida si un ticket coincide inequívocamente con alguno de los registros estáticos
 * conocidos de VENTAS_DEMO_INICIALES.
 */
export function esTicketDemoConocido(ticket: VentaLocal): boolean {
  if (!ticket || !ticket.ticket_id || !ticket.payload || !ticket.payload.ticket) {
    return false;
  }
  const ref = VENTAS_DEMO_INICIALES.find((d) => d.ticket_id === ticket.ticket_id);
  if (!ref) return false;

  const t = ticket.payload.ticket;
  const rt = ref.payload.ticket;

  return (
    t.total_venta === rt.total_venta &&
    t.metodo_pago === rt.metodo_pago &&
    (t.monto_efectivo || 0) === (rt.monto_efectivo || 0) &&
    (t.monto_transf || 0) === (rt.monto_transf || 0) &&
    ticket.creado_en === ref.creado_en
  );
}

/**
 * Migra transaccionalmente los tickets demo conocidos desde ventas_queue hacia ventas_demo.
 * Si un ticket no coincide con los ejemplos conocidos, se conserva estrictamente en ventas_queue.
 */
export async function migrarTicketsDemoDeColaOperativa(db: IDBDatabase): Promise<{
  migrados: number;
  conservados: number;
}> {
  if (
    !db.objectStoreNames.contains(STORE_VENTAS) ||
    !db.objectStoreNames.contains(STORE_VENTAS_DEMO)
  ) {
    return { migrados: 0, conservados: 0 };
  }

  return new Promise((resolve, reject) => {
    const tx = db.transaction([STORE_VENTAS, STORE_VENTAS_DEMO], "readwrite");
    const storeQueue = tx.objectStore(STORE_VENTAS);
    const storeDemo = tx.objectStore(STORE_VENTAS_DEMO);

    let migrados = 0;
    let conservados = 0;

    const getAllReq = storeQueue.getAll();
    getAllReq.onsuccess = () => {
      const tickets = (getAllReq.result || []) as VentaLocal[];
      for (const t of tickets) {
        if (esTicketDemoConocido(t)) {
          storeDemo.put(t);
          storeQueue.delete(t.ticket_id);
          migrados++;
        } else {
          conservados++;
        }
      }
    };
    getAllReq.onerror = () => reject(getAllReq.error);

    tx.oncomplete = () => resolve({ migrados, conservados });
    tx.onerror = () => reject(tx.error);
  });
}

let dbPromise: Promise<IDBDatabase> | null = null;

export function openDB(): Promise<IDBDatabase> {
  if (dbPromise) return dbPromise;

  dbPromise = new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);

    request.onupgradeneeded = (event) => {
      const db = (event.target as IDBOpenDBRequest).result;

      // 1. Cola persistente de ventas
      if (!db.objectStoreNames.contains(STORE_VENTAS)) {
        const store = db.createObjectStore(STORE_VENTAS, { keyPath: "ticket_id" });
        store.createIndex("estado", "estado", { unique: false });
        store.createIndex("creado_en", "creado_en", { unique: false });
      }

      // 2. Caché local de catálogo remoto
      if (!db.objectStoreNames.contains(STORE_CATALOGO)) {
        db.createObjectStore(STORE_CATALOGO, { keyPath: "id_producto" });
      }

      // 3. Metadatos (ej. fecha de última actualización de catálogo)
      if (!db.objectStoreNames.contains(STORE_META)) {
        db.createObjectStore(STORE_META, { keyPath: "clave" });
      }

      // 4. Catálogo demo local editable e independiente (Fase 14.3)
      if (!db.objectStoreNames.contains(STORE_CATALOGO_DEMO)) {
        const storeDemo = db.createObjectStore(STORE_CATALOGO_DEMO, { keyPath: "id_producto" });
        storeDemo.createIndex("activo", "activo", { unique: false });
      }

      // 5. Historial de Cargas y Servicios Demo local e independiente (Fase 14.4)
      if (!db.objectStoreNames.contains(STORE_SERVICIOS_DEMO)) {
        const storeServicios = db.createObjectStore(STORE_SERVICIOS_DEMO, {
          keyPath: "id",
          autoIncrement: true,
        });
        storeServicios.createIndex("anulado", "anulado", { unique: false });
        storeServicios.createIndex("fecha", "fecha", { unique: false });
        storeServicios.createIndex("servicio", "servicio", { unique: false });
      }

      // 6. Gastos y Retiros Demo local e independiente (Fase 14.5)
      if (!db.objectStoreNames.contains(STORE_GASTOS_DEMO)) {
        const storeGastos = db.createObjectStore(STORE_GASTOS_DEMO, {
          keyPath: "id",
          autoIncrement: true,
        });
        storeGastos.createIndex("anulado", "anulado", { unique: false });
        storeGastos.createIndex("fecha", "fecha", { unique: false });
        storeGastos.createIndex("tipo", "tipo", { unique: false });
        storeGastos.createIndex("metodo_pago", "metodo_pago", { unique: false });
      }

      // 7. Cierres Diarios Demo (Fase 14.5)
      if (!db.objectStoreNames.contains(STORE_CIERRES_DEMO)) {
        db.createObjectStore(STORE_CIERRES_DEMO, { keyPath: "fecha" });
      }

      // 8. Pedidos a Proveedores Demo (Fase 14.7)
      if (!db.objectStoreNames.contains(STORE_PEDIDOS_DEMO)) {
        const storePedidos = db.createObjectStore(STORE_PEDIDOS_DEMO, { keyPath: "id" });
        storePedidos.createIndex("proveedor", "proveedor", { unique: false });
        storePedidos.createIndex("fecha", "fecha", { unique: false });
        storePedidos.createIndex("estado", "estado", { unique: false });
      }

      // 9. Ventas Demo locales e independientes (Fase 14.8)
      if (!db.objectStoreNames.contains(STORE_VENTAS_DEMO)) {
        const storeVentasDemo = db.createObjectStore(STORE_VENTAS_DEMO, { keyPath: "ticket_id" });
        storeVentasDemo.createIndex("estado", "estado", { unique: false });
        storeVentasDemo.createIndex("creado_en", "creado_en", { unique: false });
      }

      // 10. Cola offline persistente de Servicios para Modo Conectado (Sección 21)
      if (!db.objectStoreNames.contains(STORE_SERVICIOS_QUEUE)) {
        const storeSrvQueue = db.createObjectStore(STORE_SERVICIOS_QUEUE, {
          keyPath: "idempotency_key",
        });
        storeSrvQueue.createIndex("estado", "estado", { unique: false });
        storeSrvQueue.createIndex("creado_en", "creado_en", { unique: false });
      }

      // 11. Cola offline persistente de Gastos para Modo Conectado (Sección 21)
      if (!db.objectStoreNames.contains(STORE_GASTOS_QUEUE)) {
        const storeGasQueue = db.createObjectStore(STORE_GASTOS_QUEUE, {
          keyPath: "idempotency_key",
        });
        storeGasQueue.createIndex("estado", "estado", { unique: false });
        storeGasQueue.createIndex("creado_en", "creado_en", { unique: false });
      }
    };

    request.onsuccess = async () => {
      const db = request.result;
      try {
        await migrarTicketsDemoDeColaOperativa(db);
      } catch (err) {
        console.warn("Advertencia en migración transaccional de ventas demo:", err);
      }
      resolve(db);
    };
    request.onerror = () => reject(request.error);
  });

  return dbPromise;
}

export function resetDBInstance(): void {
  dbPromise = null;
}

// ==============================================================================
// OPERACIONES SOBRE LA COLA DE VENTAS
// ==============================================================================

export async function guardarVentaLocal(venta: VentaLocal): Promise<void> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_VENTAS, "readwrite");
    const store = tx.objectStore(STORE_VENTAS);
    const req = store.put(venta);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
  });
}

export async function obtenerVentaLocal(ticket_id: string): Promise<VentaLocal | undefined> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_VENTAS, "readonly");
    const store = tx.objectStore(STORE_VENTAS);
    const req = store.get(ticket_id);
    req.onsuccess = () => resolve(req.result as VentaLocal | undefined);
    req.onerror = () => reject(req.error);
  });
}

export async function listarVentasLocales(): Promise<VentaLocal[]> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_VENTAS, "readonly");
    const store = tx.objectStore(STORE_VENTAS);
    const req = store.getAll();
    req.onsuccess = () => {
      const items = (req.result || []) as VentaLocal[];
      // Ordenar por fecha descendente
      items.sort((a, b) => new Date(b.creado_en).getTime() - new Date(a.creado_en).getTime());
      resolve(items);
    };
    req.onerror = () => reject(req.error);
  });
}

export async function actualizarEstadoVenta(
  ticket_id: string,
  updates: Partial<VentaLocal>
): Promise<VentaLocal | undefined> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_VENTAS, "readwrite");
    const store = tx.objectStore(STORE_VENTAS);
    const getReq = store.get(ticket_id);

    getReq.onsuccess = () => {
      const actual = getReq.result as VentaLocal | undefined;
      if (!actual) {
        resolve(undefined);
        return;
      }
      const nuevo: VentaLocal = { ...actual, ...updates };
      const putReq = store.put(nuevo);
      putReq.onsuccess = () => resolve(nuevo);
      putReq.onerror = () => reject(putReq.error);
    };
    getReq.onerror = () => reject(getReq.error);
  });
}

// ==============================================================================
// OPERACIONES SOBRE VENTAS DEMO (STORE_VENTAS_DEMO)
// ==============================================================================

export async function guardarVentaDemo(venta: VentaLocal): Promise<void> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_VENTAS_DEMO, "readwrite");
    const store = tx.objectStore(STORE_VENTAS_DEMO);
    const req = store.put(venta);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
  });
}

export async function obtenerVentaDemo(ticket_id: string): Promise<VentaLocal | undefined> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_VENTAS_DEMO, "readonly");
    const store = tx.objectStore(STORE_VENTAS_DEMO);
    const req = store.get(ticket_id);
    req.onsuccess = () => resolve(req.result as VentaLocal | undefined);
    req.onerror = () => reject(req.error);
  });
}

export async function listarVentasDemo(): Promise<VentaLocal[]> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_VENTAS_DEMO, "readonly");
    const store = tx.objectStore(STORE_VENTAS_DEMO);
    const req = store.getAll();
    req.onsuccess = () => {
      const items = (req.result || []) as VentaLocal[];
      // Ordenar por fecha descendente
      items.sort((a, b) => new Date(b.creado_en).getTime() - new Date(a.creado_en).getTime());
      resolve(items);
    };
    req.onerror = () => reject(req.error);
  });
}

export async function actualizarEstadoVentaDemo(
  ticket_id: string,
  updates: Partial<VentaLocal>
): Promise<VentaLocal | undefined> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_VENTAS_DEMO, "readwrite");
    const store = tx.objectStore(STORE_VENTAS_DEMO);
    const getReq = store.get(ticket_id);

    getReq.onsuccess = () => {
      const actual = getReq.result as VentaLocal | undefined;
      if (!actual) {
        resolve(undefined);
        return;
      }
      const nuevo: VentaLocal = { ...actual, ...updates };
      const putReq = store.put(nuevo);
      putReq.onsuccess = () => resolve(nuevo);
      putReq.onerror = () => reject(putReq.error);
    };
    getReq.onerror = () => reject(getReq.error);
  });
}

export async function inicializarVentasDemoSiVacio(): Promise<void> {
  const actuales = await listarVentasDemo();
  if (actuales.length === 0) {
    for (const v of VENTAS_DEMO_INICIALES) {
      await guardarVentaDemo(v);
    }
  }
}

export async function listarVentasHistorialDemo(filtros?: {
  fecha?: string;
  metodo_pago?: string;
  busqueda?: string;
  incluir_anuladas?: boolean;
}): Promise<TicketResumen[]> {
  await inicializarVentasDemoSiVacio();
  const lista = await listarVentasDemo();

  let resultado = lista.map((v) => {
    const isAnulado = Boolean(v.anulado || v.payload.ticket.anulado);
    const resumenItems = v.payload.items
      .map((it) => `${it.producto} x${it.cantidad}`)
      .join(", ");

    return {
      ticket_id: v.ticket_id,
      fecha: v.payload.ticket.fecha || fechaArgentinaTexto(new Date(v.creado_en)),
      fecha_hora_utc: v.creado_en,
      total_venta: v.payload.ticket.total_venta,
      monto_efectivo: v.payload.ticket.monto_efectivo,
      monto_transferencia: v.payload.ticket.monto_transf,
      monto_transf: v.payload.ticket.monto_transf,
      metodo_pago: v.payload.ticket.metodo_pago,
      cantidad_items: v.payload.items.length,
      anulado: isAnulado,
      motivo_anulacion: v.motivo_anulacion || v.payload.ticket.motivo_anulacion,
      resumen_items: resumenItems,
    } as TicketResumen;
  });

  if (filtros) {
    if (filtros.incluir_anuladas === false) {
      resultado = resultado.filter((t) => !t.anulado);
    }

    if (filtros.metodo_pago && filtros.metodo_pago !== "TODOS") {
      resultado = resultado.filter(
        (t) => t.metodo_pago.toUpperCase() === filtros.metodo_pago?.toUpperCase()
      );
    }

    if (filtros.fecha) {
      const targetDate = filtros.fecha.trim();
      resultado = resultado.filter((t) => {
        try {
          if (t.fecha_hora_utc?.startsWith(targetDate)) return true;
          const [d, m, y] = t.fecha.split(" ")[0].split("/").map(Number);
          const tDate = `${String(y).padStart(4, "0")}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
          return tDate === targetDate;
        } catch {
          return false;
        }
      });
    }

    if (filtros.busqueda && filtros.busqueda.trim()) {
      const q = filtros.busqueda.trim().toLowerCase();
      resultado = resultado.filter((t) => {
        const matchId = t.ticket_id.toLowerCase().includes(q);
        const matchItems = t.resumen_items?.toLowerCase().includes(q) || false;
        return matchId || matchItems;
      });
    }
  }

  return resultado;
}

export async function obtenerDetalleTicketDemo(
  ticket_id: string
): Promise<TicketDetalle | undefined> {
  await inicializarVentasDemoSiVacio();
  const v = await obtenerVentaDemo(ticket_id);
  if (!v) return undefined;

  const isAnulado = Boolean(v.anulado || v.payload.ticket.anulado);

  return {
    ticket_id: v.ticket_id,
    fecha: v.payload.ticket.fecha || fechaArgentinaTexto(new Date(v.creado_en)),
    fecha_hora_utc: v.creado_en,
    total_venta: v.payload.ticket.total_venta,
    monto_efectivo: v.payload.ticket.monto_efectivo,
    monto_transferencia: v.payload.ticket.monto_transf,
    monto_transf: v.payload.ticket.monto_transf,
    metodo_pago: v.payload.ticket.metodo_pago,
    anulado: isAnulado,
    motivo_anulacion: v.motivo_anulacion || v.payload.ticket.motivo_anulacion,
    items: v.payload.items,
    auditoria: v.auditoria || [],
  };
}

export async function anularVentaDemo(
  ticket_id: string,
  motivo: string = "Anulación solicitada por usuario",
  usuario: string = "sistema"
): Promise<TicketDetalle> {
  const actual = await obtenerVentaDemo(ticket_id);
  if (!actual) {
    throw new Error(`El ticket '${ticket_id}' no existe.`);
  }

  const prevTotal = actual.payload.ticket.total_venta;
  const nuevoAuditoria: VentaAuditoria = {
    id: Date.now(),
    ticket_id,
    accion: "ANULACION",
    motivo,
    usuario,
    datos_previos_json: JSON.stringify({
      total_venta: prevTotal,
      anulado: Boolean(actual.anulado),
    }),
    datos_nuevos_json: JSON.stringify({
      total_venta: prevTotal,
      anulado: true,
      motivo_anulacion: motivo,
    }),
    creado_en_utc: new Date().toISOString(),
  };

  const listaAudit = [...(actual.auditoria || []), nuevoAuditoria];

  const updatedPayload = {
    ...actual.payload,
    ticket: {
      ...actual.payload.ticket,
      anulado: true,
      motivo_anulacion: motivo,
    },
  };

  const updatedVenta: VentaLocal = {
    ...actual,
    anulado: true,
    motivo_anulacion: motivo,
    payload: updatedPayload,
    auditoria: listaAudit,
  };

  await guardarVentaDemo(updatedVenta);

  return {
    ticket_id,
    fecha: updatedPayload.ticket.fecha,
    fecha_hora_utc: actual.creado_en,
    total_venta: updatedPayload.ticket.total_venta,
    monto_efectivo: updatedPayload.ticket.monto_efectivo,
    monto_transferencia: updatedPayload.ticket.monto_transf,
    monto_transf: updatedPayload.ticket.monto_transf,
    metodo_pago: updatedPayload.ticket.metodo_pago,
    anulado: true,
    motivo_anulacion: motivo,
    items: updatedPayload.items,
    auditoria: listaAudit,
  };
}

export async function editarItemVentaDemo(
  ticket_id: string,
  itemIndex: number,
  nuevaCantidad?: number,
  nuevoSubtotal?: number,
  usuario: string = "sistema"
): Promise<TicketDetalle> {
  const actual = await obtenerVentaDemo(ticket_id);
  if (!actual) {
    throw new Error(`El ticket '${ticket_id}' no existe.`);
  }
  if (actual.anulado || actual.payload.ticket.anulado) {
    throw new Error(`No se puede modificar un ticket anulado ('${ticket_id}').`);
  }
  if (itemIndex < 0 || itemIndex >= actual.payload.items.length) {
    throw new Error(`El índice de ítem (${itemIndex}) no existe en el ticket.`);
  }

  const items = [...actual.payload.items];
  const itemActual = items[itemIndex];

  const cantFinal =
    nuevaCantidad !== undefined && nuevaCantidad > 0 ? nuevaCantidad : itemActual.cantidad;
  const subtotalFinal =
    nuevoSubtotal !== undefined && nuevoSubtotal >= 0
      ? nuevoSubtotal
      : Math.round(cantFinal * itemActual.precio_unit);

  const prevItem = { ...itemActual };
  items[itemIndex] = {
    ...itemActual,
    cantidad: cantFinal,
    subtotal: subtotalFinal,
  };

  const newTotal = items.reduce((acc, it) => acc + it.subtotal, 0);
  const oldTotal = actual.payload.ticket.total_venta;
  const efvo = actual.payload.ticket.monto_efectivo;
  const transf = actual.payload.ticket.monto_transf;
  const diff = oldTotal - newTotal;

  let newEfvo = efvo;
  let newTransf = transf;
  let isAnulado = false;
  let motivoAnulacion: string | undefined;

  if (newTotal <= 0) {
    isAnulado = true;
    motivoAnulacion = "Total recalculado en $0";
    newEfvo = 0;
    newTransf = 0;
  } else {
    if (diff > 0) {
      if (efvo >= diff) {
        newEfvo = efvo - diff;
      } else {
        newEfvo = 0;
        newTransf = Math.max(0, transf - (diff - efvo));
      }
    } else if (diff < 0) {
      if (transf > 0 && efvo === 0) {
        newTransf = transf - diff;
      } else {
        newEfvo = efvo - diff;
      }
    }

    if (newEfvo + newTransf !== newTotal) {
      newEfvo = Math.max(0, newTotal - newTransf);
    }
  }

  let newMetodo = actual.payload.ticket.metodo_pago;
  if (newEfvo > 0 && newTransf > 0) {
    newMetodo = "MIXTO";
  } else if (newEfvo > 0) {
    newMetodo = "EFECTIVO";
  } else {
    newMetodo = "TRANSFERENCIA";
  }

  const nuevoAuditoria: VentaAuditoria = {
    id: Date.now(),
    ticket_id,
    item_id: itemIndex,
    accion: "EDICION_ITEM",
    motivo: `Modificado ${itemActual.producto}`,
    usuario,
    datos_previos_json: JSON.stringify(prevItem),
    datos_nuevos_json: JSON.stringify(items[itemIndex]),
    creado_en_utc: new Date().toISOString(),
  };

  const listaAudit = [...(actual.auditoria || []), nuevoAuditoria];

  const updatedPayload = {
    ticket: {
      ...actual.payload.ticket,
      total_venta: newTotal,
      monto_efectivo: newEfvo,
      monto_transf: newTransf,
      metodo_pago: newMetodo,
      anulado: isAnulado,
      motivo_anulacion: motivoAnulacion,
    },
    items,
  };

  const updatedVenta: VentaLocal = {
    ...actual,
    anulado: isAnulado,
    motivo_anulacion: motivoAnulacion,
    payload: updatedPayload,
    auditoria: listaAudit,
  };

  await guardarVentaDemo(updatedVenta);

  return {
    ticket_id,
    fecha: updatedPayload.ticket.fecha,
    fecha_hora_utc: actual.creado_en,
    total_venta: newTotal,
    monto_efectivo: newEfvo,
    monto_transferencia: newTransf,
    monto_transf: newTransf,
    metodo_pago: newMetodo,
    anulado: isAnulado,
    motivo_anulacion: motivoAnulacion,
    items,
    auditoria: listaAudit,
  };
}

// ==============================================================================
// OPERACIONES SOBRE EL CATÁLOGO CACHEADO
// ==============================================================================

export async function guardarCatalogoCache(productos: Producto[]): Promise<void> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction([STORE_CATALOGO, STORE_META], "readwrite");
    const catStore = tx.objectStore(STORE_CATALOGO);
    const metaStore = tx.objectStore(STORE_META);

    catStore.clear();
    for (const prod of productos) {
      catStore.put(prod);
    }
    metaStore.put({ clave: "catalogo_actualizado_en", valor: new Date().toISOString() });

    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

export async function obtenerCatalogoCache(): Promise<{
  productos: Producto[];
  actualizado_en?: string;
}> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction([STORE_CATALOGO, STORE_META], "readonly");
    const catStore = tx.objectStore(STORE_CATALOGO);
    const metaStore = tx.objectStore(STORE_META);

    const catReq = catStore.getAll();
    const metaReq = metaStore.get("catalogo_actualizado_en");

    tx.oncomplete = () => {
      resolve({
        productos: (catReq.result || []) as Producto[],
        actualizado_en: metaReq.result ? (metaReq.result.valor as string) : undefined,
      });
    };
    tx.onerror = () => reject(tx.error);
  });
}

// ==============================================================================
// OPERACIONES DEL CATÁLOGO DEMO INDEPENDIENTE (Modo Demo Público / Offline)
// ==============================================================================

export async function inicializarCatalogoDemoSiVacio(
  productosBase: Producto[]
): Promise<Producto[]> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_CATALOGO_DEMO, "readwrite");
    const store = tx.objectStore(STORE_CATALOGO_DEMO);
    const countReq = store.count();

    countReq.onsuccess = () => {
      if (countReq.result === 0) {
        for (const prod of productosBase) {
          store.put({
            ...prod,
            activo: prod.activo !== undefined ? prod.activo : true,
          });
        }
        tx.oncomplete = () => {
          resolve(
            productosBase.map((p) => ({
              ...p,
              activo: p.activo !== undefined ? p.activo : true,
            }))
          );
        };
      } else {
        const getAllReq = store.getAll();
        getAllReq.onsuccess = () => {
          resolve((getAllReq.result || []) as Producto[]);
        };
      }
    };
    countReq.onerror = () => reject(countReq.error);
  });
}

export async function listarProductosDemo(
  soloActivos: boolean = false
): Promise<Producto[]> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_CATALOGO_DEMO, "readonly");
    const store = tx.objectStore(STORE_CATALOGO_DEMO);
    const req = store.getAll();

    req.onsuccess = () => {
      let items = (req.result || []) as Producto[];
      if (soloActivos) {
        items = items.filter((p) => p.activo !== false);
      }
      items.sort((a, b) => a.id_producto - b.id_producto);
      resolve(items);
    };
    req.onerror = () => reject(req.error);
  });
}

export async function crearProductoDemo(
  datos: ProductoInput
): Promise<Producto> {
  const db = await openDB();
  const productos = await listarProductosDemo(false);

  // Validación de unicidad de nombre (insensible a mayúsculas)
  const duplicado = productos.find(
    (p) => p.nombre.trim().toLowerCase() === datos.nombre.trim().toLowerCase()
  );
  if (duplicado) {
    throw new Error(`Ya existe un producto con el nombre "${datos.nombre.trim()}".`);
  }

  const maxId = productos.reduce((max, p) => Math.max(max, p.id_producto), 0);
  const nuevoId = maxId + 1;
  const costo = Math.round(Number(datos.costo) || 0);
  const precio = Math.round(Number(datos.precio_venta) || 0);
  const margen = costo > 0 ? parseFloat(((precio - costo) / costo).toFixed(4)) : 0.0;
  const fecha = new Date().toISOString().split("T")[0];

  const nuevo: Producto = {
    id_producto: nuevoId,
    nombre: datos.nombre.trim(),
    categoria: datos.categoria.trim() || "Otro",
    proveedor: datos.proveedor.trim() || "General",
    unidad: datos.unidad.trim() || "Unidad",
    costo,
    precio_venta: precio,
    precio_dia: precio,
    precio_noche: precio,
    margen_porcentaje: margen,
    activo: datos.activo !== undefined ? datos.activo : true,
    fecha_act: fecha,
  };

  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_CATALOGO_DEMO, "readwrite");
    const store = tx.objectStore(STORE_CATALOGO_DEMO);
    const req = store.add(nuevo);
    req.onsuccess = () => resolve(nuevo);
    req.onerror = () => reject(req.error);
  });
}

export async function editarProductoDemo(
  id_producto: number,
  cambios: Partial<ProductoInput>
): Promise<Producto> {
  const db = await openDB();
  const productos = await listarProductosDemo(false);
  const actual = productos.find((p) => p.id_producto === id_producto);
  if (!actual) {
    throw new Error(`No se encontró el producto con ID ${id_producto}.`);
  }

  if (cambios.nombre && cambios.nombre.trim()) {
    const duplicado = productos.find(
      (p) =>
        p.id_producto !== id_producto &&
        p.nombre.trim().toLowerCase() === cambios.nombre!.trim().toLowerCase()
    );
    if (duplicado) {
      throw new Error(`Ya existe otro producto con el nombre "${cambios.nombre.trim()}".`);
    }
  }

  const costo =
    cambios.costo !== undefined ? Math.round(Number(cambios.costo) || 0) : actual.costo;
  const precio =
    cambios.precio_venta !== undefined
      ? Math.round(Number(cambios.precio_venta) || 0)
      : actual.precio_venta;
  const margen = costo > 0 ? parseFloat(((precio - costo) / costo).toFixed(4)) : 0.0;

  const actualizado: Producto = {
    ...actual,
    nombre: cambios.nombre !== undefined ? cambios.nombre.trim() : actual.nombre,
    categoria:
      cambios.categoria !== undefined ? cambios.categoria.trim() : actual.categoria,
    proveedor:
      cambios.proveedor !== undefined ? cambios.proveedor.trim() : actual.proveedor,
    unidad: cambios.unidad !== undefined ? cambios.unidad.trim() : actual.unidad,
    costo,
    precio_venta: precio,
    precio_dia: precio,
    precio_noche: precio,
    margen_porcentaje: margen,
    activo: cambios.activo !== undefined ? cambios.activo : actual.activo,
    fecha_act: new Date().toISOString().split("T")[0],
  };

  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_CATALOGO_DEMO, "readwrite");
    const store = tx.objectStore(STORE_CATALOGO_DEMO);
    const req = store.put(actualizado);
    req.onsuccess = () => resolve(actualizado);
    req.onerror = () => reject(req.error);
  });
}

export async function cambiarEstadoProductoDemo(
  id_producto: number,
  activo: boolean
): Promise<Producto> {
  return editarProductoDemo(id_producto, { activo });
}

export async function eliminarProductoDemo(
  id_producto: number
): Promise<ProductoDeleteResponse> {
  const db = await openDB();
  const productos = await listarProductosDemo(false);
  const actual = productos.find((p) => p.id_producto === id_producto);
  if (!actual) {
    throw new Error(`No se encontró el producto con ID ${id_producto}.`);
  }

  // Verificar si el producto tiene ventas en la base demo o en la cola local
  await inicializarVentasDemoSiVacio();
  const ventasDemo = await listarVentasDemo();
  const ventasQueue = await listarVentasLocales();
  const todasVentas = [...ventasDemo, ...ventasQueue];

  const nomNorm = actual.nombre.trim().toLowerCase();
  const tieneVentas = todasVentas.some((v) =>
    (v.payload?.items || []).some(
      (it: any) =>
        it.producto_id === id_producto ||
        String(it.producto || "").trim().toLowerCase() === nomNorm
    )
  );

  if (tieneVentas) {
    await editarProductoDemo(id_producto, { activo: false });
    return {
      id_producto,
      nombre: actual.nombre,
      status: "desactivado",
      accion: "baja_logica",
      eliminado_fisico: false,
      activo: false,
      tiene_ventas: true,
      mensaje: `El producto '${actual.nombre}' posee ventas registradas y fue desactivado lógicamente.`,
    };
  }

  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_CATALOGO_DEMO, "readwrite");
    const store = tx.objectStore(STORE_CATALOGO_DEMO);
    const req = store.delete(id_producto);
    req.onsuccess = () =>
      resolve({
        id_producto,
        nombre: actual.nombre,
        status: "eliminado",
        accion: "borrado_fisico",
        eliminado_fisico: true,
        activo: false,
        tiene_ventas: false,
        mensaje: `Producto '${actual.nombre}' eliminado físicamente del catálogo.`,
      });
    req.onerror = () => reject(req.error);
  });
}

// ==============================================================================
// HISTORIAL DE CARGAS Y SERVICIOS DEMO (IndexedDB Aislado)
// ==============================================================================

export function fechaArgentinaTexto(date: Date = new Date()): string {
  const pad = (n: number) => (n < 10 ? `0${n}` : `${n}`);
  const d = pad(date.getDate());
  const m = pad(date.getMonth() + 1);
  const y = date.getFullYear();
  const h = pad(date.getHours());
  const min = pad(date.getMinutes());
  const s = pad(date.getSeconds());
  return `${d}/${m}/${y} ${h}:${min}:${s}`;
}

export const CARGAS_DEMO_INICIALES: CargaServicio[] = [
  {
    id: 1,
    fecha: "24/10/2026 10:30:00",
    servicio: "Claro",
    monto_carga: 3000,
    monto_adicional: 200,
    total_cobrado: 3200,
    pago_efvo: 3200,
    pago_transf: 0,
    anulado: false,
  },
  {
    id: 2,
    fecha: "24/10/2026 11:15:00",
    servicio: "SUBE",
    monto_carga: 2000,
    monto_adicional: 100,
    total_cobrado: 2100,
    pago_efvo: 2100,
    pago_transf: 0,
    anulado: false,
  },
  {
    id: 3,
    fecha: "24/10/2026 12:45:00",
    servicio: "Personal",
    monto_carga: 4000,
    monto_adicional: 200,
    total_cobrado: 4200,
    pago_efvo: 0,
    pago_transf: 4200,
    anulado: false,
  },
  {
    id: 4,
    fecha: "24/10/2026 13:20:00",
    servicio: "SUBE (MP)",
    monto_carga: 2000,
    monto_adicional: 100,
    total_cobrado: 2100,
    pago_efvo: 2100,
    pago_transf: 0,
    anulado: false,
  },
];

export async function inicializarServiciosDemoSiVacio(): Promise<void> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_SERVICIOS_DEMO, "readwrite");
    const store = tx.objectStore(STORE_SERVICIOS_DEMO);
    const countReq = store.count();

    countReq.onsuccess = () => {
      if (countReq.result === 0) {
        for (const carga of CARGAS_DEMO_INICIALES) {
          store.put(carga);
        }
      }
      resolve();
    };
    countReq.onerror = () => reject(countReq.error);
  });
}

export async function listarServiciosDemo(
  incluirAnuladas: boolean = false
): Promise<CargaServicio[]> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_SERVICIOS_DEMO, "readonly");
    const store = tx.objectStore(STORE_SERVICIOS_DEMO);
    const req = store.getAll();

    req.onsuccess = () => {
      let items = (req.result || []) as CargaServicio[];
      if (!incluirAnuladas) {
        items = items.filter((c) => !c.anulado);
      }
      // Ordenar por ID descendente
      items.sort((a, b) => b.id - a.id);
      resolve(items);
    };
    req.onerror = () => reject(req.error);
  });
}

export async function guardarCargaDemo(
  carga: CargaServicioInput
): Promise<CargaServicio> {
  const db = await openDB();
  const fecha = carga.fecha || fechaArgentinaTexto();
  const esperadoTotal = carga.monto_carga + carga.monto_adicional;

  if (carga.monto_carga <= 0) {
    throw new Error("El monto de la carga debe ser mayor a cero.");
  }
  if (carga.monto_adicional < 0) {
    throw new Error("El recargo no puede ser negativo.");
  }
  if (carga.pago_efvo < 0 || carga.pago_transf < 0) {
    throw new Error("Los importes de pago no pueden ser negativos.");
  }
  if (carga.pago_efvo + carga.pago_transf !== esperadoTotal) {
    throw new Error(
      `La suma de efectivo ($${carga.pago_efvo}) y transferencia ($${carga.pago_transf}) debe coincidir con el total ($${esperadoTotal}).`
    );
  }

  if (carga.idempotency_key) {
    const todos = await listarServiciosDemo(true);
    const existente = todos.find((c) => c.idempotency_key === carga.idempotency_key);
    if (existente) {
      const srvNombre = (carga.servicio || carga.servicio_destino || "Otro").trim();
      if (
        existente.servicio.toLowerCase() === srvNombre.toLowerCase() &&
        existente.monto_carga === carga.monto_carga &&
        existente.monto_adicional === carga.monto_adicional &&
        existente.pago_efvo === carga.pago_efvo &&
        existente.pago_transf === carga.pago_transf &&
        !existente.anulado
      ) {
        return existente;
      }
      throw new Error(
        `Conflicto de idempotencia: la clave '${carga.idempotency_key}' ya fue utilizada con datos diferentes.`
      );
    }
  }

  const srvNombre = (carga.servicio || carga.servicio_destino || "Otro").trim();
  const nuevoItem: Omit<CargaServicio, "id"> = {
    fecha,
    servicio: srvNombre,
    plataforma_origen: carga.plataforma_origen || null,
    servicio_destino: carga.servicio_destino || srvNombre,
    monto_carga: carga.monto_carga,
    monto_adicional: carga.monto_adicional,
    total_cobrado: esperadoTotal,
    pago_efvo: carga.pago_efvo,
    pago_transf: carga.pago_transf,
    anulado: false,
    idempotency_key: carga.idempotency_key,
  };

  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_SERVICIOS_DEMO, "readwrite");
    const store = tx.objectStore(STORE_SERVICIOS_DEMO);
    const req = store.add(nuevoItem);

    req.onsuccess = () => {
      const id = Number(req.result);
      resolve({ id, ...nuevoItem });
    };
    req.onerror = () => reject(req.error);
  });
}

export async function editarCargaDemo(
  id: number,
  cambios: CargaServicioInput
): Promise<CargaServicio> {
  const db = await openDB();
  const cargas = await listarServiciosDemo(true);
  const actual = cargas.find((c) => c.id === id);
  if (!actual) {
    throw new Error(`No se encontró la carga con ID ${id}.`);
  }
  if (actual.anulado) {
    throw new Error("No se puede editar una carga anulada.");
  }

  const nuevoCarga = cambios.monto_carga ?? actual.monto_carga;
  const nuevoAdic = cambios.monto_adicional ?? actual.monto_adicional;
  const esperadoTotal = nuevoCarga + nuevoAdic;
  const nuevoEfvo = cambios.pago_efvo ?? actual.pago_efvo;
  const nuevoTransf = cambios.pago_transf ?? actual.pago_transf;

  if (nuevoCarga <= 0) {
    throw new Error("El monto de la carga debe ser mayor a cero.");
  }
  if (nuevoAdic < 0) {
    throw new Error("El recargo no puede ser negativo.");
  }
  if (nuevoEfvo + nuevoTransf !== esperadoTotal) {
    throw new Error(
      `La suma de efectivo ($${nuevoEfvo}) y transferencia ($${nuevoTransf}) debe coincidir con el total ($${esperadoTotal}).`
    );
  }

  const actualizado: CargaServicio = {
    ...actual,
    fecha: cambios.fecha || actual.fecha,
    servicio: cambios.servicio ? cambios.servicio.trim() : actual.servicio,
    monto_carga: nuevoCarga,
    monto_adicional: nuevoAdic,
    total_cobrado: esperadoTotal,
    pago_efvo: nuevoEfvo,
    pago_transf: nuevoTransf,
  };

  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_SERVICIOS_DEMO, "readwrite");
    const store = tx.objectStore(STORE_SERVICIOS_DEMO);
    const req = store.put(actualizado);
    req.onsuccess = () => resolve(actualizado);
    req.onerror = () => reject(req.error);
  });
}

export async function anularCargaDemo(id: number): Promise<CargaServicio> {
  const db = await openDB();
  const cargas = await listarServiciosDemo(true);
  const actual = cargas.find((c) => c.id === id);
  if (!actual) {
    throw new Error(`No se encontró la carga con ID ${id}.`);
  }

  const anulado: CargaServicio = {
    ...actual,
    anulado: true,
  };

  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_SERVICIOS_DEMO, "readwrite");
    const store = tx.objectStore(STORE_SERVICIOS_DEMO);
    const req = store.put(anulado);
    req.onsuccess = () => resolve(anulado);
    req.onerror = () => reject(req.error);
  });
}

export function clasificarServicio(servicio: string | undefined): "CLARO" | "SUBE_MP" | "PREPAY" {
  const norm = String(servicio || "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, " ");
  if (norm.includes("claro")) {
    return "CLARO";
  }
  if (norm.includes("sube") && (norm.includes("mp") || norm.includes("mercado"))) {
    return "SUBE_MP";
  }
  return "PREPAY";
}

export async function calcularResumenServiciosDemo(
  fechaFiltro?: string
): Promise<ResumenServicios> {
  const cargas = await listarServiciosDemo(true);

  // Filtrado opcional por fecha (YYYY-MM-DD o DD/MM/YYYY)
  let filtradas = cargas;
  if (fechaFiltro) {
    const ff = fechaFiltro.trim();
    let target = ff;
    if (ff.includes("/")) {
      const [d, m, y] = ff.split("/").map((p) => Number(p));
      target = `${String(y).padStart(4, "0")}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    }

    filtradas = cargas.filter((c) => {
      try {
        const [d, m, y] = c.fecha.split(" ")[0].split("/").map((p) => Number(p));
        const cDate = `${String(y).padStart(4, "0")}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
        return cDate === target;
      } catch {
        return false;
      }
    });
  }

  const activas = filtradas.filter((c) => !c.anulado);
  const anuladas = filtradas.filter((c) => c.anulado);

  let capital_prepay = 0;
  let capital_claro = 0;
  let capital_sube_mp = 0;

  for (const c of activas) {
    const cat = clasificarServicio(c.servicio);
    if (cat === "CLARO") {
      capital_claro += c.monto_carga;
    } else if (cat === "SUBE_MP") {
      capital_sube_mp += c.monto_carga;
    } else {
      capital_prepay += c.monto_carga;
    }
  }

  return {
    total_operaciones: activas.length,
    total_cobrado: activas.reduce((acc, c) => acc + c.total_cobrado, 0),
    total_capital: activas.reduce((acc, c) => acc + c.monto_carga, 0),
    total_adicional: activas.reduce((acc, c) => acc + c.monto_adicional, 0),
    total_efectivo: activas.reduce((acc, c) => acc + c.pago_efvo, 0),
    total_transferencia: activas.reduce((acc, c) => acc + c.pago_transf, 0),
    capital_prepay,
    capital_claro,
    capital_sube_mp,
    operaciones_anuladas: anuladas.length,
  };
}

// ==============================================================================
// GASTOS, RETIROS, ARQUEO Y CIERRES DEMO (IndexedDB Aislado)
// ==============================================================================

export const GASTOS_DEMO_INICIALES: Gasto[] = [
  {
    id: 1,
    fecha: "24/10/2026 11:30:00",
    tipo: "Comida",
    detalle: "Panadería y medialunas",
    monto_salida: 2500,
    origen_fondos: "Caja diaria",
    metodo_pago: "EFECTIVO",
    anulado: false,
  },
  {
    id: 2,
    fecha: "24/10/2026 13:45:00",
    tipo: "Limpieza",
    detalle: "Artículos de limpieza y bolsas",
    monto_salida: 1800,
    origen_fondos: "Caja diaria",
    metodo_pago: "EFECTIVO",
    anulado: false,
  },
  {
    id: 3,
    fecha: "24/10/2026 15:10:00",
    tipo: "Servicios del local",
    detalle: "Abono de Internet y conectividad",
    monto_salida: 14000,
    origen_fondos: "Banco",
    metodo_pago: "TRANSFERENCIA",
    anulado: false,
  },
];

export async function inicializarGastosDemoSiVacio(): Promise<void> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_GASTOS_DEMO, "readwrite");
    const store = tx.objectStore(STORE_GASTOS_DEMO);
    const countReq = store.count();

    countReq.onsuccess = () => {
      if (countReq.result === 0) {
        for (const gasto of GASTOS_DEMO_INICIALES) {
          store.put(gasto);
        }
      }
      resolve();
    };
    countReq.onerror = () => reject(countReq.error);
  });
}

export async function listarGastosDemo(incluirAnulados: boolean = false): Promise<Gasto[]> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_GASTOS_DEMO, "readonly");
    const store = tx.objectStore(STORE_GASTOS_DEMO);
    const req = store.getAll();

    req.onsuccess = () => {
      let items = (req.result || []) as Gasto[];
      if (!incluirAnulados) {
        items = items.filter((g) => !g.anulado);
      }
      items.sort((a, b) => b.id - a.id);
      resolve(items);
    };
    req.onerror = () => reject(req.error);
  });
}

export async function guardarGastoDemo(gasto: GastoInput): Promise<Gasto> {
  const db = await openDB();
  const detalle = gasto.detalle.trim();
  if (!detalle) {
    throw new Error("El concepto o detalle del gasto es obligatorio.");
  }
  if (gasto.monto_salida <= 0) {
    throw new Error("El importe del gasto debe ser mayor a cero.");
  }

  if (gasto.idempotency_key) {
    const todos = await listarGastosDemo(true);
    const existente = todos.find((g) => g.idempotency_key === gasto.idempotency_key);
    if (existente) {
      if (
        existente.detalle === detalle &&
        existente.monto_salida === Math.round(gasto.monto_salida) &&
        existente.tipo.toLowerCase() === (gasto.tipo ? gasto.tipo.trim() : "General").toLowerCase() &&
        existente.origen_fondos.toLowerCase() === (gasto.origen_fondos ? gasto.origen_fondos.trim() : "Caja diaria").toLowerCase() &&
        existente.metodo_pago === (gasto.metodo_pago || "EFECTIVO") &&
        !existente.anulado
      ) {
        return existente;
      }
      throw new Error(
        `Conflicto de idempotencia: la clave '${gasto.idempotency_key}' ya fue utilizada con datos diferentes.`
      );
    }
  }

  const nuevoItem: Omit<Gasto, "id"> = {
    fecha: gasto.fecha || fechaArgentinaTexto(),
    tipo: gasto.tipo ? gasto.tipo.trim() : "General",
    detalle,
    monto_salida: Math.round(gasto.monto_salida),
    origen_fondos: gasto.origen_fondos ? gasto.origen_fondos.trim() : "Caja diaria",
    metodo_pago: gasto.metodo_pago || "EFECTIVO",
    anulado: false,
    idempotency_key: gasto.idempotency_key,
  };

  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_GASTOS_DEMO, "readwrite");
    const store = tx.objectStore(STORE_GASTOS_DEMO);
    const req = store.add(nuevoItem);

    req.onsuccess = () => {
      const id = Number(req.result);
      resolve({ id, ...nuevoItem });
    };
    req.onerror = () => reject(req.error);
  });
}

export async function editarGastoDemo(id: number, cambios: GastoInput): Promise<Gasto> {
  const db = await openDB();
  const gastos = await listarGastosDemo(true);
  const actual = gastos.find((g) => g.id === id);
  if (!actual) {
    throw new Error(`No se encontró el gasto con ID ${id}.`);
  }
  if (actual.anulado) {
    throw new Error("No se puede editar un gasto anulado.");
  }

  const detalle = cambios.detalle ? cambios.detalle.trim() : actual.detalle;
  if (!detalle) {
    throw new Error("El concepto del gasto no puede estar vacío.");
  }
  const monto = cambios.monto_salida !== undefined ? Math.round(cambios.monto_salida) : actual.monto_salida;
  if (monto <= 0) {
    throw new Error("El importe del gasto debe ser mayor a cero.");
  }

  const actualizado: Gasto = {
    ...actual,
    fecha: cambios.fecha || actual.fecha,
    tipo: cambios.tipo !== undefined ? cambios.tipo.trim() : actual.tipo,
    detalle,
    monto_salida: monto,
    origen_fondos: cambios.origen_fondos !== undefined ? cambios.origen_fondos.trim() : actual.origen_fondos,
    metodo_pago: cambios.metodo_pago || actual.metodo_pago,
  };

  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_GASTOS_DEMO, "readwrite");
    const store = tx.objectStore(STORE_GASTOS_DEMO);
    const req = store.put(actualizado);
    req.onsuccess = () => resolve(actualizado);
    req.onerror = () => reject(req.error);
  });
}

export async function anularGastoDemo(id: number): Promise<Gasto> {
  const db = await openDB();
  const gastos = await listarGastosDemo(true);
  const actual = gastos.find((g) => g.id === id);
  if (!actual) {
    throw new Error(`No se encontró el gasto con ID ${id}.`);
  }

  const anulado: Gasto = {
    ...actual,
    anulado: true,
  };

  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_GASTOS_DEMO, "readwrite");
    const store = tx.objectStore(STORE_GASTOS_DEMO);
    const req = store.put(anulado);
    req.onsuccess = () => resolve(anulado);
    req.onerror = () => reject(req.error);
  });
}

export async function calcularResumenGastosDemo(fechaFiltro?: string): Promise<ResumenGastos> {
  const gastos = await listarGastosDemo(true);
  let filtrados = gastos;

  if (fechaFiltro) {
    const ff = fechaFiltro.trim();
    let target = ff;
    if (ff.includes("/")) {
      const [d, m, y] = ff.split("/").map(Number);
      target = `${String(y).padStart(4, "0")}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    }

    filtrados = gastos.filter((g) => {
      try {
        const [d, m, y] = g.fecha.split(" ")[0].split("/").map(Number);
        const gDate = `${String(y).padStart(4, "0")}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
        return gDate === target;
      } catch {
        return false;
      }
    });
  }

  const activas = filtrados.filter((g) => !g.anulado);
  const anuladas = filtrados.filter((g) => g.anulado);

  const gastos_efvo = activas
    .filter((g) => g.metodo_pago === "EFECTIVO")
    .reduce((acc, g) => acc + g.monto_salida, 0);
  const gastos_transf = activas
    .filter((g) => g.metodo_pago === "TRANSFERENCIA")
    .reduce((acc, g) => acc + g.monto_salida, 0);

  return {
    total_operaciones: activas.length,
    gastos_efvo,
    gastos_transf,
    gastos_total: gastos_efvo + gastos_transf,
    operaciones_anuladas: anuladas.length,
  };
}

export async function calcularArqueoDemo(fechaFiltro?: string): Promise<ArqueoDiario> {
  const fechaObj = fechaFiltro || fechaArgentinaTexto().split(" ")[0];
  const targetISO = fechaFiltro ? extraerFechaISO(fechaFiltro) : "";

  // 1. Ventas desde el almacén demo (filtradas por fecha cuando se especifica fechaFiltro)
  const ventas = await listarVentasDemo();
  let ventas_efvo = 0;
  let ventas_transf = 0;
  let total_ventas = 0;

  for (const v of ventas) {
    if (v.anulado || v.payload.ticket.anulado) continue;
    if (targetISO) {
      const vFechaISO = extraerFechaISO(v.payload.ticket.fecha || v.creado_en);
      if (vFechaISO !== targetISO) continue;
    }
    ventas_efvo += v.payload.ticket.monto_efectivo || 0;
    ventas_transf += v.payload.ticket.monto_transf || 0;
    total_ventas += v.payload.ticket.total_venta || 0;
  }

  // 2. Servicios
  const resCargas = await calcularResumenServiciosDemo(fechaFiltro);
  const servicios_efvo = resCargas.total_efectivo;
  const servicios_transf = resCargas.total_transferencia;
  const d_recargas = resCargas.total_transferencia;
  const sube_mp_capital = resCargas.capital_sube_mp;

  // 3. Gastos
  const resGastos = await calcularResumenGastosDemo(fechaFiltro);
  const gastos_efvo = resGastos.gastos_efvo;
  const gastos_transf = resGastos.gastos_transf;

  // 4. Conciliación de arqueo
  const total_banco = ventas_transf + d_recargas - sube_mp_capital - gastos_transf;
  const efectivo_disponible_drugstore = ventas_efvo;

  // Efectivo físico esperado en cajón:
  // Ventas efvo + Servicios efvo - Gastos efvo (sin restar transferencias d_recargas ni enmascarar con Math.max)
  const efectivo_esperado = ventas_efvo + servicios_efvo - gastos_efvo;

  return {
    fecha: fechaObj,
    ventas_efvo,
    ventas_transf,
    total_ventas,
    ganancia_estimada_ventas: Math.round(total_ventas * 0.10),
    servicios_efvo,
    servicios_transf,
    total_servicios: resCargas.total_cobrado,
    capital_prepay: resCargas.capital_prepay,
    capital_claro: resCargas.capital_claro,
    capital_sube_mp: resCargas.capital_sube_mp,
    adicionales_ganancia: resCargas.total_adicional,
    d_recargas,
    gastos_efvo,
    gastos_transf,
    gastos_total: resGastos.gastos_total,
    efectivo_disponible_drugstore,
    efectivo_esperado,
    total_banco,
  };
}

export async function guardarCierreDemo(cierre: CierreDiario): Promise<CierreDiario> {
  const db = await openDB();
  const fecha = cierre.fecha.trim();
  if (!fecha) {
    throw new Error("La fecha de cierre es obligatoria.");
  }
  const diferencia = cierre.efectivo_contado - cierre.efectivo_esperado;

  const item: CierreDiario = {
    ...cierre,
    fecha,
    diferencia,
    cerrado_en_utc: new Date().toISOString(),
  };

  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_CIERRES_DEMO, "readwrite");
    const store = tx.objectStore(STORE_CIERRES_DEMO);
    const req = store.put(item);
    req.onsuccess = () => resolve(item);
    req.onerror = () => reject(req.error);
  });
}

export async function obtenerCierreDemo(fecha: string): Promise<CierreDiario | undefined> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_CIERRES_DEMO, "readonly");
    const store = tx.objectStore(STORE_CIERRES_DEMO);
    const req = store.get(fecha.trim());
    req.onsuccess = () => resolve(req.result as CierreDiario | undefined);
    req.onerror = () => reject(req.error);
  });
}

export async function listarCierresDemo(): Promise<CierreDiario[]> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_CIERRES_DEMO, "readonly");
    const store = tx.objectStore(STORE_CIERRES_DEMO);
    const req = store.getAll();
    req.onsuccess = () => {
      const items = (req.result || []) as CierreDiario[];
      items.sort((a, b) => b.fecha.localeCompare(a.fecha));
      resolve(items);
    };
    req.onerror = () => reject(req.error);
  });
}

// ==============================================================================
// 8. OPERACIONES DE PREVENTISTAS Y PEDIDOS (DEMO LOCAL INDEXEDDB)
// ==============================================================================

export async function listarProveedoresDemo(): Promise<string[]> {
  const productos = await listarProductosDemo(false);
  const provSet = new Set<string>();
  for (const p of productos) {
    const pr = (p.proveedor || "").trim();
    if (pr) provSet.add(pr);
  }
  return Array.from(provSet).sort((a, b) => a.localeCompare(b, "es", { sensitivity: "base" }));
}

export async function listarProductosPorProveedorDemo(proveedor: string): Promise<Producto[]> {
  const productos = await listarProductosDemo(false);
  const target = proveedor.trim().toLowerCase();
  return productos.filter((p) => (p.proveedor || "").trim().toLowerCase() === target);
}

export async function guardarPreciosLoteDemo(
  proveedor: string,
  cambios: Array<{ id_producto: number; costo: number; precio_venta: number }>
): Promise<{ actualizados: number; mensaje: string }> {
  const db = await openDB();
  const productos = await listarProductosDemo(false);
  const mapa = new Map(productos.map((p) => [p.id_producto, p]));

  let actualizados = 0;
  const hoyStr = new Date().toISOString().split("T")[0];

  return new Promise((resolve, reject) => {
    const tx = db.transaction([STORE_CATALOGO_DEMO, STORE_CATALOGO], "readwrite");
    const storeDemo = tx.objectStore(STORE_CATALOGO_DEMO);
    const storeCache = tx.objectStore(STORE_CATALOGO);

    for (const c of cambios) {
      const prod = mapa.get(c.id_producto);
      if (!prod) continue;

      const nCosto = Math.round(Number(c.costo) || 0);
      const nPrecio = Math.round(Number(c.precio_venta) || 0);

      if (prod.costo !== nCosto || (prod.precio_venta ?? prod.precio_dia) !== nPrecio) {
        const nuevoMargen = nCosto > 0 ? parseFloat(((nPrecio - nCosto) / nCosto).toFixed(4)) : 0.0;
        const actualizado: Producto = {
          ...prod,
          costo: nCosto,
          precio_venta: nPrecio,
          precio_dia: nPrecio,
          precio_noche: nPrecio,
          margen_porcentaje: nuevoMargen,
          fecha_act: hoyStr,
        };

        storeDemo.put(actualizado);
        storeCache.put(actualizado);
        actualizados++;
      }
    }

    tx.oncomplete = () => {
      resolve({
        actualizados,
        mensaje: `Se actualizaron ${actualizados} productos para ${proveedor}.`,
      });
    };
    tx.onerror = () => reject(tx.error);
  });
}

export async function inicializarPedidosDemoSiVacio(): Promise<void> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_PEDIDOS_DEMO, "readwrite");
    const store = tx.objectStore(STORE_PEDIDOS_DEMO);
    const countReq = store.count();

    countReq.onsuccess = () => {
      if (countReq.result === 0) {
        const seedPedidos: PedidoProveedorDetalle[] = [
          {
            id: "PED-20261024-001",
            fecha: "2026-10-24",
            proveedor: "Coca Cola",
            estado: "PENDIENTE",
            total: 54000,
            observaciones: "Entrega programada para el lunes por la mañana.",
            creado_en_utc: new Date().toISOString(),
            actualizado_en_utc: new Date().toISOString(),
            items_count: 2,
            items: [
              {
                id: 1,
                producto_id: 1,
                producto_nombre: "Coca Cola 2.25L",
                cantidad: 18,
                costo_unitario: 2000,
                subtotal: 36000,
              },
              {
                id: 2,
                producto_id: 2,
                producto_nombre: "Sprite 2.25L",
                cantidad: 10,
                costo_unitario: 1800,
                subtotal: 18000,
              },
            ],
          },
          {
            id: "PED-20261023-001",
            fecha: "2026-10-23",
            proveedor: "Cervecería Quilmes",
            estado: "RECIBIDO",
            total: 37000,
            observaciones: "Mercadería recibida conforme en depósito.",
            creado_en_utc: new Date(Date.now() - 86400000).toISOString(),
            actualizado_en_utc: new Date(Date.now() - 43200000).toISOString(),
            items_count: 2,
            items: [
              {
                id: 3,
                producto_id: 3,
                producto_nombre: "Quilmes Clásica 1L",
                cantidad: 10,
                costo_unitario: 1500,
                subtotal: 15000,
              },
              {
                id: 4,
                producto_id: 4,
                producto_nombre: "Stella Artois 1L",
                cantidad: 10,
                costo_unitario: 2200,
                subtotal: 22000,
              },
            ],
          },
        ];

        for (const p of seedPedidos) {
          store.put(p);
        }
      }
      resolve();
    };
    countReq.onerror = () => reject(countReq.error);
  });
}

export async function listarPedidosDemo(filtros?: {
  proveedor?: string;
  fecha?: string;
  estado?: string;
}): Promise<PedidoProveedor[]> {
  await inicializarPedidosDemoSiVacio();
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_PEDIDOS_DEMO, "readonly");
    const store = tx.objectStore(STORE_PEDIDOS_DEMO);
    const req = store.getAll();

    req.onsuccess = () => {
      let items = (req.result || []) as PedidoProveedorDetalle[];

      if (filtros?.proveedor && filtros.proveedor.trim()) {
        const pr = filtros.proveedor.trim().toLowerCase();
        items = items.filter((p) => p.proveedor.trim().toLowerCase() === pr);
      }
      if (filtros?.fecha && filtros.fecha.trim()) {
        items = items.filter((p) => p.fecha === filtros.fecha!.trim());
      }
      if (filtros?.estado && filtros.estado.trim() && filtros.estado.toUpperCase() !== "TODOS") {
        const est = filtros.estado.trim().toUpperCase();
        items = items.filter((p) => p.estado === est);
      }

      items.sort((a, b) => {
        const c = b.fecha.localeCompare(a.fecha);
        return c !== 0 ? c : b.id.localeCompare(a.id);
      });

      const res: PedidoProveedor[] = items.map((p) => ({
        id: p.id,
        fecha: p.fecha,
        proveedor: p.proveedor,
        estado: p.estado,
        total: p.total,
        observaciones: p.observaciones,
        creado_en_utc: p.creado_en_utc,
        actualizado_en_utc: p.actualizado_en_utc,
        items_count: p.items?.length || p.items_count || 0,
      }));

      resolve(res);
    };
    req.onerror = () => reject(req.error);
  });
}

export async function obtenerDetallePedidoDemo(id: string): Promise<PedidoProveedorDetalle | null> {
  await inicializarPedidosDemoSiVacio();
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_PEDIDOS_DEMO, "readonly");
    const store = tx.objectStore(STORE_PEDIDOS_DEMO);
    const req = store.get(id);

    req.onsuccess = () => resolve((req.result as PedidoProveedorDetalle) || null);
    req.onerror = () => reject(req.error);
  });
}

export async function crearPedidoDemo(datos: {
  proveedor: string;
  fecha?: string;
  observaciones?: string;
  items: PedidoItem[];
}): Promise<PedidoProveedorDetalle> {
  const prov = (datos.proveedor || "").trim();
  if (!prov) {
    throw new Error("El proveedor es obligatorio para registrar un pedido.");
  }
  if (!datos.items || datos.items.length === 0) {
    throw new Error("El pedido debe contener al menos un producto.");
  }

  const pedidos = await listarPedidosDemo();
  const hoyStr = new Date().toISOString().split("T")[0];
  const hoyComp = hoyStr.replace(/-/g, "");
  const prefijo = `PED-${hoyComp}-`;
  const delDia = pedidos.filter((p) => p.id.startsWith(prefijo));
  const nuevoId = `${prefijo}${String(delDia.length + 1).padStart(3, "0")}`;

  let total = 0;
  const itemsProcesados: PedidoItem[] = datos.items.map((it, idx) => {
    const cant = Math.max(1, Number(it.cantidad) || 1);
    const costo = Math.max(0, Number(it.costo_unitario) || 0);
    const subtotal = Math.round(cant * costo);
    total += subtotal;
    return {
      id: idx + 1,
      producto_id: it.producto_id,
      producto_nombre: it.producto_nombre.trim(),
      cantidad: cant,
      costo_unitario: costo,
      subtotal,
    };
  });

  const nuevoPedido: PedidoProveedorDetalle = {
    id: nuevoId,
    fecha: datos.fecha?.trim() || hoyStr,
    proveedor: prov,
    estado: "PENDIENTE",
    total,
    observaciones: datos.observaciones?.trim() || "",
    creado_en_utc: new Date().toISOString(),
    actualizado_en_utc: new Date().toISOString(),
    items_count: itemsProcesados.length,
    items: itemsProcesados,
  };

  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_PEDIDOS_DEMO, "readwrite");
    const store = tx.objectStore(STORE_PEDIDOS_DEMO);
    const req = store.add(nuevoPedido);
    req.onsuccess = () => resolve(nuevoPedido);
    req.onerror = () => reject(req.error);
  });
}

export async function cambiarEstadoPedidoDemo(
  id: string,
  nuevoEstado: "PENDIENTE" | "RECIBIDO" | "CANCELADO",
  observaciones?: string
): Promise<PedidoProveedor> {
  const detalle = await obtenerDetallePedidoDemo(id);
  if (!detalle) {
    throw new Error(`No se encontró el pedido ${id}.`);
  }

  let obs = detalle.observaciones || "";
  if (observaciones && observaciones.trim()) {
    obs = obs ? `${obs} | ${observaciones.trim()}` : observaciones.trim();
  }

  const actualizado: PedidoProveedorDetalle = {
    ...detalle,
    estado: nuevoEstado,
    observaciones: obs,
    actualizado_en_utc: new Date().toISOString(),
  };

  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_PEDIDOS_DEMO, "readwrite");
    const store = tx.objectStore(STORE_PEDIDOS_DEMO);
    const req = store.put(actualizado);
    req.onsuccess = () => resolve(actualizado);
    req.onerror = () => reject(req.error);
  });
}

export async function cancelarPedidoDemo(id: string, motivo: string): Promise<PedidoProveedor> {
  if (!motivo.trim()) {
    throw new Error("El motivo de cancelación es obligatorio.");
  }
  return cambiarEstadoPedidoDemo(id, "CANCELADO", `Cancelación: ${motivo.trim()}`);
}

// ==============================================================================
// 9. DASHBOARD FINANCIERO Y REPORTES (VISOR) DEMO LOCAL
// ==============================================================================

export function formatearFechaISO(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export function extraerFechaISO(fechaStr: string | undefined): string {
  if (!fechaStr) return "";
  const f = fechaStr.trim();
  if (f.includes("T")) {
    return f.split("T")[0];
  }
  const parteFecha = f.split(" ")[0];
  if (parteFecha.includes("/")) {
    const parts = parteFecha.split("/").map((p) => p.trim());
    if (parts.length === 3) {
      if (parts[0].length === 4) {
        return `${parts[0]}-${parts[1].padStart(2, "0")}-${parts[2].padStart(2, "0")}`;
      } else {
        return `${parts[2]}-${parts[1].padStart(2, "0")}-${parts[0].padStart(2, "0")}`;
      }
    }
  }
  if (parteFecha.includes("-")) {
    return parteFecha;
  }
  return parteFecha;
}

export async function calcularMetricasDashboardDemo(
  desdeParam?: string,
  hastaParam?: string,
  periodoTipo: PeriodoTipo = "hoy"
): Promise<MetricasDashboard> {
  const hoyObj = new Date();
  const hoyISO = formatearFechaISO(hoyObj);

  let desde = hoyISO;
  let hasta = hoyISO;

  if (periodoTipo === "hoy") {
    desde = hoyISO;
    hasta = hoyISO;
  } else if (periodoTipo === "ultimos_7_dias") {
    const d = new Date(hoyObj);
    d.setDate(d.getDate() - 6);
    desde = formatearFechaISO(d);
    hasta = hoyISO;
  } else if (periodoTipo === "ultimos_30_dias") {
    const d = new Date(hoyObj);
    d.setDate(d.getDate() - 29);
    desde = formatearFechaISO(d);
    hasta = hoyISO;
  } else if (periodoTipo === "personalizado") {
    desde = desdeParam ? extraerFechaISO(desdeParam) : hoyISO;
    hasta = hastaParam ? extraerFechaISO(hastaParam) : hoyISO;
    if (desde > hasta) {
      const tmp = desde;
      desde = hasta;
      hasta = tmp;
    }
  }

  // Asegurar carga inicial en demo si están vacíos
  await Promise.all([
    inicializarVentasDemoSiVacio(),
    inicializarServiciosDemoSiVacio(),
    inicializarGastosDemoSiVacio(),
  ]);

  const [todasVentas, todasCargas, todosGastos, todosCierres] = await Promise.all([
    listarVentasDemo(),
    listarServiciosDemo(true),
    listarGastosDemo(true),
    listarCierresDemo(),
  ]);

  // Generar lista de días para serie temporal
  const diasSerie: string[] = [];
  try {
    let curr = new Date(desde + "T00:00:00");
    const end = new Date(hasta + "T00:00:00");
    let safeCount = 0;
    while (curr <= end && safeCount <= 31) {
      diasSerie.push(formatearFechaISO(curr));
      curr.setDate(curr.getDate() + 1);
      safeCount++;
    }
  } catch {
    diasSerie.push(hoyISO);
  }

  // Acumuladores diarios
  const serieMap = new Map<
    string,
    {
      ventas_total: number;
      servicios_total: number;
      gastos_total: number;
      adicionales_ganancia: number;
      ventas_transf: number;
      recargas_transf: number;
      sube_mp_capital: number;
      gastos_transf: number;
    }
  >();

  for (const d of diasSerie) {
    serieMap.set(d, {
      ventas_total: 0,
      servicios_total: 0,
      gastos_total: 0,
      adicionales_ganancia: 0,
      ventas_transf: 0,
      recargas_transf: 0,
      sube_mp_capital: 0,
      gastos_transf: 0,
    });
  }

  // 1. Filtrar y procesar Ventas (Caja A)
  let total_ventas = 0;
  let ventas_efvo = 0;
  let ventas_transf = 0;
  let cantidad_tickets = 0;

  let costo_historico_total = 0;
  let suma_ganancia_bruta = 0;
  let suma_subtotal_con_costo = 0;
  let ventas_con_costo_completo = 0;
  let ventas_con_costo_incompleto = 0;
  let items_con_costo_desconocido = 0;

  for (const v of todasVentas) {
    if (v.anulado || v.payload.ticket.anulado) continue;
    const fISO = extraerFechaISO(v.payload.ticket.fecha || v.creado_en);
    if (fISO >= desde && fISO <= hasta) {
      const tot = v.payload.ticket.total_venta || 0;
      const efv = v.payload.ticket.monto_efectivo || 0;
      const trf = v.payload.ticket.monto_transf || 0;

      total_ventas += tot;
      ventas_efvo += efv;
      ventas_transf += trf;
      cantidad_tickets++;

      // Análisis de costo histórico congelado ítem por ítem
      let ticketTieneTodosCostos = true;
      let ticketCostoTotal = 0;

      if (!v.payload.items || v.payload.items.length === 0) {
        ticketTieneTodosCostos = false;
      } else {
        for (const item of v.payload.items) {
          if (
            item.costo_unitario !== undefined &&
            item.costo_unitario !== null &&
            !isNaN(Number(item.costo_unitario))
          ) {
            ticketCostoTotal += Number(item.costo_unitario) * Number(item.cantidad);
          } else {
            ticketTieneTodosCostos = false;
            items_con_costo_desconocido++;
          }
        }
      }

      if (ticketTieneTodosCostos) {
        ventas_con_costo_completo++;
        costo_historico_total += ticketCostoTotal;
        const gananciaTicket = tot - ticketCostoTotal;
        suma_ganancia_bruta += gananciaTicket;
        suma_subtotal_con_costo += tot;
      } else {
        ventas_con_costo_incompleto++;
      }

      const s = serieMap.get(fISO);
      if (s) {
        s.ventas_total += tot;
        s.ventas_transf += trf;
      }
    }
  }

  const ganancia_bruta_real =
    ventas_con_costo_completo > 0 ? Math.round(suma_ganancia_bruta) : null;
  const margen_bruto_porcentaje =
    ventas_con_costo_completo > 0 && suma_subtotal_con_costo > 0
      ? Math.round((suma_ganancia_bruta / suma_subtotal_con_costo) * 1000) / 10
      : null;
  const tiene_costos_completos =
    ventas_con_costo_completo > 0 && ventas_con_costo_incompleto === 0;

  // 2. Filtrar y procesar Cargas y Servicios (Caja B, C, D recargas, E)
  let d_recargas = 0;
  let b_total = 0;
  let b_efvo = 0;
  let b_transf = 0;
  let b_count = 0;

  let c_total = 0;
  let c_efvo = 0;
  let c_transf = 0;
  let c_count = 0;

  let e_total = 0;
  let e_efvo = 0;
  let e_transf = 0;
  let e_count = 0;

  let sube_mp_capital = 0;
  let servicios_efvo_total = 0;

  for (const c of todasCargas) {
    if (c.anulado) continue;
    const fISO = extraerFechaISO(c.fecha);
    if (fISO >= desde && fISO <= hasta) {
      d_recargas += c.pago_transf;
      servicios_efvo_total += c.pago_efvo;

      // Desglose idéntico a mostrar_visor() en app.py
      const cap_transf = Math.min(c.pago_transf, c.monto_carga);
      const cap_efvo = Math.max(0, c.monto_carga - cap_transf);
      const adic_transf = Math.max(0, c.pago_transf - c.monto_carga);
      const adic_efvo = Math.max(0, c.monto_adicional - adic_transf);

      const catSrv = clasificarServicio(c.servicio);
      if (catSrv === "CLARO") {
        e_total += c.monto_carga;
        e_efvo += cap_efvo;
        e_transf += cap_transf;
        e_count++;
      } else if (catSrv === "SUBE_MP") {
        sube_mp_capital += c.monto_carga;
      } else {
        b_total += c.monto_carga;
        b_efvo += cap_efvo;
        b_transf += cap_transf;
        b_count++;
      }

      c_total += c.monto_adicional;
      c_efvo += adic_efvo;
      c_transf += adic_transf;
      c_count++;

      const s = serieMap.get(fISO);
      if (s) {
        s.servicios_total += c.total_cobrado;
        s.adicionales_ganancia += c.monto_adicional;
        s.recargas_transf += c.pago_transf;
        if (catSrv === "SUBE_MP") {
          s.sube_mp_capital += c.monto_carga;
        }
      }
    }
  }

  // 3. Filtrar y procesar Gastos
  let gastos_efvo = 0;
  let gastos_transf = 0;
  let cantidad_gastos = 0;

  for (const g of todosGastos) {
    if (g.anulado) continue;
    const fISO = extraerFechaISO(g.fecha);
    if (fISO >= desde && fISO <= hasta) {
      if (g.metodo_pago === "EFECTIVO") {
        gastos_efvo += g.monto_salida;
      } else {
        gastos_transf += g.monto_salida;
      }
      cantidad_gastos++;

      const s = serieMap.get(fISO);
      if (s) {
        s.gastos_total += g.monto_salida;
        if (g.metodo_pago === "TRANSFERENCIA") {
          s.gastos_transf += g.monto_salida;
        }
      }
    }
  }

  // 4. Conciliaciones y cálculos finales
  // Caja A: efectivo disponible de ventas drugstore = ventas_efvo
  const efectivo_disponible_drugstore = ventas_efvo;

  // Caja D: Banco total = ventas_transf + d_recargas - sube_mp_capital - gastos_transf
  const total_banco_neto = ventas_transf + d_recargas - sube_mp_capital - gastos_transf;

  // Efectivo físico esperado en cajón:
  // Ventas efvo + Servicios efvo - Gastos efvo (sin restar transferencias d_recargas ni enmascarar con Math.max)
  const efectivo_esperado_cajon = ventas_efvo + servicios_efvo_total - gastos_efvo;

  // Serie temporal ordenada
  const serie_temporal: PuntoSerieTemporal[] = diasSerie.map((fISO) => {
    const s = serieMap.get(fISO) || {
      ventas_total: 0,
      servicios_total: 0,
      gastos_total: 0,
      adicionales_ganancia: 0,
      ventas_transf: 0,
      recargas_transf: 0,
      sube_mp_capital: 0,
      gastos_transf: 0,
    };
    const bNeto = s.ventas_transf + s.recargas_transf - s.sube_mp_capital - s.gastos_transf;
    const [, m, d] = fISO.split("-");
    return {
      fecha: fISO,
      etiqueta: `${d}/${m}`,
      ventas_total: s.ventas_total,
      servicios_total: s.servicios_total,
      gastos_total: s.gastos_total,
      adicionales_ganancia: s.adicionales_ganancia,
      banco_neto: bNeto,
    };
  });

  // Filtrar cierres dentro del período
  const cierresFiltrados = todosCierres.filter((c) => {
    const fISO = extraerFechaISO(c.fecha);
    return fISO >= desde && fISO <= hasta;
  });

  return {
    periodo_tipo: periodoTipo,
    desde,
    hasta,
    caja_a: {
      total_ventas,
      ventas_efvo,
      ventas_transf,
      efectivo_disponible_drugstore,
      ganancia_estimada_ventas: Math.round(total_ventas * 0.1),
      es_estimacion_heredada: true,
      cantidad_tickets,
      ganancia_bruta_real,
      costo_historico_total,
      ventas_con_costo_completo,
      ventas_con_costo_incompleto,
      items_con_costo_desconocido,
      margen_bruto_porcentaje,
      tiene_costos_completos,
    },
    caja_b: {
      capital_total: b_total,
      capital_efvo: b_efvo,
      capital_transf: b_transf,
      cantidad_operaciones: b_count,
    },
    caja_c: {
      ganancia_adicional_total: c_total,
      adicional_efvo: c_efvo,
      adicional_transf: c_transf,
      cantidad_operaciones: c_count,
    },
    caja_d: {
      ventas_transf,
      recargas_transf: d_recargas,
      sube_mp_capital,
      gastos_transf,
      total_banco_neto,
    },
    caja_e: {
      capital_total: e_total,
      capital_efvo: e_efvo,
      capital_transf: e_transf,
      cantidad_operaciones: e_count,
    },
    gastos: {
      gastos_total: gastos_efvo + gastos_transf,
      gastos_efvo,
      gastos_transf,
      cantidad_gastos,
    },
    conciliacion_cajon: {
      ventas_efvo,
      servicios_efvo: servicios_efvo_total,
      gastos_efvo,
      d_recargas,
      efectivo_esperado_cajon,
    },
    serie_temporal,
    cierres: cierresFiltrados,
    esquema_completo: true,
    tablas_faltantes: [],
    advertencia_esquema: null,
  };
}

// ==============================================================================
// 10. COLA OFFLINE PERSISTENTE DE SERVICIOS Y GASTOS (MODO CONECTADO - SECCIÓN 21)
// ==============================================================================

export interface ServicioColaLocal {
  idempotency_key: string;
  payload: CargaServicioInput;
  estado: "pendiente" | "sincronizando" | "sincronizada" | "requiere_revision";
  intentos: number;
  ultimo_error?: string;
  codigo_http?: number;
  creado_en: string;
  sincronizado_en?: string;
}

export interface GastoColaLocal {
  idempotency_key: string;
  payload: GastoInput;
  estado: "pendiente" | "sincronizando" | "sincronizada" | "requiere_revision";
  intentos: number;
  ultimo_error?: string;
  codigo_http?: number;
  creado_en: string;
  sincronizado_en?: string;
}

export async function guardarServicioColaLocal(
  payload: CargaServicioInput
): Promise<ServicioColaLocal> {
  const db = await openDB();
  const key =
    payload.idempotency_key ||
    `srv-${Date.now().toString(36)}-${Math.random().toString(36).substring(2, 9)}`;
  const item: ServicioColaLocal = {
    idempotency_key: key,
    payload: { ...payload, idempotency_key: key },
    estado: "pendiente",
    intentos: 0,
    creado_en: new Date().toISOString(),
  };

  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_SERVICIOS_QUEUE, "readwrite");
    const store = tx.objectStore(STORE_SERVICIOS_QUEUE);
    const req = store.put(item);
    req.onsuccess = () => resolve(item);
    req.onerror = () => reject(req.error);
  });
}

export async function listarServiciosColaLocal(): Promise<ServicioColaLocal[]> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_SERVICIOS_QUEUE, "readonly");
    const store = tx.objectStore(STORE_SERVICIOS_QUEUE);
    const req = store.getAll();
    req.onsuccess = () => {
      const items = (req.result || []) as ServicioColaLocal[];
      items.sort((a, b) => a.creado_en.localeCompare(b.creado_en));
      resolve(items);
    };
    req.onerror = () => reject(req.error);
  });
}

export async function actualizarEstadoServicioColaLocal(
  idempotency_key: string,
  cambios: Partial<ServicioColaLocal>
): Promise<void> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_SERVICIOS_QUEUE, "readwrite");
    const store = tx.objectStore(STORE_SERVICIOS_QUEUE);
    const getReq = store.get(idempotency_key);
    getReq.onsuccess = () => {
      const actual = getReq.result as ServicioColaLocal | undefined;
      if (!actual) {
        resolve();
        return;
      }
      store.put({ ...actual, ...cambios });
    };
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

export async function guardarGastoColaLocal(payload: GastoInput): Promise<GastoColaLocal> {
  const db = await openDB();
  const key =
    payload.idempotency_key ||
    `gst-${Date.now().toString(36)}-${Math.random().toString(36).substring(2, 9)}`;
  const item: GastoColaLocal = {
    idempotency_key: key,
    payload: { ...payload, idempotency_key: key },
    estado: "pendiente",
    intentos: 0,
    creado_en: new Date().toISOString(),
  };

  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_GASTOS_QUEUE, "readwrite");
    const store = tx.objectStore(STORE_GASTOS_QUEUE);
    const req = store.put(item);
    req.onsuccess = () => resolve(item);
    req.onerror = () => reject(req.error);
  });
}

export async function listarGastosColaLocal(): Promise<GastoColaLocal[]> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_GASTOS_QUEUE, "readonly");
    const store = tx.objectStore(STORE_GASTOS_QUEUE);
    const req = store.getAll();
    req.onsuccess = () => {
      const items = (req.result || []) as GastoColaLocal[];
      items.sort((a, b) => a.creado_en.localeCompare(b.creado_en));
      resolve(items);
    };
    req.onerror = () => reject(req.error);
  });
}

export async function actualizarEstadoGastoColaLocal(
  idempotency_key: string,
  cambios: Partial<GastoColaLocal>
): Promise<void> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_GASTOS_QUEUE, "readwrite");
    const store = tx.objectStore(STORE_GASTOS_QUEUE);
    const getReq = store.get(idempotency_key);
    getReq.onsuccess = () => {
      const actual = getReq.result as GastoColaLocal | undefined;
      if (!actual) {
        resolve();
        return;
      }
      store.put({ ...actual, ...cambios });
    };
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}


