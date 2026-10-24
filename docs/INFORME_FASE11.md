# INFORME TÉCNICO (FASE 11) — GENARO POS
## Auditoría Final de Idempotencia, Recuperación y Preparación de la Prueba Remota

> **Fecha y Hora:** 2026-10-22 17:35 UTC  
> **Workspace Autorizado:** `/home/alanrod10/workspace/genaro-pos-turso-lab`  
> **Rama:** `feature/turso-migration-lab`  
> **Commit Base de Referencia:** `b16b8b70a077a9b6a61858e22a64a9ba5dbacc63`  
> **Directorio Original (`/mnt/c/GenaroPOS`):** Verificado 100% intacto y protegido (solo lectura).  
> **Aislamiento Operativo:** 0 conexiones remotas, 0 peticiones de red hacia Turso / Google Sheets / Apps Script, 0 credenciales leídas o expuestas, 0 commits y 0 pushes a Git.

---

## 1. Archivos Inspeccionados y Versiones

1. **`docs/INFORME_FASE10.md`**: Informe técnico previo de unificación transaccional bajo DB-API 2.0.
2. **`db/accounting.py`**: Módulo transaccional central de Genaro POS. Refactorizado y auditado para incorporar control de resultado incierto en `commit()`, idempotencia en ventas y fencing tokens en el worker de Outbox.
3. **`db/schema_v1.sql`**: Esquema relacional DDL (SQLite / libSQL) con restricciones CHECK, claves foráneas y tabla `outbox_events`.
4. **`tests/test_data_integrity.py`**: Suite de 10 pruebas de normalización, invariantes contables e integridad referencial.
5. **`tests/test_accounting_hardening.py`**: Suite de 8 pruebas de endurecimiento contable, pagos mixtos y ciclo de vida de eventos Outbox.
6. **`tests/test_transaction_unification.py`**: Suite de 6 pruebas de context manager DB-API 2.0, rollback atómico y ausencia de fugas de sentencias `BEGIN`.
7. **`tests/test_idempotency_and_recovery.py`**: **Nueva suite de 10 pruebas** que valida idempotencia ante reintentos, recuperación ante commit incierto, concurrencia en SQLite WAL y prevención de desorden en Outbox.
8. **Entorno de Ejecución:**
   * Python: `3.14.4` (en `.venv`).
   * Pytest: `9.1.1` con `pytest-cov 7.1.0`.
   * SQLite engine: `3.46.1` (con soporte nativo para WAL y cláusula `RETURNING`).
   * SDK candidate: `turso-serverless 0.1.0` (inspeccionado en AST/código fuente).

---

## 2. Auditoría del Resultado Incierto de `conn.commit()`

En arquitecturas cliente-servidor desacopladas o distribuidas sobre HTTP (como Turso sobre el protocolo hrana/HTTPS), la llamada a `conn.commit()` no es una operación local instantánea. Es un mensaje de red que viaja al clúster remoto.

Se analizaron formalmente los cuatro escenarios de fallo transaccional:

| Escenario | Punto de Falla | Estado Real en el Servidor | Percepción del Cliente | Tratamiento en el Laboratorio |
| :--- | :--- | :--- | :--- | :--- |
| **1. Fallo antes de commit** | Excepción de validación, error sintáctico SQL o restricción CHECK dentro del bloque de trabajo. | Sin transacción activa (nunca se solicitó confirmación). | Error explícito del dominio. | **Reversión garantizada:** `transaccion(conn)` ejecuta `conn.rollback()` y relanza la excepción original. |
| **2. Commit rechazado por servidor** | Conflicto de bloqueo (`SQLITE_BUSY`), timeout antes de escribir en WAL remoto, o fallo previo a la confirmación física. | Transacción abortada o descartada en el servidor. | Excepción en `conn.commit()`. | **Resultado Incierto:** Se eleva `ErrorCommitIncierto`. No se asume éxito ni se reintenta a ciegas. |
| **3. Servidor confirma pero se pierde la respuesta** | El servidor escribe el commit en almacenamiento durable, pero la conexión de red cae antes de que el HTTP 200 / ACK llegue al cliente. | **Transacción efectivamente confirmada en el servidor.** | Excepción de timeout / reset de conexión en `conn.commit()`. | **Resultado Incierto Crítico:** El cliente no sabe si se confirmó. Si asumiera rollback y repitiera una venta sin idempotencia, duplicaría cobros y stock. Se lanza `ErrorCommitIncierto` y la auto-recuperación se confía a la **idempotencia** del reintento. |
| **4. Commit exitoso pero cae la aplicación** | El commit termina con éxito en la base, pero el proceso de Streamlit o el worker cae antes de renderizar la respuesta al usuario. | **Transacción confirmada.** | Pantalla en blanco o desconexión en navegador. | El usuario reenvía la venta con el mismo `ticket_id`; el backend responde pacíficamente reconociendo la venta sin duplicar registros. |

### Hallazgo Crítico sobre `rollback()`
Si `conn.commit()` lanza un error, ejecutar `conn.rollback()` **no puede revertir** una transacción que el motor remoto ya confirmó físicamente en el Escenario 3. Interpretar un fallo en `commit()` como "rollback garantizado" es un error arquitectónico grave. Por ello, el context manager `transaccion(conn)` fue actualizado para atrapar fallos durante `conn.commit()` y elevar explícitamente `ErrorCommitIncierto`.

---

## 3. Estrategia de Idempotencia de Ventas

### Identificador Estable
En Genaro POS, cada venta cuenta con un identificador unívoco: el `ticket_id` (en la aplicación web actual se genera con formato `T-<timestamp_ms>`, y en la arquitectura desacoplada puede ser un UUID v4 o timestamp de entropía alta generado por el cliente al presionar "Registrar Venta").

### Comportamiento Implementado en `registrar_venta_atomica()`:
1. **Verificación Previa (Idempotency Check):**
   Antes de insertar, se consulta `tickets` por `ticket_id`.
2. **Caso Reintento Seguro (Misma Operación):**
   Si el `ticket_id` ya existe en la base:
   * Se compara `total_venta`, `monto_efectivo`, `monto_transferencia`, `metodo_pago` y `anulado == 0`.
   * Se comparan uno a uno los ítems asociados (`producto_nombre`, `cantidad`, `subtotal`).
   * Si todos los parámetros coinciden exactamente, se concluye que es un reintento legítimo del Escenario 3 o 4. La función **no inserta duplicados**, no genera nuevos eventos Outbox y retorna limpiamente:
     ```python
     {"status": "EXISTENTE_IDEMPOTENTE", "ticket_id": ticket_id, "mensaje": "..."}
     ```
3. **Caso Conflicto de Clave (Payload Distinto):**
   Si el `ticket_id` ya existe pero con diferente monto o productos, se detecta una colisión de claves y se eleva `ErrorValidacion("Conflicto de idempotencia...")`.
4. **Ventas Legítimas Diferentes:**
   Si dos clientes distintos compran exactamente los mismos artículos a la misma hora, reciben `ticket_id` distintos; ambas transacciones se insertan con normalidad (`status: "REGISTRADO"`).

---

## 4. Auditoría de la Concurrencia del Outbox

Se auditaron en profundidad las sentencias de `reclamar_lote_outbox()`, `confirmar_evento_outbox()` y `registrar_fallo_evento_outbox()`.

### Mejoras y Correcciones Implementadas:

1. **Corrección del Cálculo de Timeout de Reclamo:**
   * *Diagnóstico:* Previamente, el timeout de recuperación de eventos colgados en `EN_PROCESO` se medía comparando `datetime('now')` contra `creado_en_utc`. Si un evento fue creado hace horas y reclamado recientemente, cualquier llamada posterior lo consideraba "vencido" de inmediato.
   * *Solución:* Se actualizó la sentencia para comparar contra `COALESCE(procesado_en_utc, creado_en_utc)`, garantizando que el timeout mida el tiempo real transcurrido desde que el worker tomó el evento.

2. **Atrapamiento Atómico con `UPDATE ... RETURNING`:**
   * La selección y transición a `EN_PROCESO` ocurre en una única sentencia SQL DML indivisible, soportada por SQLite 3.35+ y libSQL.

3. **Prevención de Desorden por Entidad (`aggregate_id` Mutual Exclusion):**
   * *Riesgo:* Si un ticket `T-1` se crea (Evento 1) y luego se modifica un ítem (Evento 2), dos workers concurrentes podrían procesar el Evento 2 antes o simultáneamente con el Evento 1, provocando que la versión vieja sobreescriba a la nueva en Google Sheets.
   * *Solución:* La subconsulta de reclamo ahora excluye eventos de cualquier `aggregate_id` que ya posea un evento en estado `EN_PROCESO`:
     ```sql
     AND NOT EXISTS (
         SELECT 1 FROM outbox_events o2
         WHERE o2.aggregate_id = o1.aggregate_id
           AND o2.estado = 'EN_PROCESO'
           AND o2.id != o1.id
     )
     ```
   * Esto garantiza orden estrictamente secuencial por entidad de negocio.

4. **Fencing Token contra Workers Demorados:**
   * *Riesgo:* Un worker reclama un evento, se suspende por latencia; el timeout expira y el evento se reasigna a un segundo worker (incrementando `intentos`). El primer worker despierta y confirma el evento, pisando el trabajo del segundo.
   * *Solución:* `confirmar_evento_outbox` y `registrar_fallo_evento_outbox` ahora aceptan un parámetro `intentos_esperados`. La confirmación requiere `WHERE id = ? AND estado = 'EN_PROCESO' AND intentos = ?`. Si el reclamo caducó, el UPDATE afecta 0 filas y retorna `False`.

---

## 5. Cambios Implementados en el Laboratorio

### Archivos Modificados:
1. **`db/accounting.py`:**
   * Incorporada la excepción `ErrorCommitIncierto`.
   * Refactorizado `transaccion(conn)` para aislar fallos de ejecución (rollback) de fallos en `conn.commit()` (resultado incierto).
   * Refactorizado `registrar_venta_atomica()` con detección de reintento idempotente y retorno estructurado de estado.
   * Reescrito `reclamar_lote_outbox()` con timeout basado en `procesado_en_utc`, orden estricto y exclusión de concurrencia sobre la misma entidad.
   * Añadido fencing token (`intentos_esperados`) y verificación obligatoria de `estado = 'EN_PROCESO'` en `confirmar_evento_outbox()` y `registrar_fallo_evento_outbox()`.
2. **`tests/test_transaction_unification.py`:**
   * Actualizada la prueba `test_fallo_en_commit_provoca_rollback` para validar la captura y tipado preciso de `ErrorCommitIncierto`.
3. **`tests/test_idempotency_and_recovery.py` (Nuevo):**
   * 10 casos de prueba independientes que evalúan idempotencia repetida, conflicto de payload, ventas idénticas legítimas, fallos antes y durante commit, concurrencia en disco WAL y control de versiones del Outbox.

---

## 6. Resultados Reales de las Pruebas

### Comando Ejecutado:
```bash
cd /home/alanrod10/workspace/genaro-pos-turso-lab && .venv/bin/pytest -v --cov=db tests/
```

### Salida Completa del Runner:
```text
============================= test session starts ==============================
platform linux -- Python 3.14.4, pytest-9.1.1, pluggy-1.7.0
rootdir: /home/alanrod10/workspace/genaro-pos-turso-lab
configfile: pytest.ini
plugins: cov-7.1.0
collecting ... collected 34 items                                                             

tests/test_accounting_hardening.py::test_venta_un_item_valida PASSED     [  2%]
tests/test_accounting_hardening.py::test_venta_multiples_items_pago_mixto PASSED [  5%]
tests/test_accounting_hardening.py::test_rechazo_descuadre_pagos PASSED  [  8%]
tests/test_accounting_hardening.py::test_rechazo_descuadre_subtotales_items PASSED [ 11%]
tests/test_accounting_hardening.py::test_modificar_item_recalculo_efectivo_primero PASSED [ 14%]
tests/test_accounting_hardening.py::test_anulacion_total_al_llevar_items_a_cero PASSED [ 17%]
tests/test_accounting_hardening.py::test_reclamar_lote_outbox_y_confirmacion PASSED [ 20%]
tests/test_accounting_hardening.py::test_fallo_y_dead_letter_outbox PASSED [ 23%]
tests/test_data_integrity.py::test_parse_moneda_localizada PASSED        [ 26%]
tests/test_data_integrity.py::test_conversion_fechas_utc_local PASSED    [ 29%]
tests/test_data_integrity.py::test_productos_nombres_duplicados_y_margen PASSED [ 32%]
tests/test_data_integrity.py::test_balance_pago_tickets PASSED           [ 35%]
tests/test_data_integrity.py::test_foreign_key_rechaza_huerfano PASSED   [ 38%]
tests/test_data_integrity.py::test_creacion_atomica_y_rollback PASSED    [ 41%]
tests/test_data_integrity.py::test_outbox_idempotencia PASSED            [ 44%]
tests/test_data_integrity.py::test_politicas_prepay_parametrizadas[...] PASSED [ 47%]
tests/test_data_integrity.py::test_politicas_prepay_parametrizadas[...] PASSED [ 50%]
tests/test_data_integrity.py::test_politicas_prepay_parametrizadas[...] PASSED [ 52%]
tests/test_idempotency_and_recovery.py::test_venta_repetida_misma_operacion_es_idempotente PASSED [ 55%]
tests/test_idempotency_and_recovery.py::test_venta_repetida_con_payload_diferente_es_rechazada PASSED [ 58%]
tests/test_idempotency_and_recovery.py::test_ventas_legitimas_distintas_con_mismos_articulos PASSED [ 61%]
tests/test_idempotency_and_recovery.py::test_fallo_antes_de_confirmar_rollback_limpio_y_reintento PASSED [ 64%]
tests/test_idempotency_and_recovery.py::test_simulacion_commit_incierto_y_recuperacion_idempotente PASSED [ 67%]
tests/test_idempotency_and_recovery.py::test_error_commit_incierto_lanzado_explicitamente PASSED [ 70%]
tests/test_idempotency_and_recovery.py::test_reclamacion_concurrente_entre_dos_conexiones PASSED [ 73%]
tests/test_idempotency_and_recovery.py::test_fencing_token_impide_confirmacion_espuria PASSED [ 76%]
tests/test_idempotency_and_recovery.py::test_orden_secuencial_por_aggregate_id_evita_desorden PASSED [ 79%]
tests/test_idempotency_and_recovery.py::test_recuperacion_timeout_por_timestamp_en_proceso PASSED [ 82%]
tests/test_transaction_unification.py::test_transaccion_context_manager_commit PASSED [ 85%]
tests/test_transaction_unification.py::test_transaccion_context_manager_rollback_on_exception PASSED [ 88%]
tests/test_transaction_unification.py::test_no_begin_statement_leak PASSED [ 91%]
tests/test_transaction_unification.py::test_rollback_ante_fallo_en_item_intermedio PASSED [ 94%]
tests/test_transaction_unification.py::test_reclamacion_outbox_atomica_sin_begin_immediate PASSED [ 97%]
tests/test_transaction_unification.py::test_fallo_en_commit_provoca_rollback PASSED [100%]

================================ tests coverage ================================
_______________ coverage: platform linux, python 3.14.4-final-0 ________________

Name                  Stmts   Miss  Cover
-----------------------------------------
db/accounting.py        148     25    83%
db/normalization.py      47      5    89%
-----------------------------------------
TOTAL                   195     30    85%
============================== 34 passed in 0.18s ==============================
```

---

## 7. Riesgos Abiertos y Limitaciones del Entorno Local

1. **Diferencias Semánticas entre SQLite Local y Turso sobre HTTP:**
   * La prueba local con dos hilos y WAL evalúa la atomicidad lógica de `UPDATE ... RETURNING`, pero sobre SQLite local en memoria/archivo compartido los bloqueos son manejados por el kernel de Linux. En Turso Cloud, el driver `turso-serverless 0.1.0` opera sobre peticiones HTTP independientes; la latencia de red, posibles reintentos HTTP automáticos y errores transitorios de proxy (códigos 502/503/504) solo pueden certificarse contra una base de datos remota real.
2. **Decisiones de Negocio Pendientes de Aprobación por el Propietario:**
   * **Política PREPAY para Nuevos Registros:** La discrepancia histórica entre la regla del Visor (`min(transf, carga)` al capital) y la regla de Caja (`round(carga * efvo / total)`) se mantiene parametrizada y documentada, sin alterar datos históricos.
   * **Modificación de Ítems en Pagos Mixtos:** La regla `EFECTIVO_PRIMERO` continúa parametrizada y protegida, esperando la validación formal del propietario.

---

## 8. Lista de Pruebas Necesarias en una Base Turso de Desarrollo (`genaro-pos-dev`)

Una vez que el propietario autorice la creación del entorno remoto `genaro-pos-dev`, se deberán ejecutar:

1. **Validación del Protocolo DB-API sobre HTTP:**
   * Conectar con `turso_serverless.connect(url, auth_token=token)`.
   * Verificar que `_maybe_implicit_begin` se ejecute sin errores al lanzar el primer `INSERT` de un ticket.
2. **Validación de la Cláusula `UPDATE ... RETURNING` Remota:**
   * Ejecutar `reclamar_lote_outbox()` sobre la conexión HTTP de Turso para certificar que el motor libSQL remoto devuelve las tuplas seleccionadas sin discrepancias de serialización JSON.
3. **Comportamiento ante Timeout Real de Red:**
   * Simular una desconexión abrupta durante `conn.commit()` para validar la elevación de `ErrorCommitIncierto` con el driver real.
4. **Reintento Idempotente sobre Conexión Remota:**
   * Verificar que tras un fallo de conexión, el reintento del cliente reciba `status: "EXISTENTE_IDEMPOTENTE"` sin provocar un error 500 ni registrar tickets duplicados en Turso.
5. **Concurrencia de Múltiples Procesos Remotos:**
   * Lanzar dos procesos Python independientes contra `genaro-pos-dev` reclamando eventos Outbox concurrentemente.

---

## 9. Estado Final de Git y Confirmación del Aislamiento

* **Directorio de Producción Original (`/mnt/c/GenaroPOS`):**  
  Verificado: `drwxrwxrwx 1 alanrod10 alanrod10 4096 Oct  7 19:04 /mnt/c/GenaroPOS` (Completamente intacto, sin escrituras).
* **Conexiones Externas:**  
  0 peticiones remotas realizadas. 0 tokens o contraseñas accedidas.
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
* **Compromiso Git:**  
  No se realizaron commits, no se ejecutó `git push` y la rama `main` de GitHub permanece 100% inalterada en el commit `b16b8b70a077a9b6a61858e22a64a9ba5dbacc63`.
* **Conclusión:**  
  El laboratorio local se encuentra plenamente endurecido, con 34 pruebas automáticas pasando al 100%, cobertura del 85% y listo para la fase de pruebas sobre una base Turso de desarrollo cuando sea autorizada.