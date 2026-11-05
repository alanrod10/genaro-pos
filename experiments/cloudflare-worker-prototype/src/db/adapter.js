/**
 * Adaptador completo de Base de Datos para GenaroPOS (libSQL / Turso).
 * Implementa el 100% de los esquemas canónicos, transacciones y consultas requeridas
 * por la API de GenaroPOS en Staging y producción.
 */

import { clasificarServicio } from "../finance/financial_engine.js";

export function normalizarFechaIsoJujuy(fechaStr = null) {
  if (fechaStr && typeof fechaStr === "string") {
    const clean = fechaStr.trim().split(" ")[0];
    if (/^\d{4}-\d{2}-\d{2}$/.test(clean)) {
      return clean;
    }
    if (/^\d{1,2}\/\d{1,2}\/\d{4}$/.test(clean)) {
      const [d, m, y] = clean.split("/").map(Number);
      const pad = (n) => String(n).padStart(2, "0");
      return `${y}-${pad(m)}-${pad(d)}`;
    }
  }
  const now = new Date();
  const jujuyOffsetMs = -3 * 60 * 60 * 1000;
  const jujuyNow = new Date(now.getTime() + jujuyOffsetMs);
  const pad = (n) => String(n).padStart(2, "0");
  return `${jujuyNow.getUTCFullYear()}-${pad(jujuyNow.getUTCMonth() + 1)}-${pad(jujuyNow.getUTCDate())}`;
}

export function normalizarFechaHoraUtc(valor = null) {
  if (!valor || typeof valor !== "string" || !valor.trim()) {
    return new Date().toISOString();
  }
  const s = valor.trim();
  // Formato argentino DD/MM/YYYY HH:MM:SS o DD/MM/YYYY HH:MM o DD/MM/YYYY
  const mAr = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})(?:\s+(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?)?$/);
  if (mAr) {
    const [, d, m, y, hh = "00", mm = "00", ss = "00"] = mAr;
    const utcMs = Date.UTC(Number(y), Number(m) - 1, Number(d), Number(hh) + 3, Number(mm), Number(ss));
    return new Date(utcMs).toISOString();
  }
  const parsed = new Date(s.includes("T") ? s : s.replace(" ", "T") + (s.endsWith("Z") ? "" : "Z"));
  if (!Number.isNaN(parsed.getTime())) {
    return parsed.toISOString();
  }
  return new Date().toISOString();
}

export function formatearFechaLocalAr(utcStr) {
  if (!utcStr) return "";
  const parsed = new Date(String(utcStr).includes("T") ? utcStr : String(utcStr).replace(" ", "T") + "Z");
  if (Number.isNaN(parsed.getTime())) return String(utcStr);
  const jujuyMs = parsed.getTime() - 3 * 60 * 60 * 1000;
  const dt = new Date(jujuyMs);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(dt.getUTCDate())}/${pad(dt.getUTCMonth() + 1)}/${dt.getUTCFullYear()} ${pad(dt.getUTCHours())}:${pad(dt.getUTCMinutes())}:${pad(dt.getUTCSeconds())}`;
}

export function obtenerRangoUtcDiaJujuy(fechaStr = null) {
  const iso = normalizarFechaIsoJujuy(fechaStr);
  const [y, m, d] = iso.split("-").map(Number);

  const pad = (n) => String(n).padStart(2, "0");
  const desdeUtc = `${y}-${pad(m)}-${pad(d)}T03:00:00.000Z`;

  const nextDay = new Date(Date.UTC(y, m - 1, d + 1));
  const ny = nextDay.getUTCFullYear();
  const nm = nextDay.getUTCMonth() + 1;
  const nd = nextDay.getUTCDate();
  const hastaUtc = `${ny}-${pad(nm)}-${pad(nd)}T02:59:59.999Z`;

  return { desdeUtc, hastaUtc, fechaJujuy: `${y}-${pad(m)}-${pad(d)}` };
}

// --- Módulo Catálogo ---
export async function listarProductos(client, { includeInactive = false, search = null, category = null } = {}) {
  let sql = `
    SELECT id,
           id as id_producto,
           nombre,
           COALESCE(categoria, 'Otro') as categoria,
           COALESCE(categoria, 'Otro') as categoria_nombre,
           COALESCE(proveedor, 'General') as proveedor,
           COALESCE(unidad, 'Unidad') as unidad,
           COALESCE(costo, 0) as costo,
           COALESCE(costo, 0) as costo_unitario,
           COALESCE(precio_dia, 0) as precio,
           COALESCE(precio_dia, 0) as precio_dia,
           COALESCE(precio_dia, 0) as precio_venta,
           activo,
           COALESCE(fecha_actualizacion, '') as fecha_actualizacion,
           CASE WHEN COALESCE(costo, 0) > 0 
                THEN ROUND(((COALESCE(precio_dia, 0) - COALESCE(costo, 0)) * 1.0 / COALESCE(costo, 0)) * 100, 2)
                ELSE 0 END as margen_pct
    FROM productos
    WHERE 1=1
  `;
  const args = [];
  if (!includeInactive) {
    sql += " AND activo = 1";
  }
  if (category) {
    sql += " AND categoria = ?";
    args.push(category);
  }
  if (search) {
    sql += " AND (nombre LIKE ? OR categoria LIKE ? OR proveedor LIKE ?)";
    args.push(`%${search}%`, `%${search}%`, `%${search}%`);
  }
  sql += " ORDER BY id ASC;";

  const rs = await client.execute({ sql, args });
  return rs.rows;
}

export async function crearProducto(
  client,
  {
    id,
    codigo_barras,
    nombre,
    categoria,
    categoria_id,
    proveedor,
    unidad,
    precio,
    precio_dia,
    precio_venta,
    costo,
    costo_unitario,
    activo,
    usuario,
  }
) {
  const cat = categoria || categoria_id || "Otro";
  const prov = proveedor || "General";
  const uni = unidad || "Unidad";
  const cost = Number(costo !== undefined ? costo : (costo_unitario || 0));
  const pr = Number(
    precio_venta !== undefined ? precio_venta : (precio !== undefined ? precio : (precio_dia || 0))
  );
  const actVal = activo === false || activo === 0 ? 0 : 1;
  const nowUtc = new Date().toISOString();

  let res;
  const isNumericId = id !== undefined && id !== null && /^\d+$/.test(String(id));
  if (isNumericId) {
    res = await client.execute({
      sql: `INSERT INTO productos (id, nombre, categoria, proveedor, unidad, costo, precio_dia, activo, fecha_actualizacion)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);`,
      args: [Number(id), nombre, cat, prov, uni, cost, pr, actVal, nowUtc],
    });
  } else {
    res = await client.execute({
      sql: `INSERT INTO productos (nombre, categoria, proveedor, unidad, costo, precio_dia, activo, fecha_actualizacion)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?);`,
      args: [nombre, cat, prov, uni, cost, pr, actVal, nowUtc],
    });
  }
  const prodId = isNumericId ? Number(id) : Number(res.lastInsertRowid);

  await client.execute({
    sql: `INSERT INTO productos_auditoria (producto_id, accion, valores_nuevos, fecha_utc, actor)
          VALUES (?, 'CREAR', ?, ?, ?);`,
    args: [prodId, JSON.stringify({ nombre, categoria: cat, proveedor: prov, unidad: uni, costo: cost, precio_dia: pr, activo: actVal }), nowUtc, usuario || "sistema"],
  }).catch(() => {});

  return {
    id: prodId,
    id_producto: prodId,
    nombre,
    categoria: cat,
    proveedor: prov,
    unidad: uni,
    costo: cost,
    costo_unitario: cost,
    precio: pr,
    precio_dia: pr,
    precio_venta: pr,
    activo: actVal,
  };
}

export async function actualizarProducto(client, id, datos, usuario) {
  const nowUtc = new Date().toISOString();
  const updates = [];
  const args = [];

  if (datos.nombre !== undefined) { updates.push("nombre = ?"); args.push(datos.nombre); }
  if (datos.categoria !== undefined) { updates.push("categoria = ?"); args.push(datos.categoria); }
  if (datos.proveedor !== undefined) { updates.push("proveedor = ?"); args.push(datos.proveedor); }
  if (datos.unidad !== undefined) { updates.push("unidad = ?"); args.push(datos.unidad); }
  if (datos.costo !== undefined || datos.costo_unitario !== undefined) {
    updates.push("costo = ?");
    args.push(datos.costo !== undefined ? datos.costo : datos.costo_unitario);
  }
  if (datos.precio_venta !== undefined || datos.precio !== undefined || datos.precio_dia !== undefined) {
    updates.push("precio_dia = ?");
    args.push(
      datos.precio_venta !== undefined
        ? datos.precio_venta
        : (datos.precio !== undefined ? datos.precio : datos.precio_dia)
    );
  }
  if (datos.activo !== undefined) {
    updates.push("activo = ?");
    args.push(datos.activo ? 1 : 0);
  }

  if (updates.length > 0) {
    updates.push("fecha_actualizacion = ?");
    args.push(nowUtc);
    args.push(id);
    args.push(id);
    await client.batch([
      { sql: `UPDATE productos SET ${updates.join(", ")} WHERE id = ? OR nombre = ?;`, args },
      {
        sql: `INSERT INTO productos_auditoria (producto_id, accion, valores_nuevos, fecha_utc, actor)
              VALUES (?, 'EDITAR', ?, ?, ?);`,
        args: [id, JSON.stringify(datos), nowUtc, usuario || "sistema"],
      }
    ], "write");
  }

  const rs = await client.execute({ sql: "SELECT * FROM productos WHERE id = ? OR nombre = ?;", args: [id, id] });
  if (rs.rows.length === 0) return null;
  const p = rs.rows[0];
  return {
    ...p,
    id: Number(p.id),
    id_producto: Number(p.id),
    costo: Number(p.costo),
    costo_unitario: Number(p.costo),
    precio: Number(p.precio_dia),
    precio_dia: Number(p.precio_dia),
    precio_venta: Number(p.precio_dia),
  };
}

export async function cambiarEstadoProducto(client, id, activo, usuario) {
  const nowUtc = new Date().toISOString();
  await client.batch([
    {
      sql: "UPDATE productos SET activo = ? WHERE id = ? OR nombre = ?;",
      args: [activo ? 1 : 0, id, id],
    },
    {
      sql: `INSERT INTO productos_auditoria (producto_id, accion, valores_nuevos, fecha_utc, actor)
            VALUES (?, ?, ?, ?, ?);`,
      args: [id, activo ? 'ACTIVAR' : 'DESACTIVAR', JSON.stringify({ activo: activo ? 1 : 0 }), nowUtc, usuario || "sistema"],
    }
  ], "write");
  return { id, activo: activo ? 1 : 0 };
}

// --- Módulo Ventas POS ---
export function normalizarPayloadVenta(ticket, items) {
  if (!ticket || typeof ticket !== "object") {
    throw new Error("INVALID_INPUT: El objeto ticket es obligatorio.");
  }
  const ticketId = String(ticket.ticket_id ?? ticket.id ?? "").trim();
  if (!ticketId) {
    throw new Error("INVALID_INPUT: ticket_id es obligatorio.");
  }

  const metodoPago = String(ticket.metodo_pago ?? "").trim().toUpperCase();
  if (!["EFECTIVO", "TRANSFERENCIA", "MIXTO"].includes(metodoPago)) {
    throw new Error("INVALID_INPUT: Método de pago no permitido.");
  }

  const totalVenta = Number(ticket.total_venta);
  const montoEfectivo = Number(ticket.monto_efectivo);
  const montoTransferencia = Number(
    ticket.monto_transferencia !== undefined ? ticket.monto_transferencia : (ticket.monto_transf ?? 0)
  );

  if (
    !Number.isFinite(totalVenta) ||
    !Number.isFinite(montoEfectivo) ||
    !Number.isFinite(montoTransferencia) ||
    totalVenta < 0 ||
    montoEfectivo < 0 ||
    montoTransferencia < 0
  ) {
    throw new Error("INVALID_INPUT: Los importes del ticket deben ser números no negativos válidos.");
  }

  if (Math.abs((montoEfectivo + montoTransferencia) - totalVenta) > 0.01) {
    throw new Error("INVALID_INPUT: El monto en efectivo más transferencia debe ser exactamente igual al total.");
  }
  if (metodoPago === "EFECTIVO" && (Math.abs(montoEfectivo - totalVenta) > 0.01 || montoTransferencia !== 0)) {
    throw new Error("INVALID_INPUT: El pago en efectivo no coincide con el método de pago.");
  }
  if (metodoPago === "TRANSFERENCIA" && (Math.abs(montoTransferencia - totalVenta) > 0.01 || montoEfectivo !== 0)) {
    throw new Error("INVALID_INPUT: El pago por transferencia no coincide con el método de pago.");
  }

  if (!Array.isArray(items) || items.length === 0) {
    throw new Error("INVALID_INPUT: La venta debe tener al menos un artículo.");
  }

  const fechaHoraUtc = normalizarFechaHoraUtc(ticket.fecha_hora_utc || ticket.fecha);
  const esNocturno = ticket.es_nocturno ? 1 : 0;

  let sumaSubtotales = 0;
  const itemsNormalizados = items.map((it) => {
    const productoNombre = String(it?.producto ?? it?.producto_nombre ?? "").trim();
    if (!productoNombre) {
      throw new Error("INVALID_INPUT: Cada ítem requiere nombre de producto.");
    }
    const cantidad = Number(it?.cantidad);
    if (!Number.isFinite(cantidad) || cantidad <= 0) {
      throw new Error("INVALID_INPUT: La cantidad de cada ítem debe ser mayor a cero.");
    }
    const precioUnitario = Number(
      it?.precio_unit !== undefined ? it.precio_unit : it?.precio_unitario
    );
    if (!Number.isFinite(precioUnitario) || precioUnitario < 0) {
      throw new Error("INVALID_INPUT: Precio unitario inválido.");
    }
    const subtotalCalc = Math.round(cantidad * precioUnitario);
    if (it?.subtotal !== undefined && Math.abs(Number(it.subtotal) - subtotalCalc) > 0.01) {
      throw new Error("INVALID_INPUT: El subtotal del ítem no coincide con cantidad * precio_unitario.");
    }
    sumaSubtotales += subtotalCalc;

    let costoSnapshot = null;
    if (it?.costo_unitario !== undefined && it?.costo_unitario !== null) {
      const cVal = Number(it.costo_unitario);
      if (!Number.isFinite(cVal) || cVal < 0) {
        throw new Error("INVALID_INPUT: costo_unitario inválido.");
      }
      costoSnapshot = Math.round(cVal);
    }

    return {
      producto_nombre: productoNombre,
      cantidad,
      unidad: String(it?.unidad || "Unidad").trim(),
      precio_unitario: Math.round(precioUnitario),
      subtotal: subtotalCalc,
      metodo_pago: metodoPago,
      costo_snapshot: costoSnapshot,
    };
  });

  return {
    cabecera: {
      id: ticketId,
      fecha_hora_utc: fechaHoraUtc,
      total_venta: Math.round(totalVenta),
      monto_efectivo: Math.round(montoEfectivo),
      monto_transferencia: Math.round(montoTransferencia),
      metodo_pago: metodoPago,
      es_nocturno: esNocturno,
    },
    items: itemsNormalizados,
    sumaSubtotales,
  };
}

export async function registrarVentaIdempotente(client, { ticket, items, usuario, idempotencyKey = null }) {
  const { cabecera, items: detalle, sumaSubtotales } = normalizarPayloadVenta(ticket, items);
  const ticketId = cabecera.id;

  // 1. Comprobación de idempotencia por ID de ticket
  const prev = await client.execute({
    sql: "SELECT id, total_venta, monto_efectivo, monto_transferencia, metodo_pago, fecha_hora_utc, anulado FROM tickets WHERE id = ?;",
    args: [ticketId],
  });
  if (prev.rows.length > 0) {
    const row = prev.rows[0];
    const prevItemsRs = await client.execute({
      sql: "SELECT producto_nombre, cantidad, precio_unitario, subtotal FROM ticket_items WHERE ticket_id = ? ORDER BY id ASC;",
      args: [ticketId],
    });
    const sameHeader =
      Number(row.anulado) === 0 &&
      Math.abs(Number(row.total_venta) - cabecera.total_venta) <= 0.01 &&
      Math.abs(Number(row.monto_efectivo) - cabecera.monto_efectivo) <= 0.01 &&
      Math.abs(Number(row.monto_transferencia) - cabecera.monto_transferencia) <= 0.01 &&
      String(row.metodo_pago) === cabecera.metodo_pago &&
      prevItemsRs.rows.length === detalle.length;

    let sameItems = sameHeader;
    if (sameHeader) {
      const sortKey = (x) =>
        `${String(x.producto_nombre).toLowerCase()}|${Number(x.cantidad).toFixed(4)}|${Number(x.precio_unitario)}|${Number(x.subtotal)}`;
      const a = prevItemsRs.rows.map(sortKey).sort();
      const b = detalle.map(sortKey).sort();
      for (let i = 0; i < a.length; i++) {
        if (a[i] !== b[i]) {
          sameItems = false;
          break;
        }
      }
    }

    if (!sameItems) {
      throw new Error(`CONFLICT: El ticket '${ticketId}' ya existe pero con contenido discordante.`);
    }

    return {
      ticket_id: row.id,
      status: "existente",
      mensaje: "Venta previamente registrada (operación idempotente).",
      total_venta: Number(row.total_venta),
      monto_efectivo: Number(row.monto_efectivo),
      monto_transferencia: Number(row.monto_transferencia),
      monto_transf: Number(row.monto_transferencia),
      metodo_pago: row.metodo_pago,
      fecha_hora_utc: row.fecha_hora_utc,
    };
  }

  if (Math.abs(sumaSubtotales - cabecera.total_venta) > 0.01) {
    throw new Error(
      `INVALID_INPUT: Los subtotales (${sumaSubtotales}) no coinciden con el total del ticket (${cabecera.total_venta}).`
    );
  }

  // Resolver productos activos no ambiguos en catálogo
  const nombresUnicos = [...new Set(detalle.map((i) => i.producto_nombre.toLowerCase()))];
  const placeholders = nombresUnicos.map(() => "?").join(",");
  const prodsRs = await client.execute({
    sql: `SELECT LOWER(nombre) as nombre_lower, COUNT(*) as cnt, MIN(id) as id, MIN(costo) as costo_unitario
          FROM productos
          WHERE activo = 1 AND LOWER(nombre) IN (${placeholders})
          GROUP BY LOWER(nombre);`,
    args: nombresUnicos,
  });
  const prodsMap = new Map();
  for (const r of prodsRs.rows) {
    if (Number(r.cnt) === 1) {
      prodsMap.set(r.nombre_lower, {
        id: Number(r.id),
        costo_unitario: r.costo_unitario !== null && r.costo_unitario !== undefined ? Number(r.costo_unitario) : null,
      });
    }
  }

  const statements = [
    {
      sql: `INSERT INTO tickets (id, fecha_hora_utc, total_venta, monto_efectivo, monto_transferencia, metodo_pago, es_nocturno)
            VALUES (?, ?, ?, ?, ?, ?, ?);`,
      args: [
        cabecera.id,
        cabecera.fecha_hora_utc,
        cabecera.total_venta,
        cabecera.monto_efectivo,
        cabecera.monto_transferencia,
        cabecera.metodo_pago,
        cabecera.es_nocturno,
      ],
    },
  ];

  for (const it of detalle) {
    const prod = prodsMap.get(it.producto_nombre.toLowerCase());
    const prodId = prod ? prod.id : null;
    // Conservar snapshot de costo unitario capturado al agregar al carrito si fue provisto
    const costoUnit =
      it.costo_snapshot !== null && it.costo_snapshot !== undefined
        ? it.costo_snapshot
        : (prod ? prod.costo_unitario : null);

    statements.push({
      sql: `INSERT INTO ticket_items (ticket_id, producto_id, producto_nombre, cantidad, unidad, precio_unitario, subtotal, metodo_pago, costo_unitario)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);`,
      args: [
        cabecera.id,
        prodId,
        it.producto_nombre,
        it.cantidad,
        it.unidad,
        it.precio_unitario,
        it.subtotal,
        it.metodo_pago,
        costoUnit,
      ],
    });
  }

  statements.push({
    sql: `INSERT INTO ventas_auditoria (ticket_id, item_id, accion, motivo, usuario, datos_nuevos_json, creado_en_utc)
          VALUES (?, NULL, 'CREACION', 'Alta inicial de venta', ?, ?, ?);`,
    args: [
      cabecera.id,
      usuario || "sistema",
      JSON.stringify({ cabecera, items: detalle, idempotency_key: idempotencyKey }),
      cabecera.fecha_hora_utc,
    ],
  });

  await client.batch(statements, "write");

  return {
    ticket_id: cabecera.id,
    status: "registrado",
    mensaje: "Venta registrada exitosamente.",
    total_venta: cabecera.total_venta,
    monto_efectivo: cabecera.monto_efectivo,
    monto_transferencia: cabecera.monto_transferencia,
    monto_transf: cabecera.monto_transferencia,
    items_count: detalle.length,
    metodo_pago: cabecera.metodo_pago,
    fecha_hora_utc: cabecera.fecha_hora_utc,
  };
}

export async function listarVentas(
  client,
  { fecha = null, ticket_id = null, metodo_pago = null, busqueda = null, incluir_anuladas = true, limit = 50 } = {}
) {
  let sql = `
    SELECT t.id, t.fecha_hora_utc, t.total_venta, t.monto_efectivo, t.monto_transferencia, t.metodo_pago, t.anulado, t.motivo_anulacion,
           COUNT(ti.id) as cantidad_items,
           GROUP_CONCAT(ti.producto_nombre || ' x' || CAST(ti.cantidad AS TEXT), ', ') as resumen_items
    FROM tickets t
    LEFT JOIN ticket_items ti ON ti.ticket_id = t.id
    WHERE 1=1
  `;
  const args = [];
  if (!incluir_anuladas) {
    sql += " AND t.anulado = 0";
  }
  if (ticket_id) {
    sql += " AND t.id = ?";
    args.push(ticket_id);
  }
  if (metodo_pago && metodo_pago !== "TODOS") {
    sql += " AND t.metodo_pago = ?";
    args.push(metodo_pago.toUpperCase());
  }
  if (fecha) {
    const { desdeUtc, hastaUtc } = obtenerRangoUtcDiaJujuy(fecha);
    sql += " AND t.fecha_hora_utc >= ? AND t.fecha_hora_utc <= ?";
    args.push(desdeUtc, hastaUtc);
  }
  if (busqueda && String(busqueda).trim()) {
    const q = `%${String(busqueda).trim()}%`;
    sql += " AND (t.id LIKE ? OR EXISTS (SELECT 1 FROM ticket_items ti2 WHERE ti2.ticket_id = t.id AND ti2.producto_nombre LIKE ?))";
    args.push(q, q);
  }
  sql += " GROUP BY t.id ORDER BY t.fecha_hora_utc DESC LIMIT ?;";
  args.push(limit);

  const rs = await client.execute({ sql, args });
  return rs.rows.map((r) => ({
    ...r,
    id: r.id,
    ticket_id: r.id,
    fecha: formatearFechaLocalAr(r.fecha_hora_utc),
    fecha_hora_utc: r.fecha_hora_utc,
    total_venta: Number(r.total_venta),
    monto_efectivo: Number(r.monto_efectivo),
    monto_transferencia: Number(r.monto_transferencia),
    monto_transf: Number(r.monto_transferencia),
    metodo_pago: r.metodo_pago,
    cantidad_items: Number(r.cantidad_items || 0),
    anulado: Boolean(r.anulado),
    motivo_anulacion: r.motivo_anulacion || undefined,
    resumen_items: r.resumen_items || "",
  }));
}

export async function obtenerDetalleVenta(client, ticketId) {
  const tRs = await client.execute({ sql: "SELECT * FROM tickets WHERE id = ?;", args: [ticketId] });
  if (tRs.rows.length === 0) return null;

  const t = tRs.rows[0];
  const iRs = await client.execute({
    sql: "SELECT * FROM ticket_items WHERE ticket_id = ? ORDER BY id ASC;",
    args: [ticketId],
  });
  const aRs = await client
    .execute({ sql: "SELECT * FROM ventas_auditoria WHERE ticket_id = ? ORDER BY id ASC;", args: [ticketId] })
    .catch(() => ({ rows: [] }));

  const items = iRs.rows.map((it) => ({
    ...it,
    id: Number(it.id),
    ticket_id: it.ticket_id,
    producto_id: it.producto_id !== null && it.producto_id !== undefined ? Number(it.producto_id) : undefined,
    producto: it.producto_nombre,
    producto_nombre: it.producto_nombre,
    cantidad: Number(it.cantidad),
    unidad: it.unidad || "Unidad",
    precio_unit: Number(it.precio_unitario),
    precio_unitario: Number(it.precio_unitario),
    subtotal: Number(it.subtotal),
    metodo_pago: it.metodo_pago,
    costo_unitario: it.costo_unitario !== null && it.costo_unitario !== undefined ? Number(it.costo_unitario) : null,
  }));

  const ticketObj = {
    ...t,
    id: t.id,
    ticket_id: t.id,
    fecha: formatearFechaLocalAr(t.fecha_hora_utc),
    fecha_hora_utc: t.fecha_hora_utc,
    total_venta: Number(t.total_venta),
    monto_efectivo: Number(t.monto_efectivo),
    monto_transferencia: Number(t.monto_transferencia),
    monto_transf: Number(t.monto_transferencia),
    metodo_pago: t.metodo_pago,
    es_nocturno: Boolean(t.es_nocturno),
    anulado: Boolean(t.anulado),
    motivo_anulacion: t.motivo_anulacion || undefined,
  };

  return {
    ...ticketObj,
    ticket: ticketObj,
    items,
    auditoria: aRs.rows,
  };
}

export async function anularVenta(client, ticketId, usuario, motivo) {
  const existing = await obtenerDetalleVenta(client, ticketId);
  if (!existing) {
    throw new Error(`NOT_FOUND: El ticket '${ticketId}' no existe.`);
  }
  const nowUtc = new Date().toISOString();
  await client.batch([
    {
      sql: "UPDATE tickets SET anulado = 1, motivo_anulacion = ? WHERE id = ?;",
      args: [motivo, ticketId],
    },
    {
      sql: `INSERT INTO ventas_auditoria (ticket_id, item_id, accion, motivo, usuario, creado_en_utc)
            VALUES (?, NULL, 'ANULACION', ?, ?, ?);`,
      args: [ticketId, motivo, usuario, nowUtc],
    },
  ], "write");
  const updated = await obtenerDetalleVenta(client, ticketId);
  return {
    ...updated,
    anulado: true,
    motivo,
  };
}

export async function editarItemVenta(
  client,
  ticketId,
  itemId,
  { cantidad, nueva_cantidad, nuevo_subtotal, precio_unitario, monto_efectivo, monto_transf, monto_transferencia },
  usuario
) {
  const tRs = await client.execute({
    sql: "SELECT id, total_venta, monto_efectivo, monto_transferencia, metodo_pago, anulado FROM tickets WHERE id = ?;",
    args: [ticketId],
  });
  if (tRs.rows.length === 0) {
    throw new Error(`NOT_FOUND: El ticket '${ticketId}' no existe.`);
  }
  const tRow = tRs.rows[0];
  if (Number(tRow.anulado) === 1) {
    throw new Error(`INVALID_INPUT: No se pueden editar ítems de un ticket anulado ('${ticketId}').`);
  }

  const iRs = await client.execute({
    sql: "SELECT id, cantidad, precio_unitario, subtotal, costo_unitario FROM ticket_items WHERE id = ? AND ticket_id = ?;",
    args: [itemId, ticketId],
  });
  if (iRs.rows.length === 0) {
    throw new Error(`NOT_FOUND: El ítem #${itemId} no pertenece al ticket '${ticketId}'.`);
  }
  const iRow = iRs.rows[0];

  const cantRaw = nueva_cantidad !== undefined && nueva_cantidad !== null ? nueva_cantidad : cantidad;
  const cantFinal = cantRaw !== undefined && cantRaw !== null ? Number(cantRaw) : Number(iRow.cantidad);
  if (!Number.isFinite(cantFinal) || cantFinal <= 0) {
    throw new Error("INVALID_INPUT: La cantidad del ítem debe ser mayor a 0.");
  }

  const precioUnitFinal =
    precio_unitario !== undefined && precio_unitario !== null && Number(precio_unitario) > 0
      ? Math.round(Number(precio_unitario))
      : Number(iRow.precio_unitario);

  const subtotalFinal =
    nuevo_subtotal !== undefined && nuevo_subtotal !== null
      ? Math.round(Number(nuevo_subtotal))
      : Math.round(cantFinal * precioUnitFinal);

  if (!Number.isFinite(subtotalFinal) || subtotalFinal < 0) {
    throw new Error("INVALID_INPUT: El subtotal del ítem no puede ser negativo.");
  }

  // Calcular el nuevo total de todos los ítems del ticket
  const allItemsRs = await client.execute({
    sql: "SELECT id, subtotal FROM ticket_items WHERE ticket_id = ?;",
    args: [ticketId],
  });
  let newTotal = 0;
  for (const row of allItemsRs.rows) {
    if (String(row.id) === String(itemId)) {
      newTotal += subtotalFinal;
    } else {
      newTotal += Number(row.subtotal);
    }
  }

  const oldTotal = Number(tRow.total_venta);
  const efvo = Number(tRow.monto_efectivo);
  const transf = Number(tRow.monto_transferencia);
  const diff = oldTotal - newTotal;

  let newEfvo = efvo;
  let newTransf = transf;
  let newMetodo = String(tRow.metodo_pago);

  const tieneDesgloseExplicito =
    monto_efectivo !== undefined || monto_transf !== undefined || monto_transferencia !== undefined;

  if (newTotal <= 0) {
    newTotal = 0;
    newEfvo = 0;
    newTransf = 0;
  } else if (tieneDesgloseExplicito) {
    newEfvo = Number(monto_efectivo !== undefined ? monto_efectivo : efvo);
    newTransf = Number(
      monto_transferencia !== undefined
        ? monto_transferencia
        : (monto_transf !== undefined ? monto_transf : transf)
    );
    if (
      !Number.isFinite(newEfvo) ||
      !Number.isFinite(newTransf) ||
      newEfvo < 0 ||
      newTransf < 0 ||
      Math.round(newEfvo + newTransf) !== newTotal
    ) {
      throw new Error(
        "INVALID_INPUT: Para editar el total de un ticket con pago MIXTO o desglose explícito, monto_efectivo + monto_transf debe igualar al nuevo total."
      );
    }
    newEfvo = Math.round(newEfvo);
    newTransf = Math.round(newTransf);
    if (newEfvo > 0 && newTransf > 0) {
      newMetodo = "MIXTO";
    } else if (newEfvo > 0) {
      newMetodo = "EFECTIVO";
    } else {
      newMetodo = "TRANSFERENCIA";
    }
  } else {
    if (diff > 0) {
      if (efvo >= diff) {
        newEfvo = efvo - diff;
        newTransf = transf;
      } else {
        newEfvo = 0;
        newTransf = Math.max(0, transf - (diff - efvo));
      }
    } else if (diff < 0) {
      if (transf > 0 && efvo === 0) {
        newTransf = transf - diff;
        newEfvo = efvo;
      } else {
        newEfvo = efvo - diff;
        newTransf = transf;
      }
    }

    if (newEfvo + newTransf !== newTotal) {
      newEfvo = Math.max(0, newTotal - newTransf);
    }

    if (newEfvo > 0 && newTransf > 0) {
      newMetodo = "MIXTO";
    } else if (newEfvo > 0) {
      newMetodo = "EFECTIVO";
    } else {
      newMetodo = "TRANSFERENCIA";
    }
  }

  const nowUtc = new Date().toISOString();
  const itemPrev = {
    item_id: Number(itemId),
    cantidad: Number(iRow.cantidad),
    subtotal: Number(iRow.subtotal),
    costo_unitario: iRow.costo_unitario !== null ? Number(iRow.costo_unitario) : null,
  };
  const itemNuevo = {
    item_id: Number(itemId),
    cantidad: cantFinal,
    subtotal: subtotalFinal,
    costo_unitario: iRow.costo_unitario !== null ? Number(iRow.costo_unitario) : null,
  };

  await client.batch([
    {
      sql: "UPDATE ticket_items SET cantidad = ?, precio_unitario = ?, subtotal = ? WHERE id = ? AND ticket_id = ?;",
      args: [cantFinal, precioUnitFinal, subtotalFinal, itemId, ticketId],
    },
    {
      sql: "UPDATE tickets SET total_venta = ?, monto_efectivo = ?, monto_transferencia = ?, metodo_pago = ? WHERE id = ?;",
      args: [newTotal, newEfvo, newTransf, newMetodo, ticketId],
    },
    {
      sql: `INSERT INTO ventas_auditoria (ticket_id, item_id, accion, motivo, usuario, datos_previos_json, datos_nuevos_json, creado_en_utc)
            VALUES (?, ?, 'EDICION_ITEM', 'Corrección de ítem en historial', ?, ?, ?, ?);`,
      args: [
        ticketId,
        itemId,
        usuario || "sistema",
        JSON.stringify(itemPrev),
        JSON.stringify(itemNuevo),
        nowUtc,
      ],
    },
  ], "write");

  return obtenerDetalleVenta(client, ticketId);
}

// --- Módulo Cargas / Servicios ---
function mapearFilaServicio(c, status = undefined) {
  const out = {
    ...c,
    id: Number(c.id),
    fecha: formatearFechaLocalAr(c.fecha_hora_utc),
    fecha_hora_utc: c.fecha_hora_utc,
    servicio: c.servicio,
    tipo_servicio: c.servicio,
    monto_carga: Number(c.monto_carga),
    monto_capital: Number(c.monto_carga),
    monto_adicional: Number(c.monto_adicional),
    cargo_comision: Number(c.monto_adicional),
    total_cobrado: Number(c.total_cobrado),
    monto_total: Number(c.total_cobrado),
    pago_efvo: Number(c.pago_efectivo),
    pago_efectivo: Number(c.pago_efectivo),
    pago_transf: Number(c.pago_transferencia),
    pago_transferencia: Number(c.pago_transferencia),
    anulado: Boolean(c.anulado),
    idempotency_key: c.idempotency_key || undefined,
    plataforma_origen: c.plataforma_origen || null,
    servicio_destino: c.servicio_destino || null,
  };
  if (status) out.status = status;
  return out;
}

export async function registrarCargaIdempotente(client, { servicio, usuario, idempotencyKey = null }) {
  if (!servicio || typeof servicio !== "object") {
    throw new Error("INVALID_INPUT: Datos de carga inválidos.");
  }
  const key = idempotencyKey || servicio.idempotency_key || null;
  const nowUtc = normalizarFechaHoraUtc(servicio.fecha_hora_utc || servicio.fecha);
  const montoCarga = Number(servicio.monto_carga !== undefined ? servicio.monto_carga : servicio.monto_capital);
  const montoAdicional = Number(
    servicio.monto_adicional !== undefined ? servicio.monto_adicional : (servicio.cargo_comision ?? 0)
  );
  if (!Number.isFinite(montoCarga) || montoCarga <= 0 || !Number.isFinite(montoAdicional) || montoAdicional < 0) {
    throw new Error("INVALID_INPUT: Monto de carga o adicional inválido.");
  }
  const totalCobrado = Number(
    servicio.total_cobrado !== undefined
      ? servicio.total_cobrado
      : (servicio.monto_total !== undefined ? servicio.monto_total : montoCarga + montoAdicional)
  );

  let pagoEfvo;
  let pagoTransf;
  if (
    servicio.pago_efvo !== undefined ||
    servicio.pago_efectivo !== undefined ||
    servicio.pago_transf !== undefined ||
    servicio.pago_transferencia !== undefined
  ) {
    pagoEfvo = Number(servicio.pago_efvo !== undefined ? servicio.pago_efvo : (servicio.pago_efectivo ?? 0));
    pagoTransf = Number(
      servicio.pago_transf !== undefined ? servicio.pago_transf : (servicio.pago_transferencia ?? 0)
    );
  } else {
    pagoEfvo = servicio.metodo_pago === "TRANSFERENCIA" ? 0 : totalCobrado;
    pagoTransf = servicio.metodo_pago === "TRANSFERENCIA" ? totalCobrado : 0;
  }

  if (Math.abs((pagoEfvo + pagoTransf) - totalCobrado) > 0.01) {
    throw new Error("INVALID_INPUT: La suma de pago en efectivo y transferencia debe igualar el total cobrado.");
  }

  const tipo = String(servicio.servicio || servicio.tipo_servicio || "").trim();
  if (!tipo) {
    throw new Error("INVALID_INPUT: El nombre del servicio es obligatorio.");
  }

  if (key) {
    const prev = await client.execute({
      sql: "SELECT * FROM cargas_servicios WHERE idempotency_key = ?;",
      args: [key],
    });
    if (prev.rows.length > 0) {
      const row = prev.rows[0];
      if (
        Math.abs(Number(row.total_cobrado) - totalCobrado) > 0.01 ||
        Math.abs(Number(row.monto_carga) - montoCarga) > 0.01 ||
        String(row.servicio).toLowerCase() !== tipo.toLowerCase()
      ) {
        throw new Error("CONFLICT: Mismo idempotency_key con payload discordante");
      }
      return mapearFilaServicio(row, "existente");
    }
  }

  const plataformaOrigen = servicio.plataforma_origen ? String(servicio.plataforma_origen).trim().toUpperCase() : null;
  const servicioDestino = servicio.servicio_destino ? String(servicio.servicio_destino).trim().toUpperCase() : null;

  const res = await client.execute({
    sql: `INSERT INTO cargas_servicios (fecha_hora_utc, servicio, monto_carga, monto_adicional, total_cobrado, pago_efectivo, pago_transferencia, anulado, idempotency_key, plataforma_origen, servicio_destino)
          VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?);`,
    args: [nowUtc, tipo, montoCarga, montoAdicional, totalCobrado, pagoEfvo, pagoTransf, key, plataformaOrigen, servicioDestino],
  });
  const cargaId = Number(res.lastInsertRowid) || 1;

  await client
    .execute({
      sql: `INSERT INTO cargas_servicios_auditoria (carga_id, accion, valores_nuevos, fecha_utc, actor)
          VALUES (?, 'CREAR', ?, ?, ?);`,
      args: [cargaId, JSON.stringify(servicio), nowUtc, usuario || "sistema"],
    })
    .catch(() => {});

  return mapearFilaServicio(
    {
      id: cargaId,
      fecha_hora_utc: nowUtc,
      servicio: tipo,
      monto_carga: montoCarga,
      monto_adicional: montoAdicional,
      total_cobrado: totalCobrado,
      pago_efectivo: pagoEfvo,
      pago_transferencia: pagoTransf,
      anulado: 0,
      idempotency_key: key,
      plataforma_origen: plataformaOrigen,
      servicio_destino: servicioDestino,
    },
    "registrado"
  );
}

export async function listarServicios(client, { fecha = null, tipo = null, incluir_anuladas = false, plataforma_origen = null, servicio_destino = null } = {}) {
  let sql = `
    SELECT id, fecha_hora_utc, servicio, monto_carga, monto_adicional, total_cobrado, pago_efectivo, pago_transferencia, anulado, idempotency_key, plataforma_origen, servicio_destino
    FROM cargas_servicios
    WHERE 1=1
  `;
  const args = [];
  if (!incluir_anuladas) {
    sql += " AND anulado = 0";
  }
  if (tipo) {
    sql += " AND servicio = ?";
    args.push(tipo);
  }
  if (plataforma_origen) {
    sql += " AND plataforma_origen = ?";
    args.push(plataforma_origen);
  }
  if (servicio_destino) {
    sql += " AND servicio_destino = ?";
    args.push(servicio_destino);
  }
  if (fecha) {
    const { desdeUtc, hastaUtc } = obtenerRangoUtcDiaJujuy(fecha);
    sql += " AND fecha_hora_utc >= ? AND fecha_hora_utc <= ?";
    args.push(desdeUtc, hastaUtc);
  }
  sql += " ORDER BY fecha_hora_utc DESC;";

  const rs = await client.execute({ sql, args });
  return rs.rows.map((r) => mapearFilaServicio(r));
}

export async function obtenerResumenServicios(client, { fecha = null } = {}) {
  const { desdeUtc, hastaUtc } = obtenerRangoUtcDiaJujuy(fecha);
  const rs = await client.execute({
    sql: `
      SELECT servicio, monto_carga, monto_adicional, total_cobrado, pago_efectivo, pago_transferencia, anulado
      FROM cargas_servicios
      WHERE fecha_hora_utc >= ? AND fecha_hora_utc <= ?;
    `,
    args: [desdeUtc, hastaUtc],
  });

  let totalOperaciones = 0;
  let totalCobrado = 0;
  let totalCapital = 0;
  let totalAdicional = 0;
  let totalEfectivo = 0;
  let totalTransferencia = 0;
  let capitalPrepay = 0;
  let capitalClaro = 0;
  let capitalSubeMp = 0;
  let operacionesAnuladas = 0;

  for (const r of rs.rows) {
    if (Number(r.anulado) === 1) {
      operacionesAnuladas += 1;
      continue;
    }
    totalOperaciones += 1;
    const cap = Number(r.monto_carga) || 0;
    const adic = Number(r.monto_adicional) || 0;
    const cob = Number(r.total_cobrado) || 0;
    const efv = Number(r.pago_efectivo) || 0;
    const trf = Number(r.pago_transferencia) || 0;

    totalCapital += cap;
    totalAdicional += adic;
    totalCobrado += cob;
    totalEfectivo += efv;
    totalTransferencia += trf;

    const { categoria } = clasificarServicio(r.servicio);
    if (categoria === "CLARO") {
      capitalClaro += cap;
    } else if (categoria === "SUBE_MP") {
      capitalSubeMp += cap;
    } else {
      capitalPrepay += cap;
    }
  }

  return {
    total_operaciones: totalOperaciones,
    total_cobrado: totalCobrado,
    total_capital: totalCapital,
    total_adicional: totalAdicional,
    total_comision: totalAdicional,
    total_efectivo: totalEfectivo,
    total_transferencia: totalTransferencia,
    capital_prepay: capitalPrepay,
    capital_claro: capitalClaro,
    capital_sube_mp: capitalSubeMp,
    operaciones_anuladas: operacionesAnuladas,
  };
}

export async function obtenerCargaPorId(client, cargaId) {
  const rs = await client.execute({ sql: "SELECT * FROM cargas_servicios WHERE id = ?;", args: [cargaId] });
  if (rs.rows.length === 0) return null;
  return mapearFilaServicio(rs.rows[0]);
}

export async function editarCargaServicio(client, cargaId, datos, usuario) {
  const actual = await obtenerCargaPorId(client, cargaId);
  if (!actual) {
    throw new Error(`NOT_FOUND: Carga ${cargaId} no encontrada.`);
  }
  if (actual.anulado) {
    throw new Error("CONFLICT: No se puede editar una carga anulada.");
  }

  const nowUtc = new Date().toISOString();
  const nuevoServicio = String(
    datos.servicio !== undefined
      ? datos.servicio
      : (datos.tipo_servicio !== undefined ? datos.tipo_servicio : actual.servicio)
  ).trim();
  const nuevoMontoCarga = Number(
    datos.monto_carga !== undefined
      ? datos.monto_carga
      : (datos.monto_capital !== undefined ? datos.monto_capital : actual.monto_carga)
  );
  const nuevoAdicional = Number(
    datos.monto_adicional !== undefined
      ? datos.monto_adicional
      : (datos.cargo_comision !== undefined ? datos.cargo_comision : actual.monto_adicional)
  );
  const nuevoTotal = datos.total_cobrado !== undefined || datos.monto_total !== undefined
    ? Number(datos.total_cobrado !== undefined ? datos.total_cobrado : datos.monto_total)
    : nuevoMontoCarga + nuevoAdicional;

  let nuevoEfvo = datos.pago_efvo !== undefined || datos.pago_efectivo !== undefined
    ? Number(datos.pago_efvo !== undefined ? datos.pago_efvo : datos.pago_efectivo)
    : actual.pago_efvo;
  let nuevoTransf = datos.pago_transf !== undefined || datos.pago_transferencia !== undefined
    ? Number(datos.pago_transf !== undefined ? datos.pago_transf : datos.pago_transferencia)
    : actual.pago_transf;

  // Si se modificó el total sin especificar desglose de pago y era pago simple, ajustar proporcionalmente
  if (
    datos.pago_efvo === undefined &&
    datos.pago_efectivo === undefined &&
    datos.pago_transf === undefined &&
    datos.pago_transferencia === undefined &&
    nuevoEfvo + nuevoTransf !== nuevoTotal
  ) {
    if (actual.pago_transf === 0) {
      nuevoEfvo = nuevoTotal;
      nuevoTransf = 0;
    } else if (actual.pago_efvo === 0) {
      nuevoEfvo = 0;
      nuevoTransf = nuevoTotal;
    }
  }

  if (!Number.isInteger(nuevoMontoCarga) || nuevoMontoCarga <= 0) {
    throw new Error("INVALID_INPUT: monto_carga debe ser entero > 0.");
  }
  if (!Number.isInteger(nuevoAdicional) || nuevoAdicional < 0) {
    throw new Error("INVALID_INPUT: monto_adicional debe ser entero >= 0.");
  }
  if (nuevoTotal !== nuevoMontoCarga + nuevoAdicional) {
    throw new Error("INVALID_INPUT: total_cobrado debe ser igual a monto_carga + monto_adicional.");
  }
  if (nuevoEfvo + nuevoTransf !== nuevoTotal) {
    throw new Error("INVALID_INPUT: pago_efvo + pago_transf debe igualar total_cobrado.");
  }

  const nuevaPlat = datos.plataforma_origen !== undefined
    ? (datos.plataforma_origen ? String(datos.plataforma_origen).trim().toUpperCase() : null)
    : (actual.plataforma_origen || null);
  const nuevoDest = datos.servicio_destino !== undefined
    ? (datos.servicio_destino ? String(datos.servicio_destino).trim().toUpperCase() : null)
    : (actual.servicio_destino || null);

  await client.batch([
    {
      sql: `UPDATE cargas_servicios
            SET servicio = ?, monto_carga = ?, monto_adicional = ?, total_cobrado = ?, pago_efectivo = ?, pago_transferencia = ?, plataforma_origen = ?, servicio_destino = ?
            WHERE id = ?;`,
      args: [nuevoServicio, nuevoMontoCarga, nuevoAdicional, nuevoTotal, nuevoEfvo, nuevoTransf, nuevaPlat, nuevoDest, cargaId],
    },
    {
      sql: `INSERT INTO cargas_servicios_auditoria (carga_id, accion, valores_anteriores, valores_nuevos, fecha_utc, actor)
            VALUES (?, 'EDITAR', ?, ?, ?, ?);`,
      args: [cargaId, JSON.stringify(actual), JSON.stringify(datos), nowUtc, usuario || "sistema"],
    }
  ], "write");

  return obtenerCargaPorId(client, cargaId);
}

export async function anularCargaServicio(client, cargaId, usuario, motivo) {
  const nowUtc = new Date().toISOString();
  await client.batch([
    {
      sql: "UPDATE cargas_servicios SET anulado = 1 WHERE id = ?;",
      args: [cargaId],
    },
    {
      sql: `INSERT INTO cargas_servicios_auditoria (carga_id, accion, valores_nuevos, fecha_utc, actor)
            VALUES (?, 'ANULAR', ?, ?, ?);`,
      args: [cargaId, JSON.stringify({ motivo }), nowUtc, usuario || "sistema"],
    }
  ], "write");
  return { id: cargaId, anulado: true, motivo };
}

// --- Módulo Gastos y Cierres ---
function mapearFilaGasto(row) {
  if (!row) return null;
  const montoSalida = Number(row.monto_salida) || 0;
  return {
    id: Number(row.id),
    legacy_row_id: row.legacy_row_id || null,
    fecha_hora_utc: row.fecha_hora_utc,
    tipo: row.tipo,
    categoria: row.tipo,
    detalle: row.detalle,
    concepto: row.detalle,
    monto_salida: montoSalida,
    monto: montoSalida,
    origen_fondos: row.origen_fondos || "Caja diaria",
    metodo_pago: row.metodo_pago,
    anulado: Boolean(row.anulado),
    idempotency_key: row.idempotency_key || null,
  };
}

export async function registrarGastoIdempotente(client, { gasto, usuario, idempotencyKey = null }) {
  const key = (idempotencyKey || gasto.idempotency_key || "").trim() || null;
  const nowUtc = normalizarFechaHoraUtc(gasto.fecha_hora_utc || gasto.fecha);
  const montoRaw = gasto.monto_salida !== undefined ? gasto.monto_salida : gasto.monto;
  const monto = Number(montoRaw);
  const concepto = String(gasto.detalle !== undefined ? gasto.detalle : (gasto.concepto || "")).trim();
  const cat = String(gasto.tipo !== undefined ? gasto.tipo : (gasto.categoria || "General")).trim() || "General";
  const origenFondos = String(gasto.origen_fondos || "Caja diaria").trim() || "Caja diaria";
  const metodo = String(gasto.metodo_pago || "EFECTIVO").trim().toUpperCase();

  if (!concepto) {
    throw new Error("INVALID_INPUT: El detalle del gasto no puede estar vacío.");
  }
  if (!Number.isFinite(monto) || !Number.isInteger(monto) || monto <= 0) {
    throw new Error("INVALID_INPUT: monto_salida debe ser un entero > 0.");
  }
  if (!["EFECTIVO", "TRANSFERENCIA"].includes(metodo)) {
    throw new Error("INVALID_INPUT: metodo_pago debe ser EFECTIVO o TRANSFERENCIA.");
  }

  if (key) {
    const prev = await client.execute({
      sql: "SELECT * FROM gastos_retiros WHERE idempotency_key = ?;",
      args: [key],
    });
    if (prev.rows.length > 0) {
      const row = prev.rows[0];
      if (
        Math.abs(Number(row.monto_salida) - monto) > 0.01 ||
        String(row.tipo) !== cat ||
        String(row.detalle) !== concepto ||
        String(row.metodo_pago) !== metodo
      ) {
        throw new Error("CONFLICT: Mismo idempotency_key con payload discordante");
      }
      return {
        ...mapearFilaGasto(row),
        status: "existente",
      };
    }
  }

  const res = await client.execute({
    sql: `INSERT INTO gastos_retiros (fecha_hora_utc, tipo, detalle, monto_salida, origen_fondos, metodo_pago, anulado, idempotency_key)
          VALUES (?, ?, ?, ?, ?, ?, 0, ?);`,
    args: [nowUtc, cat, concepto, monto, origenFondos, metodo, key],
  });
  const gastoId = Number(res.lastInsertRowid) || 1;

  await client.execute({
    sql: `INSERT INTO gastos_auditoria (gasto_id, accion, valores_nuevos, fecha_utc, actor)
          VALUES (?, 'CREAR', ?, ?, ?);`,
    args: [gastoId, JSON.stringify({ tipo: cat, detalle: concepto, monto_salida: monto, origen_fondos: origenFondos, metodo_pago: metodo }), nowUtc, usuario || "sistema"],
  }).catch(() => {});

  const creado = await obtenerGastoPorId(client, gastoId);
  return {
    ...(creado || {
      id: gastoId,
      fecha_hora_utc: nowUtc,
      tipo: cat,
      categoria: cat,
      detalle: concepto,
      concepto,
      monto_salida: monto,
      monto,
      origen_fondos: origenFondos,
      metodo_pago: metodo,
      anulado: false,
      idempotency_key: key,
    }),
    status: "registrado",
  };
}

export async function listarGastos(client, { fecha = null, tipo = null, categoria = null, metodo_pago = null, incluir_anulados = false, limit = 200 } = {}) {
  let sql = `
    SELECT id, legacy_row_id, fecha_hora_utc, tipo, detalle, monto_salida, origen_fondos, metodo_pago, anulado, idempotency_key
    FROM gastos_retiros
    WHERE 1=1
  `;
  const args = [];
  if (!incluir_anulados) {
    sql += " AND anulado = 0";
  }
  const filtroTipo = tipo || categoria;
  if (filtroTipo) {
    sql += " AND tipo = ?";
    args.push(filtroTipo);
  }
  if (metodo_pago) {
    sql += " AND metodo_pago = ?";
    args.push(String(metodo_pago).toUpperCase());
  }
  if (fecha) {
    const { desdeUtc, hastaUtc } = obtenerRangoUtcDiaJujuy(fecha);
    sql += " AND fecha_hora_utc >= ? AND fecha_hora_utc <= ?";
    args.push(desdeUtc, hastaUtc);
  }
  sql += " ORDER BY fecha_hora_utc DESC, id DESC LIMIT ?;";
  args.push(Number(limit) || 200);

  const rs = await client.execute({ sql, args });
  return rs.rows.map(mapearFilaGasto);
}

export async function obtenerResumenGastos(client, { fecha = null, desde = null, hasta = null } = {}) {
  let sql = `
    SELECT
      COUNT(*) as cantidad_gastos,
      COALESCE(SUM(monto_salida), 0) as total_gastos,
      COALESCE(SUM(CASE WHEN metodo_pago = 'EFECTIVO' THEN monto_salida ELSE 0 END), 0) as total_efectivo,
      COALESCE(SUM(CASE WHEN metodo_pago = 'TRANSFERENCIA' THEN monto_salida ELSE 0 END), 0) as total_transferencia,
      COALESCE(SUM(CASE WHEN tipo = 'Gasto Operativo' THEN monto_salida ELSE 0 END), 0) as total_operativo,
      COALESCE(SUM(CASE WHEN tipo = 'Pago a Proveedor' THEN monto_salida ELSE 0 END), 0) as total_proveedores,
      COALESCE(SUM(CASE WHEN tipo = 'Retiro de Caja' THEN monto_salida ELSE 0 END), 0) as total_retiros,
      COALESCE(SUM(CASE WHEN tipo = 'Otros' THEN monto_salida ELSE 0 END), 0) as total_otros
    FROM gastos_retiros
    WHERE anulado = 0
  `;
  const args = [];
  if (fecha) {
    const { desdeUtc, hastaUtc } = obtenerRangoUtcDiaJujuy(fecha);
    sql += " AND fecha_hora_utc >= ? AND fecha_hora_utc <= ?";
    args.push(desdeUtc, hastaUtc);
  } else {
    if (desde) {
      const { desdeUtc } = obtenerRangoUtcDiaJujuy(desde);
      sql += " AND fecha_hora_utc >= ?";
      args.push(desdeUtc);
    }
    if (hasta) {
      const { hastaUtc } = obtenerRangoUtcDiaJujuy(hasta);
      sql += " AND fecha_hora_utc <= ?";
      args.push(hastaUtc);
    }
  }

  const rs = await client.execute({ sql, args });
  const row = rs.rows[0] || {};
  const cantidadGastos = Number(row.cantidad_gastos) || 0;
  const totalGastos = Number(row.total_gastos) || 0;
  const totalEfectivo = Number(row.total_efectivo) || 0;
  const totalTransferencia = Number(row.total_transferencia) || 0;
  return {
    cantidad_gastos: cantidadGastos,
    total_gastos: totalGastos,
    total_efectivo: totalEfectivo,
    total_transferencia: totalTransferencia,
    total_operativo: Number(row.total_operativo) || 0,
    total_proveedores: Number(row.total_proveedores) || 0,
    total_retiros: Number(row.total_retiros) || 0,
    total_otros: Number(row.total_otros) || 0,
    // Aliases de compatibilidad
    total_operaciones: cantidadGastos,
    gastos_efvo: totalEfectivo,
    gastos_transf: totalTransferencia,
    gastos_total: totalGastos,
  };
}

export async function obtenerGastoPorId(client, gastoId) {
  const rs = await client.execute({ sql: "SELECT * FROM gastos_retiros WHERE id = ?;", args: [gastoId] });
  if (rs.rows.length === 0) return null;
  return mapearFilaGasto(rs.rows[0]);
}

export async function editarGasto(client, gastoId, datos, usuario) {
  const actual = await obtenerGastoPorId(client, gastoId);
  if (!actual) {
    throw new Error(`NOT_FOUND: Gasto ${gastoId} no encontrado.`);
  }
  if (actual.anulado) {
    throw new Error("CONFLICT: No se puede editar un gasto anulado.");
  }

  const nowUtc = new Date().toISOString();
  const updates = [];
  const args = [];

  if (datos.monto_salida !== undefined || datos.monto !== undefined) {
    const nuevoMonto = Number(datos.monto_salida !== undefined ? datos.monto_salida : datos.monto);
    if (!Number.isInteger(nuevoMonto) || nuevoMonto <= 0) {
      throw new Error("INVALID_INPUT: monto_salida debe ser entero > 0.");
    }
    updates.push("monto_salida = ?");
    args.push(nuevoMonto);
  }
  if (datos.detalle !== undefined || datos.concepto !== undefined) {
    const nuevoDetalle = String(datos.detalle !== undefined ? datos.detalle : datos.concepto).trim();
    if (!nuevoDetalle) {
      throw new Error("INVALID_INPUT: detalle no puede estar vacío.");
    }
    updates.push("detalle = ?");
    args.push(nuevoDetalle);
  }
  if (datos.tipo !== undefined || datos.categoria !== undefined) {
    updates.push("tipo = ?");
    args.push(String(datos.tipo !== undefined ? datos.tipo : datos.categoria).trim());
  }
  if (datos.origen_fondos !== undefined) {
    updates.push("origen_fondos = ?");
    args.push(String(datos.origen_fondos).trim());
  }
  if (datos.metodo_pago !== undefined) {
    const nuevoMetodo = String(datos.metodo_pago).trim().toUpperCase();
    if (!["EFECTIVO", "TRANSFERENCIA"].includes(nuevoMetodo)) {
      throw new Error("INVALID_INPUT: metodo_pago inválido.");
    }
    updates.push("metodo_pago = ?");
    args.push(nuevoMetodo);
  }

  if (updates.length > 0) {
    args.push(gastoId);
    await client.batch([
      { sql: `UPDATE gastos_retiros SET ${updates.join(", ")} WHERE id = ?;`, args },
      {
        sql: `INSERT INTO gastos_auditoria (gasto_id, accion, valores_anteriores, valores_nuevos, fecha_utc, actor)
              VALUES (?, 'EDITAR', ?, ?, ?, ?);`,
        args: [gastoId, JSON.stringify(actual), JSON.stringify(datos), nowUtc, usuario || "sistema"],
      }
    ], "write");
  }

  return obtenerGastoPorId(client, gastoId);
}

export async function anularGasto(client, gastoId, usuario, motivo) {
  const nowUtc = new Date().toISOString();
  await client.batch([
    {
      sql: "UPDATE gastos_retiros SET anulado = 1 WHERE id = ?;",
      args: [gastoId],
    },
    {
      sql: `INSERT INTO gastos_auditoria (gasto_id, accion, valores_nuevos, fecha_utc, actor)
            VALUES (?, 'ANULAR', ?, ?, ?);`,
      args: [gastoId, JSON.stringify({ motivo }), nowUtc, usuario || "sistema"],
    }
  ], "write");
  return { id: gastoId, anulado: true, motivo };
}

export async function calcularArqueoDiario(client, fechaStr = null) {
  const { desdeUtc, hastaUtc, fechaJujuy } = obtenerRangoUtcDiaJujuy(fechaStr);

  const [vRs, sRs, gRs, cRs] = await Promise.all([
    client.execute({
      sql: `SELECT
              COALESCE(SUM(monto_efectivo), 0) as efvo,
              COALESCE(SUM(monto_transferencia), 0) as transf
            FROM tickets
            WHERE anulado = 0 AND fecha_hora_utc >= ? AND fecha_hora_utc <= ?;`,
      args: [desdeUtc, hastaUtc],
    }),
    client.execute({
      sql: `SELECT
              servicio, monto_carga, monto_adicional, total_cobrado, pago_efectivo, pago_transferencia
            FROM cargas_servicios
            WHERE anulado = 0 AND fecha_hora_utc >= ? AND fecha_hora_utc <= ?;`,
      args: [desdeUtc, hastaUtc],
    }),
    client.execute({
      sql: `SELECT
              COALESCE(SUM(CASE WHEN metodo_pago = 'EFECTIVO' THEN monto_salida ELSE 0 END), 0) as efvo,
              COALESCE(SUM(CASE WHEN metodo_pago = 'TRANSFERENCIA' THEN monto_salida ELSE 0 END), 0) as transf
            FROM gastos_retiros
            WHERE anulado = 0 AND fecha_hora_utc >= ? AND fecha_hora_utc <= ?;`,
      args: [desdeUtc, hastaUtc],
    }),
    client.execute({
      sql: "SELECT * FROM cierres_diarios WHERE fecha = ?;",
      args: [fechaJujuy],
    }),
  ]);

  const ventasEfvo = Number(vRs.rows[0]?.efvo) || 0;
  const ventasTransf = Number(vRs.rows[0]?.transf) || 0;
  const totalVentas = ventasEfvo + ventasTransf;
  const gananciaEstimadaVentas = Math.round(totalVentas * 0.1);

  let servEfvo = 0;
  let servTransf = 0;
  let capitalPrepay = 0;
  let capitalClaro = 0;
  let capitalSubeMp = 0;
  let adicionalesGanancia = 0;

  for (const r of sRs.rows) {
    const cap = Number(r.monto_carga) || 0;
    const adic = Number(r.monto_adicional) || 0;
    const efv = Number(r.pago_efectivo) || 0;
    const trf = Number(r.pago_transferencia) || 0;

    servEfvo += efv;
    servTransf += trf;
    adicionalesGanancia += adic;

    const { categoria } = clasificarServicio(r.servicio);
    if (categoria === "CLARO") {
      capitalClaro += cap;
    } else if (categoria === "SUBE_MP") {
      capitalSubeMp += cap;
    } else {
      capitalPrepay += cap;
    }
  }

  const totalServicios = servEfvo + servTransf;
  const gastosEfvo = Number(gRs.rows[0]?.efvo) || 0;
  const gastosTransf = Number(gRs.rows[0]?.transf) || 0;
  const gastosTotal = gastosEfvo + gastosTransf;

  const efectivoDisponibleDrugstore = ventasEfvo - gastosEfvo;
  const efectivoEsperado = ventasEfvo + servEfvo - gastosEfvo;
  const totalBanco = ventasTransf + servTransf - capitalSubeMp - gastosTransf;

  const cierreRow = cRs.rows[0] || null;
  const cierreExistente = cierreRow
    ? {
        id: Number(cierreRow.id),
        fecha: cierreRow.fecha,
        efectivo_esperado: Number(cierreRow.efectivo_esperado) || 0,
        efectivo_contado: Number(cierreRow.efectivo_contado) || 0,
        efectivo_real: Number(cierreRow.efectivo_contado) || 0,
        diferencia: Number(cierreRow.diferencia) || 0,
        total_ventas_efvo: Number(cierreRow.total_ventas_efvo) || 0,
        total_ventas_transf: Number(cierreRow.total_ventas_transf) || 0,
        total_servicios_efvo: Number(cierreRow.total_servicios_efvo) || 0,
        total_servicios_transf: Number(cierreRow.total_servicios_transf) || 0,
        total_gastos_efvo: Number(cierreRow.total_gastos_efvo) || 0,
        total_gastos_transf: Number(cierreRow.total_gastos_transf) || 0,
        total_banco: Number(cierreRow.total_banco) || 0,
        observaciones: cierreRow.observaciones || null,
        cerrado_por: cierreRow.cerrado_por,
        cerrado_en_utc: cierreRow.cerrado_en_utc,
        actualizado_en_utc: cierreRow.actualizado_en_utc || null,
      }
    : null;

  return {
    fecha: fechaJujuy,
    ventas_efvo: ventasEfvo,
    ventas_transf: ventasTransf,
    total_ventas: totalVentas,
    ganancia_estimada_ventas: gananciaEstimadaVentas,
    servicios_efvo: servEfvo,
    servicios_transf: servTransf,
    total_servicios: totalServicios,
    capital_prepay: capitalPrepay,
    capital_claro: capitalClaro,
    capital_sube_mp: capitalSubeMp,
    adicionales_ganancia: adicionalesGanancia,
    d_recargas: servTransf,
    gastos_efvo: gastosEfvo,
    gastos_transf: gastosTransf,
    gastos_total: gastosTotal,
    efectivo_disponible_drugstore: efectivoDisponibleDrugstore,
    efectivo_esperado: efectivoEsperado,
    total_banco: totalBanco,
    cierre_existente: cierreExistente,
    // Aliases de compatibilidad con tests del Worker
    ventas_efectivo: ventasEfvo,
    servicios_efectivo: servEfvo,
    gastos_efectivo: gastosEfvo,
    diferencia: cierreExistente ? cierreExistente.diferencia : 0,
  };
}

export async function registrarCierreDiario(client, payload) {
  const fecha = normalizarFechaIsoJujuy(payload.fecha);
  const efectivoContadoRaw = payload.efectivo_contado !== undefined ? payload.efectivo_contado : payload.efectivo_real;
  const efectivoContado = Number(efectivoContadoRaw);
  if (
    !Number.isFinite(efectivoContado) ||
    !Number.isInteger(efectivoContado) ||
    (payload.efectivo_contado !== undefined && efectivoContado < 0)
  ) {
    throw new Error("INVALID_INPUT: efectivo_contado debe ser un entero >= 0.");
  }

  const arqueo = await calcularArqueoDiario(client, fecha);
  const diferencia = efectivoContado - arqueo.efectivo_esperado;
  const nowUtc = new Date().toISOString();
  const cerradoPor = payload.cerrado_por || "sistema";
  const observaciones = payload.observaciones !== undefined && payload.observaciones !== null
    ? String(payload.observaciones)
    : null;

  const existente = await obtenerCierrePorFecha(client, fecha);
  if (existente) {
    await client.execute({
      sql: `UPDATE cierres_diarios
            SET efectivo_esperado = ?, efectivo_contado = ?, diferencia = ?,
                total_ventas_efvo = ?, total_ventas_transf = ?,
                total_servicios_efvo = ?, total_servicios_transf = ?,
                total_gastos_efvo = ?, total_gastos_transf = ?,
                total_banco = ?, observaciones = ?, cerrado_por = ?, actualizado_en_utc = ?
            WHERE fecha = ?;`,
      args: [
        arqueo.efectivo_esperado,
        efectivoContado,
        diferencia,
        arqueo.ventas_efvo,
        arqueo.ventas_transf,
        arqueo.servicios_efvo,
        arqueo.servicios_transf,
        arqueo.gastos_efvo,
        arqueo.gastos_transf,
        arqueo.total_banco,
        observaciones,
        cerradoPor,
        nowUtc,
        fecha,
      ],
    });
  } else {
    await client.execute({
      sql: `INSERT INTO cierres_diarios (
              fecha, efectivo_esperado, efectivo_contado, diferencia,
              total_ventas_efvo, total_ventas_transf,
              total_servicios_efvo, total_servicios_transf,
              total_gastos_efvo, total_gastos_transf,
              total_banco, observaciones, cerrado_por, cerrado_en_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);`,
      args: [
        fecha,
        arqueo.efectivo_esperado,
        efectivoContado,
        diferencia,
        arqueo.ventas_efvo,
        arqueo.ventas_transf,
        arqueo.servicios_efvo,
        arqueo.servicios_transf,
        arqueo.gastos_efvo,
        arqueo.gastos_transf,
        arqueo.total_banco,
        observaciones,
        cerradoPor,
        nowUtc,
      ],
    });
  }

  return obtenerCierrePorFecha(client, fecha);
}

export async function listarCierres(client, limit = 60) {
  const rs = await client.execute({
    sql: "SELECT * FROM cierres_diarios ORDER BY fecha DESC LIMIT ?;",
    args: [Number(limit) || 60],
  });
  return rs.rows.map((c) => ({
    id: Number(c.id),
    fecha: c.fecha,
    efectivo_esperado: Number(c.efectivo_esperado) || 0,
    efectivo_contado: Number(c.efectivo_contado) || 0,
    efectivo_real: Number(c.efectivo_contado) || 0,
    diferencia: Number(c.diferencia) || 0,
    total_ventas_efvo: Number(c.total_ventas_efvo) || 0,
    total_ventas_transf: Number(c.total_ventas_transf) || 0,
    total_servicios_efvo: Number(c.total_servicios_efvo) || 0,
    total_servicios_transf: Number(c.total_servicios_transf) || 0,
    total_gastos_efvo: Number(c.total_gastos_efvo) || 0,
    total_gastos_transf: Number(c.total_gastos_transf) || 0,
    total_banco: Number(c.total_banco) || 0,
    observaciones: c.observaciones || null,
    cerrado_por: c.cerrado_por,
    cerrado_en_utc: c.cerrado_en_utc,
    actualizado_en_utc: c.actualizado_en_utc || null,
  }));
}

export async function obtenerCierrePorFecha(client, fecha) {
  const rs = await client.execute({
    sql: "SELECT * FROM cierres_diarios WHERE fecha = ?;",
    args: [fecha],
  });
  if (rs.rows.length === 0) return null;
  const c = rs.rows[0];
  return {
    id: Number(c.id),
    fecha: c.fecha,
    efectivo_esperado: Number(c.efectivo_esperado) || 0,
    efectivo_contado: Number(c.efectivo_contado) || 0,
    efectivo_real: Number(c.efectivo_contado) || 0,
    diferencia: Number(c.diferencia) || 0,
    total_ventas_efvo: Number(c.total_ventas_efvo) || 0,
    total_ventas_transf: Number(c.total_ventas_transf) || 0,
    total_servicios_efvo: Number(c.total_servicios_efvo) || 0,
    total_servicios_transf: Number(c.total_servicios_transf) || 0,
    total_gastos_efvo: Number(c.total_gastos_efvo) || 0,
    total_gastos_transf: Number(c.total_gastos_transf) || 0,
    total_banco: Number(c.total_banco) || 0,
    observaciones: c.observaciones || null,
    cerrado_por: c.cerrado_por,
    cerrado_en_utc: c.cerrado_en_utc,
    actualizado_en_utc: c.actualizado_en_utc || null,
  };
}

// --- Módulo Preventistas ---
export async function listarProveedores(client) {
  const rs = await client.execute(
    "SELECT DISTINCT proveedor as nombre FROM productos WHERE activo = 1 AND proveedor IS NOT NULL AND TRIM(proveedor) != '' ORDER BY proveedor;"
  );
  return rs.rows.map((r) => String(r.nombre));
}

export async function listarProductosPorProveedor(client, proveedorNombre, incluirInactivos = false) {
  let sql = `
    SELECT id, id as id_producto, nombre, categoria, proveedor, unidad,
           precio_dia as precio, costo as costo_unitario, margen_ratio, activo
    FROM productos
    WHERE proveedor = ?
  `;
  if (!incluirInactivos) {
    sql += " AND activo = 1";
  }
  sql += " ORDER BY nombre;";
  const rs = await client.execute({
    sql,
    args: [proveedorNombre],
  });
  return rs.rows.map((row) => ({
    id: Number(row.id),
    id_producto: Number(row.id_producto),
    nombre: row.nombre,
    categoria: row.categoria,
    proveedor: row.proveedor,
    unidad: row.unidad,
    precio: Number(row.precio),
    precio_dia: Number(row.precio),
    precio_venta: Number(row.precio),
    costo: Number(row.costo_unitario),
    costo_unitario: Number(row.costo_unitario),
    margen_ratio: Number(row.margen_ratio || 0),
    activo: Number(row.activo),
  }));
}

export async function altaRapidaProductoPreventista(client, { proveedor, nombre, categoria, unidad, costo, costo_unitario, precio_venta, precio }, usuario) {
  const nowUtc = new Date().toISOString();
  const costoFinal = Number(costo !== undefined ? costo : (costo_unitario || 0));
  const precioFinal = Number(precio_venta !== undefined ? precio_venta : (precio || 0));
  const catFinal = categoria || "Otro";
  const unidadFinal = unidad || "Unidad";

  const res = await client.execute({
    sql: `INSERT INTO productos (nombre, categoria, proveedor, unidad, costo, precio_dia, activo, fecha_actualizacion)
          VALUES (?, ?, ?, ?, ?, ?, 1, ?);`,
    args: [nombre, catFinal, proveedor, unidadFinal, costoFinal, precioFinal, nowUtc],
  });
  const prodId = Number(res.lastInsertRowid) || 1;

  await client.execute({
    sql: `INSERT INTO productos_auditoria (producto_id, accion, valores_nuevos, fecha_utc, actor)
          VALUES (?, 'CREAR', ?, ?, ?);`,
    args: [prodId, JSON.stringify({ nombre, proveedor, costo: costoFinal, precio_dia: precioFinal }), nowUtc, usuario || "sistema"],
  }).catch(() => {});

  return {
    id: prodId,
    id_producto: prodId,
    nombre,
    categoria: catFinal,
    proveedor,
    unidad: unidadFinal,
    precio: precioFinal,
    precio_dia: precioFinal,
    precio_venta: precioFinal,
    costo: costoFinal,
    costo_unitario: costoFinal,
    margen_ratio: costoFinal > 0 ? Number(((precioFinal - costoFinal) / costoFinal).toFixed(4)) : 0,
    activo: 1,
  };
}

export async function actualizarPreciosPreventista(client, proveedor, cambios, usuario) {
  const nowUtc = new Date().toISOString();
  const stmts = [];

  for (const c of cambios) {
    const updates = ["fecha_actualizacion = ?"];
    const args = [nowUtc];

    if (c.nuevo_costo !== undefined || c.costo !== undefined || c.costo_unitario !== undefined) {
      updates.push("costo = ?");
      args.push(
        c.nuevo_costo !== undefined
          ? c.nuevo_costo
          : (c.costo !== undefined ? c.costo : c.costo_unitario)
      );
    }
    if (c.nuevo_precio !== undefined || c.precio_venta !== undefined || c.precio !== undefined) {
      updates.push("precio_dia = ?");
      args.push(
        c.nuevo_precio !== undefined
          ? c.nuevo_precio
          : (c.precio_venta !== undefined ? c.precio_venta : c.precio)
      );
    }

    const prodId = c.producto_id || c.id_producto || c.id;
    args.push(prodId);
    args.push(proveedor);

    stmts.push({
      sql: `UPDATE productos SET ${updates.join(", ")} WHERE id = ? AND proveedor = ?;`,
      args,
    });
  }

  if (stmts.length > 0) {
    await client.batch(stmts, "write");
  }

  return { proveedor, actualizados: cambios.length, items: [] };
}

export async function crearPedidoPreventista(client, { id, fecha, proveedor_id, proveedor, observaciones, items, usuario }) {
  const pId = id || `ped-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
  const nowUtc = new Date().toISOString();
  const fechaPedido = fecha || normalizarFechaIsoJujuy(null);
  const provNombre = String(proveedor || proveedor_id || "").trim();
  if (!provNombre) {
    throw new Error("INVALID_INPUT: proveedor es obligatorio.");
  }
  if (!Array.isArray(items) || items.length === 0) {
    throw new Error("INVALID_INPUT: El pedido debe contener al menos un ítem.");
  }

  let totalEstimado = 0;
  const itemsNormalizados = [];
  for (const it of items) {
    const cantidad = Number(it.cantidad);
    const costoUnit = Number(
      it.costo_unitario_estimado !== undefined
        ? it.costo_unitario_estimado
        : (it.costo_unitario !== undefined ? it.costo_unitario : (it.costo || 0))
    );
    const sub = Math.round(cantidad * costoUnit);
    totalEstimado += sub;
    itemsNormalizados.push({
      producto_id: it.producto_id || null,
      producto_nombre: it.producto_nombre || it.nombre || "Item",
      cantidad,
      costo_unitario: costoUnit,
      subtotal: sub,
    });
  }

  const obs = observaciones !== undefined && observaciones !== null
    ? String(observaciones)
    : "Pedido generado desde POS";

  const stmts = [
    {
      sql: `INSERT INTO pedidos_proveedor (id, fecha, proveedor, estado, total, observaciones, creado_en_utc, actualizado_en_utc)
            VALUES (?, ?, ?, 'PENDIENTE', ?, ?, ?, ?);`,
      args: [pId, fechaPedido, provNombre, totalEstimado, obs, nowUtc, nowUtc],
    }
  ];

  for (const it of itemsNormalizados) {
    stmts.push({
      sql: `INSERT INTO pedido_items (pedido_id, producto_id, producto_nombre, cantidad, costo_unitario, subtotal)
            VALUES (?, ?, ?, ?, ?, ?);`,
      args: [pId, it.producto_id, it.producto_nombre, it.cantidad, it.costo_unitario, it.subtotal],
    });
  }

  stmts.push({
    sql: `INSERT INTO pedidos_auditoria (pedido_id, accion, valores_anteriores, valores_nuevos, fecha_utc, actor)
          VALUES (?, 'CREAR_PEDIDO', NULL, ?, ?, ?);`,
    args: [pId, JSON.stringify({ proveedor: provNombre, total: totalEstimado, items_count: itemsNormalizados.length }), nowUtc, usuario || "sistema"],
  });

  await client.batch(stmts, "write");
  return obtenerDetallePedido(client, pId);
}

export async function listarPedidosPreventista(client, { proveedor = null, estado = null, limit = 100 } = {}) {
  let sql = "SELECT * FROM pedidos_proveedor WHERE 1=1";
  const args = [];
  if (proveedor) {
    sql += " AND proveedor = ?";
    args.push(proveedor);
  }
  if (estado) {
    sql += " AND estado = ?";
    args.push(estado);
  }
  sql += " ORDER BY fecha DESC, creado_en_utc DESC LIMIT ?;";
  args.push(Number(limit) || 100);
  const rs = await client.execute({ sql, args });
  return rs.rows.map((p) => ({
    id: p.id,
    fecha: p.fecha,
    proveedor: p.proveedor,
    estado: p.estado,
    total: Number(p.total) || 0,
    total_estimado: Number(p.total) || 0,
    observaciones: p.observaciones || null,
    creado_en_utc: p.creado_en_utc,
    actualizado_en_utc: p.actualizado_en_utc,
  }));
}

export async function obtenerDetallePedido(client, pedidoId) {
  const pRs = await client.execute({ sql: "SELECT * FROM pedidos_proveedor WHERE id = ?;", args: [pedidoId] });
  if (pRs.rows.length === 0) return null;
  const p = pRs.rows[0];
  const itemsRs = await client.execute({
    sql: "SELECT * FROM pedido_items WHERE pedido_id = ? ORDER BY id;",
    args: [pedidoId],
  });
  const items = itemsRs.rows.map((it) => ({
    id: Number(it.id),
    pedido_id: it.pedido_id,
    producto_id: it.producto_id !== null ? Number(it.producto_id) : null,
    producto_nombre: it.producto_nombre,
    cantidad: Number(it.cantidad),
    costo_unitario: Number(it.costo_unitario),
    subtotal: Number(it.subtotal),
  }));
  const cabecera = {
    id: p.id,
    fecha: p.fecha,
    proveedor: p.proveedor,
    estado: p.estado,
    total: Number(p.total) || 0,
    total_estimado: Number(p.total) || 0,
    observaciones: p.observaciones || null,
    creado_en_utc: p.creado_en_utc,
    actualizado_en_utc: p.actualizado_en_utc,
  };
  return {
    ...cabecera,
    items,
    pedido: cabecera,
  };
}

export async function cambiarEstadoPedido(client, pedidoId, nuevoEstado, observaciones, usuario) {
  const nowUtc = new Date().toISOString();
  await client.batch([
    {
      sql: "UPDATE pedidos_proveedor SET estado = ?, observaciones = COALESCE(?, observaciones), actualizado_en_utc = ? WHERE id = ?;",
      args: [nuevoEstado, observaciones !== undefined ? observaciones : null, nowUtc, pedidoId],
    },
    {
      sql: `INSERT INTO pedidos_auditoria (pedido_id, accion, valores_anteriores, valores_nuevos, fecha_utc, actor)
            VALUES (?, 'CAMBIAR_ESTADO', NULL, ?, ?, ?);`,
      args: [pedidoId, JSON.stringify({ nuevoEstado, observaciones }), nowUtc, usuario || "sistema"],
    }
  ], "write");
  const actualizado = await obtenerDetallePedido(client, pedidoId);
  return actualizado || { id: pedidoId, estado: nuevoEstado, observaciones, actualizado_por: usuario };
}

// --- Módulo Dashboard Financiero ---
export async function calcularMetricasDashboard(client, { desde = null, hasta = null } = {}) {
  let filtroTickets = "";
  let filtroFechaSimple = "";
  let filtroCierres = "";
  const argsTickets = [];
  const argsSimple = [];
  const argsCierres = [];

  if (desde) {
    const { desdeUtc } = obtenerRangoUtcDiaJujuy(desde);
    filtroTickets += " AND t.fecha_hora_utc >= ?";
    filtroFechaSimple += " AND fecha_hora_utc >= ?";
    filtroCierres += " AND fecha >= ?";
    argsTickets.push(desdeUtc);
    argsSimple.push(desdeUtc);
    argsCierres.push(desde);
  }
  if (hasta) {
    const { hastaUtc } = obtenerRangoUtcDiaJujuy(hasta);
    filtroTickets += " AND t.fecha_hora_utc <= ?";
    filtroFechaSimple += " AND fecha_hora_utc <= ?";
    filtroCierres += " AND fecha <= ?";
    argsTickets.push(hastaUtc);
    argsSimple.push(hastaUtc);
    argsCierres.push(hasta);
  }

  // Caja A: Ventas de productos con costo congelado en ticket_items (fallback a productos.costo solo si NULL)
  const [rsTicketsHeader, rsTicketItems, rsServicios, rsGastos, rsCierres] = await Promise.all([
    client.execute({
      sql: `SELECT
              COUNT(*) as cantidad_tickets,
              COALESCE(SUM(t.total_venta), 0) as total_ventas,
              COALESCE(SUM(t.monto_efectivo), 0) as ventas_efectivo,
              COALESCE(SUM(t.monto_transferencia), 0) as ventas_transferencia
            FROM tickets t
            WHERE t.anulado = 0 ${filtroTickets};`,
      args: argsTickets,
    }),
    client.execute({
      sql: `SELECT
              COALESCE(SUM(ti.subtotal), 0) as ingresos_items,
              COALESCE(SUM(CAST(ROUND(ti.cantidad * COALESCE(ti.costo_unitario, p.costo, 0)) AS INTEGER)), 0) as costo_estimado,
              SUM(CASE WHEN ti.costo_unitario IS NULL AND p.id IS NULL THEN 1 ELSE 0 END) as items_sin_costo
            FROM ticket_items ti
            JOIN tickets t ON t.id = ti.ticket_id
            LEFT JOIN productos p ON p.id = ti.producto_id
            WHERE t.anulado = 0 ${filtroTickets};`,
      args: argsTickets,
    }),
    client.execute({
      sql: `SELECT servicio, monto_carga, monto_adicional, total_cobrado, pago_efectivo, pago_transferencia
            FROM cargas_servicios
            WHERE anulado = 0 ${filtroFechaSimple};`,
      args: argsSimple,
    }),
    client.execute({
      sql: `SELECT
              COUNT(*) as cantidad_movimientos,
              COALESCE(SUM(monto_salida), 0) as total_salidas,
              COALESCE(SUM(CASE WHEN metodo_pago = 'EFECTIVO' THEN monto_salida ELSE 0 END), 0) as salidas_efectivo,
              COALESCE(SUM(CASE WHEN metodo_pago = 'TRANSFERENCIA' THEN monto_salida ELSE 0 END), 0) as salidas_transferencia,
              COALESCE(SUM(CASE WHEN tipo = 'Gasto Operativo' THEN monto_salida ELSE 0 END), 0) as gastos_operativos,
              COALESCE(SUM(CASE WHEN tipo = 'Pago a Proveedor' THEN monto_salida ELSE 0 END), 0) as pagos_proveedores,
              COALESCE(SUM(CASE WHEN tipo = 'Retiro de Caja' THEN monto_salida ELSE 0 END), 0) as retiros_caja,
              COALESCE(SUM(CASE WHEN tipo = 'Otros' THEN monto_salida ELSE 0 END), 0) as otros_gastos
            FROM gastos_retiros
            WHERE anulado = 0 ${filtroFechaSimple};`,
      args: argsSimple,
    }),
    client.execute({
      sql: `SELECT * FROM cierres_diarios WHERE 1=1 ${filtroCierres} ORDER BY fecha DESC LIMIT 60;`,
      args: argsCierres,
    }),
  ]);

  const th = rsTicketsHeader.rows[0] || {};
  const ti = rsTicketItems.rows[0] || {};
  const gs = rsGastos.rows[0] || {};

  const cantidadTickets = Number(th.cantidad_tickets) || 0;
  const ventasBrutas = Number(th.total_ventas) || 0;
  const ventasEfectivo = Number(th.ventas_efectivo) || 0;
  const ventasTransferencia = Number(th.ventas_transferencia) || 0;
  const costoMercaderia = Number(ti.costo_estimado) || 0;
  const gananciaBrutaProductos = ventasBrutas - costoMercaderia;
  const margenBrutoPct = ventasBrutas > 0 ? Number(((gananciaBrutaProductos / ventasBrutas) * 100).toFixed(1)) : 0;
  const itemsSinCosto = Number(ti.items_sin_costo) || 0;

  let cantidadCargas = 0;
  let capitalCargado = 0;
  let gananciaComisiones = 0;
  let totalCobradoServicios = 0;
  let serviciosEfectivo = 0;
  let serviciosTransferencia = 0;

  let bTotal = 0;
  let bEfvo = 0;
  let bTransf = 0;
  let bCount = 0;

  let cEfvo = 0;
  let cTransf = 0;

  let eTotal = 0;
  let eEfvo = 0;
  let eTransf = 0;
  let eCount = 0;

  let subeMpCapital = 0;

  for (const r of rsServicios.rows) {
    cantidadCargas += 1;
    const cap = Number(r.monto_carga) || 0;
    const adic = Number(r.monto_adicional) || 0;
    const cob = Number(r.total_cobrado) || 0;
    const efv = Number(r.pago_efectivo) || 0;
    const trf = Number(r.pago_transferencia) || 0;

    capitalCargado += cap;
    gananciaComisiones += adic;
    totalCobradoServicios += cob;
    serviciosEfectivo += efv;
    serviciosTransferencia += trf;

    const capTransf = Math.min(trf, cap);
    const capEfvo = Math.max(0, cap - capTransf);
    const adicTransf = Math.max(0, trf - cap);
    const adicEfvo = Math.max(0, adic - adicTransf);

    cEfvo += adicEfvo;
    cTransf += adicTransf;

    const { categoria } = clasificarServicio(r.servicio);
    if (categoria === "CLARO") {
      eTotal += cap;
      eEfvo += capEfvo;
      eTransf += capTransf;
      eCount += 1;
    } else if (categoria === "SUBE_MP") {
      subeMpCapital += cap;
    } else {
      bTotal += cap;
      bEfvo += capEfvo;
      bTransf += capTransf;
      bCount += 1;
    }
  }

  const cantidadGastos = Number(gs.cantidad_movimientos) || 0;
  const totalSalidas = Number(gs.total_salidas) || 0;
  const salidasEfectivo = Number(gs.salidas_efectivo) || 0;
  const salidasTransferencia = Number(gs.salidas_transferencia) || 0;
  const gastosOperativos = Number(gs.gastos_operativos) || 0;
  const pagosProveedores = Number(gs.pagos_proveedores) || 0;
  const retirosCaja = Number(gs.retiros_caja) || 0;
  const otrosGastos = Number(gs.otros_gastos) || 0;

  const gananciaBrutaTotal = gananciaBrutaProductos + gananciaComisiones;
  const resultadoOperativoNeto = gananciaBrutaTotal - gastosOperativos;

  const entradasEfectivo = ventasEfectivo + serviciosEfectivo;
  const efectivoNetoEsperado = entradasEfectivo - salidasEfectivo;

  const entradasBanco = ventasTransferencia + serviciosTransferencia;
  const saldoNetoBanco = entradasBanco - salidasTransferencia;
  const totalBancoNeto = ventasTransferencia + serviciosTransferencia - subeMpCapital - salidasTransferencia;

  const cierresList = rsCierres.rows.map((c) => ({
    id: Number(c.id),
    fecha: c.fecha,
    efectivo_esperado: Number(c.efectivo_esperado) || 0,
    efectivo_contado: Number(c.efectivo_contado) || 0,
    diferencia: Number(c.diferencia) || 0,
    total_ventas_efvo: Number(c.total_ventas_efvo) || 0,
    total_ventas_transf: Number(c.total_ventas_transf) || 0,
    total_servicios_efvo: Number(c.total_servicios_efvo) || 0,
    total_servicios_transf: Number(c.total_servicios_transf) || 0,
    total_gastos_efvo: Number(c.total_gastos_efvo) || 0,
    total_gastos_transf: Number(c.total_gastos_transf) || 0,
    total_banco: Number(c.total_banco) || 0,
    observaciones: c.observaciones || null,
    cerrado_por: c.cerrado_por,
    cerrado_en_utc: c.cerrado_en_utc,
  }));

  let sumaContadoCierres = 0;
  let sumaEsperadoCierres = 0;
  let diferenciaAcumuladaCierres = 0;
  for (const c of cierresList) {
    sumaContadoCierres += c.efectivo_contado;
    sumaEsperadoCierres += c.efectivo_esperado;
    diferenciaAcumuladaCierres += c.diferencia;
  }

  const hoyJujuy = normalizarFechaIsoJujuy(null);

  return {
    periodo_tipo: "personalizado",
    desde: desde || hoyJujuy,
    hasta: hasta || hoyJujuy,
    esquema_completo: true,
    tablas_faltantes: [],
    advertencia_esquema: null,
    caja_a: {
      nombre: "Caja A — Ventas de Productos",
      total_ventas: ventasBrutas,
      ventas_efvo: ventasEfectivo,
      ventas_transf: ventasTransferencia,
      efectivo_disponible_drugstore: ventasEfectivo,
      ganancia_estimada_ventas: Math.round(ventasBrutas * 0.1),
      es_estimacion_heredada: true,
      cantidad_tickets: cantidadTickets,
      ganancia_bruta_real: cantidadTickets > 0 ? gananciaBrutaProductos : null,
      costo_historico_total: costoMercaderia,
      ventas_con_costo_completo: itemsSinCosto === 0 ? cantidadTickets : Math.max(0, cantidadTickets - itemsSinCosto),
      ventas_con_costo_incompleto: itemsSinCosto > 0 ? 1 : 0,
      items_con_costo_desconocido: itemsSinCosto,
      margen_bruto_porcentaje: cantidadTickets > 0 ? margenBrutoPct : null,
      tiene_costos_completos: cantidadTickets > 0 && itemsSinCosto === 0,
      // Alias de compatibilidad
      ventas_brutas: ventasBrutas,
      costo_mercaderia_vendida: costoMercaderia,
      ganancia_bruta_productos: gananciaBrutaProductos,
      margen_bruto_pct: margenBrutoPct,
      cobrado_efectivo: ventasEfectivo,
      cobrado_transferencia: ventasTransferencia,
      items_sin_costo_catalogo: itemsSinCosto,
      nota_metodologica: "Costo histórico congelado al momento de cada venta (ticket_items.costo_unitario).",
    },
    caja_b: {
      nombre: "Caja B — Servicios y Cargas Virtuales",
      capital_total: bTotal,
      capital_efvo: bEfvo,
      capital_transf: bTransf,
      cantidad_operaciones: bCount,
      // Alias de compatibilidad
      cantidad_cargas: cantidadCargas,
      capital_cargado: capitalCargado,
      ganancia_comisiones: gananciaComisiones,
      total_cobrado: totalCobradoServicios,
      cobrado_efectivo: serviciosEfectivo,
      cobrado_transferencia: serviciosTransferencia,
    },
    caja_c: {
      nombre: "Caja C — Resultado Operativo del Negocio",
      ganancia_adicional_total: gananciaComisiones,
      adicional_efvo: cEfvo,
      adicional_transf: cTransf,
      cantidad_operaciones: cantidadCargas,
      // Alias de compatibilidad
      ganancia_bruta_productos: gananciaBrutaProductos,
      ganancia_comisiones_servicios: gananciaComisiones,
      ganancia_bruta_total: gananciaBrutaTotal,
      gastos_operativos: gastosOperativos,
      resultado_operativo_neto: resultadoOperativoNeto,
      pagos_proveedores_informativo: pagosProveedores,
      retiros_caja_informativo: retirosCaja,
      otros_egresos_informativo: otrosGastos,
    },
    caja_d: {
      nombre: "Caja D — Efectivo Físico en Cajón / Banco",
      ventas_transf: ventasTransferencia,
      recargas_transf: serviciosTransferencia,
      sube_mp_capital: subeMpCapital,
      gastos_transf: salidasTransferencia,
      total_banco_neto: totalBancoNeto,
      // Alias de compatibilidad
      entradas_ventas_efvo: ventasEfectivo,
      entradas_servicios_efvo: serviciosEfectivo,
      total_entradas_efvo: entradasEfectivo,
      salidas_gastos_efvo: salidasEfectivo,
      efectivo_neto_esperado: efectivoNetoEsperado,
    },
    caja_e: {
      nombre: "Caja E — Claro (Solo Capital) / Banco",
      capital_total: eTotal,
      capital_efvo: eEfvo,
      capital_transf: eTransf,
      cantidad_operaciones: eCount,
      // Alias de compatibilidad
      entradas_ventas_transf: ventasTransferencia,
      entradas_servicios_transf: serviciosTransferencia,
      total_entradas_transf: entradasBanco,
      salidas_gastos_transf: salidasTransferencia,
      saldo_neto_banco: saldoNetoBanco,
    },
    gastos: {
      gastos_total: totalSalidas,
      gastos_efvo: salidasEfectivo,
      gastos_transf: salidasTransferencia,
      cantidad_gastos: cantidadGastos,
      // Alias de compatibilidad
      cantidad_movimientos: cantidadGastos,
      total_salidas: totalSalidas,
      salidas_efectivo: salidasEfectivo,
      salidas_transferencia: salidasTransferencia,
      gastos_operativos: gastosOperativos,
      pagos_proveedores: pagosProveedores,
      retiros_caja: retirosCaja,
      otros_gastos: otrosGastos,
    },
    conciliacion_cajon: {
      ventas_efvo: ventasEfectivo,
      servicios_efvo: serviciosEfectivo,
      gastos_efvo: salidasEfectivo,
      d_recargas: serviciosTransferencia,
      efectivo_esperado_cajon: efectivoNetoEsperado,
      // Alias de compatibilidad
      cantidad_cierres_registrados: cierresList.length,
      ultimo_cierre: cierresList[0] || null,
      suma_efectivo_esperado_en_cierres: sumaEsperadoCierres,
      suma_efectivo_contado_en_cierres: sumaContadoCierres,
      diferencia_acumulada_cierres: diferenciaAcumuladaCierres,
    },
    serie_temporal: [],
    cierres: cierresList,
  };
}

// --- Módulo System Readiness ---
const MIGRACIONES_ESPERADAS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11];
const TABLAS_REQUERIDAS_READINESS = [
  "_schema_migrations",
  "productos",
  "productos_auditoria",
  "tickets",
  "ticket_items",
  "ventas_auditoria",
  "cargas_servicios",
  "cargas_servicios_auditoria",
  "gastos_retiros",
  "gastos_auditoria",
  "cierres_diarios",
  "pedidos_proveedor",
  "pedido_items",
  "pedidos_auditoria",
  "auth_scram_challenges",
];

export async function comprobarReadinessEsquema(client) {
  const masterRs = await client.execute(
    "SELECT type, name FROM sqlite_master WHERE type IN ('table', 'index') AND name NOT LIKE 'sqlite_%';"
  );
  const tablasPresentes = new Set();
  const indicesPresentes = new Set();
  for (const r of masterRs.rows) {
    if (r.type === "table") tablasPresentes.add(String(r.name));
    if (r.type === "index") indicesPresentes.add(String(r.name));
  }

  let versiones = [];
  if (tablasPresentes.has("_schema_migrations")) {
    const rs = await client.execute("SELECT version, nombre FROM _schema_migrations ORDER BY version;");
    versiones = rs.rows.map((r) => Number(r.version));
  }

  const migracionesPendientes = MIGRACIONES_ESPERADAS.filter((v) => !versiones.includes(v));
  const tablasFaltantes = TABLAS_REQUERIDAS_READINESS.filter((t) => !tablasPresentes.has(t));
  const inconsistencias = [];

  // Verificar esquema real de auth_scram_challenges (migración 011)
  if (!tablasPresentes.has("auth_scram_challenges")) {
    inconsistencias.push("Tabla requerida 'auth_scram_challenges' (migración 011) no existe.");
  } else {
    const infoRs = await client.execute("PRAGMA table_info('auth_scram_challenges');");
    const cols = new Set(infoRs.rows.map((c) => String(c.name)));
    for (const colReq of ["jti", "exp", "used", "creado_en_utc"]) {
      if (!cols.has(colReq)) {
        inconsistencias.push(`Columna requerida 'auth_scram_challenges.${colReq}' no existe.`);
      }
    }
    for (const idxReq of ["idx_scram_challenges_exp", "idx_scram_challenges_used"]) {
      if (!indicesPresentes.has(idxReq)) {
        inconsistencias.push(`Índice requerido '${idxReq}' en 'auth_scram_challenges' no existe.`);
      }
    }
  }

  // Verificar costo_unitario en ticket_items (migración 009)
  if (tablasPresentes.has("ticket_items")) {
    const tiColsRs = await client.execute("PRAGMA table_info('ticket_items');");
    const tiCols = new Set(tiColsRs.rows.map((c) => String(c.name)));
    if (!tiCols.has("costo_unitario")) {
      inconsistencias.push("Columna requerida 'ticket_items.costo_unitario' (migración 009) no existe.");
    }
  }

  const listo =
    migracionesPendientes.length === 0 &&
    tablasFaltantes.length === 0 &&
    inconsistencias.length === 0;

  let codigoEstado = "LISTO";
  if (inconsistencias.length > 0) {
    codigoEstado = "ESQUEMA_INCONSISTENTE";
  } else if (migracionesPendientes.length > 0 || tablasFaltantes.length > 0) {
    codigoEstado = "MIGRACIONES_PENDIENTES";
  }

  return {
    status: listo ? "PREPARADO" : "NO_PREPARADO",
    listo,
    esquema_completo: listo,
    codigo_estado: codigoEstado,
    mensaje_operativo: listo
      ? "Esquema de base de datos verificado y listo (migraciones 001..011 completas)."
      : `Esquema incompleto o inconsistente (${codigoEstado}).`,
    migraciones_requeridas: MIGRACIONES_ESPERADAS,
    migraciones_aplicadas: versiones,
    versiones_aplicadas: versiones,
    migraciones_pendientes: migracionesPendientes,
    tablas_requeridas: TABLAS_REQUERIDAS_READINESS,
    tablas_presentes: Array.from(tablasPresentes).sort(),
    tablas_faltantes: tablasFaltantes,
    inconsistencias,
    comando_migracion: "python scripts/run_migrations.py --env dev",
  };
}


// --- Funciones de Inicialización para Entornos de Prueba Local (:memory:) ---

export async function inicializarEsquemaCompleto(client) {
  await client.batch([
    `CREATE TABLE IF NOT EXISTS _schema_migrations (
      version INTEGER PRIMARY KEY,
      nombre TEXT NOT NULL,
      aplicado_en TEXT NOT NULL DEFAULT (datetime('now'))
    );`,
    `CREATE TABLE IF NOT EXISTS productos (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      nombre TEXT NOT NULL COLLATE NOCASE,
      categoria TEXT NOT NULL DEFAULT 'Otro',
      proveedor TEXT NOT NULL DEFAULT 'General',
      unidad TEXT NOT NULL DEFAULT 'Unidad',
      costo INTEGER NOT NULL DEFAULT 0 CHECK (costo >= 0),
      precio_dia INTEGER NOT NULL CHECK (precio_dia >= 0),
      precio_noche INTEGER NOT NULL DEFAULT 0,
      margen_ratio REAL AS (CASE WHEN costo > 0 THEN ROUND ((CAST (precio_dia AS REAL) - costo) / costo, 4) ELSE 0.0 END) VIRTUAL,
      activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
      fecha_actualizacion TEXT NOT NULL DEFAULT (datetime ('now'))
    );`,
    `CREATE TABLE IF NOT EXISTS productos_auditoria (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      producto_id INTEGER NOT NULL,
      accion TEXT NOT NULL,
      valores_anteriores TEXT,
      valores_nuevos TEXT NOT NULL,
      fecha_utc TEXT NOT NULL DEFAULT (datetime('now')),
      actor TEXT NOT NULL DEFAULT 'sistema'
    );`,
    `CREATE TABLE IF NOT EXISTS tickets (
      id TEXT PRIMARY KEY,
      fecha_hora_utc TEXT NOT NULL DEFAULT (datetime('now')),
      total_venta INTEGER NOT NULL CHECK (total_venta >= 0),
      monto_efectivo INTEGER NOT NULL DEFAULT 0 CHECK (monto_efectivo >= 0),
      monto_transferencia INTEGER NOT NULL DEFAULT 0 CHECK (monto_transferencia >= 0),
      metodo_pago TEXT NOT NULL CHECK (metodo_pago IN ('EFECTIVO', 'TRANSFERENCIA', 'MIXTO')),
      es_nocturno INTEGER NOT NULL DEFAULT 0 CHECK (es_nocturno IN (0, 1)),
      anulado INTEGER NOT NULL DEFAULT 0 CHECK (anulado IN (0, 1)),
      motivo_anulacion TEXT,
      CONSTRAINT chk_ticket_balance CHECK (total_venta = monto_efectivo + monto_transferencia)
    );`,
    `CREATE TABLE IF NOT EXISTS ticket_items (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ticket_id TEXT NOT NULL,
      producto_id INTEGER,
      producto_nombre TEXT NOT NULL,
      cantidad REAL NOT NULL CHECK (cantidad > 0),
      unidad TEXT NOT NULL DEFAULT 'Unidad',
      precio_unitario INTEGER NOT NULL CHECK (precio_unitario >= 0),
      subtotal INTEGER NOT NULL CHECK (subtotal >= 0),
      metodo_pago TEXT NOT NULL,
      costo_unitario INTEGER,
      FOREIGN KEY (ticket_id) REFERENCES tickets (id) ON DELETE CASCADE
    );`,
    `CREATE TABLE IF NOT EXISTS ventas_auditoria (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ticket_id TEXT NOT NULL,
      item_id INTEGER,
      accion TEXT NOT NULL,
      motivo TEXT,
      usuario TEXT NOT NULL DEFAULT 'sistema',
      datos_previos_json TEXT,
      datos_nuevos_json TEXT,
      creado_en_utc TEXT NOT NULL DEFAULT (datetime ('now'))
    );`,
    `CREATE TABLE IF NOT EXISTS cargas_servicios (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      legacy_row_id TEXT,
      fecha_hora_utc TEXT NOT NULL DEFAULT (datetime('now')),
      servicio TEXT NOT NULL COLLATE NOCASE,
      monto_carga INTEGER NOT NULL CHECK (monto_carga > 0),
      monto_adicional INTEGER NOT NULL DEFAULT 0 CHECK (monto_adicional >= 0),
      total_cobrado INTEGER NOT NULL CHECK (total_cobrado >= 0),
      pago_efectivo INTEGER NOT NULL DEFAULT 0 CHECK (pago_efectivo >= 0),
      pago_transferencia INTEGER NOT NULL DEFAULT 0 CHECK (pago_transferencia >= 0),
      anulado INTEGER NOT NULL DEFAULT 0 CHECK (anulado IN (0, 1)),
      idempotency_key TEXT DEFAULT NULL,
      plataforma_origen TEXT DEFAULT NULL,
      servicio_destino TEXT DEFAULT NULL
    );`,
    `CREATE UNIQUE INDEX IF NOT EXISTS idx_cargas_servicios_idempotency_key
      ON cargas_servicios (idempotency_key)
      WHERE idempotency_key IS NOT NULL;`,
    `CREATE INDEX IF NOT EXISTS idx_cargas_plataforma_origen ON cargas_servicios(plataforma_origen);`,
    `CREATE INDEX IF NOT EXISTS idx_cargas_servicio_destino ON cargas_servicios(servicio_destino);`,
    `CREATE TABLE IF NOT EXISTS cargas_servicios_auditoria (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      carga_id INTEGER NOT NULL,
      accion TEXT NOT NULL,
      valores_anteriores TEXT,
      valores_nuevos TEXT NOT NULL,
      fecha_utc TEXT NOT NULL DEFAULT (datetime('now')),
      actor TEXT NOT NULL DEFAULT 'sistema'
    );`,
    `CREATE TABLE IF NOT EXISTS gastos_retiros (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      legacy_row_id TEXT,
      fecha_hora_utc TEXT NOT NULL DEFAULT (datetime('now')),
      tipo TEXT NOT NULL DEFAULT 'General',
      detalle TEXT NOT NULL,
      monto_salida INTEGER NOT NULL CHECK (monto_salida > 0),
      origen_fondos TEXT DEFAULT 'Caja diaria',
      metodo_pago TEXT NOT NULL CHECK (metodo_pago IN ('EFECTIVO', 'TRANSFERENCIA')),
      anulado INTEGER NOT NULL DEFAULT 0 CHECK (anulado IN (0, 1)),
      idempotency_key TEXT DEFAULT NULL
    );`,
    `CREATE UNIQUE INDEX IF NOT EXISTS idx_gastos_retiros_idempotency_key
      ON gastos_retiros (idempotency_key)
      WHERE idempotency_key IS NOT NULL;`,
    `CREATE TABLE IF NOT EXISTS gastos_auditoria (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      gasto_id INTEGER NOT NULL,
      accion TEXT NOT NULL,
      valores_anteriores TEXT,
      valores_nuevos TEXT NOT NULL,
      fecha_utc TEXT NOT NULL DEFAULT (datetime('now')),
      actor TEXT NOT NULL DEFAULT 'sistema'
    );`,
    `CREATE TABLE IF NOT EXISTS cierres_diarios (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      fecha TEXT NOT NULL UNIQUE,
      efectivo_esperado INTEGER NOT NULL,
      efectivo_contado INTEGER NOT NULL,
      diferencia INTEGER NOT NULL,
      total_ventas_efvo INTEGER NOT NULL DEFAULT 0,
      total_ventas_transf INTEGER NOT NULL DEFAULT 0,
      total_servicios_efvo INTEGER NOT NULL DEFAULT 0,
      total_servicios_transf INTEGER NOT NULL DEFAULT 0,
      total_gastos_efvo INTEGER NOT NULL DEFAULT 0,
      total_gastos_transf INTEGER NOT NULL DEFAULT 0,
      total_banco INTEGER NOT NULL DEFAULT 0,
      observaciones TEXT,
      cerrado_por TEXT NOT NULL DEFAULT 'sistema',
      cerrado_en_utc TEXT NOT NULL DEFAULT (datetime('now')),
      actualizado_en_utc TEXT
    );`,
    `CREATE TABLE IF NOT EXISTS pedidos_proveedor (
      id TEXT PRIMARY KEY,
      fecha TEXT NOT NULL,
      proveedor TEXT NOT NULL,
      estado TEXT NOT NULL DEFAULT 'PENDIENTE',
      total REAL NOT NULL DEFAULT 0,
      observaciones TEXT,
      creado_en_utc TEXT NOT NULL DEFAULT (datetime('now')),
      actualizado_en_utc TEXT NOT NULL DEFAULT (datetime('now'))
    );`,
    `CREATE TABLE IF NOT EXISTS pedido_items (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      pedido_id TEXT NOT NULL,
      producto_id INTEGER,
      producto_nombre TEXT NOT NULL,
      cantidad REAL NOT NULL,
      costo_unitario REAL NOT NULL DEFAULT 0,
      subtotal REAL NOT NULL DEFAULT 0
    );`,
    `CREATE TABLE IF NOT EXISTS pedidos_auditoria (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      pedido_id TEXT NOT NULL,
      accion TEXT NOT NULL,
      valores_anteriores TEXT,
      valores_nuevos TEXT NOT NULL,
      fecha_utc TEXT NOT NULL DEFAULT (datetime('now')),
      actor TEXT NOT NULL DEFAULT 'sistema'
    );`,
    `CREATE TABLE IF NOT EXISTS auth_scram_challenges (
      jti TEXT PRIMARY KEY,
      exp INTEGER NOT NULL,
      used INTEGER NOT NULL DEFAULT 0 CHECK (used IN (0, 1)),
      creado_en_utc TEXT NOT NULL DEFAULT (datetime('now'))
    );`,
    `CREATE INDEX IF NOT EXISTS idx_scram_challenges_exp ON auth_scram_challenges(exp);`,
    `CREATE INDEX IF NOT EXISTS idx_scram_challenges_used ON auth_scram_challenges(used, exp);`,
    `CREATE TABLE IF NOT EXISTS tesoreria_fondos (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      codigo TEXT NOT NULL UNIQUE,
      nombre TEXT NOT NULL,
      naturaleza TEXT NOT NULL,
      titularidad TEXT NOT NULL,
      moneda TEXT NOT NULL DEFAULT 'ARS',
      estado TEXT NOT NULL DEFAULT 'ACTIVO',
      saldo_centavos INTEGER NOT NULL DEFAULT 0,
      permite_sobregiro INTEGER NOT NULL DEFAULT 0,
      version INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      updated_at TEXT NOT NULL DEFAULT (datetime('now')),
      creado_por TEXT NOT NULL DEFAULT 'SISTEMA',
      actualizado_por TEXT NOT NULL DEFAULT 'SISTEMA',
      saldo_inicial_cents INTEGER NOT NULL DEFAULT 0,
      saldo_inicial_configurado_en TEXT DEFAULT NULL,
      saldo_inicial_configurado_por TEXT DEFAULT NULL,
      estado_configuracion TEXT NOT NULL DEFAULT 'INICIAL_CERO'
    );`,
    `CREATE TABLE IF NOT EXISTS tesoreria_obligaciones_apertura (
      codigo TEXT PRIMARY KEY,
      nombre TEXT NOT NULL,
      descripcion TEXT NOT NULL,
      saldo_inicial_cents INTEGER NOT NULL DEFAULT 0,
      configurado_en TEXT DEFAULT NULL,
      configurado_por TEXT DEFAULT NULL,
      estado_configuracion TEXT NOT NULL DEFAULT 'INICIAL_CERO'
    );`,
    `CREATE TABLE IF NOT EXISTS tesoreria_saldos_iniciales_auditoria (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      item_codigo TEXT NOT NULL,
      item_nombre TEXT NOT NULL,
      categoria TEXT NOT NULL,
      saldo_anterior_cents INTEGER NOT NULL,
      saldo_nuevo_cents INTEGER NOT NULL,
      fecha_hora TEXT NOT NULL,
      usuario_responsable TEXT NOT NULL,
      motivo TEXT NOT NULL,
      referencia_autorizacion TEXT,
      jornada_id INTEGER,
      es_apertura_inicial INTEGER NOT NULL DEFAULT 0
    );`,
    `CREATE TABLE IF NOT EXISTS tesoreria_movimientos (
      movement_id TEXT PRIMARY KEY,
      codigo_fondo TEXT NOT NULL,
      monto_centavos INTEGER NOT NULL,
      tipo TEXT NOT NULL,
      creado_en_utc TEXT NOT NULL DEFAULT (datetime('now'))
    );`,
    `CREATE TABLE IF NOT EXISTS tesoreria_cuenta_corriente_david (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      idempotency_key TEXT NOT NULL UNIQUE,
      tipo_operacion TEXT NOT NULL,
      monto_cents INTEGER NOT NULL,
      saldo_deuda_resultante_cents INTEGER NOT NULL,
      creado_en_utc TEXT NOT NULL DEFAULT (datetime('now')),
      actor TEXT NOT NULL,
      motivo TEXT NOT NULL
    );`,
    `CREATE TABLE IF NOT EXISTS tesoreria_anticipos_preventistas (
      id TEXT PRIMARY KEY,
      idempotency_key TEXT NOT NULL UNIQUE,
      proveedor TEXT NOT NULL,
      monto_original_cents INTEGER NOT NULL,
      motivo TEXT NOT NULL,
      creado_por TEXT NOT NULL,
      creado_en_utc TEXT NOT NULL DEFAULT (datetime('now'))
    );`
  ], "write");

  const nombresMigraciones = {
    1: "001_initial_schema",
    2: "002_seed_categories",
    3: "003_sales_audit_and_cancellation",
    4: "004_services_and_virtual_loads",
    5: "005_expenses_and_cash_reconciliation",
    6: "006_suppliers_and_orders",
    7: "007_products_audit_and_soft_delete",
    8: "008_services_expenses_audit",
    9: "009_ticket_items_cost_snapshot",
    10: "010_idempotency_keys",
    11: "011_auth_scram_challenges",
  };

  for (let m = 1; m <= 11; m++) {
    await client.execute({
      sql: "INSERT OR IGNORE INTO _schema_migrations (version, nombre, aplicado_en) VALUES (?, ?, datetime('now'));",
      args: [m, nombresMigraciones[m] || `00${m}_migration`],
    });
  }

  // Sembrar 10 fondos canónicos y 3 obligaciones si no existen
  const fondosInit = [
    { codigo: "1110", nombre: "Drugstore Diario", nat: "EFECTIVO", tit: "PROPIA" },
    { codigo: "1120", nombre: "Drugstore Acumulado", nat: "EFECTIVO", tit: "PROPIA" },
    { codigo: "1130", nombre: "Prepay Efectivo", nat: "EFECTIVO", tit: "PROPIA" },
    { codigo: "1140", nombre: "Adicionales Efectivo", nat: "ADICIONALES_PARTICULARES", tit: "PROPIA" },
    { codigo: "1150", nombre: "Claro Efectivo", nat: "RECAUDACION_TERCEROS", tit: "PROPIA" },
    { codigo: "1160", nombre: "Prepay Diario Efectivo", nat: "EFECTIVO", tit: "PROPIA" },
    { codigo: "1210", nombre: "Mercado Pago", nat: "BILLETERA_VIRTUAL", tit: "PROPIA" },
    { codigo: "1220", nombre: "Prepay Digital", nat: "BILLETERA_VIRTUAL", tit: "CUSTODIA" },
    { codigo: "1230", nombre: "Claro Comercios", nat: "BILLETERA_VIRTUAL", tit: "CUSTODIA" },
    { codigo: "1310", nombre: "Tránsito Preventistas", nat: "TRANSITO_PREVENTISTAS", tit: "TRANSITO" },
  ];

  for (const f of fondosInit) {
    await client.execute({
      sql: `INSERT OR IGNORE INTO tesoreria_fondos (codigo, nombre, naturaleza, titularidad, saldo_centavos, saldo_inicial_cents, estado_configuracion)
            VALUES (?, ?, ?, ?, 0, 0, 'INICIAL_CERO');`,
      args: [f.codigo, f.nombre, f.nat, f.tit],
    });
  }

  const obligInit = [
    { codigo: "DAVID", nombre: "Deuda pendiente con David", desc: "Obligación acumulada por liquidaciones PREPAY" },
    { codigo: "CLARO", nombre: "Obligaciones circuito Claro / Ezequiel", desc: "Liquidaciones pendientes servicio Claro" },
    { codigo: "PREVENTISTAS", nombre: "Obligaciones de reposición Preventistas (histórico)", desc: "Anticipos y reposiciones de mercadería" },
  ];

  for (const ob of obligInit) {
    await client.execute({
      sql: `INSERT OR IGNORE INTO tesoreria_obligaciones_apertura (codigo, nombre, descripcion, saldo_inicial_cents, estado_configuracion)
            VALUES (?, ?, ?, 0, 'INICIAL_CERO');`,
      args: [ob.codigo, ob.nombre, ob.desc],
    });
  }
}

export async function poblarDatosPrueba(client) {
  await client.batch([
    {
      sql: `INSERT OR IGNORE INTO productos (id, nombre, categoria, proveedor, unidad, costo, precio_dia, activo)
            VALUES (1, 'Alfajor Chocolate', 'Kiosco', 'Distribuidora San Salvador', 'Unidad', 950, 1500, 1);`,
    },
    {
      sql: `INSERT OR IGNORE INTO productos (id, nombre, categoria, proveedor, unidad, costo, precio_dia, activo)
            VALUES (2, 'Paracetamol 500mg', 'Farmacia', 'Droguería del Sur', 'Unidad', 1400, 2200, 1);`,
    },
    {
      sql: `INSERT OR IGNORE INTO productos (id, nombre, categoria, proveedor, unidad, costo, precio_dia, activo)
            VALUES (3, 'Producto Descontinuado', 'Kiosco', 'Distribuidora San Salvador', 'Unidad', 300, 500, 0);`,
    }
  ], "write");
}

// --- Módulo Tesorería y Saldos Iniciales / Aperturas ---

export async function obtenerSaldosInicialesWorker(client) {
  const resFondos = await client.execute({
    sql: `SELECT codigo, nombre, saldo_centavos, saldo_inicial_cents, saldo_inicial_configurado_en,
                 saldo_inicial_configurado_por, estado_configuracion
          FROM tesoreria_fondos ORDER BY codigo ASC;`
  });

  const resOblig = await client.execute({
    sql: `SELECT codigo, nombre, descripcion, saldo_inicial_cents, configurado_en,
                 configurado_por, estado_configuracion
          FROM tesoreria_obligaciones_apertura ORDER BY codigo ASC;`
  });

  const fondosFisicos = [];
  const cuentasDigitales = [];
  let totalFisicosCents = 0;
  let totalDigitalesCents = 0;

  for (const row of resFondos.rows) {
    const cod = String(row.codigo);
    const item = {
      codigo: cod,
      nombre: String(row.nombre),
      categoria: ["1110", "1120", "1130", "1140", "1150", "1160"].includes(cod) ? "FONDO_FISICO" : "CUENTA_DIGITAL",
      saldo_inicial_cents: Number(row.saldo_inicial_cents ?? 0),
      saldo_actual_cents: Number(row.saldo_centavos ?? 0),
      configurado_en: row.saldo_inicial_configurado_en || null,
      configurado_por: row.saldo_inicial_configurado_por || null,
      estado_configuracion: row.estado_configuracion || "INICIAL_CERO",
      editable: true,
    };
    if (item.categoria === "FONDO_FISICO") {
      fondosFisicos.push(item);
      totalFisicosCents += item.saldo_inicial_cents;
    } else {
      cuentasDigitales.push(item);
      totalDigitalesCents += item.saldo_inicial_cents;
    }
  }

  const obligaciones = [];
  let totalObligacionesCents = 0;
  for (const row of resOblig.rows) {
    const sInit = Number(row.saldo_inicial_cents ?? 0);
    obligaciones.push({
      codigo: String(row.codigo),
      nombre: String(row.nombre),
      categoria: "OBLIGACION",
      saldo_inicial_cents: sInit,
      saldo_actual_cents: sInit,
      configurado_en: row.configurado_en || null,
      configurado_por: row.configurado_por || null,
      estado_configuracion: row.estado_configuracion || "INICIAL_CERO",
      editable: true,
    });
    totalObligacionesCents += sInit;
  }

  return {
    fondos_fisicos: fondosFisicos,
    cuentas_digitales: cuentasDigitales,
    obligaciones,
    total_fondos_fisicos_cents: totalFisicosCents,
    total_cuentas_digitales_cents: totalDigitalesCents,
    total_obligaciones_cents: totalObligacionesCents,
  };
}

export async function modificarSaldoInicialWorker(client, { codigo, saldo_nuevo_cents, usuario, motivo, referencia_autorizacion = null }) {
  if (!usuario || typeof usuario !== "string" || !usuario.trim()) {
    throw new Error("INVALID_INPUT: El usuario responsable es obligatorio.");
  }
  if (!motivo || typeof motivo !== "string" || !motivo.trim()) {
    throw new Error("INVALID_INPUT: El motivo de modificación de saldo inicial es obligatorio para auditoría.");
  }
  if (!Number.isInteger(saldo_nuevo_cents) || saldo_nuevo_cents < 0) {
    throw new Error("INVALID_INPUT: El saldo nuevo debe ser un número entero mayor o igual a 0 centavos.");
  }

  const cod = String(codigo).trim().toUpperCase();

  // 1. Verificar si es un fondo
  const resFondo = await client.execute({
    sql: "SELECT id, codigo, nombre, saldo_centavos, saldo_inicial_cents FROM tesoreria_fondos WHERE codigo = ?;",
    args: [cod]
  });

  if (resFondo.rows.length > 0) {
    const f = resFondo.rows[0];
    const movCount = await client.execute({
      sql: "SELECT count(*) as cnt FROM tesoreria_movimientos WHERE codigo_fondo = ?;",
      args: [cod]
    });
    if (movCount.rows.length > 0 && Number(movCount.rows[0].cnt) > 0) {
      throw new Error(`CONFLICT: El fondo ${cod} ya posee movimientos registrados. No se puede alterar retroactivamente el saldo inicial sin un procedimiento de regularización auditado.`);
    }

    const saldoAnterior = Number(f.saldo_inicial_cents ?? 0);
    const cat = ["1110", "1120", "1130", "1140", "1150", "1160"].includes(cod) ? "FONDO_FISICO" : "CUENTA_DIGITAL";

    await client.batch([
      {
        sql: `UPDATE tesoreria_fondos
              SET saldo_inicial_cents = ?,
                  saldo_centavos = ?,
                  saldo_inicial_configurado_en = datetime('now'),
                  saldo_inicial_configurado_por = ?,
                  estado_configuracion = 'CONFIGURADO'
              WHERE codigo = ?;`,
        args: [saldo_nuevo_cents, saldo_nuevo_cents, usuario, cod]
      },
      {
        sql: `INSERT INTO tesoreria_saldos_iniciales_auditoria (
                item_codigo, item_nombre, categoria, saldo_anterior_cents, saldo_nuevo_cents,
                fecha_hora, usuario_responsable, motivo, referencia_autorizacion, es_apertura_inicial
              ) VALUES (?, ?, ?, ?, ?, datetime('now'), ?, ?, ?, 0);`,
        args: [cod, String(f.nombre), cat, saldoAnterior, saldo_nuevo_cents, usuario, motivo.trim(), referencia_autorizacion ? String(referencia_autorizacion).trim() : null]
      }
    ], "write");

    return {
      status: "SUCCESS",
      item_codigo: cod,
      item_nombre: String(f.nombre),
      categoria: cat,
      saldo_anterior_cents: saldoAnterior,
      saldo_nuevo_cents,
      usuario_responsable: usuario,
      motivo: motivo.trim(),
      referencia_autorizacion: referencia_autorizacion || null,
      fecha_hora: new Date().toISOString()
    };
  }

  // 2. Verificar si es una obligación
  const resOblig = await client.execute({
    sql: "SELECT codigo, nombre, descripcion, saldo_inicial_cents FROM tesoreria_obligaciones_apertura WHERE codigo = ?;",
    args: [cod]
  });

  if (resOblig.rows.length > 0) {
    const ob = resOblig.rows[0];
    if (cod === "DAVID") {
      const movDavid = await client.execute("SELECT count(*) as cnt FROM tesoreria_cuenta_corriente_david;");
      if (movDavid.rows.length > 0 && Number(movDavid.rows[0].cnt) > 0) {
        throw new Error("CONFLICT: La cuenta corriente de David ya posee movimientos registrados. Debe utilizar el procedimiento de regularización/reconocimiento formal.");
      }
    }

    const saldoAnterior = Number(ob.saldo_inicial_cents ?? 0);

    await client.batch([
      {
        sql: `UPDATE tesoreria_obligaciones_apertura
              SET saldo_inicial_cents = ?,
                  configurado_en = datetime('now'),
                  configurado_por = ?,
                  estado_configuracion = 'CONFIGURADO'
              WHERE codigo = ?;`,
        args: [saldo_nuevo_cents, usuario, cod]
      },
      {
        sql: `INSERT INTO tesoreria_saldos_iniciales_auditoria (
                item_codigo, item_nombre, categoria, saldo_anterior_cents, saldo_nuevo_cents,
                fecha_hora, usuario_responsable, motivo, referencia_autorizacion, es_apertura_inicial
              ) VALUES (?, ?, 'OBLIGACION', ?, ?, datetime('now'), ?, ?, ?, 0);`,
        args: [cod, String(ob.nombre), saldoAnterior, saldo_nuevo_cents, usuario, motivo.trim(), referencia_autorizacion ? String(referencia_autorizacion).trim() : null]
      }
    ], "write");

    return {
      status: "SUCCESS",
      item_codigo: cod,
      item_nombre: String(ob.nombre),
      categoria: "OBLIGACION",
      saldo_anterior_cents: saldoAnterior,
      saldo_nuevo_cents,
      usuario_responsable: usuario,
      motivo: motivo.trim(),
      referencia_autorizacion: referencia_autorizacion || null,
      fecha_hora: new Date().toISOString()
    };
  }

  throw new Error(`NOT_FOUND: Código '${cod}' no reconocido como fondo de tesorería ni obligación configurable.`);
}

export async function obtenerHistorialAperturasWorker(client) {
  const res = await client.execute(
    "SELECT * FROM tesoreria_saldos_iniciales_auditoria ORDER BY fecha_hora DESC, id DESC;"
  );
  return res.rows.map(r => ({
    id: Number(r.id),
    item_codigo: String(r.item_codigo),
    item_nombre: String(r.item_nombre),
    categoria: String(r.categoria),
    saldo_anterior_cents: Number(r.saldo_anterior_cents),
    saldo_nuevo_cents: Number(r.saldo_nuevo_cents),
    fecha_hora: String(r.fecha_hora),
    usuario_responsable: String(r.usuario_responsable),
    motivo: String(r.motivo),
    referencia_autorizacion: r.referencia_autorizacion ? String(r.referencia_autorizacion) : null,
    jornada_id: r.jornada_id ? Number(r.jornada_id) : null,
    es_apertura_inicial: Boolean(r.es_apertura_inicial)
  }));
}

export async function obtenerFondosTesoreriaWorker(client) {
  const res = await client.execute(
    "SELECT id, codigo, nombre, naturaleza, titularidad, moneda, estado, saldo_centavos, permite_sobregiro, version, saldo_inicial_cents, estado_configuracion FROM tesoreria_fondos ORDER BY codigo ASC;"
  );
  return res.rows.map(r => ({
    id: Number(r.id),
    codigo: String(r.codigo),
    nombre: String(r.nombre),
    naturaleza: String(r.naturaleza),
    titularidad: String(r.titularidad),
    moneda: String(r.moneda),
    estado: String(r.estado),
    saldo_centavos: Number(r.saldo_centavos),
    permite_sobregiro: Number(r.permite_sobregiro),
    version: Number(r.version),
    saldo_inicial_cents: Number(r.saldo_inicial_cents ?? 0),
    estado_configuracion: String(r.estado_configuracion || "INICIAL_CERO")
  }));
}

export async function obtenerResumenTesoreriaWorker(client) {
  const fondos = await obtenerFondosTesoreriaWorker(client);
  const fisica = fondos.filter(f => ["1110", "1120", "1130", "1140", "1150", "1160"].includes(f.codigo));
  const digital = fondos.filter(f => ["1210", "1220", "1230", "1310"].includes(f.codigo));

  const totalFisicoCents = fisica.reduce((acc, f) => acc + f.saldo_centavos, 0);
  const totalDigitalCents = digital.reduce((acc, f) => acc + f.saldo_centavos, 0);

  return {
    total_efectivo_fisico_cents: totalFisicoCents,
    total_cuentas_digitales_cents: totalDigitalCents,
    total_disponible_cents: totalFisicoCents + totalDigitalCents,
    fondos_count: fondos.length,
    fondos
  };
}

export async function obtenerCCDavidWorker(client) {
  const obRes = await client.execute({
    sql: "SELECT saldo_inicial_cents FROM tesoreria_obligaciones_apertura WHERE codigo = 'DAVID';",
  });
  const saldoInicialDavid = obRes.rows.length > 0 ? Number(obRes.rows[0].saldo_inicial_cents ?? 0) : 0;

  let movimientos = [];
  try {
    const movRes = await client.execute("SELECT * FROM tesoreria_cuenta_corriente_david ORDER BY creado_en_utc DESC;");
    movimientos = movRes.rows.map(r => ({
      id: Number(r.id),
      idempotency_key: String(r.idempotency_key),
      tipo_operacion: String(r.tipo_operacion),
      monto_cents: Number(r.monto_cents),
      saldo_deuda_resultante_cents: Number(r.saldo_deuda_resultante_cents),
      creado_en_utc: String(r.creado_en_utc),
      actor: String(r.actor),
      motivo: String(r.motivo)
    }));
  } catch {}

  const deudaActual = movimientos.length > 0 ? movimientos[0].saldo_deuda_resultante_cents : saldoInicialDavid;

  return {
    obligacion_pendiente_cents: deudaActual,
    saldo_inicial_cents: saldoInicialDavid,
    total_movimientos: movimientos.length,
    movimientos
  };
}

export async function obtenerPreventistasResumenWorker(client) {
  let anticipos = [];
  try {
    const antRes = await client.execute("SELECT * FROM tesoreria_anticipos_preventistas ORDER BY creado_en_utc DESC;");
    anticipos = antRes.rows.map(r => ({
      id: String(r.id),
      proveedor: String(r.proveedor),
      monto_original_cents: Number(r.monto_original_cents),
      motivo: String(r.motivo),
      creado_por: String(r.creado_por),
      creado_en_utc: String(r.creado_en_utc)
    }));
  } catch {}

  return {
    total_anticipos: anticipos.length,
    anticipos
  };
}
