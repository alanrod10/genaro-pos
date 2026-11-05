/**
 * Servicio de Cliente para el Laboratorio de Tesorería — GENARO TESORERÍA (Sprint 1 y 2).
 *
 * Directivas de aislamiento y fidelidad:
 * - Se comunica exclusivamente con /api/tesoreria.
 * - Desacoplado de IndexedDB de ventas, carritos y catálogos comerciales.
 * - En operaciones monetarias (entradas, salidas, traslados, reservas): fail-closed estricto.
 *   PROHIBIDO simular operaciones confirmadas si el backend no responde.
 * - En lectura de catálogo y estado inicial: fallback a los 9 fondos canónicos con saldo 0
 *   para permitir ejecución en tests y modo sin conexión sin comprometer saldos reales.
 */

import {
  ArqueoTesoreria,
  AuditoriaFondoItem,
  CierreTesoreria,
  EventoAuditoria,
  ExpedienteDiferencia,
  EstadoFondo,
  FondoTesoreria,
  FundAvailability,
  JornadaTesoreria,
  MovimientoTesoreria,
  ReservaTesoreria,
  ResumenOutbox,
  ResumenTesoreria,
  ReversionTesoreria,
  TipoArqueo,
  TrasladoTesoreria,
  TreasurySummary,
  ValidacionCierre,
  VerificacionAuditoria,
  CommercialBridgeStatus,
  CommercialEventsListResponse,
  DrainResponse,
  ReprocessResponse,
  SimplifiedSyncStatus,
  EstadoCuentaDavidResponse,
  AcreditacionDavidPayload,
  PagoDavidPayload,
  ReconocimientoDeudaHistoricaPayload,
  AnticipoPreventistaItem,
  ReposicionPreventistaItem,
  ResumenPreventistasResponse,
  CrearAnticipoPreventistaPayload,
  ReponerAnticipoPreventistaPayload,
  SaldosInicialesResponse,
  ModificarSaldoInicialPayload,
  AuditoriaSaldoInicialItem,
} from "../types/tesoreria";
import { addMoney, subtractMoney } from "../utils/money";

function getApiBaseUrl(): string {
  const envUrl = (import.meta as any).env?.VITE_API_URL;
  if (envUrl && typeof envUrl === "string" && envUrl.trim()) {
    return `${envUrl.replace(/\/+$/, "")}/api/tesoreria`;
  }
  if (
    typeof window !== "undefined" &&
    window.location?.origin &&
    window.location.origin !== "null" &&
    !window.location.origin.startsWith("file:")
  ) {
    return `${window.location.origin}/api/tesoreria`;
  }
  return "http://localhost:8000/api/tesoreria";
}

function getAuthHeaders(idempotencyKey?: string): HeadersInit {
  const token = typeof localStorage !== "undefined" ? localStorage.getItem("genaro_access_token") : null;
  return {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}),
  };
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let errorDetail = `Error HTTP ${res.status}: ${res.statusText}`;
    try {
      const errJson = await res.json();
      if (errJson && errJson.detail) {
        errorDetail = typeof errJson.detail === "string" ? errJson.detail : JSON.stringify(errJson.detail);
      }
    } catch {
      // Usar statusText
    }
    throw new Error(errorDetail);
  }
  return await res.json();
}

// Catálogo canónico de fallback para modo desconectado / pruebas unitarias de UI (Catálogo Aprobado Sprint 4)
const FONDOS_CANONICOS_FALLBACK: FondoTesoreria[] = [
  {
    id: 1,
    codigo: "1110",
    nombre: "Drugstore Diario",
    naturaleza: "EFECTIVO",
    titularidad: "PROPIA",
    moneda: "ARS",
    estado: "ACTIVO",
    saldo_centavos: 0,
    saldo_formateado: "$0,00",
    disponibilidad_inmediata: true,
    permite_sobregiro: 0,
    version: 1,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    creado_por: "SISTEMA_LAB",
    actualizado_por: "SISTEMA_LAB",
  },
  {
    id: 2,
    codigo: "1120",
    nombre: "Drugstore Acumulado",
    naturaleza: "EFECTIVO",
    titularidad: "PROPIA",
    moneda: "ARS",
    estado: "ACTIVO",
    saldo_centavos: 0,
    saldo_formateado: "$0,00",
    disponibilidad_inmediata: true,
    permite_sobregiro: 0,
    version: 1,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    creado_por: "SISTEMA_LAB",
    actualizado_por: "SISTEMA_LAB",
  },
  {
    id: 3,
    codigo: "1130",
    nombre: "Prepay Efectivo",
    naturaleza: "EFECTIVO",
    titularidad: "PROPIA",
    moneda: "ARS",
    estado: "ACTIVO",
    saldo_centavos: 0,
    saldo_formateado: "$0,00",
    disponibilidad_inmediata: true,
    permite_sobregiro: 0,
    version: 1,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    creado_por: "SISTEMA_LAB",
    actualizado_por: "SISTEMA_LAB",
  },
  {
    id: 4,
    codigo: "1140",
    nombre: "Adicionales Efectivo",
    naturaleza: "ADICIONALES_PARTICULARES",
    titularidad: "PROPIA",
    moneda: "ARS",
    estado: "ACTIVO",
    saldo_centavos: 0,
    saldo_formateado: "$0,00",
    disponibilidad_inmediata: true,
    permite_sobregiro: 0,
    version: 1,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    creado_por: "SISTEMA_LAB",
    actualizado_por: "SISTEMA_LAB",
  },
  {
    id: 5,
    codigo: "1150",
    nombre: "Claro Efectivo",
    naturaleza: "RECAUDACION_TERCEROS",
    titularidad: "CUSTODIA",
    moneda: "ARS",
    estado: "ACTIVO",
    saldo_centavos: 0,
    saldo_formateado: "$0,00",
    disponibilidad_inmediata: false,
    permite_sobregiro: 0,
    version: 1,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    creado_por: "SISTEMA_LAB",
    actualizado_por: "SISTEMA_LAB",
  },
  {
    id: 6,
    codigo: "1210",
    nombre: "Mercado Pago Propio",
    naturaleza: "BILLETERA_VIRTUAL",
    titularidad: "PROPIA",
    moneda: "ARS",
    estado: "ACTIVO",
    saldo_centavos: 0,
    saldo_formateado: "$0,00",
    disponibilidad_inmediata: true,
    permite_sobregiro: 0,
    version: 1,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    creado_por: "SISTEMA_LAB",
    actualizado_por: "SISTEMA_LAB",
  },
  {
    id: 7,
    codigo: "1220",
    nombre: "Prepay Digital Saldo",
    naturaleza: "BILLETERA_VIRTUAL",
    titularidad: "PROPIA",
    moneda: "ARS",
    estado: "ACTIVO",
    saldo_centavos: 0,
    saldo_formateado: "$0,00",
    disponibilidad_inmediata: true,
    permite_sobregiro: 0,
    version: 1,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    creado_por: "SISTEMA_LAB",
    actualizado_por: "SISTEMA_LAB",
  },
  {
    id: 8,
    codigo: "1230",
    nombre: "Claro Comercios Digital Saldo",
    naturaleza: "BILLETERA_VIRTUAL",
    titularidad: "PROPIA",
    moneda: "ARS",
    estado: "ACTIVO",
    saldo_centavos: 0,
    saldo_formateado: "$0,00",
    disponibilidad_inmediata: true,
    permite_sobregiro: 0,
    version: 1,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    creado_por: "SISTEMA_LAB",
    actualizado_por: "SISTEMA_LAB",
  },
  {
    id: 9,
    codigo: "1310",
    nombre: "Cobranzas en Tránsito Preventistas",
    naturaleza: "TRANSITO_PREVENTISTAS",
    titularidad: "TRANSITO",
    moneda: "ARS",
    estado: "ACTIVO",
    saldo_centavos: 0,
    saldo_formateado: "$0,00",
    disponibilidad_inmediata: false,
    permite_sobregiro: 0,
    version: 1,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    creado_por: "SISTEMA_LAB",
    actualizado_por: "SISTEMA_LAB",
  },
];

let mockJornadaActiva: JornadaTesoreria | null = null;
let mockNumeroJornada = 1;
const mockFondos: FondoTesoreria[] = JSON.parse(JSON.stringify(FONDOS_CANONICOS_FALLBACK));
const mockAuditoria: Record<string, AuditoriaFondoItem[]> = {};

mockFondos.forEach((f) => {
  mockAuditoria[f.codigo] = [
    {
      id: f.id,
      fondo_id: f.id,
      codigo: f.codigo,
      accion: "INSERT",
      usuario: "SISTEMA_LAB",
      valores_previos: null,
      valores_nuevos: { ...f },
      motivo: "Alta inicial en catálogo",
      timestamp_utc: f.created_at,
    },
  ];
});

export const TesoreriaService = {
  async obtenerResumen(): Promise<ResumenTesoreria> {
    try {
      const res = await fetch(`${getApiBaseUrl()}/resumen`, {
        headers: getAuthHeaders(),
      });
      if (res.ok) return await res.json();
    } catch {
      // Fallback desconectado / pruebas de interfaz
    }
    return {
      total_fondos: mockFondos.length,
      fondos_activos: mockFondos.filter((f) => f.estado === "ACTIVO").length,
      total_saldo_registrado_centavos: 0,
      total_saldo_registrado_formateado: "$0,00",
      total_saldo_disponible_centavos: 0,
      total_saldo_disponible_formateado: "$0,00",
      jornada_activa: mockJornadaActiva,
      aislamiento_confirmado: true,
      db_path: "db/tesoreria_lab.db",
    };
  },

  async obtenerFondos(): Promise<FondoTesoreria[]> {
    try {
      const res = await fetch(`${getApiBaseUrl()}/fondos`, {
        headers: getAuthHeaders(),
      });
      if (res.ok) return await res.json();
    } catch {
      // Fallback
    }
    return mockFondos;
  },

  async obtenerFondoDetalle(codigo: string): Promise<FondoTesoreria> {
    try {
      const res = await fetch(`${getApiBaseUrl()}/fondos/${codigo}`, {
        headers: getAuthHeaders(),
      });
      if (res.ok) return await res.json();
    } catch {
      // Fallback
    }
    const found = mockFondos.find((f) => f.codigo === codigo);
    if (!found) throw new Error(`Fondo ${codigo} no encontrado`);
    return found;
  },

  async obtenerAuditoriaFondo(codigo: string): Promise<AuditoriaFondoItem[]> {
    try {
      const res = await fetch(`${getApiBaseUrl()}/fondos/${codigo}/auditoria`, {
        headers: getAuthHeaders(),
      });
      if (res.ok) return await res.json();
    } catch {
      // Fallback
    }
    return mockAuditoria[codigo] || [];
  },

  async actualizarEstadoFondo(
    codigo: string,
    nuevoEstado: EstadoFondo,
    motivo: string
  ): Promise<FondoTesoreria> {
    try {
      const res = await fetch(`${getApiBaseUrl()}/fondos/${codigo}/estado`, {
        method: "PATCH",
        headers: getAuthHeaders(),
        body: JSON.stringify({ nuevo_estado: nuevoEstado, motivo }),
      });
      if (res.ok) return await res.json();
    } catch {
      // Fallback
    }

    const idx = mockFondos.findIndex((f) => f.codigo === codigo);
    if (idx === -1) throw new Error(`Fondo ${codigo} no encontrado`);

    const fAnt = mockFondos[idx];
    const fAct: FondoTesoreria = {
      ...fAnt,
      estado: nuevoEstado,
      version: fAnt.version + 1,
      updated_at: new Date().toISOString(),
      actualizado_por: "alan",
    };
    mockFondos[idx] = fAct;

    if (!mockAuditoria[codigo]) mockAuditoria[codigo] = [];
    mockAuditoria[codigo].unshift({
      id: Date.now(),
      fondo_id: fAct.id,
      codigo,
      accion: "UPDATE",
      usuario: "alan",
      valores_previos: { estado: fAnt.estado, version: fAnt.version },
      valores_nuevos: { estado: fAct.estado, version: fAct.version },
      motivo,
      timestamp_utc: fAct.updated_at,
    });

    return fAct;
  },

  async obtenerJornadaActiva(): Promise<JornadaTesoreria | null> {
    try {
      const res = await fetch(`${getApiBaseUrl()}/jornada/actual`, {
        headers: getAuthHeaders(),
      });
      if (res.ok) return await res.json();
    } catch {
      // Fallback
    }
    return mockJornadaActiva;
  },

  async abrirJornada(_usuario?: string, observaciones?: string): Promise<JornadaTesoreria> {
    try {
      const res = await fetch(`${getApiBaseUrl()}/jornada/abrir`, {
        method: "POST",
        headers: getAuthHeaders(),
        body: JSON.stringify({ observaciones }),
      });
      if (res.ok) return await res.json();
    } catch {
      // Fallback
    }

    if (mockJornadaActiva) {
      throw new Error("Ya existe una jornada activa");
    }

    let totalApertura = 0;
    const detalles = mockFondos.map((f, i) => {
      totalApertura = addMoney(totalApertura, f.saldo_centavos);
      return {
        id: i + 1,
        jornada_id: mockNumeroJornada,
        fondo_id: f.id,
        codigo_fondo: f.codigo,
        nombre_fondo: f.nombre,
        naturaleza: f.naturaleza,
        titularidad: f.titularidad,
        saldo_apertura_centavos: f.saldo_centavos,
        saldo_cierre_centavos: null,
        diferencia_centavos: null,
      };
    });

    mockJornadaActiva = {
      id: mockNumeroJornada,
      numero_jornada: mockNumeroJornada,
      fecha_operativa: new Date().toISOString().slice(0, 10),
      estado: "ABIERTA",
      apertura_timestamp_utc: new Date().toISOString(),
      cierre_timestamp_utc: null,
      usuario_apertura: _usuario || "alan",
      usuario_cierre: null,
      total_apertura_centavos: totalApertura,
      total_cierre_centavos: null,
      observaciones: observaciones || null,
      version: 1,
      detalles,
    };

    return mockJornadaActiva;
  },

  async cerrarJornada(
    jornadaId: number,
    arqueoPorFondo?: Record<string, number>,
    observaciones?: string
  ): Promise<JornadaTesoreria> {
    try {
      const res = await fetch(`${getApiBaseUrl()}/jornada/cerrar`, {
        method: "POST",
        headers: getAuthHeaders(),
        body: JSON.stringify({
          jornada_id: jornadaId,
          arqueo_centavos_por_fondo: arqueoPorFondo,
          observaciones,
        }),
      });
      if (res.ok) return await res.json();
    } catch {
      // Fallback
    }

    if (!mockJornadaActiva || mockJornadaActiva.id !== jornadaId) {
      throw new Error("Jornada no coincide o no está activa");
    }

    let totalCierre = 0;
    const detallesCerrados = (mockJornadaActiva.detalles || []).map((d) => {
      const saldoArqueado =
        arqueoPorFondo && d.codigo_fondo in arqueoPorFondo
          ? arqueoPorFondo[d.codigo_fondo]
          : d.saldo_apertura_centavos;
      const diferencia = subtractMoney(saldoArqueado, d.saldo_apertura_centavos);
      totalCierre = addMoney(totalCierre, saldoArqueado);
      return {
        ...d,
        saldo_cierre_centavos: saldoArqueado,
        diferencia_centavos: diferencia,
      };
    });

    const jCerrada: JornadaTesoreria = {
      ...mockJornadaActiva,
      estado: "CERRADA",
      cierre_timestamp_utc: new Date().toISOString(),
      usuario_cierre: "alan",
      total_cierre_centavos: totalCierre,
      observaciones: observaciones || mockJornadaActiva.observaciones,
      detalles: detallesCerrados,
      version: mockJornadaActiva.version + 1,
    };

    mockJornadaActiva = null;
    mockNumeroJornada++;
    return jCerrada;
  },

  // =====================================================================
  // LAB-005 — MOVIMIENTOS (FAIL-CLOSED: NO SIMULA OPERACIONES CONFIRMADAS)
  // =====================================================================
  async registrarEntrada(
    codigoFondo: string,
    montoCentavos: number,
    contrapartida: string,
    motivo: string,
    idempotencyKey?: string,
    referenciaEvidencia?: string
  ): Promise<MovimientoTesoreria> {
    const res = await fetch(`${getApiBaseUrl()}/movimientos/entrada`, {
      method: "POST",
      headers: getAuthHeaders(idempotencyKey),
      body: JSON.stringify({
        codigo_fondo: codigoFondo,
        monto_centavos: montoCentavos,
        contrapartida,
        motivo,
        referencia_evidencia: referenciaEvidencia,
      }),
    });
    return handleResponse<MovimientoTesoreria>(res);
  },

  async registrarSalida(
    codigoFondo: string,
    montoCentavos: number,
    contrapartida: string,
    motivo: string,
    idempotencyKey?: string,
    referenciaEvidencia?: string
  ): Promise<MovimientoTesoreria> {
    const res = await fetch(`${getApiBaseUrl()}/movimientos/salida`, {
      method: "POST",
      headers: getAuthHeaders(idempotencyKey),
      body: JSON.stringify({
        codigo_fondo: codigoFondo,
        monto_centavos: montoCentavos,
        contrapartida,
        motivo,
        referencia_evidencia: referenciaEvidencia,
      }),
    });
    return handleResponse<MovimientoTesoreria>(res);
  },

  async obtenerMovimientos(
    codigoFondo?: string,
    jornadaId?: number,
    limite: number = 50,
    movementType?: string,
    search?: string
  ): Promise<MovimientoTesoreria[]> {
    try {
      const params = new URLSearchParams();
      if (codigoFondo) params.set("codigo_fondo", codigoFondo);
      if (jornadaId) params.set("jornada_id", jornadaId.toString());
      if (movementType) params.set("movement_type", movementType);
      if (search) params.set("search", search);
      params.set("limite", limite.toString());

      const res = await fetch(`${getApiBaseUrl()}/movimientos?${params.toString()}`, {
        headers: getAuthHeaders(),
      });
      if (res.ok) return await res.json();
    } catch {
      // Fallback
    }
    return [];
  },

  // =====================================================================
  // LAB-006 — TRASLADOS (FAIL-CLOSED)
  // =====================================================================
  async trasladarFondos(
    codigoOrigen: string,
    codigoDestino: string,
    montoCentavos: number,
    motivo: string,
    idempotencyKey?: string,
    evidencia?: string
  ): Promise<TrasladoTesoreria> {
    const res = await fetch(`${getApiBaseUrl()}/traslados`, {
      method: "POST",
      headers: getAuthHeaders(idempotencyKey),
      body: JSON.stringify({
        origin_fund_code: codigoOrigen,
        destination_fund_code: codigoDestino,
        amount_cents: montoCentavos,
        reason: motivo,
        evidence_reference: evidencia,
        codigo_origen: codigoOrigen,
        codigo_destino: codigoDestino,
        monto_centavos: montoCentavos,
        motivo,
        evidencia,
      }),
    });
    return handleResponse<TrasladoTesoreria>(res);
  },

  async confirmarRecepcionTraslado(
    transferId: string,
    observaciones?: string,
    idempotencyKey?: string
  ): Promise<TrasladoTesoreria> {
    const res = await fetch(`${getApiBaseUrl()}/traslados/${transferId}/confirmar`, {
      method: "POST",
      headers: getAuthHeaders(idempotencyKey),
      body: JSON.stringify({
        observaciones,
      }),
    });
    return handleResponse<TrasladoTesoreria>(res);
  },

  async cancelarTraslado(
    transferId: string,
    motivo: string,
    idempotencyKey?: string
  ): Promise<TrasladoTesoreria> {
    const res = await fetch(`${getApiBaseUrl()}/traslados/${transferId}/cancelar`, {
      method: "POST",
      headers: getAuthHeaders(idempotencyKey),
      body: JSON.stringify({
        reason: motivo,
        motivo,
      }),
    });
    return handleResponse<TrasladoTesoreria>(res);
  },

  async obtenerTraslados(
    limite: number = 50,
    status?: string,
    originFundCode?: string,
    destinationFundCode?: string,
    search?: string
  ): Promise<TrasladoTesoreria[]> {
    try {
      const params = new URLSearchParams();
      params.append("limite", String(limite));
      if (status && status !== "ALL") params.append("status", status);
      if (originFundCode) params.append("origin_fund_code", originFundCode);
      if (destinationFundCode) params.append("destination_fund_code", destinationFundCode);
      if (search && search.trim()) params.append("search", search.trim());

      const res = await fetch(`${getApiBaseUrl()}/traslados?${params.toString()}`, {
        headers: getAuthHeaders(),
      });
      if (res.ok) return await res.json();
    } catch {
      // Fallback
    }
    return [];
  },

  // =====================================================================
  // LAB-007 — RESERVAS (FAIL-CLOSED)
  // =====================================================================
  async crearReserva(
    codigoFondo: string,
    montoCentavos: number,
    motivo: string,
    beneficiario?: string,
    obligationId?: string,
    idempotencyKey?: string
  ): Promise<ReservaTesoreria> {
    const res = await fetch(`${getApiBaseUrl()}/reservas`, {
      method: "POST",
      headers: getAuthHeaders(idempotencyKey),
      body: JSON.stringify({
        codigo_fondo: codigoFondo,
        monto_centavos: montoCentavos,
        motivo,
        beneficiario,
        obligation_id: obligationId,
      }),
    });
    return handleResponse<ReservaTesoreria>(res);
  },

  async aplicarReserva(
    reservationId: string,
    montoCentavos?: number,
    motivo?: string,
    idempotencyKey?: string
  ): Promise<ReservaTesoreria> {
    const res = await fetch(`${getApiBaseUrl()}/reservas/${reservationId}/aplicar`, {
      method: "POST",
      headers: getAuthHeaders(idempotencyKey),
      body: JSON.stringify({
        monto_centavos: montoCentavos,
        motivo: motivo || "Aplicación de reserva",
      }),
    });
    return handleResponse<ReservaTesoreria>(res);
  },

  async liberarReserva(
    reservationId: string,
    montoCentavos?: number,
    motivo?: string,
    idempotencyKey?: string
  ): Promise<ReservaTesoreria> {
    const res = await fetch(`${getApiBaseUrl()}/reservas/${reservationId}/liberar`, {
      method: "POST",
      headers: getAuthHeaders(idempotencyKey),
      body: JSON.stringify({
        monto_centavos: montoCentavos,
        motivo: motivo || "Liberación de reserva",
      }),
    });
    return handleResponse<ReservaTesoreria>(res);
  },

  async obtenerReserva(reservationId: string): Promise<ReservaTesoreria> {
    const res = await fetch(`${getApiBaseUrl()}/reservas/${reservationId}`, {
      headers: getAuthHeaders(),
    });
    return handleResponse<ReservaTesoreria>(res);
  },

  async obtenerReservas(
    codigoFondo?: string,
    estado?: string,
    limite: number = 50
  ): Promise<ReservaTesoreria[]> {
    try {
      const params = new URLSearchParams();
      if (codigoFondo) params.set("codigo_fondo", codigoFondo);
      if (estado) params.set("estado", estado);
      params.set("limite", limite.toString());

      const res = await fetch(`${getApiBaseUrl()}/reservas?${params.toString()}`, {
        headers: getAuthHeaders(),
      });
      if (res.ok) return await res.json();
    } catch {
      // Fallback
    }
    return [];
  },

  // =====================================================================
  // LAB-008 — DISPONIBILIDAD Y RESUMEN
  // =====================================================================
  async obtenerBalanceFondo(codigoFondo: string): Promise<{ codigo_fondo: string; saldo_registrado_centavos: number; moneda: string }> {
    const res = await fetch(`${getApiBaseUrl()}/fondos/${codigoFondo}/balance`, {
      headers: getAuthHeaders(),
    });
    return handleResponse(res);
  },

  async obtenerDisponibilidadFondo(codigoFondo: string): Promise<FundAvailability> {
    const res = await fetch(`${getApiBaseUrl()}/fondos/${codigoFondo}/disponibilidad`, {
      headers: getAuthHeaders(),
    });
    return handleResponse<FundAvailability>(res);
  },

  async obtenerResumenDisponibilidad(): Promise<TreasurySummary> {
    try {
      const res = await fetch(`${getApiBaseUrl()}/disponibilidad/resumen`, {
        headers: getAuthHeaders(),
      });
      if (res.ok) return await res.json();
    } catch {
      // Fallback
    }
    return {
      fondos_propios: {
        total_registrado_cents: 0,
        total_registrado_formatted: "$0,00",
        total_reservado_cents: 0,
        total_reservado_formatted: "$0,00",
        total_disponible_cents: 0,
        total_disponible_formatted: "$0,00",
      },
      fondos_administrados_custodia: {
        total_registrado_cents: 0,
        total_registrado_formatted: "$0,00",
        total_reservado_cents: 0,
        total_reservado_formatted: "$0,00",
        total_disponible_cents: 0,
        total_disponible_formatted: "$0,00",
      },
      dinero_en_transito: {
        amount_cents: 0,
        amount_formatted: "$0,00",
        traslados_pendientes_count: 0,
      },
      total_consolidado: {
        total_registrado_cents: 0,
        total_registrado_formatted: "$0,00",
      },
      fondos: [],
    };
  },

  // Aliases ergonómicos para componentes UI
  iniciarTraslado(
    codigoOrigen: string,
    codigoDestino: string,
    montoCentavos: number,
    motivo: string,
    idempotencyKey?: string,
    evidencia?: string
  ) {
    return this.trasladarFondos(codigoOrigen, codigoDestino, montoCentavos, motivo, idempotencyKey, evidencia);
  },

  confirmarTraslado(transferId: string, idempotencyKey?: string, observaciones?: string) {
    return this.confirmarRecepcionTraslado(transferId, observaciones, idempotencyKey);
  },

  obtenerHistorialReserva(reservationId: string) {
    return this.obtenerReserva(reservationId);
  },

  // =====================================================================
  // SPRINT 3 — LAB-009: ARQUEOS Y EXPEDIENTES DE DIFERENCIAS
  // =====================================================================

  async crearArqueo(
    codigoFondo: string,
    tipoArqueo: TipoArqueo,
    montoVerificadoCentavos: number,
    observaciones?: string,
    crearExpedienteDiferencia: boolean = false,
    notasInvestigacion?: string,
    turno?: string,
    esDigital: boolean = false,
    evidenciaReferencia?: string,
    fechaConsulta?: string
  ): Promise<ArqueoTesoreria> {
    const res = await fetch(`${getApiBaseUrl()}/arqueos`, {
      method: "POST",
      headers: getAuthHeaders(),
      body: JSON.stringify({
        codigo_fondo: codigoFondo,
        tipo_arqueo: tipoArqueo,
        monto_verificado_centavos: montoVerificadoCentavos,
        observaciones: observaciones || null,
        crear_expediente_diferencia: crearExpedienteDiferencia,
        notas_investigacion: notasInvestigacion || null,
        turno: turno || null,
        es_digital: esDigital,
        evidencia_referencia: evidenciaReferencia || null,
        fecha_consulta: fechaConsulta || null,
      }),
    });
    return handleResponse<ArqueoTesoreria>(res);
  },

  async conciliarCuentaDigital(
    codigoFondo: string,
    saldoDeclaradoCentavos: number,
    evidenciaReferencia?: string,
    observaciones?: string,
    fechaConsulta?: string,
    crearExpediente?: boolean,
    notasInvestigacion?: string
  ): Promise<ArqueoTesoreria> {
    return this.crearArqueo(
      codigoFondo,
      "TOTAL",
      saldoDeclaradoCentavos,
      observaciones,
      crearExpediente ?? false,
      notasInvestigacion,
      undefined,
      true,
      evidenciaReferencia,
      fechaConsulta
    );
  },

  async obtenerTrasladosTransitoFondo(codigoFondo: string): Promise<TrasladoTesoreria[]> {
    const res = await fetch(`${getApiBaseUrl()}/fondos/${codigoFondo}/traslados-transito`, {
      headers: getAuthHeaders(),
    });
    return handleResponse<TrasladoTesoreria[]>(res);
  },

  async obtenerArqueo(reconciliationId: string): Promise<ArqueoTesoreria> {
    const res = await fetch(`${getApiBaseUrl()}/arqueos/${reconciliationId}`, {
      headers: getAuthHeaders(),
    });
    return handleResponse<ArqueoTesoreria>(res);
  },

  async obtenerHistorialArqueos(codigoFondo?: string, jornadaId?: number, limite: number = 50): Promise<ArqueoTesoreria[]> {
    const params = new URLSearchParams();
    if (codigoFondo) params.append("codigo_fondo", codigoFondo);
    if (jornadaId) params.append("jornada_id", String(jornadaId));
    if (limite) params.append("limite", String(limite));

    const res = await fetch(`${getApiBaseUrl()}/arqueos?${params.toString()}`, {
      headers: getAuthHeaders(),
    });
    return handleResponse<ArqueoTesoreria[]>(res);
  },

  async autorizarDiferenciaArqueo(
    reconciliationId: string,
    observaciones?: string
  ): Promise<ArqueoTesoreria> {
    const res = await fetch(`${getApiBaseUrl()}/arqueos/${reconciliationId}/autorizar`, {
      method: "POST",
      headers: getAuthHeaders(),
      body: JSON.stringify({
        observaciones: observaciones || null,
      }),
    });
    return handleResponse<ArqueoTesoreria>(res);
  },

  async crearExpedienteDiferencia(reconciliationId: string, notasInvestigacion: string): Promise<ExpedienteDiferencia> {
    const res = await fetch(`${getApiBaseUrl()}/arqueos/${reconciliationId}/expediente`, {
      method: "POST",
      headers: getAuthHeaders(),
      body: JSON.stringify({
        notas_investigacion: notasInvestigacion,
      }),
    });
    return handleResponse<ExpedienteDiferencia>(res);
  },

  async resolverExpedienteDiferencia(
    caseId: string,
    notasResolucion: string,
    referenciaEvidencia: string
  ): Promise<ExpedienteDiferencia> {
    const res = await fetch(`${getApiBaseUrl()}/expedientes/${caseId}/resolver`, {
      method: "POST",
      headers: getAuthHeaders(),
      body: JSON.stringify({
        notas_resolucion: notasResolucion,
        referencia_evidencia: referenciaEvidencia,
      }),
    });
    return handleResponse<ExpedienteDiferencia>(res);
  },

  async obtenerExpedientesDiferencias(
    codigoFondo?: string,
    jornadaId?: number,
    estado?: string,
    limite: number = 50
  ): Promise<ExpedienteDiferencia[]> {
    const params = new URLSearchParams();
    if (codigoFondo) params.append("codigo_fondo", codigoFondo);
    if (jornadaId) params.append("jornada_id", String(jornadaId));
    if (estado) params.append("estado", estado);
    if (limite) params.append("limite", String(limite));

    const res = await fetch(`${getApiBaseUrl()}/expedientes?${params.toString()}`, {
      headers: getAuthHeaders(),
    });
    return handleResponse<ExpedienteDiferencia[]>(res);
  },

  // =====================================================================
  // SPRINT 3 — LAB-010: CIERRES DEFINITIVOS Y PROTOCOLO
  // =====================================================================

  async iniciarCierre(journalId: number): Promise<JornadaTesoreria> {
    const res = await fetch(`${getApiBaseUrl()}/cierres/iniciar`, {
      method: "POST",
      headers: getAuthHeaders(),
      body: JSON.stringify({ journal_id: journalId }),
    });
    return handleResponse<JornadaTesoreria>(res);
  },

  async validarCierre(
    journalId: number,
    allowPendingTransfers: boolean = false,
    allowNonzero1110: boolean = false
  ): Promise<ValidacionCierre> {
    const params = new URLSearchParams();
    if (allowPendingTransfers) params.append("allow_pending_transfers", "true");
    if (allowNonzero1110) params.append("allow_nonzero_1110", "true");
    const qs = params.toString() ? `?${params.toString()}` : "";
    const res = await fetch(`${getApiBaseUrl()}/cierres/validar/${journalId}${qs}`, {
      headers: getAuthHeaders(),
    });
    return handleResponse<ValidacionCierre>(res);
  },

  async ejecutarVaciado1110(journalId: number, motivo?: string): Promise<{ transfer_id: string; amount_cents: number; message: string }> {
    const res = await fetch(`${getApiBaseUrl()}/cierres/vaciado-1110`, {
      method: "POST",
      headers: getAuthHeaders(),
      body: JSON.stringify({ journal_id: journalId, motivo }),
    });
    return handleResponse<{ transfer_id: string; amount_cents: number; message: string }>(res);
  },

  async ejecutarVaciado1160(journalId: number, motivo?: string): Promise<{ transfer_id?: string; amount_cents: number; message: string; status?: string }> {
    const res = await fetch(`${getApiBaseUrl()}/cierres/vaciado-1160`, {
      method: "POST",
      headers: getAuthHeaders(),
      body: JSON.stringify({ journal_id: journalId, motivo }),
    });
    return handleResponse<{ transfer_id?: string; amount_cents: number; message: string; status?: string }>(res);
  },

  async confirmarCierre(
    journalId: number,
    observaciones?: string,
    allowPendingTransfers: boolean = false,
    allowNonzero1110: boolean = false
  ): Promise<CierreTesoreria> {
    const res = await fetch(`${getApiBaseUrl()}/cierres/confirmar`, {
      method: "POST",
      headers: getAuthHeaders(),
      body: JSON.stringify({
        journal_id: journalId,
        observaciones,
        allow_pending_transfers: allowPendingTransfers,
        allow_nonzero_1110: allowNonzero1110,
      }),
    });
    return handleResponse<CierreTesoreria>(res);
  },

  async obtenerHistorialCierres(limite: number = 50): Promise<CierreTesoreria[]> {
    const res = await fetch(`${getApiBaseUrl()}/cierres?limite=${limite}`, {
      headers: getAuthHeaders(),
    });
    return handleResponse<CierreTesoreria[]>(res);
  },

  async obtenerCierre(journalId: number): Promise<CierreTesoreria> {
    const res = await fetch(`${getApiBaseUrl()}/cierres/${journalId}`, {
      headers: getAuthHeaders(),
    });
    return handleResponse<CierreTesoreria>(res);
  },

  async abrirSiguienteJornada(observaciones?: string): Promise<JornadaTesoreria> {
    const res = await fetch(`${getApiBaseUrl()}/jornada/siguiente`, {
      method: "POST",
      headers: getAuthHeaders(),
      body: JSON.stringify({ observaciones }),
    });
    return handleResponse<JornadaTesoreria>(res);
  },

  // =====================================================================
  // SPRINT 3 — LAB-011: REVERSIONES INMUTABLES
  // =====================================================================

  async revertirMovimiento(
    movementId: string,
    motivo: string,
    referenciaEvidencia?: string,
    montoParcialCentavos?: number,
    idempotencyKey?: string
  ): Promise<ReversionTesoreria> {
    const res = await fetch(`${getApiBaseUrl()}/reversiones/movimiento`, {
      method: "POST",
      headers: getAuthHeaders(idempotencyKey),
      body: JSON.stringify({
        movement_id: movementId,
        motivo: motivo,
        referencia_evidencia: referenciaEvidencia || null,
        monto_parcial_centavos: montoParcialCentavos || null,
      }),
    });
    return handleResponse<ReversionTesoreria>(res);
  },

  async revertirTraslado(
    transferId: string,
    motivo: string,
    referenciaEvidencia?: string,
    idempotencyKey?: string
  ): Promise<ReversionTesoreria> {
    const res = await fetch(`${getApiBaseUrl()}/reversiones/traslado`, {
      method: "POST",
      headers: getAuthHeaders(idempotencyKey),
      body: JSON.stringify({
        transfer_id: transferId,
        motivo: motivo,
        referencia_evidencia: referenciaEvidencia || null,
      }),
    });
    return handleResponse<ReversionTesoreria>(res);
  },

  async revertirOperacionReserva(
    reservationId: string,
    operationId: number,
    motivo: string,
    referenciaEvidencia?: string,
    idempotencyKey?: string
  ): Promise<ReversionTesoreria> {
    const res = await fetch(`${getApiBaseUrl()}/reversiones/reserva`, {
      method: "POST",
      headers: getAuthHeaders(idempotencyKey),
      body: JSON.stringify({
        reservation_id: reservationId,
        operation_id: operationId,
        motivo: motivo,
        referencia_evidencia: referenciaEvidencia || null,
      }),
    });
    return handleResponse<ReversionTesoreria>(res);
  },

  async obtenerReversiones(tipo?: string, limite: number = 50): Promise<ReversionTesoreria[]> {
    const params = new URLSearchParams();
    if (tipo) params.append("tipo", tipo);
    if (limite) params.append("limite", String(limite));

    const res = await fetch(`${getApiBaseUrl()}/reversiones?${params.toString()}`, {
      headers: getAuthHeaders(),
    });
    return handleResponse<ReversionTesoreria[]>(res);
  },

  // =====================================================================
  // SPRINT 3 — LAB-012: AUDITORÍA Y OUTBOX
  // =====================================================================

  async obtenerCadenaAuditoria(
    tipoEntidad?: string,
    entidadId?: string,
    tipoEvento?: string,
    limite: number = 100
  ): Promise<EventoAuditoria[]> {
    const params = new URLSearchParams();
    if (tipoEntidad) params.append("tipo_entidad", tipoEntidad);
    if (entidadId) params.append("entidad_id", entidadId);
    if (tipoEvento) params.append("tipo_evento", tipoEvento);
    if (limite) params.append("limite", String(limite));

    const res = await fetch(`${getApiBaseUrl()}/auditoria/cadena?${params.toString()}`, {
      headers: getAuthHeaders(),
    });
    return handleResponse<EventoAuditoria[]>(res);
  },

  async verificarAuditoria(): Promise<VerificacionAuditoria> {
    const res = await fetch(`${getApiBaseUrl()}/auditoria/verificar`, {
      headers: getAuthHeaders(),
    });
    return handleResponse<VerificacionAuditoria>(res);
  },

  async obtenerEstadoOutbox(): Promise<ResumenOutbox> {
    const res = await fetch(`${getApiBaseUrl()}/outbox/estado`, {
      headers: getAuthHeaders(),
    });
    return handleResponse<ResumenOutbox>(res);
  },

  async procesarOutbox(consumerId: string = "LOCAL_LAB_WORKER", maxBatch: number = 50): Promise<{ processed_count: number; dead_letter_count: number }> {
    const res = await fetch(`${getApiBaseUrl()}/outbox/procesar`, {
      method: "POST",
      headers: getAuthHeaders(),
      body: JSON.stringify({
        consumer_id: consumerId,
        max_batch: maxBatch,
      }),
    });
    return handleResponse<{ processed_count: number; dead_letter_count: number }>(res);
  },

  async reprocesarOutbox(eventId: string): Promise<{ event_id: string; status: string; message: string }> {
    const res = await fetch(`${getApiBaseUrl()}/outbox/${eventId}/reprocesar`, {
      method: "POST",
      headers: getAuthHeaders(),
    });
    return handleResponse<{ event_id: string; status: string; message: string }>(res);
  },

  // =====================================================================
  // SPRINT 3 — FASE 0: EGRESO RESERVADO ATÓMICO
  // =====================================================================

  async ejecutarEgresoReservado(
    reservationId: string,
    montoCentavos: number,
    contrapartida: string,
    motivo: string,
    idempotencyKey?: string,
    referenciaEvidencia?: string
  ): Promise<{ message: string; movement: MovimientoTesoreria; reservation: ReservaTesoreria }> {
    const res = await fetch(`${getApiBaseUrl()}/reservas/${reservationId}/egreso-reservado`, {
      method: "POST",
      headers: getAuthHeaders(idempotencyKey),
      body: JSON.stringify({
        amount_cents: montoCentavos,
        counterpart: contrapartida,
        reason: motivo,
        evidence_reference: referenciaEvidencia || null,
      }),
    });
    return handleResponse<{ message: string; movement: MovimientoTesoreria; reservation: ReservaTesoreria }>(res);
  },

  // =====================================================================
  // ETAPA 5 — PUENTE DE INTEGRACIÓN COMERCIAL Y MONITOREO
  // =====================================================================

  async getCommercialBridgeSyncStatus(): Promise<SimplifiedSyncStatus> {
    const res = await fetch(`${getApiBaseUrl()}/commercial-bridge/sync-status`, {
      headers: getAuthHeaders(),
    });
    return handleResponse<SimplifiedSyncStatus>(res);
  },

  async getCommercialBridgeStatus(): Promise<CommercialBridgeStatus> {
    const res = await fetch(`${getApiBaseUrl()}/commercial-bridge/status`, {
      headers: getAuthHeaders(),
    });
    return handleResponse<CommercialBridgeStatus>(res);
  },

  async getCommercialBridgeEvents(params?: {
    status?: string;
    event_type?: string;
    search?: string;
    limit?: number;
    offset?: number;
  }): Promise<CommercialEventsListResponse> {
    const query = new URLSearchParams();
    if (params?.status && params.status !== "ALL") query.append("status", params.status);
    if (params?.event_type) query.append("event_type", params.event_type);
    if (params?.search) query.append("search", params.search);
    if (params?.limit) query.append("limit", params.limit.toString());
    if (params?.offset) query.append("offset", params.offset.toString());

    const url = `${getApiBaseUrl()}/commercial-bridge/events${query.toString() ? `?${query.toString()}` : ""}`;
    const res = await fetch(url, {
      headers: getAuthHeaders(),
    });
    return handleResponse<CommercialEventsListResponse>(res);
  },

  async reprocessCommercialEvent(eventId: string): Promise<ReprocessResponse> {
    const res = await fetch(`${getApiBaseUrl()}/commercial-bridge/reprocess/${eventId}`, {
      method: "POST",
      headers: getAuthHeaders(),
    });
    return handleResponse<ReprocessResponse>(res);
  },

  async triggerCommercialBridgeDrain(): Promise<DrainResponse> {
    const res = await fetch(`${getApiBaseUrl()}/commercial-bridge/trigger-drain`, {
      method: "POST",
      headers: getAuthHeaders(),
    });
    return handleResponse<DrainResponse>(res);
  },

  // =====================================================================
  // CUENTA CORRIENTE PREPAY / DAVID
  // =====================================================================

  async getCuentaCorrienteDavid(fechaCorte?: string): Promise<EstadoCuentaDavidResponse> {
    const query = fechaCorte ? `?fecha_corte=${encodeURIComponent(fechaCorte)}` : "";
    const res = await fetch(`${getApiBaseUrl()}/cuenta-corriente/david${query}`, {
      headers: getAuthHeaders(),
    });
    return handleResponse<EstadoCuentaDavidResponse>(res);
  },

  async registrarAcreditacionDavid(
    payload: AcreditacionDavidPayload,
    idempotencyKey?: string,
  ): Promise<any> {
    const key = idempotencyKey || `acr_david_${Date.now()}_${Math.random().toString(36).substring(2, 9)}`;
    const res = await fetch(`${getApiBaseUrl()}/cuenta-corriente/david/acreditacion`, {
      method: "POST",
      headers: getAuthHeaders(key),
      body: JSON.stringify(payload),
    });
    return handleResponse<any>(res);
  },

  async registrarPagoDavid(
    payload: PagoDavidPayload,
    idempotencyKey?: string,
  ): Promise<any> {
    const key = idempotencyKey || `pag_david_${Date.now()}_${Math.random().toString(36).substring(2, 9)}`;
    const res = await fetch(`${getApiBaseUrl()}/cuenta-corriente/david/pago`, {
      method: "POST",
      headers: getAuthHeaders(key),
      body: JSON.stringify(payload),
    });
    return handleResponse<any>(res);
  },

  async registrarReconocimientoDeudaHistorica(
    payload: ReconocimientoDeudaHistoricaPayload,
    idempotencyKey?: string,
  ): Promise<any> {
    const key = idempotencyKey || `his_david_${Date.now()}_${Math.random().toString(36).substring(2, 9)}`;
    const res = await fetch(`${getApiBaseUrl()}/cuenta-corriente/david/reconocimiento-historico`, {
      method: "POST",
      headers: getAuthHeaders(key),
      body: JSON.stringify(payload),
    });
    return handleResponse<any>(res);
  },

  // =====================================================================
  // ANTICIPOS Y REPOSICIONES DE PREVENTISTAS (1130 -> 1120 / 1110)
  // =====================================================================

  async getAnticiposPreventistas(estado?: string, proveedor?: string): Promise<AnticipoPreventistaItem[]> {
    const params = new URLSearchParams();
    if (estado && estado !== "ALL") params.append("estado", estado);
    if (proveedor) params.append("proveedor", proveedor);
    const query = params.toString() ? `?${params.toString()}` : "";
    const res = await fetch(`${getApiBaseUrl()}/preventistas/anticipos${query}`, {
      headers: getAuthHeaders(),
    });
    return handleResponse<AnticipoPreventistaItem[]>(res);
  },

  async crearAnticipoPreventista(
    payload: CrearAnticipoPreventistaPayload,
    idempotencyKey?: string,
  ): Promise<any> {
    const key = idempotencyKey || `ant_prev_${Date.now()}_${Math.random().toString(36).substring(2, 9)}`;
    const res = await fetch(`${getApiBaseUrl()}/preventistas/anticipos`, {
      method: "POST",
      headers: getAuthHeaders(key),
      body: JSON.stringify(payload),
    });
    return handleResponse<any>(res);
  },

  async getDetalleAnticipoPreventista(anticipoId: string): Promise<AnticipoPreventistaItem> {
    const res = await fetch(`${getApiBaseUrl()}/preventistas/anticipos/${anticipoId}`, {
      headers: getAuthHeaders(),
    });
    return handleResponse<AnticipoPreventistaItem>(res);
  },

  async reponerAnticipoPreventista(
    anticipoId: string,
    payload: ReponerAnticipoPreventistaPayload,
    idempotencyKey?: string,
  ): Promise<any> {
    const key = idempotencyKey || `rep_prev_${Date.now()}_${Math.random().toString(36).substring(2, 9)}`;
    const res = await fetch(`${getApiBaseUrl()}/preventistas/anticipos/${anticipoId}/reposicion`, {
      method: "POST",
      headers: getAuthHeaders(key),
      body: JSON.stringify(payload),
    });
    return handleResponse<any>(res);
  },

  async getReposicionesDeAnticipo(anticipoId: string): Promise<ReposicionPreventistaItem[]> {
    const res = await fetch(`${getApiBaseUrl()}/preventistas/anticipos/${anticipoId}/reposiciones`, {
      headers: getAuthHeaders(),
    });
    return handleResponse<ReposicionPreventistaItem[]>(res);
  },

  async getHistorialReposicionesPreventistas(limit: number = 50, offset: number = 0): Promise<ReposicionPreventistaItem[]> {
    const res = await fetch(`${getApiBaseUrl()}/preventistas/reposiciones?limit=${limit}&offset=${offset}`, {
      headers: getAuthHeaders(),
    });
    return handleResponse<ReposicionPreventistaItem[]>(res);
  },

  async getResumenPreventistas(): Promise<ResumenPreventistasResponse> {
    const res = await fetch(`${getApiBaseUrl()}/preventistas/resumen`, {
      headers: getAuthHeaders(),
    });
    return handleResponse<ResumenPreventistasResponse>(res);
  },

  async getSaldosIniciales(): Promise<SaldosInicialesResponse> {
    const res = await fetch(`${getApiBaseUrl()}/apertura/saldos-iniciales`, {
      headers: getAuthHeaders(),
    });
    return handleResponse<SaldosInicialesResponse>(res);
  },

  async modificarSaldoInicial(payload: ModificarSaldoInicialPayload): Promise<any> {
    const res = await fetch(`${getApiBaseUrl()}/apertura/saldos-iniciales`, {
      method: "POST",
      headers: getAuthHeaders(),
      body: JSON.stringify(payload),
    });
    return handleResponse<any>(res);
  },

  async getHistorialAperturas(): Promise<AuditoriaSaldoInicialItem[]> {
    const res = await fetch(`${getApiBaseUrl()}/apertura/historial`, {
      headers: getAuthHeaders(),
    });
    return handleResponse<AuditoriaSaldoInicialItem[]>(res);
  },
};
