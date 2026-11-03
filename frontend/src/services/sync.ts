import { VentaLocal } from "../types/sales";
import { getApiClient, IApiClient, isReadOnlySmokeActive } from "./api";
import {
  actualizarEstadoVenta,
  actualizarEstadoVentaDemo,
  listarVentasLocales,
  listarVentasDemo,
  obtenerVentaLocal,
  obtenerVentaDemo,
  listarServiciosColaLocal,
  actualizarEstadoServicioColaLocal,
  listarGastosColaLocal,
  actualizarEstadoGastoColaLocal,
} from "./db";

// Candado en memoria para evitar envíos concurrentes del mismo ticket
const inFlightSync = new Set<string>();

export interface SyncResult {
  ticket_id: string;
  estado: VentaLocal["estado"];
  codigo_http?: number;
  mensaje?: string;
}

export async function sincronizarVentaIndividual(
  ticket_id: string,
  apiClient: IApiClient = getApiClient()
): Promise<SyncResult> {
  // Protección estricta: si el modo de prueba conectado de solo lectura está activo,
  // no enviar la venta ni alterar el estado en IndexedDB (conservar pendiente intacta).
  if (isReadOnlySmokeActive(apiClient)) {
    return {
      ticket_id,
      estado: "pendiente",
      mensaje: "READ_ONLY_SMOKE_MODE: Sincronización bloqueada por modo de prueba de solo lectura.",
    };
  }

  if (inFlightSync.has(ticket_id)) {
    return {
      ticket_id,
      estado: "sincronizando",
      mensaje: "La venta ya tiene una sincronización en curso.",
    };
  }

  inFlightSync.add(ticket_id);

  let venta: VentaLocal | undefined = undefined;
  let actualizarEstado = actualizarEstadoVenta;

  try {
    const isDemoClient = typeof apiClient?.isDemo === "function" ? apiClient.isDemo() : false;

    // Buscar el ticket preferentemente en el almacén correspondiente al cliente, con fallback seguro
    venta = isDemoClient ? await obtenerVentaDemo(ticket_id) : await obtenerVentaLocal(ticket_id);
    let esStoreDemo = isDemoClient;

    if (!venta) {
      const ventaAlternativa = isDemoClient ? await obtenerVentaLocal(ticket_id) : await obtenerVentaDemo(ticket_id);
      if (ventaAlternativa) {
        venta = ventaAlternativa;
        esStoreDemo = !isDemoClient;
      }
    }

    if (!venta) {
      return {
        ticket_id,
        estado: "requiere_revision",
        mensaje: isDemoClient
          ? "Ticket no encontrado en el almacén demo ni en la cola local."
          : "Ticket no encontrado en la cola local ni en el almacén demo.",
      };
    }

    actualizarEstado = esStoreDemo ? actualizarEstadoVentaDemo : actualizarEstadoVenta;

    if (venta.estado === "sincronizada") {
      return {
        ticket_id,
        estado: "sincronizada",
        codigo_http: venta.codigo_http,
        mensaje: "La venta ya se encuentra sincronizada.",
      };
    }

    await actualizarEstado(ticket_id, { estado: "sincronizando" });
    const respuesta = await apiClient.registrarVenta(venta.payload);

    // 1. Éxito: 201 Created o 200 OK (idempotente)
    if (
      (respuesta.status === 201 || respuesta.status === 200) &&
      respuesta.data?.ticket_id === venta.ticket_id
    ) {
      await actualizarEstado(ticket_id, {
        estado: "sincronizada",
        codigo_http: respuesta.status,
        sincronizado_en: new Date().toISOString(),
        ultimo_error: undefined,
      });
      return {
        ticket_id,
        estado: "sincronizada",
        codigo_http: respuesta.status,
        mensaje: respuesta.data.mensaje,
      };
    }

    // 2. Esquema no preparado (503 SCHEMA_NOT_READY): conservar estrictamente en pendiente para cuando concluya el mantenimiento
    if (respuesta.status === 503 || (respuesta.error && respuesta.error.includes("SCHEMA_NOT_READY"))) {
      const errorMsg = respuesta.error || "Base de datos en mantenimiento: migraciones pendientes (HTTP 503).";
      await actualizarEstado(ticket_id, {
        estado: "pendiente",
        intentos: (venta ? venta.intentos : 0) + 1,
        codigo_http: 503,
        ultimo_error: errorMsg,
      });
      return {
        ticket_id,
        estado: "pendiente",
        codigo_http: 503,
        mensaje: errorMsg,
      };
    }

    // 3. Errores deterministas de validación (422) o conflicto de contenido (409)
    if (respuesta.status === 409 || respuesta.status === 422 || respuesta.status === 400) {
      const errorMsg =
        respuesta.error || `Error ${respuesta.status}: Rechazado por reglas de negocio.`;
      await actualizarEstado(ticket_id, {
        estado: "requiere_revision",
        codigo_http: respuesta.status,
        ultimo_error: errorMsg,
      });
      return {
        ticket_id,
        estado: "requiere_revision",
        codigo_http: respuesta.status,
        mensaje: errorMsg,
      };
    }

    // 3. Error del servidor (500) o respuesta inesperada: mantener en pendiente
    const errorMsg = respuesta.error || `Error HTTP ${respuesta.status}`;
    await actualizarEstado(ticket_id, {
      estado: "pendiente",
      intentos: (venta ? venta.intentos : 0) + 1,
      codigo_http: respuesta.status,
      ultimo_error: errorMsg,
    });
    return {
      ticket_id,
      estado: "pendiente",
      codigo_http: respuesta.status,
      mensaje: errorMsg,
    };
  } catch (err: unknown) {
    // 4. Excepción de red / timeout: conservar en pendiente sin perder datos
    const errorMsg = err instanceof Error ? err.message : "Fallo de conexión";
    await actualizarEstado(ticket_id, {
      estado: "pendiente",
      intentos: (venta ? venta.intentos : 0) + 1,
      ultimo_error: errorMsg,
    });
    return {
      ticket_id,
      estado: "pendiente",
      mensaje: errorMsg,
    };
  } finally {
    inFlightSync.delete(ticket_id);
  }
}

export async function sincronizarCola(
  apiClient: IApiClient = getApiClient()
): Promise<{
  procesados: number;
  exitosos: number;
  fallidos: number;
  resultados: SyncResult[];
}> {
  // Protección estricta: en modo conectado de solo lectura, abortar sin enviar ninguna venta
  if (isReadOnlySmokeActive(apiClient)) {
    return {
      procesados: 0,
      exitosos: 0,
      fallidos: 0,
      resultados: [],
    };
  }

  const isDemo = typeof apiClient?.isDemo === "function" ? apiClient.isDemo() : false;
  const todas = isDemo ? await listarVentasDemo() : await listarVentasLocales();
  // Solo sincronizar las que están estrictamente pendientes
  const pendientes = todas.filter((v) => v.estado === "pendiente");

  let exitosos = 0;
  let fallidos = 0;
  const resultados: SyncResult[] = [];

  for (const venta of pendientes) {
    const res = await sincronizarVentaIndividual(venta.ticket_id, apiClient);
    resultados.push(res);
    if (res.estado === "sincronizada") {
      exitosos++;
    } else {
      fallidos++;
    }

    // Detener la sincronización en lote si se produce un error de autenticación (401),
    // permisos insuficientes (403), límite de solicitudes (429) o si el esquema no está preparado (503 SCHEMA_NOT_READY)
    // para no reenviar a ciegas ventas adicionales sin una base de datos lista.
    if (
      res.codigo_http === 401 ||
      res.codigo_http === 403 ||
      res.codigo_http === 429 ||
      res.codigo_http === 503 ||
      (res.mensaje && res.mensaje.includes("SCHEMA_NOT_READY"))
    ) {
      break;
    }
  }

  let procesadosTotal = pendientes.length;

  if (!isDemo) {
    const resSrv = await sincronizarColaServicios(apiClient);
    const resGst = await sincronizarColaGastos(apiClient);
    procesadosTotal += resSrv.procesados + resGst.procesados;
    exitosos += resSrv.exitosos + resGst.exitosos;
    fallidos += resSrv.fallidos + resGst.fallidos;
  }

  return {
    procesados: procesadosTotal,
    exitosos,
    fallidos,
    resultados,
  };
}

export async function sincronizarColaServicios(
  apiClient: IApiClient = getApiClient()
): Promise<{ procesados: number; exitosos: number; fallidos: number }> {
  if (isReadOnlySmokeActive(apiClient)) {
    return { procesados: 0, exitosos: 0, fallidos: 0 };
  }
  const todas = await listarServiciosColaLocal();
  const pendientes = todas.filter((s) => s.estado === "pendiente");
  let exitosos = 0;
  let fallidos = 0;

  for (const item of pendientes) {
    const lockKey = `srv:${item.idempotency_key}`;
    if (inFlightSync.has(lockKey)) continue;
    inFlightSync.add(lockKey);
    await actualizarEstadoServicioColaLocal(item.idempotency_key, { estado: "sincronizando" });
    try {
      const res = await apiClient.registrarCarga(item.payload);
      if ((res.status === 201 || res.status === 200) && res.data) {
        await actualizarEstadoServicioColaLocal(item.idempotency_key, {
          estado: "sincronizada",
          codigo_http: res.status,
          sincronizado_en: new Date().toISOString(),
          ultimo_error: undefined,
        });
        exitosos++;
      } else if (res.status === 400 || res.status === 409 || res.status === 422) {
        await actualizarEstadoServicioColaLocal(item.idempotency_key, {
          estado: "requiere_revision",
          codigo_http: res.status,
          ultimo_error: res.error || `Error ${res.status}`,
        });
        fallidos++;
      } else {
        await actualizarEstadoServicioColaLocal(item.idempotency_key, {
          estado: "pendiente",
          intentos: item.intentos + 1,
          codigo_http: res.status,
          ultimo_error: res.error || `Error HTTP ${res.status}`,
        });
        fallidos++;
        if (res.status === 401 || res.status === 403 || res.status === 429 || res.status === 503) break;
      }
    } catch (err: unknown) {
      await actualizarEstadoServicioColaLocal(item.idempotency_key, {
        estado: "pendiente",
        intentos: item.intentos + 1,
        ultimo_error: err instanceof Error ? err.message : "Fallo de conexión",
      });
      fallidos++;
    } finally {
      inFlightSync.delete(lockKey);
    }
  }

  return { procesados: pendientes.length, exitosos, fallidos };
}

export async function sincronizarColaGastos(
  apiClient: IApiClient = getApiClient()
): Promise<{ procesados: number; exitosos: number; fallidos: number }> {
  if (isReadOnlySmokeActive(apiClient)) {
    return { procesados: 0, exitosos: 0, fallidos: 0 };
  }
  const todas = await listarGastosColaLocal();
  const pendientes = todas.filter((g) => g.estado === "pendiente");
  let exitosos = 0;
  let fallidos = 0;

  for (const item of pendientes) {
    const lockKey = `gst:${item.idempotency_key}`;
    if (inFlightSync.has(lockKey)) continue;
    inFlightSync.add(lockKey);
    await actualizarEstadoGastoColaLocal(item.idempotency_key, { estado: "sincronizando" });
    try {
      const res = await apiClient.registrarGasto(item.payload);
      if ((res.status === 201 || res.status === 200) && res.data) {
        await actualizarEstadoGastoColaLocal(item.idempotency_key, {
          estado: "sincronizada",
          codigo_http: res.status,
          sincronizado_en: new Date().toISOString(),
          ultimo_error: undefined,
        });
        exitosos++;
      } else if (res.status === 400 || res.status === 409 || res.status === 422) {
        await actualizarEstadoGastoColaLocal(item.idempotency_key, {
          estado: "requiere_revision",
          codigo_http: res.status,
          ultimo_error: res.error || `Error ${res.status}`,
        });
        fallidos++;
      } else {
        await actualizarEstadoGastoColaLocal(item.idempotency_key, {
          estado: "pendiente",
          intentos: item.intentos + 1,
          codigo_http: res.status,
          ultimo_error: res.error || `Error HTTP ${res.status}`,
        });
        fallidos++;
        if (res.status === 401 || res.status === 403 || res.status === 429 || res.status === 503) break;
      }
    } catch (err: unknown) {
      await actualizarEstadoGastoColaLocal(item.idempotency_key, {
        estado: "pendiente",
        intentos: item.intentos + 1,
        ultimo_error: err instanceof Error ? err.message : "Fallo de conexión",
      });
      fallidos++;
    } finally {
      inFlightSync.delete(lockKey);
    }
  }

  return { procesados: pendientes.length, exitosos, fallidos };
}
