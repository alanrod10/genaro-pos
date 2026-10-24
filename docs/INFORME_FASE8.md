# INFORME TÉCNICO DE ENDURECIMIENTO LOCAL (FASE 8) — GENARO POS

> **Fecha:** 2026-10-22 16:58 UTC  
> **Workspace Auditado y Operado:** `/home/alanrod10/workspace/genaro-pos-turso-lab`  
> **Rama:** `feature/turso-migration-lab`  
> **Commit Base:** `b16b8b70a077a9b6a61858e22a64a9ba5dbacc63`  
> **Aislamiento:** Verificado 100% local. Directorio `/mnt/c/GenaroPOS` intacto. Sin conexiones remotas ni dependencias productivas modificadas.

---

## 1. Archivos Inspeccionados

Se inspeccionó de forma completa y directa el árbol del laboratorio:
1. `docs/INFORME_AUDITORIA_FASE7.md` (presente y verificado íntegro).
2. `db/schema_v1.sql` (esquema DDL V1).
3. `db/normalization.py` (parseo monetario y cálculo PREPAY).
4. `tests/test_data_integrity.py` (suite previa de 10 tests).
5. `tests/test_turso_dev_remote.py` (harness de aborto seguro).
6. `docs/REVISION_DDL_V1.md` (especificación de arquitectura de datos).
7. `docs/DECISIONES_PENDIENTES_MIGRACION.md` (registro de decisiones pendientes).
8. `.gitignore` y `.env.example`.

---

## 2. Hallazgos y Riesgos Previos Tratados en Esta Fase

* **Riesgo 1 (Integridad Inter-Tabla):** SQLite/libSQL no admite subconsultas en restricciones `CHECK`. El DDL por sí solo no podía evitar que la suma de subtotales de los ítems discrepase del total del ticket si una capa intermedia fallaba.
* **Riesgo 2 (Redistribución en Ediciones):** La anulación o reducción de un ítem en un ticket mixto requería una máquina de recálculo atómica que mantuviera la igualdad `total = efvo + transf` sin depender de la UI de Streamlit ni arriesgar estados parciales.
* **Riesgo 3 (Resiliencia del Outbox):** Faltaba una implementación concreta de la máquina de estados del Outbox que manejase bloqueos de reclamo (`EN_PROCESO`), timeouts por caída del worker y descarte a cola de fallos (*dead-letter*) tras superar el máximo de reintentos.

---

## 3. Cambios Realizados en el Laboratorio (Archivo por Archivo)

1. **`db/accounting.py` (Nuevo):**
   * Implementa `validar_coherencia_venta()`: valida que `total_venta == efvo + transf` y `total_venta == sum(subtotales)`.
   * Implementa `registrar_venta_atomica()`: orquesta en una única transacción SQL (`BEGIN`...`COMMIT`) la inserción de cabecera en `tickets`, $N$ líneas en `ticket_items` y el evento correspondiente en `outbox_events`.
   * Implementa `modificar_item_y_recalcular_ticket()`: recalcula el ticket padre ante modificaciones de ítems, ajusta los medios de pago de forma determinista y registra el evento `UPDATE` en el Outbox.
   * Implementa la máquina de estados del Outbox: `reclamar_lote_outbox()`, `confirmar_evento_outbox()` y `registrar_fallo_evento_outbox()`.
2. **`tests/test_accounting_hardening.py` (Nuevo):**
   * Suite de 8 pruebas sintéticas exhaustivas que validan ventas con 1 y múltiples ítems, rechazo estricto de descuadres, rollback completo ante excepciones, recálculo atómico por edición, anulación total y ciclo de vida de eventos del Outbox.
3. **`docs/DECISIONES_PENDIENTES_MIGRACION.md` (Actualizado):**
   * Se documentó formalmente el protocolo de preservación histórica: **prohibición de recálculo retroactivo** sobre ventas pasadas para proteger la verdad contable ya archivada.

---

## 4. Justificación de las Decisiones Técnicas

* **Validación en Capa de Servicio Transaccional vs. Triggers:**
  * En SQLite, los triggers `AFTER INSERT ON ticket_items` presentan el problema de evaluar fila por fila durante inserciones en lote, disparando excepciones antes de que se hayan insertado todos los ítems de un ticket.
  * Se optó por una **función de orquestación transaccional (`registrar_venta_atomica`)** que ejecuta la validación matemática completa inmediatamente antes de persistir y consolida cabecera, detalle y Outbox dentro de un mismo bloque transaccional atómico, disparando `ROLLBACK` ante cualquier anomalía.
* **Máquina de Estados del Outbox:**
  * Estados soportados: `PENDIENTE` $\rightarrow$ `EN_PROCESO` $\rightarrow$ `SINCRONIZADO` (o `ERROR` tras 5 intentos fallidos).
  * Concurrencia segura: `BEGIN IMMEDIATE` para reclamar lotes, impidiendo que dos workers procesen simultáneamente los mismos eventos.
  * Auto-recuperación: Eventos colgados en `EN_PROCESO` por más de 10 minutos son devueltos a `PENDIENTE` con incremento de intentos.

---

## 5. Resultados de las Pruebas (Antes y Después)

* **Antes de la Fase 8:** 10 pruebas ejecutadas (`tests/test_data_integrity.py`), todas aprobadas.
* **Después de la Fase 8:** 18 pruebas ejecutadas (10 previas + 8 nuevas de endurecimiento contable).
* **Comando:**
  ```bash
  cd /home/alanrod10/workspace/genaro-pos-turso-lab && source .venv/bin/activate && PYTHONPATH=. pytest -v
  ```
* **Resultado:** **`18 passed in 0.07s`** (100% de éxito).

---

## 6. Riesgos que Siguen Abiertos

1. **Latencia de Red HTTP hacia Turso en Producción:**
   * Las pruebas se ejecutaron sobre SQLite local en memoria. El comportamiento bajo latencia real de red sobre HTTP/Websockets mediante `turso_serverless` solo podrá evaluarse cuando se aprovisione una base de datos dev en Turso Cloud.
2. **Tiempo de Respuesta de la API de Google Sheets:**
   * La escritura en Google Sheets por parte del worker puede demorar entre 1.000 ms y 2.500 ms por lote. La cola Outbox absorbe esta lentitud, pero debe asegurarse una periodicidad de cron suficiente (1 a 2 minutos).

---

## 7. Decisiones que Requieren Aprobación del Propietario

1. **Regla de Redistribución en Edición de Tickets Mixtos:**
   * *Propuesta:* Reducir primero del efectivo y luego de la transferencia si un ítem disminuye de precio o se anula.
   * *Estado:* `PENDIENTE DE APROBACIÓN`.
2. **Criterio PREPAY:**
   * *Propuesta:* Priorizar transferencia al capital en nuevas operaciones (`min(transf, carga)`). Mantener histórico sin recálculo.
   * *Estado:* `PENDIENTE DE APROBACIÓN`.
3. **Confirmación del Commit de Producción:**
   * Verificación manual en Streamlit Community Cloud de si el commit activo coincide con `b16b8b70`.
4. **Código de Google Apps Script:**
   * Exportación de los archivos `.gs` desde la planilla para definir si se retiran los botones de cobro en Sheets.

---

## 8. Validaciones Pendientes para una Futura Prueba en Turso DEV

Cuando el propietario autorice la creación de una base `genaro-pos-dev`:
* Ejecutar `tests/test_turso_dev_remote.py` configurando `ENV_NAME="genaro-pos-dev"`.
* Medir la latencia real $p50$ y $p95$ en la confirmación atómica de tickets y Outbox.
* Validar el comportamiento de `BEGIN CONCURRENT` bajo concurrencia simulada.

---

## 9. Confirmación Explícita del Alcance de Aislamiento

* **Directorio Windows `/mnt/c/GenaroPOS`:** **NO FUE MODIFICADO NI ACCEDIDO PARA ESCRITURA.**
* **Repositorio Remoto GitHub:** **NO SE HIZO NINGÚN `git push` NI MODIFICACIÓN EN `main`.**
* **Google Sheets y Apps Script:** **0 ESCRITURAS REALIZADAS.**
* **Bases de Datos Remotas:** **0 BASES O TOKENS CREADOS.**

---

## 10. Recomendación Fundamentada sobre el Siguiente Paso

El laboratorio local cuenta ahora con un **núcleo de integridad contable y transaccional probado y endurecido**.  
El siguiente paso recomendado consiste en **solicitar las aprobaciones pendientes del propietario (D1 a D4)** para proceder formalmente al aprovisionamiento seguro de la base de pruebas `genaro-pos-dev` en Turso y realizar el primer benchmark de latencia en la nube sin afectar la operación real del comercio.
