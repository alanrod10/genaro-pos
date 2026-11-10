# GENAROPOS V4 — INFORME DE AUDITORÍA INICIAL (FASE IV-A)
## Auditoría Técnica y Financiera en Modo Solo Lectura de la Base de Staging

**Proyecto:** GenaroPOS V4  
**Fecha de emisión:** 06/11/2026  
**Fase:** FASE IV-A (Auditoría Inicial Solo Lectura — Cero Modificación de Código)  
**Rama inspeccionada:** `feature/staging-central-accounting`  
**Commit HEAD auditado:** `614048924b171f11a84c8be69888b5ec186884aa`  
**Commit base de comparación:** `0cafea8ec39e3a6005895dc372cad1e66e381606`  
**Estado del Working Tree:** `clean` (0 archivos modificados, 0 archivos staged)  
**Entorno de ejecución:** STAGING / Entorno Local Aislado (libSQL / SQLite local)  
**Certificación de Seguridad:** CERO conexión y CERO afectación a recursos o bases de Producción (`genaro-pos-live-ops`, `genaro-pos-prod`).  

---

## 1. HECHOS COMPROBADOS (EVIDENCIA DIRECTA DE CÓDIGO Y GIT)

1. **Identidad del Repositorio:**
   - La rama de trabajo activa es `feature/staging-central-accounting`.
   - El historial de commits incluye exactamente la secuencia:
     - `6140489` — `feat(staging): implementacion controlada parcial etapa 3 (A.1, A.2, D.1, E.1) y especificaciones B.1, B.2, C.1`
     - `6836ddf` — `docs(audit): auditoria tecnica y economica integral v4, matriz de trazabilidad y plan de correcciones`
     - `0cafea8` — `feat(staging): unificar arquitectura de contabilizacion centralizada (alcance ALL)`
     - `17ec293` — `docs(produccion): informe final de publicacion v4 y evidencias de despliegue en genaro-pos-live` (HEAD de `release/produccion-2026-11-03` y `main`).
   - El diff exacto entre `0cafea8` y `6140489` involucra 13 archivos (1227 inserciones, 167 eliminaciones).

2. **A.1 Implementado y Comprobado:**
   - En [`experiments/cloudflare-worker-prototype/src/db/adapter.js`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/experiments/cloudflare-worker-prototype/src/db/adapter.js#L1570-L1630), la función `registrarGastoIdempotente` utiliza `client.batch(batchStatements, "write")`.
   - Se eliminaron todos los `.catch(() => {})`.
   - Se genera una clave `effectiveKey` que resuelve atómicamente la clave foránea del gasto en `gastos_auditoria` y en `commercial_events_outbox` mediante `json_set`.

3. **A.2 Implementado y Comprobado:**
   - En [`experiments/cloudflare-worker-prototype/src/db/adapter.js`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/experiments/cloudflare-worker-prototype/src/db/adapter.js#L1060-L1190), la función `registrarCargaIdempotente` ejecuta en un solo lote atómico `cargas_servicios`, `cargas_servicios_auditoria` y `commercial_events_outbox` para el camino comercial base.
   - En el camino PREPAY, la pre-reserva se ejecuta primero; ante fallos en el commit comercial, el bloque `catch` ejecuta `cancelarPreReservaPrepayWorker`.
   - No quedan `.catch(() => {})` silenciosos en auditoría ni Outbox.

4. **D.1 Implementado y Comprobado:**
   - En [`tesoreria/commercial_bridge_worker.py`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/tesoreria/commercial_bridge_worker.py#L1416-L1530) y [`experiments/cloudflare-worker-prototype/src/db/tesoreria_worker.js`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/experiments/cloudflare-worker-prototype/src/db/tesoreria_worker.js#L2383-L2455), se desglosó la métrica `held_pending_definition` para eventos con retención operativa (`UNSPECIFIED_PLATFORM...` o `PENDING_DEFINITION...`).
   - Los eventos retenidos ya no se presentan como `published` ni como "sincronizadas". El monitor activa `requiere_atencion = true`.

5. **E.1 Implementado y Comprobado:**
   - En [`db/readiness.py`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/db/readiness.py#L110-L135), se catalogó `FOREIGN_KEYS_REQUERIDAS` y se consolidó la inspección con `PRAGMA foreign_key_list` vía `UNION ALL` con `PRAGMA table_info`.
   - En base preparada se ejecutan exactamente 4 consultas a la base, respetando la aserción de `Test 10`.

6. **Reproducibilidad de Pruebas Automatizadas:**
   - **Backend Pytest:** 771 pasados, 0 fallidos en 15.84s.
   - **Readiness Optimization:** 16 pasados, 0 fallidos en 1.43s.
   - **Worker Prototype (Node test):** 64 pasados, 0 fallidos en 1.35s.
   - **Frontend Vitest:** 383 pasados, 0 fallidos en 10.41s.
   - **Frontend Build (Vite):** Exitoso, 0 errores en 2.63s.

---

## 2. FUNCIONALIDADES VERIFICADAS (CIRCUITOS PROBADOS)

1. **Idempotencia de Operaciones:**
   - Comprobada mediante pruebas de doble invocación con la misma clave de idempotencia en ventas, servicios y gastos. No se duplican registros ni auditorías.
2. **Rollback de Lotes Atómicos:**
   - Comprobada la reversión íntegra de `gastos_retiros` y `cargas_servicios` cuando alguna de las sentencias del lote (`auditoria` u `outbox`) falla.
3. **Aislamiento Estricto de Laboratorio de Tesorería:**
   - [`tesoreria/isolation.py`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/tesoreria/isolation.py) bloquea con `ErrorViolacionAislamiento` cualquier conexión a esquemas `turso://`, `libsql://`, `https://` o nombres de bases comerciales.

---

## 3. FUNCIONALIDADES DECLARADAS PERO NO VERIFICADAS (BRECHAS DETECTADAS)

1. **Circuitos Económicos B.1 y B.2 en el Motor Central Python:**
   - La especificación documental [`ESPECIFICACION_RECARGAS_Y_COMPENSACIONES_B1_B2.md`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/ESPECIFICACION_RECARGAS_Y_COMPENSACIONES_B1_B2.md) fue redactada, pero **NO está implementada en código** (estaba explícitamente prohibido en la etapa anterior).
   - En el código real de `tesoreria/commercial_bridge_worker.py`, los adicionales de Claro y Mercado Pago aún no se segregan a una cuenta de comisión diferenciada.
2. **Cuenta Corriente de Ezequiel (C.1):**
   - El documento [`PROPUESTA_CUENTA_CORRIENTE_EZEQUIEL_C1.md`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/PROPUESTA_CUENTA_CORRIENTE_EZEQUIEL_C1.md) existe como propuesta teórica. No existe la tabla física `tesoreria_cuenta_corriente_ezequiel`, ni la migración 015, ni endpoints en la API.
3. **Flujo Operativo Completo en Celulares Reales:**
   - Aunque `responsive_viewports.test.tsx` corre 33 pruebas emuladas con jsdom/happy-dom en anchos de 320px a 1920px, no se ha realizado prueba en dispositivos físicos reales con teclado virtual desplegado (apertura de teclado Android/iOS en inputs de cobro).

---

## 4. INCONSISTENCIAS CRÍTICAS DETECTADAS

### Inconsistencia 1: Cargo Diario PREPAY ($350) Cableado en Código
- **Hallazgo:** En `tesoreria/prepay_coordinator.py`, `tesoreria_worker.js` y `db/migrations/014_prepay_coordinacion_y_cargo_diario.sql`, el importe de $350,00 ARS (35.000 centavos) figura como valor por defecto y literal cableado en código.
- **Riesgo:** Si la plataforma PREPAY actualiza su costo operativo diario o se requiere modificarlo por inflación, el sistema requeriría modificar migraciones y código fuente en lugar de leer una tabla de configuración o parámetro de tesorería.

### Inconsistencia 2: Modelo DDL Propuesto en C.1 vs. Invariante Inmutable de David
- **Hallazgo:** En `PROPUESTA_CUENTA_CORRIENTE_EZEQUIEL_C1.md` redactado previamente, se propuso una tabla de auditoría con `ON DELETE CASCADE`.
- **Contradicción:** En el sistema de Tesorería real de David ([`tesoreria/migrations/004_cuenta_corriente_david.sql`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/tesoreria/migrations/004_cuenta_corriente_david.sql#L29-L30)), se utiliza estrictamente `ON DELETE RESTRICT` y el saldo no es un campo mutable sino derivado de movimientos inmutables vinculados a `tesoreria_movimientos`.
- **Corrección requerida:** Descartar el `ON DELETE CASCADE` de la propuesta C.1 y homologar el diseño al patrón exacto de `tesoreria_cuenta_corriente_david`.

### Inconsistencia 3: Ausencia de Cuenta Digital para Claro Comercios
- **Hallazgo:** En el catálogo canónico de 10 fondos de Tesorería (`1110`, `1120`, `1130`, `1140`, `1150`, `1160`, `1210`, `1220`, `1230`, `1310`), existe `1150 Claro Efectivo` (custodia física), pero **NO existe un fondo digital para Claro**.
- **Impacto:** Cuando el cliente paga una recarga Claro por Transferencia, el dinero entra a `1210 Mercado Pago Propio`. El negocio queda debiendo saldo a Claro, pero no hay un fondo o pasivo digital explícito en el catálogo para registrar esta deuda digital con el proveedor Claro.

---

## 5. RIESGOS RESIDUALES IDENTIFICADOS

| Riesgo | Severidad | Subsistema Afectado | Causa Raíz | Mitigación Propuesta |
| :--- | :---: | :--- | :--- | :--- |
| **Descalce de Custodia Claro** | ALTA | Tesorería / Fondos | Cobro por transferencia ingresa a `1210` pero la deuda es con Claro. Si no hay asiento de pasivo, `1210` sobreestima disponibilidad propia. | Implementar asiento formal de provisión y traslado compensatorio al proveedor Claro. |
| **Adicionales mezclados con capital** | MEDIA | Servicios / Cierres | En cobros de Claro y MP, el adicional de recarga no tiene contrapartida contable explícita en `1110`. | Aplicar la matriz de segregación de capital y ganancia en `commercial_bridge_worker.py`. |
| **Transacciones de Ezequiel en la informalidad** | MEDIA | Tesorería / Cuentas Corrientes | Al no existir la entidad Ezequiel, las entregas de mercadería y adelantos se anotan como gastos de caja. | Crear la migración `015` formalizando la cuenta corriente con el mismo rigor que David. |

---

## 6. PENDIENTES FUNCIONALES Y ECONÓMICOS

1. **Definición de Traslado Compensatorio Claro:** ¿Cómo se paga físicamente a Claro la recarga cobrada por transferencia? ¿Se deposita por banco a Claro, o se retira efectivo de la caja `1110` para entregarlo al recaudador físico de Claro?
2. **Definición de Cuenta Corriente Ezequiel:** ¿Ezequiel retira mercadería al costo o a precio de venta al público? ¿Quién autoriza los retiros y con qué comprobante?
3. **Costo Diario Prepay:** ¿El costo de $350 es fijo por jornada donde hubo al menos 1 operación Prepay, o se cobra independientemente de si hubo operaciones?

---

## 7. RECOMENDACIÓN PRIORITARIA DEL EQUIPO

**NO modificar código en este momento.**  
Presentar formalmente al propietario del sistema:
1. La [`MATRIZ_DECISIONES_ECONOMICAS_ETAPA_IV.md`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/MATRIZ_DECISIONES_ECONOMICAS_ETAPA_IV.md) para confirmar las 3 definiciones económicas críticas.
2. El [`DISENO_TECNICO_CONSOLIDACION_ETAPA_IV.md`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/DISENO_TECNICO_CONSOLIDACION_ETAPA_IV.md) para alinear la arquitectura técnica con las invariantes contables.
3. Aguardar la autorización expresa antes de abrir la Fase IV-D de implementación.
