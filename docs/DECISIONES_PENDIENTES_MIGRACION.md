# Decisiones Pendientes de Migración — Genaro POS

> **Estado del Documento:** Borrador de Análisis Técnico.  
> **Aviso:** Ninguna de estas decisiones se asume como aprobada. Se enumeran con su propuesta recomendada para evaluación y definición expresa por parte del propietario del comercio.

---

### 1. Criterio de Imputación PREPAY en Pagos Mixtos (Nuevas Transacciones)
* **Situación:** En el código actual, el *Resumen de Caja* prorratea proporcionalmente el capital frente al total cobrado, mientras que el *Visor* y la hoja de cálculo de Google Sheets priorizan la transferencia hacia el capital (`capital_transferido = min(pago_transf, monto_carga)`) imputando el remanente al adicional.
* **Propuesta Recomendada:** Adoptar para **nuevas operaciones** la regla del Visor (prioridad de transferencia al capital), por representar con fidelidad la compra mayorista de saldo virtual bancarizado.
* **Política Histórica:** **Sin recálculo retroactivo.** Los datos históricos conservan sus valores originales para no alterar balances pasados.
* **Estado:** `PENDIENTE DE APROBACIÓN`.

---

### 2. Rol y Operatoria de Google Sheets (Archivo Histórico vs. Bidireccional)
* **Situación:** En la actualidad, tanto Streamlit como Apps Script leen y reescriben hojas completas, arriesgando pérdida de datos por colisión.
* **Propuesta Recomendada:** Google Sheets debe convertirse en un **archivo histórico y réplica auditada**. Los botones de cobro/registro directo en la planilla no se desactivan de inmediato: se conservan operativos hasta el momento del corte programado, tras validar que la app web cubra el 100% de la operatoria diaria.
* **Estado:** `PENDIENTE DE APROBACIÓN`.

---

### 3. Confirmación de Commit en Producción (Streamlit Community Cloud)
* **Situación:** El repositorio GitHub `alanrod10/genaro-pos` tiene `b16b8b70a077a9b6a61858e22a64a9ba5dbacc63` en `main`, pero la plataforma PaaS no expone públicamente el SHA activo sin inicio de sesión de propietario.
* **Acción Requerida:** El propietario debe verificar en la consola de Streamlit Cloud (`Manage app -> Logs`) el commit desplegado y la versión de Python del contenedor.
* **Estado:** `PENDIENTE DE VERIFICACIÓN`.

---

### 4. Obtención del Código Real de Google Apps Script
* **Situación:** La cuenta de servicio de GCP no tiene permisos de Drive API sobre el script vinculado. La lógica actual se reconstruyó mediante ingeniería inversa de las fórmulas vivas de Sheets.
* **Acción Requerida:** El propietario debe acceder a *Extensiones > Apps Script* en Google Sheets, copiar los archivos fuente (`.gs` / `.html`) y facilitarlos para incorporarlos a la revisión formal.
* **Estado:** `PENDIENTE DE OBTENCIÓN`.

---

### 5. Tarifa Nocturna (Modo Noche)
* **Definición del Propietario:** El comercio ya no utiliza tarifa nocturna.
* **Alcance:** **Fuera del alcance funcional.** No se construirán selectores ni recálculos nocturnos. Los campos `PRECIO_NOCHE` y `ES_NOCTURNO` se tratan como remanentes legacy y se rellenan con valores neutros (`precio_dia` y `0` respectivamente) solo si se requiere para exportaciones de compatibilidad.
* **Estado:** `RESUELTO (Fuera de Alcance)`.

---

### 6. Semántica de Catálogo: Nombres de Productos Duplicados
* **Hallazgo:** Existen productos activos con nombres idénticos y distintos proveedores/costos (ej. `'Galletas Pitusas 160gr'`).
* **Regla:** **No imponer restricción UNIQUE sobre la columna `nombre`.** La clave primaria es numérica (`id`). Los nombres duplicados se preservan y no se fusionan automáticamente.
* **Estado:** `RESUELTO (Técnico)`.

---

### 7. Tratamiento de Importes y Cuarentena de Anomalías
* **Regla:** Los importes monetarios se manejan como pesos enteros. Cualquier valor histórico no convertible o ambiguo no será descartado ni modificado silenciosamente: se ingresará en una tabla de **Cuarentena de Migración** con registro de procedencia, motivo y valor original.
* **Estado:** `RESUELTO (Técnico)`.

---

### 8. Atomicidad Transaccional en Cobro y Cargas
* **Regla:** Cada venta debe persistir la cabecera del ticket, las $N$ líneas de ítem y el evento en la cola Outbox dentro de una única transacción SQL (`BEGIN` ... `COMMIT`). Fallas en cualquier línea revierten toda la operación (`ROLLBACK`).
* **Estado:** `RESUELTO (Técnico)`.

---

### 9. Mecanismo de Corrección en Historiales
* **Regla:** El recálculo de ventas por edición o borrado de ítems debe ejecutarse mediante transacciones SQL basadas en `id` del ítem y `ticket_id`, evitando mapeos basados en índices de visualización en la interfaz.
* **Estado:** `RESUELTO (Técnico)`.
