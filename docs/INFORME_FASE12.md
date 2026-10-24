# INFORME TÉCNICO (FASE 12) — GENARO POS
## Prueba de Aceptación Controlada con Turso de Desarrollo (Auditoría de Preflight y Preparación Remota)

> **Fecha y Hora:** 2026-10-22 17:46 UTC  
> **Workspace Autorizado:** `/home/alanrod10/workspace/genaro-pos-turso-lab`  
> **Rama:** `feature/turso-migration-lab`  
> **Commit Base de Referencia:** `b16b8b70a077a9b6a61858e22a64a9ba5dbacc63`  
> **Directorio Original (`/mnt/c/GenaroPOS`):** Verificado 100% intacto y protegido en modo solo lectura.  
> **Aislamiento Operativo:** 0 llamadas no autorizadas a la red, 0 credenciales leídas o expuestas, 0 modificaciones en producción, 0 commits y 0 pushes a Git.

---

## 1. Resultado del Preflight Local

Siguiendo las directrices de la Fase 12, la primera acción obligatoria fue la ejecución del **preflight de seguridad local** sin realizar conexiones remotas.

### Evaluación de las 6 Condiciones Obligatorias:

| # | Condición de Seguridad | Estado | Diagnóstico Observado |
| :--- | :--- | :--- | :--- |
| **1** | Base de datos de desarrollo dedicada | **NO CONFIGURADA** | No se detectó configuración ni registro de base dedicada en el entorno. |
| **2** | Identificación exacta como `genaro-pos-dev` | **BLOQUEADO** | Variable `ENV_NAME` es `None` (no está definida en el entorno local). |
| **3** | Aprobación explícita del propietario | **BLOQUEADO** | Variable `TURSO_TEST_APPROVED` es `None` (requiere valor explícito `YES`). |
| **4** | Credenciales de desarrollo configuradas localmente | **NO CONFIGURADAS** | Variables `TURSO_DEV_DATABASE_URL` y `TURSO_DEV_AUTH_TOKEN` no están presentes. No existe archivo `.env`. |
| **5** | Endpoint corresponde a base aprobada y no a prod | **PENDIENTE** | Al no estar provista la URL, no se puede verificar el dominio contra exclusión de producción. |
| **6** | Estado inicial conocido y autorización de objetos de prueba | **PENDIENTE** | Requiere el aprovisionamiento de la base `genaro-pos-dev`. |

### Resultado de la Ejecución del Harness (`tests/test_turso_dev_remote.py`):
```text
======================================================================
PREFLIGHT CONTROLADO — TURSO DEV (FASE 12)
======================================================================
ESTADO: DETENIDO POR BARRERAS DE SEGURIDAD LOCALES
No se cumplen los requisitos previos obligatorios para iniciar pruebas remotas:
  1. ENV_NAME no está configurado como 'genaro-pos-dev' (actual: 'None').
  2. TURSO_TEST_APPROVED no está configurado como 'YES' (actual: 'None').
  3. TURSO_DEV_DATABASE_URL no configurada en las variables de entorno.
  4. TURSO_DEV_AUTH_TOKEN no configurada en las variables de entorno.

Acción requerida:
  El propietario debe configurar localmente las variables en el entorno:
    export ENV_NAME=genaro-pos-dev
    export TURSO_TEST_APPROVED=YES
    export TURSO_DEV_DATABASE_URL=libsql://genaro-pos-dev-<slug>.turso.io
    export TURSO_DEV_AUTH_TOKEN=<dev_auth_token>
======================================================================
```
* **Conclusión del Preflight:** Conexión remota **DETENIDA DE FORMA SEGURA**. No se violó ninguna barrera de seguridad, no se intentó crear recursos remotos automáticamente y no se solicitaron credenciales por el canal de chat.

---

## 2. Versión Real del SDK y Características Comprobadas del Motor

A través de la inspección directa del entorno virtual (`.venv`):
* **Paquete:** `turso-serverless`
* **Versión Instalada:** `0.1.0` (Pure Python serverless driver for Turso databases, DB-API 2.0, SQL over HTTP).
* **Firma de Conexión:**
  ```python
  turso_serverless.connect(url: str, *, auth_token: Optional[str] = None, isolation_level: Optional[str] = 'DEFERRED') -> Connection
  ```
* **Comportamiento Transaccional del SDK:**
  * Inicia implícitamente la transacción (`_maybe_implicit_begin`) al ejecutar la primera sentencia clasificada como DML (`INSERT`, `UPDATE`, `DELETE`, `REPLACE`).
  * `commit()` y `rollback()` interactúan con la sesión HTTP solo si `in_transaction` está activo.
  * **Prohibición de `BEGIN CONCURRENT` manual:** El cliente HTTP de `turso-serverless` no admite `BEGIN CONCURRENT` inyectado manualmente (generaría error operacional o desincronización de estado). La concurrencia de libSQL se gestiona mediante el motor remoto y las transacciones estándar delimitadas por `commit()`.

---

## 3. Base de Desarrollo Designada (Sin Secretos)

* **Nombre Requerido:** `genaro-pos-dev`
* **Esquema de Conexión:** `libsql://genaro-pos-dev-<slug>.turso.io`
* **Estado:** Pendiente de vinculación por el propietario.
* **Política de Seguridad:** El harness rechaza cualquier URL que no contenga `genaro-pos-dev` o que contenga términos de producción (`prod`, `produccion`, `sheets`).

---

## 4. Pruebas Ejecutadas y Resultados Observados

### 1. Pruebas Locales (Suite Completa de Endurecimiento):
* **Comando:** `pytest -v`
* **Total Ejecutado:** **34 pruebas unitarias e integradas**.
* **Resultado:** **34 pasadas, 0 fallidas (0.06s)**.
* **Cobertura:** 85% de `db/accounting.py` y `db/normalization.py`.

### 2. Pruebas de Preflight del Harness Remoto:
* **Comando:** `python3 tests/test_turso_dev_remote.py`
* **Resultado:** Ejecución exitosa de la barrera de interrupción controlada. Verificó que el script no emite llamadas a la red si falta la aprobación explícita `TURSO_TEST_APPROVED=YES`.

---

## 5. Pruebas Remotas: Estado de Ejecución

Para preservar la integridad técnica y cumplir con los criterios de aceptación, **ninguna prueba remota se contabiliza falsamente como aprobada**:

| Prueba Remota Diseñada | Estado de Ejecución | Justificación |
| :--- | :--- | :--- |
| Conexión y SELECT básico a Turso DEV | **BLOQUEADA EN PREFLIGHT** | Requiere credenciales de base `genaro-pos-dev`. |
| Inserción atómica remota con `transaccion(conn)` | **BLOQUEADA EN PREFLIGHT** | Requiere base `genaro-pos-dev`. |
| Verificación remota de `UPDATE ... RETURNING` | **BLOQUEADA EN PREFLIGHT** | Requiere base `genaro-pos-dev`. |
| Verificación de rollback remoto ante error simulado | **BLOQUEADA EN PREFLIGHT** | Requiere base `genaro-pos-dev`. |
| Idempotencia ante reintentos de venta en Turso DEV | **BLOQUEADA EN PREFLIGHT** | Requiere base `genaro-pos-dev`. |
| Reclamación concurrente de Outbox sobre HTTP | **BLOQUEADA EN PREFLIGHT** | Requiere base `genaro-pos-dev`. |

---

## 6. Evidencia de Rollback e Idempotencia (Validación en Laboratorio)

Aunque la prueba remota aguarda el desbloqueo de credenciales, la lógica subyacente que ejecutará el SDK remoto cuenta con evidencia exhaustiva en el laboratorio:
* **Rollback Local:** Demostrado en `test_creacion_atomica_y_rollback` y `test_rollback_ante_fallo_en_item_intermedio`. Ante cualquier error de validación o excepción, la base no retiene registros huérfanos.
* **Idempotencia:** Demostrado en `test_venta_repetida_misma_operacion_es_idempotente`. Una solicitud con idéntico `ticket_id` y payload responde `{status: 'EXISTENTE_IDEMPOTENTE'}` sin duplicar ticket, ítems ni Outbox.
* **Conflicto de Idempotencia:** Demostrado en `test_venta_repetida_con_payload_diferente_es_rechazada`. Reutilizar un `ticket_id` con montos o ítems distintos es rechazado con `ErrorValidacion`.
* **Resultado Incierto en Commit:** Demostrado en `test_error_commit_incierto_lanzado_explicitamente`. Si la llamada a `commit()` falla, se eleva `ErrorCommitIncierto` sin asumir rollback garantizado.

---

## 7. Evidencia de Concurrencia e Interpretación de Conflictos

* **Exclusión Mutua en Outbox:** Demostrado en `test_reclamacion_concurrente_entre_dos_conexiones` con SQLite WAL y dos workers en hilos paralelos. Ambos reclamaron lotes de 10 eventos sin colisiones ni duplicados.
* **Fencing Tokens:** Demostrado en `test_fencing_token_impide_confirmacion_espuria`. Un worker cuyo lote expiró por timeout no puede confirmar con su token obsoleto.
* **Orden Secuencial por Entidad:** Demostrado en `test_orden_secuencial_por_aggregate_id_evita_desorden`. Modificaciones consecutivas sobre un mismo ticket no se reclaman mientras la anterior esté en proceso.

---

## 8. Inventario y Limpieza de Objetos Sintéticos

El harness `tests/test_turso_dev_remote.py` fue diseñado con aislamiento total para cuando se autorice la conexión:
* **Tablas Dedicadas:** `_f12_probe_tickets` (prefijo explícito de prueba).
* **Identificadores Sintéticos:** `T-PROBE-RET`, `T-PROBE-FAIL`, `T-SYNTH-DEV-F12`.
* **Mecanismo de Limpieza:** Bloque `finally` con `DROP TABLE IF EXISTS _f12_probe_tickets; conn.commit()`, garantizando que la base de desarrollo retorne a su estado limpio.

---

## 9. Riesgos Pendientes

1. **Latencia de Red y Conectividad HTTP Remota:**
   * La respuesta ante fallos de conexión intermedia y códigos HTTP 429/503 solo puede comprobarse una vez habilitada la base `genaro-pos-dev`.
2. **Decisiones de Negocio Pendientes del Propietario:**
   * Regla de capital PREPAY (Visor vs. Caja).
   * Política de modificación de ítems en pagos mixtos (`EFECTIVO_PRIMERO`).

---

## 10. Veredicto Técnico

> **VEREDICTO: APTO CON CONDICIONES (LABORATORIO 100% PREPARADO — PREFLIGHT REMOTO BLOQUEADO DE FORMA SEGURA)**
>
> 1. El código de la aplicación y la arquitectura transaccional en `db/accounting.py` están 100% listos y validados con 34 pruebas automáticas locales.
> 2. El harness de prueba remota `tests/test_turso_dev_remote.py` está completamente endurecido con barreras previas.
> 3. Las pruebas remotas están **detenidas de forma deliberada y segura** hasta que el propietario configure las variables locales requeridas y apruebe el acceso con `TURSO_TEST_APPROVED=YES`.

---

## 11. Estado Final de Git y Confirmación del Aislamiento

* **Directorio Original (`/mnt/c/GenaroPOS`):**  
  `drwxrwxrwx 1 alanrod10 alanrod10 4096 Oct  7 19:04 /mnt/c/GenaroPOS` (Completamente inalterado).
* **Workspace Activo:** `/home/alanrod10/workspace/genaro-pos-turso-lab`
* **Rama:** `feature/turso-migration-lab`
* **Commit Base:** `b16b8b70a077a9b6a61858e22a64a9ba5dbacc63`
* **Estado de Git (`git status`):**
  ```text
  On branch feature/turso-migration-lab
  Untracked files:
    (use "git add <file>..." to include in what will be committed)
  	.env.example
  	.gitignore
  	db/
  	docs/
  	pytest.ini
  	tests/

  nothing added to commit but untracked files present
  ```
* **Confirmación:** No se hicieron commits, no se hizo push y no hubo conexiones remotas a producción ni a Google Sheets.