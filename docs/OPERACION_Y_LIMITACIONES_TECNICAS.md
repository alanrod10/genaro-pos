# GenaroPOS — Documentación Técnica de Operación, Sincronización, Despliegue (USD 0/mes) y Respaldos

Este documento registra de forma verificable el estado real de los mecanismos de sincronización, las fronteras operativas del esquema de despliegue de costo **USD 0/mes** y los procedimientos seguros de respaldo y recuperación para **GenaroPOS**.

---

## 1. Estado Real de `ventas_outbox` y Relación con Google Sheets (Sección 22)

### 1.1. Qué existe en el esquema SQL
La migración versionada `db/migrations/002_sales_support.sql` crea la tabla `ventas_outbox`:
- Columnas: `id`, `ticket_id`, `payload_json`, `estado` (`PENDIENTE`, `PROCESANDO`, `ENVIADO`, `ERROR`), `intentos`, `ultimo_error`, `creado_en_utc`, `actualizado_en_utc`.
- Propósito original de diseño: servir como bandeja de salida transaccional (*Transactional Outbox*) para replicar asincrónicamente tickets de Turso/libSQL hacia Google Sheets sin bloquear el cobro en caja.

### 1.2. Estado real de implementación en el código actual
- **Persistencia primaria activa**: El adaptador `db/sales.py` (`registrar_venta_turso`, `anular_venta_turso`, `editar_item_venta_turso`) persiste de forma transaccional e idempotente en las tablas `tickets`, `ticket_items` y `ventas_auditoria`.
- **Outbox no conectado a un worker**: Actualmente `registrar_venta_turso` **no inserta filas en `ventas_outbox`** ni existe un proceso programado (*worker* / *cron*) en FastAPI que drene `ventas_outbox` hacia Google Sheets.
- **Implicación operativa**:
  1. Todas las ventas registradas desde la nueva PWA (React + FastAPI) quedan almacenadas y auditadas en **Turso/libSQL**, que actúa como fuente de verdad de la nueva arquitectura.
  2. Dichas ventas **no se copian automáticamente a las hojas de cálculo de Google Sheets** usadas por el sistema legacy (`app.py` en Streamlit).
  3. No debe asumirse que las operaciones realizadas en Turso aparecerán en Google Sheets a menos que en una fase futura se implemente explícitamente un conector de salida o exportación.

---

## 2. Cola Offline en el Navegador (IndexedDB v7) y Sincronización Idempotente

### 2.1. Alcance de las colas locales en modo conectado (`RealApiClient`)
La base local IndexedDB (`GenaroPOS_LocalDB`, versión `7`, definida en `frontend/src/services/db.ts`) mantiene tres almacenes de cola para contingencia de red en modo conectado:
1. **`ventas_queue`**: Guarda cada venta confirmada en Caja antes de enviarla a `POST /api/sales`. Sincronizada por `sincronizarVentaIndividual` y `sincronizarCola` (`frontend/src/services/sync.ts`) usando `ticket_id` como clave de idempotencia.
2. **`servicios_queue`**: Cuando `POST /api/services` falla por error de conectividad o indisponibilidad temporal (`HTTP 0`, `502`, `503`, `504`), `ServicesAdmin.tsx` conserva el registro con su `idempotency_key` (`srv-...`) y `sincronizarColaServicios` lo reenvía con la cabecera `Idempotency-Key`.
3. **`gastos_queue`**: Cuando `POST /api/expenses` falla por error de conectividad o indisponibilidad temporal (`HTTP 0`, `502`, `503`, `504`), `ExpensesCashAdmin.tsx` conserva el registro con su `idempotency_key` (`gst-...`) y `sincronizarColaGastos` lo reenvía con la cabecera `Idempotency-Key`.

### 2.2. Limitaciones de la operación offline
- Las ediciones o anulaciones históricas (`PATCH` / `PUT` / `POST .../cancel` o `.../anular`) sobre registros ya persistidos en el servidor requieren conexión activa con el backend para validar el estado actual del registro y generar la traza de auditoría en Turso.
- En modo de prueba de solo lectura (`GENARO_API_READ_ONLY=true` o `VITE_READ_ONLY_CONNECTED_SMOKE=true`), toda mutación y sincronización de colas queda estrictamente bloqueada.

---

## 3. Límites Operativos del Despliegue de Costo USD 0/mes (Sección 26)

### 3.1. Acceso remoto y móvil mediante Cloudflare Tunnel
1. **Quick Tunnels (`trycloudflare.com`)**:
   - Son túneles efímeros gratuitos pensados para pruebas puntuales.
   - Cambian de URL cada vez que se reinicia el proceso `cloudflared`, carecen de SLA y pueden experimentar cortes o límites de conexiones concurrentes.
2. **Recomendación para uso diario estable a costo USD 0**:
   - Utilizar un **Named Tunnel** de Cloudflare Zero Trust (plan Free, hasta 50 usuarios sin costo) vinculado a un dominio o subdominio fijo.
   - Si se protege el túnel con **Cloudflare Access** (PIN por correo), tener presente que cuando vence la cookie de sesión de Cloudflare (`CF_Authorization`), las peticiones `fetch` de la SPA pueden recibir redirecciones `302` o documentos `text/html`. El cliente `RealApiClient.login` detecta explícitamente estas respuestas (`resp.redirected` o `Content-Type: text/html`) e informa al operador que debe recargar la página para renovar la sesión del túnel, sin confundirlo con un error de contraseña de GenaroPOS.

### 3.2. Vite en desarrollo vs. Build de producción para uso móvil y PWA
- **Por qué no usar `vite` (modo dev) como servidor permanente detrás de un túnel**:
  - En modo desarrollo (`npm run dev`), Vite sirve decenas de módulos TypeScript/ESM sin empaquetar. A través de un túnel móvil esto incrementa la latencia inicial y puede entrar en conflicto con cachés de Service Worker.
  - Por ello, `frontend/src/main.tsx` **solo registra `/sw.js` en compilaciones de producción (`import.meta.env.PROD`) bajo HTTPS** y desregistra automáticamente cualquier Service Worker activo cuando detecta `import.meta.env.DEV`.
- **Modo recomendado para pruebas móviles y operación**:
  - Compilar el frontend con `npm run build` dentro de `frontend/` y servir los artefactos empaquetados (`frontend/dist`) mediante `npx vite preview --port 5173 --host 127.0.0.1` (con proxy `/api` hacia `127.0.0.1:8000`) o directamente desde un servidor inverso local.

### 3.3. Límites del plan gratuito de Turso y del limitador de tasa en memoria
- **Turso Starter / Free**:
  - Las consultas de arqueo (`db/expenses.py`), historial de ventas (`db/sales.py`), servicios (`db/services.py`) y reportes (`db/reports.py`) utilizan rangos UTC calculados a partir de la medianoche local de Argentina (`America/Argentina/Jujuy`, `UTC-3`), permitiendo aprovechar índices sobre `fecha_hora_utc` y evitando lecturas completas de tabla.
- **Rate Limiter de Login (`api/rate_limiter.py`)**:
  - Opera en memoria del proceso FastAPI aislando contadores por par `(ip, username)` (5 intentos cada 15 minutos por usuario) con un tope global por IP (25 intentos). Esto evita que un error de tipeo de un operador bloquee al otro cuando ambos acceden detrás del mismo túnel local (`127.0.0.1`), manteniendo protección contra fuerza bruta.

---

## 4. Procedimientos de Respaldo, Verificación de Esquema y Recuperación (Sección 27)

### 4.1. Verificación previa de preparación de esquema (`Readiness`)
Antes de habilitar escrituras en el backend (`GENARO_API_READ_ONLY=false`), verificar siempre que las **10 migraciones (`001` a `010`)** y las **13 tablas requeridas** estén presentes y sin inconsistencias:
```bash
curl -s http://127.0.0.1:8000/api/system/readiness | python3 -m json.tool
```
El estado devuelto debe ser `"status": "PREPARADO"` y `"listo": true`, incluyendo las tablas de auditoría (`productos_auditoria`, `cargas_servicios_auditoria`, `gastos_auditoria`, `ventas_auditoria`, `pedidos_auditoria`).

### 4.2. Respaldo seguro antes de aplicar nuevas migraciones
1. **Activar modo de solo lectura** en la API (`GENARO_API_READ_ONLY=true`) para impedir escrituras concurrentes durante la ventana de respaldo.
2. **Crear un directorio de respaldo fechado** fuera del árbol de código (por ejemplo, en `/home/alanrod10/backups/genaro-pos-dev/backup_pre_<version>_<timestamp>Z/`).
3. **Exportar todas las tablas operativas y de auditoría** junto con un archivo `manifest.json` que registre el conteo exacto de filas de cada tabla y el estado de `schema_migrations`.
4. **Verificar el respaldo** comprobando que los archivos `.json` / `.sql` exportados no estén vacíos y que sus conteos coincidan con la base antes de ejecutar cualquier sentencia DDL.

### 4.3. Restauración en caso de contingencia
En caso de necesitar revertir una migración o restaurar el estado previo en el entorno de laboratorio/desarrollo:
1. Mantener `GENARO_API_READ_ONLY=true`.
2. Inspeccionar el `manifest.json` del respaldo correspondiente en `/home/alanrod10/backups/genaro-pos-dev/`.
3. Restaurar las tablas en orden de dependencia (primero catálogos y cabeceras: `schema_migrations`, `productos`, `tickets`, `cargas_servicios`, `gastos_retiros`, `cierres_diarios`, `pedidos_proveedor`; luego tablas hijas y auditorías: `ticket_items`, `pedido_items`, `*_auditoria`) con `PRAGMA foreign_keys = ON`.
4. Ejecutar `verificar_preparacion_esquema` (`db/readiness.py`) para confirmar que `"listo": true` antes de reabrir la operación.
