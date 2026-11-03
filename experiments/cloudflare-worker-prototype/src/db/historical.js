/**
 * Adaptador de persistencia para el módulo HISTÓRICO en Cloudflare Workers.
 * Consultas exclusivamente de solo lectura contra las tablas históricas:
 * - historico_lotes
 * - historico_tickets
 * - historico_ticket_items
 * - historico_cargas_servicios
 */

export async function obtenerResumenHistorico(client) {
  const [resTickets, resItems, resCargas] = await Promise.all([
    client.execute({
      sql: `
        SELECT 
          COUNT(*) as cnt, 
          COALESCE(SUM(total_venta), 0) as tot_ventas,
          COALESCE(SUM(monto_efectivo), 0) as tot_efectivo,
          COALESCE(SUM(monto_transferencia), 0) as tot_transferencia,
          MIN(fecha_hora_local) as min_fecha,
          MAX(fecha_hora_local) as max_fecha
        FROM historico_tickets
      `,
      args: [],
    }),
    client.execute({
      sql: `SELECT COUNT(*) as cnt, COALESCE(SUM(subtotal), 0) as tot_subtotal FROM historico_ticket_items`,
      args: [],
    }),
    client.execute({
      sql: `
        SELECT 
          COUNT(*) as cnt,
          COALESCE(SUM(total_cobrado), 0) as tot_cobrado,
          COALESCE(SUM(monto_carga), 0) as tot_carga,
          COALESCE(SUM(monto_adicional), 0) as tot_adicional,
          COALESCE(SUM(pago_efectivo), 0) as tot_efectivo,
          COALESCE(SUM(pago_transferencia), 0) as tot_transferencia
        FROM historico_cargas_servicios
      `,
      args: [],
    }),
  ]);

  const rowT = resTickets.rows[0];
  const rowI = resItems.rows[0];
  const rowC = resCargas.rows[0];

  const cantTickets = Number(rowT?.cnt ?? 0);
  const totVentas = Number(rowT?.tot_ventas ?? 0);
  const promTicket = cantTickets > 0 ? Math.round((totVentas / cantTickets) * 100) / 100 : 0.0;

  return {
    total_ventas_pesos: totVentas,
    total_tickets: cantTickets,
    ticket_promedio: promTicket,
    total_ventas_efectivo: Number(rowT?.tot_efectivo ?? 0),
    total_ventas_transferencia: Number(rowT?.tot_transferencia ?? 0),
    fecha_inicio: String(rowT?.min_fecha || "2025-12-01 08:36:20"),
    fecha_fin: String(rowT?.max_fecha || "2026-10-29 17:02:41"),
    periodo_meses: 11,
    total_items_filas: Number(rowI?.cnt ?? 0),
    total_items_subtotal: Number(rowI?.tot_subtotal ?? 0),
    total_cargas_cobrado: Number(rowC?.tot_cobrado ?? 0),
    total_cargas_capital: Number(rowC?.tot_carga ?? 0),
    total_cargas_adicional: Number(rowC?.tot_adicional ?? 0),
    total_operaciones_cargas: Number(rowC?.cnt ?? 0),
    total_cargas_efectivo: Number(rowC?.tot_efectivo ?? 0),
    total_cargas_transferencia: Number(rowC?.tot_transferencia ?? 0),
    advertencia_pagos:
      "Existe una diferencia neta de +$6.900 (+0,03%) entre la suma de pagos ($23.630.790) y las ventas ($23.623.890) debido a 4 tickets con registro mixto no balanceado.",
    advertencia_items:
      "El subtotal de artículos ($23.522.590) difiere en -$101.300 (-0,43%) respecto a cabeceras debido a 29 tickets sin desglose ($117.400) y 6 tickets con reintentos temporales.",
  };
}

export async function obtenerVentasHistoricas(
  client,
  { desde = null, hasta = null, metodo_pago = null, agrupacion = "mes", limit = 50, offset = 0 } = {}
) {
  const whereClauses = ["1=1"];
  const params = [];

  if (desde) {
    whereClauses.push("substr(fecha_hora_local, 1, 10) >= ?");
    params.push(String(desde).trim());
  }
  if (hasta) {
    whereClauses.push("substr(fecha_hora_local, 1, 10) <= ?");
    params.push(String(hasta).trim());
  }
  if (metodo_pago && ["EFECTIVO", "TRANSFERENCIA", "MIXTO"].includes(metodo_pago.toUpperCase())) {
    whereClauses.push("metodo_pago = ?");
    params.push(metodo_pago.toUpperCase());
  }

  const whereSql = whereClauses.join(" AND ");

  // 1. Totales
  const resTot = await client.execute({
    sql: `
      SELECT 
        COUNT(*) as cnt,
        COALESCE(SUM(total_venta), 0) as tot_ventas,
        COALESCE(SUM(monto_efectivo), 0) as efvo,
        COALESCE(SUM(monto_transferencia), 0) as transf
      FROM historico_tickets
      WHERE ${whereSql}
    `,
    args: params,
  });

  const rowTot = resTot.rows[0];
  const cantTickets = Number(rowTot?.cnt ?? 0);
  const totVentas = Number(rowTot?.tot_ventas ?? 0);
  const promTicket = cantTickets > 0 ? Math.round((totVentas / cantTickets) * 100) / 100 : 0.0;

  // 2. Serie temporal
  const dateExpr = agrupacion === "mes" ? "substr(fecha_hora_local, 1, 7)" : "substr(fecha_hora_local, 1, 10)";
  const resSerie = await client.execute({
    sql: `
      SELECT 
        ${dateExpr} AS periodo,
        COUNT(*) AS tickets,
        SUM(total_venta) AS ventas_total,
        SUM(monto_efectivo) AS efvo,
        SUM(monto_transferencia) AS transf
      FROM historico_tickets
      WHERE ${whereSql}
      GROUP BY ${dateExpr}
      ORDER BY periodo ASC
    `,
    args: params,
  });

  const serieTemporal = resSerie.rows.map((r) => ({
    periodo: String(r.periodo),
    tickets: Number(r.tickets ?? 0),
    ventas_total: Number(r.ventas_total ?? 0),
    monto_efectivo: Number(r.efvo ?? 0),
    monto_transferencia: Number(r.transf ?? 0),
  }));

  // 3. Paginación de tickets
  const safeLimit = Math.max(1, Math.min(Number(limit) || 50, 200));
  const safeOffset = Math.max(0, Number(offset) || 0);
  const paramsPage = [...params, safeLimit, safeOffset];

  const resTickets = await client.execute({
    sql: `
      SELECT 
        id,
        fecha_hora_local,
        total_venta,
        monto_efectivo,
        monto_transferencia,
        metodo_pago,
        tiene_items,
        balance_pago_diferencia
      FROM historico_tickets
      WHERE ${whereSql}
      ORDER BY fecha_hora_local DESC
      LIMIT ? OFFSET ?
    `,
    args: paramsPage,
  });

  const tickets = resTickets.rows.map((r) => ({
    id: String(r.id),
    fecha_hora_local: String(r.fecha_hora_local),
    total_venta: Number(r.total_venta ?? 0),
    monto_efectivo: Number(r.monto_efectivo ?? 0),
    monto_transferencia: Number(r.monto_transferencia ?? 0),
    metodo_pago: String(r.metodo_pago),
    tiene_items: Number(r.tiene_items ?? 0),
    balance_pago_diferencia: Number(r.balance_pago_diferencia ?? 0),
  }));

  return {
    desde,
    hasta,
    metodo_pago,
    total_ventas: totVentas,
    cantidad_tickets: cantTickets,
    ticket_promedio: promTicket,
    total_efectivo: Number(rowTot?.efvo ?? 0),
    total_transferencia: Number(rowTot?.transf ?? 0),
    serie_temporal: serieTemporal,
    tickets,
    total_registros: cantTickets,
    limit: safeLimit,
    offset: safeOffset,
  };
}

export async function obtenerRankingProductosHistorico(
  client,
  { desde = null, hasta = null, depurado = true, limit = 50, offset = 0 } = {}
) {
  const whereClauses = ["1=1"];
  const params = [];

  if (depurado) {
    whereClauses.push("es_duplicado_deriva = 0");
  }
  if (desde) {
    whereClauses.push("substr(fecha_hora_local, 1, 10) >= ?");
    params.push(String(desde).trim());
  }
  if (hasta) {
    whereClauses.push("substr(fecha_hora_local, 1, 10) <= ?");
    params.push(String(hasta).trim());
  }

  const whereSql = whereClauses.join(" AND ");

  const resCnt = await client.execute({
    sql: `SELECT COUNT(DISTINCT producto_nombre) as cnt FROM historico_ticket_items WHERE ${whereSql}`,
    args: params,
  });
  const totalDistintos = Number(resCnt.rows[0]?.cnt ?? 0);

  const safeLimit = Math.max(1, Math.min(Number(limit) || 50, 200));
  const safeOffset = Math.max(0, Number(offset) || 0);
  const paramsPage = [...params, safeLimit, safeOffset];

  const resRank = await client.execute({
    sql: `
      SELECT 
        producto_nombre,
        COUNT(*) AS operaciones,
        SUM(CASE WHEN cantidad IS NOT NULL THEN cantidad ELSE 0 END) AS unidades_cuantificables,
        SUM(CASE WHEN cantidad IS NULL THEN 1 ELSE 0 END) AS ventas_pesables_monto,
        SUM(subtotal) AS total_recaudado
      FROM historico_ticket_items
      WHERE ${whereSql}
      GROUP BY producto_nombre
      ORDER BY total_recaudado DESC
      LIMIT ? OFFSET ?
    `,
    args: paramsPage,
  });

  const productos = resRank.rows.map((r, idx) => ({
    posicion: safeOffset + idx + 1,
    producto_nombre: String(r.producto_nombre),
    operaciones: Number(r.operaciones ?? 0),
    unidades_cuantificables: Math.round(Number(r.unidades_cuantificables ?? 0) * 100) / 100,
    ventas_pesables_monto: Number(r.ventas_pesables_monto ?? 0),
    total_recaudado: Number(r.total_recaudado ?? 0),
  }));

  return {
    desde,
    hasta,
    modo_depurado: Boolean(depurado),
    total_productos_distintos: totalDistintos,
    productos,
    total_registros: totalDistintos,
    limit: safeLimit,
    offset: safeOffset,
    advertencia_pesables:
      "1.220 líneas corresponden a productos vendidos por importe fijo en pesos sin pesaje registrado en origen. Las ventas pesables se identifican por separado.",
  };
}

export async function obtenerServiciosHistoricos(
  client,
  { desde = null, hasta = null, excluir_duplicados = false } = {}
) {
  const whereClauses = ["1=1"];
  const params = [];

  if (excluir_duplicados) {
    whereClauses.push("es_posible_duplicado = 0");
  }
  if (desde) {
    whereClauses.push("substr(fecha_hora_local, 1, 10) >= ?");
    params.push(String(desde).trim());
  }
  if (hasta) {
    whereClauses.push("substr(fecha_hora_local, 1, 10) <= ?");
    params.push(String(hasta).trim());
  }

  const whereSql = whereClauses.join(" AND ");

  const [resTot, resSrv, resSerie] = await Promise.all([
    client.execute({
      sql: `
        SELECT 
          COUNT(*) as cnt,
          COALESCE(SUM(monto_carga), 0) as tot_carga,
          COALESCE(SUM(monto_adicional), 0) as tot_adicional,
          COALESCE(SUM(total_cobrado), 0) as tot_cobrado,
          COALESCE(SUM(pago_efectivo), 0) as efvo,
          COALESCE(SUM(pago_transferencia), 0) as transf
        FROM historico_cargas_servicios
        WHERE ${whereSql}
      `,
      args: params,
    }),
    client.execute({
      sql: `
        SELECT 
          servicio,
          COUNT(*) AS total_operaciones,
          SUM(monto_carga) AS capital,
          SUM(monto_adicional) AS ganancia,
          SUM(total_cobrado) AS cobrado,
          SUM(pago_efectivo) AS efvo,
          SUM(pago_transferencia) AS transf
        FROM historico_cargas_servicios
        WHERE ${whereSql}
        GROUP BY servicio
        ORDER BY cobrado DESC
      `,
      args: params,
    }),
    client.execute({
      sql: `
        SELECT 
          substr(fecha_hora_local, 1, 7) AS periodo,
          COUNT(*) as cnt,
          SUM(total_cobrado) as cobrado,
          SUM(monto_adicional) as adicional
        FROM historico_cargas_servicios
        WHERE ${whereSql}
        GROUP BY substr(fecha_hora_local, 1, 7)
        ORDER BY periodo ASC
      `,
      args: params,
    }),
  ]);

  const rowTot = resTot.rows[0];

  const servicios = resSrv.rows.map((r) => ({
    servicio: String(r.servicio),
    total_operaciones: Number(r.total_operaciones ?? 0),
    capital_recargado: Number(r.capital ?? 0),
    adicional_ganancia: Number(r.ganancia ?? 0),
    total_cobrado: Number(r.cobrado ?? 0),
    pago_efectivo: Number(r.efvo ?? 0),
    pago_transferencia: Number(r.transf ?? 0),
  }));

  const serieTemporal = resSerie.rows.map((r) => ({
    periodo: String(r.periodo),
    operaciones: Number(r.cnt ?? 0),
    total_cobrado: Number(r.cobrado ?? 0),
    adicional_ganancia: Number(r.adicional ?? 0),
  }));

  return {
    desde,
    hasta,
    excluir_duplicados: Boolean(excluir_duplicados),
    total_operaciones: Number(rowTot?.cnt ?? 0),
    capital_recargado: Number(rowTot?.tot_carga ?? 0),
    adicional_ganancia: Number(rowTot?.tot_adicional ?? 0),
    total_cobrado: Number(rowTot?.tot_cobrado ?? 0),
    pago_efectivo: Number(rowTot?.efvo ?? 0),
    pago_transferencia: Number(rowTot?.transf ?? 0),
    servicios,
    serie_temporal: serieTemporal,
    advertencia_duplicados:
      "12 parejas de operaciones se registraron al mismo segundo exacto. Se ofrece la opción de excluirlas estadísticamente sin alterar el registro persistido.",
  };
}

export async function obtenerTicketDetalleHistorico(client, ticketId) {
  const tidClean = String(ticketId).trim();

  const resT = await client.execute({
    sql: `
      SELECT 
        id,
        fecha_hora_local,
        fecha_hora_utc,
        total_venta,
        monto_efectivo,
        monto_transferencia,
        metodo_pago,
        es_nocturno,
        balance_pago_diferencia,
        tiene_items,
        origen_fila_excel
      FROM historico_tickets
      WHERE id = ?
    `,
    args: [tidClean],
  });

  const rowT = resT.rows[0];
  if (!rowT) {
    return null;
  }

  const resItems = await client.execute({
    sql: `
      SELECT 
        id,
        ticket_id,
        producto_nombre,
        cantidad,
        unidad,
        precio_unitario,
        subtotal,
        metodo_pago,
        calculo_subtotal_coincide,
        es_duplicado_deriva,
        ticket_id_correlacionado
      FROM historico_ticket_items
      WHERE ticket_id = ? OR ticket_id_correlacionado = ?
      ORDER BY id ASC
    `,
    args: [tidClean, tidClean],
  });

  const items = resItems.rows.map((r) => ({
    id: Number(r.id),
    ticket_id: String(r.ticket_id),
    producto_nombre: String(r.producto_nombre),
    cantidad: r.cantidad !== null ? Number(r.cantidad) : null,
    unidad: String(r.unidad || "Unidad"),
    precio_unitario: Number(r.precio_unitario ?? 0),
    subtotal: Number(r.subtotal ?? 0),
    metodo_pago: String(r.metodo_pago || ""),
    calculo_subtotal_coincide: Number(r.calculo_subtotal_coincide ?? 1),
    es_duplicado_deriva: Number(r.es_duplicado_deriva ?? 0),
    ticket_id_correlacionado: r.ticket_id_correlacionado ? String(r.ticket_id_correlacionado) : null,
  }));

  const alertas = [];
  const balDiff = Number(rowT.balance_pago_diferencia ?? 0);
  if (balDiff !== 0) {
    alertas.push(`Discrepancia en pagos: El balance difiere en $${Math.abs(balDiff)} del total facturado.`);
  }
  if (Number(rowT.tiene_items) === 0) {
    alertas.push("Venta directa de cabecera sin desglose de artículos registrado.");
  }
  for (const it of items) {
    if (it.ticket_id_correlacionado === tidClean) {
      alertas.push(`Ítem '${it.producto_nombre}' reasociado por proximidad de timestamp (< 1s).`);
    }
  }

  return {
    id: String(rowT.id),
    fecha_hora_local: String(rowT.fecha_hora_local),
    fecha_hora_utc: String(rowT.fecha_hora_utc),
    total_venta: Number(rowT.total_venta ?? 0),
    monto_efectivo: Number(rowT.monto_efectivo ?? 0),
    monto_transferencia: Number(rowT.monto_transferencia ?? 0),
    metodo_pago: String(rowT.metodo_pago),
    es_nocturno: Number(rowT.es_nocturno ?? 0),
    balance_pago_diferencia: balDiff,
    tiene_items: Number(rowT.tiene_items ?? 1),
    origen_fila_excel: Number(rowT.origen_fila_excel ?? 0),
    items,
    alertas,
  };
}

export async function buscarTicketsHistoricos(
  client,
  { query = null, fecha = null, limit = 20, offset = 0 } = {}
) {
  const whereClauses = ["1=1"];
  const params = [];

  if (query) {
    whereClauses.push("id LIKE ?");
    params.push(`%${String(query).trim()}%`);
  }
  if (fecha) {
    whereClauses.push("substr(fecha_hora_local, 1, 10) = ?");
    params.push(String(fecha).trim());
  }

  const whereSql = whereClauses.join(" AND ");
  const safeLimit = Math.max(1, Math.min(Number(limit) || 20, 100));
  const safeOffset = Math.max(0, Number(offset) || 0);

  const resCnt = await client.execute({
    sql: `SELECT COUNT(*) as cnt FROM historico_tickets WHERE ${whereSql}`,
    args: params,
  });
  const total = Number(resCnt.rows[0]?.cnt ?? 0);

  const paramsPage = [...params, safeLimit, safeOffset];
  const resTickets = await client.execute({
    sql: `
      SELECT id, fecha_hora_local, total_venta, monto_efectivo, monto_transferencia, metodo_pago, tiene_items, balance_pago_diferencia
      FROM historico_tickets
      WHERE ${whereSql}
      ORDER BY fecha_hora_local DESC
      LIMIT ? OFFSET ?
    `,
    args: paramsPage,
  });

  const tickets = resTickets.rows.map((r) => ({
    id: String(r.id),
    fecha_hora_local: String(r.fecha_hora_local),
    total_venta: Number(r.total_venta ?? 0),
    monto_efectivo: Number(r.monto_efectivo ?? 0),
    monto_transferencia: Number(r.monto_transferencia ?? 0),
    metodo_pago: String(r.metodo_pago),
    tiene_items: Number(r.tiene_items ?? 1),
    balance_pago_diferencia: Number(r.balance_pago_diferencia ?? 0),
  }));

  return {
    total_encontrados: total,
    tickets,
    limit: safeLimit,
    offset: safeOffset,
  };
}

export async function compararPeriodosHistoricos(
  client,
  { desde_a, hasta_a, desde_b, hasta_b }
) {
  async function metricasPeriodo(dIni, dFin) {
    const [resV, resC, resTop] = await Promise.all([
      client.execute({
        sql: `
          SELECT COUNT(*) as cnt, COALESCE(SUM(total_venta), 0) as tot_ventas
          FROM historico_tickets
          WHERE substr(fecha_hora_local, 1, 10) >= ? AND substr(fecha_hora_local, 1, 10) <= ?
        `,
        args: [dIni, dFin],
      }),
      client.execute({
        sql: `
          SELECT COUNT(*) as cnt, COALESCE(SUM(total_cobrado), 0) as tot_cobrado, COALESCE(SUM(monto_adicional), 0) as tot_adicional
          FROM historico_cargas_servicios
          WHERE substr(fecha_hora_local, 1, 10) >= ? AND substr(fecha_hora_local, 1, 10) <= ?
        `,
        args: [dIni, dFin],
      }),
      client.execute({
        sql: `
          SELECT producto_nombre, SUM(subtotal) as tot_subtotal
          FROM historico_ticket_items
          WHERE es_duplicado_deriva = 0
            AND substr(fecha_hora_local, 1, 10) >= ? AND substr(fecha_hora_local, 1, 10) <= ?
          GROUP BY producto_nombre
          ORDER BY tot_subtotal DESC
          LIMIT 5
        `,
        args: [dIni, dFin],
      }),
    ]);

    const rowV = resV.rows[0];
    const rowC = resC.rows[0];
    const cantT = Number(rowV?.cnt ?? 0);
    const totV = Number(rowV?.tot_ventas ?? 0);
    const promT = cantT > 0 ? Math.round((totV / cantT) * 100) / 100 : 0.0;

    const topProds = resTop.rows.map((r) => ({
      nombre: String(r.producto_nombre),
      subtotal: Number(r.tot_subtotal ?? 0),
    }));

    return {
      desde: dIni,
      hasta: dFin,
      facturacion_ventas: totV,
      cantidad_tickets: cantT,
      ticket_promedio: promT,
      cargas_cobrado: Number(rowC?.tot_cobrado ?? 0),
      cargas_adicional: Number(rowC?.tot_adicional ?? 0),
      operaciones_cargas: Number(rowC?.cnt ?? 0),
      top_productos: topProds,
    };
  }

  const [mA, mB] = await Promise.all([
    metricasPeriodo(desde_a, hasta_a),
    metricasPeriodo(desde_b, hasta_b),
  ]);

  function calcPct(valA, valB) {
    if (valA === 0) {
      return valB > 0 ? 100.0 : 0.0;
    }
    return Math.round(((valB - valA) / valA) * 10000) / 100;
  }

  const variaciones = {
    facturacion_ventas_pct: calcPct(mA.facturacion_ventas, mB.facturacion_ventas),
    cantidad_tickets_pct: calcPct(mA.cantidad_tickets, mB.cantidad_tickets),
    ticket_promedio_pct: calcPct(mA.ticket_promedio, mB.ticket_promedio),
    cargas_cobrado_pct: calcPct(mA.cargas_cobrado, mB.cargas_cobrado),
  };

  return {
    periodo_a: mA,
    periodo_b: mB,
    variaciones,
  };
}
