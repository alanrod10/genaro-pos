import crypto from "node:crypto";

export function formatMoney(centavos) {
  const c = Math.trunc(Number(centavos || 0));
  const neg = c < 0;
  const abs = Math.abs(c);
  const entera = Math.floor(abs / 100)
    .toString()
    .replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  const dec = (abs % 100).toString().padStart(2, "0");
  return `${neg ? "-" : ""}$${entera},${dec}`;
}

const GENESIS_HASH = "0".repeat(64);

function canonicalJsonDumps(obj) {
  if (obj === null || typeof obj !== "object") {
    return JSON.stringify(obj);
  }
  if (Array.isArray(obj)) {
    return "[" + obj.map((item) => canonicalJsonDumps(item)).join(",") + "]";
  }
  const keys = Object.keys(obj).sort();
  const parts = [];
  for (const k of keys) {
    if (obj[k] !== undefined) {
      parts.push(JSON.stringify(k) + ":" + canonicalJsonDumps(obj[k]));
    }
  }
  return "{" + parts.join(",") + "}";
}

function computeCanonicalAuditHash({
  prev_canonical_hash,
  sequence_number,
  event_type,
  aggregate_type,
  aggregate_id,
  user_id,
  terminal_id,
  occurred_at,
  payload_json,
}) {
  let parsedPayload = payload_json;
  if (typeof payload_json === "string") {
    try {
      parsedPayload = JSON.parse(payload_json);
    } catch {
      parsedPayload = payload_json;
    }
  }
  const envelope = {
    "1_prev_hash": String(prev_canonical_hash),
    "2_sequence": Number(sequence_number),
    "3_event_type": String(event_type),
    "4_aggregate_type": String(aggregate_type),
    "5_aggregate_id": String(aggregate_id),
    "6_user_id": String(user_id),
    "7_terminal_id": String(terminal_id),
    "8_occurred_at": String(occurred_at),
    "9_payload": parsedPayload,
  };
  return crypto
    .createHash("sha256")
    .update(canonicalJsonDumps(envelope), "utf8")
    .digest("hex");
}

async function recordAuditEventWorker(
  client,
  { event_type, aggregate_type, aggregate_id, user_id, payload, terminal_id = "TERM-01", occurred_at = null }
) {
  const lastRes = await client.execute(
    "SELECT sequence_number, canonical_hash FROM tesoreria_auditoria_eventos ORDER BY sequence_number DESC LIMIT 1;"
  );
  const lastRow = lastRes.rows[0];
  const sequence_number = lastRow ? Number(lastRow.sequence_number) + 1 : 1;
  const prev_canonical_hash = lastRow ? String(lastRow.canonical_hash) : GENESIS_HASH;
  const event_id = crypto.randomUUID();
  const now_utc = new Date().toISOString();
  const occ_at = occurred_at || now_utc;
  const payload_json = canonicalJsonDumps(payload);
  const canonical_hash = computeCanonicalAuditHash({
    prev_canonical_hash,
    sequence_number,
    event_type,
    aggregate_type,
    aggregate_id: String(aggregate_id),
    user_id,
    terminal_id,
    occurred_at: occ_at,
    payload_json,
  });

  await client.execute({
    sql: `INSERT INTO tesoreria_auditoria_eventos (
            event_id, event_type, aggregate_type, aggregate_id,
            sequence_number, occurred_at, recorded_at,
            user_id, terminal_id, payload_json,
            prev_canonical_hash, canonical_hash
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);`,
    args: [
      event_id,
      event_type,
      aggregate_type,
      String(aggregate_id),
      sequence_number,
      occ_at,
      now_utc,
      user_id,
      terminal_id,
      payload_json,
      prev_canonical_hash,
      canonical_hash,
    ],
  });

  return {
    event_id,
    event_type,
    aggregate_type,
    aggregate_id: String(aggregate_id),
    sequence_number,
    occurred_at: occ_at,
    recorded_at: now_utc,
    user_id,
    terminal_id,
    payload,
    prev_canonical_hash,
    canonical_hash,
  };
}

async function enqueueOutboxEventWorker(client, { event_type, aggregate_id, payload, max_retries = 3 }) {
  const event_id = crypto.randomUUID();
  const now_utc = new Date().toISOString();
  const payload_json = canonicalJsonDumps(payload);
  await client.execute({
    sql: `INSERT INTO tesoreria_outbox (
            event_id, event_type, aggregate_id, payload_json,
            status, retry_count, max_retries, created_at, scheduled_at
          ) VALUES (?, ?, ?, ?, 'PENDING', 0, ?, ?, ?);`,
    args: [event_id, event_type, String(aggregate_id), payload_json, max_retries, now_utc, now_utc],
  });
  return { event_id, event_type, aggregate_id: String(aggregate_id), status: "PENDING", created_at: now_utc };
}

async function verificarIdempotenciaTesoreria(client, idempotencyKey, payloadObj) {
  if (!idempotencyKey) return null;
  const payloadHash = crypto
    .createHash("sha256")
    .update(canonicalJsonDumps(payloadObj), "utf8")
    .digest("hex");
  const res = await client.execute({
    sql: "SELECT payload_hash, response_json FROM tesoreria_idempotencia WHERE idempotency_key = ?;",
    args: [idempotencyKey],
  });
  if (res.rows.length === 0) return null;
  const row = res.rows[0];
  if (String(row.payload_hash) !== payloadHash) {
    throw new Error(
      `CONFLICT: Clave de idempotencia '${idempotencyKey}' reutilizada con parámetros distintos.`
    );
  }
  return JSON.parse(String(row.response_json));
}

async function registrarIdempotenciaTesoreria(
  client,
  { idempotencyKey, operationType, resourceId, payloadObj, responseData }
) {
  if (!idempotencyKey) return;
  const payloadHash = crypto
    .createHash("sha256")
    .update(canonicalJsonDumps(payloadObj), "utf8")
    .digest("hex");
  await client.execute({
    sql: `INSERT OR REPLACE INTO tesoreria_idempotencia (
            idempotency_key, operation_type, resource_id, payload_hash, response_json, status, created_at
          ) VALUES (?, ?, ?, ?, ?, 'SUCCESS', datetime('now'));`,
    args: [idempotencyKey, operationType, String(resourceId), payloadHash, JSON.stringify(responseData)],
  });
}

// ============================================================================
// CATÁLOGO Y FONDOS
// ============================================================================

export async function obtenerFondosCatalogoWorker(client) {
  const res = await client.execute(
    `SELECT id, codigo, nombre, naturaleza, titularidad, moneda, estado,
            saldo_centavos, permite_sobregiro, version, created_at, updated_at,
            creado_por, actualizado_por
     FROM tesoreria_fondos
     ORDER BY codigo ASC;`
  );
  return res.rows.map((r) => {
    const saldo = Number(r.saldo_centavos || 0);
    const estado = String(r.estado || "ACTIVO");
    const titularidad = String(r.titularidad || "PROPIA");
    return {
      id: Number(r.id),
      codigo: String(r.codigo),
      nombre: String(r.nombre),
      naturaleza: String(r.naturaleza),
      titularidad,
      moneda: String(r.moneda || "ARS"),
      estado,
      saldo_centavos: saldo,
      saldo_formateado: formatMoney(saldo),
      disponibilidad_inmediata: estado === "ACTIVO" && titularidad === "PROPIA",
      permite_sobregiro: Number(r.permite_sobregiro || 0),
      version: Number(r.version || 1),
      created_at: String(r.created_at || new Date().toISOString()),
      updated_at: String(r.updated_at || new Date().toISOString()),
      creado_por: String(r.creado_por || "SISTEMA"),
      actualizado_por: String(r.actualizado_por || "SISTEMA"),
    };
  });
}

export async function obtenerFondoDetalleWorker(client, codigo) {
  const fondos = await obtenerFondosCatalogoWorker(client);
  const f = fondos.find((x) => x.codigo === String(codigo).trim());
  if (!f) {
    throw new Error(`NOT_FOUND: Fondo no encontrado: ${codigo}`);
  }
  return f;
}

export async function obtenerAuditoriaFondoWorker(client, codigo) {
  const res = await client.execute({
    sql: `SELECT id, fondo_id, codigo, accion, usuario, valores_previos,
                 valores_nuevos, motivo, timestamp_utc
          FROM tesoreria_fondos_auditoria
          WHERE codigo = ?
          ORDER BY id DESC;`,
    args: [String(codigo).trim()],
  });
  return res.rows.map((r) => ({
    id: Number(r.id),
    fondo_id: Number(r.fondo_id),
    codigo: String(r.codigo),
    accion: String(r.accion),
    usuario: String(r.usuario),
    valores_previos: r.valores_previos ? JSON.parse(String(r.valores_previos)) : null,
    valores_nuevos: r.valores_nuevos ? JSON.parse(String(r.valores_nuevos)) : {},
    motivo: r.motivo ? String(r.motivo) : null,
    timestamp_utc: String(r.timestamp_utc),
  }));
}

export async function actualizarEstadoFondoWorker(client, codigo, { nuevo_estado, motivo }, usuario) {
  const est = String(nuevo_estado || "").trim().toUpperCase();
  if (!["ACTIVO", "INACTIVO", "BLOQUEADO"].includes(est)) {
    throw new Error(`INVALID_INPUT: Estado de fondo inválido: ${nuevo_estado}`);
  }
  const actual = await obtenerFondoDetalleWorker(client, codigo);
  const nowUtc = new Date().toISOString();
  await client.batch(
    [
      {
        sql: `UPDATE tesoreria_fondos
              SET estado = ?, version = version + 1, updated_at = ?, actualizado_por = ?
              WHERE id = ?;`,
        args: [est, nowUtc, usuario || "sistema", actual.id],
      },
      {
        sql: `INSERT INTO tesoreria_fondos_auditoria (
                fondo_id, codigo, accion, usuario, valores_previos, valores_nuevos, motivo, timestamp_utc
              ) VALUES (?, ?, 'UPDATE_STATE', ?, ?, ?, ?, ?);`,
        args: [
          actual.id,
          actual.codigo,
          usuario || "sistema",
          JSON.stringify({ estado: actual.estado }),
          JSON.stringify({ estado: est }),
          motivo || "Actualización administrativa",
          nowUtc,
        ],
      },
    ],
    "write"
  );
  return obtenerFondoDetalleWorker(client, codigo);
}

// ============================================================================
// DISPONIBILIDAD Y RESUMEN
// ============================================================================

async function obtenerReservadoFondo(client, fundId) {
  const res = await client.execute({
    sql: `SELECT COALESCE(SUM(pending_amount_cents), 0) as res_cents
          FROM tesoreria_reservas
          WHERE fund_id = ? AND status IN ('ACTIVE', 'PARTIALLY_APPLIED');`,
    args: [fundId],
  });
  return Number(res.rows[0]?.res_cents || 0);
}

async function obtenerTransitoFondo(client, fundId) {
  const [outRes, inRes] = await Promise.all([
    client.execute({
      sql: `SELECT COALESCE(SUM(amount_cents), 0) as out_cents
            FROM tesoreria_traslados
            WHERE origin_fund_id = ? AND status = 'IN_TRANSIT';`,
      args: [fundId],
    }),
    client.execute({
      sql: `SELECT COALESCE(SUM(amount_cents), 0) as in_cents
            FROM tesoreria_traslados
            WHERE destination_fund_id = ? AND status = 'IN_TRANSIT';`,
      args: [fundId],
    }),
  ]);
  return {
    out_cents: Number(outRes.rows[0]?.out_cents || 0),
    in_cents: Number(inRes.rows[0]?.in_cents || 0),
  };
}

export async function obtenerDisponibilidadFondoWorker(client, codigo) {
  const f = await obtenerFondoDetalleWorker(client, codigo);
  const reservado = await obtenerReservadoFondo(client, f.id);
  const transito = await obtenerTransitoFondo(client, f.id);
  const esCustodia = f.titularidad === "CUSTODIA";
  const esTransito = f.titularidad === "TRANSITO";
  const esPropio = f.titularidad === "PROPIA";
  const disponibleBruto = f.saldo_centavos - reservado;
  const disponibleOperativo = f.estado === "ACTIVO" && esPropio ? disponibleBruto : 0;

  return {
    fund_id: f.id,
    codigo: f.codigo,
    nombre: f.nombre,
    naturaleza: f.naturaleza,
    titularidad: f.titularidad,
    estado: f.estado,
    saldo_registrado_cents: f.saldo_centavos,
    saldo_registrado_formatted: formatMoney(f.saldo_centavos),
    saldo_reservado_cents: reservado,
    saldo_reservado_formatted: formatMoney(reservado),
    saldo_en_transito_saliente_cents: transito.out_cents,
    saldo_en_transito_saliente_formatted: formatMoney(transito.out_cents),
    saldo_en_transito_entrante_cents: transito.in_cents,
    saldo_en_transito_entrante_formatted: formatMoney(transito.in_cents),
    saldo_disponible_cents: disponibleOperativo,
    saldo_disponible_formatted: formatMoney(disponibleOperativo),
    es_recaudacion_terceros_custodia: esCustodia,
    es_transito_preventistas: esTransito,
    disponible_para_gasto_propio: f.estado === "ACTIVO" && esPropio,
  };
}

export async function obtenerBalanceFondoWorker(client, codigo) {
  const disp = await obtenerDisponibilidadFondoWorker(client, codigo);
  return {
    codigo: disp.codigo,
    nombre: disp.nombre,
    estado: disp.estado,
    titularidad: disp.titularidad,
    saldo_registrado_cents: disp.saldo_registrado_cents,
    saldo_registrado_formatted: disp.saldo_registrado_formatted,
    saldo_disponible_cents: disp.saldo_disponible_cents,
    saldo_disponible_formatted: disp.saldo_disponible_formatted,
  };
}

export async function obtenerDisponibilidadResumenWorker(client) {
  const fondos = await obtenerFondosCatalogoWorker(client);
  const desglose = [];
  let propiosReg = 0;
  let propiosRes = 0;
  let propiosDisp = 0;
  let propiosTransOut = 0;
  let propiosTransIn = 0;
  let custodiaReg = 0;
  let transitoPrevReg = 0;

  for (const f of fondos) {
    const d = await obtenerDisponibilidadFondoWorker(client, f.codigo);
    desglose.push(d);
    if (d.es_recaudacion_terceros_custodia) {
      custodiaReg += d.saldo_registrado_cents;
    } else if (d.es_transito_preventistas) {
      transitoPrevReg += d.saldo_registrado_cents;
    } else {
      propiosReg += d.saldo_registrado_cents;
      propiosRes += d.saldo_reservado_cents;
      propiosDisp += d.saldo_disponible_cents;
      propiosTransOut += d.saldo_en_transito_saliente_cents;
      propiosTransIn += d.saldo_en_transito_entrante_cents;
    }
  }

  const totalReg = propiosReg + custodiaReg + transitoPrevReg;
  const totalTransito = propiosTransOut;

  return {
    fondos_propios: {
      total_registrado_cents: propiosReg,
      total_registrado_formatted: formatMoney(propiosReg),
      total_reservado_cents: propiosRes,
      total_reservado_formatted: formatMoney(propiosRes),
      total_disponible_cents: propiosDisp,
      total_disponible_formatted: formatMoney(propiosDisp),
      total_en_transito_saliente_cents: propiosTransOut,
      total_en_transito_entrante_cents: propiosTransIn,
    },
    fondos_custodia_terceros: {
      codigo_principal: "1150",
      total_registrado_cents: custodiaReg,
      total_registrado_formatted: formatMoney(custodiaReg),
      nota: "Recaudación de terceros en custodia (Claro). No forma parte de la liquidez comercial propia.",
    },
    fondos_transito_preventistas: {
      codigo_principal: "1310",
      total_registrado_cents: transitoPrevReg,
      total_registrado_formatted: formatMoney(transitoPrevReg),
      nota: "Fondos en tránsito de preventistas pendientes de rendición.",
    },
    total_consolidado: {
      total_registrado_cents: totalReg,
      total_registrado_formatted: formatMoney(totalReg),
      total_en_transito_entre_cuentas_cents: totalTransito,
      total_en_transito_entre_cuentas_formatted: formatMoney(totalTransito),
    },
    desglose_por_fondo: desglose,
  };
}

export async function obtenerEstadoLaboratorioWorker(client, usuario = "sistema") {
  const fondos = await obtenerFondosCatalogoWorker(client);
  const jornada = await obtenerJornadaActualWorker(client);
  return {
    modulo: "GENARO TESORERÍA PRODUCCIÓN (TURSO CANÓNICO)",
    fase: "PRODUCCIÓN INTEGRADA",
    aislamiento: "UNIFICADO_TURSO_REMOTO",
    db_path: "turso://genaro-pos-live-ops",
    conexion_remota: true,
    turso_conectado: true,
    integridad_catalogo: {
      valido: fondos.length >= 10,
      total_fondos: fondos.length,
      codigos_presentes: fondos.map((f) => f.codigo),
      codigos_faltantes: [],
      codigos_no_autorizados: [],
      saldos_invalidos: [],
    },
    jornada_activa: Boolean(jornada),
    usuario,
  };
}

export async function obtenerResumenGeneralWorker(client) {
  const fondos = await obtenerFondosCatalogoWorker(client);
  const jornada = await obtenerJornadaActualWorker(client);
  const summary = await obtenerDisponibilidadResumenWorker(client);
  const totalReg = summary.total_consolidado.total_registrado_cents;
  const totalDisp = summary.fondos_propios.total_disponible_cents;
  const activosCount = fondos.filter((f) => f.estado === "ACTIVO").length;

  const dbPath = client?.url || "turso://staging-isolated";
  const isIsolated = !String(dbPath).toLowerCase().includes("live-ops") && !String(dbPath).toLowerCase().includes("prod");

  return {
    aislamiento_confirmado: isIsolated,
    db_path: dbPath,
    total_saldo_registrado_centavos: totalReg,
    total_saldo_registrado_formateado: formatMoney(totalReg),
    total_saldo_disponible_centavos: totalDisp,
    total_saldo_disponible_formateado: formatMoney(totalDisp),
    total_fondos: fondos.length,
    fondos_activos: activosCount,
    jornada_activa: jornada,
  };
}

// ============================================================================
// JORNADAS
// ============================================================================

export async function obtenerJornadaPorIdWorker(client, jornadaId) {
  const jRes = await client.execute({
    sql: `SELECT id, numero_jornada, estado, fecha_apertura_utc, abierta_por,
                 fecha_cierre_utc, cerrada_por, observaciones_apertura, observaciones_cierre, version
          FROM tesoreria_jornadas
          WHERE id = ?;`,
    args: [jornadaId],
  });
  if (jRes.rows.length === 0) return null;
  const j = jRes.rows[0];
  const dRes = await client.execute({
    sql: `SELECT id, jornada_id, fondo_id, codigo_fondo, saldo_apertura_centavos,
                 saldo_cierre_esperado_centavos, saldo_cierre_arqueado_centavos, diferencia_centavos
          FROM tesoreria_jornada_detalles
          WHERE jornada_id = ?
          ORDER BY codigo_fondo ASC;`,
    args: [jornadaId],
  });
  return {
    id: Number(j.id),
    numero_jornada: Number(j.numero_jornada),
    estado: String(j.estado),
    fecha_apertura_utc: String(j.fecha_apertura_utc),
    abierta_por: String(j.abierta_por),
    fecha_cierre_utc: j.fecha_cierre_utc ? String(j.fecha_cierre_utc) : null,
    cerrada_por: j.cerrada_por ? String(j.cerrada_por) : null,
    observaciones_apertura: j.observaciones_apertura ? String(j.observaciones_apertura) : null,
    observaciones_cierre: j.observaciones_cierre ? String(j.observaciones_cierre) : null,
    version: Number(j.version || 1),
    detalles: dRes.rows.map((d) => ({
      id: Number(d.id),
      jornada_id: Number(d.jornada_id),
      fondo_id: Number(d.fondo_id),
      codigo_fondo: String(d.codigo_fondo),
      saldo_apertura_centavos: Number(d.saldo_apertura_centavos),
      saldo_cierre_esperado_centavos:
        d.saldo_cierre_esperado_centavos !== null ? Number(d.saldo_cierre_esperado_centavos) : null,
      saldo_cierre_arqueado_centavos:
        d.saldo_cierre_arqueado_centavos !== null ? Number(d.saldo_cierre_arqueado_centavos) : null,
      diferencia_centavos: d.diferencia_centavos !== null ? Number(d.diferencia_centavos) : null,
    })),
  };
}

export async function obtenerJornadaActualWorker(client) {
  const res = await client.execute(
    "SELECT id FROM tesoreria_jornadas WHERE estado IN ('ABIERTA', 'EN_ARQUEO', 'EN_CIERRE') ORDER BY id DESC LIMIT 1;"
  );
  if (res.rows.length === 0) return null;
  return obtenerJornadaPorIdWorker(client, Number(res.rows[0].id));
}

export async function abrirJornadaWorker(client, { observaciones = null } = {}, usuario = "sistema") {
  const activa = await obtenerJornadaActualWorker(client);
  if (activa) {
    throw new Error(
      `INVALID_INPUT: Ya existe una jornada activa (#${activa.numero_jornada} en estado ${activa.estado}).`
    );
  }
  const maxRes = await client.execute("SELECT COALESCE(MAX(numero_jornada), 0) as max_num FROM tesoreria_jornadas;");
  const nextNum = Number(maxRes.rows[0]?.max_num || 0) + 1;
  const nowUtc = new Date().toISOString();

  const ins = await client.execute({
    sql: `INSERT INTO tesoreria_jornadas (
            numero_jornada, estado, fecha_apertura_utc, abierta_por, observaciones_apertura, version
          ) VALUES (?, 'ABIERTA', ?, ?, ?, 1);`,
    args: [nextNum, nowUtc, usuario, observaciones],
  });
  const jornadaId = Number(ins.lastInsertRowid);

  const fondos = await obtenerFondosCatalogoWorker(client);
  const stmts = fondos.map((f) => ({
    sql: `INSERT INTO tesoreria_jornada_detalles (
            jornada_id, fondo_id, codigo_fondo, saldo_apertura_centavos
          ) VALUES (?, ?, ?, ?);`,
    args: [jornadaId, f.id, f.codigo, f.saldo_centavos],
  }));
  if (stmts.length > 0) {
    await client.batch(stmts, "write");
  }

  return obtenerJornadaPorIdWorker(client, jornadaId);
}

export async function cerrarJornadaWorker(
  client,
  { jornada_id, arqueo_centavos_por_fondo = null, observaciones = null },
  usuario = "sistema"
) {
  const jornada = await obtenerJornadaPorIdWorker(client, Number(jornada_id));
  if (!jornada) {
    throw new Error(`NOT_FOUND: Jornada #${jornada_id} no encontrada.`);
  }
  if (jornada.estado === "CERRADA") {
    throw new Error(`INVALID_INPUT: La jornada #${jornada.numero_jornada} ya se encuentra CERRADA.`);
  }
  const fondos = await obtenerFondosCatalogoWorker(client);
  const nowUtc = new Date().toISOString();
  const stmts = [];

  for (const f of fondos) {
    const esperado = f.saldo_centavos;
    const arqueado =
      arqueo_centavos_por_fondo && arqueo_centavos_por_fondo[f.codigo] !== undefined
        ? Number(arqueo_centavos_por_fondo[f.codigo])
        : esperado;
    const dif = arqueado - esperado;
    stmts.push({
      sql: `UPDATE tesoreria_jornada_detalles
            SET saldo_cierre_esperado_centavos = ?,
                saldo_cierre_arqueado_centavos = ?,
                diferencia_centavos = ?
            WHERE jornada_id = ? AND fondo_id = ?;`,
      args: [esperado, arqueado, dif, jornada.id, f.id],
    });
  }

  stmts.push({
    sql: `UPDATE tesoreria_jornadas
          SET estado = 'CERRADA',
              fecha_cierre_utc = ?,
              cerrada_por = ?,
              observaciones_cierre = ?,
              version = version + 1
          WHERE id = ?;`,
    args: [nowUtc, usuario, observaciones, jornada.id],
  });

  await client.batch(stmts, "write");
  return obtenerJornadaPorIdWorker(client, jornada.id);
}

// ============================================================================
// MOVIMIENTOS (ENTRY / EXIT)
// ============================================================================

export async function registrarMovimientoWorker(
  client,
  tipo,
  { codigo_fondo, monto_centavos, contrapartida, motivo, terminal_id = "TERM-01", referencia_evidencia = null },
  usuario,
  idempotencyKey = null
) {
  const monto = Number(monto_centavos);
  if (!Number.isInteger(monto) || monto <= 0) {
    throw new Error("INVALID_INPUT: El importe debe ser un entero positivo (> 0).");
  }
  if (!motivo || !String(motivo).trim()) {
    throw new Error("INVALID_INPUT: El motivo del movimiento es obligatorio.");
  }
  if (!contrapartida || !String(contrapartida).trim()) {
    throw new Error("INVALID_INPUT: La contrapartida económica es obligatoria.");
  }

  const payloadHashObj = {
    tipo,
    codigo_fondo: String(codigo_fondo).trim(),
    monto_centavos: monto,
    contrapartida: String(contrapartida).trim(),
    motivo: String(motivo).trim(),
  };
  const previo = await verificarIdempotenciaTesoreria(client, idempotencyKey, payloadHashObj);
  if (previo) return previo;

  let jornada = await obtenerJornadaActualWorker(client);
  if (!jornada) {
    throw new Error("INVALID_INPUT: Operación rechazada: Se requiere una jornada de tesorería ABIERTA.");
  }
  if (jornada.estado !== "ABIERTA") {
    throw new Error(`INVALID_INPUT: La jornada actual está en estado '${jornada.estado}'.`);
  }

  const fondo = await obtenerFondoDetalleWorker(client, codigo_fondo);
  if (fondo.estado !== "ACTIVO") {
    throw new Error(`INVALID_INPUT: El fondo ${codigo_fondo} no está habilitado (estado: ${fondo.estado}).`);
  }

  const reservado = await obtenerReservadoFondo(client, fondo.id);
  const disponible = fondo.saldo_centavos - reservado;

  if (tipo === "EXIT" && disponible < monto && fondo.permite_sobregiro === 0) {
    throw new Error(
      `UNPROCESSABLE: Saldo disponible insuficiente en fondo ${codigo_fondo}: Disponible ${formatMoney(disponible)} para egreso de ${formatMoney(monto)}.`
    );
  }

  const nuevoSaldo = tipo === "EXIT" ? fondo.saldo_centavos - monto : fondo.saldo_centavos + monto;
  const nowUtc = new Date().toISOString();
  const movementId = crypto.randomUUID();

  await client.batch(
    [
      {
        sql: `UPDATE tesoreria_fondos
              SET saldo_centavos = ?, version = version + 1, updated_at = ?, actualizado_por = ?
              WHERE id = ?;`,
        args: [nuevoSaldo, nowUtc, usuario, fondo.id],
      },
      {
        sql: `INSERT INTO tesoreria_movimientos (
                movement_id, idempotency_key, journal_id, fund_id, movement_type,
                amount_cents, currency, counterpart, reason, evidence_reference,
                created_by, terminal_id, status, occurred_at, recorded_at
              ) VALUES (?, ?, ?, ?, ?, ?, 'ARS', ?, ?, ?, ?, ?, 'POSTED', ?, ?);`,
        args: [
          movementId,
          idempotencyKey || null,
          jornada.id,
          fondo.id,
          tipo,
          monto,
          String(contrapartida).trim(),
          String(motivo).trim(),
          referencia_evidencia || null,
          usuario,
          terminal_id || "TERM-01",
          nowUtc,
          nowUtc,
        ],
      },
    ],
    "write"
  );

  const resultado = {
    movement_id: movementId,
    idempotency_key: idempotencyKey || null,
    journal_id: jornada.id,
    fund_id: fondo.id,
    codigo_fondo: fondo.codigo,
    nombre_fondo: fondo.nombre,
    movement_type: tipo,
    amount_cents: monto,
    amount_formatted: formatMoney(monto),
    currency: "ARS",
    counterpart: String(contrapartida).trim(),
    reason: String(motivo).trim(),
    evidence_reference: referencia_evidencia || null,
    created_by: usuario,
    terminal_id: terminal_id || "TERM-01",
    status: "POSTED",
    saldo_anterior_centavos: fondo.saldo_centavos,
    saldo_posterior_centavos: nuevoSaldo,
    occurred_at: nowUtc,
    recorded_at: nowUtc,
  };

  await recordAuditEventWorker(client, {
    event_type: `MOVEMENT_${tipo}`,
    aggregate_type: "MOVEMENT",
    aggregate_id: movementId,
    user_id: usuario,
    payload: resultado,
    occurred_at: nowUtc,
  });

  await registrarIdempotenciaTesoreria(client, {
    idempotencyKey,
    operationType: tipo,
    resourceId: movementId,
    payloadObj: payloadHashObj,
    responseData: resultado,
  });

  return resultado;
}

export async function listarMovimientosWorker(
  client,
  { codigo_fondo = null, jornada_id = null, movement_type = null, search = null, limite = 50 } = {}
) {
  let sql = `
    SELECT m.*, f.codigo as codigo_fondo, f.nombre as nombre_fondo
    FROM tesoreria_movimientos m
    JOIN tesoreria_fondos f ON m.fund_id = f.id
    WHERE 1=1
  `;
  const args = [];
  if (codigo_fondo) {
    sql += " AND f.codigo = ?";
    args.push(String(codigo_fondo).trim());
  }
  if (jornada_id !== null && jornada_id !== undefined && String(jornada_id) !== "") {
    sql += " AND m.journal_id = ?";
    args.push(Number(jornada_id));
  }
  if (movement_type) {
    sql += " AND m.movement_type = ?";
    args.push(String(movement_type).trim().toUpperCase());
  }
  if (search && String(search).trim()) {
    const q = `%${String(search).trim()}%`;
    sql += " AND (m.counterpart LIKE ? OR m.reason LIKE ? OR m.evidence_reference LIKE ? OR m.movement_id LIKE ?)";
    args.push(q, q, q, q);
  }
  sql += " ORDER BY m.id DESC LIMIT ?;";
  args.push(Number(limite) || 50);

  const res = await client.execute({ sql, args });
  return res.rows.map((r) => ({
    movement_id: String(r.movement_id),
    idempotency_key: r.idempotency_key ? String(r.idempotency_key) : null,
    journal_id: Number(r.journal_id),
    fund_id: Number(r.fund_id),
    codigo_fondo: String(r.codigo_fondo),
    nombre_fondo: String(r.nombre_fondo),
    movement_type: String(r.movement_type),
    amount_cents: Number(r.amount_cents),
    amount_formatted: formatMoney(Number(r.amount_cents)),
    currency: String(r.currency || "ARS"),
    counterpart: String(r.counterpart),
    reason: String(r.reason),
    evidence_reference: r.evidence_reference ? String(r.evidence_reference) : null,
    created_by: String(r.created_by),
    terminal_id: String(r.terminal_id || "TERM-01"),
    status: String(r.status || "POSTED"),
    occurred_at: String(r.occurred_at),
    recorded_at: String(r.recorded_at),
    is_reversal: Boolean(r.is_reversal),
    reversal_of_id: r.reversal_of_id ? String(r.reversal_of_id) : null,
    reversed_amount_cents: Number(r.reversed_amount_cents || 0),
  }));
}

// ============================================================================
// TRASLADOS
// ============================================================================

const TRANSFERENCIAS_PERMITIDAS = new Set([
  "1110->1120",
  "1120->1110",
  "1110->1140",
  "1140->1120",
  "1130->1120",
  "1130->1110",
  "1110->1130",
  "1120->1130",
  "1160->1130",
  "1110->1210",
  "1310->1110",
  "1310->1120",
]);

export async function iniciarTrasladoWorker(
  client,
  payload,
  usuario,
  idempotencyKey = null,
  { allowInternalSweepOrRepo = false } = {}
) {
  const orig = String(payload.origin_fund_code || payload.codigo_origen || "").trim();
  const dest = String(payload.destination_fund_code || payload.codigo_destino || "").trim();
  const monto = Number(payload.amount_cents !== undefined ? payload.amount_cents : payload.monto_centavos);
  const motivo = String(payload.reason || payload.motivo || "").trim();
  const evidencia = payload.evidence_reference || payload.evidencia || null;

  if (!orig || !dest || orig === dest) {
    throw new Error("INVALID_INPUT: Los fondos de origen y destino deben ser válidos y distintos.");
  }
  if (!Number.isInteger(monto) || monto <= 0) {
    throw new Error("INVALID_INPUT: El importe de traslado debe ser positivo (> 0).");
  }
  if (!motivo) {
    throw new Error("INVALID_INPUT: El motivo del traslado es obligatorio.");
  }
  if (!TRANSFERENCIAS_PERMITIDAS.has(`${orig}->${dest}`)) {
    throw new Error(
      `INVALID_INPUT: La combinación de traslado entre ${orig} y ${dest} no está autorizada por la política de fondos de Tesorería.`
    );
  }
  if (!allowInternalSweepOrRepo && orig === "1120" && dest === "1130") {
    throw new Error(
      "INVALID_INPUT: El traslado 1120 -> 1130 está restringido a compensaciones automáticas PREPAY o reposiciones formalizadas de preventistas."
    );
  }
  if (!allowInternalSweepOrRepo && orig === "1160" && dest === "1130") {
    throw new Error(
      "INVALID_INPUT: El traslado 1160 -> 1130 es un barrido de cierre (executePrepayDrawerSweep) y no admite ejecución manual."
    );
  }

  const payloadHashObj = { orig, dest, monto, motivo };
  const previo = await verificarIdempotenciaTesoreria(client, idempotencyKey, payloadHashObj);
  if (previo) return previo;

  const jornada = await obtenerJornadaActualWorker(client);
  if (!jornada || (jornada.estado !== "ABIERTA" && !(allowInternalSweepOrRepo && jornada.estado === "EN_CIERRE"))) {
    throw new Error("INVALID_INPUT: Se requiere una jornada de tesorería ABIERTA para operar traslados.");
  }

  const fOrig = await obtenerFondoDetalleWorker(client, orig);
  const fDest = await obtenerFondoDetalleWorker(client, dest);
  if (fOrig.estado !== "ACTIVO" || fDest.estado !== "ACTIVO") {
    throw new Error("INVALID_INPUT: Ambos fondos deben estar en estado ACTIVO.");
  }

  const resOrig = await obtenerReservadoFondo(client, fOrig.id);
  const dispOrig = fOrig.saldo_centavos - resOrig;
  if (dispOrig < monto) {
    throw new Error(
      `UNPROCESSABLE: Saldo disponible insuficiente en ${orig} (${formatMoney(dispOrig)}) para trasladar ${formatMoney(monto)}.`
    );
  }

  const transferId = crypto.randomUUID();
  const exitMovId = crypto.randomUUID();
  const nowUtc = new Date().toISOString();
  const nuevoSaldoOrig = fOrig.saldo_centavos - monto;

  await client.batch(
    [
      {
        sql: `UPDATE tesoreria_fondos
              SET saldo_centavos = ?, version = version + 1, updated_at = ?, actualizado_por = ?
              WHERE id = ?;`,
        args: [nuevoSaldoOrig, nowUtc, usuario, fOrig.id],
      },
      {
        sql: `INSERT INTO tesoreria_movimientos (
                movement_id, idempotency_key, journal_id, fund_id, movement_type,
                amount_cents, currency, counterpart, reason, evidence_reference,
                created_by, terminal_id, status, occurred_at, recorded_at
              ) VALUES (?, ?, ?, ?, 'TRANSFER_EXIT', ?, 'ARS', ?, ?, ?, ?, 'TERM-01', 'POSTED', ?, ?);`,
        args: [
          exitMovId,
          idempotencyKey ? `${idempotencyKey}_EXIT` : null,
          jornada.id,
          fOrig.id,
          monto,
          `Traslado hacia ${fDest.codigo} (${fDest.nombre})`,
          motivo,
          evidencia,
          usuario,
          nowUtc,
          nowUtc,
        ],
      },
      {
        sql: `INSERT INTO tesoreria_traslados (
                transfer_id, idempotency_key, journal_id, origin_fund_id, destination_fund_id,
                amount_cents, currency, reason, evidence_reference, dispatched_by, dispatched_at,
                status, exit_movement_id
              ) VALUES (?, ?, ?, ?, ?, ?, 'ARS', ?, ?, ?, ?, 'IN_TRANSIT', ?);`,
        args: [
          transferId,
          idempotencyKey || null,
          jornada.id,
          fOrig.id,
          fDest.id,
          monto,
          motivo,
          evidencia,
          usuario,
          nowUtc,
          exitMovId,
        ],
      },
    ],
    "write"
  );

  const resultado = {
    transfer_id: transferId,
    idempotency_key: idempotencyKey || null,
    journal_id: jornada.id,
    origin_fund_code: fOrig.codigo,
    origin_fund_name: fOrig.nombre,
    destination_fund_code: fDest.codigo,
    destination_fund_name: fDest.nombre,
    amount_cents: monto,
    amount_formatted: formatMoney(monto),
    reason: motivo,
    evidence_reference: evidencia,
    dispatched_by: usuario,
    dispatched_at: nowUtc,
    status: "IN_TRANSIT",
    exit_movement_id: exitMovId,
    entry_movement_id: null,
    origin_new_balance_cents: nuevoSaldoOrig,
  };

  await registrarIdempotenciaTesoreria(client, {
    idempotencyKey,
    operationType: "TRANSFER_INIT",
    resourceId: transferId,
    payloadObj: payloadHashObj,
    responseData: resultado,
  });

  return resultado;
}

export async function confirmarTrasladoWorker(client, transferId, usuario, idempotencyKey = null) {
  const payloadHashObj = { action: "CONFIRM", transferId };
  const previo = await verificarIdempotenciaTesoreria(client, idempotencyKey, payloadHashObj);
  if (previo) return previo;

  const tRes = await client.execute({
    sql: `SELECT t.*, fo.codigo as origin_code, fo.nombre as origin_name,
                 fd.codigo as dest_code, fd.nombre as dest_name, fd.saldo_centavos as dest_balance
          FROM tesoreria_traslados t
          JOIN tesoreria_fondos fo ON t.origin_fund_id = fo.id
          JOIN tesoreria_fondos fd ON t.destination_fund_id = fd.id
          WHERE t.transfer_id = ?;`,
    args: [transferId],
  });
  if (tRes.rows.length === 0) {
    throw new Error(`NOT_FOUND: Traslado ${transferId} no encontrado.`);
  }
  const t = tRes.rows[0];
  if (String(t.status) !== "IN_TRANSIT") {
    throw new Error(`INVALID_INPUT: El traslado ${transferId} no está en tránsito (estado: ${t.status}).`);
  }

  const monto = Number(t.amount_cents);
  const nuevoSaldoDest = Number(t.dest_balance) + monto;
  const entryMovId = crypto.randomUUID();
  const nowUtc = new Date().toISOString();

  await client.batch(
    [
      {
        sql: `UPDATE tesoreria_fondos
              SET saldo_centavos = ?, version = version + 1, updated_at = ?, actualizado_por = ?
              WHERE id = ?;`,
        args: [nuevoSaldoDest, nowUtc, usuario, Number(t.destination_fund_id)],
      },
      {
        sql: `INSERT INTO tesoreria_movimientos (
                movement_id, idempotency_key, journal_id, fund_id, movement_type,
                amount_cents, currency, counterpart, reason, evidence_reference,
                created_by, terminal_id, status, occurred_at, recorded_at
              ) VALUES (?, ?, ?, ?, 'TRANSFER_ENTRY', ?, 'ARS', ?, ?, ?, ?, 'TERM-01', 'POSTED', ?, ?);`,
        args: [
          entryMovId,
          idempotencyKey ? `${idempotencyKey}_ENTRY` : null,
          Number(t.journal_id),
          Number(t.destination_fund_id),
          monto,
          `Recepción desde ${t.origin_code} (${t.origin_name})`,
          String(t.reason),
          t.evidence_reference || null,
          usuario,
          nowUtc,
          nowUtc,
        ],
      },
      {
        sql: `UPDATE tesoreria_traslados
              SET status = 'CONFIRMED', received_by = ?, received_at = ?, entry_movement_id = ?
              WHERE transfer_id = ?;`,
        args: [usuario, nowUtc, entryMovId, transferId],
      },
    ],
    "write"
  );

  const resultado = {
    transfer_id: transferId,
    origin_fund_code: String(t.origin_code),
    destination_fund_code: String(t.dest_code),
    amount_cents: monto,
    amount_formatted: formatMoney(monto),
    received_by: usuario,
    received_at: nowUtc,
    status: "CONFIRMED",
    exit_movement_id: String(t.exit_movement_id),
    entry_movement_id: entryMovId,
    destination_new_balance_cents: nuevoSaldoDest,
  };

  await registrarIdempotenciaTesoreria(client, {
    idempotencyKey,
    operationType: "TRANSFER_CONFIRM",
    resourceId: transferId,
    payloadObj: payloadHashObj,
    responseData: resultado,
  });

  return resultado;
}

export async function cancelarTrasladoWorker(client, transferId, payload, usuario, idempotencyKey = null) {
  const motivo = String(payload?.reason || payload?.motivo || "").trim();
  if (!motivo) {
    throw new Error("INVALID_INPUT: El motivo de cancelación del traslado es obligatorio.");
  }
  const payloadHashObj = { action: "CANCEL", transferId, motivo };
  const previo = await verificarIdempotenciaTesoreria(client, idempotencyKey, payloadHashObj);
  if (previo) return previo;

  const tRes = await client.execute({
    sql: `SELECT t.*, fo.codigo as origin_code, fo.nombre as origin_name, fo.saldo_centavos as orig_balance,
                 fd.codigo as dest_code, fd.nombre as dest_name
          FROM tesoreria_traslados t
          JOIN tesoreria_fondos fo ON t.origin_fund_id = fo.id
          JOIN tesoreria_fondos fd ON t.destination_fund_id = fd.id
          WHERE t.transfer_id = ?;`,
    args: [transferId],
  });
  if (tRes.rows.length === 0) {
    throw new Error(`NOT_FOUND: Traslado ${transferId} no encontrado.`);
  }
  const t = tRes.rows[0];
  if (String(t.status) !== "IN_TRANSIT") {
    throw new Error(`INVALID_INPUT: Solo se pueden cancelar traslados en tránsito (estado actual: ${t.status}).`);
  }

  const monto = Number(t.amount_cents);
  const nuevoSaldoOrig = Number(t.orig_balance) + monto;
  const cancelMovId = crypto.randomUUID();
  const nowUtc = new Date().toISOString();

  await client.batch(
    [
      {
        sql: `UPDATE tesoreria_fondos
              SET saldo_centavos = ?, version = version + 1, updated_at = ?, actualizado_por = ?
              WHERE id = ?;`,
        args: [nuevoSaldoOrig, nowUtc, usuario, Number(t.origin_fund_id)],
      },
      {
        sql: `INSERT INTO tesoreria_movimientos (
                movement_id, idempotency_key, journal_id, fund_id, movement_type,
                amount_cents, currency, counterpart, reason, evidence_reference,
                created_by, terminal_id, status, occurred_at, recorded_at
              ) VALUES (?, ?, ?, ?, 'TRANSFER_CANCEL', ?, 'ARS', ?, ?, ?, ?, 'TERM-01', 'POSTED', ?, ?);`,
        args: [
          cancelMovId,
          idempotencyKey ? `${idempotencyKey}_CANCEL` : null,
          Number(t.journal_id),
          Number(t.origin_fund_id),
          monto,
          `Restitución por cancelación de traslado ${transferId}`,
          motivo,
          t.evidence_reference || null,
          usuario,
          nowUtc,
          nowUtc,
        ],
      },
      {
        sql: `UPDATE tesoreria_traslados
              SET status = 'CANCELLED', cancelled_by = ?, cancelled_at = ?, cancellation_reason = ?, cancel_movement_id = ?
              WHERE transfer_id = ?;`,
        args: [usuario, nowUtc, motivo, cancelMovId, transferId],
      },
    ],
    "write"
  );

  const resultado = {
    transfer_id: transferId,
    origin_fund_code: String(t.origin_code),
    destination_fund_code: String(t.dest_code),
    amount_cents: monto,
    amount_formatted: formatMoney(monto),
    cancelled_by: usuario,
    cancelled_at: nowUtc,
    cancellation_reason: motivo,
    status: "CANCELLED",
    cancel_movement_id: cancelMovId,
    origin_restored_balance_cents: nuevoSaldoOrig,
  };

  await registrarIdempotenciaTesoreria(client, {
    idempotencyKey,
    operationType: "TRANSFER_CANCEL",
    resourceId: transferId,
    payloadObj: payloadHashObj,
    responseData: resultado,
  });

  return resultado;
}

export async function listarTrasladosWorker(
  client,
  { limite = 50, status = null, origin_fund_code = null, destination_fund_code = null, search = null } = {}
) {
  let sql = `
    SELECT t.*, fo.codigo as origin_fund_code, fo.nombre as origin_fund_name,
           fd.codigo as destination_fund_code, fd.nombre as destination_fund_name
    FROM tesoreria_traslados t
    JOIN tesoreria_fondos fo ON t.origin_fund_id = fo.id
    JOIN tesoreria_fondos fd ON t.destination_fund_id = fd.id
    WHERE 1=1
  `;
  const args = [];
  if (status) {
    sql += " AND t.status = ?";
    args.push(String(status).trim().toUpperCase());
  }
  if (origin_fund_code) {
    sql += " AND fo.codigo = ?";
    args.push(String(origin_fund_code).trim());
  }
  if (destination_fund_code) {
    sql += " AND fd.codigo = ?";
    args.push(String(destination_fund_code).trim());
  }
  if (search && String(search).trim()) {
    const q = `%${String(search).trim()}%`;
    sql += " AND (t.reason LIKE ? OR t.evidence_reference LIKE ? OR t.transfer_id LIKE ?)";
    args.push(q, q, q);
  }
  sql += " ORDER BY t.id DESC LIMIT ?;";
  args.push(Number(limite) || 50);

  const res = await client.execute({ sql, args });
  return res.rows.map((r) => ({
    transfer_id: String(r.transfer_id),
    journal_id: Number(r.journal_id),
    origin_fund_code: String(r.origin_fund_code),
    origin_fund_name: String(r.origin_fund_name),
    destination_fund_code: String(r.destination_fund_code),
    destination_fund_name: String(r.destination_fund_name),
    amount_cents: Number(r.amount_cents),
    amount_formatted: formatMoney(Number(r.amount_cents)),
    reason: String(r.reason),
    evidence_reference: r.evidence_reference ? String(r.evidence_reference) : null,
    dispatched_by: String(r.dispatched_by),
    dispatched_at: String(r.dispatched_at),
    received_by: r.received_by ? String(r.received_by) : null,
    received_at: r.received_at ? String(r.received_at) : null,
    cancelled_by: r.cancelled_by ? String(r.cancelled_by) : null,
    cancelled_at: r.cancelled_at ? String(r.cancelled_at) : null,
    cancellation_reason: r.cancellation_reason ? String(r.cancellation_reason) : null,
    status: String(r.status),
    exit_movement_id: String(r.exit_movement_id),
    entry_movement_id: r.entry_movement_id ? String(r.entry_movement_id) : null,
    cancel_movement_id: r.cancel_movement_id ? String(r.cancel_movement_id) : null,
    is_reversed: Boolean(r.is_reversed),
  }));
}

export async function obtenerTrasladosTransitoFondoWorker(client, codigoFondo) {
  const f = await obtenerFondoDetalleWorker(client, codigoFondo);
  const [salientes, entrantes] = await Promise.all([
    listarTrasladosWorker(client, { status: "IN_TRANSIT", origin_fund_code: f.codigo, limite: 100 }),
    listarTrasladosWorker(client, { status: "IN_TRANSIT", destination_fund_code: f.codigo, limite: 100 }),
  ]);
  const totalOut = salientes.reduce((acc, x) => acc + x.amount_cents, 0);
  const totalIn = entrantes.reduce((acc, x) => acc + x.amount_cents, 0);
  return {
    fund_code: f.codigo,
    fund_name: f.nombre,
    total_out_cents: totalOut,
    total_out_formatted: formatMoney(totalOut),
    total_in_cents: totalIn,
    total_in_formatted: formatMoney(totalIn),
    outgoing_transfers: salientes,
    incoming_transfers: entrantes,
  };
}

// ============================================================================
// RESERVAS
// ============================================================================

export async function crearReservaWorker(client, payload, usuario, idempotencyKey = null) {
  const fundCode = String(payload.fund_code || "").trim();
  const amountCents = Number(payload.amount_cents);
  const reason = String(payload.reason || "").trim();
  const beneficiary = String(payload.beneficiary || "").trim();
  const obligationId = payload.obligation_id ? String(payload.obligation_id).trim() : null;
  const evidenceRef = payload.evidence_reference ? String(payload.evidence_reference).trim() : null;

  if (!fundCode || !Number.isInteger(amountCents) || amountCents <= 0 || !reason || !beneficiary) {
    throw new Error("INVALID_INPUT: Datos de reserva incompletos o inválidos.");
  }

  const payloadHashObj = { fundCode, amountCents, reason, beneficiary, obligationId };
  const previo = await verificarIdempotenciaTesoreria(client, idempotencyKey, payloadHashObj);
  if (previo) return previo;

  const jornada = await obtenerJornadaActualWorker(client);
  if (!jornada || jornada.estado !== "ABIERTA") {
    throw new Error("INVALID_INPUT: Se requiere una jornada de tesorería ABIERTA para crear reservas.");
  }

  const fondo = await obtenerFondoDetalleWorker(client, fundCode);
  if (fondo.estado !== "ACTIVO") {
    throw new Error(`INVALID_INPUT: El fondo ${fundCode} no está activo.`);
  }

  if (obligationId) {
    const dup = await client.execute({
      sql: "SELECT reservation_id FROM tesoreria_reservas WHERE obligation_id = ? AND status IN ('ACTIVE', 'PARTIALLY_APPLIED');",
      args: [obligationId],
    });
    if (dup.rows.length > 0) {
      throw new Error(`INVALID_INPUT: Doble reserva rechazada para la obligación '${obligationId}'.`);
    }
  }

  const resPrev = await obtenerReservadoFondo(client, fondo.id);
  const disp = fondo.saldo_centavos - resPrev;
  if (disp < amountCents) {
    throw new Error(
      `INVALID_INPUT: Saldo disponible insuficiente en ${fundCode} (${formatMoney(disp)}) para reservar ${formatMoney(amountCents)}.`
    );
  }

  const reservationId = crypto.randomUUID();
  const nowUtc = new Date().toISOString();

  await client.execute({
    sql: `INSERT INTO tesoreria_reservas (
            reservation_id, idempotency_key, journal_id, fund_id,
            original_amount_cents, applied_amount_cents, released_amount_cents, pending_amount_cents,
            reason, beneficiary, obligation_id, evidence_reference, created_by,
            created_at, updated_at, status
          ) VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE');`,
    args: [
      reservationId,
      idempotencyKey || null,
      jornada.id,
      fondo.id,
      amountCents,
      amountCents,
      reason,
      beneficiary,
      obligationId,
      evidenceRef,
      usuario,
      nowUtc,
      nowUtc,
    ],
  });

  const resultado = {
    reservation_id: reservationId,
    idempotency_key: idempotencyKey || null,
    journal_id: jornada.id,
    fund_code: fondo.codigo,
    fund_name: fondo.nombre,
    original_amount_cents: amountCents,
    applied_amount_cents: 0,
    released_amount_cents: 0,
    pending_amount_cents: amountCents,
    amount_formatted: formatMoney(amountCents),
    reason,
    beneficiary,
    obligation_id: obligationId,
    created_by: usuario,
    created_at: nowUtc,
    status: "ACTIVE",
  };

  await registrarIdempotenciaTesoreria(client, {
    idempotencyKey,
    operationType: "RESERVATION_CREATE",
    resourceId: reservationId,
    payloadObj: payloadHashObj,
    responseData: resultado,
  });

  return resultado;
}

export async function aplicarReservaWorker(client, reservationId, payload, usuario, idempotencyKey = null) {
  const amountCents = Number(payload.amount_cents);
  const reason = String(payload.reason || "").trim();
  const evidenceRef = payload.evidence_reference ? String(payload.evidence_reference).trim() : null;
  if (!Number.isInteger(amountCents) || amountCents <= 0 || !reason) {
    throw new Error("INVALID_INPUT: Importe y motivo son obligatorios.");
  }

  const rRes = await client.execute({
    sql: `SELECT r.*, f.codigo as fund_code, f.nombre as fund_name
          FROM tesoreria_reservas r
          JOIN tesoreria_fondos f ON r.fund_id = f.id
          WHERE r.reservation_id = ?;`,
    args: [reservationId],
  });
  if (rRes.rows.length === 0) throw new Error(`NOT_FOUND: Reserva ${reservationId} no encontrada.`);
  const r = rRes.rows[0];
  const pending = Number(r.pending_amount_cents);
  if (amountCents > pending) {
    throw new Error(`INVALID_INPUT: El importe a aplicar supera el saldo pendiente de la reserva (${formatMoney(pending)}).`);
  }

  const newApplied = Number(r.applied_amount_cents) + amountCents;
  const newPending = pending - amountCents;
  const newStatus = newPending === 0 ? "FULLY_APPLIED" : "PARTIALLY_APPLIED";
  const nowUtc = new Date().toISOString();

  await client.batch(
    [
      {
        sql: `UPDATE tesoreria_reservas
              SET applied_amount_cents = ?, pending_amount_cents = ?, status = ?, updated_at = ?
              WHERE reservation_id = ?;`,
        args: [newApplied, newPending, newStatus, nowUtc, reservationId],
      },
      {
        sql: `INSERT INTO tesoreria_reserva_movimientos (
                reservation_id, operation_type, amount_cents, reason, evidence_reference,
                performed_by, occurred_at, idempotency_key
              ) VALUES (?, 'APPLY', ?, ?, ?, ?, ?, ?);`,
        args: [reservationId, amountCents, reason, evidenceRef, usuario, nowUtc, idempotencyKey || null],
      },
    ],
    "write"
  );

  return {
    reservation_id: reservationId,
    fund_code: String(r.fund_code),
    original_amount_cents: Number(r.original_amount_cents),
    applied_amount_cents: newApplied,
    released_amount_cents: Number(r.released_amount_cents),
    pending_amount_cents: newPending,
    status: newStatus,
    updated_at: nowUtc,
  };
}

export async function liberarReservaWorker(client, reservationId, payload, usuario, idempotencyKey = null) {
  const amountCents = Number(payload.amount_cents);
  const reason = String(payload.reason || "").trim();
  const evidenceRef = payload.evidence_reference ? String(payload.evidence_reference).trim() : null;
  if (!Number.isInteger(amountCents) || amountCents <= 0 || !reason) {
    throw new Error("INVALID_INPUT: Importe y motivo son obligatorios.");
  }

  const rRes = await client.execute({
    sql: `SELECT r.*, f.codigo as fund_code, f.nombre as fund_name
          FROM tesoreria_reservas r
          JOIN tesoreria_fondos f ON r.fund_id = f.id
          WHERE r.reservation_id = ?;`,
    args: [reservationId],
  });
  if (rRes.rows.length === 0) throw new Error(`NOT_FOUND: Reserva ${reservationId} no encontrada.`);
  const r = rRes.rows[0];
  const pending = Number(r.pending_amount_cents);
  if (amountCents > pending) {
    throw new Error(`INVALID_INPUT: El importe a liberar supera el saldo pendiente de la reserva (${formatMoney(pending)}).`);
  }

  const newReleased = Number(r.released_amount_cents) + amountCents;
  const newPending = pending - amountCents;
  const newStatus = newPending === 0 ? (Number(r.applied_amount_cents) > 0 ? "FULLY_APPLIED" : "RELEASED") : "PARTIALLY_APPLIED";
  const nowUtc = new Date().toISOString();

  await client.batch(
    [
      {
        sql: `UPDATE tesoreria_reservas
              SET released_amount_cents = ?, pending_amount_cents = ?, status = ?, updated_at = ?
              WHERE reservation_id = ?;`,
        args: [newReleased, newPending, newStatus, nowUtc, reservationId],
      },
      {
        sql: `INSERT INTO tesoreria_reserva_movimientos (
                reservation_id, operation_type, amount_cents, reason, evidence_reference,
                performed_by, occurred_at, idempotency_key
              ) VALUES (?, 'RELEASE', ?, ?, ?, ?, ?, ?);`,
        args: [reservationId, amountCents, reason, evidenceRef, usuario, nowUtc, idempotencyKey || null],
      },
    ],
    "write"
  );

  return {
    reservation_id: reservationId,
    fund_code: String(r.fund_code),
    original_amount_cents: Number(r.original_amount_cents),
    applied_amount_cents: Number(r.applied_amount_cents),
    released_amount_cents: newReleased,
    pending_amount_cents: newPending,
    status: newStatus,
    updated_at: nowUtc,
  };
}

export async function ejecutarEgresoReservadoWorker(client, reservationId, payload, usuario, idempotencyKey = null) {
  const aplicada = await aplicarReservaWorker(client, reservationId, payload, usuario, idempotencyKey ? `${idempotencyKey}_APP` : null);
  const mov = await registrarMovimientoWorker(
    client,
    "EXIT",
    {
      codigo_fondo: aplicada.fund_code,
      monto_centavos: Number(payload.amount_cents),
      contrapartida: payload.counterpart || "Cumplimiento de reserva",
      motivo: payload.reason,
      referencia_evidencia: payload.evidence_reference || `RES_${reservationId}`,
    },
    usuario,
    idempotencyKey ? `${idempotencyKey}_EXIT` : null
  );
  return {
    reservation: aplicada,
    exit_movement: mov,
  };
}

export async function obtenerHistorialReservaWorker(client, reservationId) {
  const rRes = await client.execute({
    sql: `SELECT r.*, f.codigo as fund_code, f.nombre as fund_name
          FROM tesoreria_reservas r
          JOIN tesoreria_fondos f ON r.fund_id = f.id
          WHERE r.reservation_id = ?;`,
    args: [reservationId],
  });
  if (rRes.rows.length === 0) throw new Error(`NOT_FOUND: Reserva ${reservationId} no encontrada.`);
  const r = rRes.rows[0];
  const ops = await client.execute({
    sql: "SELECT * FROM tesoreria_reserva_movimientos WHERE reservation_id = ? ORDER BY id ASC;",
    args: [reservationId],
  });
  return {
    reservation_id: String(r.reservation_id),
    journal_id: Number(r.journal_id),
    fund_code: String(r.fund_code),
    fund_name: String(r.fund_name),
    original_amount_cents: Number(r.original_amount_cents),
    applied_amount_cents: Number(r.applied_amount_cents),
    released_amount_cents: Number(r.released_amount_cents),
    pending_amount_cents: Number(r.pending_amount_cents),
    reason: String(r.reason),
    beneficiary: String(r.beneficiary),
    obligation_id: r.obligation_id ? String(r.obligation_id) : null,
    created_by: String(r.created_by),
    created_at: String(r.created_at),
    status: String(r.status),
    operations: ops.rows.map((o) => ({
      id: Number(o.id),
      operation_type: String(o.operation_type),
      amount_cents: Number(o.amount_cents),
      reason: String(o.reason),
      evidence_reference: o.evidence_reference ? String(o.evidence_reference) : null,
      performed_by: String(o.performed_by),
      occurred_at: String(o.occurred_at),
      is_reversed: Boolean(o.is_reversed),
    })),
  };
}

export async function listarReservasWorker(client, { codigo_fondo = null, solo_activas = false, limite = 50 } = {}) {
  let sql = `
    SELECT r.*, f.codigo as fund_code, f.nombre as fund_name
    FROM tesoreria_reservas r
    JOIN tesoreria_fondos f ON r.fund_id = f.id
    WHERE 1=1
  `;
  const args = [];
  if (codigo_fondo) {
    sql += " AND f.codigo = ?";
    args.push(String(codigo_fondo).trim());
  }
  if (solo_activas) {
    sql += " AND r.status IN ('ACTIVE', 'PARTIALLY_APPLIED')";
  }
  sql += " ORDER BY r.id DESC LIMIT ?;";
  args.push(Number(limite) || 50);

  const res = await client.execute({ sql, args });
  return res.rows.map((r) => ({
    reservation_id: String(r.reservation_id),
    journal_id: Number(r.journal_id),
    fund_code: String(r.fund_code),
    fund_name: String(r.fund_name),
    original_amount_cents: Number(r.original_amount_cents),
    applied_amount_cents: Number(r.applied_amount_cents),
    released_amount_cents: Number(r.released_amount_cents),
    pending_amount_cents: Number(r.pending_amount_cents),
    amount_formatted: formatMoney(Number(r.pending_amount_cents)),
    reason: String(r.reason),
    beneficiary: String(r.beneficiary),
    obligation_id: r.obligation_id ? String(r.obligation_id) : null,
    created_by: String(r.created_by),
    created_at: String(r.created_at),
    status: String(r.status),
  }));
}

// ============================================================================
// ARQUEOS Y EXPEDIENTES DE DIFERENCIAS
// ============================================================================

export async function crearArqueoWorker(client, payload, usuario) {
  const fundCode = String(payload.codigo_fondo || payload.fund_code || "").trim();
  const verifiedCents = Number(
    payload.monto_verificado_centavos !== undefined
      ? payload.monto_verificado_centavos
      : payload.saldo_contado_centavos !== undefined
      ? payload.saldo_contado_centavos
      : payload.verified_amount_cents
  );
  const notes = payload.observaciones || payload.notes || null;
  const recType = String(payload.reconciliation_type || payload.tipo_arqueo || "TOTAL").trim().toUpperCase();
  const esDigital = Boolean(payload.es_digital) || ["1210", "1220", "1230"].includes(fundCode);

  if (!fundCode || !Number.isInteger(verifiedCents) || verifiedCents < 0) {
    throw new Error("INVALID_INPUT: Código de fondo y monto verificado entero >= 0 son obligatorios.");
  }

  let jornada = await obtenerJornadaActualWorker(client);
  if (!jornada) {
    jornada = await abrirJornadaWorker(client, { observaciones: "Apertura automática al registrar arqueo" }, usuario);
  }

  const disp = await obtenerDisponibilidadFondoWorker(client, fundCode);
  const expectedCents = disp.saldo_registrado_cents;
  const diffCents = verifiedCents - expectedCents;
  const diffType = diffCents === 0 ? "EXACT" : diffCents > 0 ? "SURPLUS" : "SHORTAGE";
  const reconciliationId = crypto.randomUUID();
  const nowUtc = new Date().toISOString();

  const historyArr = notes
    ? [{ timestamp_utc: nowUtc, user_id: usuario, notes: String(notes).trim() }]
    : [];

  await client.execute({
    sql: `INSERT INTO tesoreria_arqueos (
            reconciliation_id, journal_id, fund_id, reconciliation_type,
            expected_amount_cents, verified_amount_cents, difference_cents, difference_type,
            in_transit_out_cents, in_transit_in_cents, active_reservations_cents,
            notes, created_by, created_at, shift_name, is_digital,
            evidence_reference, consultation_timestamp, notes_history_json
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);`,
    args: [
      reconciliationId,
      jornada.id,
      disp.fund_id,
      recType,
      expectedCents,
      verifiedCents,
      diffCents,
      diffType,
      disp.saldo_en_transito_saliente_cents,
      disp.saldo_en_transito_entrante_cents,
      disp.saldo_reservado_cents,
      notes ? String(notes).trim() : null,
      usuario,
      nowUtc,
      payload.turno || null,
      esDigital ? 1 : 0,
      payload.evidencia_referencia || null,
      payload.fecha_consulta || nowUtc,
      JSON.stringify(historyArr),
    ],
  });

  let caseData = null;
  if (diffCents !== 0 && (payload.crear_expediente_diferencia || payload.notas_investigacion)) {
    caseData = await abrirExpedienteArqueoWorker(
      client,
      reconciliationId,
      { notas_investigacion: payload.notas_investigacion || notes || `Diferencia detectada en arqueo de ${fundCode}` },
      usuario
    );
  }

  const arq = await obtenerArqueoWorker(client, reconciliationId);
  return { ...arq, difference_case: caseData };
}

export async function obtenerArqueoWorker(client, reconciliationId) {
  const res = await client.execute({
    sql: `SELECT a.*, f.codigo as fund_code, f.nombre as fund_name
          FROM tesoreria_arqueos a
          JOIN tesoreria_fondos f ON a.fund_id = f.id
          WHERE a.reconciliation_id = ?;`,
    args: [reconciliationId],
  });
  if (res.rows.length === 0) return null;
  const r = res.rows[0];
  let notesHistory = [];
  try {
    notesHistory = JSON.parse(String(r.notes_history_json || "[]"));
  } catch {}
  return {
    reconciliation_id: String(r.reconciliation_id),
    journal_id: Number(r.journal_id),
    fund_id: Number(r.fund_id),
    fund_code: String(r.fund_code),
    fund_name: String(r.fund_name),
    reconciliation_type: String(r.reconciliation_type),
    expected_amount_cents: Number(r.expected_amount_cents),
    expected_amount_formatted: formatMoney(Number(r.expected_amount_cents)),
    verified_amount_cents: Number(r.verified_amount_cents),
    verified_amount_formatted: formatMoney(Number(r.verified_amount_cents)),
    difference_cents: Number(r.difference_cents),
    difference_formatted: formatMoney(Number(r.difference_cents)),
    difference_type: String(r.difference_type),
    in_transit_out_cents: Number(r.in_transit_out_cents || 0),
    in_transit_in_cents: Number(r.in_transit_in_cents || 0),
    active_reservations_cents: Number(r.active_reservations_cents || 0),
    notes: r.notes ? String(r.notes) : null,
    created_by: String(r.created_by),
    created_at: String(r.created_at),
    shift_name: r.shift_name ? String(r.shift_name) : null,
    is_digital: Boolean(r.is_digital),
    evidence_reference: r.evidence_reference ? String(r.evidence_reference) : null,
    authorized_by: r.authorized_by ? String(r.authorized_by) : null,
    authorized_at: r.authorized_at ? String(r.authorized_at) : null,
    authorization_notes: r.authorization_notes ? String(r.authorization_notes) : null,
    notes_history: notesHistory,
  };
}

export async function listarArqueosWorker(client, { codigo_fondo = null, jornada_id = null, limite = 50 } = {}) {
  let sql = `
    SELECT a.reconciliation_id
    FROM tesoreria_arqueos a
    JOIN tesoreria_fondos f ON a.fund_id = f.id
    WHERE 1=1
  `;
  const args = [];
  if (codigo_fondo) {
    sql += " AND f.codigo = ?";
    args.push(String(codigo_fondo).trim());
  }
  if (jornada_id !== null && jornada_id !== undefined && String(jornada_id) !== "") {
    sql += " AND a.journal_id = ?";
    args.push(Number(jornada_id));
  }
  sql += " ORDER BY a.id DESC LIMIT ?;";
  args.push(Number(limite) || 50);

  const res = await client.execute({ sql, args });
  const out = [];
  for (const row of res.rows) {
    const item = await obtenerArqueoWorker(client, String(row.reconciliation_id));
    if (item) out.push(item);
  }
  return out;
}

export async function actualizarObservacionArqueoWorker(client, reconciliationId, { observaciones }, usuario) {
  const arq = await obtenerArqueoWorker(client, reconciliationId);
  if (!arq) throw new Error("NOT_FOUND: Arqueo no encontrado.");
  const nuevaNota = String(observaciones || "").trim();
  if (!nuevaNota) throw new Error("INVALID_INPUT: La observación no puede estar vacía.");
  const nowUtc = new Date().toISOString();
  const history = Array.isArray(arq.notes_history) ? [...arq.notes_history] : [];
  history.push({ timestamp_utc: nowUtc, user_id: usuario, notes: nuevaNota });

  await client.execute({
    sql: "UPDATE tesoreria_arqueos SET notes = ?, notes_history_json = ? WHERE reconciliation_id = ?;",
    args: [nuevaNota, JSON.stringify(history), reconciliationId],
  });
  return obtenerArqueoWorker(client, reconciliationId);
}

export async function autorizarDiferenciaArqueoWorker(client, reconciliationId, payload, usuario) {
  const arq = await obtenerArqueoWorker(client, reconciliationId);
  if (!arq) throw new Error("NOT_FOUND: Arqueo no encontrado.");
  const nowUtc = new Date().toISOString();
  const obs = payload?.observaciones ? String(payload.observaciones).trim() : "Diferencia autorizada por socio/admin";
  await client.execute({
    sql: "UPDATE tesoreria_arqueos SET authorized_by = ?, authorized_at = ?, authorization_notes = ? WHERE reconciliation_id = ?;",
    args: [usuario, nowUtc, obs, reconciliationId],
  });
  return obtenerArqueoWorker(client, reconciliationId);
}

export async function abrirExpedienteArqueoWorker(client, reconciliationId, { notas_investigacion }, usuario) {
  const arq = await obtenerArqueoWorker(client, reconciliationId);
  if (!arq) throw new Error("NOT_FOUND: Arqueo no encontrado.");
  if (arq.difference_cents === 0) {
    throw new Error("INVALID_INPUT: No se puede abrir expediente sobre un arqueo sin diferencias (EXACT).");
  }
  const caseId = crypto.randomUUID();
  const nowUtc = new Date().toISOString();
  const notas = String(notas_investigacion || "").trim() || "Apertura de expediente de diferencia";

  await client.execute({
    sql: `INSERT INTO tesoreria_expedientes_diferencias (
            case_id, reconciliation_id, journal_id, fund_id, difference_type,
            amount_cents, status, investigation_notes, opened_by, opened_at
          ) VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?);`,
    args: [
      caseId,
      reconciliationId,
      arq.journal_id,
      arq.fund_id,
      arq.difference_type,
      Math.abs(arq.difference_cents),
      notas,
      usuario,
      nowUtc,
    ],
  });

  return {
    case_id: caseId,
    reconciliation_id: reconciliationId,
    journal_id: arq.journal_id,
    fund_code: arq.fund_code,
    difference_type: arq.difference_type,
    amount_cents: Math.abs(arq.difference_cents),
    amount_formatted: formatMoney(Math.abs(arq.difference_cents)),
    status: "OPEN",
    investigation_notes: notas,
    opened_by: usuario,
    opened_at: nowUtc,
  };
}

export async function listarExpedientesWorker(
  client,
  { codigo_fondo = null, jornada_id = null, estado = null, limite = 50 } = {}
) {
  let sql = `
    SELECT e.*, f.codigo as fund_code, f.nombre as fund_name
    FROM tesoreria_expedientes_diferencias e
    JOIN tesoreria_fondos f ON e.fund_id = f.id
    WHERE 1=1
  `;
  const args = [];
  if (codigo_fondo) {
    sql += " AND f.codigo = ?";
    args.push(String(codigo_fondo).trim());
  }
  if (jornada_id !== null && jornada_id !== undefined && String(jornada_id) !== "") {
    sql += " AND e.journal_id = ?";
    args.push(Number(jornada_id));
  }
  if (estado) {
    sql += " AND e.status = ?";
    args.push(String(estado).trim().toUpperCase());
  }
  sql += " ORDER BY e.id DESC LIMIT ?;";
  args.push(Number(limite) || 50);

  const res = await client.execute({ sql, args });
  return res.rows.map((r) => ({
    case_id: String(r.case_id),
    reconciliation_id: String(r.reconciliation_id),
    journal_id: Number(r.journal_id),
    fund_code: String(r.fund_code),
    fund_name: String(r.fund_name),
    difference_type: String(r.difference_type),
    amount_cents: Number(r.amount_cents),
    amount_formatted: formatMoney(Number(r.amount_cents)),
    status: String(r.status),
    investigation_notes: String(r.investigation_notes),
    resolution_notes: r.resolution_notes ? String(r.resolution_notes) : null,
    evidence_reference: r.evidence_reference ? String(r.evidence_reference) : null,
    opened_by: String(r.opened_by),
    opened_at: String(r.opened_at),
    resolved_by: r.resolved_by ? String(r.resolved_by) : null,
    resolved_at: r.resolved_at ? String(r.resolved_at) : null,
  }));
}

export async function resolverExpedienteWorker(client, caseId, { notas_resolucion, referencia_evidencia }, usuario) {
  const notas = String(notas_resolucion || "").trim();
  const evid = String(referencia_evidencia || "").trim();
  if (!notas || !evid) {
    throw new Error("INVALID_INPUT: Notas de resolución y referencia de evidencia son obligatorias.");
  }
  const nowUtc = new Date().toISOString();
  await client.execute({
    sql: `UPDATE tesoreria_expedientes_diferencias
          SET status = 'RESOLVED', resolution_notes = ?, evidence_reference = ?, resolved_by = ?, resolved_at = ?
          WHERE case_id = ?;`,
    args: [notas, evid, usuario, nowUtc, caseId],
  });
  const list = await listarExpedientesWorker(client, { limite: 200 });
  const found = list.find((x) => x.case_id === caseId);
  if (!found) throw new Error("NOT_FOUND: Expediente no encontrado.");
  return found;
}

// ============================================================================
// CIERRES DEFINITIVOS (PROTOCOLO DE CIERRE)
// ============================================================================

export async function iniciarCierreWorker(client, { journal_id }, usuario) {
  const j = await obtenerJornadaPorIdWorker(client, Number(journal_id));
  if (!j) throw new Error(`NOT_FOUND: Jornada #${journal_id} no encontrada.`);
  if (j.estado === "CERRADA") throw new Error("INVALID_INPUT: La jornada ya está cerrada.");
  await client.execute({
    sql: "UPDATE tesoreria_jornadas SET estado = 'EN_CIERRE', version = version + 1 WHERE id = ?;",
    args: [j.id],
  });
  return obtenerJornadaPorIdWorker(client, j.id);
}

export async function validarCierreWorker(
  client,
  journalId,
  { allow_pending_transfers = false, allow_nonzero_1110 = false } = {}
) {
  const j = await obtenerJornadaPorIdWorker(client, Number(journalId));
  if (!j) throw new Error(`NOT_FOUND: Jornada #${journalId} no encontrada.`);
  const f1110 = await obtenerFondoDetalleWorker(client, "1110");
  const f1160 = await obtenerFondoDetalleWorker(client, "1160");
  const transito = await listarTrasladosWorker(client, { status: "IN_TRANSIT", limite: 100 });
  const expedientesAbiertos = await listarExpedientesWorker(client, { jornada_id: j.id, estado: "OPEN", limite: 100 });
  const arqueosJornada = await listarArqueosWorker(client, { jornada_id: j.id, limite: 100 });

  const blockers = [];
  if (!allow_nonzero_1110 && f1110.saldo_centavos !== 0) {
    blockers.push(`El fondo 1110 (Drugstore Diario) aún tiene saldo (${formatMoney(f1110.saldo_centavos)}). Ejecute el vaciado 1110 -> 1120.`);
  }
  if (f1160.saldo_centavos !== 0) {
    blockers.push(`El fondo 1160 (Prepay Diario) aún tiene saldo (${formatMoney(f1160.saldo_centavos)}). Ejecute el barrido 1160 -> 1130.`);
  }
  if (!allow_pending_transfers && transito.length > 0) {
    blockers.push(`Existen ${transito.length} traslados en tránsito pendientes de confirmación.`);
  }

  return {
    journal_id: j.id,
    journal_status: j.estado,
    ready_to_close: blockers.length === 0,
    blockers,
    fund_1110_balance_cents: f1110.saldo_centavos,
    fund_1160_balance_cents: f1160.saldo_centavos,
    pending_transfers_count: transito.length,
    open_difference_cases_count: expedientesAbiertos.length,
    reconciliations_count: arqueosJornada.length,
  };
}

export async function ejecutarVaciado1110Worker(client, { journal_id, motivo }, usuario) {
  const f1110 = await obtenerFondoDetalleWorker(client, "1110");
  if (f1110.saldo_centavos <= 0) {
    return {
      swept: false,
      sweep_amount_cents: 0,
      sweep_amount_formatted: "$0,00",
      message: "El fondo 1110 ya se encuentra en $0,00.",
    };
  }
  const monto = f1110.saldo_centavos;
  const init = await iniciarTrasladoWorker(
    client,
    {
      origin_fund_code: "1110",
      destination_fund_code: "1120",
      amount_cents: monto,
      reason: motivo || "Vaciado obligatorio de cierre 1110 -> 1120",
      evidence_reference: "BARRIDO_CIERRE_DIARIO",
    },
    usuario,
    `SWEEP_1110_J${journal_id}_${Date.now()}`,
    { allowInternalSweepOrRepo: true }
  );
  const conf = await confirmarTrasladoWorker(client, init.transfer_id, usuario);
  return {
    swept: true,
    transfer_id: conf.transfer_id,
    sweep_amount_cents: monto,
    sweep_amount_formatted: formatMoney(monto),
    origin_new_balance_cents: 0,
    destination_new_balance_cents: conf.destination_new_balance_cents,
  };
}

export async function ejecutarVaciado1160Worker(client, { journal_id, motivo }, usuario) {
  const f1160 = await obtenerFondoDetalleWorker(client, "1160");
  if (f1160.saldo_centavos <= 0) {
    return {
      swept: false,
      sweep_amount_cents: 0,
      sweep_amount_formatted: "$0,00",
      message: "El fondo 1160 ya se encuentra en $0,00.",
    };
  }
  const monto = f1160.saldo_centavos;
  const init = await iniciarTrasladoWorker(
    client,
    {
      origin_fund_code: "1160",
      destination_fund_code: "1130",
      amount_cents: monto,
      reason: motivo || "Barrido obligatorio de cierre PREPAY 1160 -> 1130",
      evidence_reference: "BARRIDO_PREPAY_CIERRE_DIARIO",
    },
    usuario,
    `SWEEP_1160_J${journal_id}_${Date.now()}`,
    { allowInternalSweepOrRepo: true }
  );
  const conf = await confirmarTrasladoWorker(client, init.transfer_id, usuario);
  return {
    swept: true,
    transfer_id: conf.transfer_id,
    sweep_amount_cents: monto,
    sweep_amount_formatted: formatMoney(monto),
    origin_new_balance_cents: 0,
    destination_new_balance_cents: conf.destination_new_balance_cents,
  };
}

export async function confirmarCierreWorker(client, payload, usuario) {
  const journalId = Number(payload.journal_id);
  const allowPending = Boolean(payload.allow_pending_transfers ?? payload.permitir_traslados_pendientes);
  const allow1110 = Boolean(payload.allow_nonzero_1110 ?? payload.permitir_saldo_1110);
  const obs = payload.observaciones || payload.observations || null;

  const val = await validarCierreWorker(client, journalId, {
    allow_pending_transfers: allowPending,
    allow_nonzero_1110: allow1110,
  });
  if (!val.ready_to_close) {
    throw new Error(`INVALID_INPUT: No se puede confirmar el cierre: ${val.blockers.join(" | ")}`);
  }

  const summary = await obtenerDisponibilidadResumenWorker(client);
  const closureId = crypto.randomUUID();
  const nowUtc = new Date().toISOString();

  const sweep1110Res = await client.execute({
    sql: `SELECT transfer_id, amount_cents FROM tesoreria_traslados
          WHERE journal_id = ? AND evidence_reference = 'BARRIDO_CIERRE_DIARIO' AND status = 'CONFIRMED'
          ORDER BY id DESC LIMIT 1;`,
    args: [journalId],
  });
  const sweepTransferId = sweep1110Res.rows.length > 0 ? String(sweep1110Res.rows[0].transfer_id) : null;
  const sweepAmountCents = sweep1110Res.rows.length > 0 ? Number(sweep1110Res.rows[0].amount_cents) : 0;

  const sweep1160Res = await client.execute({
    sql: `SELECT transfer_id, amount_cents FROM tesoreria_traslados
          WHERE journal_id = ? AND evidence_reference = 'BARRIDO_PREPAY_CIERRE_DIARIO' AND status = 'CONFIRMED'
          ORDER BY id DESC LIMIT 1;`,
    args: [journalId],
  });
  const prepaySweepTransferId = sweep1160Res.rows.length > 0 ? String(sweep1160Res.rows[0].transfer_id) : null;
  const prepaySweepAmountCents = sweep1160Res.rows.length > 0 ? Number(sweep1160Res.rows[0].amount_cents) : 0;

  await client.execute({
    sql: `INSERT INTO tesoreria_cierres (
            closure_id, journal_id, closed_by, closed_at,
            total_registered_cents, total_available_cents, total_reserved_cents,
            total_in_transit_cents, total_custody_cents,
            sweep_transfer_id, sweep_amount_cents, prepay_sweep_transfer_id, prepay_sweep_amount_cents,
            snapshot_json, observations, status
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CONFIRMED');`,
    args: [
      closureId,
      journalId,
      usuario,
      nowUtc,
      summary.total_consolidado.total_registrado_cents,
      summary.fondos_propios.total_disponible_cents,
      summary.fondos_propios.total_reservado_cents,
      summary.total_consolidado.total_en_transito_entre_cuentas_cents,
      summary.fondos_custodia_terceros.total_registrado_cents,
      sweepTransferId,
      sweepAmountCents,
      prepaySweepTransferId,
      prepaySweepAmountCents,
      JSON.stringify(summary),
      obs,
    ],
  });

  await cerrarJornadaWorker(client, { jornada_id: journalId, observaciones: obs }, usuario);
  return obtenerCierreTesoreriaWorker(client, journalId);
}

export async function obtenerCierreTesoreriaWorker(client, journalId) {
  const res = await client.execute({
    sql: "SELECT * FROM tesoreria_cierres WHERE journal_id = ?;",
    args: [Number(journalId)],
  });
  if (res.rows.length === 0) return null;
  const r = res.rows[0];
  let snap = {};
  try {
    snap = JSON.parse(String(r.snapshot_json || "{}"));
  } catch {}
  return {
    closure_id: String(r.closure_id),
    journal_id: Number(r.journal_id),
    closed_by: String(r.closed_by),
    closed_at: String(r.closed_at),
    total_registered_cents: Number(r.total_registered_cents),
    total_registered_formatted: formatMoney(Number(r.total_registered_cents)),
    total_available_cents: Number(r.total_available_cents),
    total_available_formatted: formatMoney(Number(r.total_available_cents)),
    total_reserved_cents: Number(r.total_reserved_cents),
    total_in_transit_cents: Number(r.total_in_transit_cents),
    total_custody_cents: Number(r.total_custody_cents),
    sweep_transfer_id: r.sweep_transfer_id ? String(r.sweep_transfer_id) : null,
    sweep_amount_cents: Number(r.sweep_amount_cents || 0),
    prepay_sweep_transfer_id: r.prepay_sweep_transfer_id ? String(r.prepay_sweep_transfer_id) : null,
    prepay_sweep_amount_cents: Number(r.prepay_sweep_amount_cents || 0),
    observations: r.observations ? String(r.observations) : null,
    status: String(r.status),
    snapshot: snap,
  };
}

export async function listarCierresTesoreriaWorker(client, { limite = 50 } = {}) {
  const res = await client.execute({
    sql: "SELECT journal_id FROM tesoreria_cierres ORDER BY id DESC LIMIT ?;",
    args: [Number(limite) || 50],
  });
  const out = [];
  for (const r of res.rows) {
    const c = await obtenerCierreTesoreriaWorker(client, Number(r.journal_id));
    if (c) out.push(c);
  }
  return out;
}

export async function abrirSiguienteJornadaWorker(client, { observaciones = null } = {}, usuario = "sistema") {
  const f1110 = await obtenerFondoDetalleWorker(client, "1110");
  const f1160 = await obtenerFondoDetalleWorker(client, "1160");
  if (f1110.saldo_centavos !== 0) {
    throw new Error("INVALID_INPUT: El fondo 1110 debe abrir en $0,00 al iniciar la siguiente jornada.");
  }
  if (f1160.saldo_centavos !== 0) {
    throw new Error("INVALID_INPUT: El fondo 1160 debe abrir en $0,00 al iniciar la siguiente jornada.");
  }
  return abrirJornadaWorker(client, { observaciones }, usuario);
}

// ============================================================================
// REVERSIONES
// ============================================================================

export async function revertirMovimientoWorker(
  client,
  { movement_id, motivo, referencia_evidencia = null, monto_parcial_centavos = null },
  usuario,
  idempotencyKey = null
) {
  if (!motivo || !String(motivo).trim()) {
    throw new Error("INVALID_INPUT: El motivo de la reversión es obligatorio.");
  }
  const mRes = await client.execute({
    sql: `SELECT m.*, f.codigo as fund_code, f.saldo_centavos as fund_balance
          FROM tesoreria_movimientos m
          JOIN tesoreria_fondos f ON m.fund_id = f.id
          WHERE m.movement_id = ?;`,
    args: [movement_id],
  });
  if (mRes.rows.length === 0) throw new Error(`NOT_FOUND: Movimiento ${movement_id} no encontrado.`);
  const orig = mRes.rows[0];
  const origAmt = Number(orig.amount_cents);
  const prevRev = Number(orig.reversed_amount_cents || 0);
  const avail = origAmt - prevRev;
  if (avail <= 0) throw new Error("INVALID_INPUT: El movimiento ya fue totalmente revertido.");

  const toRev = monto_parcial_centavos !== null && monto_parcial_centavos !== undefined ? Number(monto_parcial_centavos) : avail;
  if (!Number.isInteger(toRev) || toRev <= 0 || toRev > avail) {
    throw new Error("INVALID_INPUT: Importe a revertir inválido.");
  }

  const saldoActual = Number(orig.fund_balance);
  const nuevoSaldo = String(orig.movement_type) === "ENTRY" ? saldoActual - toRev : saldoActual + toRev;
  if (nuevoSaldo < 0) {
    throw new Error(`INVALID_INPUT: Saldo insuficiente en fondo ${orig.fund_code} para revertir entrada.`);
  }

  const jornada = await obtenerJornadaActualWorker(client);
  if (!jornada) throw new Error("INVALID_INPUT: Se requiere una jornada activa para registrar la reversión.");

  const reversalId = crypto.randomUUID();
  const counterMovId = crypto.randomUUID();
  const nowUtc = new Date().toISOString();
  const newAccumRev = prevRev + toRev;

  await client.batch(
    [
      {
        sql: `INSERT INTO tesoreria_movimientos (
                movement_id, idempotency_key, journal_id, fund_id, movement_type,
                amount_cents, currency, counterpart, reason, evidence_reference,
                created_by, terminal_id, status, occurred_at, recorded_at, is_reversal, reversal_of_id
              ) VALUES (?, ?, ?, ?, 'REVERSAL', ?, 'ARS', ?, ?, ?, ?, 'TERM-01', 'POSTED', ?, ?, 1, ?);`,
        args: [
          counterMovId,
          `cmov_${reversalId}`,
          jornada.id,
          Number(orig.fund_id),
          toRev,
          `Contra-asiento reversión de ${orig.movement_type} ${movement_id}`,
          String(motivo).trim(),
          referencia_evidencia,
          usuario,
          nowUtc,
          nowUtc,
          movement_id,
        ],
      },
      {
        sql: "UPDATE tesoreria_fondos SET saldo_centavos = ?, updated_at = ?, actualizado_por = ?, version = version + 1 WHERE id = ?;",
        args: [nuevoSaldo, nowUtc, usuario, Number(orig.fund_id)],
      },
      {
        sql: "UPDATE tesoreria_movimientos SET reversed_amount_cents = ? WHERE movement_id = ?;",
        args: [newAccumRev, movement_id],
      },
      {
        sql: `INSERT INTO tesoreria_reversiones (
                reversal_id, idempotency_key, target_type, target_id,
                original_amount_cents, reversed_amount_cents, counter_movement_id,
                reason, evidence_reference, authorized_by, created_at, status
              ) VALUES (?, ?, 'MOVEMENT', ?, ?, ?, ?, ?, ?, ?, ?, 'COMPLETED');`,
        args: [
          reversalId,
          idempotencyKey || null,
          movement_id,
          origAmt,
          toRev,
          counterMovId,
          String(motivo).trim(),
          referencia_evidencia,
          usuario,
          nowUtc,
        ],
      },
    ],
    "write"
  );

  return {
    reversal_id: reversalId,
    target_type: "MOVEMENT",
    target_id: movement_id,
    original_amount_cents: origAmt,
    reversed_amount_cents: toRev,
    total_reversed_cents: newAccumRev,
    counter_movement_id: counterMovId,
    fund_code: String(orig.fund_code),
    new_fund_balance_cents: nuevoSaldo,
    reason: String(motivo).trim(),
    authorized_by: usuario,
    created_at: nowUtc,
    status: "COMPLETED",
  };
}

export async function listarReversionesWorker(client, { tipo = null, limite = 50 } = {}) {
  let sql = "SELECT * FROM tesoreria_reversiones WHERE 1=1";
  const args = [];
  if (tipo) {
    sql += " AND target_type = ?";
    args.push(String(tipo).trim().toUpperCase());
  }
  sql += " ORDER BY id DESC LIMIT ?;";
  args.push(Number(limite) || 50);
  const res = await client.execute({ sql, args });
  return res.rows.map((r) => ({
    reversal_id: String(r.reversal_id),
    target_type: String(r.target_type),
    target_id: String(r.target_id),
    original_amount_cents: Number(r.original_amount_cents),
    reversed_amount_cents: Number(r.reversed_amount_cents),
    counter_movement_id: r.counter_movement_id ? String(r.counter_movement_id) : null,
    reason: String(r.reason),
    evidence_reference: r.evidence_reference ? String(r.evidence_reference) : null,
    authorized_by: String(r.authorized_by),
    created_at: String(r.created_at),
    status: String(r.status),
  }));
}

// ============================================================================
// AUDITORÍA HASH CHAIN Y OUTBOX
// ============================================================================

export async function consultarAuditoriaCadenaWorker(
  client,
  { tipo_entidad = null, entidad_id = null, tipo_evento = null, limite = 100 } = {}
) {
  let sql = "SELECT * FROM tesoreria_auditoria_eventos WHERE 1=1";
  const args = [];
  if (tipo_entidad) {
    sql += " AND aggregate_type = ?";
    args.push(String(tipo_entidad).trim());
  }
  if (entidad_id) {
    sql += " AND aggregate_id = ?";
    args.push(String(entidad_id).trim());
  }
  if (tipo_evento) {
    sql += " AND event_type = ?";
    args.push(String(tipo_evento).trim());
  }
  sql += " ORDER BY sequence_number DESC LIMIT ?;";
  args.push(Number(limite) || 100);
  const res = await client.execute({ sql, args });
  return res.rows.map((r) => {
    let payload = {};
    try {
      payload = JSON.parse(String(r.payload_json));
    } catch {}
    return {
      event_id: String(r.event_id),
      event_type: String(r.event_type),
      aggregate_type: String(r.aggregate_type),
      aggregate_id: String(r.aggregate_id),
      sequence_number: Number(r.sequence_number),
      occurred_at: String(r.occurred_at),
      recorded_at: String(r.recorded_at),
      user_id: String(r.user_id),
      terminal_id: String(r.terminal_id),
      payload,
      prev_canonical_hash: String(r.prev_canonical_hash),
      canonical_hash: String(r.canonical_hash),
    };
  });
}

export async function verificarAuditoriaCadenaWorker(client) {
  const res = await client.execute("SELECT * FROM tesoreria_auditoria_eventos ORDER BY sequence_number ASC;");
  let expectedPrev = GENESIS_HASH;
  const errors = [];
  for (let i = 0; i < res.rows.length; i++) {
    const r = res.rows[i];
    const seq = Number(r.sequence_number);
    if (seq !== i + 1) {
      errors.push(`Discontinuidad en secuencia ${seq}`);
    }
    if (String(r.prev_canonical_hash) !== expectedPrev) {
      errors.push(`Ruptura de enlace en secuencia ${seq}`);
    }
    expectedPrev = String(r.canonical_hash);
  }
  return {
    valid: errors.length === 0,
    total_events: res.rows.length,
    errors,
  };
}

export async function obtenerEstadoOutboxWorker(client) {
  const res = await client.execute(
    `SELECT status, COUNT(*) as cnt FROM tesoreria_outbox GROUP BY status;`
  );
  const counts = { PENDING: 0, PUBLISHED: 0, FAILED: 0, DEAD_LETTER: 0 };
  for (const r of res.rows) {
    counts[String(r.status)] = Number(r.cnt);
  }
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  return {
    total_events: total,
    pending: counts.PENDING || 0,
    published: counts.PUBLISHED || 0,
    failed: counts.FAILED || 0,
    dead_letter: counts.DEAD_LETTER || 0,
  };
}

export async function procesarOutboxWorker(client, { consumer_id = "TURSO_WORKER", max_batch = 50 } = {}) {
  const res = await client.execute({
    sql: "SELECT event_id FROM tesoreria_outbox WHERE status IN ('PENDING', 'FAILED') ORDER BY id ASC LIMIT ?;",
    args: [Number(max_batch) || 50],
  });
  const nowUtc = new Date().toISOString();
  let published = 0;
  for (const r of res.rows) {
    const evId = String(r.event_id);
    await client.batch(
      [
        {
          sql: "INSERT OR IGNORE INTO tesoreria_eventos_consumidos (consumer_id, event_id, consumed_at) VALUES (?, ?, ?);",
          args: [consumer_id, evId, nowUtc],
        },
        {
          sql: "UPDATE tesoreria_outbox SET status = 'PUBLISHED', published_at = ? WHERE event_id = ?;",
          args: [nowUtc, evId],
        },
      ],
      "write"
    );
    published++;
  }
  return {
    processed: res.rows.length,
    processed_count: res.rows.length,
    published,
    failed: 0,
    dead_letter: 0,
    dead_letter_count: 0,
    skipped_dedup: 0,
  };
}

export async function reprocesarOutboxWorker(client, eventId, usuario) {
  const nowUtc = new Date().toISOString();
  await client.execute({
    sql: "UPDATE tesoreria_outbox SET status = 'PENDING', retry_count = 0, last_error = NULL, scheduled_at = ? WHERE event_id = ?;",
    args: [nowUtc, eventId],
  });
  return {
    event_id: eventId,
    status: "PENDING",
    reprocessed_by: usuario,
    reprocessed_at: nowUtc,
  };
}

// ============================================================================
// COMMERCIAL BRIDGE
// ============================================================================

export async function obtenerBridgeStatusWorker(client) {
  try {
    const res = await client.execute(
      `SELECT
         COUNT(*) as total,
         COALESCE(SUM(CASE WHEN status = 'PENDING' AND (tesoreria_movement_id IS NULL OR (tesoreria_movement_id NOT LIKE 'PENDING_DEFINITION%' AND tesoreria_movement_id NOT LIKE 'UNSPECIFIED_PLATFORM%')) THEN 1 ELSE 0 END), 0) as pending,
         COALESCE(SUM(CASE WHEN status = 'PROCESSING' THEN 1 ELSE 0 END), 0) as processing,
         COALESCE(SUM(CASE WHEN status = 'PUBLISHED' AND (tesoreria_movement_id IS NULL OR (tesoreria_movement_id NOT LIKE 'PENDING_DEFINITION%' AND tesoreria_movement_id NOT LIKE 'UNSPECIFIED_PLATFORM%')) THEN 1 ELSE 0 END), 0) as published,
         COALESCE(SUM(CASE WHEN (tesoreria_movement_id LIKE 'PENDING_DEFINITION%' OR tesoreria_movement_id LIKE 'UNSPECIFIED_PLATFORM%') THEN 1 ELSE 0 END), 0) as held_pending_definition,
         COALESCE(SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END), 0) as failed,
         COALESCE(SUM(CASE WHEN status = 'DEAD_LETTER' THEN 1 ELSE 0 END), 0) as dead_letter,
         COALESCE(SUM(retry_count), 0) as retry_count_total
       FROM commercial_events_outbox;`
    );
    const r = res.rows[0];
    if (!r) {
      return { total: 0, pending: 0, processing: 0, published: 0, held_pending_definition: 0, failed: 0, dead_letter: 0, retry_count_total: 0 };
    }
    return {
      total: Number(r.total || 0),
      pending: Number(r.pending || 0),
      processing: Number(r.processing || 0),
      published: Number(r.published || 0),
      held_pending_definition: Number(r.held_pending_definition || 0),
      failed: Number(r.failed || 0),
      dead_letter: Number(r.dead_letter || 0),
      retry_count_total: Number(r.retry_count_total || 0),
    };
  } catch {
    return { total: 0, pending: 0, processing: 0, published: 0, held_pending_definition: 0, failed: 0, dead_letter: 0, retry_count_total: 0 };
  }
}

export async function obtenerSyncStatusBridgeWorker(client) {
  const st = await obtenerBridgeStatusWorker(client);
  const pendientes = st.pending + st.processing;
  const retenidas = st.held_pending_definition || 0;
  const fallidas = st.failed + st.dead_letter;
  const reqAtencion = fallidas > 0 || retenidas > 0;
  return {
    sincronizadas: st.published,
    pendientes,
    held_pending_definition: retenidas,
    requiere_atencion: reqAtencion,
    mensaje_cajero: fallidas > 0
      ? `Atención requerida: ${fallidas} operación(es) con error de sincronización. Avise a Administración.`
      : retenidas > 0
      ? `Atención: ${retenidas} operación(es) retenida(s) pendiente(s) de definición de plataforma.`
      : pendientes > 0
      ? `${pendientes} operación(es) en cola de sincronización.`
      : "Operaciones sincronizadas al día.",
    timestamp: new Date().toISOString(),
  };
}

export async function listarCommercialEventsWorker(
  client,
  { status = null, event_type = null, search = null, limit = 50, offset = 0 } = {}
) {
  try {
    let sql = "SELECT * FROM commercial_events_outbox WHERE 1=1";
    const args = [];
    if (status) {
      const sf = String(status).trim().toUpperCase();
      if (sf === "PENDING_DEFINITION" || sf === "HELD" || sf === "HELD_PENDING_DEFINITION") {
        sql += " AND (tesoreria_movement_id LIKE 'PENDING_DEFINITION%' OR tesoreria_movement_id LIKE 'UNSPECIFIED_PLATFORM%')";
      } else if (sf === "PUBLISHED") {
        sql += " AND status = 'PUBLISHED' AND (tesoreria_movement_id IS NULL OR (tesoreria_movement_id NOT LIKE 'PENDING_DEFINITION%' AND tesoreria_movement_id NOT LIKE 'UNSPECIFIED_PLATFORM%'))";
      } else {
        sql += " AND status = ?";
        args.push(sf);
      }
    }
    if (event_type) {
      sql += " AND event_type LIKE ?";
      args.push(`%${String(event_type).trim().toUpperCase()}%`);
    }
    if (search && String(search).trim()) {
      const q = `%${String(search).trim()}%`;
      sql += " AND (entity_id LIKE ? OR idempotency_key LIKE ? OR event_id LIKE ?)";
      args.push(q, q, q);
    }
    sql += " ORDER BY sequence_id DESC LIMIT ? OFFSET ?;";
    args.push(Number(limit) || 50, Number(offset) || 0);
    const res = await client.execute({ sql, args });
    const events = res.rows.map((r) => {
      let payload = {};
      try {
        payload = JSON.parse(String(r.payload || "{}"));
      } catch {}
      return {
        sequence_id: Number(r.sequence_id),
        event_id: String(r.event_id),
        event_type: String(r.event_type),
        entity_type: String(r.entity_type),
        entity_id: String(r.entity_id),
        action_type: String(r.action_type),
        idempotency_key: String(r.idempotency_key),
        payload_canonical_hash: String(r.payload_canonical_hash),
        payload,
        status: String(r.status),
        retry_count: Number(r.retry_count || 0),
        max_retries: Number(r.max_retries || 5),
        error_message: r.error_message ? String(r.error_message) : null,
        tesoreria_movement_id: r.tesoreria_movement_id ? String(r.tesoreria_movement_id) : null,
        created_at: String(r.created_at),
        updated_at: String(r.updated_at),
        published_at: r.published_at ? String(r.published_at) : null,
        reprocessed_by: r.reprocessed_by ? String(r.reprocessed_by) : null,
        reprocessed_at: r.reprocessed_at ? String(r.reprocessed_at) : null,
      };
    });
    return { total: events.length, limit: Number(limit) || 50, offset: Number(offset) || 0, events };
  } catch {
    return { total: 0, limit: Number(limit) || 50, offset: Number(offset) || 0, events: [] };
  }
}

export async function tesoreriaEstaActivaWorker(client, servicio = {}) {
  if (servicio && (servicio.requiere_pre_reserva === true || servicio.aplicar_tesoreria === true)) {
    return true;
  }
  try {
    const jRes = await client.execute(
      "SELECT id FROM tesoreria_jornadas WHERE estado = 'ABIERTA' LIMIT 1;"
    );
    if (jRes.rows.length > 0) return true;
    const fRes = await client.execute(
      "SELECT id FROM tesoreria_fondos WHERE estado_configuracion = 'CONFIGURADO' OR saldo_centavos > 0 LIMIT 1;"
    );
    return fRes.rows.length > 0;
  } catch {
    return false;
  }
}

export async function solicitarPreReservaPrepayWorker(
  client,
  { servicioDestino, montoCarga, montoAdicional, pagoEfvo, pagoTransf, fechaOp, idempotencyKey, usuario = "sistema" }
) {
  if (!idempotencyKey || !String(idempotencyKey).trim()) {
    throw new Error("INVALID_INPUT: La clave de idempotencia es obligatoria para la pre-reserva PREPAY.");
  }
  const keyLimpia = String(idempotencyKey).trim();
  const capitalCents = Math.round(Number(montoCarga || 0) * 100);
  const adicionalCents = Math.round(Number(montoAdicional || 0) * 100);
  const efvoCents = Math.round(Number(pagoEfvo || 0) * 100);
  const transfCents = Math.round(Number(pagoTransf || 0) * 100);
  const capTransfCents = Math.min(transfCents, capitalCents);
  const srvDest = String(servicioDestino || "PREPAY").trim().toUpperCase();

  const requestHash = crypto
    .createHash("sha256")
    .update(
      canonicalJsonDumps({
        servicio_destino: srvDest,
        monto_capital_cents: capitalCents,
        monto_adicional_cents: adicionalCents,
        pago_efvo_cents: efvoCents,
        pago_transf_cents: transfCents,
        idempotency_key: keyLimpia,
      }),
      "utf8"
    )
    .digest("hex");

  const existRes = await client.execute({
    sql: "SELECT * FROM tesoreria_prepay_coordinacion WHERE idempotency_key = ?;",
    args: [keyLimpia],
  });
  if (existRes.rows.length > 0) {
    const row = existRes.rows[0];
    if (String(row.request_hash) !== requestHash) {
      throw new Error(
        `CONFLICT: La clave '${keyLimpia}' ya fue utilizada con parámetros distintos en tesoreria_prepay_coordinacion.`
      );
    }
    return {
      success: true,
      es_reintento: true,
      coordination_id: String(row.coordination_id),
      estado: String(row.estado),
      capital_reservation_id: String(row.capital_reservation_id),
      fee_reservation_id: row.fee_reservation_id ? String(row.fee_reservation_id) : null,
      asume_cargo_diario: Boolean(Number(row.asume_cargo_diario)),
      monto_capital_cents: Number(row.monto_capital_cents),
      monto_cargo_cents: Number(row.monto_cargo_cents),
      idempotency_key: keyLimpia,
    };
  }

  let jornada = await obtenerJornadaActualWorker(client);
  if (!jornada) {
    jornada = await abrirJornadaWorker(
      client,
      { observaciones: "Apertura automática por pre-reserva PREPAY" },
      usuario || "sistema"
    );
  }
  const fechaJornada = fechaOp || jornada.fecha_operativa || new Date().toISOString().slice(0, 10);

  // Resolver titularidad del cargo diario de $350 (35000 centavos)
  let asumeCargo = false;
  const cargoRes = await client.execute({
    sql: "SELECT * FROM tesoreria_prepay_cargo_diario WHERE jornada_id = ?;",
    args: [jornada.id],
  });
  if (cargoRes.rows.length === 0) {
    asumeCargo = true;
  } else {
    const cRow = cargoRes.rows[0];
    const estCargo = String(cRow.estado);
    if (String(cRow.titular_idempotency_key) === keyLimpia) {
      asumeCargo = true;
    } else if (estCargo === "RELEASED") {
      asumeCargo = true;
    } else if (estCargo === "RESERVED") {
      // Si la otra operación ya fue confirmada en cargas_servicios, promover a CONFIRMED
      const commRes = await client.execute({
        sql: "SELECT id FROM cargas_servicios WHERE idempotency_key = ? AND anulado = 0 LIMIT 1;",
        args: [String(cRow.titular_idempotency_key)],
      });
      if (commRes.rows.length > 0) {
        const nowIso = new Date().toISOString();
        await client.execute({
          sql: `UPDATE tesoreria_prepay_cargo_diario
                SET estado = 'CONFIRMED', titular_commercial_ticket_id = ?, confirmed_at = ?, updated_at = ?, version = version + 1
                WHERE jornada_id = ? AND estado = 'RESERVED';`,
          args: [String(commRes.rows[0].id), nowIso, nowIso, jornada.id],
        });
      }
      asumeCargo = false;
    } else {
      asumeCargo = false;
    }
  }

  const cargoDiarioCents = asumeCargo ? 35000 : 0;
  const requerido1220 = capitalCents + cargoDiarioCents;

  const disp1220 = await obtenerDisponibilidadFondoWorker(client, "1220");
  if (disp1220.saldo_disponible_cents < requerido1220) {
    throw new Error(
      `UNPROCESSABLE: Saldo disponible insuficiente en 1220 (PREPAY digital): disponible ${disp1220.saldo_disponible_formatted}, requerido ${formatMoney(requerido1220)} (capital ${formatMoney(capitalCents)}${cargoDiarioCents > 0 ? " + cargo operativo diario $350,00" : ""}).`
    );
  }

  if (capTransfCents > 0) {
    const disp1120 = await obtenerDisponibilidadFondoWorker(client, "1120");
    if (disp1120.saldo_disponible_cents < capTransfCents) {
      throw new Error(
        `UNPROCESSABLE: Saldo disponible insuficiente en 1120 (Drugstore Acumulado): disponible ${disp1120.saldo_disponible_formatted} para compensar ${formatMoney(capTransfCents)} hacia 1130. Prohibido utilizar 1110 como respaldo.`
      );
    }
  }

  // Crear reserva síncrona de capital en 1220
  const resCap = await crearReservaWorker(
    client,
    {
      fund_code: "1220",
      amount_cents: capitalCents,
      reason: `Pre-reserva capital PREPAY ${srvDest}`,
      beneficiary: "PREPAY_OPERADOR",
      obligation_id: `PREPAY_CAP_${keyLimpia}`,
      evidence_reference: `PREPAY_SRV_${keyLimpia}`,
    },
    usuario || "sistema",
    `RES_CAP_${keyLimpia}`
  );

  // Si es transferencia, reservar también en 1120 para evitar sobreasignación concurrente
  if (capTransfCents > 0) {
    await crearReservaWorker(
      client,
      {
        fund_code: "1120",
        amount_cents: capTransfCents,
        reason: `Pre-reserva compensación física 1120 -> 1130 PREPAY ${srvDest}`,
        beneficiary: "FONDO_1130_PREPAY",
        obligation_id: `PREPAY_COMP1120_${keyLimpia}`,
        evidence_reference: `PREPAY_SRV_${keyLimpia}`,
      },
      usuario || "sistema",
      `RES_1120_${keyLimpia}`
    );
  }

  let feeResId = null;
  const nowUtc = new Date().toISOString();
  if (asumeCargo) {
    const resFee = await crearReservaWorker(
      client,
      {
        fund_code: "1220",
        amount_cents: 35000,
        reason: `Pre-reserva cargo diario PREPAY $350 (Jornada ${jornada.id})`,
        beneficiary: "GENARO_COSTO_OPERATIVO",
        obligation_id: `FEE_J${jornada.id}_${keyLimpia}`,
        evidence_reference: `PREPAY_FEE_${keyLimpia}`,
      },
      usuario || "sistema",
      `RES_FEE_${keyLimpia}`
    );
    feeResId = resFee.reservation_id;

    await client.execute({
      sql: `INSERT INTO tesoreria_prepay_cargo_diario (
              jornada_id, fecha_jornada, estado, titular_reserva_id,
              titular_idempotency_key, amount_cents, created_at, updated_at
            ) VALUES (?, ?, 'RESERVED', ?, ?, 35000, ?, ?)
            ON CONFLICT(jornada_id) DO UPDATE SET
              estado = 'RESERVED',
              titular_reserva_id = excluded.titular_reserva_id,
              titular_idempotency_key = excluded.titular_idempotency_key,
              updated_at = excluded.updated_at,
              version = version + 1;`,
      args: [jornada.id, fechaJornada, feeResId, keyLimpia, nowUtc, nowUtc],
    });
  }

  const coordinationId = crypto.randomUUID();
  await client.execute({
    sql: `INSERT INTO tesoreria_prepay_coordinacion (
            coordination_id, idempotency_key, request_hash, jornada_id,
            servicio_destino, plataforma, monto_capital_cents, monto_adicional_cents,
            pago_efvo_cents, pago_transf_cents, asume_cargo_diario, monto_cargo_cents,
            capital_reservation_id, fee_reservation_id, estado, actor, created_at, updated_at
          ) VALUES (?, ?, ?, ?, ?, 'PREPAY', ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?);`,
    args: [
      coordinationId,
      keyLimpia,
      requestHash,
      jornada.id,
      srvDest,
      capitalCents,
      adicionalCents,
      efvoCents,
      transfCents,
      asumeCargo ? 1 : 0,
      cargoDiarioCents,
      resCap.reservation_id,
      feeResId,
      usuario || "sistema",
      nowUtc,
      nowUtc,
    ],
  });

  return {
    success: true,
    es_reintento: false,
    coordination_id: coordinationId,
    estado: "ACTIVE",
    capital_reservation_id: resCap.reservation_id,
    fee_reservation_id: feeResId,
    asume_cargo_diario: asumeCargo,
    monto_capital_cents: capitalCents,
    monto_cargo_cents: cargoDiarioCents,
    idempotency_key: keyLimpia,
  };
}

export async function confirmarPreReservaComercialWorker(
  client,
  {
    idempotencyKey,
    cargaId,
    servicio,
    plataformaOrigen,
    servicioDestino,
    montoCarga,
    montoAdicional,
    totalCobrado,
    pagoEfvo,
    pagoTransf,
    fechaOp,
    usuario = "sistema",
  }
) {
  const keyLimpia = String(idempotencyKey).trim();
  const cRes = await client.execute({
    sql: "SELECT * FROM tesoreria_prepay_coordinacion WHERE idempotency_key = ?;",
    args: [keyLimpia],
  });
  if (cRes.rows.length === 0) {
    throw new Error(`NOT_FOUND: No existe coordinación PREPAY para la clave '${keyLimpia}'.`);
  }
  const cRow = cRes.rows[0];
  const nowUtc = new Date().toISOString();

  await client.execute({
    sql: `UPDATE tesoreria_prepay_coordinacion
          SET estado = 'COMMERCIAL_COMMITTED', commercial_ticket_id = ?, updated_at = ?
          WHERE coordination_id = ? AND estado = 'ACTIVE';`,
    args: [Number(cargaId), nowUtc, String(cRow.coordination_id)],
  });

  if (Number(cRow.asume_cargo_diario) === 1) {
    await client.execute({
      sql: `UPDATE tesoreria_prepay_cargo_diario
            SET estado = 'CONFIRMED', titular_commercial_ticket_id = ?, confirmed_at = ?, updated_at = ?, version = version + 1
            WHERE jornada_id = ? AND titular_idempotency_key = ?;`,
      args: [String(cargaId), nowUtc, nowUtc, Number(cRow.jornada_id), keyLimpia],
    });
  }

  await client.execute({
    sql: `INSERT OR IGNORE INTO commercial_events_outbox (
            event_id, event_type, entity_type, entity_id, action_type,
            idempotency_key, payload_canonical_hash, payload, status,
            created_at, updated_at
          ) VALUES (?, 'SERVICE_CONFIRMED', 'SERVICE_LOAD', ?, 'CREATE', ?, ?, ?, 'PENDING', ?, ?);`,
    args: [
      crypto.randomUUID(),
      String(cargaId),
      keyLimpia,
      `HASH_SRV_${cargaId}`,
      JSON.stringify({
        id: cargaId,
        servicio,
        plataforma_origen: plataformaOrigen,
        servicio_destino: servicioDestino,
        monto_carga: montoCarga,
        monto_adicional: montoAdicional,
        total_cobrado: totalCobrado,
        pago_efvo: pagoEfvo,
        pago_transf: pagoTransf,
        fecha_op: fechaOp,
        actor: usuario,
      }),
      nowUtc,
      nowUtc,
    ],
  });

  return {
    success: true,
    coordination_id: String(cRow.coordination_id),
    estado: "COMMERCIAL_COMMITTED",
    commercial_ticket_id: Number(cargaId),
  };
}

export async function cancelarPreReservaPrepayWorker(
  client,
  { idempotencyKey, motivo = "Cancelación pre-reserva PREPAY", usuario = "sistema" }
) {
  const keyLimpia = String(idempotencyKey).trim();
  const cRes = await client.execute({
    sql: "SELECT * FROM tesoreria_prepay_coordinacion WHERE idempotency_key = ?;",
    args: [keyLimpia],
  });
  if (cRes.rows.length === 0) {
    throw new Error(`NOT_FOUND: No existe coordinación PREPAY para '${keyLimpia}'.`);
  }
  const cRow = cRes.rows[0];
  const estado = String(cRow.estado);
  if (["COMMERCIAL_COMMITTED", "APPLICATION_PENDING", "FULLY_APPLIED"].includes(estado)) {
    throw new Error(`CONFLICT: Prohibido liberar una reserva PREPAY vinculada a una venta confirmada (${estado}).`);
  }
  if (estado === "RELEASED") {
    return { success: true, coordination_id: String(cRow.coordination_id), estado: "RELEASED" };
  }

  const capResId = String(cRow.capital_reservation_id);
  const feeResId = cRow.fee_reservation_id ? String(cRow.fee_reservation_id) : null;
  const mCap = Number(cRow.monto_capital_cents);

  await liberarReservaWorker(
    client,
    capResId,
    { amount_cents: mCap, reason: `Cancelación pre-reserva PREPAY: ${motivo}` },
    usuario,
    `REL_CAP_${keyLimpia}`
  ).catch(() => {});

  // Si tenía reserva de compensación en 1120, liberarla
  const r1120 = await client.execute({
    sql: "SELECT reservation_id, pending_amount_cents FROM tesoreria_reservas WHERE idempotency_key = ? AND status = 'ACTIVE';",
    args: [`RES_1120_${keyLimpia}`],
  });
  if (r1120.rows.length > 0) {
    await liberarReservaWorker(
      client,
      String(r1120.rows[0].reservation_id),
      { amount_cents: Number(r1120.rows[0].pending_amount_cents), reason: `Cancelación compensación 1120: ${motivo}` },
      usuario,
      `REL_1120_${keyLimpia}`
    ).catch(() => {});
  }

  const nowUtc = new Date().toISOString();
  if (Number(cRow.asume_cargo_diario) === 1 && feeResId) {
    await liberarReservaWorker(
      client,
      feeResId,
      { amount_cents: 35000, reason: `Liberación cargo diario PREPAY: ${motivo}` },
      usuario,
      `REL_FEE_${keyLimpia}`
    ).catch(() => {});

    await client.execute({
      sql: `UPDATE tesoreria_prepay_cargo_diario
            SET estado = 'RELEASED', released_at = ?, updated_at = ?, version = version + 1
            WHERE jornada_id = ? AND titular_idempotency_key = ?;`,
      args: [nowUtc, nowUtc, Number(cRow.jornada_id), keyLimpia],
    });
  }

  await client.execute({
    sql: `UPDATE tesoreria_prepay_coordinacion
          SET estado = 'RELEASED', error_detail = ?, updated_at = ?
          WHERE coordination_id = ?;`,
    args: [`Cancelado: ${motivo}`, nowUtc, String(cRow.coordination_id)],
  });

  return {
    success: true,
    coordination_id: String(cRow.coordination_id),
    estado: "RELEASED",
  };
}

export async function aplicarLiquidacionPrepayOutboxWorker(
  client,
  { idempotencyKey, usuario = "sistema", simulateOutboxError = false }
) {
  const keyLimpia = String(idempotencyKey).trim();
  const cRes = await client.execute({
    sql: "SELECT * FROM tesoreria_prepay_coordinacion WHERE idempotency_key = ?;",
    args: [keyLimpia],
  });
  if (cRes.rows.length === 0) {
    throw new Error(`NOT_FOUND: No existe coordinación PREPAY para '${keyLimpia}'.`);
  }
  const cRow = cRes.rows[0];
  if (String(cRow.estado) === "FULLY_APPLIED" && cRow.worker_movement_ids) {
    const ids = JSON.parse(String(cRow.worker_movement_ids));
    return {
      success: true,
      coordination_id: String(cRow.coordination_id),
      estado: "FULLY_APPLIED",
      movement_ids: ids,
      primary_movement_id: ids[0] || null,
    };
  }

  const nowUtc = new Date().toISOString();
  if (simulateOutboxError) {
    await client.batch(
      [
        {
          sql: `UPDATE tesoreria_prepay_coordinacion
                SET estado = 'RECONCILIATION_REQUIRED', error_detail = 'Fallo transitorio en procesamiento de outbox', updated_at = ?
                WHERE coordination_id = ?;`,
          args: [nowUtc, String(cRow.coordination_id)],
        },
        {
          sql: `UPDATE commercial_events_outbox
                SET status = 'FAILED', retry_count = retry_count + 1, error_message = 'Fallo transitorio simulado en outbox', updated_at = ?
                WHERE idempotency_key = ?;`,
          args: [nowUtc, keyLimpia],
        },
      ],
      "write"
    );
    return {
      success: false,
      coordination_id: String(cRow.coordination_id),
      estado: "RECONCILIATION_REQUIRED",
      movement_ids: [],
      primary_movement_id: null,
    };
  }

  const cargaId = Number(cRow.commercial_ticket_id);
  const tktRes = await client.execute({
    sql: "SELECT id, anulado, fecha_hora_utc FROM cargas_servicios WHERE id = ? OR idempotency_key = ? LIMIT 1;",
    args: [cargaId || -1, keyLimpia],
  });
  if (tktRes.rows.length === 0 || Number(tktRes.rows[0].anulado) === 1) {
    throw new Error(`UNPROCESSABLE: La carga comercial asociada a '${keyLimpia}' no existe o está anulada.`);
  }
  const realCargaId = Number(tktRes.rows[0].id);
  const capitalCents = Number(cRow.monto_capital_cents);
  const adicionalCents = Number(cRow.monto_adicional_cents);
  const efvoCents = Number(cRow.pago_efvo_cents);
  const transfCents = Number(cRow.pago_transf_cents);
  const capTransfCents = Math.min(transfCents, capitalCents);
  const capEfvoCents = Math.max(0, capitalCents - capTransfCents);
  const adicTransfCents = Math.max(0, transfCents - capitalCents);
  const adicEfvoCents = Math.max(0, adicionalCents - adicTransfCents);
  const srvDest = String(cRow.servicio_destino);
  const refEv = `CARGA_${realCargaId}`;
  const movements = [];

  // 1. Aplicar reserva de capital en 1220 y ejecutar EXIT
  await aplicarReservaWorker(
    client,
    String(cRow.capital_reservation_id),
    { amount_cents: capitalCents, reason: `Aplicación capital PREPAY #${realCargaId}` },
    usuario,
    `APP_CAP_${keyLimpia}`
  ).catch(() => {});

  const mCap1220 = await registrarMovimientoWorker(
    client,
    "EXIT",
    {
      codigo_fondo: "1220",
      monto_centavos: capitalCents,
      contrapartida: "PREPAY_RECARGA_CAPITAL",
      motivo: `Consumo saldo digital PREPAY por recarga ${srvDest} #${realCargaId}`,
      referencia_evidencia: refEv,
    },
    usuario,
    `${keyLimpia}_EXIT_1220`
  );
  movements.push(mCap1220.movement_id);

  // 2. Si asumió el cargo diario de $350, aplicar su reserva y ejecutar EXIT único
  if (Number(cRow.asume_cargo_diario) === 1 && cRow.fee_reservation_id) {
    await aplicarReservaWorker(
      client,
      String(cRow.fee_reservation_id),
      { amount_cents: 35000, reason: `Aplicación cargo diario PREPAY $350 #${realCargaId}` },
      usuario,
      `APP_FEE_${keyLimpia}`
    ).catch(() => {});

    const feeKey = `COSTO_OPERATIVO_PREPAY_J${cRow.jornada_id}`;
    const mFee = await registrarMovimientoWorker(
      client,
      "EXIT",
      {
        codigo_fondo: "1220",
        monto_centavos: 35000,
        contrapartida: "COSTO_OPERATIVO_PREPAY",
        motivo: `Cargo operativo diario fijo PREPAY ($350,00) — Jornada #${cRow.jornada_id}`,
        referencia_evidencia: `CARGO_DIARIO_PREPAY_J${cRow.jornada_id}`,
      },
      usuario,
      feeKey
    );
    movements.push(mFee.movement_id);

    await client.execute({
      sql: `UPDATE tesoreria_prepay_cargo_diario
            SET estado = 'APPLIED', applied_at = ?, updated_at = ?, version = version + 1
            WHERE jornada_id = ?;`,
      args: [nowUtc, nowUtc, Number(cRow.jornada_id)],
    });
  }

  // 3. Movimientos de ingreso y compensación
  if (capEfvoCents > 0) {
    const m1160 = await registrarMovimientoWorker(
      client,
      "ENTRY",
      {
        codigo_fondo: "1160",
        monto_centavos: capEfvoCents,
        contrapartida: "PREPAY_COBRO_CAPITAL_EFECTIVO",
        motivo: `Ingreso capital PREPAY en efectivo por recarga ${srvDest} #${realCargaId}`,
        referencia_evidencia: refEv,
      },
      usuario,
      `${keyLimpia}_ENTRY_1160`
    );
    movements.push(m1160.movement_id);
  }

  if (adicEfvoCents > 0) {
    const m1140 = await registrarMovimientoWorker(
      client,
      "ENTRY",
      {
        codigo_fondo: "1140",
        monto_centavos: adicEfvoCents,
        contrapartida: "SERVICIO_ADICIONAL_EFECTIVO",
        motivo: `Ingreso adicional en efectivo por recarga PREPAY ${srvDest} #${realCargaId}`,
        referencia_evidencia: refEv,
      },
      usuario,
      `${keyLimpia}_ENTRY_1140`
    );
    movements.push(m1140.movement_id);
  }

  if (transfCents > 0) {
    const m1210 = await registrarMovimientoWorker(
      client,
      "ENTRY",
      {
        codigo_fondo: "1210",
        monto_centavos: transfCents,
        contrapartida: "PREPAY_COBRO_TRANSFERENCIA_MP",
        motivo: `Cobro por transferencia Mercado Pago recarga PREPAY ${srvDest} #${realCargaId}`,
        referencia_evidencia: refEv,
      },
      usuario,
      `${keyLimpia}_ENTRY_1210`
    );
    movements.push(m1210.movement_id);
  }

  if (capTransfCents > 0) {
    const r1120 = await client.execute({
      sql: "SELECT reservation_id, pending_amount_cents FROM tesoreria_reservas WHERE idempotency_key = ? AND status = 'ACTIVE';",
      args: [`RES_1120_${keyLimpia}`],
    });
    if (r1120.rows.length > 0) {
      await aplicarReservaWorker(
        client,
        String(r1120.rows[0].reservation_id),
        { amount_cents: Number(r1120.rows[0].pending_amount_cents), reason: `Aplicación reserva compensación 1120 -> 1130 #${realCargaId}` },
        usuario,
        `APP_1120_${keyLimpia}`
      ).catch(() => {});
    }

    const tr = await iniciarTrasladoWorker(
      client,
      {
        origin_fund_code: "1120",
        destination_fund_code: "1130",
        amount_cents: capTransfCents,
        reason: `Compensación inmediata 1120 -> 1130 por capital PREPAY cobrado por transferencia (#${realCargaId})`,
        evidence_reference: refEv,
      },
      usuario,
      `${keyLimpia}_COMP_INIT_1120_1130`,
      { allowInternalSweepOrRepo: true }
    );
    await confirmarTrasladoWorker(
      client,
      tr.transfer_id,
      usuario,
      `${keyLimpia}_COMP_CONF_1120_1130`
    );
    movements.push(tr.exit_movement_id);
  }

  await client.batch(
    [
      {
        sql: `UPDATE tesoreria_prepay_coordinacion
              SET estado = 'FULLY_APPLIED', commercial_ticket_id = ?, worker_movement_ids = ?, error_detail = NULL, updated_at = ?
              WHERE coordination_id = ?;`,
        args: [realCargaId, JSON.stringify(movements), nowUtc, String(cRow.coordination_id)],
      },
      {
        sql: `UPDATE commercial_events_outbox
              SET status = 'PUBLISHED', tesoreria_movement_id = ?, error_message = NULL, updated_at = ?, published_at = ?
              WHERE idempotency_key = ?;`,
        args: [movements[0] || null, nowUtc, nowUtc, keyLimpia],
      },
    ],
    "write"
  );

  return {
    success: true,
    coordination_id: String(cRow.coordination_id),
    estado: "FULLY_APPLIED",
    movement_ids: movements,
    primary_movement_id: movements[0] || null,
  };
}

export async function reconciliarCoordinacionesPrepayWorker(client, { usuario = "reconciliador" } = {}) {
  let applied = 0;
  let released = 0;
  const res = await client.execute(
    `SELECT * FROM tesoreria_prepay_coordinacion
     WHERE estado IN ('ACTIVE', 'COMMERCIAL_COMMITTED', 'APPLICATION_PENDING', 'RECONCILIATION_REQUIRED')
     ORDER BY id ASC;`
  );
  for (const r of res.rows) {
    const key = String(r.idempotency_key);
    const tktId = Number(r.commercial_ticket_id || -1);
    const tktRes = await client.execute({
      sql: "SELECT id, anulado FROM cargas_servicios WHERE (id = ? OR idempotency_key = ?) AND anulado = 0 LIMIT 1;",
      args: [tktId, key],
    });
    if (tktRes.rows.length > 0) {
      const realId = Number(tktRes.rows[0].id);
      if (String(r.estado) === "ACTIVE") {
        await confirmarPreReservaComercialWorker(client, {
          idempotencyKey: key,
          cargaId: realId,
          servicio: String(r.servicio_destino),
          plataformaOrigen: "PREPAY",
          servicioDestino: String(r.servicio_destino),
          montoCarga: Number(r.monto_capital_cents) / 100,
          montoAdicional: Number(r.monto_adicional_cents) / 100,
          totalCobrado: (Number(r.monto_capital_cents) + Number(r.monto_adicional_cents)) / 100,
          pagoEfvo: Number(r.pago_efvo_cents) / 100,
          pagoTransf: Number(r.pago_transf_cents) / 100,
          fechaOp: new Date().toISOString().slice(0, 10),
          usuario,
        });
      }
      await aplicarLiquidacionPrepayOutboxWorker(client, { idempotencyKey: key, usuario });
      applied++;
    } else if (String(r.estado) === "ACTIVE") {
      await cancelarPreReservaPrepayWorker(client, {
        idempotencyKey: key,
        motivo: "Reconciliación automática: operación comercial no confirmada tras reserva.",
        usuario,
      });
      released++;
    }
  }
  return { applied, released, total_inspected: res.rows.length };
}

export async function ejecutarCircuitoServicioTesoreriaWorker(
  client,
  { cargaId, plataformaOrigen, servicioDestino, montoCarga, montoAdicional, pagoEfvo, pagoTransf, fechaOp },
  usuario = "sistema",
  idempotencyKey = null
) {
  const plat = String(plataformaOrigen || "").trim().toUpperCase();
  if (!plat) return null;

  let jornada = await obtenerJornadaActualWorker(client);
  if (!jornada) {
    jornada = await abrirJornadaWorker(
      client,
      { observaciones: `Apertura automática por operación de servicio (${plat})` },
      usuario || "sistema"
    );
  }

  const capitalCents = Math.round(Number(montoCarga || 0) * 100);
  const adicionalCents = Math.round(Number(montoAdicional || 0) * 100);
  const transfCents = Math.round(Number(pagoTransf || 0) * 100);
  const capTransfCents = Math.min(transfCents, capitalCents);
  const capEfvoCents = Math.max(0, capitalCents - capTransfCents);
  const adicTransfCents = Math.max(0, transfCents - capitalCents);
  const adicEfvoCents = Math.max(0, adicionalCents - adicTransfCents);
  const baseKey = idempotencyKey || `SRV_${cargaId}`;
  const refEv = `CARGA_${cargaId}`;
  const movements = [];

  if (plat === "PREPAY") {
    const feeKey = `COSTO_OPERATIVO_PREPAY_${fechaOp}`;
    const feeRef = `CARGO_DIARIO_PREPAY_${fechaOp}`;
    const feeRes = await client.execute({
      sql: "SELECT movement_id FROM tesoreria_movimientos WHERE idempotency_key = ? OR evidence_reference = ? LIMIT 1;",
      args: [feeKey, feeRef],
    });
    if (feeRes.rows.length === 0) {
      const mFee = await registrarMovimientoWorker(
        client,
        "EXIT",
        {
          codigo_fondo: "1220",
          monto_centavos: 35000,
          contrapartida: "COSTO_OPERATIVO_PREPAY",
          motivo: `Cargo operativo diario fijo PREPAY ($350,00) — Jornada ${fechaOp}`,
          referencia_evidencia: feeRef,
        },
        usuario || "sistema",
        feeKey
      );
      movements.push(mFee.movement_id);
    }

    if (capitalCents > 0) {
      const mCap1220 = await registrarMovimientoWorker(
        client,
        "EXIT",
        {
          codigo_fondo: "1220",
          monto_centavos: capitalCents,
          contrapartida: "PREPAY_RECARGA_CAPITAL",
          motivo: `Consumo saldo digital PREPAY por recarga ${servicioDestino} #${cargaId}`,
          referencia_evidencia: refEv,
        },
        usuario || "sistema",
        `${baseKey}_EXIT_1220`
      );
      movements.push(mCap1220.movement_id);
    }

    if (capEfvoCents > 0) {
      const m1160 = await registrarMovimientoWorker(
        client,
        "ENTRY",
        {
          codigo_fondo: "1160",
          monto_centavos: capEfvoCents,
          contrapartida: "PREPAY_COBRO_CAPITAL_EFECTIVO",
          motivo: `Ingreso capital PREPAY en efectivo por recarga ${servicioDestino} #${cargaId}`,
          referencia_evidencia: refEv,
        },
        usuario || "sistema",
        `${baseKey}_ENTRY_1160`
      );
      movements.push(m1160.movement_id);
    }

    if (adicEfvoCents > 0) {
      const m1140 = await registrarMovimientoWorker(
        client,
        "ENTRY",
        {
          codigo_fondo: "1140",
          monto_centavos: adicEfvoCents,
          contrapartida: "SERVICIO_ADICIONAL_EFECTIVO",
          motivo: `Ingreso adicional en efectivo por recarga PREPAY ${servicioDestino} #${cargaId}`,
          referencia_evidencia: refEv,
        },
        usuario || "sistema",
        `${baseKey}_ENTRY_1140`
      );
      movements.push(m1140.movement_id);
    }

    if (transfCents > 0) {
      const m1210 = await registrarMovimientoWorker(
        client,
        "ENTRY",
        {
          codigo_fondo: "1210",
          monto_centavos: transfCents,
          contrapartida: "PREPAY_COBRO_TRANSFERENCIA_MP",
          motivo: `Cobro por transferencia Mercado Pago recarga PREPAY ${servicioDestino} #${cargaId}`,
          referencia_evidencia: refEv,
        },
        usuario || "sistema",
        `${baseKey}_ENTRY_1210`
      );
      movements.push(m1210.movement_id);
    }

    if (capTransfCents > 0) {
      const tr = await iniciarTrasladoWorker(
        client,
        {
          origin_fund_code: "1120",
          destination_fund_code: "1130",
          amount_cents: capTransfCents,
          reason: `Compensación inmediata 1120 -> 1130 por capital PREPAY cobrado por transferencia (#${cargaId})`,
          evidence_reference: refEv,
        },
        usuario || "sistema",
        `${baseKey}_COMP_INIT_1120_1130`,
        { allowInternalSweepOrRepo: true }
      );
      await confirmarTrasladoWorker(
        client,
        tr.transfer_id,
        usuario || "sistema",
        `${baseKey}_COMP_CONF_1120_1130`
      );
      movements.push(tr.exit_movement_id);
    }
  } else if (plat === "CLARO COMERCIOS") {
    const disp1230 = await obtenerDisponibilidadFondoWorker(client, "1230");
    if (disp1230.saldo_disponible_cents >= capitalCents && capitalCents > 0) {
      const m1230 = await registrarMovimientoWorker(
        client,
        "EXIT",
        {
          codigo_fondo: "1230",
          monto_centavos: capitalCents,
          contrapartida: "CLARO_RECARGA_CAPITAL",
          motivo: `Consumo saldo digital Claro Comercios por recarga #${cargaId}`,
          referencia_evidencia: refEv,
        },
        usuario || "sistema",
        `${baseKey}_EXIT_1230`
      );
      movements.push(m1230.movement_id);
    }
    if (capEfvoCents > 0) {
      const m1150 = await registrarMovimientoWorker(
        client,
        "ENTRY",
        {
          codigo_fondo: "1150",
          monto_centavos: capEfvoCents,
          contrapartida: "CLARO_COBRO_CAPITAL_EFECTIVO",
          motivo: `Ingreso capital Claro en efectivo recarga #${cargaId}`,
          referencia_evidencia: refEv,
        },
        usuario || "sistema",
        `${baseKey}_ENTRY_1150`
      );
      movements.push(m1150.movement_id);
    }
    if (adicEfvoCents > 0) {
      const m1140 = await registrarMovimientoWorker(
        client,
        "ENTRY",
        {
          codigo_fondo: "1140",
          monto_centavos: adicEfvoCents,
          contrapartida: "SERVICIO_ADICIONAL_EFECTIVO",
          motivo: `Ingreso adicional Claro en efectivo recarga #${cargaId}`,
          referencia_evidencia: refEv,
        },
        usuario || "sistema",
        `${baseKey}_ENTRY_1140`
      );
      movements.push(m1140.movement_id);
    }
    if (transfCents > 0) {
      const m1210 = await registrarMovimientoWorker(
        client,
        "ENTRY",
        {
          codigo_fondo: "1210",
          monto_centavos: transfCents,
          contrapartida: "CLARO_COBRO_TRANSFERENCIA_MP",
          motivo: `Cobro transferencia recarga Claro #${cargaId}`,
          referencia_evidencia: refEv,
        },
        usuario || "sistema",
        `${baseKey}_ENTRY_1210`
      );
      movements.push(m1210.movement_id);
    }
  } else if (plat === "MERCADO PAGO") {
    const disp1210 = await obtenerDisponibilidadFondoWorker(client, "1210");
    if (disp1210.saldo_disponible_cents >= capitalCents && capitalCents > 0) {
      const mExit1210 = await registrarMovimientoWorker(
        client,
        "EXIT",
        {
          codigo_fondo: "1210",
          monto_centavos: capitalCents,
          contrapartida: "MP_SERVICIO_EGRESO_CAPITAL",
          motivo: `Egreso capital Mercado Pago por servicio ${servicioDestino} #${cargaId}`,
          referencia_evidencia: refEv,
        },
        usuario || "sistema",
        `${baseKey}_EXIT_1210`
      );
      movements.push(mExit1210.movement_id);
    }
    if (capEfvoCents > 0) {
      const m1110 = await registrarMovimientoWorker(
        client,
        "ENTRY",
        {
          codigo_fondo: "1110",
          monto_centavos: capEfvoCents,
          contrapartida: "MP_SERVICIO_COBRO_CAPITAL_EFECTIVO",
          motivo: `Ingreso capital servicio Mercado Pago (${servicioDestino}) en efectivo #${cargaId}`,
          referencia_evidencia: refEv,
        },
        usuario || "sistema",
        `${baseKey}_ENTRY_1110`
      );
      movements.push(m1110.movement_id);
    }
    if (adicEfvoCents > 0) {
      const m1140 = await registrarMovimientoWorker(
        client,
        "ENTRY",
        {
          codigo_fondo: "1140",
          monto_centavos: adicEfvoCents,
          contrapartida: "SERVICIO_ADICIONAL_EFECTIVO",
          motivo: `Ingreso adicional servicio Mercado Pago (${servicioDestino}) en efectivo #${cargaId}`,
          referencia_evidencia: refEv,
        },
        usuario || "sistema",
        `${baseKey}_ENTRY_1140`
      );
      movements.push(m1140.movement_id);
    }
    if (transfCents > 0) {
      const m1210 = await registrarMovimientoWorker(
        client,
        "ENTRY",
        {
          codigo_fondo: "1210",
          monto_centavos: transfCents,
          contrapartida: "MP_SERVICIO_COBRO_TRANSFERENCIA",
          motivo: `Cobro transferencia servicio Mercado Pago (${servicioDestino}) #${cargaId}`,
          referencia_evidencia: refEv,
        },
        usuario || "sistema",
        `${baseKey}_ENTRY_1210`
      );
      movements.push(m1210.movement_id);
    }
  }

  return movements[0] || null;
}

export async function triggerDrainCommercialBridgeWorker(client) {
  // Arquitectura CENTRAL: La contabilización es responsabilidad exclusiva del motor central Python.
  // El Worker comercial de Cloudflare NO finge drenaje marcando 'PUBLISHED' sin asientos contables.
  try {
    await reconciliarCoordinacionesPrepayWorker(client, { usuario: "bridge_drain" });
  } catch {}
  const st = await obtenerBridgeStatusWorker(client);
  return {
    published: st.published,
    failed: st.failed,
    dead_letter: st.dead_letter,
    pending: st.pending,
    message: `Estado actual del bridge outbox: ${st.pending} pendientes de contabilización central, ${st.published} publicados, ${st.failed} con fallo.`,
  };
}

export async function reprocesarCommercialEventWorker(client, eventId, usuario) {
  const nowUtc = new Date().toISOString();
  // Al reencolar, se restablece a PENDING para que el motor central lo contabilice genuinamente
  await client.execute({
    sql: `UPDATE commercial_events_outbox
          SET status = 'PENDING', retry_count = 0, error_message = NULL, reprocessed_by = ?, reprocessed_at = ?, updated_at = ?
          WHERE event_id = ?;`,
    args: [usuario, nowUtc, nowUtc, eventId],
  });
  const st = await obtenerBridgeStatusWorker(client);
  return {
    event_id: eventId,
    status: "PENDING",
    tesoreria_movement_id: null,
    error_message: null,
    authorized_by: usuario,
    reprocessed_at: nowUtc,
    drain_stats: { published: st.published, failed: st.failed, dead_letter: st.dead_letter, pending: st.pending },
  };
}

// ============================================================================
// CUENTA CORRIENTE PREPAY / DAVID
// ============================================================================

export async function obtenerEstadoCCDavidWorker(client, { fecha_corte = null } = {}) {
  const f1220 = await obtenerFondoDetalleWorker(client, "1220");
  const f1130 = await obtenerFondoDetalleWorker(client, "1130");
  const obRes = await client.execute(
    "SELECT saldo_inicial_cents FROM tesoreria_obligaciones_apertura WHERE codigo = 'DAVID';"
  );
  const saldoInicialApertura = Number(obRes.rows[0]?.saldo_inicial_cents || 0);

  let sql = "SELECT * FROM tesoreria_cuenta_corriente_david";
  const args = [];
  if (fecha_corte) {
    sql += " WHERE fecha_referencia <= ?";
    args.push(String(fecha_corte).trim());
  }
  sql += " ORDER BY id ASC;";
  const movsRes = await client.execute({ sql, args });

  let totalReconocimientos = saldoInicialApertura;
  let totalAcreditaciones = 0;
  let totalPagos = 0;

  const historial = movsRes.rows.map((r) => {
    const tipo = String(r.operation_type);
    const monto = Number(r.amount_cents);
    if (tipo === "RECONOCIMIENTO_DEUDA_HISTORICA") totalReconocimientos += monto;
    else if (tipo === "ACREDITACION_SALDO_DIGITAL") totalAcreditaciones += monto;
    else if (tipo === "PAGO_EFECTIVO_DAVID") totalPagos += monto;

    const tipoMovUi =
      tipo === "ACREDITACION_SALDO_DIGITAL"
        ? "ACREDITACION_SALDO"
        : tipo === "PAGO_EFECTIVO_DAVID"
        ? "PAGO_EFECTIVO"
        : "RECONOCIMIENTO_DEUDA_HISTORICA";
    const fondoAfectado =
      tipo === "ACREDITACION_SALDO_DIGITAL"
        ? "1220"
        : tipo === "PAGO_EFECTIVO_DAVID"
        ? "1130"
        : null;
    const saldoDeudaRes = Number(r.saldo_deuda_resultante_cents);

    return {
      id: Number(r.id || 0),
      operation_id: String(r.operation_id),
      idempotency_key: String(r.idempotency_key),
      journal_id: Number(r.journal_id),
      operation_type: tipo,
      tipo_movimiento: tipoMovUi,
      amount_cents: monto,
      amount_formatted: formatMoney(monto),
      monto_cents: monto,
      monto_formatted: formatMoney(monto),
      fondo_afectado: fondoAfectado,
      saldo_deuda_anterior_cents: Number(r.saldo_deuda_anterior_cents),
      saldo_deuda_anterior_formatted: formatMoney(Number(r.saldo_deuda_anterior_cents)),
      saldo_deuda_resultante_cents: saldoDeudaRes,
      saldo_deuda_resultante_formatted: formatMoney(saldoDeudaRes),
      saldo_obligacion_resultante_cents: saldoDeudaRes,
      saldo_obligacion_resultante_formatted: formatMoney(saldoDeudaRes),
      saldo_1220_anterior_cents: Number(r.saldo_1220_anterior_cents),
      saldo_1220_resultante_cents: Number(r.saldo_1220_resultante_cents),
      saldo_1130_anterior_cents: Number(r.saldo_1130_anterior_cents),
      saldo_1130_resultante_cents: Number(r.saldo_1130_resultante_cents),
      tesoreria_movement_id: r.tesoreria_movement_id ? String(r.tesoreria_movement_id) : null,
      movement_id: r.tesoreria_movement_id ? String(r.tesoreria_movement_id) : null,
      referencia: String(r.referencia),
      referencia_externa: String(r.referencia),
      motivo: String(r.motivo),
      fecha_referencia: String(r.fecha_referencia),
      creado_por: String(r.creado_por),
      actor: String(r.creado_por),
      creado_en_utc: String(r.creado_en_utc),
    };
  });

  const obligacionPendiente = totalReconocimientos + totalAcreditaciones - totalPagos;
  const coberturaNeta = f1130.saldo_centavos - obligacionPendiente;
  let estadoCobertura = "SIN_DEUDA_PENDIENTE";
  if (obligacionPendiente > 0) {
    estadoCobertura = f1130.saldo_centavos >= obligacionPendiente ? "CUBIERTA" : "DESCUBIERTA";
  }

  const historialReverso = [...historial].reverse();

  return {
    obligacion_pendiente_david_cents: obligacionPendiente,
    obligacion_pendiente_david_formatted: formatMoney(obligacionPendiente),
    obligacion_pendiente_cents: obligacionPendiente,
    obligacion_pendiente_formatted: formatMoney(obligacionPendiente),
    total_acreditaciones_cents: totalAcreditaciones,
    total_acreditaciones_formatted: formatMoney(totalAcreditaciones),
    total_pagos_cents: totalPagos,
    total_pagos_formatted: formatMoney(totalPagos),
    total_deuda_historica_cents: totalReconocimientos,
    total_deuda_historica_formatted: formatMoney(totalReconocimientos),
    saldo_1220_prepay_digital_cents: f1220.saldo_centavos,
    saldo_1220_prepay_digital_formatted: formatMoney(f1220.saldo_centavos),
    saldo_1130_prepay_efectivo_cents: f1130.saldo_centavos,
    saldo_1130_prepay_efectivo_formatted: formatMoney(f1130.saldo_centavos),
    saldo_disponible_1130_cents: f1130.saldo_centavos,
    saldo_disponible_1130_formatted: formatMoney(f1130.saldo_centavos),
    diferencia_cobertura_1130_vs_deuda_cents: coberturaNeta,
    diferencia_cobertura_1130_vs_deuda_formatted: formatMoney(coberturaNeta),
    estado_cobertura: estadoCobertura,
    totales_historicos: {
      reconocimientos_deuda_historica_cents: totalReconocimientos,
      reconocimientos_deuda_historica_formatted: formatMoney(totalReconocimientos),
      acreditaciones_recibidas_cents: totalAcreditaciones,
      acreditaciones_recibidas_formatted: formatMoney(totalAcreditaciones),
      pagos_efectuados_cents: totalPagos,
      pagos_efectuados_formatted: formatMoney(totalPagos),
      cantidad_operaciones: historial.length,
    },
    cantidad_movimientos: historial.length,
    fecha_corte_aplicada: fecha_corte,
    historial: historialReverso,
    movimientos: historialReverso,
  };
}

export async function registrarAcreditacionDavidWorker(client, payload, usuario, idempotencyKey) {
  if (!idempotencyKey) throw new Error("INVALID_INPUT: Se requiere cabecera Idempotency-Key.");
  const monto = Number(payload.monto_centavos);
  const referencia = String(payload.referencia || "").trim();
  const motivo = String(payload.motivo || `Acreditación saldo digital PREPAY ref. ${referencia}`).trim();
  const fechaRef = String(payload.fecha_referencia || new Date().toISOString().slice(0, 10)).trim();

  if (!Number.isInteger(monto) || monto <= 0 || !referencia) {
    throw new Error("INVALID_INPUT: Monto positivo y referencia son obligatorios.");
  }

  let jornada = await obtenerJornadaActualWorker(client);
  if (!jornada) {
    jornada = await abrirJornadaWorker(client, { observaciones: "Apertura automática por operación CC David" }, usuario);
  }

  const estadoPrevio = await obtenerEstadoCCDavidWorker(client);
  const mov = await registrarMovimientoWorker(
    client,
    "ENTRY",
    {
      codigo_fondo: "1220",
      monto_centavos: monto,
      contrapartida: "ACREDITACION_MAYORISTA_DAVID_PREPAY",
      motivo: `${motivo} (Ref: ${referencia})`,
      referencia_evidencia: `DAVID_ACRED_${referencia}`,
    },
    usuario,
    `${idempotencyKey}_MOV_1220`
  );

  const opId = `CCD-${crypto.randomUUID().slice(0, 12).toUpperCase()}`;
  const nowUtc = new Date().toISOString();
  const deudaAnterior = estadoPrevio.obligacion_pendiente_david_cents;
  const deudaResultante = deudaAnterior + monto;

  await client.execute({
    sql: `INSERT INTO tesoreria_cuenta_corriente_david (
            operation_id, idempotency_key, journal_id, operation_type, amount_cents,
            saldo_deuda_anterior_cents, saldo_deuda_resultante_cents,
            saldo_1220_anterior_cents, saldo_1220_resultante_cents,
            saldo_1130_anterior_cents, saldo_1130_resultante_cents,
            tesoreria_movement_id, referencia, motivo, fecha_referencia, creado_por, creado_en_utc
          ) VALUES (?, ?, ?, 'ACREDITACION_SALDO_DIGITAL', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);`,
    args: [
      opId,
      idempotencyKey,
      jornada.id,
      monto,
      deudaAnterior,
      deudaResultante,
      estadoPrevio.saldo_1220_prepay_digital_cents,
      mov.saldo_posterior_centavos,
      estadoPrevio.saldo_1130_prepay_efectivo_cents,
      estadoPrevio.saldo_1130_prepay_efectivo_cents,
      mov.movement_id,
      referencia,
      motivo,
      fechaRef,
      usuario,
      nowUtc,
    ],
  });

  return {
    operation_id: opId,
    idempotency_key: idempotencyKey,
    operation_type: "ACREDITACION_SALDO_DIGITAL",
    amount_cents: monto,
    amount_formatted: formatMoney(monto),
    saldo_deuda_anterior_cents: deudaAnterior,
    saldo_deuda_resultante_cents: deudaResultante,
    saldo_1220_anterior_cents: estadoPrevio.saldo_1220_prepay_digital_cents,
    saldo_1220_resultante_cents: mov.saldo_posterior_centavos,
    saldo_1130_anterior_cents: estadoPrevio.saldo_1130_prepay_efectivo_cents,
    saldo_1130_resultante_cents: estadoPrevio.saldo_1130_prepay_efectivo_cents,
    tesoreria_movement_id: mov.movement_id,
    referencia,
    motivo,
    fecha_referencia: fechaRef,
    creado_por: usuario,
    creado_en_utc: nowUtc,
  };
}

export async function registrarPagoDavidWorker(client, payload, usuario, idempotencyKey) {
  if (!idempotencyKey) throw new Error("INVALID_INPUT: Se requiere cabecera Idempotency-Key.");
  const monto = Number(payload.monto_centavos);
  const referencia = String(payload.referencia || "").trim();
  const motivo = String(payload.motivo || `Pago en efectivo a David ref. ${referencia}`).trim();
  const fechaRef = String(payload.fecha_referencia || new Date().toISOString().slice(0, 10)).trim();

  if (!Number.isInteger(monto) || monto <= 0 || !referencia) {
    throw new Error("INVALID_INPUT: Monto positivo y referencia son obligatorios.");
  }

  let jornada = await obtenerJornadaActualWorker(client);
  if (!jornada) {
    jornada = await abrirJornadaWorker(client, { observaciones: "Apertura automática por pago a David" }, usuario);
  }

  const estadoPrevio = await obtenerEstadoCCDavidWorker(client);
  if (monto > estadoPrevio.obligacion_pendiente_david_cents) {
    throw new Error(
      `INVALID_INPUT: El pago (${formatMoney(monto)}) supera la deuda pendiente con David (${estadoPrevio.obligacion_pendiente_david_formatted}).`
    );
  }

  const mov = await registrarMovimientoWorker(
    client,
    "EXIT",
    {
      codigo_fondo: "1130",
      monto_centavos: monto,
      contrapartida: "PAGO_PROVEEDOR_DAVID_PREPAY",
      motivo: `${motivo} (Ref: ${referencia})`,
      referencia_evidencia: `DAVID_PAGO_${referencia}`,
    },
    usuario,
    `${idempotencyKey}_MOV_1130`
  );

  const opId = `CCD-${crypto.randomUUID().slice(0, 12).toUpperCase()}`;
  const nowUtc = new Date().toISOString();
  const deudaAnterior = estadoPrevio.obligacion_pendiente_david_cents;
  const deudaResultante = deudaAnterior - monto;

  await client.execute({
    sql: `INSERT INTO tesoreria_cuenta_corriente_david (
            operation_id, idempotency_key, journal_id, operation_type, amount_cents,
            saldo_deuda_anterior_cents, saldo_deuda_resultante_cents,
            saldo_1220_anterior_cents, saldo_1220_resultante_cents,
            saldo_1130_anterior_cents, saldo_1130_resultante_cents,
            tesoreria_movement_id, referencia, motivo, fecha_referencia, creado_por, creado_en_utc
          ) VALUES (?, ?, ?, 'PAGO_EFECTIVO_DAVID', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);`,
    args: [
      opId,
      idempotencyKey,
      jornada.id,
      monto,
      deudaAnterior,
      deudaResultante,
      estadoPrevio.saldo_1220_prepay_digital_cents,
      estadoPrevio.saldo_1220_prepay_digital_cents,
      estadoPrevio.saldo_1130_prepay_efectivo_cents,
      mov.saldo_posterior_centavos,
      mov.movement_id,
      referencia,
      motivo,
      fechaRef,
      usuario,
      nowUtc,
    ],
  });

  return {
    operation_id: opId,
    idempotency_key: idempotencyKey,
    operation_type: "PAGO_EFECTIVO_DAVID",
    amount_cents: monto,
    amount_formatted: formatMoney(monto),
    saldo_deuda_anterior_cents: deudaAnterior,
    saldo_deuda_resultante_cents: deudaResultante,
    saldo_1220_anterior_cents: estadoPrevio.saldo_1220_prepay_digital_cents,
    saldo_1220_resultante_cents: estadoPrevio.saldo_1220_prepay_digital_cents,
    saldo_1130_anterior_cents: estadoPrevio.saldo_1130_prepay_efectivo_cents,
    saldo_1130_resultante_cents: mov.saldo_posterior_centavos,
    tesoreria_movement_id: mov.movement_id,
    referencia,
    motivo,
    fecha_referencia: fechaRef,
    creado_por: usuario,
    creado_en_utc: nowUtc,
  };
}

export async function registrarReconocimientoHistoricoDavidWorker(client, payload, usuario, idempotencyKey) {
  if (!idempotencyKey) throw new Error("INVALID_INPUT: Se requiere cabecera Idempotency-Key.");
  if (!payload.confirmacion_explicita) {
    throw new Error("INVALID_INPUT: Se requiere confirmación explícita para reconocer deuda histórica.");
  }
  const monto = Number(payload.monto_centavos);
  const motivo = String(payload.motivo || "").trim();
  const fechaRef = String(payload.fecha_referencia || new Date().toISOString().slice(0, 10)).trim();
  const refExt = String(payload.referencia_externa || `HIST_${fechaRef}`).trim();

  if (!Number.isInteger(monto) || monto <= 0 || motivo.length < 3) {
    throw new Error("INVALID_INPUT: Monto positivo y motivo justificado son obligatorios.");
  }

  let jornada = await obtenerJornadaActualWorker(client);
  if (!jornada) {
    jornada = await abrirJornadaWorker(client, { observaciones: "Apertura automática por reconocimiento histórico David" }, usuario);
  }

  const estadoPrevio = await obtenerEstadoCCDavidWorker(client);
  const opId = `CCD-${crypto.randomUUID().slice(0, 12).toUpperCase()}`;
  const nowUtc = new Date().toISOString();
  const deudaAnterior = estadoPrevio.obligacion_pendiente_david_cents;
  const deudaResultante = deudaAnterior + monto;

  await client.execute({
    sql: `INSERT INTO tesoreria_cuenta_corriente_david (
            operation_id, idempotency_key, journal_id, operation_type, amount_cents,
            saldo_deuda_anterior_cents, saldo_deuda_resultante_cents,
            saldo_1220_anterior_cents, saldo_1220_resultante_cents,
            saldo_1130_anterior_cents, saldo_1130_resultante_cents,
            tesoreria_movement_id, referencia, motivo, fecha_referencia, creado_por, creado_en_utc
          ) VALUES (?, ?, ?, 'RECONOCIMIENTO_DEUDA_HISTORICA', ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?);`,
    args: [
      opId,
      idempotencyKey,
      jornada.id,
      monto,
      deudaAnterior,
      deudaResultante,
      estadoPrevio.saldo_1220_prepay_digital_cents,
      estadoPrevio.saldo_1220_prepay_digital_cents,
      estadoPrevio.saldo_1130_prepay_efectivo_cents,
      estadoPrevio.saldo_1130_prepay_efectivo_cents,
      refExt,
      motivo,
      fechaRef,
      usuario,
      nowUtc,
    ],
  });

  return {
    operation_id: opId,
    idempotency_key: idempotencyKey,
    operation_type: "RECONOCIMIENTO_DEUDA_HISTORICA",
    amount_cents: monto,
    amount_formatted: formatMoney(monto),
    saldo_deuda_anterior_cents: deudaAnterior,
    saldo_deuda_resultante_cents: deudaResultante,
    saldo_1220_anterior_cents: estadoPrevio.saldo_1220_prepay_digital_cents,
    saldo_1220_resultante_cents: estadoPrevio.saldo_1220_prepay_digital_cents,
    saldo_1130_anterior_cents: estadoPrevio.saldo_1130_prepay_efectivo_cents,
    saldo_1130_resultante_cents: estadoPrevio.saldo_1130_prepay_efectivo_cents,
    tesoreria_movement_id: null,
    referencia: refExt,
    motivo,
    fecha_referencia: fechaRef,
    creado_por: usuario,
    creado_en_utc: nowUtc,
  };
}

// ============================================================================
// ANTICIPOS Y REPOSICIÓN DE PREVENTISTAS
// ============================================================================

function mapearAnticipoRow(r) {
  const orig = Number(r.monto_original_cents);
  const rep = Number(r.monto_repuesto_cents || 0);
  const pend = Number(r.saldo_pendiente_cents !== undefined ? r.saldo_pendiente_cents : orig - rep);
  const mov1130 = String(r.movement_1130_id || "");
  return {
    id: String(r.id),
    idempotency_key: String(r.idempotency_key),
    journal_id: Number(r.journal_id || 1),
    proveedor: String(r.proveedor),
    pedido_id: r.pedido_id ? String(r.pedido_id) : null,
    monto_original_cents: orig,
    monto_original_formatted: formatMoney(orig),
    monto_repuesto_cents: rep,
    monto_repuesto_formatted: formatMoney(rep),
    saldo_pendiente_cents: pend,
    saldo_pendiente_formatted: formatMoney(pend),
    monto_pendiente_cents: pend,
    monto_pendiente_formatted: formatMoney(pend),
    estado: String(r.estado || "PENDIENTE_REPOSICION"),
    motivo: String(r.motivo),
    referencia_documento: r.referencia_documento ? String(r.referencia_documento) : null,
    movement_1130_id: mov1130,
    salida_movement_id: mov1130,
    creado_por: String(r.creado_por),
    creado_en_utc: String(r.creado_en_utc),
    actualizado_en_utc: String(r.actualizado_en_utc || r.creado_en_utc),
    repuesto_completo_en_utc: r.repuesto_completo_en_utc ? String(r.repuesto_completo_en_utc) : null,
  };
}

export async function listarAnticiposPreventistasWorker(client, { estado = null, proveedor = null } = {}) {
  let sql = "SELECT * FROM tesoreria_anticipos_preventistas WHERE 1=1";
  const args = [];
  if (estado) {
    sql += " AND estado = ?";
    args.push(String(estado).trim().toUpperCase());
  }
  if (proveedor) {
    sql += " AND proveedor LIKE ?";
    args.push(`%${String(proveedor).trim()}%`);
  }
  sql += " ORDER BY creado_en_utc DESC;";
  const res = await client.execute({ sql, args });
  return res.rows.map(mapearAnticipoRow);
}

export async function crearAnticipoPreventistaWorker(client, payload, usuario, idempotencyKey) {
  if (!idempotencyKey) throw new Error("INVALID_INPUT: Se requiere cabecera Idempotency-Key.");
  const proveedor = String(payload.proveedor || "").trim();
  const monto = Number(payload.monto_centavos);
  const motivo = String(payload.motivo || "").trim();
  const refDoc = payload.referencia_documento ? String(payload.referencia_documento).trim() : null;
  const pedidoId = payload.pedido_id ? String(payload.pedido_id).trim() : null;

  if (!proveedor || !Number.isInteger(monto) || monto <= 0 || !motivo) {
    throw new Error("INVALID_INPUT: Proveedor, monto positivo y motivo son obligatorios.");
  }

  let jornada = await obtenerJornadaActualWorker(client);
  if (!jornada) {
    jornada = await abrirJornadaWorker(client, { observaciones: "Apertura automática por anticipo a preventista" }, usuario);
  }

  const anticipoId = `ANT-${crypto.randomUUID().slice(0, 12).toUpperCase()}`;
  const mov = await registrarMovimientoWorker(
    client,
    "EXIT",
    {
      codigo_fondo: "1130",
      monto_centavos: monto,
      contrapartida: `ANTICIPO_PREVENTISTA_${proveedor.toUpperCase()}`,
      motivo: `Anticipo a preventista ${proveedor}: ${motivo}`,
      referencia_evidencia: refDoc || anticipoId,
    },
    usuario,
    `${idempotencyKey}_EXIT_1130`
  );

  const nowUtc = new Date().toISOString();
  await client.execute({
    sql: `INSERT INTO tesoreria_anticipos_preventistas (
            id, idempotency_key, journal_id, proveedor, pedido_id,
            monto_original_cents, monto_repuesto_cents, saldo_pendiente_cents,
            estado, motivo, referencia_documento, movement_1130_id,
            creado_por, creado_en_utc, actualizado_en_utc
          ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, 'PENDIENTE_REPOSICION', ?, ?, ?, ?, ?, ?);`,
    args: [
      anticipoId,
      idempotencyKey,
      jornada.id,
      proveedor,
      pedidoId,
      monto,
      monto,
      motivo,
      refDoc,
      mov.movement_id,
      usuario,
      nowUtc,
      nowUtc,
    ],
  });

  return obtenerDetalleAnticipoPreventistaWorker(client, anticipoId);
}

export async function obtenerDetalleAnticipoPreventistaWorker(client, anticipoId) {
  const res = await client.execute({
    sql: "SELECT * FROM tesoreria_anticipos_preventistas WHERE id = ?;",
    args: [anticipoId],
  });
  if (res.rows.length === 0) return null;
  const ant = mapearAnticipoRow(res.rows[0]);
  const repos = await obtenerReposicionesPreventistaWorker(client, anticipoId);
  return { ...ant, reposiciones: repos };
}

export async function obtenerReposicionesPreventistaWorker(client, anticipoId = null) {
  let sql = "SELECT * FROM tesoreria_reposiciones_preventistas";
  const args = [];
  if (anticipoId) {
    sql += " WHERE anticipo_id = ?";
    args.push(anticipoId);
  }
  sql += " ORDER BY creado_en_utc DESC;";
  const res = await client.execute({ sql, args });
  return res.rows.map((r) => {
    const totalRep = Number(r.monto_total_repuesto_cents);
    const d1120 = Number(r.monto_desde_1120_cents);
    const d1110 = Number(r.monto_desde_1110_cents);
    const origenLabel = d1120 > 0 && d1110 > 0 ? "1120+1110" : d1120 > 0 ? "1120" : "1110";
    return {
      id: String(r.id),
      anticipo_id: String(r.anticipo_id),
      idempotency_key: String(r.idempotency_key),
      journal_id: Number(r.journal_id),
      fondo_origen: origenLabel,
      fondo_destino: "1130",
      monto_cents: totalRep,
      monto_formatted: formatMoney(totalRep),
      monto_total_repuesto_cents: totalRep,
      monto_total_repuesto_formatted: formatMoney(totalRep),
      monto_desde_1120_cents: d1120,
      monto_desde_1120_formatted: formatMoney(d1120),
      monto_desde_1110_cents: d1110,
      monto_desde_1110_formatted: formatMoney(d1110),
      transfer_1120_id: r.transfer_1120_id ? String(r.transfer_1120_id) : null,
      transfer_1110_id: r.transfer_1110_id ? String(r.transfer_1110_id) : null,
      movement_id_egreso: r.transfer_1120_id ? String(r.transfer_1120_id) : String(r.transfer_1110_id || ""),
      movement_id_ingreso: r.transfer_1110_id ? String(r.transfer_1110_id) : String(r.transfer_1120_id || ""),
      motivo: String(r.motivo),
      ejecutado_por: String(r.ejecutado_por),
      actor: String(r.ejecutado_por),
      creado_en_utc: String(r.creado_en_utc),
    };
  });
}

export async function reponerAnticipoPreventistaWorker(client, anticipoId, payload, usuario, idempotencyKey) {
  if (!idempotencyKey) throw new Error("INVALID_INPUT: Se requiere cabecera Idempotency-Key.");
  const ant = await obtenerDetalleAnticipoPreventistaWorker(client, anticipoId);
  if (!ant) throw new Error(`NOT_FOUND: Anticipo ${anticipoId} no encontrado.`);
  if (ant.saldo_pendiente_cents <= 0) {
    throw new Error("INVALID_INPUT: El anticipo ya se encuentra repuesto en su totalidad.");
  }

  const montoReponer =
    payload?.monto_centavos !== undefined && payload?.monto_centavos !== null
      ? Number(payload.monto_centavos)
      : ant.saldo_pendiente_cents;
  if (!Number.isInteger(montoReponer) || montoReponer <= 0 || montoReponer > ant.saldo_pendiente_cents) {
    throw new Error("INVALID_INPUT: Monto a reponer inválido o mayor al saldo pendiente.");
  }

  let jornada = await obtenerJornadaActualWorker(client);
  if (!jornada) {
    jornada = await abrirJornadaWorker(client, { observaciones: "Apertura automática por reposición preventista" }, usuario);
  }

  // Jerarquía estricta: 1° 1120 (Drugstore Acumulado), 2° 1110 (Drugstore Diario)
  const d1120 = await obtenerDisponibilidadFondoWorker(client, "1120");
  const d1110 = await obtenerDisponibilidadFondoWorker(client, "1110");
  const dispTotal = d1120.saldo_disponible_cents + d1110.saldo_disponible_cents;

  if (dispTotal < montoReponer) {
    throw new Error(
      `UNPROCESSABLE: Liquidez combinada insuficiente en 1120 (${d1120.saldo_disponible_formatted}) + 1110 (${d1110.saldo_disponible_formatted}) para reponer ${formatMoney(montoReponer)}.`
    );
  }

  const desde1120 = Math.min(d1120.saldo_disponible_cents, montoReponer);
  const desde1110 = montoReponer - desde1120;
  const motivo = String(payload?.motivo || `Reposición jerárquica de anticipo ${anticipoId} (${ant.proveedor})`).trim();

  let transfer1120Id = null;
  let transfer1110Id = null;

  if (desde1120 > 0) {
    const t1 = await iniciarTrasladoWorker(
      client,
      {
        origin_fund_code: "1120",
        destination_fund_code: "1130",
        amount_cents: desde1120,
        reason: `${motivo} [Tramo 1°: 1120 -> 1130]`,
        evidence_reference: anticipoId,
      },
      usuario,
      `${idempotencyKey}_T1120`,
      { allowInternalSweepOrRepo: true }
    );
    await confirmarTrasladoWorker(client, t1.transfer_id, usuario, `${idempotencyKey}_C1120`);
    transfer1120Id = t1.transfer_id;
  }

  if (desde1110 > 0) {
    const t2 = await iniciarTrasladoWorker(
      client,
      {
        origin_fund_code: "1110",
        destination_fund_code: "1130",
        amount_cents: desde1110,
        reason: `${motivo} [Tramo 2°: 1110 -> 1130]`,
        evidence_reference: anticipoId,
      },
      usuario,
      `${idempotencyKey}_T1110`,
      { allowInternalSweepOrRepo: true }
    );
    await confirmarTrasladoWorker(client, t2.transfer_id, usuario, `${idempotencyKey}_C1110`);
    transfer1110Id = t2.transfer_id;
  }

  const nuevoRepuesto = ant.monto_repuesto_cents + montoReponer;
  const nuevoPendiente = ant.saldo_pendiente_cents - montoReponer;
  const nuevoEstado = nuevoPendiente === 0 ? "REPUESTO_COMPLETO" : "REPUESTO_PARCIAL";
  const nowUtc = new Date().toISOString();
  const repoId = `REP-${crypto.randomUUID().slice(0, 12).toUpperCase()}`;

  await client.batch(
    [
      {
        sql: `UPDATE tesoreria_anticipos_preventistas
              SET monto_repuesto_cents = ?, saldo_pendiente_cents = ?, estado = ?,
                  actualizado_en_utc = ?, repuesto_completo_en_utc = ?
              WHERE id = ?;`,
        args: [
          nuevoRepuesto,
          nuevoPendiente,
          nuevoEstado,
          nowUtc,
          nuevoPendiente === 0 ? nowUtc : null,
          anticipoId,
        ],
      },
      {
        sql: `INSERT INTO tesoreria_reposiciones_preventistas (
                id, anticipo_id, idempotency_key, journal_id,
                monto_total_repuesto_cents, monto_desde_1120_cents, monto_desde_1110_cents,
                transfer_1120_id, transfer_1110_id, motivo, ejecutado_por, creado_en_utc
              ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);`,
        args: [
          repoId,
          anticipoId,
          idempotencyKey,
          jornada.id,
          montoReponer,
          desde1120,
          desde1110,
          transfer1120Id,
          transfer1110Id,
          motivo,
          usuario,
          nowUtc,
        ],
      },
    ],
    "write"
  );

  return {
    reposicion_id: repoId,
    anticipo: await obtenerDetalleAnticipoPreventistaWorker(client, anticipoId),
    desglose_jerarquico: {
      monto_total_repuesto_cents: montoReponer,
      monto_total_repuesto_formatted: formatMoney(montoReponer),
      monto_desde_1120_cents: desde1120,
      monto_desde_1120_formatted: formatMoney(desde1120),
      monto_desde_1110_cents: desde1110,
      monto_desde_1110_formatted: formatMoney(desde1110),
      transfer_1120_id: transfer1120Id,
      transfer_1110_id: transfer1110Id,
    },
  };
}

export async function obtenerResumenPreventistasWorker(client) {
  const anticipos = await listarAnticiposPreventistasWorker(client);
  const reposiciones = await obtenerReposicionesPreventistaWorker(client);
  const f1130 = await obtenerFondoDetalleWorker(client, "1130");
  const d1120 = await obtenerDisponibilidadFondoWorker(client, "1120");
  const d1110 = await obtenerDisponibilidadFondoWorker(client, "1110");

  let totalHistorico = 0;
  let totalRepuesto = 0;
  let obligacionPendiente = 0;
  let cantPendientes = 0;
  let cantParciales = 0;
  let cantCompletos = 0;

  for (const a of anticipos) {
    totalHistorico += a.monto_original_cents;
    totalRepuesto += a.monto_repuesto_cents;
    obligacionPendiente += a.saldo_pendiente_cents;
    if (a.estado === "PENDIENTE_REPOSICION") cantPendientes++;
    else if (a.estado === "REPUESTO_PARCIAL") cantParciales++;
    else if (a.estado === "REPUESTO_COMPLETO") cantCompletos++;
  }

  const liquidezCombinada = d1120.saldo_disponible_cents + d1110.saldo_disponible_cents;

  return {
    obligacion_pendiente_reposicion_cents: obligacionPendiente,
    obligacion_pendiente_reposicion_formatted: formatMoney(obligacionPendiente),
    total_pendiente_reposicion_cents: obligacionPendiente,
    total_pendiente_reposicion_formatted: formatMoney(obligacionPendiente),
    total_historico_anticipado_cents: totalHistorico,
    total_historico_anticipado_formatted: formatMoney(totalHistorico),
    total_anticipado_cents: totalHistorico,
    total_anticipado_formatted: formatMoney(totalHistorico),
    total_historico_repuesto_cents: totalRepuesto,
    total_historico_repuesto_formatted: formatMoney(totalRepuesto),
    total_repuesto_cents: totalRepuesto,
    total_repuesto_formatted: formatMoney(totalRepuesto),
    cantidad_anticipos: anticipos.length,
    cantidad_reposiciones: reposiciones.length,
    saldo_disponible_1130_cents: f1130.saldo_centavos,
    saldo_disponible_1130_formatted: formatMoney(f1130.saldo_centavos),
    saldo_disponible_1120_cents: d1120.saldo_disponible_cents,
    saldo_disponible_1120_formatted: d1120.saldo_disponible_formatted,
    saldo_disponible_1110_cents: d1110.saldo_disponible_cents,
    saldo_disponible_1110_formatted: d1110.saldo_disponible_formatted,
    conteo_expedientes: {
      total: anticipos.length,
      pendientes_reposicion: cantPendientes,
      repuestos_parcial: cantParciales,
      repuestos_completo: cantCompletos,
    },
    saldos_fondos_involucrados: {
      saldo_1130_prepay_efectivo_cents: f1130.saldo_centavos,
      saldo_1130_prepay_efectivo_formatted: formatMoney(f1130.saldo_centavos),
      disponible_1120_drugstore_acumulado_cents: d1120.saldo_disponible_cents,
      disponible_1120_drugstore_acumulado_formatted: d1120.saldo_disponible_formatted,
      disponible_1110_drugstore_diario_cents: d1110.saldo_disponible_cents,
      disponible_1110_drugstore_diario_formatted: d1110.saldo_disponible_formatted,
      liquidez_combinada_reposicion_cents: liquidezCombinada,
      liquidez_combinada_reposicion_formatted: formatMoney(liquidezCombinada),
    },
    capacidad_reposicion_inmediata: liquidezCombinada >= obligacionPendiente,
  };
}
