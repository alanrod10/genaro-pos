# INFORME DE AUDITORÍA TÉCNICA Y ECONÓMICA INTEGRAL — GENAROPOS V4
## Etapa I: Auditoría de Solo Lectura (Staging / Laboratorio Aislado)

**Proyecto:** GenaroPOS V4  
**Fecha:** 06/11/2026  
**Entorno:** Exclusivamente STAGING / Laboratorio Aislado  
**Rama Git:** `feature/staging-central-accounting`  
**Commit Auditado:** [`0cafea8`](file:///home/alanrod10/workspace/genaro-pos-turso-lab) (`0cafea8ec39e3a6005895dc372cad1e66e381606`)  
**Commit Base:** [`17ec293`](file:///home/alanrod10/workspace/genaro-pos-turso-lab) (`17ec293ffac5cd603cdbb5d8fc47ea87c40ee144`)  
**Estado del Repositorio:** Working tree 100% limpio (`working tree clean`)  
**Modalidad:** SOLO LECTURA. Cero escrituras, cero migraciones, cero conexiones remotas a Producción.

---

## 1. IDENTIDAD DEL CÓDIGO Y AISLAMIENTO DE PRODUCCIÓN

Se verificó mediante inspección directa de Git:
1. **Rama actual:** `feature/staging-central-accounting`
2. **HEAD actual:** `0cafea8ec39e3a6005895dc372cad1e66e381606`
3. **Commit base:** `17ec293ffac5cd603cdbb5d8fc47ea87c40ee144`
4. **Relación de ancestría:** Descendiente directo sin bifurcaciones huérfanas (`git merge-base = 17ec293`).
5. **Estado del árbol:** Completamente limpio (`git status --porcelain` retorna vacío).
6. **Aislamiento de Producción:** Se verificó que todas las variables, scripts y comandos apuntan exclusivamente a rutas locales (`db/genaro-pos-dev-lab.db`, `db/tesoreria_lab.db`, `localhost:8000`). No existen procesos conectados a `genaro-pos-live-ops`, `genaro-pos-prod` ni `genaro-pos-live-api`.

---

## 2. AUDITORÍA DE ARQUITECTURA Y FUENTE DE VERDAD CONTABLE

### 2.1. Modelo de Autoridad Única
- **Canales Comerciales (Frontend React, Cloudflare Worker API, FastAPI Routers):** Operan como emisores de eventos operacionales hacia la tabla `commercial_events_outbox`.
- **Motor Central de Contabilización ([`tesoreria/commercial_bridge_worker.py`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/tesoreria/commercial_bridge_worker.py)):** Es la única autoridad que genera asientos de partida doble en el libro mayor (`tesoreria_movimientos`), calcula la actualización atómica de saldos en `tesoreria_fondos` y audita las operaciones en `tesoreria_auditoria`.
- **Evaluación:** La centralización del motor contable en Python es un acierto estructural probado, eliminando el riesgo de discrepancias matemáticas entre JavaScript y Python.

---

## 3. AUDITORÍA CRÍTICA DE ATOMICIDAD COMERCIAL + OUTBOX

Se inspeccionaron línea por línea todas las funciones de escritura comercial y emisión de eventos.

### 3.1. FastAPI Backend (Python)
- **Ventas ([`db/sales.py`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/db/sales.py)):** `registrar_venta_turso`, `anular_venta_turso` y `editar_item_venta_turso` ejecutan las operaciones comerciales, auditorías y `enqueue_commercial_event` sobre el mismo objeto de conexión SQLite/Turso (`with _conectar_turso() as conexion`), cerrando con `conexion.commit()` en bloque y `conexion.rollback()` ante cualquier error. **ATOMICIDAD DEMOSTRADA**.
- **Servicios ([`db/services.py`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/db/services.py)):** `guardar_cargas_turso` y `guardar_ediciones_cargas_turso` ejecutan la inserción comercial y el outbox en la misma transacción atómica. **ATOMICIDAD DEMOSTRADA**.
- **Gastos ([`db/expenses.py`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/db/expenses.py)):** `registrar_gasto_turso`, `editar_gasto_turso` y `anular_gasto_turso` ejecutan la inserción comercial, auditoría y outbox bajo una misma transacción con `commit`/`rollback`. **ATOMICIDAD DEMOSTRADA**.

### 3.2. Cloudflare Worker Adapter (JavaScript)
- **Ventas Worker:** `registrarVentaIdempotente`, `anularVenta` y `editarItemVenta` utilizan `client.batch(statements, "write")` que empaqueta las modificaciones de tickets y el `enqueueOutboxStatement` en un único envío atómico a libSQL/Turso. **ATOMICIDAD DEMOSTRADA**.
- **Anulación y Edición de Servicios y Gastos Worker:** `anularCargaServicio`, `editarCargaServicio` y `anularGasto` utilizan `client.batch([...], "write")` con el `outboxStmt`. **ATOMICIDAD DEMOSTRADA**.
- **Alta de Gastos Worker (`registrarGastoIdempotente`):**
  - Ubicación: [`experiments/cloudflare-worker-prototype/src/db/adapter.js:1531-1563`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/experiments/cloudflare-worker-prototype/src/db/adapter.js#L1531-L1563).
  - Comportamiento: Ejecuta `await client.execute({ sql: "INSERT INTO gastos_retiros ..." })` de forma aislada. Luego, de manera independiente, ejecuta `await client.execute(outboxStmt).catch(() => {});`.
  - **Fallo:** No están en un lote atómico (`client.batch`), y el bloque `.catch(() => {})` silencia fallos. Si la segunda llamada falla por corte de red o error de BD, el gasto queda registrado comercialmente pero **nunca se emite al Outbox financiero**.
  - **Clasificación:** 🚨 **CRÍTICO (`AUD-001`)**.
- **Alta de Servicios Worker (`registrarCargaIdempotente`):**
  - Ubicación: [`experiments/cloudflare-worker-prototype/src/db/adapter.js:1067-1141`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/experiments/cloudflare-worker-prototype/src/db/adapter.js#L1067-L1141).
  - Comportamiento: Ejecuta `await client.execute({ sql: "INSERT INTO cargas_servicios ..." })` y posteriormente, de manera separada, la inserción del Outbox.
  - **Fallo:** Falta de agrupación atómica en `client.batch`. Si ocurre una interrupción entre ambas sentencias, la carga existe pero no el evento Outbox.
  - **Clasificación:** ⚠️ **ALTO (`AUD-002`)**.

---

## 4. AUDITORÍA ECONÓMICA DE FONDOS, CIRCUITOS Y RECARGAS

Frente a la **Fuente de Verdad Económica confirmada por el Propietario**, se verificó el comportamiento del motor contable:

### 4.1. Segregación de Fondos Físicos y Digitales
- **Fondos Físicos:**
  - `1110` (Drugstore Diario): Inicia en cero; recibe efectivo de ventas y cobros en mostrador.
  - `1120` (Drugstore Acumulado): Recibe el barrido de 1110; atiende gastos generales, pago de cuentas y reposiciones.
  - `1130` (Prepay Acumulado): Recibe el barrido de 1160; reservado exclusivamente para liquidar a David.
  - `1140` (Adicionales Efectivo): Destinado exclusivamente a la ganancia de adicionales cobrados.
  - `1150` (Claro Físico): Recibe el capital de Claro Comercios bajo custodia estricta para Ezequiel.
  - `1160` (Prepay Diario): Recibe el capital de recargas Prepay del día.
- **Cuentas Digitales:**
  - `1210` (Mercado Pago Propio), `1220` (Prepay Digital Saldo), `1230` (Claro Comercios Digital), `1310` (Tránsito Preventistas).

### 4.2. Tratamiento de Capital vs. Adicional
- **Regla del Propietario:** Toda recarga se descompone en Capital (saldo de la plataforma, no es ganancia) y Adicional (ganancia del negocio que va a `1140`).
- **Comportamiento Auditado en Código:**
  - En [`tesoreria/prepay_coordinator.py:908-922`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/tesoreria/prepay_coordinator.py#L908-L922), cuando opera la pre-reserva, el adicional sí se desvía a `1140` y el capital a `1160`.
  - Sin embargo, en el circuito directo de [`tesoreria/commercial_bridge_worker.py:381-394, 521-534, 620-633`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/tesoreria/commercial_bridge_worker.py#L381-L394), el cobro en efectivo ingresa como un monto global (`pago_efvo` = capital + adicional) a `1110`, `1130` o `1150`, **sin desglosar el adicional a `1140`**.
  - **Clasificación:** ⚠️ **ALTO (`AUD-004`)**.

### 4.3. Recargas Pagadas por Transferencia
- **Regla del Propietario:**
  - PREPAY vía transferencia: Cobro entra a MP (`1210`), se traslada capital de Drugstore a PREPAY diario (`1160`), adicional va a `1140`.
  - CLARO vía transferencia: Cobro entra a MP (`1210`), se traslada capital de Drugstore a CLARO físico (`1150`), adicional va a `1140`.
- **Comportamiento Auditado en Código:**
  - Para PREPAY, `prepay_coordinator.py` ejecuta el traslado compensatorio (`1120 -> 1130`).
  - Para CLARO COMERCIOS vía transferencia ([`commercial_bridge_worker.py:636-648`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/tesoreria/commercial_bridge_worker.py#L636-L648)), solo se registra la entrada en `1210` (`SERVICIO_CLARO_DIGITAL`), **omitiéndose el traslado compensatorio de capital físico hacia `1150`** y el desglose del adicional hacia `1140`.
  - **Clasificación:** ⚠️ **ALTO (`AUD-005`)**.

---

## 5. AUDITORÍA DEL CARGO DIARIO PREPAY DE $350

- **Regla Confirmada:** Débito de $350 (35.000 centavos) sobre la cuenta digital `1220` (Prepay Digital), una sola vez por jornada, en la primera recarga Prepay del día, clasificado como costo operativo.
- **Comportamiento Auditado en Código:**
  - Ubicación: [`tesoreria/prepay_coordinator.py:50, 850-879`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/tesoreria/prepay_coordinator.py#L50) y [`experiments/cloudflare-worker-prototype/src/db/tesoreria_worker.js:2567-3005`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/experiments/cloudflare-worker-prototype/src/db/tesoreria_worker.js#L2567-L3005).
  - La lógica ya se encuentra implementada en la tabla `tesoreria_prepay_cargo_diario`, con estado `APPLIED`, idempotencia por `jornada_id` y salida reservada en `1220` con contrapartida `COSTO_OPERATIVO_PREPAY`.
  - **Evaluación:** El informe anterior lo clasificó erróneamente como "no automatizado / pendiente". La auditoría confirma que la lógica está operativa y respeta las condiciones del propietario (no toca efectivo, no afecta a David, se cobra 1 sola vez por jornada).
  - **Clasificación:** ℹ️ **INFORMATIVO / RATIFICADO (`AUD-003`)**.

---

## 6. AUDITORÍA DE LIQUIDACIONES Y REPOSICIONES

### 6.1. Liquidaciones con David
- **Código Auditado:** [`tesoreria/cuenta_corriente_david.py`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/tesoreria/cuenta_corriente_david.py).
- **Verificación:**
  - `registrar_acreditacion_david`: Incrementa el saldo digital en `1220` y registra la obligación en `tesoreria_cuenta_corriente_david`.
  - `registrar_pago_david`: Valida que el pago no supere la deuda acumulada ni el saldo disponible en `1130` (Prepay Efectivo/Acumulado). Debita exclusivamente `1130` (`PAGO_OBLIGACION_PREPAY_DAVID`) y reduce la deuda con David.
  - Prohibido pagar desde Drugstore (`1110`/`1120`).
- **Evaluación:** **100% IMPLEMENTADO Y CONFORME**.

### 6.2. Liquidaciones con Ezequiel
- **Regla Confirmada:** Liquidaciones de capital Claro desde `1150`, con posibilidad de completar con transferencia desde `1210` o fondos de Drugstore si falta efectivo.
- **Código Auditado:** No existe un libro mayor específico `tesoreria_cuenta_corriente_ezequiel` ni endpoints específicos de liquidación mixta para Ezequiel en `api/routers/tesoreria.py`. Solo existe un registro de apertura inicial con código `'CLARO'`.
- **Evaluación:** 🟡 **MEDIO (`AUD-006`): Funcionalidad no implementada como módulo formal de cuenta corriente**.

### 6.3. Reposición de Efectivo PREPAY Usado para Preventistas
- **Código Auditado:** [`tesoreria/anticipos_preventistas.py`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/tesoreria/anticipos_preventistas.py).
- **Verificación:**
  - `crear_anticipo_preventista`: Debita temporalmente de `1130` y genera expediente.
  - `reponer_anticipo_preventista`: Exige orden jerárquico estricto (1° `1120`, 2° `1110`, remanente pendiente). Emite traslados trazables vinculados a `tesoreria_reposiciones_preventistas`.
- **Evaluación:** **100% IMPLEMENTADO Y CONFORME**.

### 6.4. Cierres Diarios Independientes
- **Código Auditado:** [`tesoreria/closures.py`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/tesoreria/closures.py).
- **Verificación:**
  - `executeCashDrawerSweep`: Traslado de `1110` (Drugstore Diario) a `1120` (Drugstore Acumulado).
  - `executePrepayDrawerSweep`: Traslado de `1160` (Prepay Diario) a `1130` (Prepay Acumulado).
  - Idempotentes, no generan resultados comerciales y vacían la gaveta diaria para la jornada siguiente.
- **Evaluación:** **100% IMPLEMENTADO Y CONFORME**.

---

## 7. MATRIZ CONSOLIDADA DE HALLAZGOS Y CLASIFICACIÓN

| ID | Área | Clasificación | Descripción del Hallazgo | Causa Raíz |
|---|---|---|---|---|
| **`AUD-001`** | Persistencia Worker | 🚨 **CRÍTICO** | Alta de gastos en Worker JS no es atómica con Outbox y silencia errores con `.catch(() => {})`. | Separación de llamadas `client.execute()` sin `client.batch` en [`adapter.js:1531-1563`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/experiments/cloudflare-worker-prototype/src/db/adapter.js#L1531-L1563). |
| **`AUD-002`** | Transaccional Worker | ⚠️ **ALTO** | Alta de servicios en Worker JS no está empaquetada en `client.batch` con el Outbox. | Llamadas secuenciales independientes en [`adapter.js:1067-1141`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/experiments/cloudflare-worker-prototype/src/db/adapter.js#L1067-L1141). |
| **`AUD-004`** | Económica | ⚠️ **ALTO** | En recargas de servicios por mostrador no se desglosa el adicional cobrado hacia el fondo `1140` (Adicionales). | El motor central agrupa `pago_efvo` en una sola pierna a `1110`, `1130` o `1150` en [`commercial_bridge_worker.py:381-394`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/tesoreria/commercial_bridge_worker.py#L381-L394). |
| **`AUD-005`** | Económica | ⚠️ **ALTO** | En recargas CLARO COMERCIOS por transferencia, no se emite el traslado compensatorio de capital hacia `1150`. | Omisión de rutina de compensación física en el branch de Claro en [`commercial_bridge_worker.py:636-648`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/tesoreria/commercial_bridge_worker.py#L636-L648). |
| **`AUD-006`** | Módulo Faltante | 🟡 **MEDIO** | Cuenta corriente y liquidación de Ezequiel (Claro) no posee libro mayor ni endpoints dedicados como David. | Falta de módulo `cuenta_corriente_ezequiel.py` en backend. |
| **`AUD-007`** | Observabilidad | 🟡 **MEDIO** | Estado `PUBLISHED` en Outbox se usa para eventos retenidos (`is_mixed_reduction`). | Ausencia de valor enum `HELD` o `PENDING_DEFINITION` en columna `status` en SQLite. |
| **`AUD-008`** | Integridad DDL | 🟢 **BAJO** | `readiness.py` audita tablas, columnas e índices pero omite introspección de Foreign Keys (`PRAGMA foreign_key_list`). | Falta de rutina de introspección relacional en [`db/readiness.py`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/db/readiness.py). |
| **`AUD-003`** | Económica | ℹ️ **INFORMATIVO** | Cargo diario de $350 PREPAY validado en código; ratificado formalmente por el propietario. | Implementación correcta en `prepay_coordinator.py` que requería ratificación de negocio. |

---

## 8. CONCLUSIÓN Y ESTADO DE DETENCIÓN

La presente auditoría de solo lectura ha identificado con precisión quirúrgica las fortalezas estructurales del motor central y los puntos exactos que impiden considerarlo 100% confiable para producción.

En cumplimiento estricto del protocolo, **NO SE HA IMPLEMENTADO NINGUNA CORRECCIÓN EN ESTA ETAPA**. Se entregan los documentos complementarios [`MATRIZ_TRAZABILIDAD_ECONOMICA_V4.md`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/MATRIZ_TRAZABILIDAD_ECONOMICA_V4.md) y [`PLAN_CORRECCIONES_V4.md`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/PLAN_CORRECCIONES_V4.md) y se detiene la ejecución a la espera de la revisión y autorización del propietario.
