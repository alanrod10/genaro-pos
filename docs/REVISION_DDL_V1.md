# Revisión Técnica del DDL y Modelo de Datos (V1) — Genaro POS

> **Aviso Importante:** Este documento representa el diseño relacional corregido para evaluación técnica. **No se ha ejecutado ningún DDL sobre servicios remotos ni bases de producción.**

---

### 1. Margen de Ganancia (`margen_ratio` vs. `MARGEN_%`)
* **Problema:** En el código heredado de Google Sheets, la columna `MARGEN_%` se guarda como una fracción decimal (ej. `0.25` para un 25%, o `0.3556` para 35.56%). En propuestas anteriores se sugirió almacenar el valor multiplicado por 100.
* **Decisión:** Almacenar internamente la columna como **`margen_ratio REAL` (fracción 0.0 - 1.0)** para preservar 100% de paridad con la convención histórica de Google Sheets y simplificar exportaciones. La multiplicación por 100 se realiza única y exclusivamente en la capa de presentación visual (UI).

---

### 2. Representación Monetaria (Pesos Enteros vs. Fracciones)
* **Perfilado de Datos:** La verificación en las hojas transaccionales (`DB_PRODUCTOS`, `DB_MOVIMIENTOS_CAJA`, `DB_HISTORIAL_ITEMS`) confirmó que las operaciones reales se cobran y registran en pesos enteros. En `DB_CARGAS` y `GASTOS_RETIROS`, los datos contienen formato de moneda texto (ej. `"$3,700.00"` o `"$2,900"`).
* **Decisión:** Todas las columnas monetarias operativas (`total_venta`, `monto_efectivo`, `monto_transferencia`, `monto_carga`, `monto_adicional`, `monto_salida`, `costo`, `precio_dia`) se tipifican como **`INTEGER` (pesos enteros)**.
* **Regla de Parseo:**
  1. Eliminar signo `$`, espacios en blanco y puntos de millar.
  2. Si contiene decimales (`.00` o `,00`), validar que sean ceros y redondear estrictamente a entero.
  3. Si contiene centavos reales distintos de cero, enviar a **Cuarentena de Migración**.

---

### 3. Zona Horaria y Normalización de Fechas
* **Problema:** Emplear `datetime('now', '-3 hours')` como lógica única en base de datos genera ambigüedad si el servidor o la base de datos cambia de huso horario.
* **Decisión:**
  * Todas las marcas de tiempo se almacenan en formato ISO-8601 UTC estricto: `YYYY-MM-DD HH:MM:SS` (usando `datetime('now')`).
  * Toda agrupación contable diaria, filtro por fecha o presentación en pantalla se realiza convirtiendo a la zona horaria del comercio: **`America/Argentina/Jujuy` (UTC-3)**.

---

### 4. Identidad y Mapeo de Registros Legacy
* **Problema:** En Google Sheets, solo los tickets poseen un identificador (`TICKET_ID`). Las cargas, gastos y productos carecen de claves primarias estables e inmutables (se dependía de la posición de fila).
* **Solución de Claves:**
  * `tickets`: Clave primaria alfanumérica `id TEXT PRIMARY KEY` (`T-<milisegundos>-<sufijo_random>`).
  * `ticket_items`: `id INTEGER PRIMARY KEY AUTOINCREMENT` + `ticket_id` foráneo.
  * `productos`: `id INTEGER PRIMARY KEY AUTOINCREMENT`.
  * `cargas_servicios`: `id INTEGER PRIMARY KEY AUTOINCREMENT` + `legacy_row_id TEXT` (para trazabilidad histórica).
  * `gastos_retiros`: `id INTEGER PRIMARY KEY AUTOINCREMENT` + `legacy_row_id TEXT`.

---

### 5. Semántica de Catálogo: Nombres Duplicados y Bajas Lógicas
* **Problema:** `DB_PRODUCTOS` contiene ítems legítimos con nombres duplicados (distinto proveedor, marca o costo).
* **Decisión:** No incluir `UNIQUE(nombre)`. La identidad es el `id`.
* **Baja Lógica:** Columna `activo INTEGER DEFAULT 1 CHECK (activo IN (0, 1))`. La acción de eliminar un producto en la interfaz cambia su estado a inactivo (`activo = 0`). Esto garantiza que los reportes de ventas pasadas mantengan la integridad referencial intacta.

---

### 6. Integridad Contable y Restricciones `CHECK`
El DDL incorpora reglas matemáticas inviolables en el motor SQL:
* **En Tickets:**
  ```sql
  CONSTRAINT chk_ticket_balance CHECK (total_venta = (monto_efectivo + monto_transferencia))
  ```
* **En Cargas:**
  ```sql
  CONSTRAINT chk_carga_balance CHECK ((monto_carga + monto_adicional) = (pago_efectivo + pago_transferencia))
  ```

---

### 7. Precisión en Gastos y Retiros
* Se almacena `fecha_hora_utc TEXT NOT NULL` para no perder la hora y minuto del registro de egresos, garantizando auditoría completa.

---

### 8. Patrón Outbox Transaccional
Cada operación de negocio (venta, carga, gasto, actualización de catálogo) inserta en la misma transacción un evento en `outbox_events`:
* Columnas: `id`, `aggregate_type`, `aggregate_id`, `operation`, `payload_json`, `estado`, `intentos`, `creado_en_utc`, `procesado_en_utc`.
* Garantía: Si la venta se persiste, el evento de sincronización a Sheets existe indefectiblemente.

---

### 9. Modelo de Replicación hacia Google Sheets
Se descarta la sincronización por sobreescritura de toda la hoja. Se establece el modelo:
* **Espejo Actualizable por Clave:** Cada fila escrita en Sheets incluirá una columna técnica oculta `ID_SISTEMA`. El worker de sincronización actualiza filas específicas o realiza un `append` limpio sin sobrescribir el archivo completo.
* Si un ticket o carga es anulado en Turso, en Sheets se actualiza la columna `ESTADO = 'ANULADO'` o se registra la contrapartida contable sin borrar la fila física.

---

### 10. Validación de Claves Foráneas y Atomicidad
Turso y SQLite requieren `PRAGMA foreign_keys = ON;` al iniciar cada conexión para validar que los ítems huérfanos sean rechazados de inmediato.

---

### 11. Protocolo Remoto del Worker (Apps Script / External Service)
Apps Script no ejecuta drivers nativos de Python. Para interactuar con Turso:
* Debe utilizarse la **API HTTP de Turso (SQL-over-HTTP)** autenticada con un Bearer Token de solo lectura/escritura acotado:
  * Endpoint: `https://<db-name>-<org>.turso.io/v2/pipeline`
  * Método: `POST` con payload JSON que envía consultas preparadas parametrizadas.
  * Apps Script realiza la llamada mediante `UrlFetchApp.fetch()`.

---

### 12. DDL Propuesto V1 (Consolidado)

```sql
-- TABLA DE MIGRACIONES
CREATE TABLE IF NOT EXISTS _schema_migrations (
    version INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL,
    aplicado_en TEXT NOT NULL DEFAULT (datetime('now'))
);

-- CATÁLOGO DE PRODUCTOS
CREATE TABLE IF NOT EXISTS productos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL COLLATE NOCASE,
    categoria TEXT NOT NULL DEFAULT 'Otro',
    proveedor TEXT NOT NULL DEFAULT 'General',
    unidad TEXT NOT NULL DEFAULT 'Unidad',
    costo INTEGER NOT NULL DEFAULT 0 CHECK (costo >= 0),
    precio_dia INTEGER NOT NULL CHECK (precio_dia >= 0),
    precio_noche INTEGER NOT NULL DEFAULT 0,
    margen_ratio REAL GENERATED ALWAYS AS (
        CASE WHEN costo > 0 THEN ROUND((CAST(precio_dia AS REAL) - costo) / costo, 4) ELSE 0.0 END
    ) STORED,
    activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
    fecha_actualizacion TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_productos_nombre ON productos (nombre);
CREATE INDEX IF NOT EXISTS idx_productos_proveedor ON productos (proveedor);
CREATE INDEX IF NOT EXISTS idx_productos_activo ON productos (activo);

-- TICKETS DE VENTA
CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY,
    fecha_hora_utc TEXT NOT NULL DEFAULT (datetime('now')),
    total_venta INTEGER NOT NULL CHECK (total_venta >= 0),
    monto_efectivo INTEGER NOT NULL DEFAULT 0 CHECK (monto_efectivo >= 0),
    monto_transferencia INTEGER NOT NULL DEFAULT 0 CHECK (monto_transferencia >= 0),
    metodo_pago TEXT NOT NULL CHECK (metodo_pago IN ('EFECTIVO', 'TRANSFERENCIA', 'MIXTO')),
    es_nocturno INTEGER NOT NULL DEFAULT 0 CHECK (es_nocturno IN (0, 1)),
    anulado INTEGER NOT NULL DEFAULT 0 CHECK (anulado IN (0, 1)),
    motivo_anulacion TEXT,
    CONSTRAINT chk_ticket_balance CHECK (total_venta = (monto_efectivo + monto_transferencia))
);

CREATE INDEX IF NOT EXISTS idx_tickets_fecha ON tickets (fecha_hora_utc);
CREATE INDEX IF NOT EXISTS idx_tickets_anulado ON tickets (anulado);

-- ÍTEMS DE VENTA
CREATE TABLE IF NOT EXISTS ticket_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id TEXT NOT NULL,
    producto_id INTEGER,
    producto_nombre TEXT NOT NULL,
    cantidad REAL NOT NULL CHECK (cantidad > 0),
    unidad TEXT NOT NULL DEFAULT 'Unidad',
    precio_unitario INTEGER NOT NULL CHECK (precio_unitario >= 0),
    subtotal INTEGER NOT NULL CHECK (subtotal >= 0),
    metodo_pago TEXT NOT NULL,
    FOREIGN KEY (ticket_id) REFERENCES tickets (id) ON DELETE CASCADE,
    FOREIGN KEY (producto_id) REFERENCES productos (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_items_ticket ON ticket_items (ticket_id);
CREATE INDEX IF NOT EXISTS idx_items_producto ON ticket_items (producto_nombre);

-- CARGAS Y SERVICIOS
CREATE TABLE IF NOT EXISTS cargas_servicios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    legacy_row_id TEXT,
    fecha_hora_utc TEXT NOT NULL DEFAULT (datetime('now')),
    servicio TEXT NOT NULL COLLATE NOCASE,
    monto_carga INTEGER NOT NULL CHECK (monto_carga > 0),
    monto_adicional INTEGER NOT NULL DEFAULT 0 CHECK (monto_adicional >= 0),
    total_cobrado INTEGER GENERATED ALWAYS AS (monto_carga + monto_adicional) STORED,
    pago_efectivo INTEGER NOT NULL DEFAULT 0 CHECK (pago_efectivo >= 0),
    pago_transferencia INTEGER NOT NULL DEFAULT 0 CHECK (pago_transferencia >= 0),
    anulado INTEGER NOT NULL DEFAULT 0 CHECK (anulado IN (0, 1)),
    CONSTRAINT chk_carga_balance CHECK ((monto_carga + monto_adicional) = (pago_efectivo + pago_transferencia))
);

CREATE INDEX IF NOT EXISTS idx_cargas_fecha ON cargas_servicios (fecha_hora_utc);
CREATE INDEX IF NOT EXISTS idx_cargas_servicio ON cargas_servicios (servicio);

-- GASTOS Y RETIROS
CREATE TABLE IF NOT EXISTS gastos_retiros (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    legacy_row_id TEXT,
    fecha_hora_utc TEXT NOT NULL DEFAULT (datetime('now')),
    tipo TEXT NOT NULL DEFAULT 'General',
    detalle TEXT NOT NULL,
    monto_salida INTEGER NOT NULL CHECK (monto_salida > 0),
    origen_fondos TEXT DEFAULT 'Caja diaria',
    metodo_pago TEXT NOT NULL CHECK (metodo_pago IN ('EFECTIVO', 'TRANSFERENCIA'))
);

CREATE INDEX IF NOT EXISTS idx_gastos_fecha ON gastos_retiros (fecha_hora_utc);

-- COLA OUTBOX TRANSACCIONAL
CREATE TABLE IF NOT EXISTS outbox_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN ('INSERT', 'UPDATE', 'DELETE')),
    payload_json TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'PENDIENTE' CHECK (estado IN ('PENDIENTE', 'EN_PROCESO', 'SINCRONIZADO', 'ERROR')),
    intentos INTEGER NOT NULL DEFAULT 0,
    ultimo_error TEXT,
    creado_en_utc TEXT NOT NULL DEFAULT (datetime('now')),
    procesado_en_utc TEXT
);

CREATE INDEX IF NOT EXISTS idx_outbox_estado ON outbox_events (estado, intentos);
```
