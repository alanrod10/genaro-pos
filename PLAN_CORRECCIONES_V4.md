# PLAN DE ESPECIFICACIÓN Y CORRECCIONES — GENAROPOS V4
## Propuesta Técnica para Aprobación del Propietario (Etapa II)

**Proyecto:** GenaroPOS V4  
**Fecha:** 06/11/2026  
**Rama:** `feature/staging-central-accounting`  
**Commit:** [`0cafea8`](file:///home/alanrod10/workspace/genaro-pos-turso-lab)  
**Entorno de Aplicación Propuesto:** Exclusivamente STAGING / Laboratorio Aislado  
**Estado de Ejecución:** DETENIDO A LA ESPERA DE APROBACIÓN EXPRESA.

---

## 1. AGRUPACIÓN Y CLASIFICACIÓN DE LAS CORRECCIONES

El plan se estructura en 6 bloques rigurosos conforme a las directivas del proyecto:
- **Grupo A:** Correcciones técnicas sin impacto económico (Atomicidad en Worker JS).
- **Grupo B:** Correcciones que afectan movimientos y saldos (Desglose de Adicionales y Traslados compensatorios).
- **Grupo C:** Correcciones que afectan obligaciones (Cuenta Corriente Ezequiel).
- **Grupo D:** Mejoras de observabilidad y estados Outbox.
- **Grupo E:** Mejoras de integridad DDL y Readiness.
- **Grupo F:** Mejoras de interfaz y fidelidad de datos.

---

## GRUPO A: CORRECCIONES TÉCNICAS SIN IMPACTO ECONÓMICO

### Corrección A.1 — Atomicidad Estricta en Alta de Gastos del Worker JS (`AUD-001`)

1. **Hallazgo:** En el Cloudflare Worker, `registrarGastoIdempotente` inserta el gasto en `gastos_retiros` y luego, en una llamada HTTP/SQL independiente con `.catch(() => {})`, intenta insertar el evento en `commercial_events_outbox`. Si la segunda llamada falla, el gasto existe comercialmente pero no tiene evento en el Outbox contable.
2. **Causa Raíz:** Separación de sentencias en múltiples `client.execute()` y presencia de `.catch()` que oculta errores.
3. **Archivo y Función:** [`experiments/cloudflare-worker-prototype/src/db/adapter.js`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/experiments/cloudflare-worker-prototype/src/db/adapter.js) en `registrarGastoIdempotente` (Líneas 1531-1563).
4. **Comportamiento Actual:** Dos llamadas independientes sin transacción unificada.
5. **Comportamiento Esperado:** La inserción en `gastos_retiros`, la inserción en `gastos_auditoria` y la inserción en `commercial_events_outbox` deben enviarse juntas en una única llamada transaccional atómica.
6. **Riesgo Económico:** Discordancia entre dinero extraído de la caja y saldo financiero registrado en el libro mayor.
7. **Cambio Propuesto:** Reemplazar las llamadas sueltas por un `await client.batch([...], "write")` que incluya las 3 sentencias (gasto, auditoría y outbox). Si falla, libSQL hace rollback de todo.
8. **Impacto en Persistencia:** Cero cambios de esquema. Estructura idéntica.
9. **Compatibilidad Histórica:** Totalmente compatible hacia atrás con registros existentes.
10. **Pruebas Necesarias:** Simulación de fallo en `client.batch` verificando que no se cree el registro en `gastos_retiros` si falla el lote.
11. **Criterio de Aceptación:** `ATOMICIDAD DEMOSTRADA` con código de salida 0 en todas las pruebas unitarias y de integración del Worker.

---

### Corrección A.2 — Lote Atómico en Alta de Servicios del Worker JS (`AUD-002`)

1. **Hallazgo:** En `registrarCargaIdempotente`, la inserción en `cargas_servicios` y la posterior emisión de outbox no están encapsuladas en un `client.batch`.
2. **Causa Raíz:** Arquitectura legacy de pasos secuenciales asíncronos en el adaptador.
3. **Archivo y Función:** [`experiments/cloudflare-worker-prototype/src/db/adapter.js`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/experiments/cloudflare-worker-prototype/src/db/adapter.js) en `registrarCargaIdempotente` (Líneas 1067-1141).
4. **Comportamiento Actual:** Múltiples `client.execute()` secuenciales.
5. **Comportamiento Esperado:** Inserción comercial, auditoría y outbox agrupados en un solo `client.batch([...], "write")`.
6. **Riesgo Económico:** Clientes cargan saldo telefónico o SUBE sin que se genere el evento de cobro y débito en Tesorería.
7. **Cambio Propuesto:** Unificar la sentencia de `cargas_servicios`, la de auditoría y la de `commercial_events_outbox` en un solo lote atómico de escritura.
8. **Impacto en Persistencia:** Cero cambios DDL.
9. **Compatibilidad Histórica:** Total.
10. **Pruebas Necesarias:** Suite de integración de Worker (`real_client_worker_integration.test.tsx`).
11. **Criterio de Aceptación:** Inserción 100% indivisible.

---

## GRUPO B: CORRECCIONES QUE AFECTAN MOVIMIENTOS Y SALDOS

### Corrección B.1 — Segregación Estricta de Adicionales hacia Fondo 1140 (`AUD-004`)

1. **Hallazgo:** En recargas de servicios por mostrador (SUBE, Claro, Personal, Movistar), el adicional cobrado al cliente se acumula en el mismo fondo de capital (`1110`, `1130` o `1150`) en lugar de acreditarse en el fondo físico `1140` (Adicionales).
2. **Causa Raíz:** [`tesoreria/commercial_bridge_worker.py:381-394, 521-534, 620-633`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/tesoreria/commercial_bridge_worker.py#L381-L394) acredita el monto total `pago_efvo` (capital + adicional) directamente en el fondo de plataforma.
3. **Archivo y Función:** [`tesoreria/commercial_bridge_worker.py`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/tesoreria/commercial_bridge_worker.py) en `_handle_service_confirmed`.
4. **Comportamiento Actual:** `1110`, `1130` y `1150` absorben los adicionales comerciales.
5. **Comportamiento Esperado:** Conforme a la **Fuente de Verdad Económica del Propietario (Sección 3.1 y 4)**:
   - Capital de la recarga se acredita en el fondo respectivo (`1110`, `1160`/`1130` o `1150`).
   - Adicional (`monto_adicional > 0`) se acredita exclusivamente en `1140` (Adicionales Efectivo) con contrapartida `ADICIONAL_SERVICIO_EFECTIVO`.
6. **Riesgo Económico:** Distorsión de ganancias del negocio y sobreestimación del capital liquidable a plataformas de terceros.
7. **Cambio Propuesto:** En `_handle_service_confirmed`, bifurcar el cobro en efectivo:
   - Si `pago_efvo > 0`:
     - Pierna Capital (`monto_carga`): Entra al fondo de capital asignado.
     - Pierna Adicional (`monto_adicional`): Entra a `1140`.
8. **Impacto en Persistencia:** Ninguno en el esquema; afecta únicamente a los destinos de los nuevos asientos contables.
9. **Compatibilidad Histórica:** No se modifican asientos históricos anteriores a la fecha de corte.
10. **Pruebas Necesarias:** Pruebas E2E de recargas verificando saldo exacto de $100/$500 en `1140` y capital neto en el fondo correspondiente.
11. **Criterio de Aceptación:** Validación de que ningún adicional incremente fondos de terceros ni capital de Drugstore.

---

### Corrección B.2 — Traslado Compensatorio de Capital para CLARO COMERCIOS vía Transferencia (`AUD-005`)

1. **Hallazgo:** Cuando una recarga de CLARO COMERCIOS se cobra por transferencia bancaria / MP, solo se registra la entrada digital en `1210` (`SERVICIO_CLARO_DIGITAL`), omitiéndose el traslado de capital físico desde Drugstore hacia CLARO Físico (`1150`).
2. **Causa Raíz:** Falta de bloque de compensación física en el branch `CLARO COMERCIOS` de [`tesoreria/commercial_bridge_worker.py:635-648`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/tesoreria/commercial_bridge_worker.py#L635-L648).
3. **Archivo y Función:** [`tesoreria/commercial_bridge_worker.py`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/tesoreria/commercial_bridge_worker.py) en `_handle_service_confirmed`.
4. **Comportamiento Actual:** El capital queda en Mercado Pago (`1210`) y nunca llega a la caja física de Claro (`1150`).
5. **Comportamiento Esperado:** Conforme a la **Sección 4 de la Fuente de Verdad Económica**: Cobro entra a `1210`, se emite traslado compensatorio de capital de Drugstore Acumulado (`1120`) a Claro Físico (`1150`), y el adicional ingresa a `1140`.
6. **Riesgo Económico:** Faltante de efectivo en `1150` al momento de liquidar la cuenta con Ezequiel en mostrador.
7. **Cambio Propuesto:** Replicar la rutina de compensación física validada en `prepay_coordinator.py`: ejecutar `transferFunds` (1120 -> 1150) por el monto del capital y confirmarla en el mismo ciclo.
8. **Impacto en Persistencia:** Trazabilidad en `tesoreria_traslados`.
9. **Compatibilidad Histórica:** Totalmente retrocompatible.
10. **Pruebas Necesarias:** Test sintético de recarga Claro con transferencia: verificar saldo +capital en `1150`, -capital en `1120`, +total en `1210` y +adicional en `1140`.
11. **Criterio de Aceptación:** Balance cero en resultado comercial por traslado compensatorio.

---

## GRUPO C: CORRECCIONES QUE AFECTAN OBLIGACIONES

### Corrección C.1 — Especificación del Módulo de Cuenta Corriente Ezequiel (Claro) (`AUD-006`)

1. **Hallazgo:** A diferencia de David (que cuenta con `tesoreria_cuenta_corriente_david`), las liquidaciones y fondeos de Ezequiel no tienen un libro mayor dedicado ni endpoints formales en la API.
2. **Causa Raíz:** Módulo no desarrollado en fases previas.
3. **Archivo y Función:** Propuesta de nuevo módulo `tesoreria/cuenta_corriente_ezequiel.py` y endpoints en `api/routers/tesoreria.py`.
4. **Comportamiento Actual:** Se maneja fuera del sistema o mediante transferencias manuales no estructuradas.
5. **Comportamiento Esperado:** Conforme a la **Sección 7 de la Fuente de Verdad Económica**:
   - Registrar fondeos de saldo digital provistos por Ezequiel en `1230` (Claro Comercios Digital).
   - Registrar liquidaciones desde Claro Físico (`1150`), con soporte de pago complementario desde `1210` o `1120` si falta efectivo en `1150`.
   - Permitir pagos parciales conservando el saldo pendiente de deuda con Ezequiel.
6. **Riesgo Económico:** Pérdida de control de la deuda con el proveedor de Claro y mezcla de fondos de terceros con fondos propios.
7. **Cambio Propuesto:** Crear la migración `015_cuenta_corriente_ezequiel.sql` y el módulo homólogo al de David para auditar y controlar esta obligación.
8. **Impacto en Persistencia:** Creación de tabla `tesoreria_cuenta_corriente_ezequiel`.
9. **Compatibilidad Histórica:** No altera tablas previas.
10. **Pruebas Necesarias:** Suite completa de fondeos, pagos mixtos y saldos pendientes para Ezequiel.
11. **Criterio de Aceptación:** Auditoría completa de conciliación independiente con Ezequiel.

---

## GRUPO D: MEJORAS DE OBSERVABILIDAD Y ESTADOS OUTBOX

### Corrección D.1 — Semántica de Estados en Outbox (`AUD-007`)

1. **Hallazgo:** Los eventos retenidos (como la reducción de tickets mixtos) se graban con `status = 'PUBLISHED'` en la columna física de la base de datos, distinguiéndose únicamente por el prefijo en `tesoreria_movement_id`.
2. **Causa Raíz:** Restricción DDL `CHECK (status IN ('PENDING', 'PROCESSING', 'PUBLISHED', 'FAILED', 'DEAD_LETTER'))` en la migración 012.
3. **Archivo y Función:** [`tesoreria/commercial_bridge_worker.py:1122, 1497`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/tesoreria/commercial_bridge_worker.py#L1122).
4. **Comportamiento Actual:** Aparecen como "publicados" en consultas SQL básicas de recuento.
5. **Comportamiento Esperado:** Los reportes y consultas del frontend deben diferenciar nítidamente entre eventos contabilizados exitosamente (`ACCOUNTED`) y eventos retenidos en espera de definición (`HELD`).
6. **Riesgo Económico:** Confusión del auditor o cajero pensando que una venta retenida ya impactó en los libros contables.
7. **Cambio Propuesto:** Enriquecer las consultas de diagnóstico ([`get_commercial_bridge_status`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/tesoreria/commercial_bridge_worker.py#L1416)) para desglosar la métrica `held_pending_definition` separada de `published_clean`.
8. **Impacto en Persistencia:** Cero cambios DDL. Mejora a nivel de capa de consulta y presentación.
9. **Compatibilidad Histórica:** Total.
10. **Pruebas Necesarias:** Test de métricas Outbox en `test_commercial_bridge_monitor.py`.
11. **Criterio de Aceptación:** Paneles muestran exactamente cuántos eventos están contabilizados y cuántos retenidos.

---

## GRUPO E: MEJORAS DE INTEGRIDAD DDL Y READINESS

### Corrección E.1 — Introspección de Claves Foráneas en Readiness (`AUD-008`)

1. **Hallazgo:** [`db/readiness.py`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/db/readiness.py) verifica versiones, tablas, columnas e índices pero no audita las restricciones relacionales `FOREIGN KEY`.
2. **Causa Raíz:** Omisión de llamadas a `PRAGMA foreign_key_list()`.
3. **Archivo y Función:** [`db/readiness.py`](file:///home/alanrod10/workspace/genaro-pos-turso-lab/db/readiness.py) en `comprobar_preparacion_esquema`.
4. **Comportamiento Actual:** Reporta listo si están las tablas y columnas, sin validar la integridad de las foreign keys.
5. **Comportamiento Esperado:** Validar que las relaciones críticas (ej. `ticket_items -> tickets`, `cargas_servicios_auditoria -> cargas_servicios`) estén activas físicamente.
6. **Riesgo Económico:** Registros huérfanos en caso de corrupción o tablas creadas parcialmente.
7. **Cambio Propuesto:** Agregar introspección con `PRAGMA foreign_key_list` para las tablas principales.
8. **Impacto en Persistencia:** Ninguno.
9. **Compatibilidad Histórica:** Total.
10. **Pruebas Necesarias:** `test_system_readiness.py`.
11. **Criterio de Aceptación:** Diagnóstico completo de claves foráneas.

---

## RESUMEN DE IMPACTO Y PRÓXIMOS PASOS

```
+-----------+-----------------------------------+--------------------+------------------------+
| Corrección| Descripción Breve                 | Nivel de Riesgo    | Requiere Migración DDL |
+-----------+-----------------------------------+--------------------+------------------------+
| A.1       | Batch atómico gastos Worker JS     | Técnico / Bajo     | NO                     |
| A.2       | Batch atómico servicios Worker JS  | Técnico / Bajo     | NO                     |
| B.1       | Desglose adicional a 1140 mostrador| Financiero / Medio | NO                     |
| B.2       | Traslado capital Claro transf.    | Financiero / Medio | NO                     |
| C.1       | Módulo Cta Cte Ezequiel (Claro)   | Estructural / Alto | SÍ (Migración 015)     |
| D.1       | Métrica HELD en Outbox            | Observabilidad     | NO                     |
| E.1       | Auditoría FK en Readiness         | Diagnóstico        | NO                     |
+-----------+-----------------------------------+--------------------+------------------------+
```

> [!IMPORTANT]
> **REQUERIMIENTO DE AUTORIZACIÓN:**
> Las correcciones del Grupo A, B, D y E pueden implementarse de inmediato en el entorno aislado de Staging para certificar la consistencia técnica y matemática de las recargas.
> La corrección C.1 (Cuenta Corriente Ezequiel) introduce una nueva tabla y circuito contable que requiere confirmación explícita del propietario respecto a si se implementa en esta iteración o en una fase posterior.
> 
> **ESTADO ACTUAL: DETENIDO A LA ESPERA DE INSTRUCCIONES DEL PROPIETARIO.**
