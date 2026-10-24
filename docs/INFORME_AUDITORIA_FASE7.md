# INFORME DE AUDITORÍA TÉCNICA DEL LABORATORIO (FASE 7) — GENARO POS

> **Fecha y Hora de Auditoría:** 2026-10-22 16:50 UTC  
> **Workspace Auditado:** `/home/alanrod10/workspace/genaro-pos-turso-lab`  
> **Rama:** `feature/turso-migration-lab`  
> **Commit Base Verificado:** `b16b8b70a077a9b6a61858e22a64a9ba5dbacc63`  
> **Directorio Original (`/mnt/c/GenaroPOS`):** Verificado intacto (sin modificaciones en timestamps ni contenidos).

---

## 1. Alcance y Límites de la Auditoría

### Alcance
* Inspección minuciosa e integral del código de esquema (`db/schema_v1.sql`), módulos de normalización (`db/normalization.py`), suites de pruebas (`tests/`), documentación de diseño (`docs/`) y archivos de configuración del laboratorio (`.gitignore`, `.env.example`, `requirements.txt`).
* Ejecución y verificación empírica de la suite de pruebas unitarias en el entorno virtual aislado Linux (`.venv`).
* Evaluación exhaustiva de integridad contable, compatibilidad con Turso Cloud / `turso_serverless`, modelo Outbox y robustez ante fallos de sincronización.

### Límites Estrictos
* **Sin conectividad remota:** No se aprovisionó ni consultó ninguna base de datos Turso remota, no se interactuó con la API de Google Sheets y no se modificó Streamlit Community Cloud.
* **Sin alteración de código productivo:** `app.py` original se mantuvo inalterado.
* **Validación en SQLite en memoria:** Los tests ejecutados demuestran validez sintáctica y lógica sobre SQLite estándar local con `PRAGMA foreign_keys = ON;`; no constituyen por sí solos prueba de latencia, red ni límites de concurrencia en la infraestructura de Turso Cloud.

---

## 2. Inventario Real de Archivos del Laboratorio

| Archivo / Directorio | Estado en Git | Tamaño / Hash | Función y Hallazgos |
|---|---|---|---|
| `db/schema_v1.sql` | Untracked (Nuevo) | 3.518 B | DDL V1 del modelo relacional propuesto (7 tablas). |
| `db/normalization.py` | Untracked (Nuevo) | 3.018 B | Lógica de conversión de moneda entera, zona horaria UTC/Argentina y cálculo PREPAY parametrizado. |
| `tests/test_data_integrity.py` | Untracked (Nuevo) | 6.536 B | Suite de 10 pruebas unitarias en memoria sobre SQLite local. |
| `tests/test_turso_dev_remote.py` | Untracked (Nuevo) | 1.488 B | Script harness para pruebas remotas en Turso DEV con aborto seguro por variable de entorno. |
| `docs/DECISIONES_PENDIENTES_MIGRACION.md` | Untracked (Nuevo) | 3.125 B | Registro estructurado de decisiones operativas y de negocio pendientes de aprobación. |
| `docs/REVISION_DDL_V1.md` | Untracked (Nuevo) | 7.025 B | Documentación técnica con justificación de tipos, índices y constraints del DDL. |
| `.gitignore` | Untracked (Nuevo) | 501 B | Reglas de exclusión de `.venv/`, `.streamlit/secrets.toml`, `*.json`, bases locales y backups. |
| `.env.example` | Untracked (Nuevo) | 196 B | Plantilla de variables sin secretos para desarrollo local. |
| `requirements.txt` | Tracked (Original) | 59 B | Dependencias heredadas del repo (`streamlit`, `st-gsheets-connection`, `streamlit-keyup`, `pandas`). |
| `app.py` | Tracked (Original) | 110.671 B | Código fuente de producción V4.6 (intacto). |
| `README.md` | Tracked (Original) | 32.713 B | Documentación original (intacto). |

* **Discrepancias con el informe de Fase 6:** Ninguna discrepancia estructural. Todos los archivos reportados en la fase anterior existen y concuerdan en función.

---

## 3. Resultados de las Pruebas Locales

* **Comando exacto ejecutado:**
  ```bash
  cd /home/alanrod10/workspace/genaro-pos-turso-lab && source .venv/bin/activate && PYTHONPATH=. pytest -v --tb=short
  ```
* **Intérprete y Entorno:** Python 3.14.4 Linux x86_64 (`pytest-9.1.1`, `pluggy-1.7.0`, `cov-7.1.0`).
* **Salida de Ejecución:**
  ```text
  tests/test_data_integrity.py::test_parse_moneda_localizada PASSED        [ 10%]
  tests/test_data_integrity.py::test_conversion_fechas_utc_local PASSED    [ 20%]
  tests/test_data_integrity.py::test_productos_nombres_duplicados_y_margen PASSED [ 30%]
  tests/test_data_integrity.py::test_balance_pago_tickets PASSED           [ 40%]
  tests/test_data_integrity.py::test_foreign_key_rechaza_huerfano PASSED   [ 50%]
  tests/test_data_integrity.py::test_creacion_atomica_y_rollback PASSED    [ 60%]
  tests/test_data_integrity.py::test_outbox_idempotencia PASSED            [ 70%]
  tests/test_data_integrity.py::test_politicas_prepay_parametrizadas[2000-100-1000-1100-esperado_visor0-esperado_caja0] PASSED [ 80%]
  tests/test_data_integrity.py::test_politicas_prepay_parametrizadas[1000-100-1100-0-esperado_visor1-esperado_caja1] PASSED [ 90%]
  tests/test_data_integrity.py::test_politicas_prepay_parametrizadas[1000-100-0-1100-esperado_visor2-esperado_caja2] PASSED [100%]

  ============================== 10 passed in 0.06s ==============================
  ```
* **Verificación de Seguridad en Script Remoto:**
  Ejecución de `python3 tests/test_turso_dev_remote.py`:
  `[SEGURIDAD]: Abortando. Variable ENV_NAME != 'genaro-pos-dev'.` (Aborto exitoso, 0 llamadas de red).

---

## 4. Hallazgos Clasificados por Severidad

### 🔴 Severidad Alta (Crítico antes de codificar la migración)
1. **Ausencia de Restricción Inter-Tabla entre Cabecera e Ítems:**
   * *Diagnóstico:* En SQLite/libSQL, la cláusula `CHECK` de una tabla no puede contener subconsultas (ej. no se puede hacer `CHECK (total_venta == (SELECT SUM(subtotal) FROM ticket_items WHERE ticket_id = tickets.id))`).
   * *Impacto:* La restricción `chk_ticket_balance` solo valida que `total_venta == efectivo + transferencia`, pero no previene que la suma de los ítems difiera del total del ticket si una capa intermedia comete un error aritmético.
   * *Mitigación Requerida:* La validación debe garantizarse a nivel de código de aplicación dentro de la transacción de cobro, y opcionalmente mediante triggers `AFTER INSERT / UPDATE / DELETE ON ticket_items`.
2. **Ambigüedad en la Redistribución de Pagos al Editar/Eliminar Ítems:**
   * *Diagnóstico:* Cuando en el historial se elimina o reduce un ítem de un ticket mixto, ¿de dónde se descuenta la diferencia? La UI actual de `app.py` descuenta primero del efectivo y luego de la transferencia. Si se aumenta el subtotal, se suma al medio predominante.
   * *Impacto:* Esta regla altera unilateralmente la proporción de medios de pago sin confirmación explícita del cajero.
   * *Mitigación Requerida:* Requerir aprobación formal del propietario sobre este comportamiento de recálculo en cascada.

### 🟡 Severidad Media (Riesgos de Concurrencia y Sincronización)
1. **Riesgo de Re-procesamiento Desordenado en el Outbox:**
   * *Diagnóstico:* Si el worker falla a mitad de lote en Google Sheets, procesar eventos simultáneamente o reintentar sin bloqueo puede causar que un `UPDATE` se aplique antes que un `INSERT` o que se reescriba un estado anterior.
   * *Mitigación Requerida:* Procesamiento mono-hilo estrictamente secuencial por `id ASC` en la tabla `outbox_events`.
2. **Imposibilidad de Borrado Físico en Google Sheets:**
   * *Diagnóstico:* Eliminar filas físicas en Sheets rompe las referencias de fórmulas (`SUMIFS`, `VLOOKUP`) en las hojas `VISOR` y `CAJA`.
   * *Mitigación Requerida:* Política estricta de anulación contable (marcar `TOTAL_VENTA = 0`, `ESTADO = 'ANULADO'`) en lugar de `deleteDimension`.

### 🟢 Severidad Baja (Optimización y Documentación)
1. **Precisión de Margen en Catálogo:**
   * *Diagnóstico:* La columna generada `margen_ratio` almacena 4 decimales. En frontend debe mostrarse con formato `format="%.1f %%"` para mantener concordancia con el 35.6% visual habitual.
2. **Dependencias del Laboratorio en Git:**
   * *Diagnóstico:* `turso_serverless` y `pytest` están instalados en `.venv` pero no se han incorporado formalmente a un archivo `requirements-dev.txt` versionado en el repo.

---

## 5. Auditoría de Integridad Contable

### Reglas de Coherencia Transaccional
Para cualquier operación de venta o modificación, el motor relacional y la capa Python deben asegurar la siguiente ecuación contable cerrada:

$$\sum_{k=1}^N \text{subtotal}_k = \text{total\_venta} = \text{monto\_efectivo} + \text{monto\_transferencia}$$

#### Protocolo de Transacción en Modificaciones de Historial
Cuando un ítem $i$ es modificado de $\text{subtotal}_{\text{viejo}}$ a $\text{subtotal}_{\text{nuevo}}$ ($\Delta = \text{subtotal}_{\text{viejo}} - \text{subtotal}_{\text{nuevo}}$):
1. **Si $\Delta > 0$ (el ítem se reduce o elimina):**
   * El total del ticket baja por $\Delta$.
   * Si $\text{monto\_efectivo} \ge \Delta$, se reduce $\text{monto\_efectivo} = \text{monto\_efectivo} - \Delta$.
   * Si no, $\text{monto\_efectivo} = 0$ y el saldo restante se descuenta de $\text{monto\_transferencia}$.
   * Si el total resultante es $0$, el ticket se marca como `anulado = 1`.
2. **Si $\Delta < 0$ (el ítem aumenta):**
   * El incremento $|\Delta|$ se suma al medio original del ítem o al medio predominante.
3. **Persistencia Atómica:** Todo el recálculo (ítems, cabecera y evento Outbox) se consolida en una sola transacción `BEGIN ... COMMIT`.

---

## 6. Auditoría de Normalización de Datos

### Evaluación de `db/normalization.py`
* **Parseo Monetario (`parse_moneda_pesos`):**
  * Satisface strings con signos de moneda, espacios y formato `"$3,700.00"` -> `(3700, True)`.
  * Identifica centavos fraccionales no nulos (ej. `"$3,700.50"`) y retorna `(0, False)`, enviando el registro a cuarentena.
  * *Mejora recomendada:* Asegurar que números en notación científica o con caracteres espurios de planillas (ej. `"#REF!"`, `"#VALUE!"`) se capturen de manera segura sin lanzar excepciones no controladas.
* **Normalización Temporal (`utc_to_argentina`, `argentina_to_utc_str`):**
  * Las funciones utilizan `zoneinfo.ZoneInfo("America/Argentina/Jujuy")` de la biblioteca estándar de Python 3.9+.
  * Son funciones puras, deterministas y completamente idempotentes.

---

## 7. Auditoría del Mecanismo Outbox (Sincronización a Sheets)

### Matriz de Riesgos del Outbox y Mitigaciones Técnicas

| Riesgo Técnico | Escenario de Falla | Mitigación Arquitectónica Verificable |
|---|---|---|
| **Doble Procesamiento** | El worker envía una fila a Sheets, pero la red se corta antes de actualizar `outbox_events` a `SINCRONIZADO`. | **Idempotencia por ID:** Cada fila en Sheets lleva `ID_SISTEMA`. Si el worker reintenta, realiza un `UPDATE` buscando el ID; si no existe, hace `APPEND`. No se crean filas duplicadas. |
| **Corte a Mitad de Lote** | Un lote de 20 eventos falla en el evento 12. | Transacción de actualización en Turso: solo los eventos confirmados por Sheets con HTTP 200 pasan a `SINCRONIZADO`; los fallidos quedan en `PENDIENTE`. |
| **Reintentos Infinitos** | Un payload corrupto provoca fallo permanente en Sheets. | **Dead-Letter:** Columna `intentos INTEGER`. Si `intentos >= 5`, el estado pasa a `'ERROR_PERMANENTE'` y se emite alerta en el panel sin detener el resto de la cola. |
| **Concurrencia de Workers** | Dos disparadores cron se solapan. | Bloqueo por estado: `UPDATE outbox_events SET estado = 'EN_PROCESO' WHERE id IN (...) AND estado = 'PENDIENTE';` de forma atómica. |
| **Desorden Cronológico** | Se sincroniza una corrección antes de la creación original. | Procesamiento estrictamente ordenado por `id ASC`. |

---

## 8. Correcciones Recomendadas y Dependencias

```text
[ C1: Crear requirements-dev.txt ]
       │
       ▼
[ C2: Implementar Trigger de Validación Contable SQLite ]
       │
       ▼
[ C3: Añadir Tabla de Cuarentena al DDL (migration_quarantine) ]
       │
       ▼
[ C4: Desarrollar Suite de Pruebas de Carga Sintética para Outbox ]
```

1. **C1 (Inmediata):** Crear `requirements-dev.txt` con versiones fijadas de `pytest==9.1.1`, `turso_serverless==0.1.0` y herramientas de prueba.
2. **C2 (Recomendada):** Diseñar triggers en SQLite/libSQL que aborten la transacción si tras modificar `ticket_items` el total no coincide con la cabecera.
3. **C3 (Pre-Migración):** Crear la tabla `migration_quarantine` en `schema_v1.sql` para alojar filas de Sheets con centavos, fechas inválidas o IDs corruptos durante la fase de ETL.
4. **C4 (Pre-Migración):** Simular en pruebas locales la cola Outbox con fallos de red simulados para certificar la idempotencia sin tocar Google Sheets real.

---

## 9. Decisiones Pendientes del Propietario

1. **D1 — Criterio PREPAY:** Aprobación de la regla del Visor (`capital_transferido = min(transf, carga)`) para operaciones nuevas.
2. **D2 — Regla de Redistribución en Edición de Tickets Mixtos:** Confirmación de si se aprueba descontar primero de efectivo y luego de transferencia al eliminar/reducir ítems.
3. **D3 — Confirmación de Commit en Producción:** Confirmación manual del SHA en los logs de Streamlit Community Cloud.
4. **D4 — Extracción del Código de Google Apps Script:** Obtención de los archivos `.gs` desde la planilla de producción.

---

## 10. Criterios Objetivos para Autorizar la Fase 8 (Aprovisionamiento DEV)

Se autorizará avanzar a la Fase 8 únicamente cuando se cumplan las siguientes condiciones medibles:
* [x] Entorno de laboratorio aislado verificado sin contaminación con producción.
* [x] Suite de pruebas sintéticas locales ejecutada con 100% de éxito (10/10 tests PASSED).
* [x] DDL V1 validado contra las limitaciones documentadas de Turso Cloud.
* [ ] Aprobación explícita del propietario de las decisiones D1 y D2.
* [ ] Creación autorizada de una base de datos exclusiva de desarrollo (`genaro-pos-dev`) en Turso para validar `turso_serverless` sobre la red.
* [ ] Ninguna credencial ni dato real transferido a repositorios o carpetas públicas.

---

### Estado Final de Git en el Laboratorio

```text
On branch feature/turso-migration-lab
Untracked files:
  (use "git add <file>..." to include in what will be committed)
	.env.example
	.gitignore
	db/
	docs/DECISIONES_PENDIENTES_MIGRACION.md
	docs/INFORME_AUDITORIA_FASE7.md
	docs/REVISION_DDL_V1.md
	tests/

nothing added to commit but untracked files present
```
* **Confirmación final:** Ningún archivo de producción fue modificado. Toda la evidencia y documentación permanece exclusivamente contenida dentro del laboratorio local.
