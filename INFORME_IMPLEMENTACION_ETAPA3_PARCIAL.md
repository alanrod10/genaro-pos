# GENAROPOS V4 — INFORME DE IMPLEMENTACIÓN CONTROLADA ETAPA III (PARCIAL)
## Resumen Técnico y Verificación de Correcciones Autorizadas en Staging

**Proyecto:** GenaroPOS V4  
**Fecha:** 06/11/2026  
**Rama de trabajo:** `feature/staging-central-accounting`  
**Commit base de partida:** `0cafea8ec39e3a6005895dc372cad1e66e381606` (y commit de auditoría `6836ddf`)  
**Entorno de ejecución:** STAGING / Entorno Local Aislado  
**Certificación:** CERO conexión y CERO afectación al entorno de Producción.  

---

## 1. DECLARACIÓN DE IDENTIDAD Y SEGURIDAD

Se certifica que durante toda la Etapa III Parcial:
- No se estableció conexión alguna con `genaro-pos-live-ops` ni `genaro-pos-prod`.
- No se modificó ningún secreto, variable ni recurso de Cloudflare Pages ni Cloudflare Workers productivos.
- No se ejecutaron migraciones sobre bases productivas.
- No se implementaron los puntos excluidos **B.1**, **B.2** ni **C.1** en código (fueron desarrollados exclusivamente como especificaciones documentales).
- Se ejecutaron todas las pruebas sobre bases locales SQLite en memoria y archivos temporales aislados.

---

## 2. DETALLE DE CORRECCIONES IMPLEMENTADAS

### 2.1. A.1 — Atomicidad del Alta de Gastos Worker JS
- **Archivo modificado:** [`experiments/cloudflare-worker-prototype/src/db/adapter.js`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/experiments/cloudflare-worker-prototype/src/db/adapter.js) (`registrarGastoIdempotente`).
- **Problema previo:** Se insertaba el gasto en `gastos_retiros` y luego, de forma aislada y con `.catch(() => {})`, se intentaba insertar en auditoría y Outbox. Si fallaba la segunda sentencia, el gasto quedaba huérfano comercialmente sin evento contable.
- **Solución implementada:**
  - Se unificó la inserción en `gastos_retiros`, `gastos_auditoria` y `commercial_events_outbox` en una única transacción atómica `await client.batch(batchStatements, "write")`.
  - Se eliminaron todos los `.catch(() => {})` silenciosos.
  - Se garantiza que si no se provee `idempotencyKey`, se genera una clave efectiva determinística `effectiveKey` que permite la resolución de `id` vía subconsultas `(SELECT id FROM gastos_retiros WHERE idempotency_key = ?)` y vinculación exacta en el Outbox (`json_set`).
  - Ante cualquier fallo en auditoría u Outbox, SQLite/libSQL realiza rollback completo y NADA persiste en la base.

### 2.2. A.2 — Atomicidad del Alta de Servicios Worker JS
- **Archivo modificado:** [`experiments/cloudflare-worker-prototype/src/db/adapter.js`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/experiments/cloudflare-worker-prototype/src/db/adapter.js) (`registrarCargaIdempotente`).
- **Problema previo:** En el flujo comercial base (y no-Prepay), `cargas_servicios` se insertaba primero, y luego la auditoría y el Outbox se ejecutaban en llamadas separadas con `.catch(() => {})`. En Prepay, la auditoría también tenía `.catch(() => {})`.
- **Solución implementada:**
  - Para el camino comercial base / no-Prepay: se unificó la inserción en `cargas_servicios`, `cargas_servicios_auditoria` y `commercial_events_outbox` en un lote atómico indivisible `client.batch(batchStmts, "write")`.
  - Se eliminaron todos los `.catch(() => {})` silenciosos.
  - Para el circuito PREPAY: la coordinación y pre-reserva se mantienen intactas; el commit comercial y su auditoría se ejecutan en lote atómico, y si cualquier paso falla, el bloque catch cancela de inmediato la pre-reserva (`cancelarPreReservaPrepayWorker`) sin dejar registros comerciales huérfanos.

### 2.3. D.1 — Semántica y Diagnóstico de Outbox (Eventos HELD vs. PUBLISHED)
- **Archivos modificados:**
  - [`tesoreria/commercial_bridge_worker.py`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/tesoreria/commercial_bridge_worker.py)
  - [`api/routers/commercial_bridge.py`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/api/routers/commercial_bridge.py)
  - [`experiments/cloudflare-worker-prototype/src/db/tesoreria_worker.js`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/experiments/cloudflare-worker-prototype/src/db/tesoreria_worker.js)
- **Problema previo:** Eventos retenidos por falta de plataforma (ej. `UNSPECIFIED_PLATFORM...` o `PENDING_DEFINITION...`) eran computados dentro de `published` y se mostraban al cajero como "Todas las operaciones sincronizadas correctamente", ocultando la falta de imputación contable.
- **Solución implementada:**
  - Se incorporó la métrica explícita `held_pending_definition` tanto en el backend Python como en el Worker JS.
  - Se depuró la métrica `published`: solo cuenta eventos con `status = 'PUBLISHED'` que NO contengan marcadores de retención en `tesoreria_movement_id`.
  - En `get_simplified_sync_status` y `obtenerSyncStatusBridgeWorker`: si existen eventos retenidos, `requiere_atencion` pasa a `true` y el mensaje advierte claramente sobre operaciones pendientes de definición de plataforma.
  - En las consultas de listado detallado (`list_commercial_events_detailed` y `listarCommercialEventsWorker`), se habilitó el filtro explícito por estado `"HELD"` / `"PENDING_DEFINITION"`.

### 2.4. E.1 — Inspección de Foreign Keys en Readiness
- **Archivos modificados:**
  - [`db/readiness.py`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/db/readiness.py)
  - [`tests/test_system_readiness_optimization.py`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/tests/test_system_readiness_optimization.py)
- **Problema previo:** `comprobar_preparacion_esquema` verificaba tablas, columnas e índices pero no inspeccionaba si las restricciones de clave foránea (`FOREIGN KEY`) estaban físicamente activas en las tablas de auditoría y detalle.
- **Solución implementada:**
  - Se definió el catálogo `FOREIGN_KEYS_REQUERIDAS` para las tablas críticas: `ticket_items`, `cargas_servicios_auditoria`, `gastos_auditoria`, `ventas_auditoria`, `productos_auditoria`, `pedido_items` y `tesoreria_prepay_coordinacion`.
  - Se integró la introspección de claves foráneas mediante `PRAGMA foreign_key_list` consolidada en una única consulta `UNION ALL` junto con `PRAGMA table_info`.
  - **Presupuesto de Consultas Preservado:** En una base de datos preparada se ejecutan exactamente 4 consultas a la base, cumpliendo estrictamente con el límite del `Test 10` (máximo 4 consultas).
  - Se agregó `test_11_deteccion_de_foreign_keys_faltantes` verificando que ante la ausencia de una FK requerida, el estado del sistema pasa inmediatamente a `ESQUEMA_INCONSISTENTE` con `listo = False`.

---

## 3. DOCUMENTOS COMPLEMENTARIOS GENERADOS (B.1, B.2, C.1)

En estricto cumplimiento del alcance autorizado (sin tocar código ni migraciones productivas para estos tres puntos), se generaron las especificaciones técnicas completas:

1. **[`ESPECIFICACION_RECARGAS_Y_COMPENSACIONES_B1_B2.md`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/ESPECIFICACION_RECARGAS_Y_COMPENSACIONES_B1_B2.md):**
   - Matriz integral de combinaciones Plataforma × Destino × Medio de Pago.
   - Tratamiento contable y financiero del adicional: segregación del capital de terceros respecto al honorario propio del negocio.
   - Circuito de traslados compensatorios para Claro Comercios ante cobranzas por transferencia digital.
   - Propuesta de migración sin ruptura histórica.

2. **[`PROPUESTA_CUENTA_CORRIENTE_EZEQUIEL_C1.md`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/PROPUESTA_CUENTA_CORRIENTE_EZEQUIEL_C1.md):**
   - Justificación comercial de la cuenta corriente de Ezequiel.
   - Especificación DDL completa para la futura migración `015_cuenta_corriente_ezequiel.sql`.
   - Mapeo de movimientos y balance append-only con auditoría inmutable.
   - Reglas de negocio e integración con el Commercial Bridge.

---

## 4. EVIDENCIA VERIFICABLE DE PRUEBAS

| Suite de Pruebas | Entorno / Comando | Total Tests | Pasados | Fallidos | Estado |
| :--- | :--- | :---: | :---: | :---: | :---: |
| **Backend Pytest** | `./.venv/bin/pytest` | 771 | 771 | 0 | **100% OK** |
| **Readiness Optimization** | `./.venv/bin/pytest tests/test_system_readiness_optimization.py` | 16 | 16 | 0 | **100% OK** |
| **Worker Prototype (Node test)** | `cd experiments/cloudflare-worker-prototype && npm test` | 64 | 64 | 0 | **100% OK** |
| **Frontend Vitest** | `cd frontend && npm test` | 383 | 383 | 0 | **100% OK** |
| **Frontend Production Build** | `cd frontend && npm run build` | Vite build | OK | 0 | **100% OK** |

---

## 5. CONCLUSIÓN Y ESTADO ACTUAL

La Etapa III Parcial ha concluido de forma exitosa, controlada y documentada en Staging.  
Todos los cambios se encuentran verificados con tests automatizados reproducibles.  
El repositorio se encuentra listo para la revisión y auditoría del titular, manteniéndose la **prohibición absoluta de tocar Producción**.
