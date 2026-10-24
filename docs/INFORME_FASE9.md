# INFORME DE AUDITORÍA TÉCNICA (FASE 9) — GENARO POS
## Compatibilidad de Transacciones, Integridad Contable y Preparación para Turso

> **Fecha y Hora de Auditoría:** 2026-10-22 17:05 UTC  
> **Workspace Auditado:** `/home/alanrod10/workspace/genaro-pos-turso-lab`  
> **Rama:** `feature/turso-migration-lab`  
> **Commit Base Verificado:** `b16b8b70a077a9b6a61858e22a64a9ba5dbacc63`  
> **Directorio Original (`/mnt/c/GenaroPOS`):** Verificado intacto (sin modificaciones).  
> **Modo de Trabajo:** Estrictamente solo lectura, análisis y verificación. Ningún archivo de código fue modificado ni creado.

---

## 1. Alcance y Medidas de Aislamiento

### Alcance
* Auditoría técnica del código desarrollado en la Fase 8 (`db/accounting.py`, `db/schema_v1.sql`, `db/normalization.py`, suites en `tests/` y documentación en `docs/`).
* Análisis de bajo nivel del código fuente del driver `turso-serverless 0.1.0` instalado en el `.venv` local para verificar la mecánica real de transacciones (`BEGIN`, `BEGIN IMMEDIATE`, `isolation_level`).
* Revisión de extremo a extremo de las validaciones contables, la máquina de estados del Outbox y las decisiones de negocio pendientes.

### Medidas de Seguridad Cumplidas
* **0 llamadas remotas:** No se interactuó con ningún cluster de Turso Cloud ni endpoints de libSQL.
* **0 escrituras externas:** Google Sheets y Google Apps Script no fueron consultados ni modificados.
* **0 alteraciones al código productivo:** `app.py` original se mantuvo inalterado en el commit `b16b8b70`.
* **Aislamiento de credenciales:** Ningún secreto, token o clave fue inspeccionado o volcado.

---

## 2. Archivos y Versiones Inspeccionados

| Archivo / Paquete | Estado en Git | Versión / Tamaño | Estado de Inspección |
|---|---|---|---|
| `docs/INFORME_FASE7.md` | Untracked | 9.771 B | Leído e inspeccionado íntegramente. |
| `docs/INFORME_FASE8.md` | Untracked | 6.786 B | Leído e inspeccionado íntegramente. |
| `docs/DECISIONES_PENDIENTES_MIGRACION.md` | Untracked | 3.518 B | Leído e inspeccionado íntegramente. |
| `docs/REVISION_DDL_V1.md` | Untracked | 7.025 B | Leído e inspeccionado íntegramente. |
| `db/schema_v1.sql` | Untracked | 3.518 B | Leído e inspeccionado íntegramente. |
| `db/normalization.py` | Untracked | 3.018 B | Leído e inspeccionado íntegramente. |
| `db/accounting.py` | Untracked | 8.895 B | Leído e inspeccionado íntegramente. |
| `tests/test_data_integrity.py` | Untracked | 6.536 B | Leído e inspeccionado íntegramente. |
| `tests/test_accounting_hardening.py` | Untracked | 6.388 B | Leído e inspeccionado íntegramente. |
| `tests/test_turso_dev_remote.py` | Untracked | 1.488 B | Leído e inspeccionado íntegramente. |
| `turso-serverless` | Paquete en `.venv` | **0.1.0** | Inspección de código fuente AST/Python (`turso_serverless/connection.py`). |
| `pytest` | Paquete en `.venv` | **9.1.1** | Ejecutado empíricamente. |
| `requirements.txt` | Tracked | 59 B | Dependencias originales del repositorio. |

---

## 3. Resultado Real de la Suite de Pruebas

### Ejecución Directa en el Entorno Virtual Aislado
* **Hallazgo de Entorno:** Al ejecutar `pytest` directamente sin prefijo, falló la recolección con `ModuleNotFoundError: No module named 'db'`, debido a que la raíz del proyecto no estaba en el `sys.path`. Al ejecutar con `PYTHONPATH=.`, la recolección fue inmediata y correcta.
* **Comando:**
  ```bash
  cd /home/alanrod10/workspace/genaro-pos-turso-lab && source .venv/bin/activate && PYTHONPATH=. pytest -v
  ```
* **Resultado:** **`18 passed in 0.06s`** (100% de éxito).
* **Desglose de Cobertura Real:**
  * **10 pruebas en `tests/test_data_integrity.py`:** Prueban lógica pura de Python (parseo de moneda, conversión horaria UTC/Jujuy, parametrización PREPAY) y restricciones básicas de SQLite en memoria (nombres duplicados, FK huérfanas, balance de pagos en ticket).
  * **8 pruebas en `tests/test_accounting_hardening.py`:** Prueban la orquestación atómica de `db/accounting.py` (rechazo contable por descuadre, recálculo por edición de ítem, anulación total y máquina de estados del Outbox).
* **Limitación Crítica:** **Todas las 18 pruebas se ejecutan contra el módulo estándar `sqlite3` de Python en memoria (`:memory:`).** Ninguna prueba evalúa el comportamiento real del paquete `turso-serverless` ni la latencia/concurrencia de la API remota de Turso.

---

## 4. Auditoría de Transacciones y Compatibilidad SDK / libSQL

### Análisis del SDK `turso-serverless 0.1.0`
Mediante la inspección del código fuente de `turso_serverless/connection.py`:
1. **Firma de Conexión:**
   ```python
   connect(url: str, *, auth_token: Optional[str] = None, isolation_level: Optional[str] = 'DEFERRED') -> Connection
   ```
2. **Manejo de Transacciones Implícitas:**
   * La clase `Connection` implementa `_maybe_implicit_begin(self, sql)`.
   * Si `isolation_level` no es `None` y no hay una transacción abierta (`self.in_transaction == False`), ante la primera sentencia DML (`INSERT`, `UPDATE`, `DELETE`, `REPLACE`), el driver **envía automáticamente al servidor:**
     ```python
     self._execute_stmt(f"BEGIN {level}", want_rows=False)
     ```
   * El método `commit()` envía `self._execute_stmt("COMMIT")` solo si `self.in_transaction == True`.
   * El método `rollback()` envía `self._execute_stmt("ROLLBACK")` solo si `self.in_transaction == True`.

### El Conflicto con `BEGIN IMMEDIATE` en `db/accounting.py`
* **[HECHO CONFIRMADO EN CÓDIGO]:** En `db/accounting.py`:
  * Línea 78 (`registrar_venta_atomica`): Ejecuta `conn.execute("BEGIN")`.
  * Línea 149 (`modificar_item_y_recalcular_ticket`): Ejecuta `conn.execute("BEGIN")`.
  * Línea 246 (`reclamar_lote_outbox`): Ejecuta `conn.execute("BEGIN IMMEDIATE")`.
* **Conflicto Potencial con `turso-serverless`:**
  * Si el código ejecuta `conn.execute("BEGIN")` o `conn.execute("BEGIN IMMEDIATE")` manualmente, está enviando una sentencia SQL directa.
  * Si la conexión tiene su `isolation_level` por defecto (`'DEFERRED'`), al llegar al primer `INSERT` o `UPDATE`, el driver evalúa `_maybe_implicit_begin()`. Si la propiedad `self.in_transaction` (que se basa en `not self._session.autocommit` reportado por el servidor) ya está activa, el driver no envía un segundo `BEGIN`. Sin embargo, ejecutar `BEGIN` manual mientras el driver gestiona transacciones DB-API 2.0 viola la convención estándar de Python (PEP 249).
  * **Específico de `BEGIN IMMEDIATE`:** En Turso Cloud (que es una arquitectura distribuida con réplicas y MVCC), el bloqueo de archivo local de SQLite `BEGIN IMMEDIATE` **no tiene la misma semántica ni está garantizado sobre el protocolo HTTP**. Turso soporta `BEGIN CONCURRENT` para concurrencia multi-versión.
* **Patrón Recomendado para la Futura Integración:**
  * Conectar con `isolation_level=None` (modo autocommit) si se desea control explícito absoluto de `conn.execute("BEGIN")` y `conn.execute("COMMIT")`.
  * O bien, conectar con `isolation_level="DEFERRED"` (o `"CONCURRENT"` en Turso) y **no ejecutar `conn.execute("BEGIN")` en el código**, delegando el inicio en el driver y llamando únicamente a `conn.commit()` y `conn.rollback()`.

---

## 5. Auditoría Contable de Extremo a Extremo

### Verificación de Invariantes en `db/accounting.py`
1. **Venta Nueva (`validar_coherencia_venta` y `registrar_venta_atomica`):**
   * Impone estrictamente:
     $$\sum_{k=1}^N \text{subtotal}_k = \text{total\_venta} = \text{monto\_efectivo} + \text{monto\_transferencia}$$
   * Comprobación: Si hay cualquier discrepancia de $1 o montos negativos, lanza `ErrorContable` o `ErrorValidacion` y **nada se escribe en la base de datos**.
   * Integridad con Outbox: La inserción en `tickets`, `ticket_items` y `outbox_events` ocurre dentro del mismo bloque transaccional antes de `conn.commit()`. Si falla la inserción del ítem $N$, el ticket y el evento Outbox se revierten íntegramente.
2. **Medios de Pago Soportados:**
   * **[HECHO CONFIRMADO]:** El esquema actual restringe `metodo_pago` estrictamente a `'EFECTIVO'`, `'TRANSFERENCIA'`, `'MIXTO'`.
   * No existen en la actualidad conceptos de comisiones bancarias, propinas ni descuentos globales por ticket en el código base.
   * Las cargas virtuales manejan su propio balance en `cargas_servicios` con la restricción:
     $$\text{monto\_carga} + \text{monto\_adicional} = \text{pago\_efectivo} + \text{pago\_transferencia}$$

---

## 6. Regla de Redistribución de Pagos: Decisión Pendiente

### Análisis de `modificar_item_y_recalcular_ticket()`
* **Regla Codificada:** Se programó el parámetro `regla_redistribucion: str = "EFECTIVO_PRIMERO"`.
  * Si un ítem se elimina o reduce, la diferencia se descuenta primero del efectivo; si el efectivo llega a 0, el saldo se descuenta de la transferencia.
  * Si el subtotal de todos los ítems llega a 0, el ticket pasa automáticamente a `anulado = 1` y los montos a 0.
* **Riesgo Operativo Identificado:**
  * En una venta mixta (ej. $10.000 pagados $5.000 en efectivo y $5.000 en transferencia), si se anula un producto de $4.000, la regla deja al ticket con $1.000 en efectivo y $5.000 en transferencia. Esto altera la proporción real de dinero físico que el cajero recibió en mano frente al comprobante bancario.
* **Estado:** **Decisión pendiente del propietario.**
* **Recomendación Estricta:** Esta función **debe permanecer aislada y deshabilitada en la UI web** durante las primeras fases de migración, hasta que el propietario defina formalmente si aprueba la regla "efectivo primero", prorrateo proporcional, o si la edición de tickets mixtos debe exigir que el cajero re-ingrese manualmente los importes cobrados.

---

## 7. Auditoría del Mecanismo Outbox

### Análisis de `db/accounting.py` frente a Fallos Reales
1. **Concurrencia de Workers (`reclamar_lote_outbox`):**
   * El código utiliza `BEGIN IMMEDIATE` y selecciona filas `WHERE estado = 'PENDIENTE'` actualizándolas a `'EN_PROCESO'`.
   * En SQLite local esto bloquea la base para otros escritores. En un entorno distribuido Turso sobre HTTP, esto debe validarse mediante una actualización condicional atómica:
     ```sql
     UPDATE outbox_events SET estado = 'EN_PROCESO' 
     WHERE id IN (SELECT id FROM outbox_events WHERE estado = 'PENDIENTE' LIMIT ?)
     ```
2. **Auto-Recuperación y Dead-Letter:**
   * Eventos colgados en `EN_PROCESO` por más de 10 minutos son devueltos a `PENDIENTE` incrementando `intentos`.
   * Al alcanzar 5 intentos fallidos, el evento pasa a `ERROR` permanente, evitando loops infinitos de reintento.
3. **Idempotencia de Entidad vs. Evento (Riesgo Detectado):**
   * **[HALLAZGO CRÍTICO]:** `outbox_events` registra eventos cronológicos (`INSERT`, `UPDATE`).
   * Si se produce un fallo de red mientras el worker escribe en Google Sheets, y el lote se reintenta, el worker debe garantizar que en Google Sheets la operación sea idempotente.
   * Si se utiliza un `appendRow()` ciego en Sheets, el reintento duplicará la fila en la planilla.
   * **Requisito Técnico:** Cada fila en Sheets debe contrastarse por su `ID_SISTEMA` (ej. `ticket_id`). Si el `ticket_id` ya existe en la hoja, debe sobreescribir la fila existente; si no existe, debe insertarla.

---

## 8. Clasificación Priorizada de Hallazgos

| Nivel | Componente / Función | Evidencia en Código | Consecuencia Potencial | Recomendación |
|---|---|---|---|---|
| 🔴 **ALTO** | `db/accounting.py` (`reclamar_lote_outbox:246`) | Uso de `BEGIN IMMEDIATE` explícito con driver DB-API. | En Turso Cloud sobre HTTP, `BEGIN IMMEDIATE` puede ser rechazado o tener comportamiento no bloqueante. | Sustituir por la semántica transaccional nativa del SDK (`isolation_level="CONCURRENT"` o transacción implícita). |
| 🔴 **ALTO** | `tests/test_*.py` | Todos los fixtures usan `sqlite3.connect(":memory:")`. | 0% de validación empírica contra el motor libSQL/Turso. | Los tests actuales certifican lógica de negocio, pero no compatibilidad con la nube de Turso. Requiere test en DEV. |
| 🟡 **MEDIO** | `db/accounting.py` (`modificar_item_y_recalcular_ticket:176`) | Regla fija `EFECTIVO_PRIMERO` en cobros mixtos. | Altera arbitrariamente la conciliación de caja física sin confirmación del cajero. | Mantener deshabilitada la edición de tickets mixtos en la UI hasta definición del propietario. |
| 🟡 **MEDIO** | Mecanismo Outbox $\rightarrow$ Sheets | Dependencia de idempotencia del lado de Google Sheets. | Reintentos por caída de red pueden generar filas duplicadas en la planilla de histórico si Sheets no valida por ID. | Diseñar el script de sincronización con lógica Upsert basada en `ID_SISTEMA` o bloqueo de celda. |
| 🟢 **BAJO** | Suite de pruebas (`pytest`) | Requiere `PYTHONPATH=.` para resolver el paquete `db`. | Fallo de importación si se ejecuta `pytest` directamente en CI/CD. | Añadir archivo de configuración `pyproject.toml` o `pytest.ini` con `pythonpath = "."`. |

---

## 9. Validaciones Necesarias Antes de Conectar Turso DEV

Antes de solicitar credenciales o crear la base `genaro-pos-dev`, deben cumplirse estas validaciones en el laboratorio:
1. **Configuración de Pytest:** Crear `pytest.ini` con `pythonpath = .` para ejecución limpia de comandos.
2. **Abstracción del Driver:** Ajustar `db/accounting.py` para aceptar tanto una conexión `sqlite3` local como una conexión `turso_serverless` sin conflictos de sentencias `BEGIN` manuales.
3. **Simulador de Sincronización:** Escribir una prueba unitaria que simule el worker de Google Sheets con caídas de red ficticias (mock) para certificar que ningún evento duplicado altere los saldos.

---

## 10. Recomendación Final

### Veredicto: **APTO CON CONDICIONES**

El laboratorio local ha demostrado solidez técnica e integridad contable matemáticamente probada (18/18 tests verdes en lógica pura). Sin embargo, **no debe intentarse una conexión a Turso Cloud** hasta que:
1. Se adapte la apertura de transacciones en `db/accounting.py` para no depender de `BEGIN IMMEDIATE` de SQLite local.
2. El propietario defina las decisiones contables pendientes (criterio PREPAY y regla de edición de tickets mixtos).
3. Se cree una base de datos aislada expresamente rotulada como `genaro-pos-dev` sin ningún dato de producción.
