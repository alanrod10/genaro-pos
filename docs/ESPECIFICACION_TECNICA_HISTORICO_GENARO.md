# ESPECIFICACIÓN TÉCNICA DEL HISTÓRICO GENAROPOS (MODO CONSULTA AISLADA)

> **Documento:** Especificación Técnica de Arquitectura, DDL y Modelo de Datos Histórico.  
> **Estado:** Borrador de Diseño Técnico — **Sin Implementar (Estrictamente Solo Lectura)**.  
> **Origen de Datos:** `/home/alanrod10/workspace/genaro-pos-turso-lab/SISTEMA_GENARO_v4_PRODUCCION.xlsx`.  
> **Hash SHA-256 Archivo Origen:** `2e2c046e7f229b47e5bcce3b0cf4bda90bf63be3eec90dcfc3cbe0f4cfdfb12e`  
> **Hash MD5 Archivo Origen:** `851599ddf096b662dedf7ab5f820f444`  
> **Fecha de Elaboración:** Octubre 2026.

---

## 1. CONCILIACIÓN EXHAUSTIVA DE INCONSISTENCIAS HISTÓRICAS

A partir de la auditoría física campo por campo sobre el libro `SISTEMA_GENARO_v4_PRODUCCION.xlsx`, se determinaron y verificaron matemáticamente las anomalías numéricas detectadas en los informes preliminares.

### 1.1. Los 4 tickets con desbalance de pagos ($6.900,00 de diferencia)

En la hoja `DB_MOVIMIENTOS_CAJA`, exactamente 4 de los 7.246 tickets (99,94% de consistencia) presentan una discrepancia entre la columna `TOTAL_VENTA` y la suma de `MONTO_EFECTIVO + MONTO_TRANSF`.

| Fila Excel | `TICKET_ID` | Fecha Registrada | `TOTAL_VENTA` | `MONTO_EFECTIVO` | `MONTO_TRANSF` | Suma Pagos | Discrepancia | Detalle en `DB_HISTORIAL_ITEMS` (Fila, Producto, Método) | Causa Raíz Demostrada |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **3437** | `T-1776286833621` | 2026-04-15 18:00:34 | $3.500,00 | $3.500,00 | $3.500,00 | $7.000,00 | **-$3.500,00** | Fila 5402: *Quilmes 1lt*, Subtotal $3.500, Pago: `MIXTO` | El script o cajero duplicó el total ($3.500) en ambas columnas en lugar de prorratear el pago mixto. |
| **3440** | `T-1776295225247` | 2026-04-15 20:20:25 | $1.200,00 | $1.200,00 | $1.200,00 | $2.400,00 | **-$1.200,00** | Fila 5405: *Velas Farolito 120gr*, Subtotal $1.200, Pago: `MIXTO` | Mismo error operativo de duplicación de importe completo en pago mixto (mismo día de operación). |
| **3441** | `T-1776300603362` | 2026-04-15 21:50:03 | $2.000,00 | $2.000,00 | $2.000,00 | $4.000,00 | **-$2.000,00** | Fila 5406: *Placer ananá 2,5lts*, Subtotal $2.000, Pago: `MIXTO` | Mismo error operativo de duplicación de importe completo en pago mixto (mismo día de operación). |
| **4655** | `T-1782588411406` | 2026-06-27 16:26:51 | $5.100,00 | $0,00 | $5.300,00 | $5.300,00 | **-$200,00** | Fila 7550: *Alfajor Bon o Bon Blanco/Negro Triple 60gr* (3 u × $1.700 = $5.100), pero Subtotal registrado: $5.300, Pago: `TRANSFERENCIA` | En la hoja de ítems se cobró $5.300 (con recargo de $200), impactado en transferencia ($5.300), pero la cabecera registró $5.100. |

**Conciliación Matemática:**  
$$\text{Diferencia Total} = (-\$3.500) + (-\$1.200) + (-\$2.000) + (-\$200) = -\$6.900,00$$  
$$\text{Total Venta Facturada} = \$23.623.890,00$$  
$$\text{Total Medios de Pago Registrados} = \$16.789.820,00 \text{ (Efvo)} + \$6.840.970,00 \text{ (Transf)} = \$23.630.790,00$$  
$$\$23.623.890,00 - \$23.630.790,00 = -\$6.900,00$$

*Regla Técnica de Preservación:* **No alterar los valores originales.** No inventar desgloses arbitrarios ni forzar reducciones en efectivo/transferencia para cuadrar el balance. Los campos `monto_efectivo` y `monto_transferencia` se conservan con su valor original, añadiendo una columna explícita de auditoría `balance_pago_diferencia`.

---

### 1.2. Los 31 tickets sin detalle y los 6 tickets con deriva temporal

La auditoría cruzada entre los 7.246 tickets de `DB_MOVIMIENTOS_CAJA` y los 7.221 identificadores de `DB_HISTORIAL_ITEMS` arrojó dos grupos atípicos:

1. **31 tickets presentes en CAJA sin coincidencia textual en ITEMS** (suman $123.900,00).
2. **6 tickets presentes en ITEMS sin coincidencia textual en CAJA** (suman $22.700,00).

#### Análisis de superposición y causa raíz (Deriva de milisegundos en Apps Script)

Al analizar la marca temporal subyacente del identificador (generada con `Date.now()` en JavaScript, formato `T-<timestamp_ms>`), se descubrió que el script de Google Sheets ejecutaba dos llamadas independientes a `Date.now()` al momento de persistir la transacción: una al volcar los ítems y otra al volcar la cabecera de caja.

De los 6 tickets de ITEMS sin clave exacta en CAJA:
- **2 tickets corresponden inequívocamente a 2 de los 31 tickets de CAJA** (superposición directa por correlación temporal de milisegundos, idéntico importe y fecha):
  1. `T-1789152902573` (CAJA Fila 5988: 2026-09-11 15:55:03, $3.500) $\leftrightarrow$ `T-1789152902152` (ITEMS Fila 9670: *Quilmes 710cc*, $3.500). Deriva: **+421 ms**.
  2. `T-1789181755971` (CAJA Fila 5999: 2026-09-11 23:55:56, $3.000) $\leftrightarrow$ `T-1789181756186` (ITEMS Fila 9681: *361 1lt*, $3.000). Deriva: **-215 ms**.

- **Los 4 tickets restantes de ITEMS representan reintentos de inserción de ítems duplicados:**
  Sus líneas de venta ya existían previamente en ITEMS bajo el ID coincidente con CAJA. El script generó un segundo bloque de ítems con un timestamp desplazado unos milisegundos:
  1. `T-1789152684913` (ITEMS Filas 9668-9669, $2.100): duplicado exacto de Fila 9666-9667 bajo `T-1789152684450` (deriva -463 ms frente a CAJA Fila 5987).
  2. `T-1789593208443` (ITEMS Fila 9872, $2.000): duplicado exacto de Fila 9871 bajo `T-1789593208072` (deriva -371 ms frente a CAJA Fila 6106).
  3. `T-1789599964121` (ITEMS Fila 9879, $7.600): duplicado exacto de Fila 9878 bajo `T-1789599963734` (deriva -387 ms frente a CAJA Fila 6109).
  4. `T-1789601117700` (ITEMS Filas 9883-9884, $4.500): duplicado exacto de Filas 9881-9882 bajo `T-1789601117052` (deriva -648 ms frente a CAJA Fila 6111).

- **Los 29 tickets restantes de CAJA** son ventas reales asentadas en caja por el operador sin registrar el desglose de productos en la hoja de ítems (suman $\$123.900 - \$3.500 - \$3.000 = \$117.400,00$).

#### Inventario completo de los 31 tickets de CAJA

| Fila | `TICKET_ID` | Fecha | Total | Efectivo | Transf. | Estado de Correlación con Ítems |
| :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| 107 | `T-1764791603642` | 2025-12-03 16:53:24 | $3.300 | $3.300 | $0 | Sin detalle (Venta directa de cabecera) |
| 110 | `T-1764793718251` | 2025-12-03 17:28:38 | $3.300 | $3.300 | $0 | Sin detalle (Venta directa de cabecera) |
| 111 | `T-1764794101464` | 2025-12-03 17:35:01 | $2.400 | $2.400 | $0 | Sin detalle (Venta directa de cabecera) |
| 1393 | `T-1768763849326` | 2026-01-18 16:17:29 | $2.000 | $0 | $2.000 | Sin detalle (Venta directa de cabecera) |
| 1437 | `T-1768934829573` | 2026-01-20 15:47:10 | $6.000 | $6.000 | $0 | Sin detalle (Venta directa de cabecera) |
| 2889 | `T-1774046806244` | 2026-03-20 19:46:46 | $4.000 | $0 | $4.000 | Sin detalle (Venta directa de cabecera) |
| 5812 | `T-1788317624621` | 2026-09-01 23:53:45 | $3.400 | $3.400 | $0 | Sin detalle (Venta directa de cabecera) |
| 5824 | `T-1788371603301` | 2026-09-02 14:53:23 | $5.000 | $0 | $5.000 | Sin detalle (Venta directa de cabecera) |
| 5826 | `T-1788372253208` | 2026-09-02 15:04:13 | $2.000 | $2.000 | $0 | Sin detalle (Venta directa de cabecera) |
| 5842 | `T-1788399445811` | 2026-09-02 22:37:26 | $2.500 | $2.500 | $0 | Sin detalle (Venta directa de cabecera) |
| 5844 | `T-1788401916156` | 2026-09-02 23:18:36 | $6.000 | $6.000 | $0 | Sin detalle (Venta directa de cabecera) |
| 5961 | `T-1789008967092` | 2026-09-09 23:56:07 | $2.000 | $2.000 | $0 | Sin detalle (Venta directa de cabecera) |
| 5967 | `T-1789057371433` | 2026-09-10 13:22:51 | $6.300 | $0 | $6.300 | Sin detalle (Venta directa de cabecera) |
| **5988** | `T-1789152902573` | 2026-09-11 15:55:03 | $3.500 | $3.500 | $0 | **Correlacionado por deriva temporal con `T-1789152902152`** |
| **5999** | `T-1789181755971` | 2026-09-11 23:55:56 | $3.000 | $3.000 | $0 | **Correlacionado por deriva temporal con `T-1789181756186`** |
| 6020 | `T-1789254588068` | 2026-09-12 20:09:48 | $7.800 | $7.800 | $0 | Sin detalle (Venta directa de cabecera) |
| 6064 | `T-1789425898584` | 2026-09-14 19:44:59 | $8.300 | $8.300 | $0 | Sin detalle (Venta directa de cabecera) |
| 6118 | `T-1789612569151` | 2026-09-16 23:36:09 | $2.400 | $0 | $2.400 | Sin detalle (Venta directa de cabecera) |
| 6122 | `T-1789652384835` | 2026-09-17 10:39:45 | $3.500 | $3.500 | $0 | Sin detalle (Venta directa de cabecera) |
| 6170 | `T-1789767637880` | 2026-09-18 21:40:37 | $3.500 | $0 | $3.500 | Sin detalle (Venta directa de cabecera) |
| 6174 | `T-1789771584076` | 2026-09-18 22:46:24 | $1.000 | $1.000 | $0 | Sin detalle (Venta directa de cabecera) |
| 6235 | `T-1790008972826` | 2026-09-21 13:42:52 | $3.900 | $3.900 | $0 | Sin detalle (Venta directa de cabecera) |
| 6252 | `T-1790092464971` | 2026-09-22 12:54:24 | $6.300 | $6.300 | $0 | Sin detalle (Venta directa de cabecera) |
| 6385 | `T-1790610910433` | 2026-09-28 12:55:10 | $3.500 | $0 | $3.500 | Sin detalle (Venta directa de cabecera) |
| 6402 | `T-1790632685343` | 2026-09-28 18:58:05 | $2.000 | $2.000 | $0 | Sin detalle (Venta directa de cabecera) |
| 6412 | `T-1790646001913` | 2026-09-28 22:40:01 | $4.400 | $0 | $4.400 | Sin detalle (Venta directa de cabecera) |
| 6541 | `T-1791057279927` | 2026-10-03 16:54:39 | $7.800 | $0 | $7.800 | Sin detalle (Venta directa de cabecera) |
| 6789 | `T-1791903200560` | 2026-10-13 11:53:20 | $6.800 | $0 | $6.800 | Sin detalle (Venta directa de cabecera) |
| 7066 | `T-1792763362300` | 2026-10-23 10:49:22 | $2.800 | $0 | $2.800 | Sin detalle (Venta directa de cabecera) |
| 7105 | `T-1792854783279` | 2026-10-24 12:13:03 | $3.000 | $3.000 | $0 | Sin detalle (Venta directa de cabecera) |
| 7229 | `T-1793242008141` | 2026-10-28 23:46:48 | $2.200 | $2.200 | $0 | Sin detalle (Venta directa de cabecera) |

---

## 2. MODELO DE DATOS HISTÓRICO Y DDL

Para garantizar que el histórico pueda consultarse sin colisionar con las reglas de negocio de la operación diaria, se define un esquema con **tablas históricas dedicadas de solo lectura**, con tipos de datos tolerantes y campos explícitos de auditoría.

### 2.1. DDL SQL Propuesto

```sql
-- ==========================================================
-- GENARO POS — ESQUEMA HISTÓRICO AISLADO (SOLO LECTURA)
-- No modifica las tablas operativas: tickets, ticket_items, cargas_servicios.
-- ==========================================================

-- 1. CONTROL DE LOTES DE IMPORTACIÓN HISTÓRICA (IDEMPOTENCIA DE ARCHIVO)
CREATE TABLE IF NOT EXISTS historico_lotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_filename TEXT NOT NULL,
    source_sha256 TEXT NOT NULL UNIQUE,
    source_size_bytes INTEGER NOT NULL,
    total_tickets INTEGER NOT NULL,
    total_items INTEGER NOT NULL,
    total_cargas INTEGER NOT NULL,
    total_ventas_pesos INTEGER NOT NULL,
    total_cargas_pesos INTEGER NOT NULL,
    creado_en_utc TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 2. CABECERAS DE VENTAS HISTÓRICAS
CREATE TABLE IF NOT EXISTS historico_tickets (
    id TEXT PRIMARY KEY,
    fecha_hora_local TEXT NOT NULL,
    fecha_hora_utc TEXT NOT NULL,
    total_venta INTEGER NOT NULL CHECK (total_venta >= 0),
    monto_efectivo INTEGER NOT NULL CHECK (monto_efectivo >= 0),
    monto_transferencia INTEGER NOT NULL CHECK (monto_transferencia >= 0),
    metodo_pago TEXT NOT NULL,
    es_nocturno INTEGER NOT NULL DEFAULT 0,
    balance_pago_diferencia INTEGER NOT NULL DEFAULT 0,
    tiene_items INTEGER NOT NULL DEFAULT 1 CHECK (tiene_items IN (0, 1)),
    origen_fila_excel INTEGER NOT NULL,
    creado_en_utc TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_hist_tickets_fecha_utc ON historico_tickets (fecha_hora_utc);
CREATE INDEX IF NOT EXISTS idx_hist_tickets_fecha_local ON historico_tickets (fecha_hora_local);

-- 3. DETALLE DE ÍTEMS VENDIDOS HISTÓRICO
CREATE TABLE IF NOT EXISTS historico_ticket_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id TEXT NOT NULL,
    ticket_id_correlacionado TEXT,
    fecha_hora_local TEXT NOT NULL,
    fecha_hora_utc TEXT NOT NULL,
    producto_nombre TEXT NOT NULL,
    cantidad REAL, -- NULLABLE para tolerar los 1.220 pesables/fraccionados por importe
    unidad TEXT DEFAULT 'Unidad',
    precio_unitario INTEGER NOT NULL CHECK (precio_unitario >= 0),
    subtotal INTEGER NOT NULL CHECK (subtotal >= 0),
    metodo_pago TEXT,
    calculo_subtotal_coincide INTEGER NOT NULL DEFAULT 1 CHECK (calculo_subtotal_coincide IN (0, 1)),
    es_duplicado_deriva INTEGER NOT NULL DEFAULT 0 CHECK (es_duplicado_deriva IN (0, 1)),
    origen_fila_excel INTEGER NOT NULL,
    creado_en_utc TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_hist_items_ticket ON historico_ticket_items (ticket_id);
CREATE INDEX IF NOT EXISTS idx_hist_items_producto ON historico_ticket_items (producto_nombre);
CREATE INDEX IF NOT EXISTS idx_hist_items_fecha_utc ON historico_ticket_items (fecha_hora_utc);

-- 4. CARGAS Y RECARGAS DE SERVICIOS HISTÓRICAS
CREATE TABLE IF NOT EXISTS historico_cargas_servicios (
    id TEXT PRIMARY KEY, -- Clave sintética hash determinista
    fecha_hora_local TEXT NOT NULL,
    fecha_hora_utc TEXT NOT NULL,
    servicio TEXT NOT NULL,
    monto_carga INTEGER NOT NULL CHECK (monto_carga > 0),
    monto_adicional INTEGER NOT NULL DEFAULT 0 CHECK (monto_adicional >= 0),
    total_cobrado INTEGER NOT NULL CHECK (total_cobrado >= 0),
    pago_efectivo INTEGER NOT NULL DEFAULT 0 CHECK (pago_efectivo >= 0),
    pago_transferencia INTEGER NOT NULL DEFAULT 0 CHECK (pago_transferencia >= 0),
    balance_pago_diferencia INTEGER NOT NULL DEFAULT 0,
    fingerprint_sha256 TEXT NOT NULL UNIQUE,
    origen_fila_excel INTEGER NOT NULL,
    creado_en_utc TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_hist_cargas_fecha_utc ON historico_cargas_servicios (fecha_hora_utc);
CREATE INDEX IF NOT EXISTS idx_hist_cargas_servicio ON historico_cargas_servicios (servicio);
```

---

### 2.2. Mapeo campo por campo y reglas de conversión

#### A. Cabeceras (`DB_MOVIMIENTOS_CAJA` $\rightarrow$ `historico_tickets`)

| Campo Origen (Excel) | Campo Destino (SQL) | Tipo de Dato | Nulabilidad | Regla de Conversión | Validación de Integridad | Tratamiento de Datos Ambiguos |
| :--- | :--- | :--- | :---: | :--- | :--- | :--- |
| `TICKET_ID` | `id` | `TEXT` | NOT NULL | Conversión a cadena limpia (`str(val).strip()`). | Debe comenzar con `T-` y ser único en la tabla. | No hay duplicados en el origen. |
| `FECHA` | `fecha_hora_local` | `TEXT` | NOT NULL | Formato ISO local `YYYY-MM-DD HH:MM:SS`. | Parseo estricto `datetime`. | Se almacena la fecha real registrada. |
| `FECHA` | `fecha_hora_utc` | `TEXT` | NOT NULL | Conversión determinista de `America/Argentina/Jujuy` a UTC (`YYYY-MM-DD HH:MM:SS`). | Garantiza alineación horaria. | Si falta hora, se asume medianoche local antes de convertir. |
| `TOTAL_VENTA` | `total_venta` | `INTEGER` | NOT NULL | Entero en pesos (`int(round(float(val)))`). | Debe ser $\ge 0$. | Rechazo ante nulos o no numéricos. |
| `MONTO_EFECTIVO` | `monto_efectivo`| `INTEGER` | NOT NULL | Entero en pesos (`int(round(float(val or 0)))`). | Debe ser $\ge 0$. | Si viene vacío, se asigna `0`. |
| `MONTO_TRANSF` | `monto_transferencia`| `INTEGER` | NOT NULL | Entero en pesos (`int(round(float(val or 0)))`). | Debe ser $\ge 0$. | Si viene vacío, se asigna `0`. |
| *Derivado* | `metodo_pago` | `TEXT` | NOT NULL | `'EFECTIVO'` si transf=0; `'TRANSFERENCIA'` si efvo=0; `'MIXTO'` si ambos > 0. | `IN ('EFECTIVO', 'TRANSFERENCIA', 'MIXTO')`. | Respeta la imputación real. |
| `ES_NOCTURNO` | `es_nocturno` | `INTEGER` | NOT NULL | Entero binario (`1` si val=1, sino `0`). | `IN (0, 1)`. | 7.243 filas son 0; 3 filas son 1. |
| *Derivado* | `balance_pago_diferencia` | `INTEGER` | NOT NULL | `total_venta - (monto_efectivo + monto_transferencia)`. | Debe ser entero. | Conserva las 4 inconsistencias sin forzar corrección arbitraria. |
| *Derivado* | `tiene_items` | `INTEGER` | NOT NULL | `0` para los 29 tickets sin ítems; `1` para los 7.217 restantes. | `IN (0, 1)`. | Identifica tickets sin detalle. |
| *Metadato* | `origen_fila_excel` | `INTEGER` | NOT NULL | Número de fila en la hoja Excel (ej. 2..7247). | Debe ser $> 1$. | Auditoría y reproducibilidad. |

#### B. Detalle de Ítems (`DB_HISTORIAL_ITEMS` $\rightarrow$ `historico_ticket_items`)

| Campo Origen (Excel) | Campo Destino (SQL) | Tipo de Dato | Nulabilidad | Regla de Conversión | Validación de Integridad | Tratamiento de Datos Ambiguos |
| :--- | :--- | :--- | :---: | :--- | :--- | :--- |
| `TICKET_ID` | `ticket_id` | `TEXT` | NOT NULL | Cadena limpia `str(val).strip()`. | Preserva el ID exacto original. | **Sin FK estricta** para no rechazar los 6 tickets con deriva temporal. |
| *Derivado* | `ticket_id_correlacionado` | `TEXT` | NULLABLE | ID de la cabecera correlacionada por proximidad temporal (< 1s). | Cadena válida o NULL. | NULL si coincide directamente; ID de CAJA si hubo deriva. |
| `FECHA` | `fecha_hora_local` | `TEXT` | NOT NULL | Formato ISO local `YYYY-MM-DD HH:MM:SS`. | Parseo estricto `datetime`. | Se preserva la fecha registrada en el ítem. |
| `FECHA` | `fecha_hora_utc` | `TEXT` | NOT NULL | Conversión de Jujuy a UTC. | Alineación horaria internacional. | Manejo estándar de huso horario. |
| `PRODUCTO` | `producto_nombre`| `TEXT` | NOT NULL | Normalización de espacios (`str(val).strip()`). | No vacío. | **Sin FK a productos:** 2.046 filas no están en el catálogo. Se guarda como texto congelado. |
| `CANTIDAD` | `cantidad` | `REAL` | **NULLABLE** | `float(val)` si val > 0, sino `NULL`. | Valor $> 0$ o `NULL`. | **Crítico:** Tolera `NULL` para las 1.220 filas vendidas por importe fijo en pesos. |
| `UNIDAD` | `unidad` | `TEXT` | NOT NULL | Texto original (`'Unidad'`, `'Gramos'`). | Por defecto `'Unidad'` si viene nulo. | Preserva la unidad informada. |
| `PRECIO_UNIT` | `precio_unitario`| `INTEGER` | NOT NULL | Entero en pesos (`int(round(float(val)))`). | Debe ser $\ge 0$. | Si viene nulo, se imputa `subtotal`. |
| `SUBTOTAL` | `subtotal` | `INTEGER` | NOT NULL | Entero en pesos (`int(round(float(val)))`). | Debe ser $\ge 0$. | **Monto financiero soberano del ítem.** |
| `METODO_PAGO` | `metodo_pago` | `TEXT` | NULLABLE | Texto normalizado (`'EFECTIVO'`, `'TRANSFERENCIA'`, `'MIXTO'`). | Limpieza de espacios finales (corrige `'TRANSFERENCIA '`). | Preserva el dato original. |
| *Derivado* | `calculo_subtotal_coincide` | `INTEGER` | NOT NULL | `1` si `cantidad * precio_unitario == subtotal`, sino `0`. | `IN (0, 1)`. | `0` para las 1.385 discrepancias explicadas. |
| *Derivado* | `es_duplicado_deriva` | `INTEGER` | NOT NULL | `1` para las 6 filas con ID derivado reinsertado, sino `0`. | `IN (0, 1)`. | Permite excluir duplicados al calcular estadísticas. |
| *Metadato* | `origen_fila_excel` | `INTEGER` | NOT NULL | Número de fila en la hoja Excel (2..11567). | $> 1$. | Trazabilidad exacta. |

#### C. Cargas y Servicios (`DB_CARGAS` $\rightarrow$ `historico_cargas_servicios`)

| Campo Origen (Excel) | Campo Destino (SQL) | Tipo de Dato | Nulabilidad | Regla de Conversión | Validación de Integridad | Tratamiento de Datos Ambiguos |
| :--- | :--- | :--- | :---: | :--- | :--- | :--- |
| *Derivado* | `id` | `TEXT` | NOT NULL | Prefijo `HC-` seguido de los primeros 16 caracteres del hash determinista. | Clave primaria única. | Evita colisiones. |
| `FECHA` | `fecha_hora_local` | `TEXT` | NOT NULL | Formato ISO local `YYYY-MM-DD HH:MM:SS`. | Parseo estricto `datetime`. | Preserva el momento exacto. |
| `FECHA` | `fecha_hora_utc` | `TEXT` | NOT NULL | Conversión de Jujuy a UTC. | Alineación horaria. | Manejo estándar. |
| `SERVICIO` | `servicio` | `TEXT` | NOT NULL | Cadena limpia (`str(val).strip()`). | No vacío. | SUBE, Claro, SUBE (MP), Personal, Otro, Movistar. |
| `MONTO_CARGA` | `monto_carga` | `INTEGER` | NOT NULL | Entero en pesos (`int(round(float(val)))`). | Debe ser $> 0$. | Representa el capital recargado. |
| `MONTO_ADICIONAL`| `monto_adicional`| `INTEGER` | NOT NULL | Entero en pesos (`int(round(float(val or 0)))`). | Debe ser $\ge 0$. | Comisión/ganancia del servicio. |
| `TOTAL_COBRADO` | `total_cobrado` | `INTEGER` | NOT NULL | Entero en pesos (`int(round(float(val)))`). | Debe ser $\ge 0$. | $100\%$ coincide con `carga + adic`. |
| `PAGO_EFVO` | `pago_efectivo` | `INTEGER` | NOT NULL | Entero en pesos (`int(round(float(val or 0)))`). | Debe ser $\ge 0$. | Si viene vacío, `0`. |
| `PAGO_TRANSF` | `pago_transferencia`| `INTEGER` | NOT NULL | Entero en pesos (`int(round(float(val or 0)))`). | Debe ser $\ge 0$. | Si viene vacío, `0`. |
| *Derivado* | `balance_pago_diferencia` | `INTEGER` | NOT NULL | `total_cobrado - (pago_efectivo + pago_transferencia)`. | Debe ser entero. | Preserva la discrepancia de $400 en fila 853. |
| *Derivado* | `fingerprint_sha256` | `TEXT` | NOT NULL | Hash criptográfico unívoco de los datos de la fila. | Clave única (`UNIQUE`). | Garantía de no duplicación. |
| *Metadato* | `origen_fila_excel` | `INTEGER` | NOT NULL | Número de fila en la hoja Excel (2..2132). | $> 1$. | Trazabilidad exacta. |

---

## 3. ESTRATEGIA DE IDEMPOTENCIA Y CLAVES DETERMINISTAS

Dado que `DB_CARGAS` carece de una clave primaria nativa y que no debe utilizarse exclusivamente el número de fila física de Excel como identidad permanente (pues variaría si el usuario reordena o limpia filas en el futuro), se define un mecanismo estricto de identificación determinista multinivel:

```
                               ┌─────────────────────────────────────────────────────────────┐
                               │                 Archivo Origen Excel                        │
                               │ SHA-256: 2e2c046e7f229b47e5bcce3b0cf4bda90bf63be3eec90d... │
                               └──────────────────────────────┬──────────────────────────────┘
                                                              │
                                            ¿SHA-256 existe en historico_lotes?
                                                              │
                                    ┌─────────────────────────┴─────────────────────────┐
                                    ▼ SÍ                                                ▼ NO
                      ┌───────────────────────────┐                       ┌───────────────────────────┐
                      │    ABORTAR INMEDIATO:     │                       │     Procesar Filas con    │
                      │   Lote ya importado.      │                       │  Fingerprint Determinista │
                      └───────────────────────────┘                       └─────────────┬─────────────┘
                                                                                        │
                                    ┌───────────────────────────────────────────────────┴───────────────────────────────────────────────────┐
                                    ▼                                                   ▼                                                   ▼
                      ┌───────────────────────────┐                       ┌───────────────────────────┐                       ┌───────────────────────────┐
                      │    historico_tickets      │                       │ historico_ticket_items    │                       │historico_cargas_servicios │
                      │ PK: TICKET_ID nativo      │                       │ Clave:                    │                       │ Clave:                    │
                      │ (7.246 IDs únicos)        │                       │ ticket_id + hash(detalle) │                       │ Fingerprint SHA-256       │
                      │                           │                       │                           │                       │ de contenido normalizado  │
                      └───────────────────────────┘                       └───────────────────────────┘                       └───────────────────────────┘
```

### 3.1. Clave determinista para Cargas y Servicios (`DB_CARGAS`)

Para cada registro de carga, se construye un contenido canónico normalizado que incluye la fecha UTC, el servicio, los importes y un índice ordinal de ocurrencia determinista para resolver eventos simultáneos:

$$\text{Canónico} = \text{fecha\_utc} \parallel \text{"|"} \parallel \text{servicio\_normalizado} \parallel \text{"|"} \parallel \text{monto\_carga} \parallel \text{"|"} \parallel \text{monto\_adic} \parallel \text{"|"} \parallel \text{total\_cobrado} \parallel \text{"|"} \parallel \text{pago\_efvo} \parallel \text{"|"} \parallel \text{pago\_transf} \parallel \text{"|#"} \parallel \text{ordinal\_en\_segundo}$$

- `ordinal_en_segundo`: Si existen dos cargas idénticas registradas en el mismo segundo exacto, se numeran secuencialmente ($1, 2, \dots$) según su aparición determinista en el archivo.
- `fingerprint_sha256`: $\text{SHA256}(\text{Canónico})$.
- `id`: `HC-` + $\text{fingerprint\_sha256}[0..15]$.

*Resultado:* El identificador es reproducible en un 100% independientemente de cómo se lea el archivo y no depende de la posición física mutable de la fila.

### 3.2. Prevención de importación duplicada accidental

1. **Barrera de Lote (`historico_lotes`):**  
   Antes de evaluar fila por fila, el motor verifica si el `source_sha256` del archivo ya fue registrado en `historico_lotes`. Si existe, la ejecución se detiene de inmediato informando que el archivo ya se encuentra persistido de forma idéntica.
2. **Restricción `UNIQUE` en base de datos:**  
   - `historico_tickets.id` posee restricción `PRIMARY KEY`.
   - `historico_cargas_servicios.fingerprint_sha256` posee restricción `UNIQUE`.
   Cualquier intento de reinserción disparará un conflicto de unicidad a nivel de motor de base de datos.
3. **Comprobación de Paridad de Contenido:**  
   Si se intentara una carga con un archivo diferente pero con filas coincidentes, el proceso detectará si una clave ya existente presenta importes o fechas distintas, arrojando una excepción de tipo `ConflictoIdentidadOrigen` y abortando la transacción mediante `ROLLBACK`.

---

## 4. VERIFICACIÓN DEL AISLAMIENTO OPERATIVO

A partir de la inspección del código local existente en el repositorio, se verificó exactamente qué consultas alimentan cada componente de GenaroPOS en la actualidad:

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                                        GENAROPOS BACKEND & TURSO                                                       │
├───────────────────────────────────────────────────────────────────────┬────────────────────────────────────────────────────────────────┤
│                     CIRCUITO OPERATIVO EN VIVO                        │                   CIRCUITO HISTÓRICO AISLADO                   │
│                (Tablas actuales — CERO MODIFICACIONES)                │             (Nuevas tablas — ESTRICTO SOLO LECTURA)            │
├───────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────┤
│  • Ventas y Cobro:              tickets, ticket_items, outbox_events  │  • Facturación Pasada:        historico_tickets                │
│  • Cargas y Recargas en Vivo:   cargas_servicios                      │  • Desglose de Artículos:     historico_ticket_items           │
│  • Arqueo y Cierres Diarios:    cierres_diarios, gastos_retiros       │  • Recargas 2025-2026:        historico_cargas_servicios       │
│  • Catálogo y Precios:          productos (precios vigentes)          │                                                                │
│  • Dashboard Hoy / 7d / 30d:    db/reports.py (sin cambios)           │  • Estadísticas Históricas:   db/historical_reports.py (nuevo) │
└───────────────────────────────────────────────────────────────────────┴────────────────────────────────────────────────────────────────┘
```

### 4.1. Mapeo de consultas existentes que PERMANECEN SIN CAMBIOS

1. **Dashboard Financiero (`db/reports.py`):**
   - Función: `calcular_metricas_dashboard_turso()`.
   - Consultas operativas:
     ```sql
     SELECT id, fecha_hora_utc, total_venta, monto_efectivo, monto_transferencia 
     FROM tickets WHERE anulado = 0 AND substr(fecha_hora_utc, 1, 10) >= ? AND substr(fecha_hora_utc, 1, 10) <= ?;
     ```
     ```sql
     SELECT fecha_hora_utc, servicio, monto_carga, monto_adicional, total_cobrado, pago_efectivo, pago_transferencia 
     FROM cargas_servicios WHERE anulado = 0 AND substr(fecha_hora_utc, 1, 10) >= ? AND substr(fecha_hora_utc, 1, 10) <= ?;
     ```
   - **Regla:** Estas consultas **no se tocan**. Continúan consultando únicamente `tickets` y `cargas_servicios`.

2. **Cobro y Transacciones en Vivo (`db/sales.py`):**
   - Inserción atómica de tickets en vivo con cálculo estricto de balance (`chk_ticket_balance`) y registro de eventos outbox (`outbox_events`).
   - **Regla:** Ningún dato histórico entra a `tickets` ni a `ticket_items`. La tabla `tickets` mantiene su integridad operacional para el cajero.

3. **Arqueo y Cierres Diarios (`db/expenses.py` y `cierres_diarios`):**
   - La función `calcular_cierre_diario_esperado()` calcula el efectivo esperado sumando las ventas en efectivo del día en `tickets` más las cargas en efectivo en `cargas_servicios`, menos los egresos de `gastos_retiros`.
   - **Garantía:** Al no haber datos históricos en `tickets` ni en `cargas_servicios`, el arqueo del cajón físico del local jamás será alterado.

4. **Catálogo y Stock (`db/catalog.py`):**
   - Consulta `SELECT id, nombre, categoria, proveedor, unidad, costo, precio_dia FROM productos WHERE activo = 1`.
   - **Garantía:** El catálogo comercial vigente no se mezcla con los 816 productos antiguos de `DB_PRODUCTOS`.

### 4.2. Nuevas consultas que serán EXCLUSIVAMENTE HISTÓRICAS

Se creará en una fase posterior un módulo dedicado (ej. `db/historical_reports.py`) con consultas que operen **únicamente** sobre el namespace histórico:

1. **Evolución mensual de facturación histórica:**
   ```sql
   SELECT 
       substr(fecha_hora_local, 1, 7) AS mes,
       COUNT(*) AS total_tickets,
       SUM(total_venta) AS facturacion_total,
       SUM(monto_efectivo) AS facturacion_efectivo,
       SUM(monto_transferencia) AS facturacion_transferencia
   FROM historico_tickets
   GROUP BY substr(fecha_hora_local, 1, 7)
   ORDER BY mes ASC;
   ```
2. **Ranking de productos más vendidos en el histórico (por nombre):**
   ```sql
   SELECT 
       producto_nombre,
       COUNT(*) AS veces_vendido,
       SUM(CASE WHEN cantidad IS NOT NULL THEN cantidad ELSE 0 END) AS unidades_registradas,
       SUM(subtotal) AS recaudacion_total
   FROM historico_ticket_items
   WHERE es_duplicado_deriva = 0
   GROUP BY producto_nombre
   ORDER BY recaudacion_total DESC
   LIMIT 50;
   ```
3. **Volumen y comisiones de recargas por servicio:**
   ```sql
   SELECT 
       servicio,
       COUNT(*) AS total_operaciones,
       SUM(monto_carga) AS capital_recargado,
       SUM(monto_adicional) AS ganancia_adicional,
       SUM(total_cobrado) AS total_cobrado
   FROM historico_cargas_servicios
   GROUP BY servicio
   ORDER BY total_cobrado DESC;
   ```

---

## 5. MATRIZ DE ESTADÍSTICAS HISTÓRICAS CONFIABLES

| Métrica Estadística | Estado de Confianza | Cifra Calculada Verificada | Fuente y Agrupación | Advertencias Obligatorias en UI / Reportes |
| :--- | :---: | :---: | :--- | :--- |
| **Facturación Total de Ventas** | **ALTA (100% Confiable)** | **$23.623.890,00** | `historico_tickets`, suma de `total_venta` (7.246 tickets). | Ninguna sobre el total. Cifra de facturación oficial del período. |
| **Recaudación por Medio de Pago** | **ALTA (99,97% Confiable)** | Efectivo: **$16.789.820,00**<br>Transf.: **$6.840.970,00** | `historico_tickets`, suma de `monto_efectivo` y `monto_transferencia`. | Advertencia visible: *Existe una diferencia neta de +$6.900 (+0,03%) entre la suma de pagos ($23.630.790) y las ventas ($23.623.890) debido a 4 tickets con registro mixto no balanceado.* |
| **Recaudación Total de Ítems** | **ALTA (Con Salvedad)** | **$23.522.590,00** | `historico_ticket_items`, suma de `subtotal` (11.566 líneas). | **Regla estricta:** Nunca presentar la suma de ítems como si fuera idéntica a la facturación de cabecera. Advertencia: *Difiere en -$101.300 (-0,43%) respecto a cabeceras debido a 29 tickets sin desglose ($117.400) y 6 tickets con reintentos temporales.* |
| **Total de Recargas Virtuales** | **ALTA (100% Confiable)** | **$9.556.400,00** | `historico_cargas_servicios`, suma de `total_cobrado` (1.741 ops). | Desglose exacto: Capital $9.069.600,00 y Adicional $486.800,00 en 100% de paridad de componentes. |
| **Pagos de Recargas** | **ALTA (99,99% Confiable)** | Efectivo: **$9.115.900,00**<br>Transf.: **$440.100,00** | `historico_cargas_servicios`. | Advertencia: *Diferencia de -$400 en una recarga SUBE MP del 22/04/2026 (cobro $7.400, pago registrado $7.000).* |
| **Volumen de Ventas por Producto** | **MEDIA (Informativa por Nombre)** | 856 productos registrados | `historico_ticket_items` agrupado por `producto_nombre`. | Advertencia: *2.046 líneas (17,7%) corresponden a nombres de productos fuera de catálogo. 1.220 líneas no registran unidades físicas por haberse vendido por importe fijo en pesos.* |
| **Ganancia Bruta Real de Ventas** | **NO CALCULABLE (Incierta)** | **N/A** | Ausente en origen. | **Prohibido estimar:** La hoja de ítems histórica no registraba costo unitario. La estimación del 10% del Visor original era un valor ficticio no contable. No debe presentarse ganancia bruta histórica. |
| **Egresos y Ganancia Neta** | **NO CALCULABLE (Incierta)** | **N/A** | Hoja `GASTOS_RETIROS` excluida y descontinuada. | No se dispone de registro fidedigno de gastos del local para el período completo. |

---

## 6. PLAN DE SIMULACIÓN Y CRITERIOS DE ACEPTACIÓN (DRY-RUN)

Para cuando se autorice una etapa posterior de prueba, se define el siguiente protocolo de simulación en modo de **estricto dry-run** (cero escrituras permanentes):

### 6.1. Procedimiento de Dry-Run

1. **Paso 1: Validación Criptográfica del Origen:**  
   Calcular el hash SHA-256 de `SISTEMA_GENARO_v4_PRODUCCION.xlsx`. Si no coincide exactamente con `2e2c046e7f229b47e5bcce3b0cf4bda90bf63be3eec90dcfc3cbe0f4cfdfb12e`, el proceso aborta inmediatamente.
2. **Paso 2: Creación de Esquema en Base Temporal en Memoria (`:memory:`):**  
   Ejecutar el DDL histórico en una base de datos SQLite en memoria independiente de la base de datos de producción o desarrollo.
3. **Paso 3: Parseo y Carga en Memoria:**  
   Transformar las filas de Excel según las reglas de conversión definidas en la Sección 2.2 e insertarlas en las tablas temporales en memoria.
4. **Paso 4: Verificación Automática de Criterios de Aceptación:**  
   Ejecutar queries de conciliación matemática contra las tablas en memoria.
5. **Paso 5: Comprobación de No Regresión Operativa:**  
   Verificar que las tablas operativas de GenaroPOS no hayan sufrido ninguna lectura, escritura o bloqueo.

### 6.2. Criterios de Aceptación Numéricos Obligatorios

| Categoría | Métrica de Aceptación | Valor Esperado Exacto | Margen de Tolerancia |
| :--- | :--- | :---: | :---: |
| **Tickets Cabecera** | Total de registros en `historico_tickets` | **7.246** | Cero |
| **Tickets Cabecera** | Suma de `total_venta` | **$23.623.890,00** | Cero |
| **Tickets Cabecera** | Registros con `balance_pago_diferencia != 0` | **4** | Cero |
| **Tickets Cabecera** | Registros con `tiene_items = 0` | **29** | Cero |
| **Detalle de Ítems** | Total de registros en `historico_ticket_items` | **11.566** | Cero |
| **Detalle de Ítems** | Suma de `subtotal` | **$23.522.590,00** | Cero |
| **Detalle de Ítems** | Registros con `cantidad IS NULL` | **1.220** | Cero |
| **Detalle de Ítems** | Registros con `es_duplicado_deriva = 1` | **6** | Cero |
| **Cargas y Servicios**| Total de registros en `historico_cargas_servicios` | **1.741** | Cero |
| **Cargas y Servicios**| Suma de `total_cobrado` | **$9.556.400,00** | Cero |
| **Cargas y Servicios**| Suma de `monto_carga` + `monto_adicional` | **$9.556.400,00** | Cero |
| **Cargas y Servicios**| Registros con `balance_pago_diferencia != 0` | **1** | Cero |
| **Aislamiento Operativo**| Modificaciones en `tickets`, `cargas_servicios`, `productos` | **0** | Cero |

---

## 7. RIESGOS Y DECISIONES PENDIENTES

### 7.1. Riesgos Identificados

1. **Riesgo de confusión entre facturación y suma de detalle:**  
   *Mitigación:* La interfaz y los reportes deben mostrar claramente que la facturación real auditada es la de cabeceras ($23.623.890), explicando que el detalle de artículos ($23.522.590) presenta un faltante de $101.300 por ventas globales sin desglose.
2. **Riesgo de cálculo erróneo de unidades físicas vendidas:**  
   *Mitigación:* Al calcular rankings de volumen por producto, el sistema debe indicar explícitamente cuántas operaciones se realizaron por peso/importe sin cantidad registrada (1.220 casos de panificados y quesos).
3. **Riesgo de colisión con bases remotas:**  
   *Mitigación:* El procedimiento futuro solo operará de manera local o sobre la base autorizada bajo inyección explícita de dependencias, sin variables de entorno no controladas.

### 7.2. Decisiones Pendientes que Requieren Autorización del Propietario

1. **Exposición en la Interfaz de Usuario:**  
   ¿Desea el propietario una pestaña o sección diferenciada en el frontend (ej. *"Estadísticas Históricas 2025–2026"*) que consulte estas tablas aisladas, o prefiere que el histórico se conserve únicamente accesible a nivel de base de datos para auditoría?
2. **Tratamiento de los 4 registros de ítems duplicados por reintento de script:**  
   En la especificación técnica se propone marcarlos con `es_duplicado_deriva = 1` para que las estadísticas de productos vendidos no los cuenten doblemente ($16.200 en total). Se requiere conformidad con este criterio de exclusión estadística.
3. **Confirmación de Exclusión Definitiva de `GASTOS_RETIROS`:**  
   Se ratifica que no se importará ningún dato de gastos pasados.

---

## 8. DECLARACIÓN DE NO INTERVENCIÓN Y ESTADO DEL SISTEMA

Se certifica formalmente que:
- El archivo `SISTEMA_GENARO_v4_PRODUCCION.xlsx` permanece exactamente en su estado original (Checksum MD5: `851599ddf096b662dedf7ab5f820f444`).
- No se han realizado escrituras ni consultas en `genaro-pos-live-ops` ni en `genaro-pos-prod`.
- No se ha modificado ningún archivo de código, dependencia ni esquema operativo del repositorio.
- No se han ejecutado scripts de migración, importación ni pruebas sobre bases de datos vivas.
- No se han realizado commits ni envíos (`push`) a ningún repositorio remoto.
- No se ha subido información a ningún servicio externo.

---
*Fin de la Especificación Técnica. Esperando autorización expresa del usuario antes de proceder con cualquier fase posterior.*
