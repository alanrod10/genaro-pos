/**
 * Tipos de datos para el Laboratorio de Tesorería — GENARO TESORERÍA (Sprint 1 y 2).
 */

export type NaturalezaFondo =
  | "EFECTIVO"
  | "FONDO_FIJO"
  | "ADICIONALES_PARTICULARES"
  | "RECAUDACION_TERCEROS"
  | "CUENTA_BANCARIA"
  | "BILLETERA_VIRTUAL"
  | "TRANSITO"
  | "TRANSITO_PREVENTISTAS";

export type TitularidadFondo = "PROPIA" | "CUSTODIA" | "TRANSITO";

export type EstadoFondo = "ACTIVO" | "INACTIVO" | "BLOQUEADO";

export type EstadoJornada = "ABIERTA" | "EN_ARQUEO" | "EN_CIERRE" | "CERRADA";

export type TipoMovimiento = "ENTRY" | "EXIT" | "TRANSFER_OUT" | "TRANSFER_IN" | "REVERSAL";

export type EstadoMovimiento = "POSTED" | "PENDING_REVIEW" | "REJECTED" | "REVERSED";

export type EstadoTraslado = "PENDING_RECEIPT" | "CONFIRMED" | "CANCELLED";

export type EstadoReserva = "ACTIVE" | "PARTIALLY_APPLIED" | "FULLY_APPLIED" | "RELEASED" | "CANCELLED";

export interface FondoTesoreria {
  id: number;
  codigo: string;
  nombre: string;
  naturaleza: NaturalezaFondo;
  titularidad: TitularidadFondo;
  moneda: string;
  estado: EstadoFondo;
  saldo_centavos: number;
  saldo_formateado: string;
  disponibilidad_inmediata: boolean;
  permite_sobregiro: number;
  version: number;
  created_at: string;
  updated_at: string;
  creado_por: string;
  actualizado_por: string;
}

export interface AuditoriaFondoItem {
  id: number;
  fondo_id: number;
  codigo: string;
  accion: "INSERT" | "UPDATE" | "DELETE_ATTEMPT" | "BLOQUEO";
  usuario: string;
  valores_previos: Record<string, unknown> | null;
  valores_nuevos: Record<string, unknown> | null;
  motivo: string | null;
  timestamp_utc: string;
}

export interface DetalleJornada {
  id: number;
  jornada_id: number;
  fondo_id: number;
  codigo_fondo: string;
  nombre_fondo: string;
  naturaleza: NaturalezaFondo;
  titularidad: TitularidadFondo;
  saldo_apertura_centavos: number;
  saldo_cierre_centavos: number | null;
  diferencia_centavos: number | null;
}

export interface JornadaTesoreria {
  id: number;
  numero_jornada: number;
  fecha_operativa: string;
  estado: EstadoJornada;
  apertura_timestamp_utc: string;
  cierre_timestamp_utc: string | null;
  usuario_apertura: string;
  usuario_cierre: string | null;
  total_apertura_centavos: number;
  total_cierre_centavos: number | null;
  observaciones: string | null;
  version: number;
  detalles?: DetalleJornada[];
}

export interface ResumenTesoreria {
  aislamiento_confirmado: boolean;
  db_path: string;
  total_saldo_registrado_centavos: number;
  total_saldo_registrado_formateado: string;
  total_saldo_disponible_centavos: number;
  total_saldo_disponible_formateado: string;
  total_fondos: number;
  fondos_activos: number;
  jornada_activa: JornadaTesoreria | null;
}

export interface MovimientoTesoreria {
  id: number;
  movement_id: string;
  idempotency_key?: string | null;
  journal_id: number;
  fund_id: number;
  codigo_fondo: string;
  nombre_fondo: string;
  movement_type: TipoMovimiento;
  amount_cents: number;
  amount_formatted: string;
  currency: string;
  counterpart: string;
  reason: string;
  evidence_reference?: string | null;
  created_by: string;
  terminal_id: string;
  status: EstadoMovimiento;
  occurred_at: string;
  recorded_at: string;
  is_reversal?: number | boolean;
  reversal_of_id?: string | null;
  reversed_amount_cents?: number;
}

export interface TrasladoTesoreria {
  id: number;
  transfer_id: string;
  idempotency_key?: string | null;
  journal_id: number;
  amount_cents: number;
  amount_formatted: string;
  currency: string;
  status: EstadoTraslado;
  origin_fund_code: string;
  origin_fund_name: string;
  destination_fund_code: string;
  destination_fund_name: string;
  dispatched_by: string;
  received_by?: string | null;
  dispatched_at: string;
  received_at?: string | null;
  reason: string;
  evidence_reference?: string | null;
  out_movement_id?: string | null;
  in_movement_id?: string | null;
}

export interface MovimientoReserva {
  id: number;
  reservation_id: string;
  action_type: "APPLY" | "RELEASE";
  amount_cents: number;
  amount_formatted: string;
  performed_by: string;
  reason: string;
  evidence_reference?: string | null;
  occurred_at: string;
  idempotency_key?: string | null;
}

export interface ReservaTesoreria {
  id: number;
  reservation_id: string;
  idempotency_key?: string | null;
  journal_id: number;
  fund_id: number;
  fund_code: string;
  fund_name: string;
  original_amount_cents: number;
  original_amount_formatted: string;
  applied_amount_cents: number;
  applied_amount_formatted: string;
  released_amount_cents: number;
  released_amount_formatted: string;
  pending_amount_cents: number;
  pending_amount_formatted: string;
  reason: string;
  beneficiary: string;
  obligation_id?: string | null;
  evidence_reference?: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
  status: EstadoReserva;
  movimientos?: MovimientoReserva[];
}

export interface FundAvailability {
  fund_id: number;
  fund_code: string;
  fund_name: string;
  nature: NaturalezaFondo;
  ownership: TitularidadFondo;
  status: EstadoFondo;
  saldo_registrado_cents: number;
  saldo_registrado_formatted: string;
  saldo_teorico_cents?: number;
  saldo_teorico_formatted?: string;
  diferencia_teorica_cents?: number;
  diferencia_teorica_formatted?: string;
  saldo_reservado_cents: number;
  saldo_reservado_formatted: string;
  saldo_disponible_cents: number;
  saldo_disponible_formatted: string;
  cantidad_reservas_activas: number;
  ultimo_saldo_verificado_cents?: number | null;
  ultimo_saldo_verificado_formatted?: string | null;
  diferencia_arqueo_cents?: number | null;
  diferencia_arqueo_formatted?: string | null;
  fecha_ultimo_arqueo?: string | null;
  reservas_afectando_disponibilidad?: Array<{
    reservation_id: string;
    pending_amount_cents: number;
    pending_amount_formatted: string;
    reason: string;
    beneficiary: string;
    created_at: string;
    status: EstadoReserva;
  }>;
}

export interface TreasurySummary {
  fondos_propios: {
    total_registrado_cents: number;
    total_registrado_formatted: string;
    total_reservado_cents: number;
    total_reservado_formatted: string;
    total_disponible_cents: number;
    total_disponible_formatted: string;
  };
  fondos_administrados_custodia: {
    total_registrado_cents: number;
    total_registrado_formatted: string;
    total_reservado_cents: number;
    total_reservado_formatted: string;
    total_disponible_cents: number;
    total_disponible_formatted: string;
  };
  dinero_en_transito: {
    amount_cents: number;
    amount_formatted: string;
    traslados_pendientes_count: number;
  };
  total_consolidado: {
    total_registrado_cents: number;
    total_registrado_formatted: string;
  };
  fondos: FundAvailability[];
}

// =====================================================================
// SPRINT 3 — TIPOS DE ARQUEOS, CIERRES, REVERSIONES, AUDITORÍA Y OUTBOX
// =====================================================================

export type TipoArqueo = "OPENING" | "PARTIAL" | "TOTAL" | "EXTRAORDINARY" | "CLOSING";

export type EstadoExpediente = "OPEN" | "UNDER_REVIEW" | "PENDING" | "RESOLVED";

export interface ArqueoTesoreria {
  id: number;
  reconciliation_id: string;
  journal_id: number;
  fund_id: number;
  fund_code: string;
  fund_name: string;
  naturaleza?: string;
  is_digital?: boolean;
  estado_revision?: string;
  reconciliation_type: TipoArqueo;
  saldo_registrado_cents?: number;
  registered_amount_cents?: number;
  saldo_registrado_formatted?: string;
  registered_amount_formatted?: string;
  saldo_verificado_cents?: number;
  verified_amount_cents?: number;
  saldo_verificado_formatted?: string;
  verified_amount_formatted?: string;
  diferencia_cents?: number;
  difference_cents?: number;
  diferencia_formatted?: string;
  difference_formatted?: string;
  notes?: string | null;
  performed_by: string;
  witness?: string | null;
  performed_at?: string;
  created_at?: string;
  difference_case_id?: string | null;
  turno?: string | null;
  fecha_consulta?: string | null;
  evidencia_referencia?: string | null;
  authorized_by?: string | null;
  authorized_at?: string | null;
  authorization_notes?: string | null;
  is_authorized?: boolean;
  tipo_diferencia?: "FALTANTE" | "SOBRANTE" | "CONFORME" | null;
}

export interface ExpedienteDiferencia {
  id: number;
  case_id: string;
  reconciliation_id: string;
  journal_id: number;
  fund_id: number;
  fund_code?: string;
  fund_name?: string;
  difference_cents: number;
  difference_formatted: string;
  status: EstadoExpediente;
  investigation_notes?: string | null;
  evidence_reference?: string | null;
  resolution_type?: string | null;
  resolution_notes?: string | null;
  resolved_by?: string | null;
  resolved_at?: string | null;
  created_at: string;
  updated_at: string;
  authorized_by?: string | null;
  authorized_at?: string | null;
  authorization_notes?: string | null;
  is_authorized?: boolean;
}

export interface ValidacionCierre {
  can_close: boolean;
  reasons?: string[];
  blockers: string[];
  warnings: string[];
  unauthorized_differences?: Array<any>;
  authorized_differences?: Array<any>;
  has_unauthorized_differences?: boolean;
  pending_movements_count?: number;
  pending_reviews_count?: number;
  pending_transfers_count?: number;
  transfers_in_transit_count?: number;
  pending_transfers_amount_cents?: number;
  pending_transfers_amount_formatted?: string;
  pending_transfers_list?: Array<{
    transfer_id: string;
    origin_fund_code: string;
    origin_fund_name: string;
    destination_fund_code: string;
    destination_fund_name: string;
    amount_cents: number;
    amount_formatted: string;
    dispatched_by: string;
    dispatched_at: string;
    reason: string;
  }>;
  active_reservations_count?: number;
  active_reservations_amount_cents?: number;
  open_diff_cases_count?: number;
  open_differences_count?: number;
  needs_sweep?: boolean;
  saldo_1110_cents?: number;
  saldo_1110_formatted?: string;
  cash_drawer_balance_cents?: number;
  cash_drawer_balance_formatted?: string;
}

export interface CierreTesoreria {
  id: number;
  closure_id: string;
  journal_id: number;
  numero_jornada?: number;
  fecha_operativa?: string;
  previous_closure_id?: string | null;
  total_registered_cents?: number;
  total_registrado_cents?: number;
  total_registered_formatted?: string;
  total_registrado_formatted?: string;
  total_verified_cents?: number;
  total_verified_formatted?: string;
  total_difference_cents?: number;
  total_diferencias_cents?: number;
  total_difference_formatted?: string;
  total_diferencias_formatted?: string;
  total_disponible_cents?: number;
  total_disponible_formatted?: string;
  total_reservado_cents?: number;
  total_reservado_formatted?: string;
  cash_drawer_swept?: number;
  sweep_transfer_id?: string | null;
  snapshot?: Record<string, any>;
  snapshot_data?: Record<string, unknown>;
  canonical_hash?: string;
  snapshot_hash?: string;
  closed_by: string;
  closed_at: string;
  status?: string;
  observations?: string | null;
  notes?: string | null;
}

export type TipoReversion = "MOVEMENT" | "TRANSFER" | "RESERVATION_OPERATION";

export interface ReversionTesoreria {
  id: number;
  reversal_id: string;
  original_movement_id?: string | null;
  counter_movement_id?: string | null;
  transfer_id?: string | null;
  reservation_id?: string | null;
  reversal_type: TipoReversion;
  reversed_amount_cents: number;
  reversed_amount_formatted: string;
  reason: string;
  authorized_by: string;
  evidence_reference?: string | null;
  occurred_at: string;
}

export interface EventoAuditoria {
  id: number;
  sequence_number: number;
  timestamp_utc: string;
  event_type: string;
  aggregate_type: string;
  aggregate_id: string;
  payload: Record<string, unknown>;
  performed_by: string;
  terminal_id?: string | null;
  previous_hash: string;
  current_hash: string;
}

export interface VerificacionAuditoria {
  valid: boolean;
  total_events: number;
  last_sequence: number;
  last_hash: string;
  broken_at_sequence?: number | null;
  error?: string | null;
}

export type EstadoOutbox = "PENDING" | "SENT" | "FAILED" | "DEAD_LETTER";

export interface EventoOutbox {
  id: number;
  event_id: string;
  event_type: string;
  aggregate_type: string;
  aggregate_id: string;
  payload: Record<string, unknown>;
  occurred_at: string;
  status: EstadoOutbox;
  retry_count: number;
  next_retry_at?: string | null;
  error_message?: string | null;
}

export interface ResumenOutbox {
  total: number;
  pending: number;
  sent: number;
  failed: number;
  dead_letter: number;
}

// =====================================================================
// ETAPA 5 — PUENTE DE INTEGRACIÓN COMERCIAL Y MONITOREO
// =====================================================================

export type EstadoEventoComercial =
  | "PENDING"
  | "PROCESSING"
  | "PUBLISHED"
  | "FAILED"
  | "DEAD_LETTER";

export interface EventoComercialOutbox {
  sequence_id: number;
  event_id: string;
  event_type: string;
  entity_type: string;
  entity_id: string;
  action_type: string;
  idempotency_key: string;
  payload_canonical_hash: string;
  payload: Record<string, unknown>;
  status: EstadoEventoComercial;
  retry_count: number;
  max_retries: number;
  error_message?: string | null;
  tesoreria_movement_id?: string | null;
  created_at: string;
  updated_at: string;
  published_at?: string | null;
  reprocessed_by?: string | null;
  reprocessed_at?: string | null;
}

export interface CommercialBridgeStatus {
  total: number;
  pending: number;
  processing: number;
  published: number;
  failed: number;
  dead_letter: number;
  retry_count_total: number;
}

export interface SimplifiedSyncStatus {
  sincronizadas: number;
  pendientes: number;
  requiere_atencion: boolean;
  mensaje_cajero: string;
  timestamp: string;
}

export interface CommercialEventsListResponse {
  total: number;
  limit: number;
  offset: number;
  events: EventoComercialOutbox[];
}

export interface ReprocessResponse {
  event_id: string;
  status: string;
  tesoreria_movement_id?: string | null;
  error_message?: string | null;
  authorized_by: string;
  reprocessed_at: string;
  drain_stats: Record<string, number>;
}

export interface DrainResponse {
  published: number;
  failed: number;
  dead_letter: number;
  message: string;
}

// =====================================================================
// TIPOS CUENTA CORRIENTE PREPAY / DAVID
// =====================================================================

export type TipoMovimientoCCDavid =
  | "ACREDITACION_SALDO"
  | "PAGO_EFECTIVO"
  | "RECONOCIMIENTO_DEUDA_HISTORICA";

export interface MovimientoCCDavid {
  id: number;
  operation_id: string;
  idempotency_key: string;
  tipo_movimiento: TipoMovimientoCCDavid;
  monto_cents: number;
  monto_formatted: string;
  fecha_referencia: string;
  recorded_at_utc: string;
  movement_id?: string | null;
  fondo_afectado?: string | null;
  referencia_externa?: string | null;
  motivo: string;
  actor: string;
  jornada_id?: number | null;
  created_at: string;
  saldo_obligacion_resultante_cents: number;
  saldo_obligacion_resultante_formatted: string;
}

export interface EstadoCuentaDavidResponse {
  obligacion_pendiente_cents: number;
  obligacion_pendiente_formatted: string;
  total_acreditaciones_cents: number;
  total_acreditaciones_formatted: string;
  total_pagos_cents: number;
  total_pagos_formatted: string;
  total_deuda_historica_cents: number;
  total_deuda_historica_formatted: string;
  saldo_disponible_1130_cents: number;
  saldo_disponible_1130_formatted: string;
  fecha_corte?: string | null;
  cantidad_movimientos: number;
  movimientos: MovimientoCCDavid[];
}

export interface AcreditacionDavidPayload {
  monto_centavos: number;
  referencia: string;
  motivo?: string;
  fecha_referencia?: string;
}

export interface PagoDavidPayload {
  monto_centavos: number;
  referencia: string;
  motivo?: string;
  fecha_referencia?: string;
}

export interface ReconocimientoDeudaHistoricaPayload {
  monto_centavos: number;
  fecha_referencia: string;
  motivo: string;
  referencia_externa?: string;
  confirmacion_explicita: boolean;
}

// ============================================================================
// CUENTA CORRIENTE CLARO COMERCIOS / EZEQUIEL Y FONDEOS PENDIENTES
// ============================================================================

export interface MovimientoCCEzequiel {
  id: number;
  operation_id: string;
  idempotency_key: string;
  tipo_movimiento: "ACREDITACION_SALDO" | "PAGO_LIQUIDACION" | "RECONOCIMIENTO_DEUDA_HISTORICA";
  monto_cents: number;
  monto_formatted: string;
  fecha_referencia: string;
  recorded_at_utc: string;
  movement_id?: string | null;
  fondo_afectado?: string | null;
  referencia_externa?: string | null;
  motivo: string;
  actor: string;
  jornada_id?: number | null;
  detalle_liquidacion?: {
    monto_1150_cents: number;
    monto_drugstore_cents: number;
    fondo_drugstore?: string | null;
    monto_transferencia_cents: number;
    total_cents: number;
    movements: string[];
  } | null;
  created_at: string;
  saldo_obligacion_resultante_cents: number;
  saldo_obligacion_resultante_formatted: string;
}

export interface EstadoCuentaEzequielResponse {
  obligacion_pendiente_cents: number;
  obligacion_pendiente_formatted: string;
  total_acreditaciones_cents: number;
  total_acreditaciones_formatted: string;
  total_pagos_cents: number;
  total_pagos_formatted: string;
  total_deuda_historica_cents: number;
  total_deuda_historica_formatted: string;
  saldo_disponible_1150_cents: number;
  saldo_disponible_1150_formatted: string;
  saldo_disponible_1230_cents: number;
  saldo_disponible_1230_formatted: string;
  saldo_disponible_1110_cents: number;
  saldo_disponible_1110_formatted: string;
  saldo_disponible_1120_cents: number;
  saldo_disponible_1120_formatted: string;
  fecha_corte?: string | null;
  cantidad_movimientos: number;
  movimientos: MovimientoCCEzequiel[];
}

export interface AcreditacionEzequielPayload {
  monto_centavos: number;
  referencia: string;
  motivo?: string;
  fecha_referencia?: string;
}

export interface LiquidacionEzequielPayload {
  monto_1150_cents?: number;
  monto_drugstore_cents?: number;
  fondo_drugstore?: string;
  monto_transferencia_cents?: number;
  referencia: string;
  motivo?: string;
  fecha_referencia?: string;
}

export interface ReconocimientoDeudaHistoricaEzequielPayload {
  monto_centavos: number;
  fecha_referencia: string;
  motivo: string;
  referencia_externa?: string;
  confirmacion_explicita: boolean;
}

export interface FondeoPendienteItem {
  id: number;
  tipo_circuito: "CLARO" | "PREPAY";
  servicio_id: number;
  idempotency_key: string;
  monto_capital_cents: number;
  monto_capital_formatted: string;
  fondo_origen_esperado: string;
  fondo_destino_custodia: string;
  estado: "PENDIENTE" | "FONDEADO" | "CANCELADO";
  movement_id_traslado?: string | null;
  actor: string;
  created_at_utc: string;
  funded_at_utc?: string | null;
  observaciones?: string | null;
}

// ============================================================================
// ANTICIPOS Y REPOSICIÓN DE PREVENTISTAS (FONDOS 1130, 1120, 1110)
// ============================================================================

export type EstadoAnticipoPreventista =
  | "PENDIENTE_REPOSICION"
  | "PARCIALMENTE_REPUESTO"
  | "TOTALMENTE_REPUESTO";

export interface ReposicionPreventistaItem {
  id: number;
  reposicion_id: string;
  idempotency_key: string;
  anticipo_id: string;
  fondo_origen: string;
  fondo_destino: string;
  monto_cents: number;
  monto_formatted: string;
  movement_id_egreso: string;
  movement_id_ingreso: string;
  motivo: string;
  actor: string;
  creado_en_utc: string;
  jornada_id?: number | null;
  proveedor?: string;
}

export interface AnticipoPreventistaItem {
  id: string;
  idempotency_key: string;
  proveedor: string;
  monto_original_cents: number;
  monto_original_formatted: string;
  monto_repuesto_cents: number;
  monto_repuesto_formatted: string;
  monto_pendiente_cents: number;
  monto_pendiente_formatted: string;
  estado: EstadoAnticipoPreventista;
  motivo: string;
  referencia_documento?: string | null;
  pedido_id?: string | null;
  salida_movement_id: string;
  creado_por: string;
  creado_en_utc: string;
  jornada_id?: number | null;
  cantidad_reposiciones?: number;
  reposiciones?: ReposicionPreventistaItem[];
}

export interface ResumenPreventistasResponse {
  total_anticipado_cents: number;
  total_anticipado_formatted: string;
  total_repuesto_cents: number;
  total_repuesto_formatted: string;
  total_pendiente_reposicion_cents: number;
  total_pendiente_reposicion_formatted: string;
  cantidad_anticipos: number;
  cantidad_reposiciones: number;
  saldo_disponible_1130_cents: number;
  saldo_disponible_1130_formatted: string;
  saldo_disponible_1120_cents: number;
  saldo_disponible_1120_formatted: string;
  saldo_disponible_1110_cents: number;
  saldo_disponible_1110_formatted: string;
}

export interface CrearAnticipoPreventistaPayload {
  proveedor: string;
  monto_centavos: number;
  motivo: string;
  referencia_documento?: string;
  pedido_id?: string;
}

export interface ReponerAnticipoPreventistaPayload {
  monto_centavos?: number;
  motivo?: string;
}

// ==========================================
// --- APERTURAS Y SALDOS INICIALES (Saldos en $0 y Configuración Auditada) ---
// ==========================================

export type EstadoApertura =
  | "NOT_CONFIGURED"
  | "PENDING_OPENING"
  | "OPENED"
  | "RECONCILIATION_REQUIRED"
  | "BLOCKED";

export interface SaldoInicialItem {
  codigo: string;
  nombre: string;
  categoria: "FONDO_FISICO" | "CUENTA_DIGITAL" | "OBLIGACION";
  naturaleza?: string;
  saldo_inicial_cents: number;
  saldo_actual_cents: number;
  estado_apertura?: EstadoApertura;
  subestado_apertura?: "SIN_MOVIMIENTOS" | "MOVIMIENTOS_POSTERIORES_REGISTRADOS" | "CONFIRMADO";
  disponibilidad_confirmada?: boolean;
  fecha_corte?: string | null;
  naturaleza_saldo?: string | null;
  observaciones?: string | null;
  comprobante?: string | null;
  configurado_en: string | null;
  configurado_por: string | null;
  estado_configuracion: "INICIAL_CERO" | "CONFIGURADO" | string;
  tiene_movimientos?: boolean;
  puede_editar_apertura?: boolean;
  editable: boolean;
}

export interface SaldosInicialesResponse {
  fondos_fisicos: SaldoInicialItem[];
  cuentas_digitales: SaldoInicialItem[];
  obligaciones: SaldoInicialItem[];
  total_fondos_fisicos_cents: number;
  total_cuentas_digitales_cents: number;
  total_obligaciones_cents: number;
  conteo_abiertos?: number;
  conteo_pendientes?: number;
  apertura_completa?: boolean;
  estado_general_apertura?: "COMPLETO" | "DIFERIDO_PENDIENTE" | string;
}

export interface ModificarSaldoInicialPayload {
  codigo: string;
  saldo_nuevo_cents: number;
  motivo: string;
  referencia_autorizacion?: string | null;
  fecha_corte?: string | null;
  naturaleza_saldo?: string | null;
  comprobante?: string | null;
  idempotency_key?: string | null;
  tipo_operacion?: "APERTURA_INICIAL" | "AJUSTE_COMPENSATORIO" | string;
}

export interface AuditoriaSaldoInicialItem {
  id: number;
  item_codigo: string;
  item_nombre: string;
  categoria: string;
  saldo_anterior_cents: number;
  saldo_nuevo_cents: number;
  fecha_hora: string;
  usuario_responsable: string;
  motivo: string;
  referencia_autorizacion: string | null;
  jornada_id?: number | null;
  es_apertura_inicial: boolean;
  fecha_corte?: string | null;
  naturaleza_saldo?: string | null;
  idempotency_key?: string | null;
  tipo_operacion?: string;
  estado_apertura?: string;
}


