/**
 * Repositorio de persistencia relacional con @libsql/client (compatible con Turso).
 *
 * Demuestra:
 * 1. Transacciones atómicas (batch e interactivas).
 * 2. Idempotencia de operaciones mediante índices únicos.
 * 3. Congelamiento inmutable de costos en ticket_items.
 * 4. Auditoría inmutable de altas y anulaciones lógicas.
 * 5. Filtros por rango de fechas (Jujuy UTC).
 */

export async function inicializarEsquemaSintetico(client) {
  await client.batch([
    `CREATE TABLE IF NOT EXISTS categorias (
      id TEXT PRIMARY KEY,
      nombre TEXT NOT NULL,
      orden INTEGER DEFAULT 0
    );`,
    `CREATE TABLE IF NOT EXISTS productos (
      id TEXT PRIMARY KEY,
      codigo_barras TEXT UNIQUE,
      nombre TEXT NOT NULL,
      categoria_id TEXT NOT NULL,
      precio REAL NOT NULL,
      costo_unitario REAL NOT NULL,
      activo INTEGER NOT NULL DEFAULT 1,
      FOREIGN KEY (categoria_id) REFERENCES categorias(id)
    );`,
    `CREATE TABLE IF NOT EXISTS tickets (
      id TEXT PRIMARY KEY,
      fecha_hora_utc TEXT NOT NULL,
      total_venta REAL NOT NULL,
      monto_efectivo REAL NOT NULL,
      monto_transferencia REAL NOT NULL,
      metodo_pago TEXT NOT NULL,
      anulado INTEGER NOT NULL DEFAULT 0,
      idempotency_key TEXT UNIQUE
    );`,
    `CREATE TABLE IF NOT EXISTS ticket_items (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ticket_id TEXT NOT NULL,
      producto_id TEXT,
      producto_nombre TEXT NOT NULL,
      cantidad REAL NOT NULL,
      precio_unitario REAL NOT NULL,
      costo_unitario REAL,
      subtotal REAL NOT NULL,
      FOREIGN KEY (ticket_id) REFERENCES tickets(id)
    );`,
    `CREATE TABLE IF NOT EXISTS ventas_auditoria (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ticket_id TEXT NOT NULL,
      usuario TEXT NOT NULL,
      accion TEXT NOT NULL,
      motivo TEXT,
      fecha_hora_utc TEXT NOT NULL
    );`,
    `CREATE TABLE IF NOT EXISTS cargas_servicios (
      id TEXT PRIMARY KEY,
      fecha_hora_utc TEXT NOT NULL,
      tipo_servicio TEXT NOT NULL,
      monto_capital REAL NOT NULL,
      cargo_comision REAL NOT NULL,
      monto_total REAL NOT NULL,
      metodo_pago TEXT NOT NULL,
      anulado INTEGER NOT NULL DEFAULT 0,
      idempotency_key TEXT
    );`,
    `CREATE UNIQUE INDEX IF NOT EXISTS uq_cargas_idempotency_key 
      ON cargas_servicios (idempotency_key) WHERE idempotency_key IS NOT NULL;`,
    `CREATE INDEX IF NOT EXISTS idx_cargas_fecha_hora_utc 
      ON cargas_servicios (fecha_hora_utc);`,
    `CREATE TABLE IF NOT EXISTS servicios_auditoria (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      servicio_id TEXT NOT NULL,
      usuario TEXT NOT NULL,
      accion TEXT NOT NULL,
      motivo TEXT,
      fecha_hora_utc TEXT NOT NULL
    );`,
    `CREATE TABLE IF NOT EXISTS gastos_retiros (
      id TEXT PRIMARY KEY,
      fecha_hora_utc TEXT NOT NULL,
      monto REAL NOT NULL,
      concepto TEXT NOT NULL,
      categoria TEXT NOT NULL,
      metodo_pago TEXT NOT NULL,
      anulado INTEGER NOT NULL DEFAULT 0,
      idempotency_key TEXT
    );`,
    `CREATE UNIQUE INDEX IF NOT EXISTS uq_gastos_idempotency_key 
      ON gastos_retiros (idempotency_key) WHERE idempotency_key IS NOT NULL;`
  ], "write");
}

export async function poblarDatosPrueba(client) {
  await client.batch([
    {
      sql: `INSERT OR IGNORE INTO categorias (id, nombre, orden) VALUES (?, ?, ?);`,
      args: ["cat-kiosco", "Kiosco y Golosinas", 1],
    },
    {
      sql: `INSERT OR IGNORE INTO categorias (id, nombre, orden) VALUES (?, ?, ?);`,
      args: ["cat-drogas", "Droguería y Farmacia", 2],
    },
    {
      sql: `INSERT OR IGNORE INTO productos (id, codigo_barras, nombre, categoria_id, precio, costo_unitario, activo)
            VALUES (?, ?, ?, ?, ?, ?, 1);`,
      args: ["prod-alfajor", "7790001", "Alfajor Chocolate", "cat-kiosco", 1500, 950],
    },
    {
      sql: `INSERT OR IGNORE INTO productos (id, codigo_barras, nombre, categoria_id, precio, costo_unitario, activo)
            VALUES (?, ?, ?, ?, ?, ?, 1);`,
      args: ["prod-paracetamol", "7790002", "Paracetamol 500mg", "cat-drogas", 2200, 1400],
    },
    {
      sql: `INSERT OR IGNORE INTO productos (id, codigo_barras, nombre, categoria_id, precio, costo_unitario, activo)
            VALUES (?, ?, ?, ?, ?, ?, 0);`, // Inactivo
      args: ["prod-antiguo", "7790003", "Producto Descontinuado", "cat-kiosco", 500, 300],
    }
  ], "write");
}

export async function obtenerCatalogo(client) {
  const rs = await client.execute(`
    SELECT p.id, p.codigo_barras, p.nombre, p.categoria_id, c.nombre as categoria_nombre,
           p.precio, p.costo_unitario, p.activo
    FROM productos p
    JOIN categorias c ON p.categoria_id = c.id
    WHERE p.activo = 1
    ORDER BY c.orden, p.nombre;
  `);
  return rs.rows;
}

export async function registrarVentaTransaccional(client, { ticket, items, usuario, idempotencyKey = null }) {
  // Comprobación de idempotencia preventiva
  if (idempotencyKey) {
    const existing = await client.execute({
      sql: "SELECT id FROM tickets WHERE idempotency_key = ?;",
      args: [idempotencyKey],
    });
    if (existing.rows.length > 0) {
      return {
        id: existing.rows[0].id,
        status: "IDEMPOTENT_REPLAY",
        message: "Operación idempotente retornada sin duplicar registros",
      };
    }
  }

  // Resolver productos para congelar costo histórico exacto
  const nombres = items.map((i) => i.producto_nombre);
  const placeholders = nombres.map(() => "?").join(",");
  const prodsRs = await client.execute({
    sql: `SELECT id, nombre, precio, costo_unitario FROM productos WHERE nombre IN (${placeholders});`,
    args: nombres,
  });

  const prodsMap = new Map();
  for (const r of prodsRs.rows) {
    prodsMap.set(r.nombre, r);
  }

  // Validaciones financieras de integridad
  if (ticket.monto_efectivo + ticket.monto_transferencia !== ticket.total_venta) {
    throw new Error("El monto en efectivo más transferencia debe ser exactamente igual al total.");
  }

  const statements = [
    {
      sql: `INSERT INTO tickets (id, fecha_hora_utc, total_venta, monto_efectivo, monto_transferencia, metodo_pago, idempotency_key)
            VALUES (?, ?, ?, ?, ?, ?, ?);`,
      args: [
        ticket.id,
        ticket.fecha_hora_utc,
        ticket.total_venta,
        ticket.monto_efectivo,
        ticket.monto_transferencia,
        ticket.metodo_pago,
        idempotencyKey,
      ],
    },
  ];

  for (const it of items) {
    const prod = prodsMap.get(it.producto_nombre);
    const prodId = prod ? prod.id : null;
    const costoUnit = prod ? prod.costo_unitario : 0;
    const subtotal = it.cantidad * it.precio_unitario;

    statements.push({
      sql: `INSERT INTO ticket_items (ticket_id, producto_id, producto_nombre, cantidad, precio_unitario, costo_unitario, subtotal)
            VALUES (?, ?, ?, ?, ?, ?, ?);`,
      args: [ticket.id, prodId, it.producto_nombre, it.cantidad, it.precio_unitario, costoUnit, subtotal],
    });
  }

  statements.push({
    sql: `INSERT INTO ventas_auditoria (ticket_id, usuario, accion, motivo, fecha_hora_utc)
          VALUES (?, ?, 'CREACION', 'Alta inicial de venta', ?);`,
    args: [ticket.id, usuario, ticket.fecha_hora_utc],
  });

  // Ejecución atómica por lote (all-or-nothing)
  await client.batch(statements, "write");

  return {
    id: ticket.id,
    status: "CREATED",
    total_venta: ticket.total_venta,
  };
}

export async function anularVentaTransaccional(client, ticketId, usuario, motivo) {
  const nowUtc = new Date().toISOString();

  // Transacción atómica: marcar anulado + auditoría inmutable
  await client.batch([
    {
      sql: "UPDATE tickets SET anulado = 1 WHERE id = ?;",
      args: [ticketId],
    },
    {
      sql: `INSERT INTO ventas_auditoria (ticket_id, usuario, accion, motivo, fecha_hora_utc)
            VALUES (?, ?, 'ANULACION', ?, ?);`,
      args: [ticketId, usuario, motivo, nowUtc],
    },
  ], "write");

  return { ticketId, anulado: true, motivo };
}

export async function registrarCargaServicio(client, { servicio, usuario, idempotencyKey = null }) {
  const statements = [
    {
      sql: `INSERT INTO cargas_servicios (id, fecha_hora_utc, tipo_servicio, monto_capital, cargo_comision, monto_total, metodo_pago, idempotency_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?);`,
      args: [
        servicio.id,
        servicio.fecha_hora_utc,
        servicio.tipo_servicio,
        servicio.monto_capital,
        servicio.cargo_comision,
        servicio.monto_total,
        servicio.metodo_pago,
        idempotencyKey,
      ],
    },
    {
      sql: `INSERT INTO servicios_auditoria (servicio_id, usuario, accion, motivo, fecha_hora_utc)
            VALUES (?, ?, 'CREACION', 'Carga de servicio', ?);`,
      args: [servicio.id, usuario, servicio.fecha_hora_utc],
    },
  ];

  await client.batch(statements, "write");
  return { id: servicio.id, status: "CREATED" };
}

export async function listarServiciosPorRango(client, fechaDesdeUtc, fechaHastaUtc) {
  const rs = await client.execute({
    sql: `SELECT * FROM cargas_servicios 
          WHERE fecha_hora_utc >= ? AND fecha_hora_utc <= ? 
          ORDER BY fecha_hora_utc DESC;`,
    args: [fechaDesdeUtc, fechaHastaUtc],
  });
  return rs.rows;
}
