# INFORME TÉCNICO (FASE 10) — GENARO POS
## Unificación Transaccional y Preparación para Turso/libSQL

> **Fecha y Hora:** 2026-10-22 17:15 UTC  
> **Workspace Auditado y Modificado:** `/home/alanrod10/workspace/genaro-pos-turso-lab`  
> **Rama:** `feature/turso-migration-lab`  
> **Commit Base:** `b16b8b70a077a9b6a61858e22a64a9ba5dbacc63`  
> **Directorio Original (`/mnt/c/GenaroPOS`):** Verificado 100% intacto y sin modificaciones.  
> **Aislamiento Operativo:** Cumplimiento estricto. 0 conexiones remotas, 0 DDL ejecutado en la nube, 0 alteraciones en Google Sheets/Apps Script y 0 commits o pushes a Git.

---

## 1. Archivos y Versiones Inspeccionados

1. `db/accounting.py` (código fuente refactorizado para unificación transaccional).
2. `db/schema_v1.sql` (esquema DDL V1 sin alteraciones).
3. `turso-serverless 0.1.0` (inspección de código fuente AST/Python en `.venv/lib/python3.14/site-packages/turso_serverless/connection.py`).
4. `docs/INFORME_FASE9.md` e `docs/INFORME_FASE8.md`.
5. `tests/test_data_integrity.py` y `tests/test_accounting_hardening.py`.
6. `tests/test_transaction_unification.py` (nueva suite de verificación transaccional).
7. `pytest.ini` (configuración del runner de pruebas).

---

## 2. Evidencia Real del Comportamiento del SDK Instalado (`turso-serverless 0.1.0`)

A partir de la inspección directa del archivo `connection.py` del paquete:
* **Firma de Conexión:**
  ```python
  def connect(url: str, *, auth_token: Optional[str] = None, isolation_level: Optional[str] = "DEFERRED") -> Connection
  ```
* **Inicio Implícito de Transacciones (`_maybe_implicit_begin`):**
  ```python
  def _maybe_implicit_begin(self, sql: str) -> None:
      if self.isolation_level is not None and not self.in_transaction and _is_dml(sql):
          level = self.isolation_level or "DEFERRED"
          self._execute_stmt(f"BEGIN {level}", want_rows=False)
  ```
* **Comprobación de DML (`_is_dml`):**
  Identifica como DML exclusivamente las palabras clave `INSERT`, `UPDATE`, `DELETE`, `REPLACE`.
* **Manejo de Commit y Rollback:**
  `commit()` y `rollback()` envían `"COMMIT"` o `"ROLLBACK"` solo si `self.in_transaction == True`.
* **Hallazgo Crítico:** Si el código cliente envía sentencias SQL directas como `conn.execute("BEGIN")` o `conn.execute("BEGIN IMMEDIATE")`:
  1. Puede entrar en conflicto con `_maybe_implicit_begin` si el estado `in_transaction` no se sincronizó a tiempo en la sesión HTTP.
  2. En SQLite estándar (PEP 249), ejecutar `execute("BEGIN")` en una conexión con `isolation_level` activo lanza un error operacional (`cannot start a transaction within a transaction`).
  3. `BEGIN IMMEDIATE` es una directiva específica de bloqueo de archivo de SQLite que **no tiene garantía semántica de bloqueo exclusivo de base distribuida en Turso sobre HTTP**.

---

## 3. Estrategia Elegida y Justificación

### Estrategia Seleccionada: **Transacciones DB-API 2.0 Puras (Context Manager Unificado)**
Se descartó la mezcla de comandos SQL manuales (`execute("BEGIN")`) y se adoptó la arquitectura transaccional estándar de Python:
1. **Nivel de Aislamiento:** Se utiliza el inicio implícito nativo del driver ante la primera sentencia DML (`INSERT`/`UPDATE`).
2. **Context Manager `transaccion(conn)`:**
   ```python
   @contextmanager
   def transaccion(conn: Any) -> Iterator[None]:
       try:
           yield
           conn.commit()
       except Exception:
           conn.rollback()
           raise
   ```
3. **Reclamación de Outbox sin `BEGIN IMMEDIATE`:**
   En lugar de depender de un bloqueo exclusivo de base de datos completa (`BEGIN IMMEDIATE`), se implementó la cláusula estándar **`UPDATE ... RETURNING`** combinada con una subconsulta acotada:
   ```sql
   UPDATE outbox_events
   SET estado = 'EN_PROCESO'
   WHERE id IN (
       SELECT id FROM outbox_events 
       WHERE estado = 'PENDIENTE' 
       ORDER BY id ASC 
       LIMIT ?
   )
   RETURNING id, aggregate_type, aggregate_id, operation, payload_json, intentos;
   ```
   * *Ventajas:*
     * Es una **única sentencia DML atómica** soportada por SQLite 3.35+ y el motor libSQL de Turso.
     * Elimina el riesgo de que dos workers concurrentes seleccionen los mismos eventos.
     * Es completamente agnóstica del backend (funciona igual en SQLite local que en Turso Cloud sobre HTTP).

---

## 4. Cambios Realizados en el Laboratorio (Archivo por Archivo)

1. **`db/accounting.py`:**
   * Se eliminaron todas las sentencias `conn.execute("BEGIN")` y `conn.execute("BEGIN IMMEDIATE")`.
   * Se incorporó el context manager `transaccion(conn)` en `registrar_venta_atomica`, `modificar_item_y_recalcular_ticket`, `reclamar_lote_outbox`, `confirmar_evento_outbox` y `registrar_fallo_evento_outbox`.
   * Se reescribió `reclamar_lote_outbox` utilizando la sentencia atómica `UPDATE ... RETURNING`.
2. **`pytest.ini` (Nuevo):**
   * Configura `pythonpath = .` y `testpaths = tests`, permitiendo la ejecución limpia y directa de `pytest -v` en cualquier entorno de CI o terminal.
3. **`tests/test_transaction_unification.py` (Nuevo):**
   * Suite de 6 pruebas específicas para verificar la ausencia de fugas de sentencias `BEGIN`, rollback ante excepciones intermedias, reclamación atómica con `RETURNING` y manejo de excepciones durante el commit.

---

## 5. Resultados de las Pruebas (Antes y Después)

* **Pruebas Previas (Fase 9):** 18 pruebas ejecutadas.
* **Pruebas Nuevas (Fase 10):** 6 pruebas añadidas en `test_transaction_unification.py`.
* **Total Ejecutado:** **24 pruebas unitarias**.
* **Comando:**
  ```bash
  cd /home/alanrod10/workspace/genaro-pos-turso-lab && source .venv/bin/activate && pytest -v
  ```
* **Salida de Ejecución:**
  ```text
  tests/test_accounting_hardening.py::test_venta_un_item_valida PASSED     [  4%]
  tests/test_accounting_hardening.py::test_venta_multiples_items_pago_mixto PASSED [  8%]
  tests/test_accounting_hardening.py::test_rechazo_descuadre_pagos PASSED  [ 12%]
  tests/test_accounting_hardening.py::test_rechazo_descuadre_subtotales_items PASSED [ 16%]
  tests/test_accounting_hardening.py::test_modificar_item_recalculo_efectivo_primero PASSED [ 20%]
  tests/test_accounting_hardening.py::test_anulacion_total_al_llevar_items_a_cero PASSED [ 25%]
  tests/test_accounting_hardening.py::test_reclamar_lote_outbox_y_confirmacion PASSED [ 29%]
  tests/test_accounting_hardening.py::test_fallo_y_dead_letter_outbox PASSED [ 33%]
  tests/test_data_integrity.py::test_parse_moneda_localizada PASSED        [ 37%]
  tests/test_data_integrity.py::test_conversion_fechas_utc_local PASSED    [ 41%]
  tests/test_data_integrity.py::test_productos_nombres_duplicados_y_margen PASSED [ 45%]
  tests/test_data_integrity.py::test_balance_pago_tickets PASSED           [ 50%]
  tests/test_data_integrity.py::test_foreign_key_rechaza_huerfano PASSED   [ 54%]
  tests/test_data_integrity.py::test_creacion_atomica_y_rollback PASSED    [ 58%]
  tests/test_data_integrity.py::test_outbox_idempotencia PASSED            [ 62%]
  tests/test_data_integrity.py::test_politicas_prepay_parametrizadas[...] PASSED [ 66%]
  tests/test_data_integrity.py::test_politicas_prepay_parametrizadas[...] PASSED [ 70%]
  tests/test_data_integrity.py::test_politicas_prepay_parametrizadas[...] PASSED [ 75%]
  tests/test_transaction_unification.py::test_transaccion_context_manager_commit PASSED [ 79%]
  tests/test_transaction_unification.py::test_transaccion_context_manager_rollback_on_exception PASSED [ 83%]
  tests/test_transaction_unification.py::test_no_begin_statement_leak PASSED [ 87%]
  tests/test_transaction_unification.py::test_rollback_ante_fallo_en_item_intermedio PASSED [ 91%]
  tests/test_transaction_unification.py::test_reclamacion_outbox_atomica_sin_begin_immediate PASSED [ 95%]
  tests/test_transaction_unification.py::test_fallo_en_commit_provoca_rollback PASSED [100%]

  ============================== 24 passed in 0.13s ==============================
  ```

---

## 6. Riesgos y Cuestiones Sin Resolver

1. **Semántica de Concurrencia Remota:**
   * Las 24 pruebas se ejecutan contra SQLite local en memoria. La interacción con múltiples sesiones concurrentes sobre la red de Turso Cloud con `BEGIN CONCURRENT` no ha sido validada empíricamente en la nube.
2. **Decisiones de Negocio Aún Pendientes de Aprobación:**
   * La regla de redistribución de pagos mixtos (`EFECTIVO_PRIMERO`) se mantiene documentada y parametrizada pero **pendiente de aprobación**.
   * La política PREPAY unificada para nuevas operaciones (Visor vs. Caja) continúa **pendiente de aprobación**.

---

## 7. Validaciones Necesarias en Turso de Desarrollo (Fase Futura)

Una vez que el propietario autorice la creación de la base `genaro-pos-dev`:
1. Ejecutar el harness `tests/test_turso_dev_remote.py` configurando `ENV_NAME="genaro-pos-dev"`.
2. Probar la ejecución de `reclamar_lote_outbox()` con `UPDATE ... RETURNING` sobre la conexión `turso_serverless.connect()`.
3. Certificar que el driver remoto maneje adecuadamente los reintentos ante códigos de estado HTTP 429 (rate-limit) o 503 temporales.

---

## 8. Confirmación de Aislamiento y Estado Final de Git

* **Directorio Original (`/mnt/c/GenaroPOS`):** Verificado sin modificaciones.
* **Red Externa:** 0 peticiones hacia Turso, libSQL, Google Sheets o Apps Script.
* **Estado de Git:**
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
* **Conclusión:** No se realizó commit ni push. El laboratorio queda unificado, técnicamente consistente y con 24 pruebas verdes en su entorno aislado.
